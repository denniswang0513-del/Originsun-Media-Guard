"""services/quote_analyzer.py — 多份報價單的 AI 差異分析（Markdown）。

輸入兩路：外部上傳的報價檔（extract_text 抽文字）+ CRM 報價各版的結構化資料
（含項目明細）。一檔抽不出文字**不整批死** —— 標註「未納入」後繼續，並要求
輸出裡如實列出（掃描 PDF 是常態，不是例外）。

🔴 **資料裡沒有的不准編**。比照企劃書生成的「（未填）」原則：缺的一律寫
「（資料中未見）」。報價分析比企劃書更碰不得杜撰 —— 這裡的數字會被拿去
跟客戶談判。

🔴 報價含金額是內部敏感資料：這個模組的輸出只寫回 preprod_quote_analyses，
任何內容都不准流進公開 `?t=` token 端點。
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# 單檔文字上限。報價 xlsx 可能整本套公式模板幾千列，全塞會撐爆 prompt；
# 截斷時在輸入裡明講，讓 AI（與看 inputs 的人）知道不是完整內容。
_PER_FILE_CHARS = 8000

_PROMPT = """你是一位影像製作公司的製片總監，正在替團隊分析**同一個提案的多份報價單**。

────────────── 提案背景 ──────────────
{background}
────────────── CRM 報價系統裡的各版報價 ──────────────
{crm_section}
────────────── 外部上傳的報價檔（抽出的文字） ──────────────
{files_section}
────────────── 資料結束 ──────────────

請輸出**繁體中文 Markdown** 的分析，依序包含這幾節：

## 各版本差異摘要
金額變化（總價/折扣/稅額）、項目的增減與單價調整。逐版對照，說得出「哪一版
改了什麼」。

## 談判軌跡
如果從版本演進**看得出來**談判方向（降價讓步、項目換血、付款條件調整）就描述；
看不出來就直接寫「（資料中未見明顯談判軌跡）」，不要硬編故事。

## 與提案預算範圍的對照
拿各版總價對照提案的預算範圍。提案沒填預算範圍就寫「（資料中未見預算範圍）」。

## 注意事項
給團隊的提醒：版本間不一致的地方、有效期限、付款節點、疑似漏項。

🔴 鐵則：
1. **只用上面實際有的資料寫。** 資料裡沒有的數字、日期、項目、條款一律不准編；
   缺的就寫「（資料中未見）」。
