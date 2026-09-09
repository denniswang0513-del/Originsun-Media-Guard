# -*- coding: utf-8 -*-
"""core/quote_chat.py — 對話式完成報價的**純規則**（無 I/O、不碰 DB）。

規劃正本：docs/QUOTE_ASSISTANT_PLAN.md。

這裡只做三件事：
  1. `build_prompt()` —— 把「目前的草稿 + 對話歷史 + 這次的話」組成給 claude 的提示。
  2. `parse_reply()` —— 把 claude 回的東西正規化成固定形狀（壞掉就回一個安全的空 patch）。
  3. `item_lines()` / `paste_tokens()` —— 上面兩支共用的小工具。

🔴 **編號規則是前後端的共同契約**：項目照 `flattenQuoteGroups` 的順序（大項目一組接一組）
從 1 編號，`patch.update` / `patch.remove` 用的就是這個編號。前端 `quote-patch.js` 的
`applyQuotePatch` 必須用同一套編號，不然 AI 說「改第 3 項」會改到別人。
所以送出前一定要先把自動存 flush 掉，讓 DB 的順序＝畫面的順序。

🔴 **這裡不決定價格、不算稅**：金額一律走 `routers/crm/quotes._calc_quotation`；
AI 沒把握的單價要放進 `needs_price`，不准自己編一個數字（報價單填錯價的代價遠大於少填一項）。
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

# 全站貼圖層存進內容裡的 token（見 routers/api_paste.py）：paste: + 32 hex + .webp
_PASTE_TOKEN = re.compile(r"paste:([0-9a-f]{32})\.webp")

MAX_QUESTIONS = 5          # 一次最多問幾題（一次問一批，但不要變成審訊）
MAX_HISTORY_TURNS = 20     # 帶進提示的對話輪數上限（再長就砍最舊的）

ROLE_USER = "user"
ROLE_AI = "ai"

_RULES = """你是「源日影像」的報價助理。使用者會貼客戶的零散訊息或截圖，你要幫他把報價單做完。

你的工作方式：
- 每一輪都在改**同一張草稿**，不是重新寫一張。
- 你不知道的單價就留 0 並列進 needs_price，**絕對不要自己編一個數字**。
- 客戶前後矛盾（例如數量改過）以最後一次為準，並在 questions 裡請使用者確認。
- 客戶沒講但這類案子通常要報的（專案管理費、字幕、修改次數、版權、交通、加班），
  列進 questions 問要不要加，不要自作主張加進項目。
- 客戶訊息裡的執行條件（誰校字幕、要不要直式版本、交期、素材誰提供）整理進 terms_add。
- 一次最多問 %d 題，問完就停，不要一題一題來回。
- reply 是講給人聽的：**不要提到 needs_price、patch 這種欄位名稱**（畫面會自己列出來）。
- 下面若有「你的價目」：客戶要的項目跟裡面哪一筆意思相同，就直接帶那個單價，
  並在 reply 說「某項照上次的價帶了」；意思對不上就留 0 進 needs_price，**不要硬套**。
- 每一項都有價、也沒有待確認的問題時，在 reply 裡說「可以了」。

只輸出 JSON，不要 code fence、不要任何解釋文字。格式：
{
  "reply": "給使用者看的話，繁體中文，兩三句",
  "patch": {
    "add": [{"group_name": "", "description": "", "unit": "式", "quantity": 1, "unit_price": 0}],
    "update": [{"n": 3, "unit_price": 6000}],
    "remove": [5]
  },
  "needs_price": ["還沒有單價的項目描述"],
  "questions": ["要回頭問使用者或問客戶的事"],
  "terms_add": ["要加進備註的條件"]
}

