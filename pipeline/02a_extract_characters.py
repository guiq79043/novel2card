# pipeline/02a_extract_characters.py
# 角色資料擷取模組
#
# 逐一將 data/chunks/ 的小說段落送給性價比模型，
# 擷取角色資訊並儲存為 JSON。
#
# 輸出：
#   data/responses/characters/{chunk_name}.json  ← 解析後的角色資料
#   data/raw_responses/characters/{chunk_name}.txt ← 模型原始回應（備查）
#   data/bad_chunks/characters/{chunk_name}.txt  ← 三層搶救仍失敗的 chunk
#   data/progress_characters.json                ← 斷點續傳進度

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


# ============================================================
#  角色資料驗證
# ============================================================

def _validate_characters(data, chunk_name: str) -> list | None:
    """
    驗證模型回傳的資料中是否包含合法的 characters 陣列。
    回傳清理後的 list，或 None（資料無效）。
    """
    # 允許兩種格式：
    # 1. {"characters": [...], "world_entries": [...]}  ← 標準格式
    # 2. [...]  ← 模型直接回傳陣列（少數情況）
    if isinstance(data, dict):
        characters = data.get("characters", [])
    elif isinstance(data, list):
        characters = data
        log.debug(f"{chunk_name}：模型直接回傳陣列，已自動適配")
    else:
        log.error(f"{chunk_name}：頂層格式既非 dict 也非 list")
        return None

    if not isinstance(characters, list):
        log.error(f"{chunk_name}：characters 欄位不是陣列")
        return None

    # 過濾掉明顯無效的條目（沒有 name 欄位或 name 為空）
    valid = []
    for item in characters:
        if not isinstance(item, dict):
            continue
        name = item.get("name", "").strip()
        if not name:
            log.debug(f"{chunk_name}：跳過一個沒有名字的角色條目")
            continue
        # 確保所有必要欄位存在
        item.setdefault("aliases",       [])
        item.setdefault("appearance",    "")
        item.setdefault("personality",   "")
        item.setdefault("speech_pattern","")
        item.setdefault("background",    "")
        valid.append(item)

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
    char_response_dir: Path,
    char_raw_dir: Path,
    char_bad_dir: Path,
) -> bool:
    """
    處理單一 chunk，回傳 True 代表成功。
    """
    prompt = get_extract_prompt()
    messages = [
        {
            "role": "system",
            "content": (
                "你是一位專業的小說角色擷取 AI。"
                "請嚴格按照指定的 JSON 格式輸出結果，"
                "禁止輸出任何額外說明、前言或 markdown 標記。"
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
        _save_bad(chunk_name, chunk_text, "API 呼叫失敗", char_bad_dir)
        return False

    # 儲存原始回應
    write_text(char_raw_dir / f"{chunk_name}.txt", raw_content)

    # 解析 JSON（三層搶救，使用高性能模型修復）
    parsed = parse_json_response(raw_content, analyze_client, pro_model)
    if parsed is None:
        log.error(f"{chunk_name}：JSON 解析徹底失敗")
        _save_bad(chunk_name, chunk_text, "JSON 解析失敗", char_bad_dir)
        return False

    # 驗證角色資料
    characters = _validate_characters(parsed, chunk_name)
    if characters is None:
        log.error(f"{chunk_name}：資料格式驗證失敗")
        _save_bad(chunk_name, chunk_text, "資料格式驗證失敗", char_bad_dir)
        return False

    if len(characters) == 0:
        log.debug(f"{chunk_name}：此段落未發現有效角色，視為成功（空結果）")

    # 儲存結果（包含來源標記）
    result = {
        "chunk": chunk_name,
        "characters": characters,
    }
    write_json(char_response_dir / f"{chunk_name}.json", result)
    log.success(f"{chunk_name}：擷取 {len(characters)} 個角色")
    return True


def _save_bad(chunk_name: str, chunk_text: str, reason: str, bad_dir: Path):
    """將失敗的 chunk 原文存到 bad_chunks 供後續手動處理。"""
    content = f"# 失敗原因：{reason}\n\n{chunk_text}"
    write_text(bad_dir / f"{chunk_name}.txt", content)
    log.warning(f"{chunk_name}：已存入 bad_chunks（{reason}）")


# ============================================================
#  主流程
# ============================================================

def main():
    load_config()

    # 路徑
    chunk_dir         = Path(get("chunk_output_dir", "data/chunks"))
    char_response_dir = Path(get("char_response_dir","data/responses/characters"))
    char_raw_dir      = Path(get("char_raw_dir",     "data/raw_responses/characters"))
    char_bad_dir      = Path(get("char_bad_dir",     "data/bad_chunks/characters"))
    progress_file     = get("char_progress_file",    "data/progress_characters.json")
    ensure_dirs(char_response_dir, char_raw_dir, char_bad_dir)

    # 模型設定
    model         = get("model")
    pro_model     = get("pro_model")
    thinking_mode = get("extract_thinking_mode", "auto")
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

    # 初始化進度追蹤
    tracker = ProgressTracker(progress_file)

    # 清理舊記錄（處理上一本小說的殘留）
    tracker.clean_stale(set(chunk_names))

    # 找出待處理的 chunk
    pending = tracker.pending_chunks(chunk_names)
    already_done = len(chunk_names) - len(pending)

    log.section(
        f"角色資料擷取開始\n"
        f"  總計：{len(chunk_names)} 個 chunk\n"
        f"  已完成：{already_done} 個（跳過）\n"
        f"  待處理：{len(pending)} 個"
    )

    if not pending:
        log.success("所有 chunk 已處理完畢，無需重跑")
        return

    success_count = 0
    failed_count  = 0

    for chunk_name in log.progress(pending, desc="擷取角色資料", unit="chunk"):
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
            char_response_dir=char_response_dir,
            char_raw_dir=char_raw_dir,
            char_bad_dir=char_bad_dir,
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
    log.section("角色資料擷取完成")
    log.success(f"成功：{summary['success']} 個 chunk")
    if failed_count:
        log.warning(
            f"失敗：{failed_count} 個 chunk\n"
            f"  → 失敗的原文已儲存至 {char_bad_dir}/\n"
            f"  → 修正 prompt 或手動調整後，重跑此模組即可自動補處理"
        )
    log.info(f"結果已儲存至 {char_response_dir}/")


if __name__ == "__main__":
    main()
