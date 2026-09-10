"""
notifier.py
───────────
Sends task completion notifications via:
  - Google Chat (Incoming Webhook)

（LINE Notify 服務已於 2025-03-31 終止，通道於 2026-07-07 移除；
 settings.json 裡殘留的 line_notify_token / channel line 鍵會被忽略。）

Webhook URL and Message Templates are read from settings.json or environment variables.

Usage:
  from notifier import notify_tab            # 同步 context
  from notifier import notify_tab_async      # async context（to_thread 包好）
  notify_tab("backup_success", project_name="20260302", file_count=100, total_size=102400)

（舊的 notify_all / send_google_chat / _build_message 於 2026-07-07 移除 —
 與 notify_tab 完全重疊的前代路徑，唯一 caller worker.py 已改用 notify_tab_async。）
"""

import os
import json

from utils.formatting import fmt_size

_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

# 機隊→master 的告警轉寄金鑰。**不能用 jwt_secret**：那是每台機器各自產的
# （CLAUDE.md「不要動的地方」已載明機隊各自的 jwt_secret 不共用），拿它當共用
# 金鑰的結果是 agent 送自己的、master 比自己的，永遠 403 —— 2026-09-10 查
# 備檔電腦（FILETRANSFER）備份失敗時發現：三次 task_failed 告警全被 master 擋掉，
# 🔴 email 一封都沒寄出去，失敗只有盯著螢幕才看得見。
#
# 跟 `/api/v1/internal/restart` 同一套：固定字串隨 OTA 包發到每台機器，因此**不是**
# 秘密，安全性靠接收端的 `via_cloudflare` 把公網那條路關掉。
INTERNAL_ALERT_KEY = "originsun-internal-alert"

# 🔴 級告警：除了 Chat 也寄 email。刻意**不含** `*_success` 那些日常完成通知 ——
# 機隊 7 台同時跑任務時會把信箱淹掉，淹掉的信箱等於沒有告警。
# `*_recovered` 有進來是為了閉環：收到 offline 的信，就該收到恢復的信。
CRITICAL_ALERTS = frozenset({
    "task_failed", "rebuild_failed", "backup_failed", "deploy_failed",
    "db_offline", "db_recovered",
    "agent_offline", "agent_recovered",
    "ai_runner_failed",
    "archive_failed",
    "dispatch_host_lost",
})


