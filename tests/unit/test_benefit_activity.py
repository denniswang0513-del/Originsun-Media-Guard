# -*- coding: utf-8 -*-
"""年度活動：每人額度＋期間＋說明附件（docs/BENEFIT_POOL_PLAN.md §9）。

> owner 2026-08-21：「我想要有一個像是活動，然後下面有好幾個人的額度與
>   有效期間，然後有一些活動說明。」
> 「有些是健檢方案⋯⋯我覺得可以就是一個可以打字、附上文件的說明。」

純規則那一層在 test_benefit_allowance.py，這一份守的是**接線**：
守衛有沒有真的裝在每一條進得了門的路上、附件會不會被整包寫回洗掉、
既有的兩個共用池會不會被這次改動波及。
"""
from tests.unit._srcscan import code_only, func_body, repo_src

SRC = "routers/crm/benefits.py"
JS = "frontend/tabs/hr_benefits/hr_benefits.js"
MY = "frontend/my.html"


def _body(fn):
    return code_only(func_body(repo_src(SRC), fn))


# ── 既有的共用池不能被波及 ────────────────────────────────────────

def test_shared_is_the_default_everywhere():
    """🔴 既有兩個池（快樂／進修）沒有 quota 值，一律要落回 shared。
    預設值錯成 per_person 的話，那兩個池會突然開始擋所有人。"""
    from db.models import HrBenefitPool
    col = HrBenefitPool.__table__.c["quota"]
    assert col.server_default.arg == "shared"
    assert "'shared'" in repo_src("main.py"), "migration 沒有給 DEFAULT"
    # 讀的那一側也要有 fallback（舊列可能是 NULL）
    assert '(p.quota or "shared")' in repo_src(SRC)


def test_shared_pools_are_never_blocked():
    """共用池超支只轉紅不擋 —— 那是公司該知道的事實（§9.4）。
    守衛的第一件事就是遇到 shared 直接放行。"""
    body = _body("async def _guard_allowance(")
    head = body[:body.index("al = ")]
    assert 'if (p.quota or "shared") != "per_person":' in head
    assert "return" in head


# ── 守衛裝在每一條路上 ────────────────────────────────────────────

def test_guard_is_wired_into_every_way_in():
    """🔴 三條路都會產生登記：員工自助、員工改自己的、管理端代登。
    少裝任何一條，那條路就是繞過額度的後門。"""
    for fn in ("async def add_my_entry(", "async def update_my_entry(",
               "async def add_entry_for("):
        assert "_guard_allowance(" in _body(fn), f"{fn} 沒有裝守衛"


def test_editing_revalidates_amount_and_date():
    """🔴 只在建立時擋的話，登記 1 元再改成 99999 就整個繞過去了。"""
    body = _body("async def update_my_entry(")
    assert "exclude_id=e.id" in body, "重驗時沒有把自己排除（會自己吃自己的額度）"


def test_admin_cannot_bypass_by_leaving_staff_empty():
    """代登可以不指定人（共用池的歷史資料就是這樣）——
    但每人額度的活動不指定人＝無從扣額度，要擋。"""
    body = _body("async def add_entry_for(")
    assert "要指定是誰" in body


# ── 附件不能被整包寫回洗掉 ────────────────────────────────────────

def test_attachments_are_not_in_the_pool_payload():
    """🔴 老坑：整包 model_dump 寫回 ＋ 欄位有預設值 ＋ 前端不送該欄
    ＝ 該欄被洗掉。附件只能走上傳／刪除那兩支。"""
    from core.schemas import BenefitPoolPayload
    assert "attachments" not in BenefitPoolPayload.model_fields
    assert "p.attachments" not in _body("async def update_pool(")


def test_attachment_write_replaces_the_list():
    """🔴 JSONB 欄要整個換掉才會被 SQLAlchemy 偵測到有變 ——
    in-place append 不進 dirty set，commit 之後檔案在磁碟上、清單裡沒有它。"""
    body = _body("async def upload_pool_file(")
    assert "p.attachments = list(p.attachments or []) + [" in body


def test_attachment_delete_uses_id_not_index():
    """索引會隨著別人刪檔而位移，刪錯的是另一個檔。"""
    body = _body("async def delete_pool_file(")
    assert 'f.get("id") != file_id' in body
    assert "pop(" not in body and "del " not in body


def test_attachment_upload_has_the_two_guards():
    """副檔名黑名單＋串流上限 —— 與零用金收據同一套。"""
    body = _body("async def upload_pool_file(")
    assert "BLOCKED_UPLOAD_EXTS" in body
    assert "stream_to_disk" in body and "_MAX_ATTACH_BYTES" in body


