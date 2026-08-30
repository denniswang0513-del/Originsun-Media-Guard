# -*- coding: utf-8 -*-
"""提案／企劃子系統的三條硬規則。

2026-08-30 體檢：這個子系統六個檔、2,481 行，改動頻繁（`docs/PROPOSAL_PLANNER.md`
是全 repo 第五常改的檔）卻幾乎沒有行為測試——改動連著測試改的比例 0–33%，
而全 repo 平均是 73–78%、財務那批是 100%。

這裡不追覆蓋率，只釘住三條「壞掉會外洩或壞資料、而且不會噴錯」的規則。三條都
寫在各自檔案的 🔴 註解裡——註解擋不住下一次重構，測試可以。
"""
import re

import pytest

from tests.unit._srcscan import code_only, func_body, repo_src


# ── 一、範本骨架不准夾帶特定案子的資訊 ──────────────────────────

def test_the_digest_prompt_forbids_carrying_over_client_data():
    """🔴 範本常常是以前做過的**真實提案**，裡面夾著別家客戶的名字與金額。
    骨架是會被反覆餵進 prompt 的東西，那些不該長期躺在裡面。

    這條只能靠 prompt 執行（沒有程式層的遮蔽），所以 prompt 裡那句話就是
    這個規則的實作 —— 有人「精簡」prompt 時把它刪掉，這裡會紅。
    """
    src = repo_src("services/brief_template_digest.py")
    prompt = src.split('_PROMPT = """')[1].split('"""')[0]
    for term in ("客戶名稱", "人名", "金額"):
        assert term in prompt, f"prompt 不再交代「不要保留{term}」"
    assert "抽象化" in prompt or "抽象成" in prompt, "只說不要留、沒說要換成什麼"
    # 檔頭也要留著這條，讓改 prompt 的人先讀到理由
    assert "不保留客戶名、金額、人名" in src.split('"""')[1]


def test_the_source_text_is_capped_before_it_reaches_the_prompt():
    """整份 PDF 塞進去會把 prompt 撐爆（而失敗訊息只會是 claude 那側的截斷）。"""
    from services.brief_template_digest import MAX_TEXT_CHARS
    assert 10_000 <= MAX_TEXT_CHARS <= 200_000
    body = code_only(func_body(repo_src("services/brief_template_digest.py"),
                               "async def digest_template("))
    assert "MAX_TEXT_CHARS" in body, "上限有定義卻沒有人用它截斷"


# ── 二、骨架 → 生成前必問 的解析 ────────────────────────────────

def _q(skeleton):
    from services.brief_template_digest import parse_questions
    return parse_questions(skeleton)


def test_questions_are_only_read_from_their_own_section():
    """🔴 只收「生成前必問」那一段底下的項目。整份骨架抓 `- ` 開頭的話，
    章節結構的每一條都會變成要問企劃人員的問題（那份骨架整個是條列的）。"""
    sk = """# 章節結構
- 開場
- 主體
# 生成前必問
- 場地有幾個出入口？
- 參與者年齡層？
# 語氣
- 溫暖但不煽情
"""
    assert _q(sk) == ["場地有幾個出入口？", "參與者年齡層？"]


def test_a_skeleton_without_that_section_asks_nothing():
    assert _q("# 章節結構\n- 開場\n") == []
    assert _q("") == []
    assert _q(None) == []


@pytest.mark.parametrize("bullet", ["- ", "* ", "・", "1. ", "2、", "3) "])
def test_the_common_bullet_shapes_all_parse(bullet):
    """claude 每次回的條列符號不一定一樣。認不出來的那次，畫面上就是
    「這份範本沒有要問的問題」—— 看起來像正常結果。"""
    assert _q(f"# 生成前必問\n{bullet}預算區間？\n") == ["預算區間？"]


def test_prose_inside_the_section_is_skipped_not_asked():
    """那一段裡的散文（「這幾題必問：」）不是問題。"""
    got = _q("# 生成前必問\n這幾題必問：\n- 場地？\n")
    assert got == ["場地？"]


