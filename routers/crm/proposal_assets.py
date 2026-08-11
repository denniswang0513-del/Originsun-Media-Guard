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
import time
from collections import defaultdict
from datetime import datetime

from typing import List

from fastapi import File, Form, HTTPException, Request, UploadFile

from config import load_settings, save_settings
from core import chunked_upload as cu
from core import pinned_assets as pa
from core.auth import check_admin_or_module
from core.drive_map import to_canonical_path, to_local_path
from core.project_folders import (BLOCKED_UPLOAD_EXTS, clean_filename,
                                  create_subfolder, dedupe, list_folder_level,
                                  make_dated_folder_name, remap_prefix,
                                  rename_and_remap, safe_rel_dir, safe_rel_path,
                                  safe_sub_dirs, safe_subfolder, save_uploads,
                                  subfolder_path,
                                  taken_names, validate_folder_name, within_dir)

from ._shared import router, _check_auth, _get_factory, _now, _require_db

try:
    from ._shared import select, CrmProject
    from db.models import PreprodProposal
except ImportError:  # DB 套件不存在的 agent 環境
    pass


def assets_auth(request: Request):
    """資產資料夾的守衛（**唯一正本**）—— 能看提案或專案的人就能讀寫資料夾內容。

    判準：資料夾內容是「提案的工作檔」，門檻跟得上提案本身即可。**不用
    admin** —— 提案庫 Tab 的「＋ 上傳檔案」按鈕對這些人可見，API 若要 admin
    就是給看不給用。改**根目錄**（全站共用設定）才需要 admin，那條走 _check_auth。

    ⚠️ 這裡不可以沿用 `_shared._check_auth` 這個裸名（Lv3 admin，語意不同）。
    提案子分頁（briefs/quotes）用的是 `api_proposals.proposal_auth`（多放行
    preprod_plan）—— 兩個守衛都是公開自述名，別再 alias 成 `_auth` 之類的
    短名把語意藏起來。
    """
    return check_admin_or_module(request, "crm_projects", "preprod_proposals")


_ROOT_SETTING_KEY = "proposals.root"
_ROOT_PREFIX = "proposals."
# 根目錄快取。這個值幾乎不變，但 _raw_root 在熱路徑上（每次列檔、每次上傳、
# 客戶每下載一個檔都會問一次），而它現在要打 DB —— 不快取的話每個動作都多
# 兩趟 LAN 往返。TTL 對齊本檔的 _OVERVIEW_SCAN_TTL；master 改設定時另外
# 明確失效（見 set_proposal_assets_root），TTL 只是給 NAS 容器那側的保險：
# 它不會收到 master 的寫入事件，靠 60 秒自己追上。
_ROOT_TTL = 60
_root_cache: dict = {"at": 0.0, "val": None}


def invalidate_root_cache() -> None:
    _root_cache["at"] = 0.0


async def _db_root() -> str:
    """DB settings 裡的 proposals.root；DB 不可用/未設 → ""。

    用 get_prefixed 不是 get_all_settings —— 後者是整張 website_settings
    無 WHERE 全撈（生產 100+ 列）只為了讀一個 key。"""
    try:
        from services.website import settings_service
        factory = await _get_factory()
        async with factory() as session:
            vals = await settings_service.get_prefixed(session, _ROOT_PREFIX)
        return str(vals.get(_ROOT_SETTING_KEY) or "").strip()
    except Exception:
        return ""                 # DB 不可用 → 呼叫端退 settings.json，不是錯誤


def _json_root() -> str:
    """settings.json 的那份（fallback）。"""
    return str((load_settings().get("proposals") or {}).get("root") or "").strip()


async def _raw_root() -> str:
    """設定裡存的原字串（master 視角，未翻譯）—— 給設定 UI 顯示/回填用。

    2026-08-07 起存 **DB settings**。判準見 core/project_folders 檔頭表格：
    「幾台機器會碰這個資料夾」。原本只有 master 會寫 → settings.json 夠用；
    現在 NAS 對外容器也要**讀**它（客戶的分享頁由它 serve，master 關機時
    仍要開得了）→ 兩台以上就該有單一真相。

    settings.json 留作 fallback：DB 掛掉、或還沒跑過遷移的機器，行為與從前
    完全相同。遷移在 master 啟動時做一次（migrate_root_to_db）。
    """
    import time
    c = _root_cache
    if c["val"] is not None and time.monotonic() - c["at"] < _ROOT_TTL:
        return c["val"]
    val = await _db_root() or _json_root()
    c.update(at=time.monotonic(), val=val)
    return val


async def proposals_root() -> str:
    """提案資產根目錄（已翻成本機視角，可直接開檔）。未設 → ""。
    NAS 容器拿到的是掛載點（/share/Archive/…），翻譯由 core.drive_map 負責。"""
    return to_local_path(await _raw_root())


