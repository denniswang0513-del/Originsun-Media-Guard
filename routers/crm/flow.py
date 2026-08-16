"""routers/crm/flow.py — 專案工作流（階段 × 五軌進度）

規格正本 `docs/PROPOSAL_PLANNER.md` §14。範本與判定邏輯的正本在
`core.project_flow`（純函式、有單元測試）；這裡只做 DB 讀取與聚合。

🔴 **這支不儲存任何別處已有的東西**（owner 2026-08-14「各自工作、資料放在
正確的位置」）：報價住報價管理、發票住帳務、素材住影像紀錄、歸檔清單與專案
回顧住 `crm_projects.archive_checklist / review_kpta`（完稿結案分頁）——
這裡查詢時把它們聚合成燈號，**不搬不抄**。整個功能唯一的新儲存是手動勾選
（`crm_projects.flow_checks`）。

為什麼住在 `routers/crm/` 而不是自己一支頂層 router：它讀寫的每一張表都是
CRM 的、資源就是 `crm_projects`，而同樣掛在那一列兩個 JSONB 欄上的姊妹功能
（`archive.py`）就在隔壁。住這裡才吃得到 `_shared` 的 `_with_project` /
`_patch_project_json`（與 archive.py 共用同一份 JSONB 讀寫樣板），也才在
`_crm_read_guard` 的保護傘下 —— 那道守衛的意義正是「新端點預設就安全」，
自己另開命名空間等於把自己排除在外。

守衛：
- 讀取＝`proposal_auth`（admin / preprod_proposals / preprod_plan /
  crm_projects）—— 公司內部進度透明，而且提案頁本來就是這個閘。
- 勾手動里程碑＝`_check_flow_check_auth`（`crm_projects` 模組即可，
  owner 2026-08-14 拍板）。
- 推進階段**不在這裡做** —— 前端打既有的 `PATCH /projects/{id}/status`，
  那支與這裡的 `can_advance` 共用 `core.project_flow.ADVANCE_MODULES`。
"""
from __future__ import annotations

import uuid

from fastapi import Request

from core import project_archive as pa
from core import project_flow as pf
from core.auth import payload_grants, tab_modules
from core.crm_logic import (effective_prod_stage, is_main_work,
                            project_works_summary, work_completeness, work_stage)

from ._shared import (router, _check_flow_check_auth, _crm_session, _now,
                      _patch_project_json, _username, _with_project)

# DB 相依包在 try —— 機隊的精簡 agent 沒有 sqlalchemy，這是整個 crm 套件的
# 慣例（12/14 個領域模組都這樣），也是 test_crm_shared_reexports 守的東西。
try:
    from sqlalchemy import func, literal, select
    from db.models import (CrmInvoice, CrmProject, CrmProjectShowcase, CrmProjectStaff,
                           CrmQuotation, FootageIndex, PortalReviewLink,
                           PreprodBrief, PreprodBriefTemplate, PreprodLocationUsage,
                           PreprodProposal, PreprodReferenceLink, ProjectMediaFile,
                           Timesheet)
except ImportError:
    pass


# 清單一次最多問幾個專案。提案清單目前是前端全集（無分頁），真實量級是
# 幾十；設上限是為了「網址塞一千個 id」不會變成一趟掃全庫的查詢。
_SUMMARY_MAX = 200


def _exists(model, *where):
    """EXISTS 子查詢 —— 這些訊號只問「有沒有」，不必 COUNT 掃完整批。"""
    return select(literal(1)).select_from(model).where(*where).exists()


def _count_sq(model, *where):
    return select(func.count()).select_from(model).where(*where).scalar_subquery()


