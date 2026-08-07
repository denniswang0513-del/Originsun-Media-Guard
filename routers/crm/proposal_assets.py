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
from core.auth import check_admin_or_module
from core.drive_map import to_canonical_path, to_local_path
from core.project_folders import (create_subfolder, list_folder_level,
                                  make_dated_folder_name, remap_prefix,
                                  rename_and_remap, safe_rel_dir, safe_rel_path,
                                  safe_subfolder, save_uploads, subfolder_path,
                                  taken_names, validate_folder_name, within_dir)

from ._shared import router, _check_auth, _get_factory, _now, _require_db

try:
    from ._shared import select, CrmProject
    from db.models import PreprodProposal
except ImportError:  # DB 套件不存在的 agent 環境
    pass


def _assets_auth(request: Request):
    """資產資料夾的守衛（**唯一正本**）—— 能看提案或專案的人就能讀寫資料夾內容。

    判準：資料夾內容是「提案的工作檔」，門檻跟得上提案本身即可。**不用
    admin** —— 提案庫 Tab 的「＋ 上傳檔案」按鈕對這些人可見，API 若要 admin
    就是給看不給用。改**根目錄**（全站共用設定）才需要 admin，那條走 _check_auth。

    ⚠️ 這裡不可以沿用 `_shared._check_auth` 這個裸名 —— 它在本套件是 Lv3
    admin，在 routers/api_proposals.py 卻是模組守衛，同名反義。
    """
    return check_admin_or_module(request, "crm_projects", "preprod_proposals")


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


def _deck_rel(deck_url: str, folder_abs: str) -> str:
    """目前指定為「提案簡報」的檔案在資料夾內的相對路徑（不在這個資料夾裡
    或還沒指定 → ""）。前端據此在列表上標星號。"""
    if not deck_url or not folder_abs or deck_url.startswith("/"):
        return ""
    local = to_local_path(deck_url)
    if not within_dir(folder_abs, local):
        return ""
    return os.path.relpath(local, folder_abs).replace("\\", "/")


async def _deck_target(session, project_id: str):
    """N:1 時 deck 掛在哪一筆衛星提案上 —— **讀寫共用的單一規則**：
    最近更新的那筆。讀寫各寫一套（一邊過濾 deck_url、一邊不過濾）會讓
    「按了星號卻標到別的檔」：寫進 B、讀回 A 的舊 deck。"""
    return (await session.execute(
        select(PreprodProposal)
        .where(PreprodProposal.project_id == project_id)
        .order_by(PreprodProposal.updated_at.desc())
        .limit(1))).scalars().first()


# 提案資產夾的單檔上限（子系統的業務判斷，不是共用預設 —— 企劃檔可能含
# 影片參考所以放寬到 300MB；core.save_uploads 刻意不給預設值）
_MAX_UPLOAD_BYTES = 300 * 1024 * 1024


async def _project_or_404(session, project_id: str):
    project = await session.get(CrmProject, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="找不到此專案")
    return project


# 對外分享子夾（owner 2026-08-07 定案）——資產夾裡混著報價/成本/內部版腳本，
# 所以公開連結**只**看得到這個子夾。拖進去＝公開，拖出來＝收回，零額外狀態
# （不用 DB 記哪個檔公開，改名搬檔都不會跑掉）。
#
# 🔴 這個名字必須由**程式**寫出來，不能靠人打對。前端的「建立對外分享夾」
# 按鈕走 mkdir 帶這個常數（見 crm-projects-plan.js）—— 打成「對外分享用」
# 那種一字之差，分享會靜靜失效：客戶那邊分頁不出現、你這邊 badge 不見，
# 兩邊都沒有錯誤訊息。
# 🔴 不可以用 "_" 或 "." 開頭 —— core._visible_dir 會把那種資料夾從列表濾掉，
# 你自己在後台也會看不到它、拖不了檔進去。
PUBLIC_SUBFOLDER = "對外分享"


