"""core/project_flow.py — 專案工作流：階段（單線）× 軌道（多方前進）的純邏輯

owner 2026-08-13/14 定調（規格正本 `docs/PROPOSAL_PLANNER.md` §14）：

- **階段**（`crm_projects.status` 八狀態）是商務主軸，**單線**：客戶分級、錢流
  口徑、結案看板都掛在上面，一個專案同時只能在一個狀態。
- **進度**是**多軌並行**的訊號：「有時候進度是多方前進」—— 還在提案階段但人已
  配置、素材已開拍是常態。所以亮燈**只看資料、不看當前階段**，軌內順序是建議
  不是鎖。
- **各自工作、資料放正確的位置**：這裡不儲存任何別處已有的東西。訊號由呼叫端
  從各自的正本撈成 `facts`，這支只負責「facts → 燈」。手動項（推不出來的那幾個）
  的值存 `crm_projects.flow_checks`，範本在這裡 —— 與 `core/proposal_survey`、
  `core/project_archive` 同一套「欄目在程式碼、值在 DB」。

無 DB、無 IO、無 ORM import —— 全部是可單元測試的純函式。
"""
from __future__ import annotations

AUTO, MANUAL = "auto", "manual"

# 燈號四態（前端據此決定畫實心/空心/勾選框/虛線）
ON, OFF, SKIP = "on", "off", "skip"

# ── 軌道範本 ───────────────────────────────────────────────────────────
# (item_key, label, kind, hint)  —— item_key 全域唯一（**不帶 stage**：項目屬於
# 軌道不屬於階段，這是「多方前進」在資料層的體現）。key 是存值/對照的鍵，不要改。
TRACKS: tuple[tuple[str, str, tuple], ...] = (
    ("biz", "商務", (
        ("client",     "客戶已建檔",     AUTO,   "專案要綁客戶才進得了分級與帳務"),
        ("quote",      "報價單已備",     AUTO,   "CRM 報價單或提案報價檔任一"),
        ("quote_won",  "報價成交",       AUTO,   "報價單狀態＝已簽核"),
        ("invoiced",   "已請款／開發票", AUTO,   "帳務有這個專案的收款發票"),
        ("settled",    "款項結清",       AUTO,   "沒有未收款的收款發票"),
    )),
    ("plan", "企劃", (
        ("proposal",   "提案已建立",     AUTO,   "提案庫有掛在這個專案的提案"),
        ("ideation",   "創意發想已開始", AUTO,   "企劃矩陣已經有人填"),
        ("brief",      "企劃書已產出",   AUTO,   "至少一版企劃書"),
        ("pitched",    "已向客戶提案",   AUTO,   "提案狀態＝已提案／入圍／成案"),
        ("won",        "成案",           AUTO,   "提案狀態＝成案"),
    )),
    ("prod", "製作", (
        ("staffed",    "人員已配置",     AUTO,   "專案派工至少一人"),
        ("shooting",   "開拍",           MANUAL, "推不出來的里程碑，人工勾"),
        ("footage_in", "素材已進影像紀錄", AUTO, "影像紀錄有檔案"),
        ("timesheet",  "工時登記中",     AUTO,   "有人登記這個專案的工時"),
        ("edited",     "剪輯完成",       MANUAL, "推不出來的里程碑，人工勾"),
        ("approved",   "客戶審批通過",   AUTO,   "看片門戶有一輪已核准"),
    )),
    ("deliver", "交付", (
        ("final",      "完稿已交付",     AUTO,   "歸檔清單的「完成檔」已收"),
        ("archived",   "歸檔清單齊備",   AUTO,   "完稿結案分頁的歸檔清單全部收齊"),
        ("work_ready", "上架素材完成度", AUTO,   "影片／圖片／說明／名單四項齊全"),
        ("published",  "官網上線",       AUTO,   "作品已發布；標記「不上官網」則略過"),
        ("retro",      "專案回顧",       AUTO,   "完稿結案分頁的 KPTA 有填"),
    )),
    # PARA 的 Resources 收割：結案前把可重用的東西升級進庫。四項全部反查得到，
    # 所以零手動 —— 沒做的收割就是流失，下一個類似案子要從頭再來一次。
    ("harvest", "收割", (
        ("h_tpl",      "企劃範本已收割", AUTO,   "這個專案的企劃書有被設為範本"),
        ("h_refs",     "參考影片已入庫", AUTO,   "片庫有引用掛在這個專案"),
        ("h_loc",      "場景已建檔",     AUTO,   "場景庫有這個專案的使用履歷"),
        ("h_footage",  "素材庫已索引",   AUTO,   "素材庫掃過這個專案的檔案"),
    )),
)

