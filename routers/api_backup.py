"""api_backup.py — 備份派工 ＋ 備份三根的專案投影。

三根（本機／NAS／Proxy）的**正本存在 CRM 專案上**（owner 2026-09-10，
`crm_projects.backup_*_root`），備份頁只顯示不編輯。這裡是機隊那一側的讀取口。
"""
from fastapi import APIRouter, Request  # type: ignore

import core.state as state  # type: ignore
from core.auth import check_lan_or_logged_in  # type: ignore
from core.drive_map import to_local_path  # type: ignore
from core.schemas import BackupRequest, BackupRootsPayload  # type: ignore
from core.worker import enqueue_job  # type: ignore

router = APIRouter()

#: 回存用：API 欄位名 ↔ crm_projects 欄位名
_WRITE_MAP = (
    ("local_root", "backup_local_root"),
    ("nas_root", "backup_nas_root"),
    ("proxy_root", "backup_proxy_root"),
)


@router.post("/api/v1/jobs")
async def create_backup_job(req: BackupRequest):
    # 磁碟代號→UNC 翻譯在 enqueue_job 中央咽喉統一做（core/drive_map），
    # 排程/書籤重放同樣涵蓋；翻譯明細由 worker 以 [UNC] log 推到終端。
    #
    # 綁了專案時三根由 enqueue_job 從 DB 覆寫（前端送什麼都不算數）——
    # 「備份頁只是顯示」不能只做在 UI 上，改個 DOM 就繞過去了。
    job_id, warning = await enqueue_job(req, req.project_name, "backup")
    resp = {"status": "queued", "job_id": job_id}
    if warning:
        resp["warning"] = warning
    return resp


# ── 備份三根的專案投影 ────────────────────────────────────────────────
# 🔴 **不要改成重用 `/api/v1/crm/projects`**，三個理由缺一不可：
#   1. 那支的 `_to_project_dict` 帶錢（合約金額／應收／已收）。備份頁不需要也不該拿到。
#      這裡是白名單投影（同 core/invoice_share.py 的做法）：只給 id/name/三根。
#   2. 守衛必須是 `check_lan_or_logged_in` 而不是 CRM 的模組鑰匙 —— 後期製作流程的
#      產品前提是「同事在本機 agent 上不登入直接用」（CLAUDE.md 已載明）。備檔電腦
#      沒人登入，用 crm_projects 模組守它等於直接鎖死備份頁。
#   3. 機隊 agent 有 sqlalchemy + asyncpg，各自讀同一個 Postgres，不必經 master 轉發。
#
# DB 斷線一律回 200 + status="db_offline"，**不可以擋住派工**（同 core/public_access
# 的「DB 掛掉不擋人」）：前端據此退回手動輸入三根。

_PROJECTION_LIMIT = 200


def _root_view(project) -> dict:
    """DB 存 canonical UNC → 翻成**呼叫這台**看得到的視角再回前端。

    翻譯放在這裡而不是前端：NAS 對外容器看到的是 `/share/…`、Windows 看到的是
    UNC，前端沒有能力知道自己跑在哪一種視角上。
    """
    return {
        "id": project.id,
        "name": project.name or "",
        "local_root": to_local_path(project.backup_local_root or ""),
        "nas_root": to_local_path(project.backup_nas_root or ""),
        "proxy_root": to_local_path(project.backup_proxy_root or ""),
    }


async def _factory():
    if not state.db_online:
        return None
    try:
        from db.session import get_session_factory  # lazy：精簡 agent 走 fallback
        return get_session_factory()
    except Exception:
        return None


