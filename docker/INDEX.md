# docker/

Phase M 官網 NAS 部署相關設定。

**部署目標**：NAS 192.168.1.132 路徑 `/share/Container/AI_Workspace/Originsun_Web/Website/`

**複用既有 nginx**：本目錄**不**起新的 nginx container，僅提供 virtual host 設定檔給既有 nginx 掛載。

| 檔案 / 目錄 | 職責 |
|---|---|
| `docker-compose.website-api.yml` | 起 2 個新容器：`cloudflared` + `website-api` |
| `Dockerfile.website` | website-api 容器映像（python:3.11-slim + FastAPI） |
| `cloudflared/config.yml` | Cloudflare Tunnel 路由設定（指向既有 nginx） |
| `nginx/originsun.conf` | 加入既有 nginx `conf.d/` 的 virtual host 設定 |

## 部署後 NAS 目錄對應

```
/share/Container/AI_Workspace/Originsun_Web/
├── nginx/                              (既有，複用)
│   └── conf.d/originsun.conf           ← 從本目錄 symlink 或複製
└── Website/
    ├── repo/                           git clone 整個 repo（包含此目錄）
    ├── dist/                           Astro build 產物
    ├── uploads/                        使用者上傳
    └── docker/                         本目錄的容器設定
```

## Nginx 關鍵設定

- `/`              → `/var/www/originsun/*`（掛載自 `Website/dist/`）
- `/api/website/*` → `proxy_pass http://website-api:8001`
- `/uploads/*`     → `/var/www/originsun/uploads/`