def test_attachment_reuses_the_existing_storage_and_serving_path():
    """不另開一條儲存與取檔的路（同單據）。"""
    body = _body("async def upload_pool_file(")
    assert "_receipts_root()" in body and "_福委會" in body
    assert "/api/v1/crm/receipt-file?path=" in repo_src(JS)
    assert "/api/v1/crm/receipt-file?path=" in repo_src(MY)


def test_undeletable_file_is_not_silent():
    """刪不掉（NAS 權限）不算失敗，但要留訊息 —— 靜默略過會讓人以為清乾淨了。"""
    body = _body("async def delete_pool_file(")
    # 🔴 釘住**做了什麼**，不是釘住 except 存在 —— 改成 `except OSError: pass`
    #    的話，「except OSError」照樣在，訊息卻不見了（破壞驗證抓到的）。
    assert "磁碟上的檔沒刪掉" in body, "刪不掉被靜默吃掉了"
    assert '"note": left' in body, "訊息沒有回給前端"


# ── 額度 CRUD 的守衛 ──────────────────────────────────────────────

def test_one_allowance_per_person_per_activity():
    """重複發是帳對不起來的起點。應用層擋 ＋ DB unique 兜底。"""
    assert "已經有一份額度了" in _body("async def add_allowance(")
    from db.models import HrBenefitAllowance
    idx = [i for i in HrBenefitAllowance.__table__.indexes
           if i.name == "uq_benefit_allowance"]
    assert idx and idx[0].unique, "DB 沒有 unique 兜底"


def test_bulk_skips_existing_instead_of_overwriting():
    """批次那顆只補沒有的 —— 覆蓋的話，手動調過的額度會被打回原形。"""
    body = _body("async def add_allowances_bulk(")
    assert "if st.id in have:" in body and "continue" in body
    assert '"skipped"' in body, "沒有回報跳過幾個（看起來像沒做事）"


def test_bulk_only_takes_active_staff():
    body = _body("async def add_allowances_bulk(")
    assert 'CrmStaff.status == "在職"' in body


def test_used_allowance_cannot_be_deleted():
    """刪了那些登記就變成沒有額度依據的孤兒（同「只有空池能刪」）。"""
    body = _body("async def delete_allowance(")
    # 條件本身要釘 —— 只釘錯誤訊息的話，把 if 停用掉訊息照樣在（實測會漏）
    assert "if used:" in body, "沒有真的檢查有沒有用過"
    assert "409" in body and "不能刪額度" in body


def test_allowance_update_does_not_move_it_to_another_person():
    """換人＝把已經花掉的錢算到別人頭上。"""
    body = _body("async def update_allowance(")
    assert "a.staff_id" not in body and "a.staff_name" not in body


def test_pool_delete_also_checks_allowances():
    body = _body("async def delete_pool(")
    assert "HrBenefitAllowance" in body


# ── 期間 ──────────────────────────────────────────────────────────

def test_window_falls_back_to_the_pool():
    """額度列上沒填期間就繼承活動的（§9.3）。"""
    body = _body("def _window_of(")
    assert "al.valid_from if al is not None and al.valid_from else pool.valid_from" in body


def test_dates_go_through_local_day_not_raw_strftime():
    """🔴 timestamptz 讀回來是 UTC，直接比會差一天（這個 repo 咬過三次）。"""
    assert "local_day(" in _body("def _allowance_state(")
    assert "local_day(" in _body("def _window_of(")
    # 顯示用的字串走 _fmt_day（repo 既有的守衛測試也在盯這條）
    body = _body("async def my_benefits(")
    assert "strftime" not in body


# ── 畫面 ──────────────────────────────────────────────────────────

def test_admin_has_allowance_and_about_sections():
    js = repo_src(JS)
    assert "function allowanceHtml(" in js and "function aboutHtml(" in js
    assert "全部在職員工各發一份" in js
    assert "hb-p-quota" in js, "新增項目時不能選類型"


def test_admin_swaps_funding_for_allowance_by_kind():
    """每人額度的活動不該顯示「撥款進池」—— 那是共用桶的概念。"""
    js = repo_src(JS)
    assert "const perPerson = (p.quota || 'shared') === 'per_person';" in js
    assert "${perPerson ? allowanceHtml(p) : `" in js


def test_my_card_shows_my_allowance_and_the_description():
    my = repo_src(MY)
    # 分支條件要釘 —— 只釘欄位名的話，把 if 改成 false 欄位名照樣在（實測會漏）
    assert 'if ((p.quota || "shared") === "per_person") {' in my,         "每人額度那條分支不見了"
    assert 'p.mine_allowance' in my
    assert "我的額度" in my and "我還可以用" in my
    assert "money(a.available)" in my, "顯示的是 balance 而不是可再申請的數字"
    assert "沒有發給你" in my, "沒發到額度時畫面要講出來（不能空白）"
    assert "p.description" in my and "p.attachments" in my
    assert "white-space:pre-wrap" in my, "說明是打字打的，換行要留著"
