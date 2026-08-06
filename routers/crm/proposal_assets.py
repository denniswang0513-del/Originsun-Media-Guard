"""routers/crm/proposal_assets.py — 提案庫資產資料夾（2026-08-06 owner 指定）

每個專案在 settings `proposals.root` 底下有一個 `{建立日}_{專案名}` 資料夾：
deck 上傳落在這裡，人也可以直接把企劃檔（腳本/分鏡/簡報原檔）丟進去。
專案改名 → 資料夾跟著改名（`rename_project_folder`，由 crm/projects.py 的
`_rename_asset_folders` 統一觸發）。

命名與改名的規則正本在 `core.project_folders`，與影像紀錄、片庫共用同一套。
資料夾名存在 `crm_projects.proposal_folder_name`（首次用到才生成）。

deck 的落點與相容性：
- 2026-08-06 起上傳的 deck 寫進這個資料夾，`deck_url` 存 **canonical 絕對路徑**
  （不以 `/` 開頭）→ 前端走 `GET /proposals/{pid}/deck/download` 帶權限下載。
- 之前的 deck 是 `/uploads/proposals/{pid}/x.pdf`（web root 靜態直出），
  `deck_url` 以 `/uploads/` 開頭 → 前端照舊直接開，不搬檔、不失效。
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime

from typing import List

from fastapi import File, HTTPException, Request, UploadFile

from config import load_settings, save_settings
from core.drive_map import to_canonical_path, to_local_path
from core.project_folders import (clean_filename, dedupe, list_folder_files,
                                  make_dated_folder_name, remap_prefix,
                                  rename_and_remap, safe_rel_path, safe_subfolder,
                                  taken_names, within_dir)

from ._shared import router, _check_auth, _get_factory, _now, _require_db

try:
    from ._shared import select, CrmProject
except ImportError:  # DB 套件不存在的 agent 環境
    pass


def proposals_root() -> str:
    """提案資產根目錄（已翻成本機視角，可直接開檔）。未設 → ""。

    走 settings.json 而不是 DB settings —— 判準（幾台機器會寫這個資料夾）
    寫在 core/project_folders 檔頭表格；提案資產夾只有 master 會寫。
    """
    conf = load_settings().get("proposals") or {}
    return to_local_path(str(conf.get("root") or "").strip())


def _raw_root() -> str:
    """設定裡存的原字串（master 視角，未翻譯）—— 給設定 UI 顯示/回填用。"""
    return str((load_settings().get("proposals") or {}).get("root") or "").strip()


async def _taken_names(session, exclude_project_id: str = "") -> set:
    return await taken_names(session, CrmProject.proposal_folder_name,
                             CrmProject.id, exclude_project_id)


# ── 資料夾內容（純掃磁碟，不建索引表）────────────────────────
# 提案資產夾裝的是企劃文件（腳本/分鏡/簡報原檔），不像影像紀錄需要縮圖、
# 分類與公開上傳 —— 不建 DB 檔案索引，每次即時列。少一張表就少一整類
# 「資料夾與 DB 不同步」的問題（照片被當新檔重複匯入那種）。

_UPLOAD_MAX_BYTES = 300 * 1024 * 1024      # 單檔 300MB（企劃檔可能含影片參考）
# 擋掉可執行檔（NAS 是共用磁碟，別讓它變成散播點）；其餘一律放行 ——
# 企劃檔格式太雜（.key/.indd/.aep/.srt…），白名單只會擋到自己人。
_BLOCKED_UPLOAD_EXTS = {
    ".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".ps1", ".vbs", ".js",
    ".jar", ".dll", ".lnk", ".reg", ".hta", ".cpl",
}


def _list_files(folder_abs: str) -> tuple:
    """資料夾內容（含子夾）；資料夾不存在 → ([], False)，不是錯誤
    （還沒建夾是正常狀態）。"""
    if not os.path.isdir(folder_abs):
        return [], False
    return list_folder_files(folder_abs)


def _deck_rel(deck_url: str, folder_abs: str) -> str:
    """目前指定為「提案簡報」的檔案在資料夾內的相對路徑（不在這個資料夾裡
    或還沒指定 → ""）。前端據此在列表上標星號。"""
    if not deck_url or not folder_abs or deck_url.startswith("/"):
        return ""
    local = to_local_path(deck_url)
    if not within_dir(folder_abs, local):
        return ""
    return os.path.relpath(local, folder_abs).replace("\\", "/")


async def _current_deck_url(session, project_id: str) -> str:
    """這個專案的衛星提案目前指定的 deck（N:1 取最近更新的一筆）。"""
    from db.models import PreprodProposal
    return (await session.execute(
        select(PreprodProposal.deck_url)
        .where(PreprodProposal.project_id == project_id,
               PreprodProposal.deck_url.isnot(None))
        .order_by(PreprodProposal.updated_at.desc())
        .limit(1))).scalar() or ""


async def _folder_for_project(session, project_id: str, *, create: bool) -> str:
    """專案的資產夾絕對路徑。create=True 會實際建出來（上傳用）；
    False 只算路徑（列檔/下載用，資料夾不存在也不算錯）。"""
    project = await session.get(CrmProject, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="找不到此專案")
    if create:
        path = await ensure_folder(session, project)
        if not path:
            raise HTTPException(status_code=400,
                                detail="提案資產資料夾無法建立（根目錄未設定或搆不到）")
        return path
    root = proposals_root()
    if not root or not project.proposal_folder_name:
        return ""
    return os.path.join(root, project.proposal_folder_name)


# ── 設定端點（比照影像紀錄：路徑帶 project_id 只是讓前端從專案面板順手改，
#    root 本身是全站共用的一份設定）──────────────────────────────

@router.get("/projects/{project_id}/proposal-assets")
async def get_proposal_assets(project_id: str, request: Request):
    """提案資產夾現況：根目錄、是否搆得到、這個專案的資料夾（還沒建就先算給看）。

    讀取放寬到專案管理模組 —— 一般同事要看得到「檔案在哪」才能按開啟資料夾；
    改路徑（POST）仍限管理員，那是全站共用的設定。"""
    from core.auth import check_admin_or_module
    check_admin_or_module(request, "crm_projects")
    _require_db()
    factory = await _get_factory()
    root = proposals_root()
    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")
        name = project.proposal_folder_name or make_dated_folder_name(
            project.name or "", project.created_at or _now(),
            await _taken_names(session, exclude_project_id=project_id))
        deck_url = await _current_deck_url(session, project_id)
    # UNC 根目錄斷線時 isdir 會卡秒級 → 不佔 event loop
    root_set = bool(root) and await asyncio.to_thread(os.path.isdir, root)
    folder_abs = os.path.join(root, name) if root else ""
    files, truncated = ([], False)
    if folder_abs:
        files, truncated = await asyncio.to_thread(_list_files, folder_abs)
    return {
        "root": _raw_root(),
        "root_set": root_set,
        "folder_name": name,
        "project_folder": folder_abs,
        "created": bool(project.proposal_folder_name),
        "deck_rel": _deck_rel(deck_url, folder_abs),
        "files": files,
        "truncated": truncated,
    }


@router.post("/projects/{project_id}/proposal-assets/settings")
async def set_proposal_assets_root(project_id: str, request: Request):
    """設定提案資產根目錄（全站共用）。存在性用本機視角驗 —— 打錯字要當場
    知道，不要留一個之後上傳才靜默退回舊落點的路徑。"""
    _check_auth(request)
    body = await request.json()
    root = str(body.get("root") or "").strip()
    if root and not await asyncio.to_thread(os.path.isdir, to_local_path(root)):
        raise HTTPException(status_code=400,
                            detail=f"資料夾不存在或無法存取：{root}")
    # save_settings 本身是 merge-on-save（頂層 dict 會淺 merge）→ 只傳這一段。
    # 整包 load_settings() 寫回會把 config.py 的預設值（含 database_url）固化
    # 進 settings.json，之後改預設值就再也不會生效。
    save_settings({"proposals": {"root": root}})
    return {"status": "ok", "root": root}


# ── 資料夾內容操作（上傳 / 下載 / 刪除 / 指定簡報）──────────────

@router.post("/projects/{project_id}/proposal-assets/upload")
async def upload_proposal_assets(project_id: str, request: Request,
                                 files: List[UploadFile] = File(...)):
    """上傳檔案進專案的提案資產夾（可多檔）。檔名保留原樣，撞名補 -2。
    資料夾不存在會先建出來 —— 這是「現有提案補檔案」的主要入口。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    saved, skipped = [], []
    async with factory() as session:
        folder = await _folder_for_project(session, project_id, create=True)
        await session.commit()          # 資料夾名可能剛生成，先落地
    taken = set(await asyncio.to_thread(os.listdir, folder))
    for f in files:
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext in _BLOCKED_UPLOAD_EXTS:
            skipped.append({"filename": f.filename, "reason": f"不允許的檔案類型（{ext}）"})
            continue
        content = await f.read()
        if len(content) > _UPLOAD_MAX_BYTES:
            skipped.append({"filename": f.filename, "reason": "超過 300MB 上限"})
            continue
        name = dedupe(clean_filename(f.filename or ""), taken, keep_ext=True)
        await asyncio.to_thread(_write_bytes, os.path.join(folder, name), content)
        taken.add(name)
        saved.append(name)
    return {"status": "ok", "saved": saved, "skipped": skipped}