async def _gather_facts(session, projects) -> dict:
    """各正本 → 每個專案的 AUTO 訊號。回 `{project_id: (facts, detail, meta)}`。

    - facts：鍵**恰好**等於 `pf.AUTO_KEYS`（test_flow_facts_align 兩面都擋）；
      值是 bool，None ＝ 略過
    - detail：item_key → 「為什麼亮」的說明字串
    - meta：不是燈號、但 payload 要用的事實（目前只有 proposal_count）

    🔴 **一趟查完，而且與專案數無關**。訊號彼此無關，逐支 await 的話是 18 趟
    跨網路 round-trip、整段期間佔著五條連線池中的一條（pool_size=5：四個人同時
    開就把池子吃光）。做法是從 `crm_projects` 驅動、每個訊號一個**相關**純量
    子查詢 —— 一列一個專案，Postgres 一個 plan 算完。

    🔴 **一份實作**：詳情面板（一個專案）與清單摘要（幾十個）走同一支。
    分兩支寫的話，兩邊對「哪盞燈算亮」的判定遲早會漂，而那正是使用者
    在清單上看到 3/5、點進去看到 2/5 的來源。

    燈要能自己解釋為什麼亮，不然沒人信它 —— detail 就是那句話。
    """
    by_id = {p.id: p for p in projects}
    if not by_id:
        return {}
    pid = CrmProject.id          # 相關子查詢都掛在驅動列上
    inv_w = (CrmInvoice.project_id == pid, CrmInvoice.payment_type == "收款",
             CrmInvoice.issue_status != "作廢")
    prop_w = (PreprodProposal.project_id == pid,)

    rows = (await session.execute(select(
        CrmProject.id.label("pid"),
        _count_sq(CrmQuotation, CrmQuotation.project_id == pid).label("quote_n"),
        _exists(CrmQuotation, CrmQuotation.project_id == pid,
                CrmQuotation.status == "已簽核").label("quote_won"),
        _count_sq(CrmInvoice, *inv_w).label("inv_n"),
        _count_sq(CrmInvoice, *inv_w,
                  CrmInvoice.payment_status == "未收款").label("inv_unpaid"),
        _count_sq(PreprodProposal, *prop_w).label("prop_n"),
        # 只問 template_id 在不在，**不把整包企劃矩陣 JSONB 拉出庫**
        # （api_proposals 清單的既有做法，那包可能好幾十 KB）
        _exists(PreprodProposal, *prop_w,
                PreprodProposal.plan.op("->>")("template_id").isnot(None)
                ).label("ideation"),
        _exists(PreprodProposal, *prop_w,
                PreprodProposal.status.in_(("已提案", "入圍", "成案"))).label("pitched"),
        _exists(PreprodProposal, *prop_w,
                PreprodProposal.status == "成案").label("won"),
        _exists(PreprodBrief,
                PreprodBrief.proposal_id.in_(
                    select(PreprodProposal.id).where(*prop_w))).label("brief"),
        _exists(CrmProjectStaff, CrmProjectStaff.project_id == pid).label("staffed"),
        _exists(ProjectMediaFile, ProjectMediaFile.project_id == pid).label("footage_in"),
        _exists(Timesheet, Timesheet.project_id == pid).label("ts_by_id"),
        # ⚠ Sheet 同步的工時列 project_id 可能還沒對映到（見 db.models.Timesheet），
        # 所以名稱也認 —— 只認得 id 的話，團隊明明在填工時卻不亮燈。拆成兩個
        # EXISTS 而不是 or_()：or_ 讓 idx_ts_project 失效、整表掃描。
        _exists(Timesheet, Timesheet.project_name == CrmProject.name,
                CrmProject.name != "").label("ts_by_name"),
        _exists(PortalReviewLink, PortalReviewLink.project_id == pid,
                PortalReviewLink.status == "已核准").label("approved"),
        _exists(PreprodBriefTemplate,
                PreprodBriefTemplate.source_project_id == pid).label("h_tpl"),
        _exists(PreprodReferenceLink,
                PreprodReferenceLink.target_type == "crm_project",
                PreprodReferenceLink.target_id == pid).label("h_refs"),
        _exists(PreprodLocationUsage,
                PreprodLocationUsage.project_id == pid).label("h_loc"),
        _exists(FootageIndex, FootageIndex.project_id == pid).label("h_footage"),
    ).where(CrmProject.id.in_(list(by_id))))).all()

    works_by_pid = await _works_by_project(session, list(by_id))
    return {r.pid: _facts_for(r, by_id[r.pid], works_by_pid.get(r.pid, []))
            for r in rows}


