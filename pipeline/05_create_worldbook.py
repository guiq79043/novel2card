# pipeline/05_create_worldbook.py
# 世界書生成模組
#
# 讀取 02b 擷取的原始世界書條目，
# 合併同名條目後使用高性能模型生成正式描述，
# 輸出符合 SillyTavern lorebook 格式的 JSON 檔案。
#
# 功能：
#   - 白名單篩選（只處理指定 type）
#   - 同名條目合併（直接拼接原始資料，保留語境差異）
#   - 02b keywords 為基礎，模型補充修正
#   - 條目級斷點續傳（key = type_條目名稱）
#   - 按 type 分檔輸出（{novel_title}_地點.json 等）
#   - 自動優先級（rule > item > faction > location > event > other）
#
# 輸出：
#   data/worldbook/{novel_title}_規則.json
#   data/worldbook/{novel_title}_道具.json
#   data/worldbook/{novel_title}_勢力.json
#   data/worldbook/{novel_title}_地點.json
#   data/worldbook/{novel_title}_事件.json
#   data/worldbook/{novel_title}_其他.json
#   data/progress_worldbook_gen.json   ← 生成進度（獨立於擷取進度）

import time
import uuid
from collections import defaultdict
from pathlib import Path

from core.config import get, load_config
from core.file_utils import (
    read_json, write_json, list_files, ensure_dirs,
)
from core.api_client import (
    make_analyze_client, call_api, parse_json_response,
)
from core.prompts import get_worldbook_prompt
from core.progress import ProgressTracker
import core.logger as log


# ============================================================
#  常數定義
# ============================================================

# type → 中文分類名（用於檔名）
TYPE_ZH = {
    "rule":     "規則",
    "item":     "道具",
    "faction":  "勢力",
    "location": "地點",
    "event":    "事件",
    "other":    "其他",
}

# 預設優先級（config 可覆蓋）
DEFAULT_INSERTION_ORDER = {
    "rule":     10,
    "item":     20,
    "faction":  30,
    "location": 40,
    "event":    50,
    "other":    60,
}

# 斷點續傳的 key 格式
def _progress_key(entry_type: str, entry_name: str) -> str:
    return f"{entry_type}_{entry_name}"


# ============================================================
#  第一步：收集並合併原始條目
# ============================================================

def collect_raw_entries(wb_response_dir: Path) -> dict[str, dict]:
    """
    讀取所有 02b 輸出的 JSON，
    以 (type, name) 為 key 合併同名條目。

    回傳格式：
    {
      "location_天京城": {
        "type": "location",
        "name": "天京城",
        "keywords": ["天京", "京城", ...],   ← 所有來源的 keywords 聯集
        "raw_contents": ["來源1的描述", "來源2的描述", ...],
        "source_chunks": ["chunk_003", ...]
      },
      ...
    }
    """
    collected: dict[str, dict] = {}

    response_files = list_files(wb_response_dir, "*.json")
    if not response_files:
        return {}

    for rf in log.progress(response_files, desc="讀取世界書擷取結果", unit="個"):
        try:
            data = read_json(rf)
        except Exception as e:
            log.warning(f"讀取失敗，跳過：{rf.name} → {e}")
            continue

        if not isinstance(data, dict):
            continue

        chunk_name   = data.get("chunk", rf.stem)
        world_entries = data.get("world_entries", [])

        for entry in world_entries:
            if not isinstance(entry, dict):
                continue

            entry_type = entry.get("type", "other")
            entry_name = entry.get("name", "").strip()
            content    = entry.get("content", "").strip()
            keywords   = entry.get("keywords", [])

            if not entry_name or not content:
                continue

            pkey = _progress_key(entry_type, entry_name)

            if pkey not in collected:
                collected[pkey] = {
                    "type":         entry_type,
                    "name":         entry_name,
                    "keywords":     [],
                    "raw_contents": [],
                    "source_chunks": [],
                }

            # 直接拼接（保留語境差異，不做去重）
            collected[pkey]["raw_contents"].append(
                f"【來源：{chunk_name}】\n{content}"
            )
            collected[pkey]["source_chunks"].append(chunk_name)

            # keywords 取聯集（去重但保留順序）
            existing_kws = set(collected[pkey]["keywords"])
            for kw in (keywords if isinstance(keywords, list) else []):
                kw = kw.strip()
                if kw and kw not in existing_kws:
                    collected[pkey]["keywords"].append(kw)
                    existing_kws.add(kw)

    return collected


# ============================================================
#  第二步：白名單篩選
# ============================================================

def apply_whitelist(
    collected: dict[str, dict],
    whitelist: list[str],
) -> dict[str, dict]:
    """
    若 whitelist 非空，只保留指定 type 的條目。
    """
    if not whitelist:
        return collected

    filtered = {
        pkey: entry
        for pkey, entry in collected.items()
        if entry["type"] in whitelist
    }

    removed = len(collected) - len(filtered)
    if removed:
        log.info(
            f"白名單篩選：移除 {removed} 個條目\n"
            f"  保留 type：{whitelist}"
        )
    return filtered