def _write_bytes(path: str, data: bytes) -> None:
    with open(path, "wb") as fp:
        fp.write(data)


@router.get("/projects/{project_id}/proposal-assets/file")
async def download_proposal_asset(project_id: str, request: Request, rel: str = ""):
    """下載資產夾內的單一檔案（rel = 列表回傳的相對路徑）。"""
    from core.auth import check_admin_or_module
    check_admin_or_module(request, "crm_projects")
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        folder = await _folder_for_project(session, project_id, create=False)
    path = await asyncio.to_thread(safe_rel_path, folder, rel) if folder else None
    if not path:
        raise HTTPException(status_code=404, detail="找不到檔案")
    from fastapi.responses import FileResponse
    return FileResponse(path, filename=os.path.basename(path))


@router.delete("/projects/{project_id}/proposal-assets/file")
async def delete_proposal_asset(project_id: str, request: Request, rel: str = ""):
    """刪除資產夾內的單一檔案。若刪掉的正是目前指定的提案簡報 → 一併清掉
    deck_url（否則詳情頁會留一個指向不存在檔案的下載連結）。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        folder = await _folder_for_project(session, project_id, create=False)
        path = await asyncio.to_thread(safe_rel_path, folder, rel) if folder else None
        if not path:
            raise HTTPException(status_code=404, detail="找不到檔案")
        await asyncio.to_thread(os.remove, path)
        canon = to_canonical_path(path)
        from db.models import PreprodProposal
        props = (await session.execute(
            select(PreprodProposal).where(
                PreprodProposal.project_id == project_id,
                PreprodProposal.deck_url == canon))).scalars().all()
        for prop in props:
            prop.deck_url = ""
        await session.commit()
    return {"status": "ok", "deck_cleared": bool(props)}


@router.post("/projects/{project_id}/proposal-assets/deck")
async def set_proposal_deck(project_id: str, request: Request):
    """指定資產夾裡的某個檔案為「提案簡報」（body {rel}；rel 空 = 取消指定）。
    這份才會出現在提案詳情與公開共編頁的「下載簡報」。"""
    _check_auth(request)
    _require_db()
    body = await request.json()
    rel = str(body.get("rel") or "").strip()
    factory = await _get_factory()
    from db.models import PreprodProposal
    async with factory() as session:
        folder = await _folder_for_project(session, project_id, create=False)
        deck_url = ""
        if rel:
            path = await asyncio.to_thread(safe_rel_path, folder, rel) if folder else None
            if not path:
                raise HTTPException(status_code=404, detail="找不到檔案")
            deck_url = to_canonical_path(path)
        props = (await session.execute(
            select(PreprodProposal).where(
                PreprodProposal.project_id == project_id))).scalars().all()
        if not props:
            raise HTTPException(status_code=404,
                                detail="此專案還沒有提案 — 請先在「提案企劃」分頁建立")
        # N:1 時指定給最近更新的那筆（與 deck 讀取端同一個挑法）
        target = max(props, key=lambda p: p.updated_at or _now())
        target.deck_url = deck_url
        target.updated_at = _now()
        await session.commit()
    return {"status": "ok", "deck_url": deck_url}


# ── 資產資料夾總覽（含「過去的」未連結資料夾）─────────────────
# owner 2026-08-06：NAS 上手工整理的舊提案資料夾也要在系統裡看得到、載得到、
# 還能繼續往裡面丟檔案。刻意**不做**「連結到專案」—— 它們就獨立存在。

_OVERVIEW_SCAN_TTL = 60      # root 子夾清單快取（總覽每次載入都打，別每次掃 NAS）
_overview_cache: dict = {"at": 0.0, "root": None, "names": []}


def _scan_root_folders(root: str) -> list:
    """root 底下的直接子資料夾名（掃不到 → None，別誤判成空）。"""
    try:
        with os.scandir(root) as it:
            return sorted(e.name for e in it
                          if e.is_dir() and e.name[:1] not in (".", "_"))
    except OSError:
        return None


async def _root_folders_cached(root: str) -> list:
    import time
    c = _overview_cache
    if c["root"] == root and time.monotonic() - c["at"] < _OVERVIEW_SCAN_TTL:
        return c["names"]
    names = await asyncio.to_thread(_scan_root_folders, root)
    if names is None:                       # 搆不到 → 沿用上次結果，不清空
        return c["names"] if c["root"] == root else []
    c.update(at=time.monotonic(), root=root, names=names)
    return names


@router.get("/proposal-assets/overview")
async def proposal_assets_overview(request: Request, q: str = ""):
    """資產資料夾總覽：root 底下每個資料夾，標示有沒有對應到專案。

    - **已連結**：`crm_projects.proposal_folder_name` 指到它 → 帶專案名/客戶。
    - **未連結**：磁碟上有、系統沒登記（過去手工整理的）→ 一樣可展開看檔案、
      下載、上傳。
    q = 資料夾名或專案名模糊搜尋。"""
    from core.auth import check_admin_or_module
    check_admin_or_module(request, "crm_projects", "preprod_proposals")
    _require_db()
    factory = await _get_factory()
    root = proposals_root()

    from ._shared import Client
    async with factory() as session:
        rows = (await session.execute(
            select(CrmProject.id, CrmProject.name, CrmProject.proposal_folder_name,
                   Client.short_name)
            .outerjoin(Client, Client.id == CrmProject.client_id)
            .where(CrmProject.proposal_folder_name.isnot(None)))).all()
    by_folder = {r[2]: {"project_id": r[0], "project_name": r[1] or "",
                        "client_name": r[3] or ""} for r in rows if r[2]}

    names = await _root_folders_cached(root) if root else []
    ql = q.strip().lower()
    out = []
    for name in names:
        info = by_folder.get(name)
        if ql and ql not in name.lower() and ql not in (
                (info or {}).get("project_name", "").lower()):
            continue
        out.append({"folder_name": name, "linked": bool(info), **(info or {})})
    # 已登記但磁碟上還沒建出來的（剛改名/root 搆不到）也列出來，才不會人間蒸發
    on_disk = set(names)
    for fname, info in by_folder.items():
        if fname not in on_disk and (not ql or ql in fname.lower()):
            out.append({"folder_name": fname, "linked": True,
                        "missing_on_disk": True, **info})
    out.sort(key=lambda x: x["folder_name"], reverse=True)
    return {"root": _raw_root(),
            "root_set": bool(names) or bool(root and await asyncio.to_thread(os.path.isdir, root)),
            "folders": out, "total": len(out)}


@router.get("/proposal-assets/folder/files")
async def proposal_folder_files(request: Request, folder: str = ""):
    """任一資產資料夾的檔案清單（未連結專案的「過去資料夾」也能看）。"""
    from core.auth import check_admin_or_module
    check_admin_or_module(request, "crm_projects", "preprod_proposals")
    folder_abs = await asyncio.to_thread(safe_subfolder, proposals_root(), folder)
    if not folder_abs:
        raise HTTPException(status_code=404, detail="找不到資料夾（或無法存取）")
    files, truncated = await asyncio.to_thread(list_folder_files, folder_abs)
    return {"folder": folder, "count": len(files),
            "truncated": truncated, "files": files}


@router.get("/proposal-assets/folder/file")
async def proposal_folder_file(request: Request, folder: str = "", rel: str = ""):
    """任一資產資料夾內的單檔下載。"""
    from core.auth import check_admin_or_module
    check_admin_or_module(request, "crm_projects", "preprod_proposals")
    folder_abs = await asyncio.to_thread(safe_subfolder, proposals_root(), folder)
    if not folder_abs:
        raise HTTPException(status_code=404, detail="找不到資料夾")
    path = await asyncio.to_thread(safe_rel_path, folder_abs, rel)
    if not path:
        raise HTTPException(status_code=404, detail="找不到檔案")
    from fastapi.responses import FileResponse
    return FileResponse(path, filename=os.path.basename(path))


@router.post("/proposal-assets/folder/upload")
async def proposal_folder_upload(request: Request, folder: str = "",
                                 files: List[UploadFile] = File(...)):
    """上傳檔案進任一資產資料夾（未連結專案的也可以 —— owner 指定）。"""
    _check_auth(request)
    folder_abs = await asyncio.to_thread(safe_subfolder, proposals_root(), folder)
    if not folder_abs:
        raise HTTPException(status_code=404, detail="找不到資料夾")
    taken = set(await asyncio.to_thread(os.listdir, folder_abs))
    saved, skipped = [], []
    for f in files:
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext in _BLOCKED_UPLOAD_EXTS:
            skipped.append({"filename": f.filename, "reason": f"不允許的檔案類型（{ext}）"})
            continue
        content = await f.read()
        if len(content) > _UPLOAD_MAX_BYTES:
            skipped.append({"filename": f.filename, "reason": "超過 300MB 上限"})
            continue
        name = dedupe(clean_filename(f.filename or ""), taken, keep_ext=True)
        await asyncio.to_thread(_write_bytes, os.path.join(folder_abs, name), content)
        taken.add(name)
        saved.append(name)
    return {"status": "ok", "saved": saved, "skipped": skipped}


async def ensure_folder(session, project) -> str:
    """取得/首次生成該專案的提案資產夾**絕對路徑**（本機視角），並在磁碟建出來。
    root 未設或建不出來 → ""（呼叫端退回舊的 uploads 落點）。不 commit。"""
    root = proposals_root()
    if not root:
        return ""
    if not project.proposal_folder_name:
        project.proposal_folder_name = make_dated_folder_name(
            project.name or "", project.created_at or _now(),
            await _taken_names(session, exclude_project_id=project.id))
    path = os.path.join(root, project.proposal_folder_name)
    try:
        await asyncio.to_thread(os.makedirs, path, exist_ok=True)
    except OSError:
        return ""                     # NAS 斷線/權限 — 呼叫端自己決定怎麼退
    return path


async def rename_project_folder(session, project_id: str, project_name: str,
                                created: datetime) -> tuple:
    """專案改名 → 提案資產夾跟著改名。回 `(changed, err)`，不 commit。

    deck_url 存的絕對路徑同步更新 —— 只改資料夾不改 URL 的話，後台的 deck
    連結會指向不存在的檔案。同進退由 rename_and_remap 保證。
    """
    project = await session.get(CrmProject, project_id)
    if not project or not project.proposal_folder_name:
        return False, ""              # 還沒建過夾 — 之後建夾自然用新名
    root = proposals_root()
    if not root:
        return False, ""
    new_name = make_dated_folder_name(
        project_name, created,
        await _taken_names(session, exclude_project_id=project_id),
        existing=project.proposal_folder_name)
    if new_name == project.proposal_folder_name:
        return False, ""

    async def _remap(old_dir: str, new_dir: str) -> None:
        # 只撈 id/deck_url 兩欄 + 批次 update（整列 select 會把 plan 企劃矩陣
        # 的 JSONB 一起拖出來，單筆可數十 KB）—— 與 media_log 的 _remap 同形狀
        from sqlalchemy import update as sa_update
        from db.models import PreprodProposal
        old_canon, new_canon = to_canonical_path(old_dir), to_canonical_path(new_dir)
        rows = (await session.execute(
            select(PreprodProposal.id, PreprodProposal.deck_url)
            .where(PreprodProposal.project_id == project_id,
                   PreprodProposal.deck_url.isnot(None)))).all()
        updates = [{"id": pid, "deck_url": new}
                   for pid, url in rows
                   if (new := remap_prefix(url or "", old_canon, new_canon)) != (url or "")]
        if updates:
            await session.execute(sa_update(PreprodProposal), updates)

    changed, err = await rename_and_remap(
        session, os.path.join(root, project.proposal_folder_name),
        os.path.join(root, new_name), remap=_remap)
    if not changed:
        return False, err
    project.proposal_folder_name = new_name
    return True, ""
