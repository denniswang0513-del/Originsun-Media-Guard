# docker/

NAS 端對外網站部署設定（Phase M 完整版 A）。

## 部署狀態（2026-04-29）

| 元件 | 位置 | 狀態 |
|---|---|---|
| `Website_Nginx` 容器 | NAS port 8090 → 80 | ✅ Running，serve dist/ + proxy /api/website/* |
| `website-api` 容器 | NAS port 8001 → 8001 | ✅ Running，跑 main_website.py |
| Astro `dist/` | `/share/.../Website/dist/` | ✅ Master scp 推來 |
| Code mount | `/share/.../Website/code/` | ✅ Master /publish 自動同步 |
| Cloudflared | `test.originsun-studio.com` → `192.168.1.132:8090` | ✅ |

## 檔案

| 檔 | 用途 |
|---|---|
| `Dockerfile.website` | 建立 `originsun/website-api:latest` image — python:3.11-slim + curl + pip 裝 requirements_website.txt |
| `requirements_website.txt` | website-api 容器最小依賴（fastapi/uvicorn/sqlalchemy/asyncpg/httpx 等，**不含** ffmpeg/torch/whisper） |
| `docker-compose.yml` | 定義 website-api service：mount `../code` → `/app`、env `DATABASE_URL`/`JWT_SECRET`/`MASTER_RELAY_URL`、接 `postgres_default` bridge |
| `nginx/originsun.conf` | Website_Nginx 設定 — `^~ /_astro/` 長期 cache、`location /` try_files、`location /api/website/` proxy_pass website-api:8001 |
| `.env`（**不進 git**）| `DATABASE_URL` / `JWT_SECRET` / `WEBSITE_CORS_ORIGINS` / `MASTER_RELAY_URL` |

## NAS 路徑佈局

```
/share/CACHEDEV1_DATA/Container/AI_Workspace/Originsun_Web/Website/
├── code/             ← master /publish 自動 scp 同步
│   ├── main_website.py
│   ├── routers/, services/, core/, db/, config.py
├── dist/             ← master npm build 完 scp 同步
├── uploads/          ← 容器寫，圖片上傳放這
└── docker/           ← 本目錄上 NAS 的副本（compose / nginx / .env）
```

## 常用指令（在 NAS 上跑，先 `Q` 跳出 console menu 進真 shell）

```bash
# 完整路徑（QNAP 預設 PATH 沒 docker）
DOCKER=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker
DEPLOY=/share/CACHEDEV1_DATA/Container/AI_Workspace/Originsun_Web/Website/docker

# 看容器狀態
$DOCKER ps --format '{{.Names}}\t{{.Status}}' | grep -E 'website|nginx'

# 看 logs
$DOCKER logs website-api --tail 50
$DOCKER logs Website_Nginx --tail 30

# Reload nginx config（改了 originsun.conf 後）
$DOCKER cp $DEPLOY/nginx/originsun.conf Website_Nginx:/etc/nginx/conf.d/default.conf
$DOCKER exec Website_Nginx nginx -t && $DOCKER exec Website_Nginx nginx -s reload

# Restart website-api（master /publish 已自動做這個；手動只在 debug 時）
$DOCKER restart website-api

# 重 build image（改 requirements 才需要；改 code 只 restart 即可）
cd $DEPLOY && $DOCKER compose build && $DOCKER compose up -d
```

## 從 master 端管 NAS（自動化路徑）

```bash
# master 端 ssh key 已設好（~/.ssh/id_originsun_nas → admin@192.168.1.132）
# /publish 流程末段會自動：
#   1. scp routers/services/core/db/main_website.py/config.py 到 NAS code/
#   2. ssh nas docker restart website-api

# 手動觸發 sync（debug 用）
python -c "from publish_update import sync_website_to_nas; sync_website_to_nas()"
```

## 健康監控

- **Container healthcheck**：`docker-compose.yml` 已設定每 30s curl `localhost:8001/healthz`，連續 3 次失敗會標 unhealthy
- **admin Tab 內**：`website.js` 會每 30s ping `/healthz`（透過 nginx），右下角顯示 ✓/⚠
- **對外可見性**：cloudflared 自己會 health check origin，origin 掛了 CF 會回 502 錯誤頁

## 故障排除

| 症狀 | 檢查 |
|---|---|
| 對外 `/works` 沒更新最新作品 | master 跑 npm build 失敗？檢查 admin Tab 頂部「上次發布」時間 |
| admin Tab 顯示「⚠ 無法連線」 | NAS website-api 容器掛了 → `docker logs website-api`；或 cloudflared 路由錯 |
| admin 編輯後 pending 不歸 0 | 60s debounce 觸發 npm build 但 scp 失敗 → master 看 publish_update 輸出 |
| 容器啟動就掛 | 通常 .env 漏 `DATABASE_URL`/`JWT_SECRET`；或 postgres_default network 不存在（先重啟 originsun_postgres） |
| 跨機 admin endpoint 401 | JWT secret 不一致 — master `settings.json` 的 `jwt_secret` 必須等於 NAS .env 的 `JWT_SECRET` |