def _load_settings() -> dict:
    if os.path.exists(_SETTINGS_PATH):
        try:
            with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def notify_tab(template_key: str, **variables) -> None:
    """
    Generic notification for any tab.
    Looks up the message template by template_key from settings.json,
    substitutes all provided keyword variables, and sends to enabled channels.

    The tab_key (e.g. 'backup', 'transcode') is derived from template_key by
    stripping the '_success' suffix. Channel toggles are read from
    notification_channels in settings.json.
    """
    settings = _load_settings()
    notif = settings.get("notifications", {})
    tpls = settings.get("message_templates", {})
    channels = settings.get("notification_channels", {})

    # Derive tab key: "backup_success" → "backup"
    tab_key = template_key.replace("_success", "")
    tab_channels = channels.get(tab_key, {"gchat": True})
    send_gchat = tab_channels.get("gchat", True)
    is_critical = template_key in CRITICAL_ALERTS

    # 兩條通道都不會發 → 連組訊息都省了
    if not send_gchat and not is_critical:
        return

    # Default fallback templates per tab
    _defaults = {
        "backup_success":    "✅ 【備份成功】專案 {project_name} 已完成！\n📂 檔案數：{file_count} | 💾 容量：{total_size}",
        "report_success":    "📊 【報表生成】{project_name} 視覺報表已出爐！\n🔗 點此查看：{report_url}",
        "transcode_success": "🎬 【轉檔完成】{project_name} 已轉檔完成！\n📂 輸出至：{dest_dir} | 共 {file_count} 個檔案",
        "concat_success":    "🔗 【串接完成】{output_file} 已輸出！\n📁 儲存位置：{dest_dir}",
        "verify_success":    "🔍 【比對完成】{project_name}\n✅ 通過：{pass_count} | ❌ 失敗：{fail_count} | 共 {total_count} 個",
        "transcribe_success":"🎙️ 【逐字稿完成】{project_name} 已生成！\n📂 輸出至：{dest_dir} | 共 {file_count} 個檔案",
        "drone_watcher_success":"🛸 【空拍排程】已掃描 {folder_count} 個資料夾、{file_count} 個檔案（{trigger}）\n⏱ 耗時：{duration}",
        "inquiry_received":  "📬 【官網新詢問】#{id} 來自 {name}\n📧 Email：{email}\n📱 電話：{phone}\n🏢 公司：{company}\n💼 服務類型：{service_type}\n💰 預算：{budget_range}\n\n訊息：\n{message}",
        "media_log_upload":  "【影像紀錄】{project_name} 新收 {file_count} 個檔案（{uploaders}）",
        "user_registered":   "【新帳號註冊】{username}（{email}）已完成公司驗證註冊 — 權限待管理員開通（右上角選單 → 使用者管理）",
        # 兼職排班（owner 2026-09-08）：正職幫兼職排了卡 → 貼群，讓兼職知道（無 emoji）
        "plan_parttime":     "【兼職排班】{planner} 幫 {staff_name} 排了 {count} 項（{days}）\n→ 員工頁 今天與這週 › 我的一週",
        # 失敗告警（channel key 即 template_key 本身，預設 gchat=True → webhook 有設就會發）
        "task_failed":       "🔴 【任務失敗】{task_type}｜{project_name}\n⚠️ {error}\n🖥️ 機器：{hostname}",
        "rebuild_failed":    "🔴 【官網重建失敗】\n⚠️ {error}\n請到官網管理 Tab 查看完整 log 並手動「立即重建」",
        "backup_failed":     "🔴 【每日備份失敗】\n⚠️ {error}\n請檢查 master→NAS SSH 與 Google Drive 憑證",
        "db_offline":        "🔴 【資料庫斷線】PostgreSQL（{db}）連線中斷 — {hostname}\n已切換 JSON fallback，每 60 秒自動重連中",
        "db_recovered":      "🟢 【資料庫恢復】PostgreSQL（{db}）連線已恢復 — {hostname}",
        "deploy_failed":     "🔴 【部署到生產失敗】v{version}\n⚠️ {detail}",
        "dispatch_host_lost":"🔴 【分散式轉檔失聯】{project_name}\n🖥️ 主機：{hosts}（{reason}）\n📂 未完成 {file_count} 支 — {action}",
        "log_oversize":      "🟠 【Log 檔超大】{hostname} 的 {filename} 已達 {size_mb}MB\nlog 只在重啟時輪替 — 建議找空檔重啟該機 agent",
        "agent_offline":     "🔴 【機隊斷線】{name}（{url}）連續 {misses} 次健康檢查無回應\n可能：關機/睡眠/網路/agent 掛掉 — 請檢查該機器",
        "agent_recovered":   "🟢 【機隊恢復】{name}（{url}）已重新上線",
        "archive_failed":    "🔴 【參考影片封存】{title}\n⚠️ {error}\n連續 {tries} 次失敗（含 yt-dlp 自我更新後重試）— 到片庫檢視或手動重試",
        "ai_runner_failed":  "🔴 【AI Runner 失效】{kind} 連續 {fails} 次呼叫 claude 失敗\n⚠️ {error}\n可能：Max 訂閱到期/登出/CLI 更新 — 到 master 跑一次 `claude` 檢查",
        "social_daily":      "📣 【今日社群任務】{count} 篇文稿待審\n{titles}\n→ 後台 官網管理 › 社群工作台",
        "loan_payment_due":  "🏦 【貸款繳款提醒】近期有 {count} 筆貸款款項待繳：\n{lines}\n→ 後台 財務管理 › 銀行貸款",
        "monthly_finance_report": "🧾 【上月財務快報】{month} 營收 {revenue} / 成本 {cost} / 淨利 {net}{warn}\n→ 後台 財務管理 › 財務三表",
        "vat_filing_due":    "🧾 【營業稅申報提醒】{period} 期預估應繳 {estimate} 元，記得申報\n💼 這期記帳費 {fee} 元（會計代繳，跟稅款同一筆匯出 → 對帳時拆成兩列）\n→ 後台 財務管理 › 稅務包",
        "income_tax_prepay": "🧾 【營所稅暫繳提醒】9 月為暫繳月，請備妥暫繳稅款",
        "project_closing":   "📥 【結案】專案「{project_name}」進入官網上架收件匣\n→ 後台 業務管理 › 專案管理 › 結案看板",
        # N-hr H2 請假申請（依 owner 2026-07-17 設計鐵則：通知文字無 emoji）
        "leave_request":     "【請假申請】{staff_name}：{leave_type} {start} ~ {end}（{days} 天）\n事由：{reason}\n→ 後台 人事管理 › 出缺勤",
        # 假勤重整（docs/LEAVE_PLAN.md §7.5）：核准／退回／消假決定 → 貼群 @申請人（一期先貼群；私訊要另接 Chat API）
        "leave_result":      "【請假{result}】{staff_name}：{leave_type} {start}～{end}（{hours} 小時）{note}\n→ 員工頁 我的假勤",
        "works_published":   "🌐 【上架驗證 ✓】{count} 件作品已確認在對外網站上線\n{titles}",
        # 工時：Sheet 與總表改過的列撞到（沒自動蓋）；owner 鐵則：新通知無 emoji
        "timesheet_conflict": "【工時衝突】Sheet 有 {count} 列與總表改過的列內容不同，沒有自動覆蓋。到 後台 人事管理 › 專案工時 › 總表 決定用哪一列",
    }

    raw_tpl = tpls.get(template_key) or _defaults.get(template_key, "")
    if not raw_tpl:
        return

    msg = raw_tpl
    for k, v in variables.items():
        if isinstance(v, float):
            if k in ("total_size", "size", "total_bytes"):
                v_str = fmt_size(v)
            else:
                v_str = f"{v:.2f}"
        else:
            v_str = str(v)
        msg = msg.replace(f"{{{k}}}", v_str)

    # 缺漏變數不該洩漏成字面值（保護 inquiry_received 等複雜範本）
    import re
    msg = re.sub(r"\{[a-z_][a-z0-9_]*\}", "-", msg)

    gchat_url = gchat_webhook_url(settings)
    # 🔴 級告警優先送「系統告警」聊天室；沒設就回頭用一般聊天室（不能因為沒設而靜音）。
    if is_critical:
        gchat_url = (os.environ.get("ALERT_WEBHOOK") or notif.get("alert_webhook", "") or gchat_url)

    # 重大告警不受各 tab 的 gchat 開關管轄 —— 那個開關是給「任務完成通知」用的
    if (send_gchat or is_critical) and gchat_url:
        _post_gchat(gchat_url, msg, template_key)

    if is_critical:
        _relay_alert_email(template_key, msg, settings)