ITEMS = {ik: (tk, lb, kind, hint)
         for tk, _tl, items in TRACKS for ik, lb, kind, hint in items}
MANUAL_KEYS = tuple(k for k, v in ITEMS.items() if v[2] == MANUAL)
AUTO_KEYS = frozenset(k for k, v in ITEMS.items() if v[2] == AUTO)

MAX_NOTE = 500

# ── 自訂項（owner 2026-08-15：「裡頭的項目細節也是要可以新增刪除」）────────
#
# 範本那五軌是全公司通用的（§14.7 決策點 1：v1 一套通用，用了再分化），但
# 「這個案子額外要做的事」無處可放 —— 而隔壁的歸檔清單早就有「＋自訂項」，
# 同一種東西兩種待遇。所以自訂項只加在**專案自己的** flow_checks blob 裡，
# 範本不動。
#
# 🔴 放在 blob 的保留鍵底下而不是跟手動項平鋪：平鋪的話一個自訂項只要取名
# 撞到未來的範本 key（例如有人加了叫 "shooting" 的自訂項，而範本後來也長出
# 同名項），兩者就會互相覆蓋。分開放，命名空間永遠不會撞。
CUSTOM_BUCKET = "_custom"
MAX_CUSTOM = 20            # 每案上限：清單是拿來看的，不是拿來當待辦系統
MAX_LABEL = 60
TRACK_KEYS = frozenset(tk for tk, _lb, _items in TRACKS)

# ── deep-link「去完成」目的地（規格 §14.4）──────────────────────────────
# 未亮的燈要能帶人去做那件事，不然它只是一句抱怨。
#
# **目的地就是一個 tab，而 tab 的鍵就是模組鍵** —— RBAC v2 的 `modules[]` 每個
# 值都對應一個 tab（`frontend/js/shared/tab-config.js` 的 TAB_MAP 就是這樣鍵
# 的）。所以這裡只放鍵，前端用既有的 TAB_MAP 換 section id、`tabLabel()` 換
# 名字，不必再維護第二張「目的地 → 網址／中文名」的表。
#
# item_key → 目的地。**每個 AUTO 項都要有一個**（test_project_flow 守著）：
# 沒有去處的燈等於叫人自己去找，而這功能的價值正是「不用找」。
# 手動項不列 —— 它們就在這頁勾，不需要去別的地方。
ITEM_DEST: dict[str, str] = {
    "client": "crm_projects", "quote": "crm_quotes", "quote_won": "crm_quotes",
    "invoiced": "crm_invoices", "settled": "crm_invoices",
    "proposal": "preprod_proposals", "ideation": "preprod_proposals",
    "brief": "preprod_proposals", "pitched": "preprod_proposals",
    "won": "preprod_proposals",
    "staffed": "crm_projects", "footage_in": "media_log",
    "timesheet": "timesheets", "approved": "portal",
    # 交付軌五項全在專案詳情的「完稿結案」分頁
    "final": "crm_projects", "archived": "crm_projects",
    "work_ready": "crm_projects", "published": "crm_projects",
    "retro": "crm_projects",
    "h_tpl": "preprod_proposals", "h_refs": "references",
    "h_loc": "preprod_locations", "h_footage": "footage",
}

# 用得到的目的地（payload 要逐個算權限）。**推導**而不是手寫第二張表。
#
# 🔴 「誰進得去這個目的地」不在這裡答 —— 那是 `core.auth.tab_modules`
# （tab 閘門本身的參數）。這裡只說「去哪」，不說「誰能去」。
DESTS: frozenset = frozenset(ITEM_DEST.values())

# 誰可以勾手動里程碑（owner 2026-08-14 拍板走模組級）。守衛與「畫不畫
# checkbox」兩處共用這一份 —— 分兩處寫的話，總有一天畫面說可以、後端回 403。
CHECK_MODULES: tuple[str, ...] = ("crm_projects",)

