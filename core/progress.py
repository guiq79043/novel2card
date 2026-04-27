# core/progress.py
# 斷點續傳進度管理
#
# 每個擷取模組（02a / 02b）各自維護一個 progress JSON 檔案，
# 記錄每個 chunk 的處理狀態，格式如下：
#
# {
#   "chunk_001": {
#     "status": "success" | "failed" | "skipped",
#     "timestamp": "2024-01-01 12:00:00",
#     "note": "可選的備註（如失敗原因）"
#   },
#   ...
# }
#
# 重跑時：
#   - status = "success" → 跳過
#   - status = "failed"  → 重新處理
#   - status = "skipped" → 重新處理

import time
from pathlib import Path

from core.file_utils import read_json, write_json, ensure_dirs
import core.logger as log

STATUS_SUCCESS = "success"
STATUS_FAILED  = "failed"
STATUS_SKIPPED = "skipped"


class ProgressTracker:
    """
    單一擷取模組的進度追蹤器。
    每次存檔都立即寫入磁碟，確保中途中斷不遺失進度。
    """

    def __init__(self, progress_file: str | Path):
        self.path = Path(progress_file)
        self._data: dict = {}
        self._load()

    # ── 載入 ──────────────────────────────────────────────

    def _load(self):
        """載入現有進度檔，若不存在則從空開始。"""
        if self.path.exists():
            try:
                loaded = read_json(self.path)
                if isinstance(loaded, dict):
                    self._data = loaded
                    log.debug(
                        f"已載入進度檔：{self.path.name}，"
                        f"共 {len(self._data)} 筆記錄"
                    )
                else:
                    log.warning(f"進度檔格式異常（非 dict），將重新建立：{self.path}")
                    self._data = {}
            except Exception as e:
                log.warning(f"進度檔讀取失敗，將重新建立：{e}")
                self._data = {}
        else:
            log.debug(f"進度檔不存在，將新建：{self.path}")

    # ── 查詢 ──────────────────────────────────────────────

    def is_done(self, chunk_name: str) -> bool:
        """
        回傳 True 代表此 chunk 已成功完成，可跳過。
        failed / skipped / 不存在 都應重新處理。
        """
        entry = self._data.get(chunk_name)
        if entry is None:
            return False
        return entry.get("status") == STATUS_SUCCESS

    def get_status(self, chunk_name: str) -> str | None:
        entry = self._data.get(chunk_name)
        return entry.get("status") if entry else None

    def pending_chunks(self, all_chunks: list[str]) -> list[str]:
        """回傳尚未成功完成的 chunk 清單（保持原始順序）。"""
        return [c for c in all_chunks if not self.is_done(c)]

    def summary(self) -> dict:
        """回傳各狀態的數量統計。"""
        counts = {STATUS_SUCCESS: 0, STATUS_FAILED: 0, STATUS_SKIPPED: 0}
        for entry in self._data.values():
            s = entry.get("status", STATUS_SKIPPED)
            if s in counts:
                counts[s] += 1
        return counts

    # ── 更新 ──────────────────────────────────────────────

    def mark_success(self, chunk_name: str, note: str = ""):
        self._update(chunk_name, STATUS_SUCCESS, note)

    def mark_failed(self, chunk_name: str, note: str = ""):
        self._update(chunk_name, STATUS_FAILED, note)

    def mark_skipped(self, chunk_name: str, note: str = ""):
        self._update(chunk_name, STATUS_SKIPPED, note)

    def _update(self, chunk_name: str, status: str, note: str):
        self._data[chunk_name] = {
            "status":    status,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "note":      note,
        }
        self._save()

    # ── 清理舊記錄 ────────────────────────────────────────

    def clean_stale(self, valid_chunks: set[str]):
        """
        移除進度檔中已不存在於 chunks 目錄的舊記錄。
        避免上一次小說的殘留記錄干擾新的一次執行。
        """
        stale = [k for k in self._data if k not in valid_chunks]
        if stale:
            log.info(f"清理 {len(stale)} 筆舊進度記錄：{stale[:5]}{'...' if len(stale) > 5 else ''}")
            for k in stale:
                del self._data[k]
            self._save()

    # ── 存檔 ──────────────────────────────────────────────

    def _save(self):
        ensure_dirs(self.path.parent)
        write_json(self.path, self._data)
