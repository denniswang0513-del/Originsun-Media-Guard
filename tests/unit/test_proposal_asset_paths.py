# -*- coding: utf-8 -*-
"""提案資產夾的路徑防線（`routers/crm/proposal_assets.py`，1,295 行）。

這個檔是提案子系統裡最大的一個，三週改了 11 次、其中只有 1 次連著測試改。
它做的事是**拿使用者傳來的字串去磁碟上讀寫檔案**——這條路上出錯不會噴 500，
會安靜地寫到不該寫的地方，或安靜地讓人下載到不該下載的檔。

三支各自守一段，而且**失敗行為刻意不同**（這是最容易在重構時被「統一」掉的）：

    file_or_404    使用者正在點的檔     → 逃逸/不存在 = 404
    _subdir_or_404 使用者正在瀏覽的層   → 不存在 = 404
    ensure_subdir  某筆提案的「家」     → 逃逸/建不出來 = **退回資產夾根**

最後那個看起來像漏洞其實不是：`folder_subpath` 是系統自己產或使用者認領過的，
磁碟上被搬走時不該讓上傳整個失敗——有落點總比 500 好。但它退回的是**根**，
不是照著逃逸路徑走，這一點才是防線。
"""
import os

import pytest
from fastapi import HTTPException

from routers.crm.proposal_assets import (_deck_rel, ensure_subdir, file_or_404,
                                         reject_oversize)


@pytest.fixture()
def folder(tmp_path):
    (tmp_path / "root").mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.txt").write_text("不該被拿到", encoding="utf-8")
    (tmp_path / "root" / "ok.pdf").write_text("x", encoding="utf-8")
    return str(tmp_path / "root")


# ── ensure_subdir：逃逸要退回根，不是照著走 ─────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("evil", [
    "../outside",
    "../../outside",
    "a/../../outside",
    "..\\outside",
])
async def test_an_escaping_subpath_lands_in_the_root_not_outside(folder, evil):
    """🔴 這是「上傳的檔會落在哪」。逃逸沒擋住的話，使用者上傳的檔會被寫到
    資產夾外面——而且回應是 200，沒有人會知道。"""
    got = await ensure_subdir(folder, evil)
    assert got == folder, f"{evil!r} 逃出去了：{got}"
    assert not os.path.exists(os.path.join(os.path.dirname(folder), "outside", "a"))


@pytest.mark.asyncio
@pytest.mark.parametrize("abs_like", [
    r"C:\Users\someone\Desktop",
    "C:/Users/someone/Desktop",
    r"\\NAS\share\提案",
    "//NAS/share/提案",
])
async def test_an_absolute_path_is_treated_as_unset(folder, abs_like):
    """🔴 2026-08-30 這支測試抓到的：`within_dir` 接不住碟符路徑。

    `os.path.join(root, "C:", "Users", …)` 在 Windows 上會把 `C:` 吃掉，於是
    `C:\\Users\\我\\x` 變成 root 底下一整串 `Users\\我\\x`。它**沒有**逃出資產夾
    （安全那面是好的），但會在客戶的共用夾裡憑空長出一棵照著某人硬碟路徑長的
    目錄樹，回應還是 200。docstring 本來就寫著「絕對路徑注入 → 當沒設」。
    """
    got = await ensure_subdir(folder, abs_like)
    assert got == folder, f"{abs_like!r} 被當成相對路徑接下去了：{got}"
    assert os.listdir(folder) == ["ok.pdf"], "在資產夾裡建出了不該有的目錄"


@pytest.mark.asyncio
async def test_a_normal_subpath_is_created_on_demand(folder):
    """這支的存在理由：那一層不存在也要能上傳（被搬走 / 還沒建）。"""
    got = await ensure_subdir(folder, "2026/提案A/報價單")
    assert os.path.isdir(got)
    assert got.startswith(folder)
    assert await ensure_subdir(folder, "2026/提案A/報價單") == got, "第二次呼叫不該炸"


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", ["", None, "/", "///"])
async def test_a_blank_subpath_means_the_root(folder, blank):
    assert await ensure_subdir(folder, blank) == folder


@pytest.mark.asyncio
async def test_backslashes_are_normalised(folder):
    """Windows 的路徑會帶反斜線進來，而比對是用正斜線做的。"""
    got = await ensure_subdir(folder, "a\\b")
    assert os.path.isdir(os.path.join(folder, "a", "b"))
    assert got == os.path.join(folder, "a", "b")


