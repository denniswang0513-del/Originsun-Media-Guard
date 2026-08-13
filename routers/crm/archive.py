"""routers/crm/archive.py — 結案歸檔清單 + 專案回顧（KPTA）

對齊 owner 的 Notion 專案啟動面版兩塊（「歸檔資料確認事項」「專案回顧」），
掛在專案詳情的「完稿結案」分頁最上方。

範本與判定邏輯的正本在 `core.project_archive`（純函式、有單元測試）；這裡只做
DB 讀寫與資料夾 IO。資料存在 `crm_projects.archive_checklist / review_kpta`。

資料夾整合走既有的提案資產夾（`routers.crm.proposal_assets`）：歸檔子夾都建在
`{資產夾}/歸檔/` 底下，掃描時只看那一層 —— 專案的檔案本來就在同一個夾，
再開第二個根目錄只會讓人不知道東西該放哪。

⚠️ 這些端點**不在**對外白名單（tests/unit/test_public_surface.py 有一條
blacklist 明確擋 `/archive`）—— 歸檔狀態是內部資訊，不給客戶連結看。
"""
from __future__ import annotations

import asyncio
import os
import uuid

from fastapi import HTTPException, Request

from core import project_archive as pa
from core.project_folders import create_subfolder

from ._shared import (router, _check_auth, _get_factory, _now,
                      _project_or_404, _require_db)


def _payload(project) -> dict:
    """歸檔分頁的完整狀態 —— 讀與寫都回這一份，前端不必再打一次 GET。"""
    checklist = pa.rows(project.archive_checklist)
    return {
        "checklist": checklist,
        "statuses": list(pa.STATUSES),
        "progress": pa.progress_of(checklist),
        "kpta": pa.kpta(project.review_kpta),
        "kpta_fields": [{"key": k, "label": lb} for k, lb in pa.KPTA_FIELDS],
        "root_folder": pa.ROOT_FOLDER,
    }


async def _write(project_id: str, attr: str, call):
    """讀某個 JSONB 欄 → `call(舊值) -> (新值, 錯誤)` → commit → 回完整狀態。

    `call` 的簽名刻意對齊 core.project_archive 那批純函式，端點就只剩一行 lambda。
    錯誤字串非空 → 422。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        project = await _project_or_404(session, project_id)
        new, err = call(getattr(project, attr))
        if err:
            raise HTTPException(status_code=422, detail=err)
        setattr(project, attr, new)
        project.updated_at = _now()
        await session.commit()
        return _payload(project)


@router.get("/projects/{project_id}/archive")
async def get_project_archive(project_id: str, request: Request):
    """歸檔清單 + 回顧（範本每次讀時對齊 —— 之後加項目，舊專案也會長出來）。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        return _payload(await _project_or_404(session, project_id))


@router.patch("/projects/{project_id}/archive")
async def patch_project_archive(project_id: str, request: Request):
    """單格寫入：{key, field(status|note), value}。last-write-wins。"""
    _check_auth(request)
    body = await request.json()
    return await _write(project_id, "archive_checklist",
                        lambda cur: pa.apply_patch(cur, body.get("key"),
                                                   body.get("field"), body.get("value")))


@router.post("/projects/{project_id}/archive/rows")
async def add_archive_row(project_id: str, request: Request):
    """加一列自訂歸檔項目：{label}。"""
    _check_auth(request)
    body = await request.json()
    return await _write(project_id, "archive_checklist",
                        lambda cur: pa.add_row(cur, body.get("label"),
                                               key_seed=uuid.uuid4().hex))


@router.delete("/projects/{project_id}/archive/rows/{key}")
async def delete_archive_row(project_id: str, key: str, request: Request):
    """刪一列自訂歸檔項目（範本列不給刪，標「不適用」即可）。"""
    _check_auth(request)
    return await _write(project_id, "archive_checklist",
                        lambda cur: pa.remove_row(cur, key))


@router.patch("/projects/{project_id}/review")
async def patch_project_review(project_id: str, request: Request):
    """專案回顧（KPTA）單欄寫入：{key, value}。"""
    _check_auth(request)
    body = await request.json()
    return await _write(project_id, "review_kpta",
                        lambda cur: pa.apply_kpta(cur, body.get("key"), body.get("value")))


