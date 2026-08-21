# -*- coding: utf-8 -*-
"""對帳單匯入草稿（owner 2026-08-21）。

一份對帳單幾十列，每列要決定分類、掛哪個專案、對到哪張發票。沒有草稿的話，
人一離開就得從上傳重來一次。

這裡釘的是草稿最容易做錯的三件事：
  1. 存的是**原始文字**不是解析結果 —— 存快照的話，草稿放兩天再開，重複判定
     還停在存檔當下（那些列可能已經從別的路進帳了 → 會匯第二次）。
  2. 重複的列一律不勾，即使草稿裡勾了。
  3. 決定用（日期, 金額, 摘要）貼回去，而且一個鍵一串 —— 同一份對帳單裡兩列
     可能一模一樣，用 dict 直接覆蓋會讓兩列共用同一個決定。
"""
from tests.unit._srcscan import repo_src  # noqa: E402

API = repo_src('routers/api_finance.py')
JS = repo_src('frontend/tabs/finance/subviews/banking.js')


def _fn(src, sig, stop='\n@router.'):
    i = src.index(sig)
    j = src.find(stop, i + 1)
    return src[i:j if j > 0 else len(src)]


def test_draft_stores_the_source_text_not_the_parsed_rows():
    from db.models import BankImportDraft
    cols = BankImportDraft.__table__.columns.keys()
    assert 'source_text' in cols, '草稿沒存原始文字 —— 開啟時就沒得重新解析'
    assert 'decisions' in cols


def test_opening_a_draft_reparses():
    body = _fn(API, 'async def open_statement_draft(')
    assert '_build_statement_preview(' in body, '開啟草稿沒有重新解析'
    assert 'd.source_text' in body


def test_rows_already_in_the_books_are_never_pre_checked():
    """🔴 存草稿之後才被匯進去的列 —— 使用者當初勾的時候還不重複。"""
    body = _fn(API, 'async def open_statement_draft(')
    assert 'and not r["duplicate"]' in body, \
        '草稿的勾選沒有被重複判定否決 → 會匯第二次'


def test_decisions_are_restored_by_content_and_allow_twins():
    body = _fn(API, 'async def open_statement_draft(')
    assert '_draft_key(' in body
    # 一個鍵一串、依序 pop：同一份對帳單裡兩列可能完全一樣
    assert 'setdefault(' in body and '.pop(0)' in body, \
        '決定用 dict 直接覆蓋 —— 兩列一模一樣時會共用同一個決定'


def test_saving_twice_updates_instead_of_piling_up():
    body = _fn(API, 'async def save_statement_draft(')
    assert 'if payload.id:' in body, '存草稿沒有更新既有那份 → 會越存越多份'
    body_js = JS[JS.index('_fb.stmtSaveDraft'):]
    assert 'd.draft_id || null' in body_js, '前端沒把 draft_id 送回去'


def test_draft_is_scoped_to_a_ledger():
    """兩本帳：草稿不能跨帳本開啟／刪除。"""
    # 守衛可以是直接呼叫 _guard，也可以是 _acct_and_entity（它內含由帳戶推 scope）
    for sig in ('async def open_statement_draft(', 'async def delete_statement_draft(',
                'async def save_statement_draft(', 'async def list_statement_drafts('):
        body = _fn(API, sig)
        assert '_guard(' in body or '_acct_and_entity(' in body, f'{sig} 沒有帳本守衛'


def test_preview_returns_source_text_for_the_save():
    """上傳 PDF 時文字是後端抽出來的，前端手上沒有 —— 不回傳就存不了草稿。"""
    body = _fn(API, 'async def _build_statement_preview(', stop='\n@router.')
    assert '"source_text": text' in body
