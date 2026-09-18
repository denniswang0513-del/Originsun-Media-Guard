# -*- coding: utf-8 -*-
"""研究週報／月報（docs/KNOWLEDGE_BASE_PLAN.md §9.5）。

owner 2026-09-18：「我想要有個週報或月報」＋「這些生產的資料都用 md 檔整理起來」
＋「不用送 google chat 送 discord」＋「這些報表可以使用連結」。

釘四件事：
  1. 期間算對 —— 週一發的是**剛過完**那一週，一號發的是**上個**月。
  2. md 檔是正本，報告 id 就是連結上的字，路徑一律過白名單。
  3. Discord 一則有 2000 字元硬上限，超過會被整則退掉（不是截斷）。
  4. 空的一期不發；同一期不重發。
"""
import datetime as dt
import os
import re

import pytest

from core import knowledge_logic as kl
from services import knowledge_report as kr
from tests.unit._srcscan import func_body, repo_src


def _item(**over):
    it = {"n": 1, "date": "2026-09-16", "title_zh": "某篇研究", "url": "https://a.tw/x",
          "title_original": "Some Study", "lang": "en", "source": "NBER", "published": "2026-09-10",
          "summary_zh": "摘要一句。", "chapter_guess": "第 3 章", "why_it_matters": "跟你的規則有關",
          "rating": ""}
    it.update(over)
    return it


# ── 1. 期間 ──────────────────────────────────────────────────
def test_monday_reports_the_week_that_just_ended():
    """2026-09-21 是週一；那天要講的是 9/14–9/20，不是當週。"""
    label, start, end = kl.last_week(dt.date(2026, 9, 21))
    assert (label, start, end) == ("2026-W38", dt.date(2026, 9, 14), dt.date(2026, 9, 20))