# 誰可以推進階段（啟動專案、改階段）。
#
# owner 2026-09-08 拍板（docs/RBAC_PLAN.md §5 第 2 點）：**開給 `crm_projects`**，
# 跟建／改／刪專案、勾里程碑同一把 —— 有專案管理鑰匙的人看得到「啟動專案」
# 按鈕，按下去就該過（權限稽核第二批「看得到做得到」）。在此之前是空 tuple
# ＝ 只有管理員（`check_admin_or_module` 零 key 時等同 `check_admin`）。
#
# 🔴 為什麼要有這個常數，而不是兩邊各寫死：推進的守衛在
# `routers/crm/projects.py::update_project_status`（PUT 整包送 status 也走
# 同一道門）與 `routers/api_crm_mobile.py`（簽回順便啟動），而「畫不畫按鈕」在
# `routers/crm/flow.py::_payload` —— 三處共用這一份，改政策只改這一行；
# 分開寫的話總有一天畫面說可以、後端回 403（或反過來：端點開了、按鈕還是灰的）。
ADVANCE_MODULES: tuple[str, ...] = ("crm_projects",)

# 進到這些階段＝這個案子拿到了（衛星提案記「成案」）。
# 正本在這裡（純模組、無 IO）—— 原本住在 routers 裡，害 core 的純邏輯只能
# 用字面值再鏡射一份。
WIN_STATUSES = frozenset({"製作", "結案", "歸檔"})


def wins_proposal(new_status: str, proposal_count: int, already_won: bool) -> bool:
    """推進到 new_status 會不會把那筆衛星提案標成案？

    也就是「這次推進**用不用得到**成案原因」。三個條件缺一不可：

    - 進到 win 階段
    - **恰好一筆**衛星提案 —— 一個專案可以並行多筆（同一案提三個 concept），
      多筆時誰贏由專案負責人在企劃分頁指定，不自動標
    - 那筆還不是「成案」

    🔴 兩個呼叫端共用這一份：`routers/crm/projects._sync_linked_proposals`
    （真的去標）與 `routers/crm/flow._payload`（決定推進對話框問不問原因）。
    分兩處寫的話，「對話框問了、後端沒用」就是使用者打完字被靜默丟掉 ——
    那正是 2026-08-14 第一輪 /simplify 抓到的缺陷，只是當時只統一了
    WIN_STATUSES 這一半。
    """
    return bool(new_status in WIN_STATUSES and proposal_count == 1 and not already_won)

# ── 階段（商務主軸，單線）──────────────────────────────────────────────
# 與 routers/crm/projects.py 的狀態白名單同一組字面值；順序＝管線順序。
# 「投標/開發/洽詢」是三種可能的**起點**（不是要依序走完），所以推進時
# 由 NEXT_STATUS 直接跳到「提案」。
PIPELINE = ("投標", "開發", "洽詢", "提案", "製作", "結案", "歸檔")
LOST = "未成案"
# 報價時順手建的殼專案落在這一階（案子還沒成立，但已經在報價了）——
# 桌機報價彈窗的 inline 建案與手機「開報價單」都用它，別各寫各的。
QUOTE_PHASE = "提案"
# 專案選單的「已結案」那一組（工作日誌、零用金的浮層都吃這個旗標）：結案之後的階段＋未成案
CLOSED_STATUSES = ("結案", "歸檔", LOST)


def is_closed(status) -> bool:
    """專案選單分組：True＝收進「已結案」那段（預設不展開）。"""
    return (status or "").strip() in CLOSED_STATUSES
TERMINAL = (LOST, "歸檔")

NEXT_STATUS = {
    "投標": "提案", "開發": "提案", "洽詢": "提案",
    "提案": "製作", "製作": "結案", "結案": "歸檔",
}

# 推進到某階段時「建議先完成」的項目（**跨軌**引用 —— 不是「某階段的清單」，
# 那會把階段與進度綁回去）。缺了只軟擋：列出來、確認後仍可推。
# 硬守衛（成案/未成案原因必填等）留在既有端點，這裡不重複實作。
GATES = {
    "製作": ("won", "quote_won"),
    "結案": ("approved", "settled"),
    "歸檔": ("final", "archived", "invoiced", "retro", "settled"),
}

# 列在缺項裡、但標成「提醒」而非阻擋 —— owner 決策點 5：實務上結案常先於收款，
# 不能因為錢還沒到就卡住流程。（放進 GATES 一起走，不另開第二個迴圈。）
ADVISORY = frozenset({"settled"})


def _norm_manual(stored) -> dict:
    """DB 的 flow_checks JSONB → 乾淨 dict（壞資料/舊 key 一律丟掉）。"""
    src = stored if isinstance(stored, dict) else {}
    out = {}
    for key in MANUAL_KEYS:
        row = src.get(key)
        if not isinstance(row, dict):
            continue
        out[key] = {
            "checked": bool(row.get("checked")),
            "by": str(row.get("by") or "")[:64],
            "at": str(row.get("at") or "")[:40],
            "note": str(row.get("note") or "")[:MAX_NOTE],
        }
    return out