# ============================================================
#  第三步：生成單一條目
# ============================================================

_WORLDBOOK_ENTRY_SCHEMA = """\
請嚴格依照以下 JSON 格式輸出，禁止輸出任何額外文字或 markdown 標記：

{
  "name": "條目名稱",
  "keywords": ["觸發關鍵字1", "關鍵字2"],
  "content": "注入到上下文的最終內容",
  "comment": "給人類閱讀的一句話備註"
}
"""


def _build_generation_prompt(raw_entry: dict, worldbook_prompt: str) -> str:
    """組合送給模型的完整 prompt。"""
    raw_text = "\n\n".join(raw_entry["raw_contents"])
    base_kws = "、".join(raw_entry["keywords"]) if raw_entry["keywords"] else "（無）"

    return (
        f"{worldbook_prompt}"
        f"條目名稱：{raw_entry['name']}\n"
        f"條目類型：{raw_entry['type']}\n"
        f"原始擷取的關鍵字（請以此為基礎補充修正）：{base_kws}\n\n"
        f"原始資料（來自不同章節，可能有重複或矛盾，請整合）：\n"
        f"{raw_text}\n\n"
        f"{_WORLDBOOK_ENTRY_SCHEMA}"
    )


def generate_entry(
    pkey: str,
    raw_entry: dict,
    client,
    pro_model: str,
    thinking_mode,
    worldbook_prompt: str,
) -> dict | None:
    """
    使用高性能模型生成單一世界書條目。
    成功回傳解析後的 dict，失敗回傳 None。
    """
    messages = [
        {
            "role": "system",
            "content": (
                "你是一位專業的 SillyTavern 世界書條目製作 AI。"
                "請根據提供的原始資料，生成一個完整、準確的世界書條目。"
                "只回傳 JSON，禁止任何額外說明。"
            ),
        },
        {
            "role": "user",
            "content": _build_generation_prompt(raw_entry, worldbook_prompt),
        },
    ]

    raw_content = call_api(
        client=client,
        model=pro_model,
        messages=messages,
        temperature=0.4,
        max_tokens=2048,
        timeout=120,
        thinking_mode=thinking_mode,
    )

    if raw_content is None:
        log.error(f"{pkey}：API 呼叫失敗")
        return None

    parsed = parse_json_response(raw_content, client, pro_model)
    if not isinstance(parsed, dict):
        log.error(f"{pkey}：JSON 解析失敗")
        return None

    # 基本驗證
    if not parsed.get("content", "").strip():
        log.warning(f"{pkey}：content 為空，跳過")
        return None

    # 確保 keywords 是陣列
    kws = parsed.get("keywords", [])
    if not isinstance(kws, list):
        kws = raw_entry["keywords"]  # 退回使用原始 keywords
    parsed["keywords"] = kws

    return parsed


# ============================================================
#  第四步：組裝 SillyTavern lorebook 格式
# ============================================================

def build_lorebook(
    entries_by_type: dict[str, list[dict]],
    entry_type: str,
    novel_title: str,
    insertion_order_map: dict[str, int],
) -> dict:
    """
    將同一 type 的條目列表組裝成完整的 SillyTavern lorebook JSON。

    SillyTavern lorebook 格式：
    {
      "name": "世界書名稱",
      "entries": {
        "0": { uid, key, comment, content, enabled, insertion_order, ... },
        "1": { ... },
        ...
      }
    }
    """
    type_zh      = TYPE_ZH.get(entry_type, entry_type)
    book_name    = f"{novel_title}_{type_zh}"
    ins_order    = insertion_order_map.get(entry_type, 100)

    entries_dict = {}
    for idx, entry in enumerate(entries_by_type):
        entries_dict[str(idx)] = {
            "uid":             idx,
            "key":             entry.get("keywords", []),
            "comment":         entry.get("comment", entry.get("name", "")),
            "content":         entry.get("content", ""),
            "enabled":         True,
            "insertion_order": ins_order,
            "selective":       False,
            "position":        0,          # 0 = before_char（注入在角色設定前）
            "name":            entry.get("name", ""),
            "extensions":      {},
        }

    return {
        "name":    book_name,
        "entries": entries_dict,
    }


# ============================================================
#  主流程
# ============================================================

