# core/file_utils.py
# 統一檔案讀寫工具

import json
import re
from pathlib import Path


# ---------- 基本讀寫 ----------

def read_text(path: str | Path, encoding: str = "utf-8") -> str:
    with open(path, "r", encoding=encoding) as f:
        return f.read()


def write_text(path: str | Path, content: str, encoding: str = "utf-8"):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding=encoding) as f:
        f.write(content)


def read_json(path: str | Path) -> any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str | Path, data: any, indent: int = 2):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)


def append_to_json_list(path: str | Path, record: dict):
    """
    將 record 追加到一個 JSON 陣列檔案。
    若檔案不存在則建立；若已存在則讀取後追加。
    """
    path = Path(path)
    if path.exists():
        existing = read_json(path)
        if not isinstance(existing, list):
            existing = [existing]
        existing.append(record)
    else:
        existing = [record]
    write_json(path, existing)


# ---------- 檔名工具 ----------

def safe_filename(name: str, max_len: int = 100) -> str:
    """
    轉換為安全的檔案名稱：
    - 移除 Windows 不允許的字符
    - 避開保留名稱（CON, PRN 等）
    - 限制長度
    """
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()

    reserved = {
        "CON", "PRN", "AUX", "NUL",
        *[f"COM{i}" for i in range(1, 10)],
        *[f"LPT{i}" for i in range(1, 10)],
    }
    if safe.upper() in reserved:
        safe = f"角色_{safe}"

    if len(safe) > max_len:
        safe = safe[:max_len]

    return safe or "unnamed"


# ---------- 目錄管理 ----------

def ensure_dirs(*paths: str | Path):
    """確保多個目錄存在"""
    for p in paths:
        Path(p).mkdir(parents=True, exist_ok=True)


def list_files(directory: str | Path, pattern: str = "*.json") -> list[Path]:
    """列出目錄下符合 pattern 的檔案，依名稱排序"""
    return sorted(Path(directory).glob(pattern))
