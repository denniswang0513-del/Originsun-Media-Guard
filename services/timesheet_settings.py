"""services/timesheet_settings.py — settings.json `timesheet.<block>` 的讀／改／記狀態，一份給
拉取（pull）與週一 digest 用：可改的鍵、cron 驗證、last_run_at／last_summary 的形狀都只有這裡。
"""
from __future__ import annotations

from config import load_settings, save_settings


class SettingsBlock:
    """`timesheet.<key>` 那一小塊：`get()` 回帶預設的 dict、`update(patch)` 只收 settable、
    `mark(**fields)` 記 runner 狀態。cron 改動先驗（ValueError 往上丟，端點回 422）。"""

    def __init__(self, key: str, settable: tuple, defaults: dict):
        self.key, self.settable, self.defaults = key, settable, defaults

    def _raw(self, s=None) -> dict:
        return ((s or load_settings()).get("timesheet") or {}).get(self.key) or {}

    def get(self) -> dict:
        p = self._raw()
        out = {k: (type(v)(p.get(k)) if p.get(k) is not None else v) for k, v in self.defaults.items()}
        out["last_run_at"] = float(p.get("last_run_at") or 0)
        out["last_summary"] = str(p.get("last_summary") or "")
        return out

    def update(self, patch: dict) -> dict:
        if patch.get("cron"):
            from services.website._runner_util import validate_cron
            validate_cron(str(patch["cron"]))
        s = load_settings()
        p = s.setdefault("timesheet", {}).setdefault(self.key, {})
        for k in self.settable:
            if k in patch and patch[k] is not None:
                p[k] = bool(patch[k]) if isinstance(self.defaults.get(k), bool) else str(patch[k]).strip()
        save_settings(s)
        return self.get()

    def mark(self, **fields) -> None:
        s = load_settings()
        s.setdefault("timesheet", {}).setdefault(self.key, {}).update(fields)
        save_settings(s)