async def migrate_root_to_db() -> None:
    """settings.json 的 proposals.root → DB settings（一次性，冪等）。
    DB 已有值就不動；沒有 settings.json 值就沒事做。master 啟動時呼叫。"""
    try:
        json_val = _json_root()
        if not json_val or await _db_root():
            return                     # DB 已是真相，別用舊的 json 蓋回去
        from services.website import settings_service
        factory = await _get_factory()
        async with factory() as session:
            await settings_service.update_settings(
                session, {_ROOT_SETTING_KEY: json_val}, updated_by="migrate")
        invalidate_root_cache()
        print(f"[migrate] proposals.root 已搬進 DB settings：{json_val}")
    except Exception as e:             # noqa: BLE001 — 遷移失敗只是繼續用 json
        print(f"[migrate] proposals.root 搬 DB 略過：{e}")


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


# ── 重點提案（勾選）──────────────────────────────────────────
#
# 取代了 2026-08-07~08 存在兩天的「對外分享」子夾。舊做法要求檔案**實際位在**
# 一個特定名字的資料夾裡，於是只能二選一：把簡報從 PPM 結構裡搬出來（破壞原本
# 的整理），或複製一份（改了 v3 客戶還在看 v2）。勾選是策展：不搬檔、隨時可改、
# 取消即失效。判定規則的正本在 core/pinned_assets（純函式，有單元測試）。


async def _pins_row(session, project_id: str):
    """這個專案的勾選掛在哪一筆提案上 —— 沿用 deck 的同一條規則（最近更新的
    那筆），否則 N:1 時「勾在 B、讀回 A」。"""
    return await _deck_target(session, project_id)


def _legacy_share_dir_has_files(folder_abs: str) -> bool:
    """舊的「對外分享」夾還在而且有東西嗎（一層就夠，不遞迴數）。"""
    if not folder_abs:
        return False
    d = os.path.join(folder_abs, pa.LEGACY_SHARE_DIR)
    try:
        with os.scandir(d) as it:
            return next(it, None) is not None
    except OSError:
        return False


async def pinned_of(session, project_id: str) -> tuple:
    """→ `(勾選清單, 是否開放給客戶)`。沒有提案列 → `([], False)`。

    **一次性接管舊分享夾**：還沒勾過任何東西、而舊的「對外分享」夾裡有檔案時，
    把整個夾轉成一個勾選項並開啟對客戶可見。客戶手上可能已經有指向那個夾的
    連結，直接砍掉舊機制會讓他們看到的東西憑空消失。

    放在這裡而不是後台的 GET —— 這支是**兩條路的共同咽喉**（後台讀、客戶讀
    都會經過）。只掛在後台的話，沒人去開那個分頁的案子，客戶就先斷了。
    磁碟探測只在「還沒勾過任何東西」時做，接管完就不再碰（自我了結，不是
    每次讀都多一趟 SMB）。
    """
    prop = await _pins_row(session, project_id)
    if not prop:
        return [], False
    cur = pa.rows(prop.pinned_assets)
    if not cur:
        folder_abs = await project_folder_abs(session, project_id)
        if await asyncio.to_thread(_legacy_share_dir_has_files, folder_abs):
            cur = pa.adopt_legacy_share_dir(cur, exists=True)
            prop.pinned_assets = cur
            prop.pins_public = True        # 舊機制本來就是對客戶開的，維持現狀
            await session.commit()
    return cur, bool(getattr(prop, "pins_public", False))


async def project_folder_abs(session, project_id: str) -> str:
    """專案資產夾的絕對路徑（唯讀用；還沒建夾 → ""，不是錯誤）。"""
    project = await _project_or_404(session, project_id)
    root = await proposals_root()
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


async def resolve_asset_file(session, project_id: str, rel: str) -> str:
    """專案資產夾內的單一檔案絕對路徑（下載/刪除/指定簡報共用）。"""
    return await file_or_404(await project_folder_abs(session, project_id), rel)


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
    return {"rel": rel, "dirs": dirs, "files": files, "truncated": truncated}


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


async def ensure_subdir(folder_abs: str, rel: str) -> str:
    """資產夾底下的某一層（**不存在就建出來**）→ 絕對路徑。

    與 `_subdir_or_404` 的分工：那支是「使用者正在瀏覽的那一層」，不存在就是
    404；這支是「某筆提案的家」（`PreprodProposal.folder_subpath`），值是系統
    自己產或使用者認領過的，磁碟上被搬走/還沒建都不該讓上傳失敗 —— 逃逸或
    建不出來就退回資產夾根（有落點總比 500 好，呼叫端不必再寫一套退路）。
    """
    sub = str(rel or "").replace("\\", "/").strip("/")
    if not folder_abs or not sub:
        return folder_abs
    target = os.path.join(folder_abs, *sub.split("/"))
    if not within_dir(folder_abs, target):     # `..` / 絕對路徑注入 → 當沒設
        return folder_abs
    try:
        await asyncio.to_thread(os.makedirs, target, exist_ok=True)
    except OSError:
        return folder_abs
    return target


