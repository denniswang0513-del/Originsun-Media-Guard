# -*- coding: utf-8 -*-
"""/leave.html 我的假勤獨立頁（owner 2026-09-15「在這裡新增假勤：看到自己的休假總表、提交休假、核准過自己上行事曆」）。

- 工作台最上排「假勤」鈕 → /leave.html（釘在 test_my_workspace_layout）；這裡釘頁本身。
- 表單不抄第二份：頁面載同一支 js/my/cards-hr.js；前面的 js/my/leave-host.js 備好它要的全域＋畫休假總表，
  兩個可選掛鉤在 window（LV_LIMIT／onLeaveRendered），工作台沒這兩個名字也照常。
- 鑰匙＝me_leave（頁面閘門、後端 /me/leave*、工作台那張卡三處同一把）。
- 核准→日曆是後端既有的線（api_hr._calendar_sync_leave），頁面只標「已上日曆／日曆未同步」。
"""
import re

from tests.unit._srcscan import func_body, js_code_only, repo_src

_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def _page():
    return js_code_only(re.sub(r"<!--.*?-->", "", repo_src("frontend/leave.html"), flags=re.S))


def _host():
    return js_code_only(repo_src("frontend/js/my/leave-host.js"))


def test_page_reuses_the_workspace_leave_card():
    p, h = _page(), _host()
    assert '<script src="./js/my/cards-hr.js"></script>' in p, "表單／送單／撤回只有 cards-hr.js 一份"
    assert p.index('src="./js/my/leave-host.js"') < p.index('src="./js/my/cards-hr.js"'), "全域要在 cards-hr.js 之前備好"
    assert "cardLeave(true)" in p
    for g in ("window.LV_LIMIT = 500", "window.onLeaveRendered = onLeaveRendered", "const _resetTodayStrip = () => {}",
              "function makeCard(", "async function mjson(", "async function mfetch("):
        assert g in h, g
    for src in (p, h):
        assert not re.search(r"fetch\(\s*['\"]/api/v1/me/leave['\"]", src), "頁面不自己送單 —— 那是 cards-hr.js 的事"


def test_card_hooks_are_optional_so_the_workspace_is_unaffected():
    js = js_code_only(repo_src("frontend/js/my/cards-hr.js"))
    load = func_body(js, "async function loadLeave(")
    assert '(typeof window.LV_LIMIT === "number" ? "?limit=" + window.LV_LIMIT : "")' in load
    assert 'typeof window.onLeaveRendered === "function"' in load, "掛鉤在 window 上（my.html 沒這名字，no-undef 才過）"
    assert "if (hook) hook(null, e);" in load and "if (hook) hook(LV);" in load, "成功與失敗（409 沒綁人員檔案）都要通知宿主"
    assert "const LV_RECENT_MAX = 20;" in js and ".slice(0, LV_RECENT_MAX).map(_lvRow)" in js, "卡片只列最近的，整本在總表"


def test_gate_is_me_leave_everywhere():
    p = _page()
    assert 'const canMine = isAdmin || mods.includes("me_leave");' in p
    assert "me_petty" not in p and "me_finance" not in p
    api = repo_src("routers/api_me.py")
    assert 'require_bound_staff(request, "me_leave")' in func_body(api, "async def my_leave_summary(")
    my = repo_src("frontend/js/my/shell.js")
    assert 'has("me_leave") && { label: "假勤", href: "/leave.html" }' in my


def test_partners_see_everyone_read_only():
    """owner 2026-09-15「合夥人是管理層級可以看到大家的休假狀態」：管理員／hr_leave／合夥人（finance_partner）
    在 /leave.html 多一區「大家的休假」—— 只讀 /hr/balances 與 /hr/leave，不放核准鈕；後端兩支唯讀端點同一組鑰匙。"""
    p, h = _page(), _host()
    assert 'const canTeam = isAdmin || mods.includes("hr_leave") || mods.includes("finance_partner");' in p
    assert 'if (canTeam) { $("lv-team").style.display = ""; loadTeamLeave(); }' in p
    assert 'if (!canMine && !canTeam) { _show("noperm-view"); return; }' in p, "只有管理層鑰匙、沒有 me_leave 的合夥人也進得來"
    assert '"/api/v1/hr/balances"' in h and '"/api/v1/hr/leave?status="' in h
    assert "/approve" not in h and "/reject" not in h and "/hr/leave/" not in h and 'method: "POST"' not in h, "合夥人頁只看不動"
    api = repo_src("routers/api_hr.py")
    assert 'LEAVE_VIEWERS = ("hr_leave", "finance_partner")' in api
    for fn in ("async def list_leave(", "async def all_balances("):
        assert "check_admin_or_module(request, *LEAVE_VIEWERS)" in func_body(api, fn), fn


def test_summary_limit_is_clamped():
    api = repo_src("routers/api_me.py")
    body = func_body(api, "async def my_leave_summary(")
    assert "limit: int = 20" in api.split("async def my_leave_summary(")[1].split(")")[0]
    assert "max(1, min(int(limit), LEAVE_SUMMARY_LIMIT_MAX))" in body
    assert "LEAVE_SUMMARY_LIMIT_MAX = 500" in api


def test_history_counts_only_approved_and_marks_calendar_sync():
    h = _host()
    assert 'if (r.status === "已核准") t[r.leave_type]' in h, "小計只算已核准（待審／退回／撤回不占假）"
    assert "r.google_event_id ?" in h and "已上日曆" in h and "日曆未同步" in h
    assert 'r.status !== "已核准") return ""' in h, "只有已核准的才談日曆"
    assert "LV." not in h and "LV &&" not in h, "總表只用掛鉤傳進來的 lv，不摸 cards-hr.js 的全域 LV"


def test_wip_note_only_when_the_ledger_is_empty():
    """2026-09-15 起時數帳會逐人建：只對特休／補休全 0 的人講「還沒建」，不再對每個人說開發中。"""
    js = js_code_only(repo_src("frontend/js/my/cards-hr.js"))
    assert "function _lvLedgerEmpty(" in js
    render = func_body(js, "function renderLeave(")
    assert "const ledgerEmpty = _lvLedgerEmpty(bal);" in render
    assert '${ledgerEmpty ? `<div class="wip-note">' in render
    assert 'badge.style.display = ledgerEmpty ? "" : "none"' in render


def test_no_emoji_in_page_text():
    for src in (_page(), _host()):
        assert not _EMOJI.search(src), "UI 文字不放 emoji"


def test_rules_card_mirrors_notion_rulebook():
    """owner 2026-09-15「這裡多一個請補修規章」：卡片下方一張靜態規章卡（抄自 Notion「請補修規章」）。
    數字要跟程式裡的規則一致：最晚一週前（NOTICE_DAYS=7）、病假一年 30 天（SICK_CAP_DAYS）、休假前兩天消假。"""
    from core.leave_logic import NOTICE_DAYS, SICK_CAP_DAYS
    raw = repo_src("frontend/leave.html")
    assert 'id="lv-rules"' in raw and "請補休規章" in raw
    assert NOTICE_DAYS == 7 and "最晚 1 週前提出申請" in raw
    assert SICK_CAP_DAYS == 30 and "1 年內合計最多 30 天" in raw
    assert "休假前兩天可消假" in raw and "颱風假公告當日無法消假" in raw
    for t in ("特休", "補休", "病假", "事假", "婚假", "喪假", "颱風假", "消假", "請假時間點"):
        assert f"<b>{t}" in raw, t
    assert raw.index('id="lv-card-host"') < raw.index('id="lv-rules"') < raw.index('id="lv-history"'), "規章在自己那張卡下面、總表之前"
