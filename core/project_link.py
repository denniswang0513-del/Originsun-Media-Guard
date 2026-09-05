"""core/project_link.py — 「哪些會計項目的錢可以掛到專案上」的單一正本。

規則本身只有一句：**這筆錢屬於某個特定案子，才可以連結專案。** 行政／設備耗材／
業務推廣那種公司層級支出掛上去，專案毛利就會多算一筆不屬於它的錢。

但三個入口的答案不一樣，而且**是刻意的**（不是漂移，不要「順手統一」）：

    零用金   只有「專案雜支」 —— owner 2026-08-17 明確拍板：「只有勾專案雜支時，
             那筆才需要連結專案；其餘的項目不開放連結」。零用金的項目清單裡
             **有**「專案外包」，是刻意排除的，不是漏掉。
    請款單   專案外包 + 專案雜支 —— 請款單本來就是付給外包/現場的錢。
    收支明細 專案 + 專案雜支 + 專案外包 —— 這裡還會出現專案收入（category='專案'）。

以前這三份分別寫死在 routers/crm/petty.py、frontend/tabs/crm/crm-payments.js、
frontend/tabs/crm/crm-cashbook.js —— 同一個問題三個地方三種答案，而且前端那兩份
改了要發版。集中在這裡之後，差異看得見、要調整只有一個檔案要動。
"""
from __future__ import annotations

# 零用金（routers/crm/petty.py 以此為不變式強制，前端經 /petty/options 取用）
PETTY_ITEMS = ("專案雜支",)

# 請款單
PAYMENT_CATEGORIES = ("專案外包", "專案雜支")

# 收支明細（前端經 /crm/cash-entries/options 取用，後端寫入時強制）
#
# 🔴 「發票代開」是 owner 2026-09-05 補的，**翻掉 09-04 的決定**（原本是
# 「案掛在發票上、不掛在代開的收支列」）。實際用起來的後果：快樂學游泳那案
# 客戶匯進來的 $161,700 記在一列 category=「發票代開」的收支上，那列存不進
# 專案 → 專案頁「客戶已匯 $0」，而錢明明收到了。
# 掛在發票上那條路沒有退場（整筆請款仍走 crm-cashbook 的 _cashKaiRemit），
# 兩條並存：發票那條管「這張票是哪幾個案的」，收支這條管「這筆錢是哪個案的」。
# 不會污染專案毛利：預算結算的成本吃 crm_project_expenses ＋ 成本行，不是收支列；
# 逐案損益的「掛帳支出」只加 expense 那一側，收入側不進成本。
CASH_CATEGORIES = ("專案", "專案雜支", "專案外包", "發票代開")

# 私帳（entity='mine'）的規則是**前綴**不是清單：公司線類別（公司_專案／
# 公司_專案支出／公司_代墊…）可掛專案，個人/家用掛上去會污染毛利 —— 與母公司
# 的白名單同一個道理，只是詞彙不同。實證：匯入回掛的 612 筆全部「公司」開頭。
MINE_CASH_LINK_PREFIX = "公司"


def cash_can_link(entity: str, category: str) -> bool:
    """這一筆收支（依其帳本）能不能掛專案 —— 規則正本，寫入守衛與下拉都走這裡。"""
    if (entity or "parent") == "mine":
        return (category or "").startswith(MINE_CASH_LINK_PREFIX)
    return (category or "") in CASH_CATEGORIES


def linkable_categories(entity: str, categories=()) -> list:
    """這本帳裡「收得下專案」的類別清單 —— 給下拉／預覽用。

    🔴 兩個產生點（收支明細的 /cash-entries/options、對帳單匯入的預覽）本來
    各自 `if ent == "mine"` 分支：一個手刻 `c.startswith(MINE_CASH_LINK_PREFIX)`
    （等於把規則抄到 router 裡）、一個直接回白名單常數繞過 `cash_can_link`。
    正本函式存在、每個新使用者卻還要自己決定何時繞過它，那就不是正本。
    這一支把「怎麼分支」也收進來：母公司沒有樹、值域就是白名單本身。
    """
    src = categories if (entity or "parent") == "mine" else CASH_CATEGORIES
    return [c for c in src if cash_can_link(entity, c)]


def invoice_project_ids(project_id, project_ids) -> list:
    """發票掛的案，**可複數**（owner 2026-09-04「連結的專案可以複數；複數專案內部代開就開複數張請款單」）。
    `project_ids` 是 JSON 清單（欄位 crm_invoices.project_ids），`project_id` 永遠＝第一個
    （舊碼、SQL JOIN、代開應匯單的 project_id 都只認它）。讀法只有這一份。"""
    import json
    try:
        ids = json.loads(project_ids or "[]")
    except (TypeError, ValueError):
        ids = []
    ids = [i for i in ids if isinstance(i, str) and i]
    if project_id and project_id not in ids:
        ids.insert(0, project_id)
    return list(dict.fromkeys(ids))


def normalize_invoice_projects(data: dict, existing_ids=None) -> None:
    """payload → `project_id`（第一個）＋ `project_ids`（JSON；只有一個以下＝None）。
    沒送 project_ids（發票分頁的單案表單）＝保留既有清單，但表單那欄換成清單外的案時清單重來。"""
    import json
    sent = data.get("project_ids")
    pid = data.get("project_id") or None
    if sent is None:
        ids = list(existing_ids or [])
        if not pid:
            ids = []
        elif pid not in ids:
            ids = [pid]
    else:
        ids = [i for i in sent if i]
        if pid and pid not in ids:
            ids.insert(0, pid)
    if pid and pid in ids:
        ids = [pid] + [i for i in ids if i != pid]
    ids = list(dict.fromkeys(ids))
    data["project_id"] = ids[0] if ids else None
    data["project_ids"] = json.dumps(ids) if len(ids) > 1 else None
