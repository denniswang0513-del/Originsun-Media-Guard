# -*- coding: utf-8 -*-
"""福委會表格版面（owner 2026-08-21：「調整 這個看起來很不整齊」）。

量到的實況：`table-layout:auto` + `width:100%` 會**按內容多寡**分欄寬。
在 2560 的螢幕上「項目」拿去 876px 裝五個字，「操作」只剩 127px 把
核准／退回擠成上下兩行，日期也被折行 —— 欄位其實對齊，看起來就是亂。

所以釘三件事：
  🔴 每張表都有 <colgroup> 且 col 數 == th 數（少一個，整張表的欄寬就錯位）
  🔴 table-layout:fixed（不然 colgroup 的寬度只是建議值）
  🔴 min-width 撐得住最寬的按鈕組合（量過：登記匯款＋退回 = 142px）
"""
import re

from tests.unit._srcscan import repo_src

JS = "frontend/tabs/hr_benefits/hr_benefits.js"
CSS = "frontend/tabs/hr_benefits/hr_benefits.html"


def _tables(src):
    """回傳每張表的 (col 數, th 數)。"""
    out = []
    for m in re.finditer(r"<colgroup>(.*?)</colgroup>\s*\n\s*<thead>(.*?)</thead>",
                         src, re.S):
        out.append((len(re.findall(r"<col\b", m.group(1))),
                    len(re.findall(r"<th\b", m.group(2)))))
    return out


def test_every_table_has_a_colgroup():
    src = repo_src(JS)
    n_tables = len(re.findall(r"<thead>", src))
    # 5 張：待審佇列／說明附件無表／撥款／每人額度／登記明細／送會計彙總
    assert n_tables == 5, f"表數變了（{n_tables}）—— 新表也要補 colgroup"
    assert len(_tables(src)) == n_tables, "有表沒有 colgroup（或 colgroup 不在 thead 前）"


def test_col_count_matches_th_count():
    """🔴 col 少一個，那一欄之後全部錯位 —— 而且畫面上看起來只是「有點怪」。"""
    for i, (cols, ths) in enumerate(_tables(repo_src(JS))):
        assert cols == ths, f"第 {i} 張表：{cols} 個 col 對 {ths} 個 th"


def test_exactly_one_flexible_column_and_it_is_last():
    """多餘寬度只留給**最後一欄**。擺在中間（例如「項目」）的話，它會
    一路撐到 800px 以上，內容跟後面的金額之間空出一條河 —— 第一版就是這樣。"""
    src = repo_src(JS)
    for i, m in enumerate(re.finditer(r"<colgroup>(.*?)</colgroup>", src, re.S)):
        cols = re.findall(r"<col\b[^>]*>", m.group(1))
        flex = [k for k, c in enumerate(cols) if "width" not in c]
        assert len(flex) == 1, f"第 {i} 張表有 {len(flex)} 個彈性欄，應該只有 1 個"
        assert flex[0] == len(cols) - 1, f"第 {i} 張表的彈性欄不在最後（在第 {flex[0]}）"


def test_table_layout_is_fixed():
    """沒有 fixed 的話 colgroup 的寬度只是建議，瀏覽器照樣按內容重分。"""
    css = repo_src(CSS)
    assert "table-layout: fixed" in css


def test_min_width_covers_the_widest_button_row():
    """量過的下限：登記匯款＋退回 142px、單據＋換＋心得 117px。

    明細表固定欄合計 802px（104+92+280+96+150+80），再加操作欄的 142
    ＝ 944 —— min-width 要 >= 這個數，否則視窗一窄按鈕又疊起來。
    """
    css = repo_src(CSS)
    m = re.search(r"min-width:\s*(\d+)px", css)
    assert m, "表格沒有 min-width —— 窄螢幕會把欄位擠壞"
    assert int(m.group(1)) >= 944, f"min-width {m.group(1)} 撐不住按鈕（要 >= 944）"
    assert "overflow-x: auto" in css, "沒有橫向捲，min-width 會把卡片撐破"


def test_fixed_columns_do_not_wrap():
    """定寬欄不折行（日期折成兩行是 owner 截圖裡最刺眼的一項）；
    只有彈性內容那欄（.wrap）准折。"""
    css = repo_src(CSS)
    td = css[css.index("#hb-root td {"):]
    td = td[:td.index("}")]
    assert "white-space: nowrap" in td
    assert "#hb-root td.wrap { white-space: normal;" in css