def gchat_webhook_url(settings: dict | None = None) -> str:
    """一般聊天室的 webhook：環境變數優先，其次 settings.json notifications.google_chat_webhook。"""
    notif = (settings or _load_settings()).get("notifications") or {}
    return os.environ.get("GOOGLE_CHAT_WEBHOOK") or notif.get("google_chat_webhook", "")


def _post_gchat(url: str, text: str, label: str) -> bool:
    try:
        import requests  # type: ignore — 精簡 agent 可能沒裝；缺它不該滅掉 email 那條
        requests.post(url, json={"text": text}, timeout=10).raise_for_status()
        return True
    except Exception as e:
        print(f"notifier: Google Chat [{label}] failed: {e}")
        return False


def send_google_chat(text: str) -> bool:
    """不走範本、直接推一段文字到一般聊天室（週一工時 digest 等自己組文字的用途）。
    沒設 webhook 回 False，不炸。"""
    url = gchat_webhook_url()
    return bool(url) and _post_gchat(url, text, "direct")


def _relay_alert_email(template_key: str, msg: str, settings: dict) -> None:
    """重大告警轉寄 email：POST 給 master 的 internal endpoint，由 master 寄出。

    為什麼不在本機直接寄：SMTP 帳密存在 NAS Postgres 的 `website_settings`，
    機隊 7 台不該持有寄信憑證，master 是唯一同時有 DB 與憑證的節點。
    只用 stdlib urllib —— 沒裝 requests 的精簡 agent 也要發得出重大告警。
    Best-effort：任何失敗只印一行，絕不讓告警路徑反過來炸掉呼叫端。
    """
    base = (os.environ.get("MASTER_SERVER") or settings.get("master_server") or "").rstrip("/")
    if not base:
        return
    key = INTERNAL_ALERT_KEY
    import urllib.request
    payload = json.dumps({
        "subject": f"[Originsun 告警] {template_key} — {machine_label()}",
        "body": msg,
    }).encode("utf-8")
    req = urllib.request.Request(
        base + "/api/v1/internal/alert_email", data=payload, method="POST",
        headers={"Content-Type": "application/json", "X-Internal-Key": key},
    )
    try:
        urllib.request.urlopen(req, timeout=10).close()
    except Exception as e:
        # 印出 master 回的狀態碼 —— 403 就是金鑰對不上（這條路徑壞了半年沒人發現，
        # 因為訊息裡看不出是被擋掉還是連不到）
        code = getattr(e, "code", "")
        print(f"notifier: alert email relay [{template_key}] failed"
              f"{f' (HTTP {code})' if code else ''}: {e} → {base}")


def machine_label() -> str:
    """告警用機器名：settings.machine_id 優先（與 job_history / 排程的機器身分一致），
    缺才退 hostname。

    刻意不用 db.json_fallback.get_machine_id — 那個模組頂層 import db.session
    （sqlalchemy），機隊 agent 沒裝 DB 套件會 ImportError，反而滅掉告警。
    """
    import socket
    try:
        return _load_settings().get("machine_id", "") or socket.gethostname()
    except Exception:
        return socket.gethostname()


async def notify_tab_async(template_key: str, **variables) -> None:
    """async best-effort 包裝：to_thread 跑同步 notify_tab，任何失敗靜默。

    async 呼叫端不用各自記得 to_thread；唯一要 caller 自己守的是
    `from notifier import ...` 這行本身（NAS 容器 / 精簡 agent 可能沒帶本模組）。
    """
    try:
        import asyncio
        await asyncio.to_thread(notify_tab, template_key, **variables)
    except Exception:
        pass

