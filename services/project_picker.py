"""services/project_picker.py — 「選一個專案」那份清單的規則（工時補登與備份頁共用的**同一份**）。

owner 2026-09-11：備份頁的「綁定專案」要跟專案工時的專案清單同一種列法，而且
**兩邊都不篩狀態**（原句：「不篩，連專案工時也不篩」）。備份最常發生在案子結束
之後（歸檔、補檔、客戶回頭要素材），用 `status in ("製作","結案")` 篩掉的結果是
「真的要用的時候剛好選不到」。分組交給 `core.project_flow.is_closed` 的 `closed`
旗標：進行中（製作／提案）預設展開、已結案（結案／歸檔／未成案）收著，打字照樣搜得到。

規則四條，兩邊共用：
  1. 全部的案都列（**不看狀態**）
  2. 已連結的母私帳只列一個 —— 留私帳那筆（工時對映只認私帳）
  3. 最近有動的排前面（最後一筆工時的日期 vs 專案 `updated_at`，取較新者）
  4. `label`＝「年份 客戶 案名」、`closed`＝`is_closed(status)`

為什麼規則住在這裡、而不是讓 `routers/api_backup` 直接 import `timesheet_manual`：
那支拖著 `services.timesheet_ingest`／`timesheet_lookup` 一整串，而 `api_backup`
跑在全部 10 台 agent 上（精簡安裝、DB 還可能是斷的）—— 把備份耦合到工時沒有道理。
"""
from __future__ import annotations

from sqlalchemy import func as safunc, select

from core.hr_logic import tw_day
from core.project_flow import is_closed


async def list_options(session, *, extra: tuple = ()) -> list:
    """→ `[{id, name, client, year, closed, label, **extra}]`，依檔頭那四條規則。

    `extra`＝要一起帶出來的 `crm_projects` 欄位名（原值不加工，呼叫端自己翻譯）。
    🔴 **不要拿它帶金額欄**：備份頁那支端點守的是 `check_lan_or_logged_in`
    （備檔電腦沒人登入），它可以連私帳案一起列出的前提就是「這支不帶錢」——
    見 `routers/api_backup` 那段註解與 `test_money_never_leaks`。
    """
    from db.models import Client, CrmProject, Timesheet
    base = [CrmProject.id, CrmProject.name, Client.short_name, CrmProject.start_date,
            CrmProject.shoot_date, CrmProject.created_at, CrmProject.status,
            CrmProject.entity, CrmProject.mine_link_id, CrmProject.source_project_id,
            CrmProject.updated_at]
    rows = (await session.execute(
        select(*base, *[getattr(CrmProject, c) for c in extra])
        .outerjoin(Client, Client.id == CrmProject.client_id)
        .order_by(CrmProject.name)
    )).all()

    # 最近有更新的排前面（owner 2026-09-06）：最近一筆工時的日期 vs 專案本身 updated_at，取較新者
    ids = [r[0] for r in rows]
    last_ts = {pid: d for pid, d in (await session.execute(
        select(Timesheet.project_id, safunc.max(Timesheet.work_date))
        .where(Timesheet.project_id.in_(ids)).group_by(Timesheet.project_id))).all()} if ids else {}
    touched = {r[0]: max((d for d in (tw_day(last_ts.get(r[0])), tw_day(r[10])) if d), default=None) for r in rows}
    rows = sorted(rows, key=lambda r: (touched[r[0]] is None,
                                       -(touched[r[0]].toordinal() if touched[r[0]] else 0), r[1] or ""))

    # 已連結的母私帳只列一個（owner 2026-09-06：同名出現兩次）。工時對映只認私帳，所以**預設留私帳那筆**；
    # 連結兩種形狀都認（母帳 mine_link_id／私帳 source_project_id），兩邊各掃一次算出來的是同一對、同一個決定。
    #
    # 🔴 例外：呼叫端點名了 `extra` 欄位（＝備份三根）而**只有母帳那筆真的有值**時留母帳。
    # 備份三根的正本在專案上，而設定的人多半只看得到母帳那筆（私帳案對沒有 finance_mine 的人
    # 整個不可見），所以值幾乎都落在母帳。一律留私帳的話，備份頁只選得到那個空殼分身：
    # 畫面說「這個案還沒設定備份資料夾」、`core.worker._apply_project_roots` 用那個 id 反查也讀不到，
    # 於是靜默退回手動填的路徑 —— 綁定對這個案等於失效，而且沒有任何徵兆。
    by_id = {r[0]: r for r in rows}
    n_base = len(base)

    def _has_extra(row) -> bool:
        return any(str(v or "").strip() for v in row[n_base:])

    shadowed = set()
    for r in rows:
        parent, mine = (r[0], r[8]) if r[7] != "mine" else (r[9], r[0])
        if parent == mine or parent not in by_id or mine not in by_id:
            continue
        keep_parent = bool(extra) and _has_extra(by_id[parent]) and not _has_extra(by_id[mine])
        shadowed.add(mine if keep_parent else parent)

    opts = []
    for r in rows:
        if r[0] in shadowed:
            continue
        d = r[3] or r[4] or r[5]
        year = str(d.year) if d else ""
        client, st = r[2] or "", r[6]
        # label＝「年份 客戶 案名」（owner 2026-09-03：跟零用金一樣的呈現）；前端用它當下拉的字，存的時候對回 id
        # closed：前端把清單分「進行中（預設）／已結案（收著）」——分組規則住這裡，前端只看旗標
        opt = {"id": r[0], "name": r[1] or "", "client": client, "year": year,
               "closed": is_closed(st),
               "label": " ".join(x for x in (year, client, r[1] or "") if x)}
        opt.update(dict(zip(extra, r[n_base:])))
        opts.append(opt)
    return opts
