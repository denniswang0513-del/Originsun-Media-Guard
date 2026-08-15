# -*- coding: utf-8 -*-
"""會議記錄的收合（預設收起）與單篇唯讀分享（owner 2026-08-15）。

四件事只在畫面或公開面上存在，後端內部測試看不到：

  A. 預設收起 + 箭頭展開；**新增的那張要自動展開**（建了卻要自己找到再點
     箭頭才能打字，是把預設收合變成使用者的麻煩）。
  B. 分享 → 面板給連結；**匿名**打公開端點拿得到、而且**只有四個欄位**
     （逐字稿、提案歸屬那些多一個 key 就是洩漏）。
  C. 公開頁 /meeting-note.html 真的畫得出來（NAS 容器 serve 的路徑，
     import 閉包壞了只會靜默空白）。
  D. 停用 → 舊連結立即 404。

自建自刪（dev/test 庫限定）。
"""
import pytest

from .conftest import HTTP, flow_case   # plan_page 是 conftest fixture，自動注入

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def case(real_server, e2e_admin_token, dev_db_only):
    with flow_case(real_server["base_url"], e2e_admin_token, "會議分享") as c:
        yield c


@pytest.fixture(scope="module")
def pg(plan_page, case, e2e_admin_token):
    """開這筆提案的會議記錄分頁，建好一張卡。"""
    page = plan_page(f"?pid={case['prop_id']}")
    page.wait_for_selector("#tab-meeting", state="attached", timeout=30000)
    page.click("#tab-meeting")
    page.wait_for_selector("#meeting-host .mv-bar", timeout=30000)
    return page


def test_new_card_opens_and_default_is_collapsed(pg):
    """A：新增的卡自動展開；重掛之後全部收起（預設收合）。"""
    pg.click("#meeting-host [data-add]")
    pg.wait_for_selector("#meeting-host .mv-card:not(.closed)", timeout=30000)
    # 展開的卡看得到本文欄
    assert pg.locator("#meeting-host .mv-card:not(.closed) .mv-body").is_visible()

    # 收起 → 本文欄看不到、但主題欄（head 上）仍看得到也仍可編
    pg.click("#meeting-host .mv-card:not(.closed) [data-tg]")
    card = pg.locator("#meeting-host .mv-card.closed").first
    assert not card.locator(".mv-body").is_visible()
    assert card.locator(".mv-title").is_visible()

    # 再展開 → 本文欄回來，而且 autoGrow 量得到高度（收著時是 0）
    card.locator("[data-tg]").click()
    body = pg.locator("#meeting-host .mv-card:not(.closed) .mv-body").first
    assert body.is_visible()
    assert body.evaluate("el => el.clientHeight") > 20, "展開後 textarea 沒補量高度"


def test_share_roundtrip_minimal_fields_then_revoke(pg, case):
    """B+C+D：分享 → 匿名讀（恰好四欄）→ 公開頁畫得出來 → 停用 → 404。"""
    base = case["base"]
    # 先給這張卡一點內容（打進 head 上的主題欄；autosave 會存）
    card = pg.locator("#meeting-host .mv-card").first
    card.locator(".mv-title").fill("分享測試會議")
    card.locator(".mv-title").blur()

    card.locator("[data-shr]").click()
    pg.wait_for_selector("#meeting-host .mv-shlink", timeout=30000)
    url = pg.input_value("#meeting-host .mv-shlink")
    assert "/meeting-note.html?t=" in url, url
    token = url.split("t=")[-1]
    # 🔴 短 token（owner 2026-08-15「網址好長」）：舊版鑄的是完整 JWT，光
    # token 就 400+ 字元。公開端點從來沒解過它（拿整串字查 DB），所以那三段
    # base64 是純浪費 —— 這行釘住「別又改回 new_share_token」。
    assert len(token) <= 24 and "." not in token, f"token 又變長了：{len(token)} 字 {token[:40]}"
    assert pg.locator("#meeting-host .mv-card [data-shr]").first.text_content() == "分享中"

    # 匿名（不帶 Authorization）打公開端點 —— 拿得到，而且**恰好**四個欄位
    r = HTTP.get(f"{base}/api/v1/proposals/shared/meeting/{token}")
    assert r.status_code == 200, r.text[:200]
    note = r.json()["note"]
    assert set(note) == {"met_at", "title", "attendees", "content"}, \
        f"公開欄位漂了（多的就是洩漏）：{sorted(note)}"

    # 公開頁本體：匿名新分頁真的畫得出主題
    guest = pg.context.new_page()
    try:
        guest.goto(f"{base}/meeting-note.html?t={token}", timeout=60000)
        guest.wait_for_selector(".card h1", timeout=30000)
        assert "分享測試會議" in guest.locator(".card h1").text_content()
    finally:
        guest.close()

    # 停用 → 匿名端點立即 404（逐字比對，比不上就擋）
    pg.on("dialog", lambda d: d.accept())
    pg.click("#meeting-host [data-shoff]")
    pg.wait_for_function(
        "() => !document.querySelector('#meeting-host .mv-shlink')", timeout=15000)
    assert HTTP.get(f"{base}/api/v1/proposals/shared/meeting/{token}").status_code == 404
    assert pg.locator("#meeting-host .mv-card [data-shr]").first.text_content() == "分享"


def test_typing_does_not_scroll_the_page_up(pg):
    """🔴 2026-08-15 使用者實際回報：長記錄裡打字，畫面每個鍵擊都往上浮。

    根因在共用件 autoGrow：`height='auto'` 那一瞬間文件變矮 → 捲動容器被
    clamp 往上跳 → 高度設回來之後 scrollTop 回不來。修法是先記下每層祖先的
    捲動量、改完高度塞回去 —— 這支守的就是那個「塞回去」。
    """
    body = pg.locator("#meeting-host .mv-card:not(.closed) .mv-body").first
    # 灌長內容讓頁面真的需要捲動（fill 觸發一次 input → autoGrow 長高）
    body.fill("第一行\n" * 120)
    # 游標先就位、頁再捲到底 —— `before` 要在**最後一個會捲動的準備動作之後**
    # 才讀（Playwright 的 click 會先把元素捲進視野，量在它前面等於量到測試
    # 自己的捲動）
    body.click()
    pg.keyboard.press("Control+End")
    pg.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    before = pg.evaluate("window.scrollY")
    assert before > 100, f"頁面根本沒捲動，測不到跳動：scrollY={before}"

    pg.keyboard.type("繼續打字")     # 每個鍵擊都走一次 autoGrow
    after = pg.evaluate("window.scrollY")
    # 往下（內容變長跟著捲）可以；往上浮超過一行字的高度就是那個 bug 回來了
    assert after >= before - 24, f"打字讓畫面往上浮了：{before} → {after}"