patch 的規則：
- add＝新增項目；update 的 n＝下面「目前草稿」裡那個編號，只帶要改的欄位；remove＝要刪掉的編號。
- **不要整包重寫**：沒有要動的項目就不要出現在 patch 裡（使用者可能剛手動改過）。
- 沒有任何要改的就回空的 patch（"add": [], "update": [], "remove": []）。
""" % MAX_QUESTIONS


# ── 模型挑選 ─────────────────────────────────────────────────
# 🔴 `--model <值>` 是**命令列參數**：使用者送什麼就接什麼的話，等於讓前端往
#    claude CLI 塞旗標。一律白名單，名單外一律回預設。
ALLOWED_MODELS = ("fable", "opus", "sonnet", "haiku")
DEFAULT_MODEL = "sonnet"     # 實測：抽結構化這種短活 sonnet 就夠，haiku 反而慢一倍


def pick_model(requested: str = "", fallback: str = "") -> str:
    """(前端選的, settings 的預設) → 真的要用的模型別名。兩個都不合法就用 DEFAULT_MODEL。"""
    for candidate in (requested, fallback):
        name = str(candidate or "").strip().lower()
        if name in ALLOWED_MODELS:
            return name
    return DEFAULT_MODEL


def paste_tokens(text: str) -> list:
    """文字裡的貼圖 token（去重、保持出現順序）。回傳檔名如 `<32hex>.webp`。"""
    seen, out = set(), []
    for hexid in _PASTE_TOKEN.findall(text or ""):
        if hexid in seen:
            continue
        seen.add(hexid)
        out.append(f"{hexid}.webp")
    return out


def strip_paste_tokens(text: str) -> str:
    """把 token 換成人看得懂的字（token 本身對 LLM 沒有意義，圖另外用路徑餵）。"""
    return _PASTE_TOKEN.sub("（截圖）", text or "")


def forget_images(chat: list) -> tuple:
    """對話裡的貼圖 token → 「（截圖已刪除）」，回 `(新的對話, 要刪的檔名清單)`。

    **對話本身留著** —— 那是「這張報價是怎麼談出來的」紀錄；只把圖拿掉。
    token 留著會變成死連結（圖已經不在圖床上了），所以換成看得懂的字。
    純函式：不刪檔、不碰 DB，刪檔的是 core.assets_host.assets_delete。
    """
    names, out, changed = [], [], False
    for m in (chat or []):
        text = m.get("text") or ""
        found = paste_tokens(text)
        if found:
            for n in found:
                if n not in names:
                    names.append(n)
            m = dict(m, text=_PASTE_TOKEN.sub("（截圖已刪除）", text))
            changed = True
        out.append(m)
    return (out if changed else list(chat or [])), names


def item_lines(items: list) -> str:
    """攤平後的項目 → 給 LLM 看的編號清單。編號＝patch 的 n（前後端共同契約）。"""
    if not items:
        return "（目前沒有任何項目）"
    out = []
    for n, it in enumerate(items, 1):
        group = (it.get("group_name") or "").strip() or "未分類"
        price = int(it.get("unit_price") or 0)
        tail = "  ← 待定價" if price <= 0 else ""
        out.append(f"{n} [{group}] {it.get('description') or ''}"
                   f" {it.get('quantity') or 0} {it.get('unit') or '式'} × {price}{tail}")
    return "\n".join(out)


def _terms_lines(terms: str) -> str:
    rows = [t.strip() for t in (terms or "").split("\n") if t.strip()]
    return "\n".join(f"{i}. {t}" for i, t in enumerate(rows, 1)) or "（沒有備註）"


def snapshot(quotation: dict) -> str:
    """目前這張草稿長什麼樣（給 LLM 的上下文）。**不含內部成本**——那個不出這台機器。"""
    q = quotation or {}
    stages = q.get("payment_stages") or []
    stage_text = "／".join(f"{s.get('label')} {s.get('pct')}%" for s in stages) or "（還沒填）"
    return (
        f"客戶：{q.get('client_short_name') or '（未填）'}\n"
        f"專案：{q.get('project_name') or '（未填）'}\n"
        f"規格：{(q.get('spec') or '').strip() or '（還沒填）'}\n"
        f"付款階段：{stage_text}\n\n"
        f"目前草稿的項目（編號就是 patch 要用的 n）：\n{item_lines(q.get('items') or [])}\n\n"
        f"目前的備註：\n{_terms_lines(q.get('terms'))}"
    )


def history_lines(chat: list) -> str:
    """對話歷史 → 提示裡的一段。只取最近 MAX_HISTORY_TURNS 則，token 換成「（截圖）」。"""
    rows = [m for m in (chat or []) if m.get("role") in (ROLE_USER, ROLE_AI)]
    rows = rows[-MAX_HISTORY_TURNS:]
    if not rows:
        return ""
    out = []
    for m in rows:
        who = "使用者" if m.get("role") == ROLE_USER else "你"
        out.append(f"{who}：{strip_paste_tokens(m.get('text') or '').strip()}")
    return "\n".join(out)


def build_prompt(quotation: dict, chat: list, image_paths: Optional[list] = None,
                 price_lines: str = "") -> str:
    """組出這一輪要餵給 claude 的完整提示。

    `chat` 的最後一則就是使用者這次說的話（端點寫入後才呼叫這裡）。
    `image_paths` 是這次貼上的截圖在本機的絕對路徑 —— claude CLI 讀得到本機圖檔。
    `price_lines` 是價目（core.price_book.prompt_lines）；空的就整段不放。
    """
    parts = [_RULES, "\n── 目前的草稿 ──\n" + snapshot(quotation)]
    if price_lines:
        parts.append("\n── 你的價目（過去報過的，對得上就直接帶）──\n" + price_lines)
    hist = history_lines(chat)
    if hist:
        parts.append("\n── 對話 ──\n" + hist)
    for p in (image_paths or []):
        parts.append(f"\n請一併讀這張截圖：{p}")
    parts.append("\n請照上面的 JSON 格式回覆。")
    return "\n".join(parts)


# ── 串流：從還沒完成的 JSON 裡把 reply 撈出來 ──────────────────

_REPLY_HEAD = re.compile(r'"reply"\s*:\s*"')
_ESC = {"n": "\n", "t": "\t", "r": "\r", "b": "", "f": "", '"': '"', "\\": "\\", "/": "/"}


def partial_reply(raw: str) -> str:
    """串流中的部分輸出 → 目前 reply 那串字（畫面邊產邊顯示用）。

    模型是照我們給的欄位順序產的，**reply 排在最前面** —— 所以在 patch／questions
    還沒開始產之前，畫面上就是一句一句長出來的人話，不會看到一堆大括號。
    撈不到（還沒產到 reply、或根本不是 JSON）→ 回空字串，畫面維持「思考中…」。
    """
    m = _REPLY_HEAD.search(raw or "")
    if not m:
        return ""
    s = raw[m.end():]
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c == '"':
            break                      # reply 收尾了
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= n:
            break                      # escape 還沒串完，下一塊再說
        nxt = s[i + 1]
        if nxt == "u":
            if i + 6 > n:
                break
            try:
                out.append(chr(int(s[i + 2:i + 6], 16)))
            except ValueError:
                pass
            i += 6
            continue
        out.append(_ESC.get(nxt, nxt))
        i += 2
    return "".join(out)


# ── 回覆正規化 ────────────────────────────────────────────────

def _strip_fence(s: str) -> str:
    """剝掉 ``` 圍欄。小模型（以及偶爾 claude）就算叫它別包還是會包 —— 剝掉比重試便宜。"""
    t = (s or "").strip()
    if not t.startswith("```"):
        return t
    t = t.split("\n", 1)[-1] if "\n" in t else ""
    if t.rstrip().endswith("```"):
        t = t.rstrip()[:-3]
    return t.strip()


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _str_list(v: Any, limit: int = 20) -> list:
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if str(x).strip()][:limit]


