import os
import json as _json

_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
_DEFAULT_SETTINGS: dict = {
    # LINE Notify 服務已終止（2025-03-31），line_notify_token / channel line 鍵已移除；
    # 既有 settings.json 殘留的鍵無害（notifier 已不讀）
    "notifications": {
        "google_chat_webhook": "",
        # 🔴 級告警專用；留空 → fallback 回 google_chat_webhook（見 notifier.CRITICAL_ALERTS）
        "alert_webhook": "",
        "custom_webhook_url": "",
    },
    "message_templates": {
        "backup_success":    "✅ 【備份成功】專案 {project_name} 已完成！\n📂 檔案數：{file_count} | 💾 容量：{total_size}",
        "report_success":    "📊 【報表生成】{project_name} 視覺報表已出爐！\n🔗 點此查看：{report_url}",
        "transcode_success": "🎬 【轉檔完成】{project_name} 已轉檔完成！\n📂 輸出至：{dest_dir} | 共 {file_count} 個檔案",
        "concat_success":    "🔗 【串接完成】{output_file} 已輸出！\n📁 儲存位置：{dest_dir}",
        "verify_success":    "🔍 【比對完成】{project_name}\n✅ 通過：{pass_count} | ❌ 失敗：{fail_count} | 共 {total_count} 個",
        "media_log_upload":  "【影像紀錄】{project_name} 新收 {file_count} 個檔案（{uploaders}）",
        "user_registered":   "【新帳號註冊】{username}（{email}）已完成公司驗證註冊 — 權限待管理員開通（右上角選單 → 使用者管理）",
    },
    "notification_channels": {
        "backup":    {"gchat": True},
        "report":    {"gchat": True},
        "transcode": {"gchat": True},
        "concat":    {"gchat": True},
        "verify":    {"gchat": True},
    },
    # job_history DB 表保留天數（core/maintenance.py 每日 purge；0 = 停用）
    "job_history_retention_days": 180,
    # 財務管理排程提醒（core/scheduler._finance_calendar_check / _loan_due_check 讀取）。
    # report_remind_hour：月報 / 稅務行事曆每日觸發時刻（0-23，含），與貸款提醒共用慣例。
    # 其餘 finance.* 鍵（baseline_month / monthly_fixed_costs / loan_remind_hour / loan_remind_days）
    # 由 api_finance / api_cashflow 於執行時寫入 settings.json；此處只補預設 hour，
    # load/save 的 dict 深度 merge 會保留那些既有鍵、不覆蓋。
    "finance": {
        "report_remind_hour": 9,
    },
    # 工時 Google Sheet（services/timesheet_puller，只在 master 跑）：ingest_token 給
    # Apps Script／腳本推；pull 是主控端定時拉整本 xlsx（公開連結），enabled 由 owner 開。
    "timesheet": {
        "ingest_token": "",
        "pull": {"enabled": False, "sheet_id": "", "cron": "0 9 * * 6"},
        # 週一 09:00 上週工時 digest → Google Chat（services/timesheet_digest）
        "digest": {"enabled": False, "cron": "0 9 * * 1"},
    },
    "nas_paths": {
        "ota_dir": "",
        "web_report_dir": r"\\192.168.1.132\Container\AI_Workspace\Originsun_Web\FileReport",
        "voice_dir": "",
        "agents_dir": r"\\192.168.1.132\Container\AI_Workspace\Originsun_Web\Agents",
        "logs_dir": r"\\192.168.1.132\Container\AI_Workspace\Originsun_Web\Logs",
    },
    # 共用圖床（NAS PasteAssets + Assets_Nginx，24/7 對外 serve、不依賴 master）。
    # 底下用命名空間分區：paste=全站貼上圖片、medialog=影像紀錄縮圖。
    # 消費端一律走 core.assets_host.assets_target(namespace)，不要各自拼路徑。
    # dev 機（行程無 NAS SMB 憑證）覆寫成本機 uploads + 同源 "/uploads"。
    "assets_host": {
        "dir": r"\\192.168.1.132\Container\AI_Workspace\Originsun_Web\PasteAssets",
        "base_url": "https://assets.originsun-studio.com",
    },
    "master_server": "http://192.168.1.107:8000",
    # 提案庫資產夾（2026-08-06 owner 指定）：每個專案一夾 `{建立日}_{專案名}`，
    # 專案改名時資料夾跟著改（core.project_folders）。deck 上傳落在這裡，
    # 也是人可以直接丟企劃檔（腳本/分鏡/簡報原檔）的地方。
    "proposals": {
        "root": r"\\192.168.1.132\Archive\00_提案企劃",
    },
    # 參考影片封存（docs/REFERENCE_LIBRARY.md §12）：master-only runner。
    # enabled 預設 False —— dev 與新環境不會自己開始下載；正式啟用由管理卡打開。
    "reference_archive": {
        "enabled": False,
        "dir": r"\\192.168.1.132\Container\AI_Workspace\Originsun_Web\ReferenceArchive",
        "max_height": 720,
        "per_hour": 6,
        "max_gb": 200,
    },
    "database_url": "postgresql+asyncpg://originsun:cdeed932212d3e5bbfe86856bc77ac9c@192.168.1.132:5432/mediaguard",
    "machine_id": "",  # 自動填入 hostname（init_settings 時）
    "agents": [],
    "compute_hosts": [],
    "concurrency": {
        "backup": 1,
        "transcode": 2,
        "concat": 2,
        "verify": 2,
        "transcribe": 1,
        "report": 1,
    },
    "browse_roots": [],  # NAS 瀏覽器允許的根目錄，例 ["S:/", "R:/", "T:/"]
    "staff_roles": ["攝影師", "剪輯師", "導演", "製片", "燈光", "收音", "空拍", "動畫"],
    "project_types": ["紀實影片", "活動紀實", "形象影片", "廣告", "MV"],
    "google_calendar": {          # 行事曆（docs/SHOOT_CALENDAR_PLAN.md）：日曆 ID＋服務帳號（空＝借官網設定的 GA 服務帳號）
        "calendar_id": "",
        "service_account_json": "",
    },
    "google_oauth": {
        "enabled": False,
        "client_id": "",
        "default_role": "editor",
        "allowed_domains": [],      # 空 = 允許所有，例 ["originsun.com"]
    },
}

