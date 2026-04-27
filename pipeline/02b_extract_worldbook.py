# pipeline/02b_extract_worldbook.py
# 世界書資料擷取模組
#
# 逐一將 data/chunks/ 的小說段落送給性價比模型，
# 擷取地點、事件、勢力、規則、道具等世界觀資料。
#
# 此模組為獨立選用，不影響角色卡生成流程（02a → 03 → 04）。
#
# 輸出：
#   data/responses/worldbook/{chunk_name}.json    ← 解析後的世界書條目
#   data/raw_responses/worldbook/{chunk_name}.txt ← 模型原始回應（備查）
#   data/bad_chunks/worldbook/{chunk_name}.txt    ← 三層搶救仍失敗的 chunk
#   data/progress_worldbook.json                  ← 斷點續傳進度（獨立於 02a）

import time
from pathlib import Path

from core.config import get, load_config
from core.file_utils import (
    read_text, write_text, write_json,
    list_files, ensure_dirs,
)
from core.api_client import (
    make_extract_client, make_analyze_client,
    call_api, parse_json_response,
)
from core.prompts import get_extract_prompt
from core.progress import ProgressTracker
import core.logger as log

# 世界書條目的合法 type 值
_VALID_TYPES = {"location", "event", "faction", "rule", "item", "other"}


# ============================================================
#  世界書資料驗證
# ============================================================

def _validate_world_entries(data, chunk_name: str) -> list | None:
    """
    驗證並清理模型回傳的 world_entries 資料。
    回傳清理後的 list，或 None（資料無效）。
    """
    if isinstance(data, dict):
        entries = data.get("world_entries", [])
    elif isinstance(data, list):
        # 模型直接回傳陣列（少數情況）
        entries = data
        log.debug(f"{chunk_name}：模型直接回傳陣列，已自動適配")
    else:
        log.error(f"{chunk_name}：頂層格式既非 dict 也非 list")
        return None

    if not isinstance(entries, list):
        log.error(f"{chunk_name}：world_entries 欄位不是陣列")
        return None

    valid = []
    for item in entries:
        if not isinstance(item, dict):
            continue

        name = item.get("name", "").strip()
        if not name:
            log.debug(f"{chunk_name}：跳過一個沒有名稱的世界書條目")
            continue

        content = item.get("content", "").strip()
        if not content:
            log.debug(f"{chunk_name}：條目「{name}」缺少 content，跳過")
            continue

        # 修正不合法的 type
        entry_type = item.get("type", "other")
        if entry_type not in _VALID_TYPES:
            log.debug(f"{chunk_name}：條目「{name}」的 type='{entry_type}' 不合法，改為 'other'")
            entry_type = "other"

        # 確保 keywords 是陣列
        keywords = item.get("keywords", [])
        if isinstance(keywords, str):
            keywords = [keywords]
        elif not isinstance(keywords, list):
            keywords = []

        valid.append({
            "type":     entry_type,
            "name":     name,
            "keywords": keywords,
            "content":  content,
        })

    return valid


# ============================================================
#  單一 chunk 處理
# ============================================================

def process_chunk(
    chunk_name: str,
    chunk_text: str,
    extract_client,
    analyze_client,
    model: str,
    pro_model: str,
    thinking_mode,
    wb_response_dir: Path,
    wb_raw_dir: Path,
    wb_bad_dir: Path,
) -> bool:
    """
    處理單一 chunk，擷取世界書條目。
    回傳 True 代表成功（包含空結果）。
    """
    # 世界書擷取使用相同的 extract prompt，
    # 但 system prompt 強調重點在 world_entries
    prompt = get_extract_prompt()
    messages = [
        {
            "role": "system",
            "content": (
                "你是一位專業的小說世界觀擷取 AI。"
                "請從段落中找出地點、事件、勢力組織、規則系統、重要道具等世界觀元素，"
                "填入 world_entries 欄位。characters 欄位可以回傳空陣列。"
                "請嚴格按照指定的 JSON 格式輸出，禁止任何額外說明或 markdown 標記。"
            ),
        },
        {
            "role": "user",
            "content": prompt + chunk_text,
        },
    ]

    # 呼叫 API
    raw_content = call_api(
        client=extract_client,
        model=model,
        messages=messages,
        temperature=0.3,
        max_tokens=8192,
        timeout=300,
        thinking_mode=thinking_mode,
    )

    if raw_content is None:
        log.error(f"{chunk_name}：API 呼叫失敗")
        _save_bad(chunk_name, chunk_text, "API 呼叫失敗", wb_bad_dir)
        return False

    # 儲存原始回應
    write_text(wb_raw_dir / f"{chunk_name}.txt", raw_content)

    # 解析 JSON
    parsed = parse_json_response(raw_content, analyze_client, pro_model)
    if parsed is None:
        log.error(f"{chunk_name}：JSON 解析徹底失敗")
        _save_bad(chunk_name, chunk_text, "JSON 解析失敗", wb_bad_dir)
        return False

    # 驗證世界書資料
    entries = _validate_world_entries(parsed, chunk_name)
    if entries is None:
        log.error(f"{chunk_name}：資料格式驗證失敗")
        _save_bad(chunk_name, chunk_text, "資料格式驗證失敗", wb_bad_dir)
        return False

    if len(entries) == 0:
        log.debug(f"{chunk_name}：此段落未發現世界書條目，視為成功（空結果）")

    # 儲存結果
    result = {
        "chunk":         chunk_name,
        "world_entries": entries,
    }
    write_json(wb_response_dir / f"{chunk_name}.json", result)
    log.success(f"{chunk_name}：擷取 {len(entries)} 個世界書條目")
    return True


