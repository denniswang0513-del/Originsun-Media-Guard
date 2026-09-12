# -*- coding: utf-8 -*-
"""收支明細 → 零用金請款（owner 2026-08-27「不管是匯款或信用卡都可以直接接到
crm 的零用金請款」）。

釘住兩件會靜默出錯的事：
  1. 換詞 —— 私帳寫 `公司_器材`，母公司的會計項目是 `設備耗材`。直接把複合鍵
     塞進單據，應付款的科目就落到另一條分支，而且沒有任何錯誤訊息。
  2. 對不出來時**留白**，不要硬猜一個項目（猜錯比留白更難翻出來）。
"""
from pathlib import Path

from core.cash_taxonomy import PETTY_ITEM_OVERRIDES, petty_item_for
from db.seed_finance import SEED_CATEGORY_MAP
from tests.unit._srcscan import cashbook_src   # 2026-09-12 拆成主檔＋五段

ROOT = Path(__file__).resolve().parents[2]

# 母公司的會計項目 —— 從種子正本推導（手抄一份的話，種子改了測試照樣綠，
# 生產卻開始對某個 override 靜靜回空字串）。值域規則同 petty._petty_item_domain：
# source='cash'、費用類、平鋪。
PARENT_ITEMS = tuple(
    r["category_text"] for r in SEED_CATEGORY_MAP
    if r["source"] == "cash" and r["treatment"] == "direct_expense"
    and "_" not in r["category_text"])


def test_override_換詞():
    assert petty_item_for("公司_器材", PARENT_ITEMS) == "設備耗材"
    assert petty_item_for("公司_軟體與耗材", PARENT_ITEMS) == "軟體網路服務"
    assert petty_item_for("公司_專案", PARENT_ITEMS) == "專案雜支"
    assert petty_item_for("公司_餐敘", PARENT_ITEMS) == "交際應酬"


def test_通則_去前綴():
    """沒進 override 的靠去前綴 —— 這些私帳詞彙本來就跟母公司一樣。"""
    assert petty_item_for("公司_教育訓練", PARENT_ITEMS) == "教育訓練"
    assert petty_item_for("公司_其他", PARENT_ITEMS) == "其他"
    # 本來就是平鋪的（母公司那本推過來）也要能用
    assert petty_item_for("設備耗材", PARENT_ITEMS) == "設備耗材"


def test_對不出來就留白():
    """🔴 不准硬猜。個人/家用的類別在母公司沒有對應，一律回空字串讓人挑。"""
    assert petty_item_for("個人_生活", PARENT_ITEMS) == ""
    assert petty_item_for("家用_變動支出", PARENT_ITEMS) == ""
    assert petty_item_for("", PARENT_ITEMS) == ""
    assert petty_item_for(None, PARENT_ITEMS) == ""


def test_override_的目標都在母公司詞彙裡():
    """對映表寫錯字的話，petty_item_for 會安靜地回空字串（白名單擋掉），
    看起來像「這個類別沒對應」而不是「對映表打錯了」。"""
    for src, dst in PETTY_ITEM_OVERRIDES.items():
        assert dst in PARENT_ITEMS, f"{src} → {dst} 不在母公司會計項目裡"


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_金額式子兩邊一致():
    """前端列上的流出金額與後端的請款金額必須是同一個式子 ——
    兩邊各寫一份就會出現「畫面 1,050、請款 1,000」這種只差匯費的鬼影。"""
    be = _read("routers/crm/petty.py")
    assert "int(e.expense or 0) + int(e.claim or 0) + int(e.bank_fee or 0)" in be
    fe = cashbook_src()
    assert "_grossOut(e) + (e.claim || 0)" in fe


def test_只建草稿且走單據正本():
    """🔴 推送不可以順手送出（送出＝即核准即產應付款）；建構必須走
    `_new_expense`（單據建構的單一正本）—— 第三個手刻建構點就是第三份會漂的
    預設值清單。空項目後端要擋（400），不落「其他」。"""
    be = _read("routers/crm/petty.py")
    push = be.split("async def _push_from_cash")[1].split("@router.delete")[0]
    assert "_new_expense(" in push
    assert "_submit_for" not in push
    assert "請先選會計項目" in push          # 空項目＝後端擋，不靠對話框的 JS


def test_請款人是身分不是欄位():
    """🔴 代為推送走**路徑參數**，own-scope 的 schema 不長 staff_id ——
    比照 PettyExpensePayload（test_petty_cash.py 釘的同一條不變式）。"""
    from core.schemas import PettyFromCashPayload
    assert "staff_id" not in PettyFromCashPayload.model_fields
    be = _read("routers/crm/petty.py")
    assert '@router.post("/petty/staff/{staff_id}/from-cash"' in be
    own = be.split("async def petty_from_cash(")[1].split("@router.post")[0]
    assert "_my_staff(request)" in own


def test_回鏈與重推防線():
    be = _read("routers/crm/petty.py")
    assert "entry.expense_id = exp.id" in be      # 兩邊釘死
    assert "已經推送過" in be                      # 重推被擋
    fe = cashbook_src()
    assert "petty_status" in fe                   # 列上看得出推過沒有
