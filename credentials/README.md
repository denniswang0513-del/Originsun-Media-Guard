# 安全憑證目錄 (Credentials Directory)

這個目錄用於存放 Google API 的認證憑證。**請勿將此目錄中的 JSON 檔案上傳至任何版本控制系統**。

## 檔案說明

| 檔名 | 說明 | 如何取得 |
|---|---|---|
| `credentials.json` | Google Cloud OAuth2 客戶端憑證 | 從 Google Cloud Console 下載 |
| `token.json` | OAuth2 授權 token（首次執行後自動生成） | 程式自動生成，無需手動放置 |

## 如何取得 credentials.json

1. 前往 [Google Cloud Console](https://console.cloud.google.com/)
2. 建立或選擇一個專案
3. 啟用「Google Drive API」
4. 前往「憑證」→「建立憑證」→「OAuth 2.0 用戶端 ID」
5. 應用程式類型選「電腦版應用程式 (Desktop app)」
6. 下載 JSON 檔案，重新命名為 `credentials.json` 並放置於此目錄

## 通知服務設定

LINE Notify Token 和 Google Chat Webhook URL 請設定於 `settings.json` 中：
```json
{
  "line_notify_token": "YOUR_TOKEN_HERE",
  "google_chat_webhook": "YOUR_WEBHOOK_URL_HERE",
  "gdrive_folder_id": "YOUR_GOOGLE_DRIVE_FOLDER_ID"
}
```