def _norm_custom(stored) -> dict:
    """blob 裡的自訂項 → 乾淨 dict（壞資料、未知軌道一律丟掉）。"""
    src = (stored or {}).get(CUSTOM_BUCKET) if isinstance(stored, dict) else None
    out = {}
    for key, row in (src or {}).items() if isinstance(src, dict) else ():
        if not isinstance(row, dict) or row.get("track") not in TRACK_KEYS:
            continue
        label = str(row.get("label") or "").strip()[:MAX_LABEL]
        if not label:
            continue
        out[str(key)[:40]] = {
            "track": row["track"], "label": label,
            "checked": bool(row.get("checked")),
            "by": str(row.get("by") or "")[:64],
            "at": str(row.get("at") or "")[:40],
            "note": str(row.get("note") or "")[:MAX_NOTE],
        }
    return out


def _rebuild(stored, manual=None, custom=None) -> dict:
    """把手動項與自訂項寫回同一個 blob（兩者都要保住，改一邊不能弄丟另一邊）。"""
    out = dict(_norm_manual(stored) if manual is None else manual)
    cur_custom = _norm_custom(stored) if custom is None else custom
    if cur_custom:
        out[CUSTOM_BUCKET] = cur_custom
    return out


def apply_check(stored, key: str, checked, note, *, who: str, when: str):
    """手動項或自訂項單筆寫入 → (新的 dict, 錯誤訊息)。錯誤時 stored 原樣退回。"""
    text = "" if note is None else str(note)
    if len(text) > MAX_NOTE:
        return stored, f"備註超過 {MAX_NOTE} 字上限"
    custom = _norm_custom(stored)
    if key in custom:
        custom[key].update(checked=bool(checked), by=who, at=when, note=text)
        return _rebuild(stored, custom=custom), ""
    if key not in ITEMS:
        return stored, "未知的項目"
    if ITEMS[key][2] != MANUAL:
        return stored, "這是自動訊號，不能手動勾（它由資料決定）"
    manual = _norm_manual(stored)
    manual[key] = {"checked": bool(checked), "by": who, "at": when, "note": text}
    return _rebuild(stored, manual=manual), ""


def add_custom(stored, track: str, label, *, key_seed: str, who: str, when: str):
    """加一個自訂項到某一軌 → (新的 dict, 錯誤訊息)。

    `key_seed` 由呼叫端給（uuid），與範本 key 不同命名空間 —— 見 CUSTOM_BUCKET。
    """
    if track not in TRACK_KEYS:
        return stored, "未知的軌道"
    text = str(label or "").strip()
    if not text:
        return stored, "請填項目名稱"
    if len(text) > MAX_LABEL:
        return stored, f"項目名稱超過 {MAX_LABEL} 字上限"
    custom = _norm_custom(stored)
    if len(custom) >= MAX_CUSTOM:
        return stored, f"自訂項已達上限 {MAX_CUSTOM} 個"
    custom[f"c_{key_seed[:16]}"] = {
        "track": track, "label": text, "checked": False,
        "by": who, "at": when, "note": "",
    }
    return _rebuild(stored, custom=custom), ""


def remove_custom(stored, key: str):
    """刪一個自訂項 → (新的 dict, 錯誤訊息)。範本項不給刪（那是全公司的）。"""
    custom = _norm_custom(stored)
    if key not in custom:
        return stored, ("範本項目不能刪除" if key in ITEMS else "找不到這個自訂項")
    custom.pop(key)
    return _rebuild(stored, custom=custom), ""


def missing_facts(facts: dict) -> tuple:
    """範本宣告了、但呼叫端沒給的自動訊號 —— 空 tuple ＝ 兩邊對齊。

    🔴 為什麼需要這支：軌道範本（這裡）與訊號怎麼算（router）是分開的，靠
    item_key 字串接。漏一個或打錯字的話 `build()` 會把它畫成「未完成」——
    **永遠不會亮，而且沒有任何錯誤**。這是這個設計唯一的靜默失效點。

    呼叫點在 `tests/unit/test_flow_facts_align.py` —— 它用假 session 真的跑
    一次 `routers/crm/flow._gather_facts`，比對它**實際**產出的鍵。刻意不放在
    請求路徑上：那只能把「一盞燈不亮」變成「整片 500」，而且是在使用者面前、
    付完 DB 代價之後才發現；放 CI 則是還沒上線就攔下來。
    """
    return tuple(sorted(AUTO_KEYS - set(facts or ())))