def _clean_add(rows: Any) -> list:
    out = []
    if not isinstance(rows, list):
        return out
    for r in rows:
        if not isinstance(r, dict):
            continue
        desc = str(r.get("description") or "").strip()
        if not desc:
            continue                      # 沒有描述的項目沒有意義，丟掉
        out.append({
            "group_name": str(r.get("group_name") or "").strip(),
            "description": desc,
            "unit": str(r.get("unit") or "式").strip() or "式",
            "quantity": max(_int(r.get("quantity"), 1), 0),
            "unit_price": max(_int(r.get("unit_price"), 0), 0),
        })
    return out


_UPDATABLE = ("group_name", "description", "unit", "quantity", "unit_price")


def _clean_update(rows: Any) -> list:
    """只留「編號 + 真的要改的欄位」。沒帶的欄位不動（不整包覆寫，見檔頭）。"""
    out = []
    if not isinstance(rows, list):
        return out
    for r in rows:
        if not isinstance(r, dict):
            continue
        n = _int(r.get("n"), 0)
        if n < 1:
            continue
        patch = {"n": n}
        for k in _UPDATABLE:
            if k not in r or r[k] is None:
                continue
            if k in ("quantity", "unit_price"):
                patch[k] = max(_int(r[k], 0), 0)
                continue
            val = str(r[k]).strip()
            # 空字串＝「沒有要改」，不是「清空」。描述／單位被洗成空的那一列，
            # 存檔時（前端 filter(it => it.description)）會整個被丟掉 —— AI 回一個
            # 空 description 就等於默默刪掉使用者的項目。group_name 例外：空的
            # 就是「未分類」那一組，是合法的目的地。
            if not val and k != "group_name":
                continue
            patch[k] = val
        if len(patch) > 1:
            out.append(patch)
    return out


