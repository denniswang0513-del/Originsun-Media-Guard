"""routers/crm/project_import.py — 專案 CSV 匯入（/projects/import_csv）。2026-09-13 從 projects.py 原樣切出（純搬移）。
掃原始碼的測試用 `_srcscan.projects_src()`。
"""
from __future__ import annotations

import csv
import io
import uuid

from fastapi import Request, UploadFile, File

from ._shared import (router, _check_auth, _require_db, _get_factory, _now,
                      _parse_shoot_date, _auto_update_client_status, map_csv_row)

try:
    from ._shared import select, Client, CrmProject
except ImportError:  # DB 套件不存在的 agent 環境 — 同 projects.py
    pass


# ── Project CSV Import ──────────────────────────────────────

_PROJECT_COL_MAP = {
    "name":           ["專案名稱", "name", "案名", "專案", "project_name", "project"],
    "client_name":    ["客戶", "客戶代稱", "client", "client_name"],
    "status":         ["狀態", "status"],
    "am_username":    ["am", "業務", "am_username"],
    "shoot_date":     ["拍攝日期", "拍攝日", "shoot_date", "拍攝"],
    "folder_path":    ["資料夾", "folder", "folder_path", "路徑"],
    "description":    ["說明", "描述", "description"],
    "notes":          ["備註", "notes"],
}


def _map_project_row(header_map: dict, row: dict) -> dict:
    return map_csv_row(_PROJECT_COL_MAP, header_map, row)


@router.post("/projects/import_csv")
async def import_projects_csv(request: Request, file: UploadFile = File(...)):
    _check_auth(request)
    _require_db()

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("big5", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    header_map = {h.lower(): h for h in headers}
    imported = updated = skipped = 0

    factory = await _get_factory()

    async with factory() as session:
        clients = (await session.execute(select(Client))).scalars().all()
        client_map = {c.short_name: c.id for c in clients}

        existing_map: dict = {
            p.name: p
            for p in (await session.execute(select(CrmProject))).scalars().all()
        }

        affected: set = set()
        for row in reader:
            data = _map_project_row(header_map, row)
            if not data.get("name"):
                skipped += 1
                continue

            client_name = data.pop("client_name", "")
            client_id = client_map.get(client_name, "")
            shoot_dt = _parse_shoot_date(data.pop("shoot_date", None))

            now = _now()
            existing = existing_map.get(data["name"])
            if existing:
                if existing.client_id:
                    affected.add(existing.client_id)   # 轉移前的舊客戶也要重算
                for k, v in data.items():
                    if k != "name" and v:
                        setattr(existing, k, v)
                if client_id:
                    existing.client_id = client_id
                    affected.add(client_id)
                if shoot_dt:
                    existing.shoot_date = shoot_dt
                existing.updated_at = now
                updated += 1
            else:
                if not client_id:
                    skipped += 1
                    continue
                proj_data = {k: v for k, v in data.items() if k != "name"}
                new_proj = CrmProject(
                    id=uuid.uuid4().hex, name=data["name"],
                    client_id=client_id, shoot_date=shoot_dt,
                    created_at=now, updated_at=now, **proj_data,
                )
                session.add(new_proj)
                existing_map[data["name"]] = new_proj
                affected.add(client_id)
                imported += 1

        # 匯入後重算受影響客戶分級（過去 CSV 匯入沒觸發 → 是舊資料卡在潛在客戶的主因）
        for cid in affected:
            if cid:
                await _auto_update_client_status(session, cid)
        await session.commit()

    return {"status": "ok", "imported": imported, "updated": updated, "skipped": skipped}