def _facts_for(row, project, works) -> tuple[dict, dict, dict]:
    """一列查詢結果 + 專案自己的 JSONB 欄 → (facts, detail, meta)。"""
    f = {
        "client": bool(project.client_id),
        "quote": row.quote_n > 0,
        "quote_won": bool(row.quote_won),
        "invoiced": row.inv_n > 0,
        # 結清＝有收款發票且沒有一張停在「未收款」。沒開過發票不算結清
        # （否則一個還沒請款的案子會顯示「款項結清」）。
        "settled": row.inv_n > 0 and row.inv_unpaid == 0,
        "proposal": row.prop_n > 0,
        "ideation": bool(row.ideation),
        "pitched": bool(row.pitched),
        "won": bool(row.won),
        "brief": bool(row.brief),
        "staffed": bool(row.staffed),
        "footage_in": bool(row.footage_in),
        "timesheet": bool(row.ts_by_id or row.ts_by_name),
        "approved": bool(row.approved),
        "h_tpl": bool(row.h_tpl),
        "h_refs": bool(row.h_refs),
        "h_loc": bool(row.h_loc),
        "h_footage": bool(row.h_footage),
    }
    # 不是燈號、但 payload 要用的事實。**不能混進 facts** —— 那個 dict 的鍵
    # 必須恰好等於 AUTO_KEYS（test_flow_facts_align 兩面都擋）。
    meta = {"proposal_count": row.prop_n}
    d = {}
    if row.quote_n:
        d["quote"] = f"報價單 {row.quote_n} 版"
    if row.prop_n:
        d["proposal"] = f"提案 {row.prop_n} 筆"
    if row.inv_n:
        d["invoiced"] = f"收款發票 {row.inv_n} 張"
        d["settled"] = ("全部收訖" if not row.inv_unpaid
                        else f"還有 {row.inv_unpaid} 張未收款")

    # ── 交付軌：讀既有的歸檔清單 / KPTA / 上架作品，不另存一份 ──
    arch_rows = pa.rows(project.archive_checklist)
    final_row = next((r for r in arch_rows if r["key"] == "final"), None)
    prog = pa.progress_of(arch_rows)
    f["final"] = bool(final_row and final_row["status"] in (pa.DONE, pa.NA))
    f["archived"] = prog["ready"]
    d["archived"] = f"{prog['done']}/{prog['total']} 項已收"
    f["retro"] = any((v or "").strip() for v in pa.kpta(project.review_kpta).values())

    f["work_ready"], f["published"], wd = _work_signals(works, project)
    d.update(wd)
    return f, d, meta


async def _works_by_project(session, pids) -> dict:
    """上架作品列，一趟撈完一批專案的。

    只取 work_completeness 要的欄位 —— select(Model) 會把 ai_reference_files
    （每份文件上限 8000 字、份數無上限）等大欄位一起拖出來算兩個布林。
    這支查不進上面那個相關子查詢的 SELECT：它一個專案回**多列**，不是純量。
    """
    rows = (await session.execute(
        select(CrmProjectShowcase.id, CrmProjectShowcase.project_id,
               CrmProjectShowcase.video_url, CrmProjectShowcase.youtube_id,
               CrmProjectShowcase.extra_videos, CrmProjectShowcase.gallery,
               CrmProjectShowcase.cover_url, CrmProjectShowcase.featured_image,
               CrmProjectShowcase.description, CrmProjectShowcase.credits,
               CrmProjectShowcase.credits_text, CrmProjectShowcase.prod_stage,
               CrmProjectShowcase.published)
        .where(CrmProjectShowcase.project_id.in_(list(pids))))).all()
    out = {}
    for w in rows:
        out.setdefault(w.project_id, []).append(w)
    return out


def _work_signals(works, project):
    """1:N 作品 → (上架素材完成度, 官網上線, detail)。

    階段推導與「不上官網」的排除都走 `core.crm_logic` 的既有正本
    （`work_stage` / `project_works_summary`），不自己再判一次 —— 結案看板
    用的是同一組函式，兩邊漂掉的話同一支作品會在兩個畫面顯示不同狀態。
    完成度也把九個欄位全帶上（少帶 youtube_id / extra_videos / credits_text
    的話，YouTube 影片或純文字名單的作品會被誤判成沒素材）。
    """
    stage = project.website_prod_stage or ""
    if not works:
        # 沒有作品列：標了不上官網就是略過，否則就是還沒建
        return (None, None, {}) if stage == "不上官網" else (False, False, {})

    summary = project_works_summary([
        {"stage": work_stage(bool(w.published),
                             effective_prod_stage(w.prod_stage, stage,
                                                  is_main=is_main_work(w)))}
        for w in works])
    if summary["total"] and summary["skipped"] == summary["total"]:
        return None, None, {"work_ready": "標記為不上官網"}

    ready = sum(1 for w in works
                if all(work_completeness(
                    video_url=w.video_url, youtube_id=w.youtube_id,
                    extra_videos=w.extra_videos, gallery=w.gallery,
                    cover_url=w.cover_url, featured_image=w.featured_image,
                    description=w.description, credits=w.credits,
                    credits_text=w.credits_text).values()))
    return (ready == len(works), bool(summary["all_live"]),
            {"work_ready": f"{ready}/{len(works)} 支作品素材齊全",
             "published": f"{summary['live']}/{summary['total']} 支已上線"})


