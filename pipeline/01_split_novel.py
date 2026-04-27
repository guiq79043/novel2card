# pipeline/01_split_novel.py
# 小說分割模組
#
# 將原始小說文字依章節和字數上限切分成多個 chunk，
# 供後續擷取模組逐段處理。
#
# 輸出：
#   data/chunks/chunk_001.txt ... chunk_NNN.txt
#   data/chunks/mapping.json   ← 每個 chunk 包含的章節標題清單

import re
from pathlib import Path

from core.config import get, load_config
from core.file_utils import write_text, write_json, ensure_dirs
import core.logger as log


# ============================================================
#  章節識別
# ============================================================

def create_chapter_pattern() -> re.Pattern:
    """建立支援多種章節格式的正則表達式（原始邏輯完整保留）"""
    patterns = [
        r'^第\d+章',
        r'^第[一二三四五六七八九十零〇壹貳參肆伍陸柒捌玖拾百千萬]+章',
        r'^第\d+回',
        r'^第[一二三四五六七八九十零〇壹貳參肆伍陸柒捌玖拾百千萬]+回',
        r'^章節\d+',
        r'^Chapter\s*\d+',
        r'^\d+\.',
        r'^[一二三四五六七八九十零〇壹貳參肆伍陸柒捌玖拾百千萬]+、',
        r'^\d+、',
    ]
    combined = '|'.join([f'({p})' for p in patterns])
    return re.compile(combined, re.IGNORECASE)


def make_chapter_checker(config: dict):
    """
    回傳 is_chapter_line(line) 函數。
    若 config 有自定義 chapter_regex 則同時檢查兩種模式。
    """
    chapter_pattern = create_chapter_pattern()
    custom_regex    = config.get("chapter_regex", "")

    if custom_regex:
        custom_pattern = re.compile(custom_regex)
        log.info(f"已載入自定義章節正則：{custom_regex}")
        def is_chapter_line(line: str) -> bool:
            return bool(chapter_pattern.match(line) or custom_pattern.match(line))
    else:
        def is_chapter_line(line: str) -> bool:
            return bool(chapter_pattern.match(line))

    return is_chapter_line


# ============================================================
#  chunk 儲存
# ============================================================

def save_chunk(
    text: str,
    chapter_titles: list[str],
    chunk_idx: int,
    output_dir: Path,
    mapping: dict,
):
    filename = f"chunk_{chunk_idx:03d}.txt"
    write_text(output_dir / filename, text)
    mapping[filename] = chapter_titles


# ============================================================
#  主流程
# ============================================================

def main():
    config    = load_config()
    input_file = Path(get("input_file", "a.txt"))
    output_dir = Path(get("chunk_output_dir", "data/chunks"))
    max_chars  = int(get("max_chunk_chars", 32767))
    mapping_file = Path(get("mapping_file", "data/chunks/mapping.json"))

    ensure_dirs(output_dir)

    # 檢查輸入檔案
    if not input_file.exists():
        log.error(f"找不到小說檔案：{input_file}")
        return

    is_chapter_line = make_chapter_checker(config)

    # 讀取小說
    with open(input_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    log.section(f"小說分割開始｜來源：{input_file}｜上限：{max_chars} 字/段")

    # 分割演算法（原始邏輯完整保留）
    mapping       = {}
    current_chunk = ""
    current_titles: list[str] = []
    chunk_count   = 1
    buffer        = ""

    for line in log.progress(lines, desc="掃描章節", unit="行"):
        line_stripped = line.strip()

        if line_stripped and is_chapter_line(line_stripped):
            if len(current_chunk) + len(buffer) > max_chars and current_chunk:
                save_chunk(current_chunk, current_titles, chunk_count, output_dir, mapping)
                chunk_count   += 1
                current_chunk  = ""
                current_titles = []

            if buffer:
                current_chunk += buffer
                buffer         = ""

            current_titles.append(line_stripped)

        buffer += line

        if len(current_chunk) + len(buffer) > max_chars:
            if current_chunk:
                save_chunk(current_chunk, current_titles, chunk_count, output_dir, mapping)
                chunk_count   += 1
                current_chunk  = buffer
                current_titles = []
                buffer         = ""
            else:
                current_chunk += buffer
                buffer         = ""

    # 處理剩餘內容
    if buffer:
        current_chunk += buffer
    if current_chunk.strip():
        save_chunk(current_chunk, current_titles, chunk_count, output_dir, mapping)

    # 儲存 mapping
    write_json(mapping_file, mapping)

    log.section("小說分割完成")
    log.success(f"共產生 {chunk_count} 個 chunk → {output_dir}/")
    log.info(f"章節對應表已儲存至 {mapping_file}")


if __name__ == "__main__":
    main()