async def land_in_home(folder_abs: str, home_subpath: str, file, *,
                       subdir: str = "", max_bytes: int) -> tuple:
    """把一個上傳檔落進**提案的家**（`home_subpath[/subdir]`）→
    `(dest 絕對路徑, rel 相對資產夾)`。存不成（副檔名黑名單/超限）→ 422。

    deck 上傳（api_proposals）與報價單上傳共用 —— ensure_subdir + save_uploads
    + skipped→422 + relpath 這串在兩邊逐字長出過第二份，收斂在 ensure_subdir
    旁邊。落地規則（清洗檔名、撞名補 -2、黑名單）全在 save_uploads，這裡
    只管「家在哪」與 HTTP 化。收字串不收 ORM 列 —— 呼叫端才好在
    **session 之外**呼叫（磁碟 I/O 不該抱著 pool 連線）。"""
    sub = "/".join(s for s in [(home_subpath or "").strip("/"), subdir] if s)
    dir_abs = await ensure_subdir(folder_abs, sub)
    saved, skipped = await save_uploads(dir_abs, [file], max_bytes=max_bytes)
    if not saved:
        reason = (skipped[0].get("reason") if skipped else "檔案沒有存成")
        raise HTTPException(status_code=422, detail=f"上傳失敗：{reason}")
    dest = os.path.join(dir_abs, saved[0])
    return dest, os.path.relpath(dest, folder_abs).replace("\\", "/")


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


async def _upload_result(folder_abs: str, files, rel: str = "", paths=None) -> dict:
    """兩個上傳端點的共同回應：落地到 rel 這一層 + 回這一層的新內容
    （前端不必再打一次列檔）。一個都沒存成（全被擋/全超限）→ 不重掃，
    前端手上那份還是對的。

    `paths` 與 `files` 等長且同序（拖整個資料夾時前端一組一組 append）——
    每個元素是該檔在來源資料夾裡的相對路徑，落地時照著重建目錄。
    不送就全部平放，行為與從前相同。"""
    dir_abs = await _subdir_or_404(folder_abs, rel)
    saved, skipped = await save_uploads(dir_abs, files, max_bytes=_MAX_UPLOAD_BYTES,
                                        rel_paths=paths)
    out = {"status": "ok", "saved": saved, "skipped": skipped}
    if saved:
        out.update(await _level(folder_abs, rel))
    return out


async def ensure_project_folder(session, project_id: str) -> str:
    """專案資產夾（會實際建出來）；建不出來 → 400。"""
    path = await ensure_folder(session, await _project_or_404(session, project_id))
    if not path:
        raise HTTPException(status_code=400,
                            detail="提案資產資料夾無法建立（根目錄未設定或搆不到）")
    return path


async def project_folder_ready(project_id: str) -> str:
    """兩個寫入端點（上傳、開夾）的共同前置：確保資產夾存在並落地它的名字。
    commit 不能省 —— 資料夾名可能是這一刻才生成的，磁碟上已經有夾、DB 卻沒記
    的話，下次就會用新名再建一個。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        folder = await ensure_project_folder(session, project_id)
        await session.commit()
    return folder


# ── 設定端點（比照影像紀錄：路徑帶 project_id 只是讓前端從專案面板順手改，
#    root 本身是全站共用的一份設定）──────────────────────────────

@router.get("/projects/{project_id}/proposal-assets")
async def get_proposal_assets(project_id: str, request: Request, rel: str = ""):
    """提案資產夾現況：根目錄、是否搆得到、這個專案的資料夾（還沒建就先算給看）。

    `rel` = 要看資料夾裡的哪一層（空 = 最外層）。逐層走而不是攤平整棵樹 ——
    連結進來的舊資料夾常有 `@Data/03_製片檔案PPM/…` 好幾層。

    讀寫都放行專案/提案模組（見 assets_auth）；改**根目錄**才要 admin。"""
    assets_auth(request)
    _require_db()
    factory = await _get_factory()
    raw = await _raw_root()            # 一次解析，root 與回應裡的 root 共用
    root = to_local_path(raw)
    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")
        name = project.proposal_folder_name or make_dated_folder_name(
            project.name or "", project.created_at or _now(),
            await _taken_names(session, exclude_project_id=project_id))
        target = await _deck_target(session, project_id)
        # 走 pinned_of 而不是直接讀欄位 —— 舊分享夾的一次性接管在那裡
        pinned, pins_public = await pinned_of(session, project_id)
    folder_abs = os.path.join(root, name) if root else ""
    # 所有 NAS 探測（root isdir / 列這一層 / deck 的 realpath）併成一次 to_thread：
    # UNC 斷線時每個都卡秒級且不重疊，分開跑等於使用者要等 3× timeout
    root_set, dirs, files, truncated, deck_rel = await asyncio.to_thread(
        _probe_folder, root, folder_abs, (target.deck_url if target else ""), rel)
    return {
        "root": raw,
        "root_set": root_set,
        "folder_name": name,
        "project_folder": folder_abs,
        "deck_rel": deck_rel,
        "pinned": pinned,
        "pins_public": pins_public,
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
    知道，不要留一個之後上傳才靜默退回舊落點的路徑。

    寫 **DB settings**（NAS 對外容器也要讀得到），同時把 settings.json 那份
    一起更新 —— 留著它當 DB 不可用時的 fallback，兩邊不同步比沒有更糟。"""
    _check_auth(request)
    _require_db()
    body = await request.json()
    root = str(body.get("root") or "").strip()
    if root and not await asyncio.to_thread(os.path.isdir, to_local_path(root)):
        raise HTTPException(status_code=400,
                            detail=f"資料夾不存在或無法存取：{root}")
    from services.website import settings_service
    factory = await _get_factory()
    async with factory() as session:
        await settings_service.update_settings(session, {_ROOT_SETTING_KEY: root})
    # save_settings 本身是 merge-on-save（頂層 dict 會淺 merge）→ 只傳這一段。
    # 整包 load_settings() 寫回會把 config.py 的預設值（含 database_url）固化
    # 進 settings.json，之後改預設值就再也不會生效。
    save_settings({"proposals": {"root": root}})
    invalidate_root_cache()        # 下一次讀就拿到新值，不必等 TTL
    invalidate_folder_cache()      # root 換了，舊的子夾清單當然作廢
    return {"status": "ok", "root": root}


