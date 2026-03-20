import os
import json as _json

_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
_DEFAULT_SETTINGS: dict = {
    "notifications": {
        "line_notify_token": "",
        "google_chat_webhook": "",
        "custom_webhook_url": "",
    },
    "message_templates": {
        "backup_success":    "✅ 【備份成功】專案 {project_name} 已完成！\n📂 檔案數：{file_count} | 💾 容量：{total_size}",
        "report_success":    "📊 【報表生成】{project_name} 視覺報表已出爐！\n🔗 點此查看：{report_url}",
        "transcode_success": "🎬 【轉檔完成】{project_name} 已轉檔完成！\n📂 輸出至：{dest_dir} | 共 {file_count} 個檔案",
        "concat_success":    "🔗 【串接完成】{output_file} 已輸出！\n📁 儲存位置：{dest_dir}",
        "verify_success":    "🔍 【比對完成】{project_name}\n✅ 通過：{pass_count} | ❌ 失敗：{fail_count} | 共 {total_count} 個",
    },
    "notification_channels": {
        "backup":    {"gchat": True, "line": False},
        "report":    {"gchat": True, "line": False},
        "transcode": {"gchat": True, "line": False},
        "concat":    {"gchat": True, "line": False},
        "verify":    {"gchat": True, "line": False},
    },
    "nas_paths": {
        "ota_dir": "",
        "web_report_dir": r"\\192.168.1.132\Container\AI_Workspace\Originsun_Web\FileReport",
        "voice_dir": "",
        "agents_dir": r"\\192.168.1.132\Container\AI_Workspace\Originsun_Web\Agents",
        "logs_dir": r"\\192.168.1.132\Container\AI_Workspace\Originsun_Web\Logs",
    },
    "master_server": "http://192.168.1.11:8000",
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


def load_settings() -> dict:
    if os.path.exists(_SETTINGS_FILE):
        try:
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = _json.load(f)
                # 一次性遷移 compute_hosts → agents
                if data.get("compute_hosts"):
                    _migrate_compute_hosts(data)
                    with open(_SETTINGS_FILE, "w", encoding="utf-8") as fw:
                        _json.dump(data, fw, ensure_ascii=False, indent=4)
                merged: dict = {}
                # Merge defaults first
                for section, defaults in _DEFAULT_SETTINGS.items():
                    if isinstance(defaults, dict):
                        merged[section] = {**defaults, **data.get(section, {})}
                    else:
                        merged[section] = data.get(section, defaults)
                # Preserve any extra keys from file not in defaults
                for key in data:
                    if key not in merged:
                        merged[key] = data[key]
                return merged
        except Exception:
            pass
    return {s: (dict(v) if isinstance(v, dict) else v) for s, v in _DEFAULT_SETTINGS.items()}
