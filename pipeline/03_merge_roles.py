# pipeline/03_merge_roles.py
# 角色整合模組
#
# 讀取 02a 輸出的所有角色擷取結果，執行：
#   1. 統計每個角色的出現次數，低於門檻的移至 minor_roles 資料夾
#   2. 用模型判斷別名（哪些名字是同一個角色），合併為單一主名
#   3. 將同一角色的所有來源資料整合成一個 JSON 陣列，輸出至 roles_json/
#
# 輸出：
#   data/roles_json/{主名}.json         ← 主要角色（供 04_create_cards 使用）
#   data/roles_json_minor/{名字}.json   ← 次要角色（手動移回才生成卡片）
#   data/roles_json/character_stats.json ← 統計資料（debug 用）

import json
import time
from collections import defaultdict
from pathlib import Path

from core.config import get, load_config
from core.file_utils import (
    read_json, write_json, list_files,
    safe_filename, ensure_dirs,
)
from core.api_client import (
    make_extract_client, make_analyze_client,
    call_api, parse_json_response,
)
import core.logger as log


# ============================================================
#  第一步：收集所有角色原始資料
# ============================================================

def collect_all_characters(char_response_dir: Path) -> dict[str, list]:
    """
    讀取所有 chunk 的擷取結果，
    回傳 {名字: [條目1, 條目2, ...]} 的原始對應表。
    每個條目保留來源 chunk 標記。
    """
    raw: dict[str, list] = defaultdict(list)

    response_files = list_files(char_response_dir, "*.json")
    if not response_files:
        return {}

    for rf in log.progress(response_files, desc="讀取擷取結果", unit="個"):
        try:
            data = read_json(rf)
        except Exception as e:
            log.warning(f"讀取失敗，跳過：{rf.name} → {e}")
            continue

        if not isinstance(data, dict):
            continue

        chunk_name  = data.get("chunk", rf.stem)
        characters  = data.get("characters", [])

        for char in characters:
            if not isinstance(char, dict):
                continue
            name = char.get("name", "").strip()
            if not name:
                continue
            entry = dict(char)
            entry["來源"] = chunk_name
            raw[name].append(entry)

    return dict(raw)


# ============================================================
#  第二步：次要角色篩選
# ============================================================

def split_minor_roles(
    raw: dict[str, list],
    threshold: int,
) -> tuple[dict[str, list], dict[str, list]]:
    """
    依出現次數（條目總數）將角色分為主要和次要。
    threshold = 0 時停用篩選，全部視為主要。
    回傳 (主要角色dict, 次要角色dict)。
    """
    if threshold <= 0:
        return raw, {}

    major = {}
    minor = {}
    for name, entries in raw.items():
        if len(entries) >= threshold:
            major[name] = entries
        else:
            minor[name] = entries

    if minor:
        log.info(
            f"次要角色篩選：{len(minor)} 個角色出現次數 < {threshold}，"
            f"移至 minor 資料夾"
        )
    return major, minor


# ============================================================
#  第三步：別名合併（模型判斷）
# ============================================================

_ALIAS_MERGE_SYSTEM = """\
你是一位小說角色分析 AI，負責判斷哪些名字指的是同一個角色。

判斷依據：
- 同一人物的本名、字號、稱謂、綽號、外號應合併
- 例如「諸葛亮」和「孔明」是同一人，「林黛玉」和「黛玉」是同一人
- 不同角色的名字即使相似也不能合併
- 若無法確定，保守判斷（不合併）

請嚴格按照以下 JSON 格式輸出，禁止任何額外說明：
[
  {
    "canonical": "主名（選擇最完整、最常用的名字）",
    "aliases": ["其他名字1", "其他名字2"]
  }
]

每個名字只能出現在一個群組中。沒有別名的角色也要列出，aliases 填空陣列。
"""


def _build_alias_prompt(names: list[str]) -> str:
    names_text = "\n".join(f"- {n}" for n in names)
    return f"請分析以下角色名字清單，判斷哪些是同一個角色的不同稱呼：\n\n{names_text}"