def build(facts: dict, stored_manual=None, detail: dict = None) -> dict:
    """facts（呼叫端從各正本撈的訊號）+ 手動勾選 → 完整工作流狀態。

    facts 的鍵 = AUTO 項的 item_key，值是 bool 或 None。**None ＝「略過」**
    （例如標記「不上官網」的專案，`published` 不該顯示成未完成）。
    缺鍵一律當 False —— 訊號撈不到時寧可顯示未完成，不要謊報完成。
    detail：item_key → 「為什麼亮」的說明字串（燈要能自己解釋）。
    """
    facts = facts or {}
    detail_map = detail or {}
    manual = _norm_manual(stored_manual)
    custom = _norm_custom(stored_manual)

    tracks = []
    for tk, tlabel, items in TRACKS:
        rows = []
        for ik, label, kind, hint in items:
            row = {"key": ik, "label": label, "kind": kind, "hint": hint}
            if kind == MANUAL:
                m = manual.get(ik) or {}
                row["state"] = ON if m.get("checked") else OFF
                row.update(by=m.get("by", ""), at=m.get("at", ""),
                           note=m.get("note", ""))
            else:
                # 缺鍵 → False（訊號撈不到就顯示未完成，寧可低報也不謊報交付）；
                # 明確 None → 呼叫端說這項不適用（略過）。`.get(ik, False)` 正好
                # 把這兩者分開 —— 用 `.get(ik)` 會混成同一種。
                val = facts.get(ik, False)
                row["state"] = SKIP if val is None else (ON if val else OFF)
                if detail_map.get(ik):
                    row["detail"] = str(detail_map[ik])[:200]
                # 只有**未亮**的燈帶去處：亮了的沒事要做，略過的更不用去。
                # 判斷放在產 payload 的地方，前端就不會有第二份 state 判斷。
                if row["state"] == OFF:
                    row["dest"] = ITEM_DEST[ik]
            rows.append(row)
        # 自訂項排在範本項之後 —— 範本是骨架，這個案子額外要做的事跟在後面。
        # `custom=True` 讓前端知道哪幾列可以刪（範本項不給刪）。
        for ck, c in custom.items():
            if c["track"] != tk:
                continue
            rows.append({"key": ck, "label": c["label"], "kind": MANUAL,
                         "hint": "這個專案的自訂項", "custom": True,
                         "state": ON if c["checked"] else OFF,
                         "by": c["by"], "at": c["at"], "note": c["note"]})
        tracks.append({
            "key": tk, "label": tlabel, "items": rows,
            "done": sum(1 for r in rows if r["state"] == ON),
            "total": sum(1 for r in rows if r["state"] != SKIP),
        })
    return {"tracks": tracks}


def missing_for(tracks: list, target_status: str) -> dict:
    """推進到 target_status 前「建議先完成」但還沒完成的項目。

    SKIP 不算缺（不上官網的案子不該卡在「官網上線」）。

    **回 `{blocking, advisory}` 兩袋而不是一袋帶旗標**：前端有兩個地方要用
    （缺項橫幅、推進確認框），各自 filter 一次就是同一段判斷寫兩份 ——
    而 advisory 的語意（owner 決策點 5：款項未結清只提醒不擋）住在這裡，
    不該讓 JS 端重新詮釋。
    """
    state = {r["key"]: r["state"] for t in (tracks or []) for r in t["items"]}
    out = {"blocking": [], "advisory": []}
    for k in GATES.get(target_status, ()):
        if state.get(k) != OFF:
            continue
        bag = "advisory" if k in ADVISORY else "blocking"
        # dest 的鍵名與 build() 產的燈號列一致 —— 前端一支 `_dest()` 兩處通用。
        # 直接索引不給預設：閘門引用手動項（沒有去處）時要當場 KeyError，
        # 不要靜默給一個永遠畫不出連結的空字串。
        out[bag].append({"key": k, "label": ITEMS[k][1], "track": ITEMS[k][0],
                         "dest": ITEM_DEST[k]})
    return out


def stage_view(status: str) -> dict:
    """階段列：管線各站 + 目前位置 + 下一站。未成案＝岔出的終態。"""
    cur = status or ""
    return {
        "status": cur,
        "pipeline": list(PIPELINE),
        "is_lost": cur == LOST,
        "is_terminal": cur in TERMINAL,
        "index": PIPELINE.index(cur) if cur in PIPELINE else -1,
        "next": NEXT_STATUS.get(cur, ""),
    }
