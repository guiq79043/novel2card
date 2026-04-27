# core/logger.py
# 統一日誌系統，相容 tqdm 進度條輸出

import time
import os
from pathlib import Path
from tqdm import tqdm as _tqdm

from core.config import get


def _get_log_file() -> str:
    return get("log_file", "data/logs/system.log")


def _ensure_log_dir():
    log_file = _get_log_file()
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)


def _write_log(msg: str):
    _ensure_log_dir()
    with open(_get_log_file(), "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------- 基本日誌 ----------

def info(msg: str):
    """一般資訊，印到 console（tqdm 安全）並寫入 log"""
    line = f"[{_timestamp()}] ℹ️  {msg}"
    _tqdm.write(line)
    _write_log(line)


def success(msg: str):
    """成功訊息"""
    line = f"[{_timestamp()}] ✅ {msg}"
    _tqdm.write(line)
    _write_log(line)


def warning(msg: str):
    """警告（不中斷流程）"""
    line = f"[{_timestamp()}] ⚠️  {msg}"
    _tqdm.write(line)
    _write_log(line)


def error(msg: str):
    """錯誤（不中斷流程，由呼叫端決定是否繼續）"""
    line = f"[{_timestamp()}] ❌ {msg}"
    _tqdm.write(line)
    _write_log(line)


def debug(msg: str):
    """除錯資訊，只在 config debug_mode: true 時顯示"""
    from core.config import get
    if get("debug_mode", False):
        line = f"[{_timestamp()}] 🔍 {msg}"
        _tqdm.write(line)
        _write_log(line)


def section(title: str):
    """分隔線，標示新的處理階段"""
    line = f"\n{'='*50}\n  {title}\n{'='*50}"
    _tqdm.write(line)
    _write_log(line)


# ---------- 進度條工廠 ----------

def progress(iterable, desc: str, total: int = None, unit: str = "個"):
    """
    回傳一個 tqdm 進度條，統一樣式。
    用法：
        for item in progress(items, desc="處理 chunk"):
            ...
    """
    return _tqdm(
        iterable,
        desc=desc,
        total=total,
        unit=unit,
        ncols=80,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} {unit} [{elapsed}<{remaining}]",
        dynamic_ncols=False,
    )
