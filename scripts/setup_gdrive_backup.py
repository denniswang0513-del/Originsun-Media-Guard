"""scripts/setup_gdrive_backup.py
────────────────────────────────
一次性 Google Drive 備份授權小工具（One-time OAuth provisioning helper）。

這支腳本**只需在 master 上跑一次**（有瀏覽器、有 credentials/ 目錄的那台）。
它會：
  1. 檢查 credentials/credentials.json 是否已放好（OAuth 桌面用戶端）。
  2. 觸發一次性 OAuth 同意畫面（開瀏覽器）→ 產生 credentials/token.json。
  3. 在你的 Google Drive 建立（或找到）「Originsun Backups」資料夾 → 印出 folder id。
  4. 印出把 folder id 貼進後台的下一步指引。

之後 Layer 2 排程（services/website/backup_service.py，每日 03:00）就能
無人值守把 originsun_backup_<ts>.tar.gz 上傳到你的 Drive。

用法：
    cd <repo root>
    python scripts/setup_gdrive_backup.py

⚠️ 必須在 master 執行（OAuth 瀏覽器 + 憑證都在 master）。
    只用 stdlib + drive_sync（root 模組），不需其他相依。
"""
from __future__ import annotations

import os
import sys

# ── 讓腳本能 import 上層 root 的 drive_sync ────────────────────────────────
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_BAR = "═" * 64


def _section(title: str) -> None:
    print()
    print(_BAR)
    print(f"  {title}")
    print(_BAR)


def _fail(msg: str) -> None:
    print()
    print("✗ " + msg)
    print()


def main() -> int:
    print()
    print(_BAR)
    print("  Originsun 備份 · Google Drive 一次性授權工具")
    print("  One-time Google Drive OAuth provisioning for backups")
    print(_BAR)
    print("  ⚠️  這支腳本必須在 master（有瀏覽器 + credentials/ 的那台）執行。")

    # ── import drive_sync（root 模組）；google 套件缺 → 清楚訊息 ──────────────
    try:
        import drive_sync  # noqa: E402  (root module, after sys.path insert)
    except ImportError as e:
        _fail(f"無法 import drive_sync（root 模組）：{e}")
        print("  請確認你在 repo 根目錄執行：cd <repo root> && python scripts/setup_gdrive_backup.py")
        return 1

    creds_file = os.path.join(_REPO_ROOT, "credentials", "credentials.json")
    token_file = os.path.join(_REPO_ROOT, "credentials", "token.json")

    # ══ Step 1｜檢查 credentials.json ════════════════════════════════════════
    _section("Step 1 / 4　檢查 OAuth 用戶端憑證 credentials.json")
    if not os.path.exists(creds_file):
        _fail(f"找不到 credentials.json：\n    {creds_file}")
        print("  請先在 Google Cloud Console 建立 OAuth 桌面用戶端，步驟如下：")
        print()
        print("    1. 前往 https://console.cloud.google.com/ ，建立（或選擇）一個專案。")
        print("    2. 「API 和服務」→「已啟用的 API 和服務」→「+ 啟用 API 和服務」，")
        print("       搜尋並啟用「Google Drive API」。")
        print("    3. 「API 和服務」→「憑證」→「+ 建立憑證」→「OAuth 用戶端 ID」。")
        print("       （若首次會要求先設定「OAuth 同意畫面」：User Type 選『外部』，")
        print("        填應用程式名稱/支援信箱即可；把自己的 Google 帳號加進『測試使用者』。）")
        print("    4. 應用程式類型選「桌面應用程式（Desktop app）」→ 建立。")
        print("    5. 按「下載 JSON」，把檔案改名為 credentials.json。")
        print(f"    6. 放到：{creds_file}")
        print("       （credentials/ 目錄若不存在請自行建立；此目錄已在 .gitignore。）")
        print("    7. 重新執行本腳本：python scripts/setup_gdrive_backup.py")
        print()
        return 1
    print(f"  ✓ 已找到：{creds_file}")

    # ══ Step 2｜觸發一次性 OAuth 同意（開瀏覽器 → 寫 token.json）══════════════
    _section("Step 2 / 4　OAuth 授權（會開啟瀏覽器，請登入並同意）")
    if os.path.exists(token_file):
        print(f"  ℹ️  已偵測到既有 token.json（{token_file}）。")
        print("     若仍有效就不會再開瀏覽器；失效則會重新要求授權。")
    print("  → 正在啟動授權流程…（瀏覽器若沒自動開，請複製終端機印出的網址）")
    try:
        drive_sync._get_service()
    except RuntimeError as e:
        # google 套件未安裝（drive_sync._get_service 會 raise RuntimeError）
        _fail(f"Google API 套件未安裝：{e}")
        print("  請安裝：")
        print("    pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib")
        return 1
    except FileNotFoundError as e:
        _fail(f"憑證問題：{e}")
        return 1
    except Exception as e:  # noqa: BLE001  — 授權被取消/網路問題等
        _fail(f"授權失敗：{e}")
        print("  常見原因：瀏覽器關太快、未把自己加入 OAuth 同意畫面的『測試使用者』、或網路被擋。")
        print("  修正後重新執行即可。")
        return 1

    if not drive_sync.gdrive_ready():
        _fail("授權流程結束，但 token.json 仍未就緒（gdrive_ready() = False）。請重試。")
        return 1
    print(f"  ✓ 授權成功，token.json 已寫入：{token_file}")

    # ══ Step 3｜建立 / 取得備份資料夾 ════════════════════════════════════════
    _section("Step 3 / 4　在 Google Drive 建立備份資料夾")
    try:
        folder_id = drive_sync.ensure_folder("Originsun Backups")
    except Exception as e:  # noqa: BLE001
        _fail(f"建立資料夾失敗：{e}")
        return 1
    print("  ✓ 資料夾就緒：「Originsun Backups」")
    print()
    print("  ┌──────────────────────────────────────────────────────────────┐")
    print("  │  你的 Google Drive 資料夾 ID（複製這一串）：")
    print(f"  │      {folder_id}")
    print("  └──────────────────────────────────────────────────────────────┘")

    # ══ Step 4｜下一步（貼進後台）════════════════════════════════════════════
    _section("Step 4 / 4　把 folder id 貼進後台並驗證")
    print("  到官網後台 admin Tab →「🗄 資料備份」卡片，完成 4 步：")
    print()
    print(f"    1. 把上面的 folder id 貼進「Google Drive 資料夾 ID」欄位：")
    print(f"         {folder_id}")
    print("    2. 按「儲存設定」。")
    print("    3. 勾選「啟用排程」（每日 03:00 master 自動打包上傳）。")
    print("    4. 按「立即備份一次」→ 確認狀態顯示成功、Drive 出現 originsun_backup_<ts>.tar.gz。")
    print()
    print("  完成後，Layer 2 off-site 備份即生效。還原步驟見 docs/BACKUP_RESTORE.md。")
    print(_BAR)
    print("  ✓ 全部完成。")
    print(_BAR)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