# ── 資料夾內容操作（上傳 / 下載 / 刪除 / 指定簡報）──────────────

@router.post("/projects/{project_id}/proposal-assets/upload")
async def upload_proposal_assets(project_id: str, request: Request, rel: str = "",
                                 files: List[UploadFile] = File(...),
                                 paths: List[str] = Form(default=[])):
    """上傳檔案進專案的提案資產夾（可多檔）。資料夾不存在會先建出來 ——
    這是「現有提案補檔案」的主要入口。`rel` = 落在哪一層（空 = 最外層，
    子層必須已存在）。落地規則見 core.save_uploads。"""
    assets_auth(request)
    return await _upload_result(await project_folder_ready(project_id), files,
                                rel, paths)


@router.post("/projects/{project_id}/proposal-assets/mkdir")
async def mkdir_proposal_assets(project_id: str, request: Request, rel: str = ""):
    """在專案資產夾的 rel 這一層底下開新子資料夾（body `{name}`）。
    資產夾本身不存在會先建出來 —— 空專案第一個動作就是開結構夾很常見。"""
    assets_auth(request)
    name = (await request.json()).get("name")
    return await _mkdir_result(await project_folder_ready(project_id), rel, name)


# ── 重點提案：勾選 / 取消 / 開放給客戶 ──────────────────────

_PIN_THUMB_NAMESPACE = "proposalpin"
_PIN_THUMB_MAX_SIDE = 1000
# 能算首頁/縮圖的格式。其餘（.pptx/.key/.docx…）顯示檔案圖示 —— 要算 PPT 首頁
# 得裝 LibreOffice（400MB+），不划算；要給客戶看的簡報本來就會轉成 PDF。
_PIN_THUMB_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".bmp", ".tif", ".tiff"}


def _pin_thumb_target(project_id: str, rel: str) -> tuple:
    """(圖床本機目錄, base_name, 對外網址)；圖床未設定 → ("", "", "")。

    base_name 用 rel 的雜湊 —— rel 含中文、斜線與空白，直接當檔名會壞。
    """
    import hashlib
    from core.assets_host import assets_target
    d, base = assets_target(_PIN_THUMB_NAMESPACE)
    if not d or not base:
        return "", "", ""
    key = hashlib.sha1(f"{project_id}\x00{rel}".encode("utf-8")).hexdigest()[:20]
    return d, key, f"{base}/{key}.webp"


async def _make_pin_thumb(folder_abs: str, project_id: str, rel: str) -> str:
    """勾選項 → 縮圖網址（算不出來 → ""，前端顯示檔案圖示，不擋勾選）。"""
    if os.path.splitext(rel)[1].lower() not in _PIN_THUMB_EXTS:
        return ""
    d, name, url = _pin_thumb_target(project_id, rel)
    if not d:
        return ""
    src = await asyncio.to_thread(safe_rel_path, folder_abs, rel)
    if not src:
        return ""
    from core.image_utils import webp_thumb_from_path
    saved = await asyncio.to_thread(webp_thumb_from_path, src, d, name,
                                    _PIN_THUMB_MAX_SIDE)
    return url if saved else ""