@pytest.mark.asyncio
async def test_an_unwritable_root_falls_back_instead_of_raising(folder):
    """建不出來就退回根 —— 呼叫端不必再寫一套退路。"""
    assert await ensure_subdir(os.path.join(folder, "ok.pdf"), "x") == \
        os.path.join(folder, "ok.pdf")


# ── file_or_404：下載那條路 ────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("evil", ["../outside/secret.txt", "../../outside/secret.txt"])
async def test_downloading_outside_the_folder_is_a_404(folder, evil):
    """🔴 這條是「誰拿得到哪個檔」。逃逸的回應必須是 404 而不是 403 ——
    403 等於告訴對方「那裡有東西」。"""
    with pytest.raises(HTTPException) as e:
        await file_or_404(folder, evil)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_a_missing_file_is_the_same_404(folder):
    with pytest.raises(HTTPException) as e:
        await file_or_404(folder, "不存在.pdf")
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_a_real_file_resolves(folder):
    got = await file_or_404(folder, "ok.pdf")
    assert got == os.path.join(folder, "ok.pdf")


@pytest.mark.asyncio
async def test_no_folder_at_all_is_a_404_not_a_crash(folder):
    """資產夾根沒設（NAS 搆不到）時，下載要回 404，不是 500。"""
    with pytest.raises(HTTPException) as e:
        await file_or_404("", "ok.pdf")
    assert e.value.status_code == 404


# ── _deck_rel：只認這個資料夾裡的檔 ─────────────────────────────

def test_a_deck_outside_the_folder_is_not_marked(folder, tmp_path):
    """星號標在別人的檔上 = 前端顯示「這份是提案簡報」，而它根本不在這個夾裡。"""
    assert _deck_rel(str(tmp_path / "outside" / "secret.txt"), folder) == ""


def test_a_web_url_deck_is_not_a_local_file(folder):
    """`/uploads/...` 那種是 web 路徑，不是磁碟上的相對位置。"""
    assert _deck_rel("/uploads/deck.pdf", folder) == ""


def test_a_deck_inside_the_folder_comes_back_with_forward_slashes(folder):
    """前端拿這個字串跟列表的 rel 比對 —— 反斜線會永遠比不中。"""
    sub = os.path.join(folder, "a")
    os.makedirs(sub)
    open(os.path.join(sub, "deck.pdf"), "w").close()
    got = _deck_rel(os.path.join(sub, "deck.pdf"), folder)
    assert got == "a/deck.pdf"
    assert "\\" not in got


@pytest.mark.parametrize("blank", ["", None])
def test_no_deck_means_no_mark(folder, blank):
    assert _deck_rel(blank, folder) == ""
    assert _deck_rel("whatever", "") == ""


# ── reject_oversize：契約定死 413 ───────────────────────────────

def test_an_oversize_upload_is_413_not_422():
    """🔴 `save_uploads` 只能回 422，而上傳契約定死 413 —— 前端據此顯示
    「檔案太大」而不是「格式錯誤」。兩個上傳端點都得在它之前擋一次。"""
    class F:
        size = 20 * 1024 * 1024
    with pytest.raises(HTTPException) as e:
        reject_oversize(F(), 10 * 1024 * 1024, "報價單")
    assert e.value.status_code == 413
    assert "10MB" in e.value.detail, "訊息沒告訴人上限是多少"
    assert "報價單" in e.value.detail


def test_a_file_at_exactly_the_limit_passes():
    class F:
        size = 10 * 1024 * 1024
    reject_oversize(F(), 10 * 1024 * 1024, "x")     # 不該 raise


def test_an_unknown_size_is_let_through():
    """拿不到 size 就不擋 —— 誤擋合法上傳比放過一個大檔嚴重（後面還有一層）。"""
    class F:
        size = None
    reject_oversize(F(), 1, "x")
    reject_oversize(object(), 1, "x")


def test_the_three_helpers_really_do_differ_on_failure():
    """🔴 把這三支「統一成同一種錯誤處理」會壞掉，所以把差異釘在這裡。

    看起來像不一致，其實是三種不同的東西：使用者點的檔（404）、使用者瀏覽的
    層（404）、系統自己記的家（退回根）。
    """
    from tests.unit._srcscan import code_only, func_body, repo_src
    src = repo_src("routers/crm/proposal_assets.py")
    ens = code_only(func_body(src, "async def ensure_subdir("))
    assert "HTTPException" not in ens, "ensure_subdir 開始丟 404 了 —— 上傳會整個失敗"
    assert ens.count("return folder_abs") >= 3, "退回根的那幾條路少了"
    for name in ("async def file_or_404(", "async def _subdir_or_404("):
        assert "404" in code_only(func_body(src, name)), name
