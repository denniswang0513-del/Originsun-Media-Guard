# docker/

Phase M 官網 NAS 部署相關設定。

**部署目標**：NAS 192.168.1.132 路徑 `/share/Container/AI_Workspace/Originsun_Web/Website/`

**策略**：新增 2 個專用容器（`Website_Nginx` + `website-api`）在新 bridge network `originsun_web`。既有 5 容器全部不動。cloudflared 複用，只在 CF Zero Trust 儀表板加 public hostname。

| 檔案 / 目錄 | 職責 |
|---|---|
| `docker-compose.yml` | 2 容器：Website_Nginx + website-api，定義 bridge `originsun_web` |
| `Dockerfile.website` | website-api 容器映像（python:3.11-slim + FastAPI） |
| `nginx/originsun.conf` | Website_Nginx 用的 virtual host 設定（反代 /api/website → website-api:8001） |

## 部署後 NAS 目錄對應

```
/share/Container/AI_Workspace/Originsun_Web/
├── FileReport/、Agents/、Logs/ (既有，不動)
└── Website/                    🆕
    ├── repo/       git clone（含本目錄）
    ├── dist/       Astro build 產物
    ├── uploads/    使用者上傳
    └── docker/     本目錄的容器設定
```

完整容器拓撲、網路、nginx 設定見 [`docs/WEBSITE_ARCHITECTURE.md`](../docs/WEBSITE_ARCHITECTURE.md) 第 2、9.2 節。
