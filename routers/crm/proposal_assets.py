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

from config import load_settings
from core.drive_map import to_canonical_path, to_local_path
from core.project_folders import (make_dated_folder_name, remap_prefix,
                                  rename_and_remap, taken_names)

from ._shared import _now

try:
    from ._shared import select, CrmProject
except ImportError:  # DB 套件不存在的 agent 環境
    pass


def proposals_root() -> str:
    """提案資產根目錄（已翻成本機視角，可直接開檔）。未設 → ""。

    走 settings.json 而不是 DB settings（影像紀錄走 DB）—— 判準是「幾台機器
    在跑」：影像紀錄的收檔端在 master 與 NAS 對外容器都有，設定各存各的會讓
    照片安靜散落兩處；提案資產夾只有 master 會寫，與片庫 reference_archive.dir
    同一條路。詳見 core/project_folders 檔頭表格。
    """
    conf = load_settings().get("proposals") or {}
    return to_local_path(str(conf.get("root") or "").strip())


async def _taken_names(session, exclude_project_id: str = "") -> set:
    return await taken_names(session, CrmProject.proposal_folder_name,
                             CrmProject.id, exclude_project_id)


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
        from db.models import PreprodProposal
        old_canon, new_canon = to_canonical_path(old_dir), to_canonical_path(new_dir)
        props = (await session.execute(
            select(PreprodProposal).where(
                PreprodProposal.project_id == project_id))).scalars().all()
        for prop in props:
            remapped = remap_prefix(prop.deck_url or "", old_canon, new_canon)
            if remapped != (prop.deck_url or ""):
                prop.deck_url = remapped

    changed, err = await rename_and_remap(
        os.path.join(root, project.proposal_folder_name),
        os.path.join(root, new_name), remap=_remap)
    if not changed:
        return False, err
    project.proposal_folder_name = new_name
    return True, ""