async def _payload(session, project, *, auth) -> dict:
    """讀與寫都回這一份 —— 勾完不必再打一次 GET（同 archive.py 慣例）。

    ⚠️ 這個慣例在 archive.py 是**免費**的（那邊的 payload 純粹是兩個已經在
    記憶體裡的 JSONB 欄），在這裡要多花 2 趟查詢。仍然值得：讓前端自己算
    軌道完成數＝把 `build()` 複製到 JS，正是 `missing_facts` 在防的那種分裂。
    但下一個要抄「同 archive.py 慣例」的功能要知道帳單不一樣。

    範本與訊號的對齊由 `tests/unit/test_flow_facts_align.py` 守著（實際呼叫
    這支 `_gather_facts` 比對），不在請求路徑上重複檢查。
    """
    facts, detail, meta = (await _gather_facts(session, [project]))[project.id]
    built = pf.build(facts, project.flow_checks, detail)
    stage = pf.stage_view(project.status)
    return {
        "stage": stage,
        "tracks": built["tracks"],
        "missing": pf.missing_for(built["tracks"], stage["next"]),
        # 這三個都是「這次動作能做什麼」，不是階段的靜態屬性 —— 所以放頂層，
        # 不往 core 產的 stage dict 上加料（那樣讀 core 讀不到全貌）。
        #
        # 看得到 ≠ 勾得動 ≠ 推得動：三層不同，前端據此決定畫什麼。兩個權限
        # 旗標都從**同一個** payload 推導、各自綁住對應端點的政策常數 ——
        # 呼叫端各算各的就會出現「畫面說可以、後端回 403」。
        "can_check": payload_grants(auth, *pf.CHECK_MODULES),
        "can_advance": payload_grants(auth, *pf.ADVANCE_MODULES),
        # deep-link「去完成」：目的地 → 這個人進不進得去。權限在**這裡**算，
        # 前端不自己讀 token 解 modules（那等於把 RBAC 判定複製到 JS）。
        # 名稱不送 —— 那是導覽的字，前端 `tabLabel()` 就拿得到。
        #
        # 🔴 **門在目的地**（owner §14.4）：這個答案只決定連結畫成可點還是
        # 禁用＋「需要＿＿權限」，不是安全邊界。用 `tab_modules` 而不是目的地
        # 鍵本身 —— 提案庫／片庫本來就收不只一個模組（正本 core.auth.TAB_ACCESS
        # 就是那些閘門的參數），只認同名的話拍攝企劃的人會在自己**進得去**的
        # tab 上看到「你沒有權限」。
        "links": {k: payload_grants(auth, *tab_modules(k)) for k in pf.DESTS},
        # 這次推進**會不會真的用到**成案原因（見 wins_proposal docstring）
        "collects_outcome_reason": pf.wins_proposal(
            stage["next"], meta["proposal_count"], bool(facts.get("won"))),
    }