@router.get("/api/v1/projects/backup-roots")
async def list_backup_roots(request: Request, q: str = "", limit: int = 50):
    """備份頁的專案選擇器。`q` 為案名模糊搜尋（空＝最近的案）。"""
    check_lan_or_logged_in(request)
    factory = await _factory()
    if not factory:
        return {"status": "db_offline", "projects": []}
    try:
        from sqlalchemy import select  # type: ignore

        from db.models import CrmProject  # type: ignore
        # 私帳案**照樣列出、照樣可勾選**（owner 2026-09-10 明確拍板）——
        # 這條是對 2026-08-28「連專案都看不到」的**局部例外**，只在這支投影成立。
        # 理由：私帳的案子一樣要備份，備檔電腦選不到就只能手打路徑，等於這整個
        # 功能對私帳案失效。代價是案名對區網免登入可見（這支守 check_lan_or_logged_in）。
        # 🔴 讓這個例外可以接受的前提是**這支不帶錢**：白名單投影只有 id／name／三根
        # （TestRootViewProjection.test_money_never_leaks 釘著）。要往這支加欄位之前，
        # 先回來讀這一段 —— 加了任何金額欄，這個例外就不成立了。
        stmt = select(CrmProject)
        kw = (q or "").strip()
        if kw:
            stmt = stmt.where(CrmProject.name.ilike(f"%{kw}%"))
        stmt = stmt.order_by(CrmProject.created_at.desc()).limit(
            max(1, min(int(limit or 50), _PROJECTION_LIMIT)))
        async with factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return {"status": "ok", "projects": [_root_view(p) for p in rows]}
    except Exception as e:
        # 讀不到專案清單不是派工的阻礙 —— 回 db_offline 讓前端退回手動輸入
        return {"status": "db_offline", "projects": [], "detail": str(e)[:200]}


@router.get("/api/v1/projects/backup-roots/{project_id}")
async def get_backup_roots(project_id: str, request: Request):
    check_lan_or_logged_in(request)
    factory = await _factory()
    if not factory:
        return {"status": "db_offline", "project": None}
    try:
        from db.models import CrmProject  # type: ignore
        async with factory() as session:
            p = await session.get(CrmProject, project_id)
        # 私帳案在這支同樣不遮（跟上面的列舉同一個 owner 決定，兩處要一致 ——
        # 列得出來卻查不到，畫面會變成「選了就壞掉」）。
        if not p:
            return {"status": "not_found", "project": None}
        return {"status": "ok", "project": _root_view(p)}
    except Exception as e:
        return {"status": "db_offline", "project": None, "detail": str(e)[:200]}


@router.put("/api/v1/projects/backup-roots/{project_id}")
async def save_backup_roots(project_id: str, req: BackupRootsPayload, request: Request):
    """備份頁把**還沒填**的三根存回專案（owner 2026-09-10）。

    🔴 **只能填空，不能覆寫。** 專案上已經有值的那一根一律跳過並回報在 `skipped`
    裡，要改請到專案頁。這不是潔癖，是這支端點守 `check_lan_or_logged_in`
    （免登入）的前提：能力限縮在「補上空白」，就沒有人能從備份頁把別人設好的
    路徑改掉或清掉。要放寬成可覆寫的話，守衛得先換成 CRM 的模組鑰匙 ——
    但那會讓沒人登入的備檔電腦連填都填不了，等於這條路整條沒用。

    正規化與 `PUT /crm/projects/{id}` 共用 `normalize_backup_roots`
    （canonical UNC ＋ 語法警告），兩條寫入路不能各算一次。
    """
    check_lan_or_logged_in(request)
    factory = await _factory()
    if not factory:
        return {"status": "db_offline", "project": None, "saved": [], "skipped": []}

    from routers.crm._shared import _now  # type: ignore
    from routers.crm.projects import normalize_backup_roots  # type: ignore
    # 只把**有送而且非空**的欄位納入正規化；沒送的不要變成 None 去清掉東西
    incoming = {col: getattr(req, field)
                for field, col in _WRITE_MAP
                if str(getattr(req, field) or "").strip()}
    warnings = normalize_backup_roots(incoming)

    try:
        from db.models import CrmProject  # type: ignore
        saved, skipped = [], []
        async with factory() as session:
            p = await session.get(CrmProject, project_id)
            if not p:
                return {"status": "not_found", "project": None,
                        "saved": [], "skipped": []}
            for field, col in _WRITE_MAP:
                if col not in incoming:
                    continue
                if str(getattr(p, col, "") or "").strip():
                    skipped.append(field)          # 已設定 → 不覆寫
                    continue
                setattr(p, col, incoming[col])
                saved.append(field)
            if saved:
                p.updated_at = _now()
                await session.commit()
                await session.refresh(p)
            view = _root_view(p)
        result = {"status": "ok", "project": view, "saved": saved, "skipped": skipped}
        if warnings:
            result["warning"] = "；".join(warnings)
        if skipped:
            result["skipped_reason"] = "這幾根專案上已經有設定了，要修改請到專案頁"
        return result
    except Exception as e:
        return {"status": "db_offline", "project": None,
                "saved": [], "skipped": [], "detail": str(e)[:200]}