2. 有檔案標註「未能抽出文字」時，必須在分析開頭如實聲明該檔未納入本次分析。
3. 直接輸出 Markdown 本文，不要開場白、不要程式碼圍欄。
"""


def build_background(prop_info: dict) -> str:
    """提案的基本盤（title/status/budget_range）→ prompt 段落。"""
    return "\n".join([
        f"- 提案標題：{prop_info.get('title') or '（資料中未見）'}",
        f"- 提案狀態：{prop_info.get('status') or '（資料中未見）'}",
        f"- 預算範圍：{prop_info.get('budget_range') or '（資料中未見）'}",
    ])


def build_crm_section(quotations: list) -> str:
    """CRM 報價各版 → 文字。`quotations` 是端點收集好的結構化 dict
    （含 items）—— 這裡不打 DB，方法論同 brief_writer：service 只管拼 prompt。"""
    if not quotations:
        return "（這個專案在 CRM 報價模組裡沒有任何報價）"
    out = []
    for q in quotations:
        head = [f"### v{q.get('version')}（狀態：{q.get('status') or '草稿'}）"]
        head.append(f"- 報價日：{q.get('quote_date') or '（資料中未見）'}")
        head.append(f"- 有效期限：{q.get('valid_until') or '（資料中未見）'}")
        head.append(f"- 小計 {q.get('subtotal', 0)}｜折扣 {q.get('discount', 0)}"
                    f"｜稅額 {q.get('tax_amount', 0)}｜含稅總計 {q.get('total', 0)}")
        if q.get("final_price") is not None:
            head.append(f"- 最終報價：{q['final_price']}")
        stages = q.get("payment_stages") or []
        if stages:
            head.append("- 付款節點：" + "、".join(
                f"{s.get('label') or ''} {s.get('pct') or ''}%" for s in stages))
        items = q.get("items") or []
        if items:
            head.append("- 項目明細：")
            head.extend(
                f"  - [{it.get('group_name') or ''}] {it.get('description') or ''}"
                f"：{it.get('quantity', 0)} {it.get('unit') or '式'}"
                f" × {it.get('unit_price', 0)} = {it.get('amount', 0)}"
                + (f"（{it['note']}）" if (it.get('note') or '').strip() else "")
                for it in items)
        out.append("\n".join(head))
    return "\n\n".join(out)


def build_files_section(extracted: list) -> str:
    """抽好文字的上傳檔 → prompt 段落。`extracted` 元素：
    {version, filename, note, text, error}（text/error 擇一有值）。"""
    if not extracted:
        return "（沒有上傳任何外部報價檔）"
    out = []
    for f in extracted:
        head = f"### 上傳 v{f.get('version')}：{f.get('filename')}"
        if (f.get("note") or "").strip():
            head += f"（備註：{f['note'].strip()}）"
        if f.get("error"):
            out.append(f"{head}\n🔴 未能抽出文字，本檔未納入分析：{f['error']}")
            continue
        text = f.get("text") or ""
        cut = ""
        if len(text) > _PER_FILE_CHARS:
            text = text[:_PER_FILE_CHARS]
            cut = "\n（⚠️ 內容過長，只截取前段 —— 後段未納入）"
        out.append(f"{head}\n{text}{cut}")
    return "\n\n".join(out)


async def analyze_quotes(analysis_id: str, *, prop_info: dict, files: list,
                         quotations: list) -> tuple[bool, str]:
    """跑分析並寫回那一筆。`files` 元素：{version, filename, note, path}
    （path = 本機可開的絕對路徑）。回 `(成功, 訊息)`。"""
    from core.doc_text import extract_text

    extracted = []
    for f in files:
        # path 空 = 端點解析不到（檔案被搬走/資產夾搆不到）—— 標註後繼續
        if not f.get("path"):
            extracted.append({"version": f.get("version"),
                              "filename": f.get("filename"),
                              "note": f.get("note") or "", "text": "",
                              "error": "檔案不在資產夾裡（可能被移動或刪除，或 NAS 搆不到）"})
            continue
        # 同步 CPU/IO-bound 解析 → to_thread；一檔失敗標註後繼續，不整批死
        try:
            text, err = await asyncio.to_thread(extract_text, f["path"])
        except Exception as e:                       # noqa: BLE001 — 壞檔不該殺掉整批
            text, err = "", f"{type(e).__name__}: {e}"
        extracted.append({"version": f.get("version"), "filename": f.get("filename"),
                          "note": f.get("note") or "", "text": text, "error": err})

    prompt = _PROMPT.format(
        background=build_background(prop_info),
        crm_section=build_crm_section(quotations),
        files_section=build_files_section(extracted),
    )
    from services.website.seo_runner import _call_claude, strip_fence
    out, err = await _call_claude(
        prompt, on_start=lambda: _save(analysis_id))   # 見 core.bg_status 檔頭
    if not out or not out.strip():
        return await _save(analysis_id, status="failed",
                           error=err or "claude 沒有回應")
    content = strip_fence(out).strip()
    await _save(analysis_id, content=content, status="ok", error=None)
    logger.info("[quote_analyzer] %s 分析完成（%d 字）", analysis_id, len(content))
    return True, content


async def _save(analysis_id: str, *, content=None, status=None, error=...) -> tuple:
    """部分更新。**一個參數都不給＝只蓋 updated_at**（開工的時間戳，
    core.bg_status 的 stale 判定靠它）。"""
    from core.db_guard import db_factory_or_503
    from db.models import PreprodQuoteAnalysis
    from routers.crm._shared import _now
    factory = db_factory_or_503()
    async with factory() as session:
        a = await session.get(PreprodQuoteAnalysis, analysis_id)
        if not a:
            return False, "找不到這一筆分析"
        if content is not None:
            a.content = content
        if status is not None:
            a.status = status
        if error is not ...:
            a.error = error
        a.updated_at = _now()
        await session.commit()
    return (status == "ok"), (error if error is not ... and error else "")