@router.get("/projects/flow/summary")
async def get_flow_summary(request: Request, ids: str = ""):
    """一批專案的進度摘要：階段 + 每軌 done/total。清單視圖用（§14.4）。

    **不回燈號明細**：清單上畫的是五格微型完成條，每格只要兩個數字。整包
    tracks 帶 items 是 4KB/專案，五十列就是 200KB —— 而畫面用不到其中 88%。

走 `_gather_facts` 同一支（理由見那支）。
    這條路徑**不回權限旗標**（can_check / links…）：清單不畫可互動的東西。
    """
    from routers.api_proposals import proposal_auth
    proposal_auth(request)
    raw = [i for i in (ids or "").split(",") if i.strip()]
    wanted = raw[:_SUMMARY_MAX]
    if len(wanted) < len(raw):
        # 截斷不報錯（422 會讓 loadFlowSummary 整欄放棄，比少一截更糟），
        # 但要留痕：症狀是尾端那些列永遠顯示「尚未取得進度」，而畫面上
        # 沒有任何東西說得出為什麼
        import logging
        logging.getLogger(__name__).info(
            "flow/summary 截斷：%d → %d", len(raw), _SUMMARY_MAX)
    if not wanted:
        return {"projects": {}}

    async with _crm_session() as session:
        # 只取用得到的欄位 —— `select(CrmProject)` 會把整列 ORM 實體拉出來
        # （200 筆量到 290KB / 6.9ms，比重寫過的聚合查詢本身還貴），而下面
        # 只讀這七個。`_facts_for` 吃的是屬性存取，Row 與 ORM 實體一樣用。
        projects = (await session.execute(select(
            CrmProject.id, CrmProject.name, CrmProject.client_id,
            CrmProject.status, CrmProject.website_prod_stage,
            CrmProject.archive_checklist, CrmProject.review_kpta,
            CrmProject.flow_checks,
        ).where(CrmProject.id.in_(wanted)))).all()
        gathered = await _gather_facts(session, projects)
        out = {}
        for p in projects:
            facts, detail, _meta = gathered[p.id]
            built = pf.build(facts, p.flow_checks, detail)
            # 帶 is_lost / is_terminal 而不是只給 status —— 清單的 chip 要據此
            # 上色，而「哪些狀態算終態」是 core.project_flow 的政策
            # （LOST / TERMINAL）。只給字串的話前端就得自己比對那兩個字面值，
            # 加一個終態就會靜默畫錯色。詳情面板的階段列本來就吃這兩個旗標。
            st = pf.stage_view(p.status)
            out[p.id] = {
                "status": st["status"], "is_lost": st["is_lost"],
                "is_terminal": st["is_terminal"],
                "tracks": [{"key": t["key"], "label": t["label"],
                            "done": t["done"], "total": t["total"]}
                           for t in built["tracks"]],
            }
        return {"projects": out}


@router.get("/projects/{project_id}/flow")
async def get_project_flow(project_id: str, request: Request):
    """專案工作流：階段（單線）+ 五軌進度（多方前進）+ 推進建議。"""
    from routers.api_proposals import proposal_auth
    auth = proposal_auth(request)
    return await _with_project(project_id, lambda s, p: _payload(s, p, auth=auth))


@router.post("/projects/{project_id}/flow/check")
async def check_flow_item(project_id: str, request: Request):
    """勾/取消一個手動里程碑：{item_key, checked, note}。

    自動訊號不給勾（422）—— 那些由資料決定，手動蓋過去就等於讓「資料說了算」
    這條規則失效。
    """
    auth = _check_flow_check_auth(request)
    body = await request.json()
    return await _patch_project_json(
        project_id, "flow_checks",
        lambda cur: pf.apply_check(cur, body.get("item_key"),
                                   body.get("checked"), body.get("note"),
                                   who=_username(request),
                                   when=_now().strftime("%Y-%m-%d %H:%M")),
        lambda s, p: _payload(s, p, auth=auth))


@router.post("/projects/{project_id}/flow/items")
async def add_flow_item(project_id: str, request: Request):
    """加一個自訂項到某一軌：{track, label}。

    範本那五軌是全公司通用的（§14.7 決策點 1），這支只動**這個專案**的 blob
    —— 「這個案子額外要做的事」以前無處可放，而隔壁的歸檔清單早就有自訂項。
    守衛與勾選同一道：能勾就能加自己的項目。
    """
    auth = _check_flow_check_auth(request)
    body = await request.json()
    return await _patch_project_json(
        project_id, "flow_checks",
        lambda cur: pf.add_custom(cur, body.get("track"), body.get("label"),
                                  key_seed=uuid.uuid4().hex,
                                  who=_username(request),
                                  when=_now().strftime("%Y-%m-%d %H:%M")),
        lambda s, p: _payload(s, p, auth=auth))


@router.delete("/projects/{project_id}/flow/items/{key}")
async def delete_flow_item(project_id: str, key: str, request: Request):
    """刪一個自訂項（範本項不給刪 —— 那是全公司的骨架，不是這個案子的事）。"""
    auth = _check_flow_check_auth(request)
    return await _patch_project_json(
        project_id, "flow_checks",
        lambda cur: pf.remove_custom(cur, key),
        lambda s, p: _payload(s, p, auth=auth))
