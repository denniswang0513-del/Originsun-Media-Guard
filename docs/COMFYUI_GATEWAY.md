# ComfyUI GPU 機 + 帳號閘門

> 2026-08-21 建置。GPU 工作站 `originsun` 上的 ComfyUI，前面掛一支閘門，
> 用**這個系統既有的帳號密碼**登入。

---

## 1. 機器

| | |
|---|---|
| 主機名稱 | `ORIGINSUN`（登入帳號 `bot`） |
| Tailscale | `100.125.114.5`（tailnet 名稱 `originsun-1`） |
| LAN | `192.168.1.85` |
| GPU | RTX 5060 Ti 16GB（Blackwell / sm_120） |
| RAM / 碟 | 128 GB／4TB NVMe 切成 C: 1.8TB + D: 1.9TB |
| SSH | master 上 `ssh gpubox`（金鑰 `~/.ssh/id_originsun_gpubox`） |

**⚠️ Blackwell**：RTX 50 系列需要 CUDA 12.8+ 的 PyTorch。ComfyUI portable 的
`_nvidia_cu126` 那個變體**不支援 sm_120**，抓錯會噴
`no kernel image is available for execution on the device`。要抓
`ComfyUI_windows_portable_nvidia.7z`（目前是 torch 2.13.0+cu130）。

---

## 2. 架構

```
                    ┌─ Tailscale  100.125.114.5:8188 ─┐
使用者的瀏覽器 ──────┤                                  ├──→ Caddy :8188
                    └─ Cloudflare comfyui.originsun-   ┘         │
                       studio.com                                │
                         │                                       │
                    cloudflared 容器                    ┌────────┴────────┐
                    （NAS 上，192.168.1.58）             │                 │
                                                  /_auth/*          其他所有路徑
                                                        │                 │
                                                   authgate          forward_auth
                                                  127.0.0.1:8190      問 authgate
                                                        │                 │
                                              登入頁 + JWT 驗證      通過 → ComfyUI
                                                        │              127.0.0.1:8189
                                                   登入時打
                                              master:8000 /auth/login
```

**為什麼登入頁由 authgate 自己服務**：cookie 因此綁在「你進來的那個網域」上，
Tailscale 位址和公開網域各自拿各自的 cookie，完全不需要處理跨網域 cookie。

**驗證是離線的**：`authgate` 只做 HMAC 簽章 + `exp` 檢查，不查 DB、不打網路。
ComfyUI 的請求非常密集，每一個都會觸發一次 forward_auth，走網路會死。
master 只在**登入那一刻**被呼叫一次（拿帳密換 token）。

**master 關機的影響**：已登入的人不受影響（cookie 有效 7 天），只有重新登入會失敗。

---

## 3. 權限

RBAC 模組 key = **`comfyui`**（`core/auth.py` `ALL_MODULES`）。
非 tab 的橫切能力鍵，同 `money_view` / `me_petty`，所以
`docs/BENEFIT_POOL_PLAN.md` §8 的五處同步只適用其中三處
（TAB_MAP 與 `index.html` section 殼不適用）。

`access_level >= 3`（管理員）**無條件放行**，不需要勾。

刻意獨立成一把鑰匙而不是沿用 `transcode` / `drone_meta`：ComfyUI 的自訂節點
等於那台機器上的任意程式碼執行，不該隨後期製作權限外溢。

---

## 4. 這台機器上的實際部署