def test_the_question_list_is_capped():
    """🔴 沒有上限的話，claude 多話的那次會在畫面上排出三十個必填欄位，
    而使用者只能整個放棄。"""
    sk = "# 生成前必問\n" + "".join(f"- 問題{i}？\n" for i in range(30))
    assert len(_q(sk)) == 8


# ── 三、報價單的資料不准走到公開端點 ────────────────────────────

def test_no_quote_endpoint_is_public():
    """🔴 報價單含金額，是內部敏感資料。這個模組的任何一條都不准掛在
    `public_router` 上（同 budget_range 金額不出公開端點的既有慣例）。

    掃的是**有沒有任何端點沒掛守衛**，不是有沒有 public_router 這個字 ——
    後者只擋得住照著現在寫法寫的人。
    """
    src = repo_src("routers/crm/proposal_quotes.py")
    # 🔴 比 `code_only` 不是全檔 —— 那個檔的 docstring 自己就寫著「沒有任何一條
    # 進 public_router」，全檔比對會被它自己的註解餵飽（這一輪第二次踩到）。
    assert "public_router" not in code_only(src), "報價單模組出現了 public_router"
    # 每一支端點函式的本體裡都要有守衛
    unguarded = []
    for m in re.finditer(r"@router\.\w+\([^)]*\)\s*\n(async def (\w+)\()", src):
        name = m.group(2)
        body = code_only(func_body(src, m.group(1)))
        if "proposal_auth(" not in body and "_check" not in body:
            unguarded.append(name)
    assert not unguarded, f"這些報價單端點沒有守衛：{unguarded}"


def test_the_scan_would_notice_if_the_endpoints_vanished():
    """🔴 掃不到端點要當失敗。改寫法（換 decorator、拆檔）之後，上面那支
    會安安靜靜地永遠綠燈，而它要守的東西早就沒人看著了。"""
    src = repo_src("routers/crm/proposal_quotes.py")
    n = len(re.findall(r"@router\.\w+\(", src))
    assert n >= 5, f"只掃到 {n} 個報價單端點，掃描本身可能壞了"


def test_quotes_never_fall_back_to_the_public_web_root():
    """🔴 deck 有 `/uploads` 退路，報價單刻意沒有 —— 資產夾建不出來就
    400 明講，不靜默落到一個公開直出的目錄。"""
    src = code_only(repo_src("routers/crm/proposal_quotes.py"))
    assert "uploads" not in src, "報價單出現了 /uploads 退路（那個目錄是公開直出的）"


# ── 四、底線開頭的夾不是提案資產夾 ──────────────────────────────

def test_underscore_and_dot_folders_are_rejected_by_name_not_by_a_list():
    """🔴 `_範本`（企劃範本庫）與 `.chunk-uploads`（分塊上傳）住在同一個 root
    底下。不擋的話它們會變成可以被「連結專案」「改名」的夾，而改名走
    `rename_and_remap`（專案夾的程序），連下去就壞了。

    用述詞而不是保留名清單：清單只擋得住有人記得登記的那些。
    """
    body = code_only(func_body(repo_src("routers/crm/proposal_assets.py"),
                               "async def _folder_or_404("))
    assert '[:1] in (".", "_")' in body, "改成保留名清單了？那下一個底線夾就漏了"
    # 擋在算路徑之前 —— 順序反了就等於先讓它通過 safe_subfolder
    assert body.index('[:1] in (".", "_")') < body.index("safe_subfolder")


def test_the_same_predicate_guards_the_overview_scan():
    """總覽掃描與單夾查詢要用同一條述詞，不然「總覽看不到、但打得開」。"""
    src = code_only(repo_src("routers/crm/proposal_assets.py"))
    assert src.count('(".", "_")') >= 2, \
        "只有一個地方在擋底線夾了 —— 另一條路會漏"


def test_the_template_dir_keeps_its_underscore():
    """範本庫靠底線開頭把自己排除在提案資產夾之外。改名時把底線拿掉，
    它就會變成一個「提案」出現在總覽裡，而且可以被改名。"""
    from routers.crm.brief_templates import TEMPLATE_DIR
    assert TEMPLATE_DIR.startswith("_"), \
        f"{TEMPLATE_DIR!r} 沒有底線開頭 —— 它會被當成提案資產夾"