async def public_share_dir(session, project_id: str) -> str:
    """專案的「對外分享」子夾絕對路徑；沒建過/root 未設 → ""（不是錯誤，
    代表這個提案還沒開放任何檔案）。

    公開端點的 root **只**能從這裡拿 —— 讓「客戶看得到什麼」只有一個定義處，
    而不是每個端點自己 join 一次路徑（少 join 一段就是整個資產夾對外）。
    """
    folder = await _project_folder_abs(session, project_id)
    return os.path.join(folder, PUBLIC_SUBFOLDER) if folder else ""


async def _project_folder_abs(session, project_id: str) -> str:
    """專案資產夾的絕對路徑（唯讀用；還沒建夾 → ""，不是錯誤）。"""
    project = await _project_or_404(session, project_id)
    root = proposals_root()
    if not root or not project.proposal_folder_name:
        return ""
    return os.path.join(root, project.proposal_folder_name)


async def file_or_404(folder_abs: str, rel: str) -> str:
    """root 底下的單一檔案絕對路徑；逃逸/不存在/不是檔案 → 404。

    「檔案怎麼離開共用磁碟」的單一判斷（專案夾、任一資產夾、公開分享夾共用）
    —— 各端點自己寫 `safe_rel_path` + `if not path` 就會各自重新決定一次
    「None 要回 404 還是 500」。"""
    path = await asyncio.to_thread(safe_rel_path, folder_abs, rel) if folder_abs else None
    if not path:
        raise HTTPException(status_code=404, detail="找不到檔案")
    return path


async def _resolve_asset_file(session, project_id: str, rel: str) -> str:
    """專案資產夾內的單一檔案絕對路徑（下載/刪除/指定簡報共用）。"""
    return await file_or_404(await _project_folder_abs(session, project_id), rel)


async def _level(folder_abs: str, rel: str, *, missing_ok: bool = False) -> dict:
    """某一層的內容（子資料夾 + 檔案）—— **所有**列檔/上傳回應的共同形狀
    （公開分享端點也吃這一份；各自手組就會在加欄位時漏掉一邊）。
    rel 是相對**資料夾根**的路徑，下載/刪除端點吃的就是這個形式。

    逃逸/不存在 → 404；`missing_ok=True` 改回空清單（給「資料夾還沒建，
    這不是錯誤」的呼叫端）。路徑防護在 core.list_folder_level 裡。"""
    got = await asyncio.to_thread(list_folder_level, folder_abs, rel) if folder_abs else None
    if got is None and not missing_ok:
        raise HTTPException(status_code=404, detail="找不到子資料夾（或無法存取）")
    dirs, files, truncated = got or ([], [], False)
    return {"rel": rel, "dirs": dirs, "files": files, "truncated": truncated,
            # 「這一層裡哪個夾是公開的」跟著清單走，而不是跟著呼叫端走 ——
            # 不然每個新的資料夾瀏覽器都得自己記得再標一次 badge
            "public_subfolder": PUBLIC_SUBFOLDER}


async def _subdir_or_404(folder_abs: str, rel: str) -> str:
    """資料夾內的子層絕對路徑（上傳落點用）；逃逸/不存在 → 404。
    rel 空 = 資料夾本身，不再驗一次 —— 呼叫端拿到 folder_abs 的路徑本來就
    經過防護，重驗是白付兩趟 realpath 的 UNC 往返。"""
    if not str(rel or "").strip("/"):
        return folder_abs
    dir_abs = await asyncio.to_thread(safe_rel_dir, folder_abs, rel)
    if not dir_abs:
        raise HTTPException(status_code=404, detail="找不到子資料夾（或無法存取）")
    return dir_abs


async def _mkdir_result(folder_abs: str, rel: str, raw_name) -> dict:
    """在 rel 這一層底下開一個新子資料夾 → 回這一層的新內容（同上傳的回應形狀，
    前端不必再打一次列檔）。名稱規則走 core.validate_folder_name（與改名同一份）。

    刻意**不動**總覽快取：這裡只會在某個資產夾**裡面**開夾，root 的子夾清單
    沒變 —— 順手 invalidate 會讓每次開子夾都白掃一次 NAS。
    """
    dir_abs = await _subdir_or_404(folder_abs, rel)
    name, err = await asyncio.to_thread(create_subfolder, dir_abs, raw_name)
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"status": "ok", "created": name, **await _level(folder_abs, rel)}