async def pin_on_row(prop, folder_abs: str, project_id: str, rel: str, *,
                     is_dir: bool = False, by: str = "") -> str:
    """把 rel 勾成**指定那一列**的重點提案（不 commit）→ 錯誤訊息（成功 ""）。

    存在的理由：deck 與勾選是同一列的兩個欄位（見 `_pins_row`），deck 的兩個
    入口（`POST /proposals/{pid}/deck` 上傳、`/deck/from-asset` 指定）要在
    **寫 deck_url 的同一個 session、同一列**上順手勾起來，才不會出現「是簡報
    但沒出現在提案資料」的分岔。走 `_save_pins` 做不到 —— 那支自己開 session、
    自己挑列。縮圖照樣算（算不出來不擋勾選）。
    """
    new, err = pa.pin(prop.pinned_assets, rel, is_dir=is_dir, by=by)
    if err:
        return err
    if not is_dir:
        thumb = await _make_pin_thumb(folder_abs, project_id, rel)
        if thumb:
            new = pa.set_thumb(new, rel, thumb)
    prop.pinned_assets = new
    return ""


async def _save_pins(project_id: str, mutate, after=None) -> dict:
    """讀 → mutate(現值) → 寫回 → 回目前狀態。`mutate` 回 `(新值, 錯誤訊息)`。

    `after(session, prop, old, new)`（async，選用）在同一個交易裡跟著跑 ——
    取消勾選要順手清 deck_url 用的（見 unpin）。
    """
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        prop = await _pins_row(session, project_id)
        if not prop:
            raise HTTPException(status_code=404,
                                detail="這個專案還沒有提案，無法設定重點提案")
        old = pa.rows(prop.pinned_assets)
        new, err = mutate(prop.pinned_assets)
        if err:
            raise HTTPException(status_code=422, detail=err)
        prop.pinned_assets = new
        if after:
            await after(session, prop, old, new)
        prop.updated_at = _now()
        await session.commit()
        return {"pinned": pa.rows(new),
                "pins_public": bool(getattr(prop, "pins_public", False))}


@router.post("/projects/{project_id}/proposal-assets/pin")
async def pin_proposal_asset(project_id: str, request: Request):
    """勾一個資產成為「重點提案」。body: `{rel, is_dir}`。

    縮圖在勾選當下算（勾的量很少，且使用者正等著看卡片長出來）；算不出來
    不影響勾選本身。
    """
    payload = assets_auth(request)
    body = await request.json()
    rel, is_dir = str(body.get("rel") or ""), bool(body.get("is_dir"))
    who = str((payload or {}).get("username") or "")
    out = await _save_pins(project_id,
                           lambda cur: pa.pin(cur, rel, is_dir=is_dir, by=who))
    if not is_dir:
        _require_db()
        factory = await _get_factory()
        async with factory() as session:
            folder_abs = await project_folder_abs(session, project_id)
        thumb = await _make_pin_thumb(folder_abs, project_id, rel)
        if thumb:
            out = await _save_pins(project_id,
                                   lambda cur: (pa.set_thumb(cur, rel, thumb), ""))
    return out


@router.delete("/projects/{project_id}/proposal-assets/pin")
async def unpin_proposal_asset(project_id: str, request: Request, rel: str = ""):
    """取消勾選 —— 客戶那條路**立刻**失效（下一次 allows 就回 False）。

    取消掉的若正好是目前的**提案簡報**（或它所在的那個勾選資料夾）→ 一併清掉
    `deck_url`。deck 與勾選是同一列的兩個欄位，不同步清的話會變成「提案資料
    裡看不到、詳情頁卻還掛著下載連結」的兩份真相。

    判斷用 `pa.allows` 的**前後差**（本來被涵蓋、現在不被涵蓋才清）——
    不是「rel 字串等於 deck」：這樣連「勾了整個資料夾、deck 在裡面」也對，
    而且**從來沒被勾過的舊 deck 不會被任何一次取消勾選誤清**。
    """
    assets_auth(request)
    state = {"deck_cleared": False}

    async def _clear_deck(session, prop, old, new):
        deck_rel = _deck_rel(prop.deck_url or "",
                             await project_folder_abs(session, project_id))
        if deck_rel and pa.allows(old, deck_rel) and not pa.allows(new, deck_rel):
            prop.deck_url = ""
            state["deck_cleared"] = True

    out = await _save_pins(project_id, lambda cur: (pa.unpin(cur, rel), ""),
                           after=_clear_deck)
    return {**out, **state}


@router.post("/projects/{project_id}/proposal-assets/pins/public")
async def set_pins_public(project_id: str, request: Request):
    """開/關「客戶看得到重點提案」。body: `{public: bool}`。

    🔴 勾選是策展、這個開關才是授權。分開的理由：勾選當下多半只是想讓團隊
    知道現在以哪一版為準，不該順手就把檔案送到客戶眼前。
    """
    assets_auth(request)
    want = bool((await request.json()).get("public"))
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        prop = await _pins_row(session, project_id)
        if not prop:
            raise HTTPException(status_code=404, detail="這個專案還沒有提案")
        prop.pins_public = want
        prop.updated_at = _now()
        await session.commit()
        return {"pinned": pa.rows(prop.pinned_assets), "pins_public": want}