def _clean_remove(rows: Any) -> list:
    if not isinstance(rows, list):
        return []
    seen, out = set(), []
    for r in rows:
        n = _int(r, 0)
        if n >= 1 and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def empty_patch() -> dict:
    """一個誰都可以放心改的空 patch。

    刻意是**函式不是常數**：常數配 `dict(EMPTY_PATCH)` 是淺拷貝，三個 list
    還是同一份 —— 呼叫端只要 append 一次，之後每一次「解析失敗回空 patch」
    都會帶著那筆髒資料，而且跨請求活著。
    """
    return {"add": [], "update": [], "remove": []}


def parse_reply(raw: str) -> dict:
    """claude 的原始輸出 → 固定形狀。**壞掉不丟例外**，回一個空 patch 加上人看得懂的話。

    寧可讓使用者看到「我沒看懂，再說一次」，也不要讓半套 patch 去改他的報價單。
    """
    text = _strip_fence(raw)
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("不是物件")
    except (ValueError, TypeError):
        fallback = (text or "").strip()
        return {
            "reply": fallback[:500] or "（我沒有正確回覆，請再說一次）",
            "patch": empty_patch(), "needs_price": [], "questions": [],
            "terms_add": [], "ok": False,
        }

    p = data.get("patch") if isinstance(data.get("patch"), dict) else {}
    return {
        "reply": str(data.get("reply") or "").strip(),
        "patch": {
            "add": _clean_add(p.get("add")),
            "update": _clean_update(p.get("update")),
            "remove": _clean_remove(p.get("remove")),
        },
        "needs_price": _str_list(data.get("needs_price")),
        "questions": _str_list(data.get("questions"), MAX_QUESTIONS),
        "terms_add": _str_list(data.get("terms_add")),
        "ok": True,
    }


def message(role: str, text: str, at: str, **extra) -> dict:
    """對話裡的一則（形狀同公布欄的 conversation：role / text / at）。"""
    row = {"role": role, "text": text, "at": at}
    row.update({k: v for k, v in extra.items() if v})
    return row
