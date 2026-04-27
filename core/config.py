# core/config.py
# 統一設定載入模組

import yaml
from pathlib import Path

_config_cache: dict | None = None
_config_path: str = "config.yaml"


def set_config_path(path: str):
    """允許在測試或特殊情境下指定不同的 config 路徑"""
    global _config_path, _config_cache
    _config_path = path
    _config_cache = None  # 清除快取，強制重新載入


def load_config(force_reload: bool = False) -> dict:
    """
    載入並快取 config.yaml。
    同一次執行只讀取一次檔案，避免重複 I/O。
    force_reload=True 可強制重新讀取（用於測試）。
    """
    global _config_cache
    if _config_cache is None or force_reload:
        path = Path(_config_path)
        if not path.exists():
            raise FileNotFoundError(
                f"找不到設定檔：{_config_path}\n"
                "請確認 config.yaml 存在於執行目錄中。"
            )
        with open(path, "r", encoding="utf-8") as f:
            _config_cache = yaml.safe_load(f)
    return _config_cache


def get(key: str, default=None):
    """快捷取得單一設定值"""
    return load_config().get(key, default)