def main():
    load_config()

    # 路徑
    wb_response_dir  = Path(get("wb_response_dir",      "data/responses/worldbook"))
    wb_output_dir    = Path(get("worldbook_output_dir",  "data/worldbook"))
    progress_file    = get("wb_gen_progress_file",       "data/progress_worldbook_gen.json")
    ensure_dirs(wb_output_dir)

    # 設定
    novel_title     = get("novel_title",        "未命名小說")
    pro_model       = get("pro_model")
    whitelist       = get("worldbook_type_whitelist", []) or []
    thinking_mode   = get("analyze_thinking_mode", "auto")
    if thinking_mode in ("false", False):
        thinking_mode = False
    sleep_interval  = float(get("api_sleep_interval", 2))

    # 優先級設定（config 可覆蓋）
    cfg_order = get("worldbook_insertion_order", {}) or {}
    insertion_order_map = {**DEFAULT_INSERTION_ORDER, **cfg_order}

    # 驗證必要設定
    if not get("pro_api_key") or not pro_model:
        log.error("請在 config.yaml 設定 pro_api_key 和 pro_model")
        return

    if not wb_response_dir.exists():
        log.error(f"找不到 {wb_response_dir}，請先執行 02b_extract_worldbook.py")
        return

    client = make_analyze_client()
    worldbook_prompt = get_worldbook_prompt()

    # ── 第一步：收集並合併原始條目 ─────────────────────────
    log.section("第一步：收集並合併原始世界書條目")
    collected = collect_raw_entries(wb_response_dir)

    if not collected:
        log.error("沒有找到任何世界書條目，請確認 02b 已成功執行")
        return

    log.success(f"收集完成：{len(collected)} 個唯一條目")

    # ── 第二步：白名單篩選 ─────────────────────────────────
    if whitelist:
        log.section("第二步：白名單篩選")
        collected = apply_whitelist(collected, whitelist)
        if not collected:
            log.warning("白名單篩選後沒有任何條目，請確認 worldbook_type_whitelist 設定")
            return
    else:
        log.info("白名單為空，處理全部 type")

    # ── 第三步：生成條目（含斷點續傳）─────────────────────
    log.section(f"第三步：生成世界書條目（共 {len(collected)} 個）")

    tracker = ProgressTracker(progress_file)
    tracker.clean_stale(set(collected.keys()))

    pending      = tracker.pending_chunks(list(collected.keys()))
    already_done = len(collected) - len(pending)

    log.info(f"已完成：{already_done} 個（跳過）｜待處理：{len(pending)} 個")

    # 讀取已生成的條目（斷點續傳時需要）
    generated: dict[str, dict] = {}

    # 先把已完成的結果從暫存讀回（如果有的話）
    cache_path = wb_output_dir / "_generation_cache.json"
    if cache_path.exists():
        try:
            generated = read_json(cache_path)
            log.debug(f"從快取讀回 {len(generated)} 個已生成條目")
        except Exception:
            generated = {}

    success_count = 0
    failed_count  = 0

    for pkey in log.progress(pending, desc="生成世界書條目", unit="個"):
        raw_entry = collected[pkey]
        entry_name = raw_entry["name"]

        result = generate_entry(
            pkey=pkey,
            raw_entry=raw_entry,
            client=client,
            pro_model=pro_model,
            thinking_mode=thinking_mode,
            worldbook_prompt=worldbook_prompt,
        )

        if result is not None:
            result["_type"]  = raw_entry["type"]
            result["_pkey"]  = pkey
            result["name"]   = result.get("name", entry_name)
            generated[pkey]  = result
            tracker.mark_success(pkey)
            log.success(f"✓ {entry_name}")
            success_count += 1

            # 每次成功都更新快取，防止中途中斷遺失進度
            write_json(cache_path, generated)
        else:
            tracker.mark_failed(pkey, "生成失敗")
            log.warning(f"✗ {entry_name}（跳過）")
            failed_count += 1

        time.sleep(sleep_interval)

    # ── 第四步：按 type 分檔輸出 ───────────────────────────
    log.section("第四步：輸出世界書檔案")

    # 按 type 分組
    by_type: dict[str, list[dict]] = defaultdict(list)
    for pkey, entry in generated.items():
        entry_type = entry.get("_type", "other")
        by_type[entry_type].append(entry)

    output_files = []
    for entry_type, entries in by_type.items():
        type_zh   = TYPE_ZH.get(entry_type, entry_type)
        filename  = f"{novel_title}_{type_zh}.json"
        out_path  = wb_output_dir / filename

        lorebook = build_lorebook(
            entries_by_type=entries,
            entry_type=entry_type,
            novel_title=novel_title,
            insertion_order_map=insertion_order_map,
        )
        write_json(out_path, lorebook)
        output_files.append(filename)
        log.success(f"✓ {filename}（{len(entries)} 個條目）")

    # 清理暫存快取
    if cache_path.exists():
        cache_path.unlink()
        log.debug("已清除生成快取")

    # 統計
    log.section("世界書生成完成")
    log.success(f"成功：{success_count} 個條目")
    if failed_count:
        log.warning(
            f"失敗：{failed_count} 個條目（已跳過）\n"
            f"  → 重跑此模組可自動補處理失敗條目"
        )
    log.info(f"輸出檔案：{output_files}")
    log.info(f"所有世界書已儲存至 {wb_output_dir}/")


if __name__ == "__main__":
    main()
