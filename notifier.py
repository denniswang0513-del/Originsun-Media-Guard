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

# 🔴 級告警：除了 Chat 也寄 email。刻意**不含** `*_success` 那些日常完成通知 ——
# 機隊 7 台同時跑任務時會把信箱淹掉，淹掉的信箱等於沒有告警。
# `*_recovered` 有進來是為了閉環：收到 offline 的信，就該收到恢復的信。
CRITICAL_ALERTS = frozenset({
    "task_failed", "rebuild_failed", "backup_failed", "deploy_failed",
    "db_offline", "db_recovered",
    "agent_offline", "agent_recovered",
    "ai_runner_failed",
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
        # 失敗告警（channel key 即 template_key 本身，預設 gchat=True → webhook 有設就會發）
        "task_failed":       "🔴 【任務失敗】{task_type}｜{project_name}\n⚠️ {error}\n🖥️ 機器：{hostname}",
        "rebuild_failed":    "🔴 【官網重建失敗】\n⚠️ {error}\n請到官網管理 Tab 查看完整 log 並手動「立即重建」",
        "backup_failed":     "🔴 【每日備份失敗】\n⚠️ {error}\n請檢查 master→NAS SSH 與 Google Drive 憑證",
        "db_offline":        "🔴 【資料庫斷線】PostgreSQL（{db}）連線中斷 — {hostname}\n已切換 JSON fallback，每 60 秒自動重連中",
        "db_recovered":      "🟢 【資料庫恢復】PostgreSQL（{db}）連線已恢復 — {hostname}",
        "deploy_failed":     "🔴 【部署到生產失敗】v{version}\n⚠️ {detail}",
        "log_oversize":      "🟠 【Log 檔超大】{hostname} 的 {filename} 已達 {size_mb}MB\nlog 只在重啟時輪替 — 建議找空檔重啟該機 agent",
        "agent_offline":     "🔴 【機隊斷線】{name}（{url}）連續 {misses} 次健康檢查無回應\n可能：關機/睡眠/網路/agent 掛掉 — 請檢查該機器",
        "agent_recovered":   "🟢 【機隊恢復】{name}（{url}）已重新上線",
        "ai_runner_failed":  "🔴 【AI Runner 失效】{kind} 連續 {fails} 次呼叫 claude 失敗\n⚠️ {error}\n可能：Max 訂閱到期/登出/CLI 更新 — 到 master 跑一次 `claude` 檢查",
        "social_daily":      "📣 【今日社群任務】{count} 篇文稿待審\n{titles}\n→ 後台 官網管理 › 社群工作台",
        "loan_payment_due":  "🏦 【貸款繳款提醒】近期有 {count} 筆貸款款項待繳：\n{lines}\n→ 後台 財務管理 › 銀行貸款",
        "project_closing":   "📥 【結案作業】專案「{project_name}」進入官網上架收件匣\n→ 後台 業務管理 › 專案管理 › 結案看板",
        "works_published":   "🌐 【上架驗證 ✓】{count} 件作品已確認在對外網站上線\n{titles}",
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

    gchat_url = os.environ.get("GOOGLE_CHAT_WEBHOOK") or notif.get("google_chat_webhook", "")
    # 🔴 級告警優先送「系統告警」聊天室；沒設就回頭用一般聊天室（不能因為沒設而靜音）。
    if is_critical:
        gchat_url = (os.environ.get("ALERT_WEBHOOK") or notif.get("alert_webhook", "") or gchat_url)

    # 重大告警不受各 tab 的 gchat 開關管轄 —— 那個開關是給「任務完成通知」用的
    if (send_gchat or is_critical) and gchat_url:
        try:
            import requests  # type: ignore — 精簡 agent 可能沒裝；缺它不該滅掉 email 那條
            requests.post(gchat_url, json={"text": msg}, timeout=10)
        except Exception as e:
            print(f"notifier: Google Chat [{template_key}] failed: {e}")

    if is_critical:
        _relay_alert_email(template_key, msg, settings)


def _relay_alert_email(template_key: str, msg: str, settings: dict) -> None:
    """重大告警轉寄 email：POST 給 master 的 internal endpoint，由 master 寄出。

    為什麼不在本機直接寄：SMTP 帳密存在 NAS Postgres 的 `website_settings`，
    機隊 7 台不該持有寄信憑證，master 是唯一同時有 DB 與憑證的節點。
    只用 stdlib urllib —— 沒裝 requests 的精簡 agent 也要發得出重大告警。
    Best-effort：任何失敗只印一行，絕不讓告警路徑反過來炸掉呼叫端。
    """
    base = (os.environ.get("MASTER_SERVER") or settings.get("master_server") or "").rstrip("/")
    key = os.environ.get("JWT_SECRET", "").strip() or settings.get("jwt_secret", "")
    if not (base and key):
        return
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
        print(f"notifier: alert email relay [{template_key}] failed: {e}")


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

