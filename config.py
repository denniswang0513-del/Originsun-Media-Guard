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
    "compute_hosts": [],
}

def save_settings(data: dict) -> None:
    with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False, indent=4)

def init_settings() -> None:
    """Ensure settings.json exists with defaults on boot."""
    if not os.path.exists(_SETTINGS_FILE):
        try:
            save_settings(_DEFAULT_SETTINGS)
        except Exception as e:
            print(f"Failed to auto-create settings.json: {e}")

init_settings()

def load_settings() -> dict:
    if os.path.exists(_SETTINGS_FILE):
        try:
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = _json.load(f)
                merged: dict = {}
                for section, defaults in _DEFAULT_SETTINGS.items():
                    if isinstance(defaults, dict):
                        merged[section] = {**defaults, **data.get(section, {})}
                    else:
                        merged[section] = data.get(section, defaults)
                return merged
        except Exception:
            pass
    return {s: (dict(v) if isinstance(v, dict) else v) for s, v in _DEFAULT_SETTINGS.items()}
