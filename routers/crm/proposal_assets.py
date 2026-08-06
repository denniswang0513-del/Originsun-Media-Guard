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

from fastapi import HTTPException, Request

from config import load_settings, save_settings
from core.drive_map import to_canonical_path, to_local_path
from core.project_folders import (make_dated_folder_name, remap_prefix,
                                  rename_and_remap, taken_names)

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
    # UNC 根目錄斷線時 isdir 會卡秒級 → 不佔 event loop
    root_set = bool(root) and await asyncio.to_thread(os.path.isdir, root)
    return {
        "root": _raw_root(),
        "root_set": root_set,
        "folder_name": name,
        "project_folder": os.path.join(root, name) if root else "",
        "created": bool(project.proposal_folder_name),
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