def _save_bad(chunk_name: str, chunk_text: str, reason: str, bad_dir: Path):
    content = f"# 失敗原因：{reason}\n\n{chunk_text}"
    write_text(bad_dir / f"{chunk_name}.txt", content)
    log.warning(f"{chunk_name}：已存入 bad_chunks（{reason}）")


# ============================================================
#  主流程
# ============================================================

def main():
    load_config()

    # 路徑
    chunk_dir       = Path(get("chunk_output_dir", "data/chunks"))
    wb_response_dir = Path(get("wb_response_dir", "data/responses/worldbook"))
    wb_raw_dir      = Path(get("wb_raw_dir",      "data/raw_responses/worldbook"))
    wb_bad_dir      = Path(get("wb_bad_dir",       "data/bad_chunks/worldbook"))
    progress_file   = get("wb_progress_file",      "data/progress_worldbook.json")
    ensure_dirs(wb_response_dir, wb_raw_dir, wb_bad_dir)

    # 模型設定
    model          = get("model")
    pro_model      = get("pro_model")
    thinking_mode  = get("extract_thinking_mode", "auto")
    if thinking_mode in ("false", False):
        thinking_mode = False
    sleep_interval = float(get("api_sleep_interval", 2))

    # 驗證必要設定
    if not get("api_key") or not model:
        log.error("請在 config.yaml 設定 api_key 和 model")
        return
    if not get("pro_api_key") or not pro_model:
        log.error("請在 config.yaml 設定 pro_api_key 和 pro_model（用於 JSON 修復）")
        return

    # 建立客戶端
    extract_client = make_extract_client()
    analyze_client = make_analyze_client()

    # 找出所有 chunk 檔案
    chunk_files = list_files(chunk_dir, "*.txt")
    if not chunk_files:
        log.error(f"在 {chunk_dir} 中找不到 chunk 檔案，請先執行 01_split_novel.py")
        return

    chunk_names = [f.stem for f in chunk_files]

    # 初始化進度追蹤（獨立於 02a）
    tracker = ProgressTracker(progress_file)

    # 清理上一本小說的殘留記錄
    tracker.clean_stale(set(chunk_names))

    # 找出待處理的 chunk
    pending      = tracker.pending_chunks(chunk_names)
    already_done = len(chunk_names) - len(pending)

    log.section(
        f"世界書資料擷取開始\n"
        f"  總計：{len(chunk_names)} 個 chunk\n"
        f"  已完成：{already_done} 個（跳過）\n"
        f"  待處理：{len(pending)} 個"
    )

    if not pending:
        log.success("所有 chunk 已處理完畢，無需重跑")
        return

    success_count = 0
    failed_count  = 0

    for chunk_name in log.progress(pending, desc="擷取世界書資料", unit="chunk"):
        chunk_path = chunk_dir / f"{chunk_name}.txt"

        try:
            chunk_text = read_text(chunk_path)
        except Exception as e:
            log.error(f"{chunk_name}：讀取 chunk 失敗 → {e}")
            tracker.mark_failed(chunk_name, f"讀取失敗：{e}")
            failed_count += 1
            continue

        ok = process_chunk(
            chunk_name=chunk_name,
            chunk_text=chunk_text,
            extract_client=extract_client,
            analyze_client=analyze_client,
            model=model,
            pro_model=pro_model,
            thinking_mode=thinking_mode,
            wb_response_dir=wb_response_dir,
            wb_raw_dir=wb_raw_dir,
            wb_bad_dir=wb_bad_dir,
        )

        if ok:
            tracker.mark_success(chunk_name)
            success_count += 1
        else:
            tracker.mark_failed(chunk_name, "處理失敗，詳見 bad_chunks")
            failed_count += 1

        time.sleep(sleep_interval)

    # 統計
    summary = tracker.summary()
    log.section("世界書資料擷取完成")
    log.success(f"成功：{summary['success']} 個 chunk")
    if failed_count:
        log.warning(
            f"失敗：{failed_count} 個 chunk\n"
            f"  → 失敗的原文已儲存至 {wb_bad_dir}/\n"
            f"  → 修正 prompt 或手動調整後，重跑此模組即可自動補處理"
        )
    log.info(f"結果已儲存至 {wb_response_dir}/")


if __name__ == "__main__":
    main()
