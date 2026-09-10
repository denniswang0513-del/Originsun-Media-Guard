# docker/

NAS 端對外網站部署設定（Phase M 完整版 A）。

## 部署狀態（2026-04-29）

| 元件 | 位置 | 狀態 |
|---|---|---|
| `Website_Nginx` 容器 | NAS port 8090 → 80 | ✅ Running，serve dist/ + proxy /api/website/* |
| `website-api` 容器 | NAS port 8001 → 8001 | ✅ Running，跑 main_website.py |
| `office-api` 容器 | NAS port 8002 → 8002 | ⏳ 設定檔已進版控，**尚未在 NAS 上建立**（見下節手動步驟），跑 main_office.py |
| `Assets_Nginx` 容器 | NAS port 8082 → 80 | ✅ Running（2026-07-24），內部貼圖圖床（詳下節） |
| Astro `dist/` | `/share/.../Website/dist/` | ✅ Master scp 推來 |
| Code mount | `/share/.../Website/code/` | ✅ Master /publish 自動同步 |
| Cloudflared | `test.originsun-studio.com` → `192.168.1.132:8090` | ✅ |

## Assets_Nginx（內部貼圖圖床，2026-07-24）

全站 textarea「貼上圖片」的落點（`routers/api_paste.py` 寫入、`js/shared/paste-image.js` 攔截貼上）。

- **資料夾**：`/share/CACHEDEV1_DATA/Container/AI_Workspace/Originsun_Web/PasteAssets/paste/`
  （SMB 777，與 FileReport 同權限模式——九台 agent 直接 UNC 寫入，不經 master）
- **容器**：`nginx:alpine`，port **8082→80**，唯讀掛載 PasteAssets + `AssetsNginx/default.conf`
  （autoindex off 防枚舉、hex 檔名 immutable cache 一年）；`restart=unless-stopped`
- **對外**：CF Zero Trust 加 hostname `assets.originsun-studio.com` → `192.168.1.132:8082`
- **URL 契約**：DB 只存 token `paste:<32hex>.webp`（不含網域）；基底由 master settings
  `assets_host.base_url` 經 `GET /api/v1/paste_config` 下發 → 圖床搬家改 settings 即可
- **命名空間**：同一台圖床（`assets_host`）底下 `paste/`（貼圖）+ `medialog/`（影像紀錄縮圖），
  由 `core/assets_host.py` 的 `assets_target(ns)` 定址
- 重建指令：`docker run -d --name Assets_Nginx --restart unless-stopped -p 8082:80 -v .../PasteAssets:/usr/share/nginx/html:ro -v .../AssetsNginx/default.conf:/etc/nginx/conf.d/default.conf:ro nginx:alpine`

## 檔案

| 檔 | 用途 |
|---|---|
| `Dockerfile.website` | 建立 `originsun/website-api:latest` image — python:3.11-slim + curl + pip 裝 requirements_website.txt |
| `requirements_website.txt` | website-api 容器最小依賴（fastapi/uvicorn/sqlalchemy/asyncpg/httpx 等，**不含** ffmpeg/torch/whisper） |
| `Dockerfile.office` | 建立 `originsun/office-api:latest` image — 同形狀，多裝 google-auth/requests/pillow-heif/Jinja2/tzdata，**不裝 Playwright**（產 PDF 留在 master） |
| `requirements_office.txt` | office-api 容器依賴。以 `requirements_website.txt` 為底，逐項差異與理由寫在該檔檔頭 |
| `docker-compose.yml` | 定義 website-api（8001）＋ office-api（8002）兩個 service：mount `../code` → `/app`、env `DATABASE_URL`/`JWT_SECRET`/…、接 `postgres_default` bridge |
| `nginx/originsun.conf` | Website_Nginx 設定 — `^~ /_astro/` 長期 cache、`location /` try_files、`location /api/website/` proxy_pass website-api:8001，外加**公開頁**那組（`/media-log.html`、`/project.html`（舊 `/proposal-plan.html` 301 到它）、`/img/`、`/tabs/proposals/`、`/js/shared/`、兩組 token API）—— 那組的清單正本在 `core/public_assets.py`，別只改這裡。檔尾另有 `office.originsun-studio.com` 的 server 區塊（整站反代 office-api:8002，**不走 try_files**；另聽 8091 給 LAN 直達） |
| `.env`（**不進 git**）| `DATABASE_URL` / `JWT_SECRET` / `WEBSITE_CORS_ORIGINS` / `MASTER_RELAY_URL` / `OFFICE_CORS_ORIGINS` |

## NAS 路徑佈局

```
/share/CACHEDEV1_DATA/Container/AI_Workspace/Originsun_Web/Website/
├── code/             ← master /publish 自動 scp 同步
│   ├── main_website.py   ← website-api 容器的入口
│   ├── main_office.py    ← office-api 容器的入口（同一份 code、兩個曝露面）
│   ├── routers/, services/, core/, db/, config.py
│   └── frontend/     ← 只有公開頁與它們 import 的模組目錄（**不是**整個
│                        前端；清單在 core/public_assets.py，有測試釘住相依閉包）
├── dist/             ← master npm build 完 scp 同步
├── uploads/          ← 容器寫，圖片上傳放這
└── docker/           ← 本目錄上 NAS 的副本（compose / nginx / .env）
```

## office-api 上線：必須人工在 NAS 做的步驟（2026-09-10 新增，尚未執行）

改 repo 裡的檔案**不會**讓這個容器出現。以下每一步都要人在 NAS／CF 上做一次：

1. **同步 docker/ 到 NAS**：把 `Dockerfile.office`、`requirements_office.txt`、
   改過的 `docker-compose.yml` 與 `nginx/originsun.conf` 放到
   `/share/.../Website/docker/`（`/publish` 只同步 `code/`，**不含這個目錄**）。

2. **`.env` 補一行** `OFFICE_CORS_ORIGINS=https://office.originsun-studio.com,http://192.168.1.132:8091`
   （沒設 = 空字串，`main_office` 會退到程式內建的預設清單，值一樣；但顯式寫出來
   才看得出換網域要改哪裡）。`DATABASE_URL` / `JWT_SECRET` 沿用既有的同一份。

3. **build ＋ 起容器**：`cd $DEPLOY && $DOCKER compose build office-api && $DOCKER compose up -d office-api`
   （只指名 office-api，避免順手重建 website-api）。

4. 🔴 **Website_Nginx 要多一條 port 對映 `-p 8091:8091`**。
   那個容器**不是** compose 管的，是手動 `docker run` 起來的 —— port 對映只能在
   建立時決定，**改 conf 不會生效、`docker restart` 也不會**，必須 `docker rm` 之後
   帶新參數重跑。先把現有參數抄下來再重建：
   ```bash
   $DOCKER inspect Website_Nginx --format '{{json .HostConfig.PortBindings}}{{json .Mounts}}'
   # 重建時原本的 -p 8090:80 與所有 -v 都要原樣帶回，另加 -p 8091:8091
   ```
   不做這步的後果：`office.originsun-studio.com` 走 CF 會通，但 LAN 直達（8091）
   不通 —— 而 8091 存在的理由正是「CF／cloudflared 出事時還有一條路」，
   少了它整件事就退回單點。

5. **把新 conf 送進 Website_Nginx 並 reload**（指令見下節），`nginx -t` 要過。

6. **CF Zero Trust 加 hostname** `office.originsun-studio.com` → `192.168.1.132:8090`
   （跟對外站同一個入口，靠 `Host` 標頭在 nginx 分流到新 server 區塊）。

7. **驗收**：
   - `curl -s http://192.168.1.132:8002/healthz`（容器直通）
   - `curl -s -H 'Host: office.originsun-studio.com' http://192.168.1.132:8090/healthz`（經 nginx）
   - `curl -s http://192.168.1.132:8091/healthz`（LAN 直達，第 4 步做完才會通）
   - 三者都要回 `{"ok":true,"service":"office-api",...,"db":true}`；`db:false` = `.env` 的
     `DATABASE_URL` 沒帶到。
   - 拿 master 簽的 token 打一支 API，401 = 兩邊 `JWT_SECRET` 不同把。

8. **跑一次 `/publish`** 讓 `publish_update.py` 把 office 的前端
   （`core/office_assets.SYNC_PATHS`）與那份精簡 settings
   （`core/office_settings.EXPORT_KEYS`）推上去，並 restart 兩個容器。
   沒跑的話頁面回 **503「未同步到本機」**（刻意不是 404，一眼看得出是同步問題）。

## 常用指令（在 NAS 上跑，先 `Q` 跳出 console menu 進真 shell）

```bash
# 完整路徑（QNAP 預設 PATH 沒 docker）
DOCKER=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker
DEPLOY=/share/CACHEDEV1_DATA/Container/AI_Workspace/Originsun_Web/Website/docker

# 看容器狀態
$DOCKER ps --format '{{.Names}}\t{{.Status}}' | grep -E 'website|office|nginx'

# 看 logs
$DOCKER logs website-api --tail 50
$DOCKER logs office-api --tail 50
$DOCKER logs Website_Nginx --tail 30

# Reload nginx config（改了 originsun.conf 後）
$DOCKER cp $DEPLOY/nginx/originsun.conf Website_Nginx:/etc/nginx/conf.d/default.conf
$DOCKER exec Website_Nginx nginx -t && $DOCKER exec Website_Nginx nginx -s reload

# Restart website-api（master /publish 已自動做這個；手動只在 debug 時）
$DOCKER restart website-api
$DOCKER restart office-api

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
| `office.…/my.html` 回 503「未同步到本機」 | 前端檔沒推上來 → `publish_update` 的第二組同步目標（`core/office_assets.SYNC_PATHS`）沒跑 |
| office 的 CRM 端點全 404、容器卻是 healthy | router 掛載失敗被吞成一行 warning → `docker logs office-api \| grep 未掛載`；最常見是缺依賴（例：沒 `tzdata` → `_shared.py` 的 `ZoneInfo` 在 import 期就炸） |
| office 發票影像開不到／上傳落在奇怪的地方 | 先確認 `/share/Archive` 有掛進容器（compose 有）＋ `NAS_LOCAL_SHARE_ROOT=/share`。設定存的是 master 視角的 UNC，讀寫兩側都要過 `core.drive_map.to_local_path`；**寫**那側 2026-09-10 才補上（`_invoices_write_root`），在那之前是安靜地寫進 `/app` 底下一個名字帶反斜線的資料夾 |
| office 成本收據上傳「成功」但 master 看不到 | 2026-09-10 已修（`costs._receipt_dir` 翻譯＋存 canonical、`receipts_root` 進 EXPORT_KEYS）。若又出現，先確認 `/share/Archive` 有掛且 `receipts_root` 指到它底下 |
| `office.…` 走 CF 通、LAN `:8091` 不通 | Website_Nginx 少了 `-p 8091:8091`，要 `docker rm` 重建（上節第 4 步） |