def detect_aliases_batch(
    names: list[str],
    client,
    model: str,
    analyze_client,
    pro_model: str,
    thinking_mode,
) -> list[dict] | None:
    """
    送一批名字給模型判斷別名關係。
    回傳 [{"canonical": ..., "aliases": [...]}, ...] 或 None（失敗）。
    """
    messages = [
        {"role": "system", "content": _ALIAS_MERGE_SYSTEM},
        {"role": "user",   "content": _build_alias_prompt(names)},
    ]

    raw = call_api(
        client=client,
        model=model,
        messages=messages,
        temperature=0.1,   # 低溫度，減少模型隨機合併
        max_tokens=4096,
        timeout=120,
        thinking_mode=thinking_mode,
    )

    if raw is None:
        return None

    parsed = parse_json_response(raw, analyze_client, pro_model)
    if not isinstance(parsed, list):
        log.warning("別名判斷：模型回傳格式異常，此批次跳過合併")
        return None

    return parsed


def build_alias_map(
    names: list[str],
    client,
    model: str,
    analyze_client,
    pro_model: str,
    thinking_mode,
    batch_size: int,
    sleep_interval: float,
) -> dict[str, str]:
    """
    分批對所有名字進行別名判斷，
    回傳 {任意名字: 主名} 的對應表。
    """
    alias_map: dict[str, str] = {}

    # 分批處理
    batches = [names[i:i+batch_size] for i in range(0, len(names), batch_size)]
    log.info(f"別名判斷：{len(names)} 個名字，分 {len(batches)} 批處理")

    for i, batch in enumerate(log.progress(batches, desc="別名判斷", unit="批"), 1):
        log.debug(f"第 {i}/{len(batches)} 批（{len(batch)} 個名字）")

        result = detect_aliases_batch(
            names=batch,
            client=client,
            model=model,
            analyze_client=analyze_client,
            pro_model=pro_model,
            thinking_mode=thinking_mode,
        )

        if result is None:
            log.warning(f"第 {i} 批別名判斷失敗，此批名字各自獨立保留")
            for name in batch:
                alias_map[name] = name
            continue

        # 建立對應表：所有名字（含別名）都指向主名
        seen_in_result = set()
        for group in result:
            if not isinstance(group, dict):
                continue
            canonical = group.get("canonical", "").strip()
            aliases   = group.get("aliases", [])
            if not canonical:
                continue

            alias_map[canonical] = canonical
            seen_in_result.add(canonical)

            for alias in aliases:
                alias = alias.strip()
                if alias and alias != canonical:
                    alias_map[alias] = canonical
                    seen_in_result.add(alias)

        # 模型漏列的名字保持獨立
        for name in batch:
            if name not in seen_in_result:
                log.debug(f"別名判斷：模型漏列「{name}」，保持獨立")
                alias_map[name] = name

        if i < len(batches):
            time.sleep(sleep_interval)

    return alias_map


# ============================================================
#  第四步：依別名表合併角色資料
# ============================================================

def merge_by_alias(
    raw: dict[str, list],
    alias_map: dict[str, str],
) -> dict[str, list]:
    """
    根據別名對應表，將各名字的條目合併到主名下。
    回傳 {主名: [所有來源條目]} 的合併結果。
    """
    merged: dict[str, list] = defaultdict(list)

    for name, entries in raw.items():
        canonical = alias_map.get(name, name)
        merged[canonical].extend(entries)

    # 在每個條目中記錄原始名字（方便追蹤）
    for canonical, entries in merged.items():
        for entry in entries:
            if "原始名字" not in entry:
                entry["原始名字"] = entry.get("name", canonical)
            entry["標準化名字"] = canonical

    return dict(merged)


# ============================================================
#  主流程
# ============================================================