def save_settings(data: dict) -> None:
    # Merge-on-save: read existing settings first, then overlay incoming data
    # so that keys not present in `data` are preserved (e.g. agents, concurrency).
    existing = {}
    if os.path.exists(_SETTINGS_FILE):
        try:
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                existing = _json.load(f)
        except Exception:
            pass
    for key, val in data.items():
        if isinstance(val, dict) and isinstance(existing.get(key), dict):
            existing[key] = {**existing[key], **val}
        else:
            existing[key] = val
    with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
        _json.dump(existing, f, ensure_ascii=False, indent=4)

def init_settings() -> None:
    """Ensure settings.json exists with defaults on boot."""
    if not os.path.exists(_SETTINGS_FILE):
        try:
            save_settings(_DEFAULT_SETTINGS)
        except Exception as e:
            print(f"Failed to auto-create settings.json: {e}")
    # 自動填入 machine_id（首次啟動時）
    try:
        import socket as _socket
        data = load_settings()
        if not data.get("machine_id"):
            data["machine_id"] = _socket.gethostname()
            save_settings(data)
    except Exception:
        pass

init_settings()

def _migrate_compute_hosts(data: dict) -> None:
    """將舊 compute_hosts 轉換為 agents 格式（一次性遷移）。"""
    import re as _re
    old = data.get("compute_hosts")
    if not old:
        return
    agents = data.setdefault("agents", [])
    existing_urls = {a.get("url", "").rstrip("/") for a in agents}
    for h in old:
        ip = h.get("ip", "")
        url = f"http://{ip}".rstrip("/")
        if url in existing_urls:
            continue
        name = h.get("name", ip)
        slug = _re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "host"
        agents.append({"id": slug, "name": name, "url": url})
        existing_urls.add(url)
    # 遷移完成，移除舊 key
    data.pop("compute_hosts", None)


# 檔案內容的快取，key 是 mtime。save_settings 一寫、mtime 就變、下次自動重讀。
# 🔴 只快取**解析後的檔案**，不快取 merge 的結果 —— merge 產出的是新的 section
# dict，而呼叫端普遍是 `s = load_settings(); s[k] = v; save_settings(s)`，
# 快取整包會讓某個呼叫端的就地修改污染下一個呼叫端拿到的設定。
_settings_cache: tuple = (None, None)


def _detached(v):
    """可變值要複製一份再交出去 —— 不然呼叫端就地改（改了沒存檔）會污染快取。
    dict section 上面本來就每次重建，這裡處理 list 那幾個（agents、schedules…）。"""
    if isinstance(v, list):
        return [dict(x) if isinstance(x, dict) else x for x in v]
    if isinstance(v, dict):
        return dict(v)
    return v


def load_settings() -> dict:
    global _settings_cache
    if os.path.exists(_SETTINGS_FILE):
        try:
            mtime = os.path.getmtime(_SETTINGS_FILE)
            if _settings_cache[0] != mtime:
                with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = _json.load(f)
                # 一次性遷移 compute_hosts → agents
                if data.get("compute_hosts"):
                    _migrate_compute_hosts(data)
                    with open(_SETTINGS_FILE, "w", encoding="utf-8") as fw:
                        _json.dump(data, fw, ensure_ascii=False, indent=4)
                    mtime = os.path.getmtime(_SETTINGS_FILE)
                _settings_cache = (mtime, data)
            data = _settings_cache[1]
            merged: dict = {}
            # Merge defaults first
            for section, defaults in _DEFAULT_SETTINGS.items():
                if isinstance(defaults, dict):
                    merged[section] = {**defaults, **data.get(section, {})}
                else:
                    merged[section] = _detached(data.get(section, defaults))
            # Preserve any extra keys from file not in defaults
            for key in data:
                if key not in merged:
                    merged[key] = _detached(data[key])
            return merged
        except Exception:
            pass
    return {s: (dict(v) if isinstance(v, dict) else v) for s, v in _DEFAULT_SETTINGS.items()}
