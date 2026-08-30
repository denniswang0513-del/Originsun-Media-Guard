# -*- coding: utf-8 -*-
"""跨頁籤 import 只准走白名單裡的共用庫。

CLAUDE.md 原本寫「每個頁籤嚴格隔離」。2026-08-31 體檢實測：19 對頁籤、58 條
跨頁籤 import —— 但它們幾乎全部指向 16 個「住在頁籤資料夾裡的共用庫」
（crm-utils 被 7 個其他頁籤用、website-utils 7 個、proposals 的 prop-* 家族
被專案詳情引用 14 條）。聲明與事實不符的代價不是壞掉，是**誤判半徑**：
照文件的心智模型改 crm-utils 以為只動 CRM，實際七個頁籤跟著動。

處置（2026-08-31 定案）＝承認現況、釘住邊界，而不是搬檔：把 16 個共用庫列成
白名單，**頁面邏輯之間仍然零互相依賴**（那才是隔離真正要守的東西）。搬進
js/shared/ 要動 28+ 個 import 路徑，而帳務子視圖的 lazy-load 路徑本來就脆
（CLAUDE.md：import 路徑必須用 location.origin 絕對路徑）—— 收益相同、風險
差一個量級，所以不搬。

🔴 想 import 白名單以外的頁籤檔？三個選項，照序想：
  1. 那段邏輯本來就該住 js/shared/ → 搬去那裡（新共用從一開始就放對地方）
  2. 它真的是另一個頁籤的功能 → 用事件/URL 導頁，不要直接 import 它的模組
  3. 都不是、確定要共用 → 加進白名單，**連同「誰在用、為什麼」一起寫**
"""
import collections
import pathlib
import re

# 目標檔（tab/檔名）→ 存在理由。加新條目要寫清楚為什麼。
ALLOWED = {
    # ── 四個「假頁籤真共用庫」（體檢點名的那批）──
    "crm/crm-utils.js":            "crmFetch/esc/inline 編輯那套 —— 事實上的第二個 shared/",
    "website/website-utils.js":    "官網後台的 fetch/token 殼 —— 掛在官網群的頁籤都用",
    "finance/fin-utils.js":        "財務值域與格式化（TREATMENT_OPTIONS 正本的鏡射）",
    # ── proposals 家族：專案詳情「提案企劃」分頁 = 直接嵌提案庫的視圖 ──
    #   （提案=專案合體（方案A）的刻意設計：同一份視圖兩處掛載，不是抄一份）
    "proposals/prop-fetch.js":     "提案 API 殼（crm 專案詳情 + 參考影片庫共用）",
    "proposals/prop-const.js":     "提案常數",
    "proposals/prop-actions.js":   "提案動作（成案/換綁）",
    "proposals/staff-view.js":     "派工視圖（crm 專案財務分頁嵌用）",
    "proposals/brief-templates.js": "企劃範本庫視圖",
    "proposals/folder-view.js":    "提案資產夾視圖",
    "proposals/pins-panel.js":     "釘選面板",
    "proposals/pins-store.js":     "釘選狀態",
    "proposals/delivery-view.js":  "完稿結案視圖",
    "proposals/ref-pills.js":      "參考影片八族 pill（references 頁共用）",
    # ── 單對互嵌 ──
    "drone_meta/drone_meta_editor.js": "空拍排列編輯器（串接頁嵌用 —— 製作串帶對齊空拍寫入）",
    "concat/concat_editor_modal.js":   "串接編輯 modal（空拍頁嵌用 —— 同上，一對雙向）",
    "crm/showcase-overlay.js":     "作品編輯 overlay 殼（官網作品管理嵌用）",
}


def _edges():
    """{目標檔: {引用它的頁籤}}，只算跨頁籤的、排除 js/shared/。"""
    hub = collections.defaultdict(set)
    root = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "tabs"
    for p in root.rglob("*.js"):
        tab = p.relative_to(root).parts[0]
        src = p.read_text(encoding="utf-8", errors="replace")
        # 🔴 也要抓裸 `import '...'`（副作用 import）—— 體檢第一版只抓
        # `import x from`，concat ↔ drone_meta 那對雙向就是這樣漏掉的
        for m in re.finditer(r"import\s+(?:[^;'\"]*?from\s+)?['\"]([^'\"]+)['\"]", src):
            mm = re.search(r"(?:tabs/|\.\./)+([a-z_]+)/([\w.-]+\.js)", m.group(1))
            if not mm or "js/shared/" in m.group(1):
                continue
            other = mm.group(1)
            if other != tab and (root / other).is_dir():
                hub[f"{other}/{mm.group(2)}"].add(tab)
    return hub


def test_cross_tab_imports_stay_inside_the_allowlist():
    hub = _edges()
    stray = {t: sorted(tabs) for t, tabs in hub.items() if t not in ALLOWED}
    assert not stray, (
        f"白名單以外的跨頁籤 import：{stray}。"
        "共用邏輯放 js/shared/、要用別的功能就導頁；真的要共用才進 ALLOWED"
        "（連同誰在用、為什麼一起寫）—— 見本檔檔頭的三個選項。")


def test_the_scan_actually_sees_the_known_hubs():
    """🔴 掃不到東西要當失敗 —— import 寫法變了之後這個邊界就沒人守了。"""
    hub = _edges()
    assert len(hub) >= 10, f"只掃到 {len(hub)} 個跨頁籤目標，掃描本身可能壞了"
    assert len(hub.get("crm/crm-utils.js", set())) >= 5, \
        "crm-utils 的引用者少於 5 個頁籤？（掃描壞了，或它真的被升格搬走了 —— 後者請整份重審這個白名單）"


def test_dead_allowlist_entries_are_removed():
    """沒人引用的白名單條目要拿掉 —— 留著等於預先豁免了未來任何一條依賴。"""
    hub = _edges()
    dead = sorted(t for t in ALLOWED if t not in hub)
    assert not dead, f"這些白名單條目已經沒有任何跨頁籤引用，請移除：{dead}"


def test_page_logic_files_are_not_imported_across_tabs():
    """隔離真正要守的底線：**頁面主檔**（<tab>/<tab>.js，掛 initTab 的那支）
    不准被別的頁籤 import —— 那是把整個頁面的初始化邏輯拖進別人的載入路徑。"""
    hub = _edges()
    bad = sorted(t for t in hub
                 if t.split("/")[1] == t.split("/")[0] + ".js")
    assert not bad, f"頁面主檔被跨頁籤 import 了：{bad}"