@router.get("/projects/{project_id}/proposal-assets/file")
async def download_proposal_asset(project_id: str, request: Request, rel: str = ""):
    """下載資產夾內的單一檔案（rel = 列表回傳的相對路徑）。"""
    assets_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        path = await resolve_asset_file(session, project_id, rel)
    from fastapi.responses import FileResponse
    return FileResponse(path, filename=os.path.basename(path))


@router.delete("/projects/{project_id}/proposal-assets/file")
async def delete_proposal_asset(project_id: str, request: Request, rel: str = ""):
    """刪除資產夾內的單一檔案。若刪掉的正是目前指定的提案簡報 → 一併清掉
    deck_url（否則詳情頁會留一個指向不存在檔案的下載連結）。"""
    assets_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        path = await resolve_asset_file(session, project_id, rel)
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
    這份才會出現在提案詳情與公開共編頁的「下載簡報」。

    指定的同時**順手勾成重點提案** —— 簡報就是此刻的重點，兩邊分開勾會做出
    「是簡報卻不在提案資料裡」的兩份真相（勾不上例如已滿 30 項 → 不擋指定，
    回一句 warning）。反向不成立：取消指定不會取消勾選。
    """
    payload = assets_auth(request)
    _require_db()
    body = await request.json()
    rel = str(body.get("rel") or "").strip()
    who = str((payload or {}).get("username") or "")
    factory = await _get_factory()
    warning = ""
    async with factory() as session:
        folder_abs = await project_folder_abs(session, project_id)
        deck_url = to_canonical_path(
            await file_or_404(folder_abs, rel)) if rel else ""
        target = await _deck_target(session, project_id)   # 讀寫同一條挑法
        if not target:
            raise HTTPException(status_code=404,
                                detail="此專案還沒有提案 — 請先在「提案企劃」分頁建立")
        target.deck_url = deck_url
        if rel:
            warning = await pin_on_row(target, folder_abs, project_id, rel, by=who)
            if warning:
                warning = f"已設為提案簡報，但沒能勾進提案資料：{warning}"
        target.updated_at = _now()
        await session.commit()
    out = {"status": "ok", "deck_url": deck_url}
    return {**out, "warning": warning} if warning else out


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


# ── 分塊上傳（繞開 Cloudflare 的 100MB 單請求上限）──────────────
#
# 落地層與影像紀錄共用 core/chunked_upload（那支刻意不綁任何子系統）。這裡只做
# 三件這個子系統特有的事：目的夾怎麼算、檔名怎麼清洗去重、要不要重建子目錄。
#
# 兩個上傳面（專案的、任一資料夾的）各包一層薄殼，端點路徑刻意排成
# `{既有 upload 端點}/begin | {id}/chunk | {id}/finish` —— 前端拿原本那個 upload
# 網址接後綴就好，不必為兩個面各記一組。
#
# staging 夾是 `{proposals.root}/.chunk-uploads`：與所有目的地同一個 volume
# （收尾用 os.replace 原子改名），`.` 開頭 → 總覽與列檔都掃不到半成品。

# 同一個 upload_id 的併發保護（行程內）。append_bytes 的位移檢查擋得住重複寫入，
# 但兩個同位移的請求同時通過檢查就會各寫一次。
_chunk_locks: defaultdict = defaultdict(asyncio.Lock)
_chunk_last_gc = 0.0
_CHUNK_GC_INTERVAL = 3600


async def _chunk_root() -> str:
    """半成品的落腳處 = 提案資產根目錄（與所有目的地同 volume）。"""
    root = await proposals_root()
    if not root:
        raise HTTPException(status_code=503, detail="尚未設定提案資產根目錄")
    return root


def _chunk_err(e: cu.ChunkError) -> HTTPException:
    """ChunkError → HTTP。🔴 `reason` 這個 key 不能改名：前端顯示錯誤走
    js/shared/utils.js 的 httpError，它讀字串 detail 或 `detail.reason`。"""
    code = {"offset": 409, "too-large": 413, "missing": 404,
            "bad-id": 400}.get(e.kind, 400)
    return HTTPException(status_code=code,
                         detail={"error": e.kind, "reason": e.detail,
                                 "received": e.received})


async def _chunk_gc(root: str) -> None:
    """每小時掃一次 staging 夾清掉棄置的半成品（scandir 打 NAS，不值得每次做）。
    順手清掉沒人持有的鎖 —— 放棄的上傳走不到 finish。"""
    global _chunk_last_gc
    now = time.monotonic()
    if now - _chunk_last_gc < _CHUNK_GC_INTERVAL:
        return
    _chunk_last_gc = now
    for k in [k for k, lock in _chunk_locks.items() if not lock.locked()]:
        _chunk_locks.pop(k, None)
    await asyncio.to_thread(cu.gc, root)


async def _chunk_begin(body: dict) -> dict:
    """開始（或接續）一個分塊上傳 → {upload_id, received, chunk_bytes}。

    upload_id 由（瀏覽器鍵, 檔名, 大小, 修改時間）推導 → 同一個檔重傳落回同一個
    半成品，換頁重開也接得回去。副檔名與大小在這裡就擋掉，不讓人先傳完才發現。
    """
    name = clean_filename(str(body.get("filename") or ""))
    ext = os.path.splitext(name)[1].lower()
    if ext in BLOCKED_UPLOAD_EXTS:
        raise HTTPException(status_code=400, detail="不允許的檔案類型（" + ext + "）")
    size = int(body.get("size") or 0)
    if size <= 0:
        raise HTTPException(status_code=400, detail="缺少檔案大小")
    if size > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413,
                            detail="檔案超過 %dMB 上限" % (_MAX_UPLOAD_BYTES // (1024 * 1024)))
    root = await _chunk_root()
    uid = cu.upload_id(str(body.get("client_key") or ""), name, size,
                       str(body.get("mtime") or ""))
    await _chunk_gc(root)
    received = await asyncio.to_thread(cu.begin, root, uid)
    return {"upload_id": uid, "received": received, "chunk_bytes": cu.CHUNK_BYTES}


async def _chunk_put(upload_id: str, offset: int, request: Request) -> dict:
    """收一塊（raw body，非 multipart）→ {received}。

    片段先收在 list 再交給 writelines —— 合併成一個 bytes 是這條路徑上唯一跑在
    事件迴圈的工作，省掉它等於把 agent 的反應時間還回去。
    """
    root = await _chunk_root()
    cap = cu.CHUNK_BYTES * 2
    parts, total = [], 0
    async for part in request.stream():
        total += len(part)
        if total > cap:
            raise HTTPException(status_code=413, detail="單塊過大")
        parts.append(part)
    try:
        cu.part_path(root, upload_id)          # 先驗 id 格式，再讓 defaultdict 生鎖
        async with _chunk_locks[upload_id]:
            received = await asyncio.to_thread(
                cu.append_bytes, root, upload_id, parts, total,
                offset=int(offset), max_bytes=_MAX_UPLOAD_BYTES)
    except cu.ChunkError as e:
        raise _chunk_err(e)
    return {"received": received}


async def _chunk_finish(folder_abs: str, rel: str, upload_id: str, body: dict) -> dict:
    """收尾：半成品改名進目的夾 → 回這一層的新內容（同單一請求上傳的形狀）。

    `path`（前端拖整個資料夾時會送）＝該檔在來源夾裡的相對路徑 → 照著重建子目錄，
    與 core.save_uploads 同一套規則（safe_sub_dirs + within_dir 兩道防護）。
    """
    root = await _chunk_root()
    dir_abs = await _subdir_or_404(folder_abs, rel)
    name = clean_filename(str(body.get("filename") or ""))
    segs = safe_sub_dirs(str(body.get("path") or "")) or []
    dest_dir = os.path.join(dir_abs, *segs) if segs else dir_abs
    if not within_dir(dir_abs, dest_dir):
        raise HTTPException(status_code=400, detail="路徑逃出資料夾")

    def _move() -> str:
        os.makedirs(dest_dir, exist_ok=True)
        final = dedupe(name, set(os.listdir(dest_dir)), keep_ext=True)
        cu.finish(root, upload_id, os.path.join(dest_dir, final),
                  expect_bytes=int(body.get("size") or -1))
        return final

    try:
        async with _chunk_locks[upload_id]:
            final = await asyncio.to_thread(_move)
    except cu.ChunkError as e:
        raise _chunk_err(e)
    _chunk_locks.pop(upload_id, None)
    saved = "/".join(segs + [final]) if segs else final
    return {"status": "ok", "saved": [saved], "skipped": [],
            **await _level(folder_abs, rel)}


@router.post("/projects/{project_id}/proposal-assets/upload/begin")
async def project_chunk_begin(project_id: str, request: Request, body: dict):
    assets_auth(request)
    return await _chunk_begin(body)


@router.put("/projects/{project_id}/proposal-assets/upload/{upload_id}/chunk")
async def project_chunk_put(project_id: str, upload_id: str, offset: int,
                            request: Request):
    assets_auth(request)
    return await _chunk_put(upload_id, offset, request)


@router.post("/projects/{project_id}/proposal-assets/upload/{upload_id}/finish")
async def project_chunk_finish(project_id: str, upload_id: str, request: Request,
                               body: dict, rel: str = ""):
    assets_auth(request)
    return await _chunk_finish(await project_folder_ready(project_id), rel,
                               upload_id, body)


@router.post("/proposal-assets/folder/upload/begin")
async def folder_chunk_begin(request: Request, body: dict, folder: str = ""):
    assets_auth(request)
    return await _chunk_begin(body)


@router.put("/proposal-assets/folder/upload/{upload_id}/chunk")
async def folder_chunk_put(upload_id: str, offset: int, request: Request,
                           folder: str = ""):
    assets_auth(request)
    return await _chunk_put(upload_id, offset, request)


@router.post("/proposal-assets/folder/upload/{upload_id}/finish")
async def folder_chunk_finish(upload_id: str, request: Request, body: dict,
                              folder: str = "", rel: str = ""):
    assets_auth(request)
    return await _chunk_finish(await _folder_or_404(folder), rel, upload_id, body)

@router.get("/proposal-assets/overview")
async def proposal_assets_overview(request: Request):
    """資產資料夾總覽：root 底下每個資料夾，標示有沒有對應到專案。

    - **已連結**：`crm_projects.proposal_folder_name` 指到它 → 帶專案名/客戶。
    - **未連結**：磁碟上有、系統沒登記（過去手工整理的）→ 一樣可展開看檔案、
      下載、上傳。

    一次回全部（資料夾數是專案數量級）—— 搜尋由前端就地過濾，不做 server-side
    `q`：每個按鍵都重跑一次 CrmProject×Client join 換不到任何東西。"""
    assets_auth(request)
    _require_db()
    factory = await _get_factory()
    raw = await _raw_root()            # 一次解析（下面回應裡的 root 是同一份）
    root = to_local_path(raw)

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
    return {"root": raw, "root_set": reachable, "folders": out}


async def _folder_or_404(folder: str) -> str:
    """root 底下的資料夾絕對路徑（三個 folder 端點共用；含路徑逃逸防護）。

    🔴 `.`/`_` 開頭的夾**不是**提案資產夾（企劃範本庫的 `_範本`、分塊上傳的
    `.chunk-uploads`…）。`_scan_root_folders` 早就用這條述詞把它們擋在總覽外，
    但**這條吃的是使用者傳來的資料夾名** —— 不擋的話它們會變成可以被「連結
    專案」「改名」的夾，而改名走 rename_and_remap（專案夾的程序），連下去就壞了。
    用同一條述詞而不是保留名清單：清單只擋得住有人記得登記的那些。"""
    if (folder or "").strip()[:1] in (".", "_"):
        raise HTTPException(status_code=404, detail="找不到資料夾（或無法存取）")
    folder_abs = await asyncio.to_thread(safe_subfolder, await proposals_root(), folder)
    if not folder_abs:
        raise HTTPException(status_code=404, detail="找不到資料夾（或無法存取）")
    return folder_abs


@router.get("/proposal-assets/folder/files")
async def proposal_folder_files(request: Request, folder: str = "", rel: str = ""):
    """任一資產資料夾裡**某一層**的內容（未連結專案的「過去資料夾」也能看）。
    `rel` 空 = 最外層；子資料夾走 dirs 回傳的 rel 再打一次。"""
    assets_auth(request)
    return {"folder": folder, **await _level(await _folder_or_404(folder), rel)}


@router.get("/proposal-assets/folder/file")
async def proposal_folder_file(request: Request, folder: str = "", rel: str = ""):
    """任一資產資料夾內的單檔下載。"""
    assets_auth(request)
    from fastapi.responses import FileResponse
    path = await file_or_404(await _folder_or_404(folder), rel)
    return FileResponse(path, filename=os.path.basename(path))


@router.post("/proposal-assets/folder/upload")
async def proposal_folder_upload(request: Request, folder: str = "", rel: str = "",
                                 files: List[UploadFile] = File(...),
                                 paths: List[str] = Form(default=[])):
    """上傳檔案進任一資產資料夾（未連結專案的也可以 —— owner 指定）。
    `rel` = 落在哪一層（空 = 最外層；就是使用者目前看的那一層）。"""
    assets_auth(request)
    return await _upload_result(await _folder_or_404(folder), files, rel, paths)


@router.post("/proposal-assets/folder/mkdir")
async def proposal_folder_mkdir(request: Request, folder: str = "", rel: str = ""):
    """在任一資產資料夾的 rel 這一層底下開新子資料夾（body `{name}`）。"""
    assets_auth(request)
    name = (await request.json()).get("name")
    return await _mkdir_result(await _folder_or_404(folder), rel, name)


@router.post("/proposal-assets/folder/rename")
async def proposal_folder_rename(request: Request):
    """把資產根目錄底下的資料夾改名（body `{folder, new_name}`）。

    已連結專案的資料夾也能從這裡改 —— deck_url 的絕對路徑同步更新，
    `proposal_folder_name` 跟著改（同進退由 core.rename_and_remap 保證）。
    只改**最外層**的資料夾名：它才是專案↔資料夾的對應鍵，子夾結構原封不動。
    """
    assets_auth(request)
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
        new_name, err = validate_folder_name(await proposals_root(),
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
    assets_auth(request)
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
                _vacate_if_empty, await proposals_root(), old):
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
    root = await proposals_root()
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
    root = await proposals_root()
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
    if not await proposals_root():
        return False, ""
    new_name = make_dated_folder_name(
        project_name, created,
        await _taken_names(session, exclude_project_id=project_id),
        existing=project.proposal_folder_name)
    if new_name == project.proposal_folder_name:
        return False, ""
    return await _rename_folder(
        session, project.proposal_folder_name, new_name, project)
