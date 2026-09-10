"""services/project_picker.py — 「選一個專案」那份清單的規則（工時補登與備份頁共用的**同一份**）。

owner 2026-09-11：備份頁的「綁定專案」要跟專案工時的專案清單同一種列法，而且
**兩邊都不篩狀態**（原句：「不篩，連專案工時也不篩」）。備份最常發生在案子結束
之後（歸檔、補檔、客戶回頭要素材），用 `status in ("製作","結案")` 篩掉的結果是
「真的要用的時候剛好選不到」。分組交給 `core.project_flow.is_closed` 的 `closed`
旗標：進行中（製作／提案）預設展開、已結案（結案／歸檔／未成案）收著，打字照樣搜得到。

規則四條，兩邊共用：
  1. 全部的案都列（**不看狀態**）
  2. 已連結的母私帳只列一個 —— 預設留私帳那筆（工時對映只認私帳）；`prefer="parent"` 見下
  3. 最近有動的排前面（最後一筆工時的日期 vs 專案 `updated_at`，取較新者）
  4. `label`＝「年份 客戶 案名」、`closed`＝`is_closed(status)`

為什麼規則住在這裡、而不是讓 `routers/api_backup` 直接 import `timesheet_manual`：
那支拖著 `services.timesheet_ingest`／`timesheet_lookup` 一整串，而 `api_backup`
跑在全部 10 台 agent 上（精簡安裝、DB 還可能是斷的）—— 把備份耦合到工時沒有道理。

🔴 這支**列出所有專案（含私帳案）**，不對私帳可見性表態 —— 兩個呼叫端各自負責：
工時的對映本來就只認私帳；備份頁是 owner 2026-09-10 明確拍板的局部例外（前提是
那支端點不帶錢）。`tests/unit/test_money_visibility` 的 EXEMPT 有這一條的理由。
"""
from __future__ import annotations

from sqlalchemy import func as safunc, select

from core.hr_logic import tw_day
from core.ledger import MINE
from core.project_flow import is_closed


