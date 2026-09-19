# -*- coding: utf-8 -*-
"""🔴 `app.routes` 必須是**攤平**的路由清單（不是巢狀的 router）。

為什麼單獨一支測試：我們有好幾條安全不變量是靠「把 `app.routes` 掃一遍」在守的 ——
公開容器只准白名單那幾支（test_public_surface）、帶錢的端點都要有守衛
（test_money_visibility）、NAS 那份不准有主控的端點（test_office_surface）。

2026-09-19 升級依賴時撞到：**fastapi 0.141 起 `include_router` 不再把子路由攤平**
到 `app.routes`，而是放一個 `_IncludedRouter` 物件。那一版底下，上面那幾支掃出來
是**空集合** —— 它們當時是紅的（所以有被發現），但下一個人只要把 EXPECTED 改成
空集合就「修好」了，那些守衛從此全瞎。

所以這裡直接釘住前提本身：掃得到東西，而且掃得到的是我們自己的 API。
真的要升 fastapi 的話，先把列舉路由的地方改成會往巢狀裡走，再讓這支綠回來。
"""


def _paths(app):
    return [getattr(r, "path", "") or "" for r in app.routes]


def test_the_main_app_routes_are_flat():
    import main
    api = [p for p in _paths(main.app) if p.startswith("/api/v1")]
    assert len(api) > 500, (
        f"`main.app.routes` 只掃到 {len(api)} 支 /api/v1 路由 —— 路由沒有被攤平。"
        "檢查 fastapi 版本（requirements_agent.txt 釘在 0.13x 就是為了這件事）。")


def test_the_public_app_routes_are_flat():
    """對外那一份更重要：它的白名單測試等於「這台機器對外開了什麼」。"""
    import main_website
    paths = _paths(main_website.app)
    assert any(p.startswith("/api/v1/crm/public") for p in paths), (
        "對外 app 掃不到任何 /api/v1/crm/public 路由 —— 白名單測試會變成在比對空集合")


def test_the_office_app_routes_are_flat():
    import main_office
    api = [p for p in _paths(main_office.app) if p.startswith("/api/v1")]
    assert len(api) > 50, f"NAS 那份只掃到 {len(api)} 支 —— 同上"
