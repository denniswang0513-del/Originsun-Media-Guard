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

# 誰可以勾手動里程碑（owner 2026-08-14 拍板走模組級）。守衛與「畫不畫
# checkbox」兩處共用這一份 —— 分兩處寫的話，總有一天畫面說可以、後端回 403。
CHECK_MODULES = ("crm_projects",)

# ── 階段（商務主軸，單線）──────────────────────────────────────────────
# 與 routers/crm/projects.py 的狀態白名單同一組字面值；順序＝管線順序。
# 「投標/開發/洽詢」是三種可能的**起點**（不是要依序走完），所以推進時
# 由 NEXT_STATUS 直接跳到「提案」。
PIPELINE = ("投標", "開發", "洽詢", "提案", "製作", "結案", "歸檔")
LOST = "未成案"
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


def apply_check(stored, key: str, checked, note, *, who: str, when: str):
    """手動項單筆寫入 → (新的 dict, 錯誤訊息)。錯誤時 stored 原樣退回。"""
    if key not in ITEMS:
        return stored, "未知的項目"
    if ITEMS[key][2] != MANUAL:
        return stored, "這是自動訊號，不能手動勾（它由資料決定）"
    text = "" if note is None else str(note)
    if len(text) > MAX_NOTE:
        return stored, f"備註超過 {MAX_NOTE} 字上限"
    cur = _norm_manual(stored)
    cur[key] = {"checked": bool(checked), "by": who, "at": when, "note": text}
    return cur, ""


def missing_facts(facts: dict) -> tuple:
    """範本宣告了、但呼叫端沒給的自動訊號 —— 空 tuple ＝ 兩邊對齊。

    🔴 為什麼需要這支：軌道範本（這裡）與訊號怎麼算（router）是分開的，靠
    item_key 字串接。漏一個或打錯字的話 `build()` 會把它畫成「未完成」——
    **永遠不會亮，而且沒有任何錯誤**。這是這個設計唯一的靜默失效點，
    所以把它變成可執行的斷言（單元測試 + router 啟動時檢查）。
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
            rows.append(row)
        tracks.append({
            "key": tk, "label": tlabel, "items": rows,
            "done": sum(1 for r in rows if r["state"] == ON),
            "total": sum(1 for r in rows if r["state"] != SKIP),
        })
    return {"tracks": tracks}


def missing_for(tracks: list, target_status: str) -> list[dict]:
    """推進到 target_status 前「建議先完成」但還沒完成的項目。

    SKIP 不算缺（不上官網的案子不該卡在「官網上線」）。ADVISORY 的項目照列
    但標 advisory=True —— 讓人看見，但語氣是提醒不是阻擋。
    """
    state = {r["key"]: r["state"] for t in (tracks or []) for r in t["items"]}
    return [{"key": k, "label": ITEMS[k][1], "track": ITEMS[k][0],
             "advisory": k in ADVISORY}
            for k in GATES.get(target_status, ()) if state.get(k) == OFF]


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