async def list_options(session, *, extra: tuple = (), prefer: str = MINE) -> list:
    """→ `[{id, name, client, year, closed, label, **extra}]`，依檔頭那四條規則。

    `extra`＝要一起帶出來的 `crm_projects` 欄位名（原值不加工，呼叫端自己翻譯）。
    🔴 **不要拿它帶金額欄**：備份頁那支端點守的是 `check_lan_or_logged_in`
    （備檔電腦沒人登入），它可以連私帳案一起列出的前提就是「這支不帶錢」——
    見 `routers/api_backup` 那段註解與 `test_money_never_leaks`。

    `prefer`＝母私帳連結成對時**留哪一本帳**（"mine" 預設／"parent"）。它跟 `extra`
    是兩件事，所以是兩個參數：曾經寫成「`extra` 非空就留母帳」，那等於把「目前只有
    備份頁會傳 extra」這個外部知識編進規則裡 —— 下一個呼叫端（例如帶 `folder_path`
    的第三個 picker）會靜默拿到備份頁那套偏好。
    """
    if prefer not in (MINE, "parent"):
        # 靜默降級的話症狀正是這些測試在防的那個：備份頁綁到看不見的私帳分身、
        # 畫面說「還沒設定備份資料夾」、`_apply_project_roots` 反查不到就用手打的路徑。
        raise ValueError(f'prefer 只收 "{MINE}" 或 "parent"，收到 {prefer!r}')
    from db.models import Client, CrmProject, Timesheet
    cols = ["id", "name", "start_date", "shoot_date", "created_at",
            "status", "entity", "mine_link_id", "source_project_id", "updated_at"]
    rows = (await session.execute(
        select(*[getattr(CrmProject, c) for c in cols],
               # 客戶欄（owner 2026-09-06）：代稱優先、沒有才全稱（同 timesheet_lookup 那份）
               safunc.coalesce(safunc.nullif(Client.short_name, ""), Client.full_name, "").label("client"),
               *[getattr(CrmProject, c) for c in extra])
        .outerjoin(Client, Client.id == CrmProject.client_id)
        .order_by(CrmProject.name)
    )).all()

    # 最近有更新的排前面（owner 2026-09-06）：最近一筆工時的日期 vs 專案本身 updated_at，取較新者。
    # 不必 `WHERE project_id IN (...)`：多出來的 group（指向已刪專案的舊工時）是永遠沒人 get 的
    # 死鍵，卻要為此每次請求多送 600+ 個 uuid 的 bind param，還會讓 asyncpg 的 statement cache
    # 隨專案數多一條。
    last_ts = {pid: d for pid, d in (await session.execute(
        select(Timesheet.project_id, safunc.max(Timesheet.work_date))
        .group_by(Timesheet.project_id))).all()}
    touched = {r.id: max((d for d in (tw_day(last_ts.get(r.id)), tw_day(r.updated_at)) if d), default=None)
               for r in rows}
    rows = sorted(rows, key=lambda r: (touched[r.id] is None,
                                       -(touched[r.id].toordinal() if touched[r.id] else 0), r.name or ""))

    # 已連結的母私帳只列一個（owner 2026-09-06：同名出現兩次）。連結兩種形狀都認
    # （母帳 mine_link_id／私帳 source_project_id），成對的兩列各掃一次算出的是同一個決定。
    #
    # 🔴 `prefer="parent"`（備份頁）：留母帳，除非私帳那筆自己在 `extra` 欄位上有值。
    # 母私帳是同一個案的兩本帳，而備份三根是實體資料夾 —— 兩邊本來就該是同一組。決定留哪一筆
    # 只看「哪一筆的值有人維護得到」：母帳那筆全公司都開得起來，私帳那筆對沒有 finance_mine
    # 的人整個不可見（連專案頁都進不去）。
    #   · 母帳有值、私帳沒有 → 留母帳。否則備份頁只選得到空殼分身：畫面說「這個案還沒設定
    #     備份資料夾」、`core.worker._apply_project_roots` 用那個 id 反查也讀不到，於是靜默
    #     退回手動填的路徑 —— 綁定對這個案等於失效，而且沒有任何徵兆。
    #   · **兩邊都還沒設 → 也留母帳**（2026-09-11 /polish：這是目前生產每一個案的狀態）。
    #     留私帳的話，第一次按「儲存到專案」就把三根寫進那筆沒人看得到的，然後母帳的專案頁
    #     永遠顯示「還沒設定」、有人在那邊補填的值會被這裡靜默忽略（私帳一有值就換它勝出）。
    #   · 私帳自己有值 → 留私帳（有人真的在那邊設過，不要把它蓋掉）。
    by_id = {r.id: r for r in rows}

    def _has_extra(row) -> bool:
        return any(str(getattr(row, c) or "").strip() for c in extra)

    shadowed = set()
    for r in rows:
        parent, mine = (r.id, r.mine_link_id) if r.entity != MINE else (r.source_project_id, r.id)
        if parent == mine or parent not in by_id or mine not in by_id:
            continue
        keep_parent = prefer == "parent" and not _has_extra(by_id[mine])
        shadowed.add(mine if keep_parent else parent)

    opts = []
    for r in rows:
        if r.id in shadowed:
            continue
        d = r.start_date or r.shoot_date or r.created_at
        year = str(tw_day(d).year) if d else ""     # timestamptz 一律過台北日（面值 .year 在 +08 會差一天）
        client, st = r.client or "", r.status
        # label＝「年份 客戶 案名」（owner 2026-09-03：跟零用金一樣的呈現）；前端用它當下拉的字，存的時候對回 id
        # closed：前端把清單分「進行中（預設）／已結案（收著）」——分組規則住這裡，前端只看旗標
        opt = {"id": r.id, "name": r.name or "", "client": client, "year": year,
               "closed": is_closed(st),
               "label": " ".join(x for x in (year, client, r.name or "") if x)}
        opt.update({c: getattr(r, c) for c in extra})
        opts.append(opt)
    return opts
