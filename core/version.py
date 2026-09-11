"""core/version.py — 本 checkout 的版號讀取（單一實作）。

版號自 2026-07-25 起是**跨機器協定**（master 與 NAS 對外容器互相比對，
用來抓「碼同步了但容器沒重啟」這個官網踩過最多次的故障），所以它的讀法
也該只有一份。原本散在 main.py / api_system(×2) / api_ota / main_website
的五份各自展開，預設值還不一致（"0.0.0" vs ""）。

⚠ read_local_version() 每次都讀檔（給 master：OTA 更新後不重啟也要看到新版號）；
  **長駐服務的自我回報請用模組載入時取一次的常數**——每 request 重讀會在
  「檔已同步、行程還跑舊碼」時回報新版號，正好把版號比對的意義抵銷掉。
"""
import json
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_FILE = os.path.join(_REPO_ROOT, "version.json")


def read_version_json() -> dict:
    """整份 version.json（version / build_date / notes）。讀不到回空 dict。"""
    try:
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def read_local_version(default: str = "0.0.0") -> str:
    """本機版號字串。"""
    return read_version_json().get("version") or default


#: 🔴 **這個行程載入時**的版號 —— 跟 `read_local_version()` 的差別就是檔頭那段警告：
#: 發版流程先寫檔再重啟，重啟沒發生的話磁碟上已經是新版、行程還跑著舊碼。
#: 要證明「真的重啟了」得看這個常數（`/api/v1/version` 的 `running` 欄位）。
RUNNING_VERSION = read_local_version(default="")
