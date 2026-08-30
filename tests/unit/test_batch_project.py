# -*- coding: utf-8 -*-
"""批次把請款單掛到專案（owner 2026-08-23：「專案我可以手動掛，精準為主」）。

為什麼需要：817 張請款單裡「專案外包」371 張／1,101 萬，一張都沒掛專案，而那是
營收的 73.5%。沒有它就沒有專案成本，沒有專案成本就沒辦法預估。

自動比對這條路是死的 —— `project_label` 只有 140 張有填、29 種值，跟專案名
**精確吻合 0 筆**（都是「2025王道週年影片」這種帶年份前綴的自由文字）。所以
owner 選擇人工掛，工具只負責「一次寫很多筆」。

🔴 人工＋批次的組合有個特有的危險：一次幾十張，掛錯不像逐張那樣當場看得出來。
   所以類別守衛不能省，而且違規要**整批擋下並說明**，不能默默跳過 —— 默默跳過
   ＝使用者以為掛好了，而專案成本永遠少那幾張。
"""
from tests.unit._srcscan import finance_src, code_only, func_body, js_code_only, repo_src

# 內容本身（不是路徑）—— finance.py 2026-08-30 拆成四個檔，
# finance_src() 把它們串起來，斷言釘的是「這支函式做了什麼」不是它在哪。
SRC = finance_src()
JS = "frontend/tabs/crm/crm-payments.js"


def _body():
    return code_only(func_body(SRC, "async def batch_assign_project("))


def test_it_reuses_the_one_rule_about_which_categories_may_link_a_project():
    """🔴 不准自己寫一份類別清單 —— core/project_link 存在的理由就是「同一個問題
    三個地方三種答案」。這裡再寫一份，改規則時就會漏掉批次這條路。"""
    body = _body()
    assert "_PAYMENT_LINK_CATEGORIES" in body, "沒走 core.project_link 的正本"
    for literal in ("專案外包", "專案雜支"):
        assert f'"{literal}"' not in body and f"'{literal}'" not in body, \
            f"批次端點裡寫死了「{literal}」—— 規則要從 project_link 來"


def test_bad_rows_abort_the_whole_batch_instead_of_being_skipped():
    """整批擋下，不是默默跳過那幾張。"""
    body = _body()
    assert "status_code=409" in body
    i = body.index("bad = [r for r in rows")
    seg = body[i:i + 700]
    assert "raise HTTPException" in seg, "違規列沒有擋下來"
    assert "continue" not in seg, "違規列被跳過了 —— 使用者會以為全部掛好了"


def test_the_error_says_which_categories_offended():
    """訊息要能行動：知道是哪些類別，才知道要取消勾選哪幾張。"""
    body = _body()
    assert "r.category or" in body and "names" in body, "沒有把違規的類別列出來"


def test_unlinking_is_always_allowed():
    """解除連結（project_id=None）不該被類別擋 —— 掛錯了要能拿下來。"""
    body = _body()
    # 🔴 要釘「緊接在前面的那一行」，不是「body 裡有沒有出現過」——
    #    body 前段本來就有另一個 if project_id:（撈專案那個），寬鬆的寫法
    #    把守衛改成 if True: 也照樣過（實測，第一版就是這樣漏掉的）
    i = body.index("bad = [r for r in rows")
    head = body[:i].rstrip().splitlines()[-1].strip()
    assert head == "if project_id:", "類別守衛的條件變成「" + head + "」—— 解除連結會被擋"


def test_cross_ledger_and_missing_rows_are_refused():
    """兩本帳不可混，找不到的列要講出來（可能剛被別人刪掉）。"""
    body = _body()
    assert "missing" in body and "status_code=404" in body
    assert "cross" in body and 'r.entity or "parent"' in body


def test_it_does_not_bolt_on_a_month_lock_guard():
    """判準：只改「屬於哪個案子」的歸屬，金額與兩個日期都沒動 → 不掛月結守衛
    （與 batch-month 同一判準）。掛上去會讓補歷史在鎖月之後完全做不了。"""
    assert "_assert_month_open" not in _body()


def test_the_unassigned_filter_only_lists_rows_that_should_have_a_project():
    """「只看未掛專案」不能把行政／薪資也列進來 —— 那些本來就不該掛，
    混進來只會讓待辦清單看起來永遠做不完。"""
    src = code_only(SRC)
    i = src.index("if unassigned:")
    seg = src[i:i + 400]
    assert "CrmPaymentRequest.project_id.is_(None)" in seg
    assert "_PAYMENT_LINK_CATEGORIES" in seg, "沒有限定在可連結專案的類別內"


# ── 前端 ────────────────────────────────────────────────────

def test_shift_range_uses_the_on_screen_order():
    """🔴 Shift 範圍選要照**畫面順序**。用 _payments 的原順序的話，使用者按了
    日期或金額排序之後，選到的會是完全不相干的一段 —— 而且看起來還很正常。"""
    js = js_code_only(repo_src(JS))
    assert "_batch.order = rows.map(p => p.id);" in js, "沒有記錄畫面順序"
    # 錨在**定義**上，不是第一次出現 —— 第一次出現在列的模板字串裡（onclick=）
    i = js.index("window._payRowClick = (ev, id) => {")
    seg = js[i:js.index("function _batchRefreshBar", i)]
    assert "const order = _batch.order;" in seg
    assert "_payments" not in seg, "範圍選用了資料順序而不是畫面順序"


def test_shift_range_adds_instead_of_toggling():
    """範圍一律加選 —— toggle 會把中間已經選好的取消掉。"""
    js = js_code_only(repo_src(JS))
    i = js.index("if (ev && ev.shiftKey")
    seg = js[i:js.index("} else if", i)]
    assert "_batch.sel.add(order[i])" in seg
    # 🔴 光看有沒有 add 抓不到 toggle —— toggle 的 else 分支裡也有 add
    assert "delete" not in seg, "範圍選變成 toggle 了 —— 中間已選的會被取消"


def test_toast_is_imported_not_taken_off_window():
    """🔴 crmToast 是 crm-utils 的具名匯出、**不在 window 上**。寫成
    `window.crmToast?.(…)` 會被 optional chaining 靜默吞掉 —— 後端好不容易
    講清楚的 409 原因，使用者一個字都看不到。（我第一版就是這樣寫的。）"""
    js = js_code_only(repo_src(JS))
    assert "window.crmToast" not in js, "又從 window 抓 crmToast 了"
    import re
    imp = re.search(r"import \{([^}]*)\} from './crm-utils.js'", js).group(1)
    assert "crmToast" in imp, "沒有從 crm-utils import crmToast"


def test_batch_mode_hides_the_quick_add_row():
    """批次模式下點列＝選取，快速新增列會變成一個點了沒反應的陷阱。"""
    js = js_code_only(repo_src(JS))
    assert "(_batch.on ? '' : _quickAddRow())" in js