def main():
    load_config()

    # 路徑
    char_response_dir = Path(get("char_response_dir", "data/responses/characters"))
    role_dir          = Path(get("role_output_dir",   "data/roles_json"))
    minor_dir         = Path(get("minor_role_dir",    "data/roles_json_minor"))
    ensure_dirs(role_dir, minor_dir)

    # 設定
    alias_merge      = get("alias_merge", True)
    alias_model_key  = get("alias_merge_model", "extract")  # "extract" | "analyze"
    batch_size       = int(get("alias_merge_batch_size", 50))
    threshold        = int(get("minor_role_threshold", 3))
    sleep_interval   = float(get("api_sleep_interval", 2))

    # 模型設定
    model     = get("model")
    pro_model = get("pro_model")
    thinking_mode = get("extract_thinking_mode", "auto")
    if thinking_mode in ("false", False):
        thinking_mode = False

    # 別名判斷用哪個模型
    if alias_model_key == "analyze":
        alias_client = make_analyze_client()
        alias_model  = pro_model
        alias_thinking = get("analyze_thinking_mode", "auto")
        if alias_thinking in ("false", False):
            alias_thinking = False
        log.info("別名判斷：使用高性能模型")
    else:
        alias_client = make_extract_client()
        alias_model  = model
        alias_thinking = thinking_mode
        log.info("別名判斷：使用性價比模型")

    # 高性能模型客戶端（JSON 修復用）
    analyze_client = make_analyze_client()

    # 驗證必要設定
    if not get("api_key") or not model:
        log.error("請在 config.yaml 設定 api_key 和 model")
        return
    if not get("pro_api_key") or not pro_model:
        log.error("請在 config.yaml 設定 pro_api_key 和 pro_model（用於 JSON 修復）")
        return

    if not char_response_dir.exists():
        log.error(f"找不到 {char_response_dir}，請先執行 02a_extract_characters.py")
        return

    # ── 第一步：收集 ───────────────────────────────────────
    log.section("第一步：收集角色擷取結果")
    raw = collect_all_characters(char_response_dir)

    if not raw:
        log.error("沒有找到任何角色資料，請確認 02a 已成功執行")
        return

    log.success(f"收集完成：{len(raw)} 個不同名字")

    # ── 第二步：次要角色篩選 ───────────────────────────────
    log.section("第二步：次要角色篩選")
    major_raw, minor_raw = split_minor_roles(raw, threshold)
    log.info(f"主要角色：{len(major_raw)} 個｜次要角色：{len(minor_raw)} 個")

    # 次要角色直接輸出（不做別名合併，節省 API 費用）
    for name, entries in minor_raw.items():
        safe_name = safe_filename(name)
        write_json(minor_dir / f"{safe_name}.json", entries)
    if minor_raw:
        log.info(f"次要角色已儲存至 {minor_dir}/（手動移回 roles_json/ 才會生成卡片）")

    if not major_raw:
        log.warning("沒有主要角色，流程結束")
        return

    # ── 第三步：別名合併 ───────────────────────────────────
    log.section("第三步：別名合併")

    names = list(major_raw.keys())

    if alias_merge:
        alias_map = build_alias_map(
            names=names,
            client=alias_client,
            model=alias_model,
            analyze_client=analyze_client,
            pro_model=pro_model,
            thinking_mode=alias_thinking,
            batch_size=batch_size,
            sleep_interval=sleep_interval,
        )
        merged = merge_by_alias(major_raw, alias_map)

        # 統計合併結果
        merged_count = sum(
            1 for name in names
            if alias_map.get(name, name) != name
        )
        log.success(f"別名合併完成：{len(names)} 個名字 → {len(merged)} 個角色（合併了 {merged_count} 個別名）")
    else:
        log.info("alias_merge = false，跳過別名合併")
        merged = {name: entries for name, entries in major_raw.items()}

    # ── 第四步：輸出 ───────────────────────────────────────
    log.section("第四步：輸出角色資料")

    stats = {}
    for canonical, entries in log.progress(merged.items(), desc="輸出角色資料", unit="個"):
        safe_name = safe_filename(canonical)
        write_json(role_dir / f"{safe_name}.json", entries)
        stats[canonical] = {
            "出現次數": len(entries),
            "來源chunk": list({e.get("來源", "") for e in entries}),
        }

    # 輸出統計檔（供 debug 和 04_create_cards 參考）
    write_json(role_dir / "character_stats.json", stats)

    log.section("角色整合完成")
    log.success(f"主要角色：{len(merged)} 個 → {role_dir}/")
    if minor_raw:
        log.info(f"次要角色：{len(minor_raw)} 個 → {minor_dir}/")


if __name__ == "__main__":
    main()