# ── 資料夾整合（建結構 / 掃描自動勾）──────────────────────

def _scan_non_empty(archive_abs: str) -> list:
    """歸檔夾底下哪些**範本**子夾「有東西」（檔案或再下一層資料夾都算）。
    夾不存在 → 空清單（不是錯誤：還沒建過而已）。

    🔴 這裡刻意**不用** core.list_folder_level：那支每一層都會做兩次 realpath
    （UNC 上各是一趟 SMB open）+ 完整 scandir + 建 dict + 排序，而我們只要一個
    bool。走裸 scandir、只看範本認得的夾、第一筆就停 —— 5 個夾從 21 趟 SMB
    降到 ≤6 趟。路徑安全無虞：子夾名是 TEMPLATE 的字面常數，沒有使用者輸入。
    """
    out = []
    try:
        with os.scandir(archive_abs) as it:
            names = [e.name for e in it if e.is_dir() and e.name in pa.KNOWN_FOLDERS]
    except OSError:
        return []
    for name in names:
        try:
            with os.scandir(os.path.join(archive_abs, name)) as sub:
                if next(sub, None) is not None:
                    out.append(name)
        except OSError:
            continue
    return out


def _build_dirs(archive_abs: str) -> None:
    """把範本的五個子夾一次建完（同一個 thread hop，名字都是程式給的常數）。"""
    for _k, _l, _h, folder in pa.TEMPLATE:
        if folder:
            os.makedirs(os.path.join(archive_abs, folder), exist_ok=True)


async def _archive_dir(project_id: str, *, create: bool) -> str:
    """`{專案資產夾}/歸檔` 的絕對路徑。create=False 且沒建過 → ""。"""
    from routers.crm.proposal_assets import (project_folder_abs, project_folder_ready)
    if create:
        folder = await project_folder_ready(project_id)
    else:
        _require_db()
        factory = await _get_factory()
        async with factory() as session:
            folder = await project_folder_abs(session, project_id)
    if not folder:
        return ""
    archive = os.path.join(folder, pa.ROOT_FOLDER)
    if create:
        # 資產夾這一層用 create_subfolder（名稱規則與後台開夾同一份）
        _name, err = await asyncio.to_thread(create_subfolder, folder, pa.ROOT_FOLDER)
        if err and not await asyncio.to_thread(os.path.isdir, archive):
            raise HTTPException(status_code=400, detail=f"建立歸檔資料夾失敗：{err}")
    return archive


@router.post("/projects/{project_id}/archive/folders")
async def build_archive_folders(project_id: str, request: Request):
    """一鍵建歸檔資料夾結構（`{資產夾}/歸檔/01_PPM資料…`）並立刻掃一次。
    已存在的夾照舊（makedirs exist_ok）。"""
    _check_auth(request)
    archive = await _archive_dir(project_id, create=True)
    if not archive:
        raise HTTPException(status_code=400,
                            detail="專案資產資料夾無法建立（根目錄未設定或搆不到）")
    await asyncio.to_thread(_build_dirs, archive)
    return await _scan_and_mark(project_id, archive)


@router.post("/projects/{project_id}/archive/scan")
async def scan_archive_folders(project_id: str, request: Request):
    """掃描歸檔夾 → 有檔案的項目自動標「已收」（只往前推進，不倒退人工標記）。"""
    _check_auth(request)
    archive = await _archive_dir(project_id, create=False)
    # SMB stat 一律進 thread —— 留在 event loop 上，NAS 一慢就凍住整個 agent
    if not archive or not await asyncio.to_thread(os.path.isdir, archive):
        raise HTTPException(status_code=404,
                            detail=f"還沒有「{pa.ROOT_FOLDER}」資料夾 — 先按「建立歸檔資料夾」")
    return await _scan_and_mark(project_id, archive)


async def _scan_and_mark(project_id: str, archive_abs: str) -> dict:
    non_empty = await asyncio.to_thread(_scan_non_empty, archive_abs)
    marked: list = []

    def call(cur):
        new, hit = pa.apply_scan(cur, non_empty)
        marked.extend(hit)
        return new, ""
    out = await _write(project_id, "archive_checklist", call)
    out["scanned"] = non_empty
    out["marked"] = marked
    return out