def test_the_first_reports_the_month_that_just_ended():
    assert kl.last_month(dt.date(2026, 10, 1)) == ("2026-09", dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    assert kl.last_month(dt.date(2027, 1, 1)) == ("2026-12", dt.date(2026, 12, 1), dt.date(2026, 12, 31))


def test_only_items_collected_in_the_span_count():
    span = (dt.date(2026, 9, 14), dt.date(2026, 9, 20))
    assert kl.in_span(_item(date="2026-09-14"), *span)
    assert kl.in_span(_item(date="2026-09-20"), *span), "兩端都要含"
    assert not kl.in_span(_item(date="2026-09-13"), *span)
    assert not kl.in_span(_item(date="2026-09-21"), *span)


@pytest.mark.parametrize("bad", ["", None, "2026-9-1x", "昨天"])
def test_a_broken_date_is_left_out_not_crashing(bad):
    assert kl.in_span(_item(date=bad), dt.date(2026, 9, 1), dt.date(2026, 9, 30)) is False


# ── 2. md 檔與連結 ───────────────────────────────────────────
def test_the_report_id_is_also_the_link():
    assert kr.report_url("weekly-2026-W38").endswith("#report/weekly-2026-W38")
    assert kr.PUBLIC_BASE.startswith("https://"), (
        "辦公室那一面不供 knowledge.html（core/office_assets.PAGES），相對路徑會 503")


@pytest.mark.parametrize("bad", ["../x", "..\\..\\settings", "weekly", "weekly-2026-W3",
                                 "daily-2026-01", "weekly-2026-W38/../x", ""])
def test_a_bad_report_id_never_becomes_a_path(bad):
    assert not kr.is_valid_report_id(bad)
    with pytest.raises(kr.ReportNotFound):
        kr.report_path(bad)


@pytest.mark.parametrize("good", ["weekly-2026-W38", "monthly-2026-09"])
def test_the_two_shapes_are_accepted(good):
    assert kr.is_valid_report_id(good)


def test_the_weekly_md_carries_the_links_and_the_caveat():
    groups = [("錢的三章", [_item(), _item(n=2, url="https://b.tw/y", title_zh="第二篇")])]
    md = kl.weekly_report_md("2026-W38", dt.date(2026, 9, 14), dt.date(2026, 9, 20), groups)
    assert md.startswith("# 研究週報 2026-W38")
    assert "(https://a.tw/x)" in md and "(https://b.tw/y)" in md
    assert "不是書的作者說的" in md, "報告要講清楚這些不是作者說的"
    assert "Some Study" in md, "外文的原文標題要留著，他才對得回去"


def test_the_monthly_md_is_a_summary_not_a_second_copy():
    groups = [("錢的三章", [_item(rating="useful"), _item(n=2, rating="useless"), _item(n=3)])]
    md = kl.monthly_report_md("2026-09", dt.date(2026, 9, 1), dt.date(2026, 9, 30), groups)
    assert "你覺得有用：1 則" in md and "你覺得沒用：1 則" in md and "還沒評：1 則" in md
    assert "這個月的來源" in md


def test_the_report_lives_next_to_the_books_not_in_the_repo():
    body = func_body(repo_src("services/knowledge_report.py"), "def reports_dir(")
    assert "ks.root()" in body, "跟著書架走，不要自己寫死一個路徑"
    # repo 根目錄本來就有一個 reports/（財務那邊的），所以不能只看名字 —— 要看實際解出來的位置
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert not os.path.abspath(kr.reports_dir()).startswith(os.path.abspath(repo) + os.sep), (
        "知識庫的報告跟書一樣住在書架那顆磁碟，不進 repo、不進 git")


# ── 3. Discord ──────────────────────────────────────────────
def test_the_push_stays_under_the_discord_limit():
    """Discord 超過 2000 字元是整則被退掉，不是截斷 —— 所以組字時就要收尾。"""
    from notifier import DISCORD_MAX_CHARS
    assert kl.PUSH_MAX_CHARS < DISCORD_MAX_CHARS
    groups = [("書%d" % b, [_item(n=i, title_zh="標題" * 20, url="https://a.tw/%d-%d" % (b, i))
                            for i in range(20)]) for b in range(10)]
    text = kl.report_push_text("研究週報 2026-W38", groups, tail="完整報告：" + kr.report_url("weekly-2026-W38"))
    assert len(text) <= kl.PUSH_MAX_CHARS
    assert "完整的在報告檔裡" in text


def test_the_push_carries_clickable_links_and_the_report_url():
    groups = [("錢的三章", [_item()])]
    text = kl.report_push_text("研究週報 2026-W38", groups, tail="完整報告：" + kr.report_url("weekly-2026-W38"))
    assert "https://a.tw/x" in text, "他在手機上看，連結要直接點得到"
    assert "#report/weekly-2026-W38" in text


def test_long_books_are_summarised_not_dumped():
    groups = [("錢的三章", [_item(n=i, url="https://a.tw/%d" % i) for i in range(10)])]
    text = kl.report_push_text("研究週報", groups)
    assert text.count("https://a.tw/") == kl.PUSH_PER_BOOK
    assert "這本還有 7 則" in text


def test_discord_not_google_chat():
    """owner 2026-09-18：「不用送 google chat 送 discord」。"""
    body = func_body(repo_src("services/knowledge_report.py"), "def push(")
    assert "send_discord" in body
    assert "google_chat" not in body and "send_google_chat" not in body


def test_no_webhook_means_no_crash():
    from notifier import send_discord
    assert send_discord("") is False


# ── 4. 空的不發、同一期不重發 ───────────────────────────────
def test_an_empty_period_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(kr, "reports_dir", lambda: str(tmp_path))
    monkeypatch.setattr(kr, "collect", lambda a, b: [])
    rep = kr.build("weekly-2026-W38", dt.date(2026, 9, 14), dt.date(2026, 9, 20))
    assert rep["n"] == 0
    assert not list(tmp_path.iterdir()), "沒東西的一期不該留下一個空檔"


def test_an_empty_period_is_not_pushed(monkeypatch):
    sent = []
    monkeypatch.setattr("notifier.send_discord", lambda t: sent.append(t) or True)
    assert kr.push({"id": "weekly-2026-W38", "n": 0, "groups": []}) is False
    assert sent == []


def test_the_same_period_is_not_sent_twice(monkeypatch, tmp_path):
    monkeypatch.setattr(kr, "reports_dir", lambda: str(tmp_path))
    (tmp_path / "weekly-2026-W38.md").write_text("已經有了", encoding="utf-8")
    built = []
    monkeypatch.setattr(kr, "build", lambda *a: built.append(a) or {"n": 1})
    rep = kr.run_one("weekly-2026-W38", dt.date(2026, 9, 14), dt.date(2026, 9, 20))
    assert built == [] and rep["n"] == 0 and rep.get("skipped")


def test_weekly_fires_on_the_configured_weekday_and_monthly_on_the_first(monkeypatch):
    ran = []
    monkeypatch.setattr(kr, "settings_report", lambda: {"enabled": True, "weekday": 0, "hour": 9})
    monkeypatch.setattr(kr, "run_one", lambda rid, a, b: ran.append(rid) or {"n": 0})
    kr.daily_check_sync(dt.date(2026, 9, 21))          # 週一，不是一號
    assert ran == ["weekly-2026-W38"]
    ran.clear()
    kr.daily_check_sync(dt.date(2026, 9, 22))          # 週二
    assert ran == []
    ran.clear()
    kr.daily_check_sync(dt.date(2026, 10, 1))          # 週四又是一號
    assert ran == ["monthly-2026-09"]
    ran.clear()
    kr.daily_check_sync(dt.date(2026, 6, 1))           # 一號剛好也是週一：兩份都發
    assert ran == ["weekly-2026-W22", "monthly-2026-05"]


def test_the_report_hour_is_not_read_from_finance():
    body = func_body(repo_src("services/knowledge_report.py"), "async def daily_check(")
    assert "hour_getter=" in body, "知識庫的鐘點不在 finance 底下"
    assert "_run_daily_master_task" in body, "master gate 靠共用底座"


def test_every_report_endpoint_calls_the_guard():
    src = repo_src("routers/api_knowledge.py")
    names = re.findall(r'@router\.get\("/reports[^"]*"\)\s*\nasync def (\w+)', src)
    assert len(names) == 2, names
    for name in names:
        assert "_guard(request)" in func_body(src, "async def %s(" % name), name
    assert src.index('@router.get("/reports")') < src.index('@router.get("/{book_id}")'), (
        "排在 /{book_id} 後面的話，「reports」會被當成書的 id")


def test_the_link_opens_the_report_directly():
    """Discord 推的連結點進來要直接看到那一份，不是先掉回書架。"""
    js = repo_src("frontend/js/knowledge/report.js")
    assert "export function openFromHash(" in js
    assert "#report/" in js
    idx = repo_src("frontend/js/knowledge/index.js")
    assert "openFromHash();" in idx and "hashchange" in idx