| 路徑 | 是什麼 |
|---|---|
| `D:\AI\ComfyUI_windows_portable\` | ComfyUI 本體（portable，自帶 python） |
| `D:\AI\start-comfyui.bat` | 啟動 ComfyUI，`--listen 127.0.0.1 --port 8189` |
| `D:\AI\caddy\Caddyfile` | 反向代理 + forward_auth（本目錄有副本） |
| `D:\AI\start-caddy.bat` | 啟動 Caddy |
| `D:\AI\authgate\authgate.py` | 閘門（本目錄有副本） |
| `D:\AI\authgate\jwt.secret` | 與 master 共用的 `jwt_secret`，ACL 鎖 Administrators + SYSTEM |
| `D:\AI\start-authgate.bat` | 啟動閘門（借用 ComfyUI 的 `python_embeded`） |

三個排程工作，全部 **onlogon + Session 1**：`ComfyUI` / `ComfyUIProxy` /
`ComfyUIAuthGate`。**不可**設成「不論使用者是否登入」—— 那會掉進 Session 0，
看不到桌面也拿不到 GPU 桌面環境。

防火牆：`ComfyUI 8188` 規則的 `remoteip` 白名單 =
`100.64.0.0/10`（整個 tailnet）+ `192.168.1.132`（NAS 主機）+
`192.168.1.58`（cloudflared 容器）。

> **本目錄的 `.py` 為什麼放在 `docs/` 底下**：`services/` 在
> `ota_manifest.AGENT_DIRS` 裡，放那會被打進全機隊的 OTA ZIP —— 但這支程式
> 只跑在 GPU 機上，跟 agent 無關。`docs/` 不進 OTA。

---

## 5. 🔴 四個坑（每一個都真的咬過）

### 5.1 Caddy 每次重啟就被 Windows 自動封鎖

症狀：服務明明在跑，從外面連卻**逾時**（不是拒絕）。

原因：程式開始監聽時若沒有現成防火牆規則，Windows 想跳「要允許嗎」提示，
無人值守跳不出來 → 直接寫兩條 `Block / Inbound / Private` 規則。而
**封鎖規則優先於允許規則**，所以你另外加的 allow 完全沒用（實測過，沒用）。

根治：

```powershell
Set-NetFirewallProfile -Profile Domain,Private,Public -NotifyOnListen False
```

⚠️ 這個參數吃的是 `GpoBoolean` 列舉，**不能傳 `$false`**，要傳字串 `False`。

排查用：`Get-NetFirewallRule -DisplayName 'caddy.exe'`（應該是 0 條）。

### 5.2 Cloudflare 進來一片 502、Tailscale 卻正常

症狀：公開網址卡在 ComfyUI 的啟動 logo，DevTools 看到幾十個 JS chunk 回 502。

原因：**併發量差很多**。瀏覽器對 HTTP/1.1 站點只開 ~6 條連線；走 Cloudflare
是 HTTP/2 多工，cloudflared 會同時對源站開幾十條。ComfyUI 前端一次拉 40 個
chunk，每一條都觸發一次 forward_auth 子請求打到 authgate ——
Python `ThreadingHTTPServer` 的 **listen backlog 預設只有 5**，滿了就拒連，
Caddy 判定 auth upstream 掛掉，回 502。

修法：`authgate.py` 的 `Gate` 類別設 `request_queue_size = 256`。

### 5.3 cloudflared 的來源 IP 不是 NAS 主機 IP

症狀：Cloudflare 回 502，但從 NAS 主機 `curl` GPU 機是通的。

原因：NAS 的 cloudflared 容器跑在 **QNAP qnet macvlan**
（`qnet-dhcp-bond0-6d6da6`），直接掛在區網上、**有自己獨立的 DHCP 位址**
（當時是 `192.168.1.58`，MAC `02:42:aa:a2:07:30`），不會被 NAT 成主機 IP。

⚠️ **那是 DHCP**。容器重建或租約到期換位址，就會再次 502、症狀一模一樣。
路由器上該對那個 MAC 設 DHCP 保留位址。排查第一步永遠是：確認容器現在的 IP
還是不是白名單裡那個。

查法：

```sh
/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker inspect cloudflared | grep IPAddress
```

### 5.4 `Invoke-WebRequest` 下載大檔會停滯

下載 ComfyUI 的 2GB 包時，`Invoke-WebRequest` 到 261 MB 就完全不動（0 MB/s）。
換 `curl.exe` 立刻 **44 MB/s**。PowerShell 的進度串流會把大檔下載拖死。
Windows 10+ 內建 `curl.exe`，而且支援 `-C -` 續傳。

---

## 6. 從零重建

1. **階段 1（遠端存取）** —— 電源設定、Tailscale（記得在 admin console 關
   key expiry）、OpenSSH Server、RDP。
2. **ComfyUI** —— 抓 `ComfyUI_windows_portable_nvidia.7z`（**不是** `_amd`、
   不是 `_intel`、不是 `_cu126`），解壓到 `D:\AI`。用 GitHub API 抓 latest，
   別寫死檔名（portable 包改過好幾次名字）。
3. **Caddy** —— `winget install CaddyServer.Caddy`，Caddyfile 用本目錄那份。
4. **authgate** —— 本目錄的 `authgate.py` 放到 `D:\AI\authgate\`，
   再把 master 的 `settings.json` 裡的 `jwt_secret` 寫成同目錄的 `jwt.secret`
   （64 字元、無換行），ACL 鎖成只有 Administrators + SYSTEM。
5. **防火牆** —— 5.1 的 `NotifyOnListen False`，加 8188 的白名單規則。
6. **排程工作** —— 三個都 onlogon、`/ru bot`、`/rl highest`。
7. **Cloudflare** —— Zero Trust 通道加 public hostname 指向
   `http://192.168.1.85:8188`（URL 欄位**不要有尾端空白**，會報
   `invalid port ":8188 " after host`）。

驗收（每一項都要真的跑過，不要看到服務在跑就當成好了）：

```powershell
# 閘門本身
curl.exe -s -o NUL -w '%{http_code}' http://127.0.0.1:8188/            # 302
curl.exe -s -o NUL -w '%{http_code}' -H "Cookie: og_comfy=<有效token>" http://127.0.0.1:8188/   # 200
# WebSocket（ComfyUI 的進度回報靠它；記得加 -m 否則 curl 會一直掛著）
curl.exe -s -o NUL -w '%{http_code}' -m 8 -H 'Connection: Upgrade' -H 'Upgrade: websocket' `
  -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' `
  http://127.0.0.1:8188/ws                                             # 101
```

**最後一定要用真的瀏覽器驗**（Playwright 就夠）。5.2 那個 502 在 curl 下
完全看不出來 —— curl 一次只發一個請求，永遠不會觸發 backlog 溢出。

```python
# 重點是 splash gone / canvas 這兩個，不是 HTTP 200
pg.goto(URL); pg.wait_for_timeout(20000)
assert pg.query_selector('#splash-loader') is None
assert pg.query_selector('canvas') is not None
```

---

## 7. 安全邊界

- ComfyUI 只聽 `127.0.0.1:8189` —— **唯一入口是 Caddy**，走 Tailscale 也一樣要登入。
- `--listen 0.0.0.0` 絕對不要用在 ComfyUI 上。它沒有任何內建登入機制，
  等於把「那台機器上的任意程式碼執行」開放給整個網段。
- 公開網址的限制（走 Tailscale 沒有）：Cloudflare 免費方案**上傳 100 MB 上限**、
  HTTP 請求 **100 秒逾時**。傳大圖、video-to-video 這類重活走 Tailscale。
- `jwt.secret` 是與 master 共用的那一把。輪替 JWT secret 時**這裡也要換**，
  否則所有人的 cookie 同時失效（見 `reference_jwt_secret_topology`）。