async def _upload_result(folder_abs: str, files, rel: str = "") -> dict:
    """兩個上傳端點的共同回應：落地到 rel 這一層 + 回這一層的新內容
    （前端不必再打一次列檔）。一個都沒存成（全被擋/全超限）→ 不重掃，
    前端手上那份還是對的。"""
    dir_abs = await _subdir_or_404(folder_abs, rel)
    saved, skipped = await save_uploads(dir_abs, files, max_bytes=_MAX_UPLOAD_BYTES)
    out = {"status": "ok", "saved": saved, "skipped": skipped}
    if saved:
        out.update(await _level(folder_abs, rel))
    return out


async def _ensure_project_folder(session, project_id: str) -> str:
    """專案資產夾（會實際建出來）；建不出來 → 400。"""
    path = await ensure_folder(session, await _project_or_404(session, project_id))
    if not path:
        raise HTTPException(status_code=400,
                            detail="提案資產資料夾無法建立（根目錄未設定或搆不到）")
    return path


async def _project_folder_ready(project_id: str) -> str:
    """兩個寫入端點（上傳、開夾）的共同前置：確保資產夾存在並落地它的名字。
    commit 不能省 —— 資料夾名可能是這一刻才生成的，磁碟上已經有夾、DB 卻沒記
    的話，下次就會用新名再建一個。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        folder = await _ensure_project_folder(session, project_id)
        await session.commit()
    return folder


# ── 設定端點（比照影像紀錄：路徑帶 project_id 只是讓前端從專案面板順手改，
#    root 本身是全站共用的一份設定）──────────────────────────────

@router.get("/projects/{project_id}/proposal-assets")
async def get_proposal_assets(project_id: str, request: Request, rel: str = ""):
    """提案資產夾現況：根目錄、是否搆得到、這個專案的資料夾（還沒建就先算給看）。

    `rel` = 要看資料夾裡的哪一層（空 = 最外層）。逐層走而不是攤平整棵樹 ——
    連結進來的舊資料夾常有 `@Data/03_製片檔案PPM/…` 好幾層。

    讀寫都放行專案/提案模組（見 _assets_auth）；改**根目錄**才要 admin。"""
    _assets_auth(request)
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
        target = await _deck_target(session, project_id)
    folder_abs = os.path.join(root, name) if root else ""
    # 所有 NAS 探測（root isdir / 列這一層 / deck 的 realpath）併成一次 to_thread：
    # UNC 斷線時每個都卡秒級且不重疊，分開跑等於使用者要等 3× timeout
    root_set, dirs, files, truncated, deck_rel = await asyncio.to_thread(
        _probe_folder, root, folder_abs, (target.deck_url if target else ""), rel)
    return {
        "root": _raw_root(),
        "root_set": root_set,
        "folder_name": name,
        "project_folder": folder_abs,
        "deck_rel": deck_rel,
        # 對外分享夾的名字由後端下發 —— 前端拿它標「客戶看得到」的 badge。
        # 寫死在 JS 就是第二份真相，改名時一定有一邊忘了改。
        "public_subfolder": PUBLIC_SUBFOLDER,
        "rel": rel,
        "dirs": dirs,
        "files": files,
        "truncated": truncated,
    }


def _probe_folder(root: str, folder_abs: str, deck_url: str, rel: str = "") -> tuple:
    """一次跑完所有磁碟探測 → (root_set, dirs, files, truncated, deck_rel)。
    在 to_thread 內執行 —— 全部是可能卡住的 UNC 操作。

    root 搆不到就**直接收工**：folder_abs 是 root 的子路徑，root 卡住時它
    必然也卡，繼續探等於讓使用者多等好幾個 UNC timeout。
    資料夾還沒建（或 rel 指向不存在的層）→ 空清單，不是錯誤。
    """
    if not root or not os.path.isdir(root):
        return False, [], [], False, ""
    # None（還沒建夾／rel 指向不存在的層）在這裡是空清單，不是錯誤
    dirs, files, truncated = (
        (list_folder_level(folder_abs, rel) if folder_abs else None) or ([], [], False))
    return True, dirs, files, truncated, _deck_rel(deck_url, folder_abs)


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
async def upload_proposal_assets(project_id: str, request: Request, rel: str = "",
                                 files: List[UploadFile] = File(...)):
    """上傳檔案進專案的提案資產夾（可多檔）。資料夾不存在會先建出來 ——
    這是「現有提案補檔案」的主要入口。`rel` = 落在哪一層（空 = 最外層，
    子層必須已存在）。落地規則見 core.save_uploads。"""
    _assets_auth(request)
    return await _upload_result(await _project_folder_ready(project_id), files, rel)


@router.post("/projects/{project_id}/proposal-assets/mkdir")
async def mkdir_proposal_assets(project_id: str, request: Request, rel: str = ""):
    """在專案資產夾的 rel 這一層底下開新子資料夾（body `{name}`）。
    資產夾本身不存在會先建出來 —— 空專案第一個動作就是開結構夾很常見。"""
    _assets_auth(request)
    name = (await request.json()).get("name")
    return await _mkdir_result(await _project_folder_ready(project_id), rel, name)


@router.get("/projects/{project_id}/proposal-assets/file")
async def download_proposal_asset(project_id: str, request: Request, rel: str = ""):
    """下載資產夾內的單一檔案（rel = 列表回傳的相對路徑）。"""
    _assets_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        path = await _resolve_asset_file(session, project_id, rel)
    from fastapi.responses import FileResponse
    return FileResponse(path, filename=os.path.basename(path))


@router.delete("/projects/{project_id}/proposal-assets/file")
async def delete_proposal_asset(project_id: str, request: Request, rel: str = ""):
    """刪除資產夾內的單一檔案。若刪掉的正是目前指定的提案簡報 → 一併清掉
    deck_url（否則詳情頁會留一個指向不存在檔案的下載連結）。"""
    _assets_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        path = await _resolve_asset_file(session, project_id, rel)
        await asyncio.to_thread(os.remove, path)
        canon = to_canonical_path(path)
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
    _assets_auth(request)
    _require_db()
    body = await request.json()
    rel = str(body.get("rel") or "").strip()
    factory = await _get_factory()
    async with factory() as session:
        deck_url = to_canonical_path(
            await _resolve_asset_file(session, project_id, rel)) if rel else ""
        target = await _deck_target(session, project_id)   # 讀寫同一條挑法
        if not target:
            raise HTTPException(status_code=404,
                                detail="此專案還沒有提案 — 請先在「提案企劃」分頁建立")
        target.deck_url = deck_url
        target.updated_at = _now()
        await session.commit()
    return {"status": "ok", "deck_url": deck_url}


# ── 資產資料夾總覽（含「過去的」未連結資料夾）─────────────────
# owner 2026-08-06：NAS 上手工整理的舊提案資料夾也要在系統裡看得到、載得到、
# 還能繼續往裡面丟檔案。2026-08-07 追加：也能改名、也能連結到專案
# （見底下的 folder/rename 與 folder/link）。

_OVERVIEW_SCAN_TTL = 60      # root 子夾清單快取（總覽每次載入都打，別每次掃 NAS）
_overview_cache: dict = {"at": 0.0, "root": None, "names": [], "ok": False}
_overview_lock = asyncio.Lock()


def invalidate_folder_cache() -> None:
    """建夾/改名後叫一聲 —— 否則總覽最多 60 秒還在列舊名，點下去 404。"""
    _overview_cache["at"] = 0.0


def _scan_root_folders(root: str) -> list:
    """root 底下的直接子資料夾名（掃不到 → None，別誤判成空）。"""
    try:
        with os.scandir(root) as it:
            return sorted(e.name for e in it
                          if e.is_dir() and e.name[:1] not in (".", "_"))
    except OSError:
        return None


async def _root_folders_cached(root: str) -> tuple:
    """(子夾名清單, 這次真的搆得到嗎)。

    單飛鎖：TTL 過期那一刻若多人同時開總覽，沒有鎖就是 N 個 scandir 同時打
    SMB —— 那正是快取要防的 stampede（media_log 為此付過學費）。
    搆不到時沿用上次結果**並更新時間戳**，否則 NAS 掛掉期間每個請求都要重付
    一次完整 UNC timeout。"""
    import time
    c = _overview_cache
    fresh = (c["root"] == root and time.monotonic() - c["at"] < _OVERVIEW_SCAN_TTL)
    if fresh:
        return c["names"], c["ok"]
    async with _overview_lock:
        if c["root"] == root and time.monotonic() - c["at"] < _OVERVIEW_SCAN_TTL:
            return c["names"], c["ok"]      # 等鎖期間別人已重填
        names = await asyncio.to_thread(_scan_root_folders, root)
        ok = names is not None
        c.update(at=time.monotonic(), root=root,
                 names=names if ok else (c["names"] if c["root"] == root else []),
                 ok=ok)
        return c["names"], ok


@router.get("/proposal-assets/overview")
async def proposal_assets_overview(request: Request):
    """資產資料夾總覽：root 底下每個資料夾，標示有沒有對應到專案。

    - **已連結**：`crm_projects.proposal_folder_name` 指到它 → 帶專案名/客戶。
    - **未連結**：磁碟上有、系統沒登記（過去手工整理的）→ 一樣可展開看檔案、
      下載、上傳。

    一次回全部（資料夾數是專案數量級）—— 搜尋由前端就地過濾，不做 server-side
    `q`：每個按鍵都重跑一次 CrmProject×Client join 換不到任何東西。"""
    _assets_auth(request)
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

    names, reachable = await _root_folders_cached(root) if root else ([], False)
    out = [{"folder_name": n, "linked": n in by_folder, **(by_folder.get(n) or {})}
           for n in names]
    # 已登記但磁碟上還沒建出來的（剛改名/root 搆不到）也列出來，才不會人間蒸發
    on_disk = set(names)
    for fname, info in by_folder.items():
        if fname not in on_disk:
            out.append({"folder_name": fname, "linked": True,
                        "missing_on_disk": True, **info})
    out.sort(key=lambda x: x["folder_name"], reverse=True)
    # root_set 直接用「這次掃得到嗎」—— 用 `bool(names) or isdir()` 的話，
    # NAS 斷線時沿用的舊快取會讓它回 True，而這個欄位的用途正是告訴前端搆不到
    return {"root": _raw_root(), "root_set": reachable, "folders": out}


async def _folder_or_404(folder: str) -> str:
    """root 底下的資料夾絕對路徑（三個 folder 端點共用；含路徑逃逸防護）。"""
    folder_abs = await asyncio.to_thread(safe_subfolder, proposals_root(), folder)
    if not folder_abs:
        raise HTTPException(status_code=404, detail="找不到資料夾（或無法存取）")
    return folder_abs


@router.get("/proposal-assets/folder/files")
async def proposal_folder_files(request: Request, folder: str = "", rel: str = ""):
    """任一資產資料夾裡**某一層**的內容（未連結專案的「過去資料夾」也能看）。
    `rel` 空 = 最外層；子資料夾走 dirs 回傳的 rel 再打一次。"""
    _assets_auth(request)
    return {"folder": folder, **await _level(await _folder_or_404(folder), rel)}


@router.get("/proposal-assets/folder/file")
async def proposal_folder_file(request: Request, folder: str = "", rel: str = ""):
    """任一資產資料夾內的單檔下載。"""
    _assets_auth(request)
    from fastapi.responses import FileResponse
    path = await file_or_404(await _folder_or_404(folder), rel)
    return FileResponse(path, filename=os.path.basename(path))


@router.post("/proposal-assets/folder/upload")
async def proposal_folder_upload(request: Request, folder: str = "", rel: str = "",
                                 files: List[UploadFile] = File(...)):
    """上傳檔案進任一資產資料夾（未連結專案的也可以 —— owner 指定）。
    `rel` = 落在哪一層（空 = 最外層；就是使用者目前看的那一層）。"""
    _assets_auth(request)
    return await _upload_result(await _folder_or_404(folder), files, rel)


@router.post("/proposal-assets/folder/mkdir")
async def proposal_folder_mkdir(request: Request, folder: str = "", rel: str = ""):
    """在任一資產資料夾的 rel 這一層底下開新子資料夾（body `{name}`）。"""
    _assets_auth(request)
    name = (await request.json()).get("name")
    return await _mkdir_result(await _folder_or_404(folder), rel, name)


@router.post("/proposal-assets/folder/rename")
async def proposal_folder_rename(request: Request):
    """把資產根目錄底下的資料夾改名（body `{folder, new_name}`）。

    已連結專案的資料夾也能從這裡改 —— deck_url 的絕對路徑同步更新，
    `proposal_folder_name` 跟著改（同進退由 core.rename_and_remap 保證）。
    只改**最外層**的資料夾名：它才是專案↔資料夾的對應鍵，子夾結構原封不動。
    """
    _assets_auth(request)
    _require_db()
    body = await request.json()
    folder = str(body.get("folder") or "").strip()
    await _folder_or_404(folder)                   # 舊夾必須真的在
    factory = await _get_factory()
    async with factory() as session:
        project = await _project_of_folder(session, folder)
        # taken：別撞上其他專案登記的名字（那些夾可能只是暫時 missing_on_disk，
        # rename_dir 的磁碟檢查看不到它們）
        taken = await _taken_names(session, exclude_project_id=project.id if project else "")
        new_name, err = validate_folder_name(proposals_root(),
                                             body.get("new_name"), taken)
        if err:
            raise HTTPException(status_code=400, detail=err)
        if new_name == folder:
            return {"status": "ok", "folder_name": folder, "changed": False}
        changed, err = await _rename_folder(session, folder, new_name, project)
        if not changed:
            raise HTTPException(status_code=400, detail=err or "改名失敗")
        await session.commit()
    return {"status": "ok", "folder_name": new_name, "changed": True}


@router.post("/proposal-assets/folder/link")
async def proposal_folder_link(request: Request):
    """把資產資料夾連結到某專案（body `{folder, project_id}`；project_id 空 = 解除連結）。

    連結後這個資料夾**就是**該專案的提案資產夾：之後上傳的簡報落在這裡、
    專案改名時資料夾跟著改名（舊名帶得動日期前綴就沿用，不會跳到今天）。

    守衛：資料夾已被別的專案認領 → 400；目標專案已有**非空**的別夾 → 400
    （不把既有資料默默改指到別處）。舊夾是空的就順手清掉，免得留一個
    看起來像「過去的提案」的空殼在總覽上。

    ⚠️ 影像紀錄有一條同名不同義的 `/media-log/link`，守衛規則**刻意不同**：
    它有 DB 檔案索引與免專案 QR token，所以會轉走 folder-only row、用索引筆數
    判斷空不空、也不支援解除連結。提案資產夾是純掃磁碟、沒有索引表，兩邊
    合併只會逼出一個誰都不像的抽象。
    """
    _assets_auth(request)
    _require_db()
    body = await request.json()
    folder = str(body.get("folder") or "").strip()
    project_id = str(body.get("project_id") or "").strip()
    await _folder_or_404(folder)

    factory = await _get_factory()
    async with factory() as session:
        holder = await _project_of_folder(session, folder)
        if not project_id:                          # 解除連結
            if holder:
                holder.proposal_folder_name = None
                await session.commit()
            return {"status": "ok", "linked": False}
        if holder and holder.id != project_id:
            raise HTTPException(
                status_code=400,
                detail=f"資料夾「{folder}」已連結到專案「{holder.name or holder.id}」")
        project = await _project_or_404(session, project_id)
        old = project.proposal_folder_name
        if old and old != folder and not await asyncio.to_thread(
                _vacate_if_empty, proposals_root(), old):
            raise HTTPException(
                status_code=400,
                detail=f"「{project.name}」原本的資產資料夾「{old}」還有東西"
                       f"（或無法移除），請先處理再連結")
        project.proposal_folder_name = folder
        await session.commit()
        name = project.name or ""
    invalidate_folder_cache()      # 剛清掉的空殼別再列
    return {"status": "ok", "linked": True,
            "project_id": project_id, "project_name": name}


def _vacate_if_empty(root: str, name: str) -> bool:
    """騰出舊資料夾：空的就刪掉 → True；裡面還有東西/刪不掉 → False。

    直接 rmdir 不先問 —— `ENOTEMPTY` 本身就是「裡面有檔案」的答案，比
    isdir + listdir + rmdir 少兩趟 UNC 往返，而且 listdir 會為了一個真假值
    把上千筆檔名整包拉回來（ensure_folder 用的是同一招）。"""
    p = subfolder_path(root, name)
    if not p:
        return True                # 名稱本身不合法 → 沒有東西要騰
    try:
        os.rmdir(p)
    except FileNotFoundError:
        pass                       # 登記了但磁碟上沒有 → 本來就沒佔位
    except OSError:
        return False
    return True


async def _project_of_folder(session, folder: str):
    """認領這個資料夾的專案（沒有 → None）。`proposal_folder_name` 沒有 DB 唯一
    約束，取第一筆即可 —— 連結端點會擋掉第二個認領者。"""
    return (await session.execute(
        select(CrmProject)
        .where(CrmProject.proposal_folder_name == folder))).scalars().first()


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
        # 不帶 exist_ok —— FileExistsError 就是「本來就在」，比多打一趟
        # UNC isdir 去問便宜；只有真的新建才需要讓總覽快取失效
        await asyncio.to_thread(os.makedirs, path)
        invalidate_folder_cache()
    except FileExistsError:
        pass
    except OSError:
        return ""                     # NAS 斷線/權限 — 呼叫端自己決定怎麼退
    return path


async def _rename_folder(session, old_name: str, new_name: str, project) -> tuple:
    """資產資料夾改名的**單一程序**（專案改名跟隨、使用者手動改名共用）
    → `(changed, err)`。成功才寫 `proposal_folder_name`（失敗時
    rename_and_remap 已把磁碟還原成舊名，欄位當然也要留舊的）。不 commit。
    `project=None` = 未連結專案的資料夾（純磁碟改名）。
    """
    root = proposals_root()
    if not root:
        return False, "尚未設定提案資產根目錄"

    async def _remap(old_dir: str, new_dir: str) -> None:
        if project is None:
            return
        # 只撈 id/deck_url 兩欄 + 批次 update（整列 select 會把 plan 企劃矩陣
        # 的 JSONB 一起拖出來，單筆可數十 KB）—— 與 media_log 的 _remap 同形狀
        from sqlalchemy import update as sa_update
        old_canon, new_canon = to_canonical_path(old_dir), to_canonical_path(new_dir)
        rows = (await session.execute(
            select(PreprodProposal.id, PreprodProposal.deck_url)
            .where(PreprodProposal.project_id == project.id,
                   PreprodProposal.deck_url.isnot(None)))).all()
        updates = [{"id": pid, "deck_url": new}
                   for pid, url in rows
                   if (new := remap_prefix(url or "", old_canon, new_canon)) != (url or "")]
        if updates:
            await session.execute(sa_update(PreprodProposal), updates)

    changed, err = await rename_and_remap(
        session, os.path.join(root, old_name), os.path.join(root, new_name),
        remap=_remap)
    if changed:
        if project is not None:
            project.proposal_folder_name = new_name
        invalidate_folder_cache()      # 總覽別再列舊名（點下去會 404）
    return changed, err


async def rename_project_folder(session, project_id: str, project_name: str,
                                created: datetime) -> tuple:
    """專案改名 → 提案資產夾跟著改名。回 `(changed, err)`，不 commit。

    deck_url 存的絕對路徑同步更新 —— 只改資料夾不改 URL 的話，後台的 deck
    連結會指向不存在的檔案。同進退由 rename_and_remap 保證。
    """
    project = await session.get(CrmProject, project_id)
    if not project or not project.proposal_folder_name:
        return False, ""              # 還沒建過夾 — 之後建夾自然用新名
    if not proposals_root():
        return False, ""
    new_name = make_dated_folder_name(
        project_name, created,
        await _taken_names(session, exclude_project_id=project_id),
        existing=project.proposal_folder_name)
    if new_name == project.proposal_folder_name:
        return False, ""
    return await _rename_folder(
        session, project.proposal_folder_name, new_name, project)
