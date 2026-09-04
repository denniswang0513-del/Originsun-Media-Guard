# ComfyUI GPU 機 + 帳號閘門

> 📅 最後更新：2026-08-21　—　🔴 規格與程式碼分歧時**以程式碼為準**。
> 這份文件不會自動跟著改，日期越舊越要當心（用 `git log -1 -- docs/COMFYUI_GATEWAY.md` 確認）。

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

---

## 8. 模型與實測數字（2026-08-21）

模型放在 **`D:\AI\models`**（不在 portable 資料夾裡），由 ComfyUI 的
`extra_model_paths.yaml` 指過去 —— 這樣更新或重裝 ComfyUI 不會碰到那幾十 GB。

| 模型 | 大小 | 用途 |
|---|---|---|
| ltx-2.5-22b-distilled-transformer (int8) | 20.5 GB | **主力影片模型** |
| gemma4-12b-with-proj (int8) | 14.3 GB | LTX-2.5 的文字編碼器 |
| gemma4_e2b_it | 9.6 GB | LTX-2.5 的第二個文字編碼器 |
| ltx-2.5-video-vae / audio-vae | 1.4 GB / 348 MB | LTX-2.5 |
| ltx-2.5-latent-spatial-upscaler-x2 | 950 MB | LTX-2.5 內建的潛在空間放大 |
| wan2.2_ti2v_5B + umt5_xxl + vae | 17.3 GB | Wan 2.2（已被 LTX-2.5 取代，保留備用）|
| seedvr2_3b_int8 + vae | 3.8 GB | 像素空間影片放大／修復 |

### 實測（RTX 5060 Ti 16GB，全部 121 幀 @ 24fps = 5 秒）

| 路徑 | 解析度 | 時間 | 峰值 VRAM | 音軌 |
|---|---|---|---|---|
| Wan 2.2 5B | 1280×704 | 538 s | 15733 MiB | ✗ |
| Wan 2.2 5B | 1920×1088 | **3226 s** | 15955 MiB | ✗ |
| Wan 720p → SeedVR2 ×1.5 | 1920×1056 | 1544 s | 15941 MiB | ✗ |
| **LTX-2.5** | 1280×704 | **105 s** | 15288 MiB | ✓ |
| **LTX-2.5** | **1920×1088** | **196 s** | 15484 MiB | ✓ |

**結論：影片一律用 LTX-2.5，直接生 1080p。** 比 Wan 生 1080p 快 16 倍、比 Wan 生
720p 還快 3 倍，而且附同步音軌、支援原生多鏡頭、文字渲染得出來。

「低解析生成 → 放大」那套兩段式**只在用 Wan 時才划算**（25.8 分 vs 53.8 分）。
LTX-2.5 出現後它沒有存在意義了 —— SeedVR2 留著是給**既有素材**做修復／放大用，
不是給生成流程用。

⚠️ 105 秒那次含冷啟動載入模型；196 秒那次模型已常駐，**不含載入**。
每次冷啟動大約要多一分鐘。

---

## 9. 怎麼跑一次

`docs/comfyui_gateway/run_template.py`（在 master 上跑，透過 Tailscale 打 GPU 機）：

```bash
# LTX-2.5 文生影片，預設 0.9 百萬像素 = 1280×736
.venv/Scripts/python.exe docs/comfyui_gateway/run_template.py video_ltx2_5_t2v

# 1080p
LTX_MP=2.0 .venv/Scripts/python.exe docs/comfyui_gateway/run_template.py video_ltx2_5_t2v

# 只轉換不送出（看 API 圖長什麼樣）
DRY=1 .venv/Scripts/python.exe docs/comfyui_gateway/run_template.py video_ltx2_5_t2v
```

**LTX 的解析度是用百萬像素驅動的**（頂層 `ResolutionSelector`），不是節點的寬高。
範本自附的對照表（16:9，multiple=32）：

```
0.5 → 960×544    0.9 → 1280×736（預設）    1.0 → 1376×768
1.5 → 1664×928   2.0 → 1920×1088
```

其他環境變數：`SEEDVR2_SCALE`（放大倍率）、`SEEDVR2_CHUNK`（每塊幀數）、
`LOADVIDEO`（要放大的來源檔，需先放進 ComfyUI 的 `input/`）。

---

## 10. 🔴 模型與範本的坑

### 10.1 curl 會把 HTTP 錯誤訊息寫進 .safetensors，而大小檢查驗不出來

第一次下載 LTX-2.5 時 HF 回 401（門禁 repo），**curl 把錯誤訊息內文寫進了檔案**
（約 200 bytes）。清理時只刪 `Length -eq 0` 的檔 —— 這些檔不是 0，躲過了。
第二次用 `curl -C -` 續傳，把真資料**接在錯誤訊息後面**：

```
檔案 = [401 錯誤訊息] + [從第 200 byte 開始的真實模型資料]
總大小 == Content-Length   ← 大小檢查完全通過
safetensors 標頭 = 垃圾    ← 載入時才炸
```

症狀是 `VAELoader` 噴 `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xfd`，
或標頭長度變成 `8367815047113827137`（那是把 `"o model L"` 當 int64 讀出來的值）。

**根治：`curl --fail`** —— HTTP 錯誤時完全不寫檔案。
**驗證要讀內容不能只看大小**：safetensors 前 8 bytes 是標頭長度（小端 int64），
第 9 個 byte 必須是 `{`。

### 10.2 `Get-ChildItem` 對正在寫入的檔案回報過期大小

下載進行中用 `Get-ChildItem` 看會一直是 0 MB，因為它讀的是快取的目錄 metadata。
要用 `(New-Object IO.FileInfo $p)` 再 `.Refresh()` 才看得到真實大小。
（我因此誤判過好幾次「下載沒動」。）

### 10.3 HF 門禁 repo：細粒度 token 預設不通

`Lightricks/LTX-2.5` 是門禁 repo。**401 = 沒憑證；403 = 憑證有效但沒授權**。
細粒度（fine-grained）token 即使有 read 權限，**還要另外勾
「Read access to contents of all public gated repos you can access」**，
否則一律 403 而且錯誤訊息不會說原因。用典型（classic）Read token 最省事。
token 存在 GPU 機 `D:\AI\hf_token.txt`。

### 10.4 新版範本用 subgraph，手寫的 UI→API 轉換器會壞掉

`video_ltx2_5_t2v.json` 頂層只有 7 個節點，真正的 40 個節點在
`definitions.subgraphs` 裡。另外 KSampler 的 seed 後面跟著一個
`control_after_generate` 小工具（`"randomize"`），API schema 裡沒有這個輸入，
**會讓後面所有值位移一格**（steps 拿到 `"randomize"`、cfg 拿到 20）。

不要自己寫轉換器。`run_template.py` 用無頭瀏覽器呼叫 ComfyUI 前端自己的
`app.graphToPrompt()` —— 那就是人按下 Run 時走的同一條路，subgraph、seed 小工具、
被 bypass 的節點全都自動處理。

### 10.5 「改了沒變化」不等於「這個維度無關」——🔴 我在這裡判斷錯過一次

SeedVR2 放大 121 幀時 OOM（宣稱要 28.3 GiB）。我把分塊從 `auto` 改成手動 13 幀，
**報錯數字一個位元組都沒變**，於是我推論「爆的是空間維度不是時間維度」，
改成 ×1.5 就過了，看起來也印證了。

**那個推論是錯的。** 2026-09-03 有人拿 10 秒的素材（242 幀、同樣的空間尺寸）
再次 OOM 才查出真因：subgraph 有一個 **`split_latent` 開關，預設 `false`**：

```
PrimitiveBoolean id=105  widgets=[false]      <- split_latent
ComfySwitchNode  id=102  switch <- 105
                         on_false <- VAEEncodeTiled       <- 整段直接進 KSampler
                         on_true  <- SeedVR2TemporalChunk
```

關著的時候，**分塊節點根本不在執行路徑上**。我改的 `frames_per_chunk` 是寫進一個
不會被執行的節點 —— 數字當然不會變。降到 ×1.5 只是讓 121 幀剛好塞得下，
把問題往後推了兩週。

`DRY=1` 能看到「值有沒有進 API 圖」，但**看不到那個節點會不會被執行**。要確認一個
開關類的設定真的生效，得看它下游的 switch 節點取的是哪一邊。

打開 `split_latent` + 手動分塊之後：

| | 5 秒 / 121 幀 | 10 秒 / 242 幀 |
|---|---|---|
| 未分塊 | 1021 s，峰值 16004 MiB（98%） | **OOM** |
| 分塊（21 幀/塊、overlap 4）| — | **811 s**，峰值 15424 MiB（95%）|

**影片長一倍、時間反而少 20%** —— 工作集變小就不必一直往 RAM 搬。
每秒影片的成本從 3.4 分鐘降到約 1.3 分鐘。接縫實測看不出來（overlap 4 會交叉淡化）。

---

## 11. 給同事用的介面：ComfyUI 內建的「應用程式」

不需要自己開發前端。ComfyUI 1.48+ 內建**線性模式**（工具列的「建立應用程式」），
可以把節點圖收起來、只留幾個表單欄位。開關就在工作流 JSON 的 `extra`：

```json
"linearMode": true,
"linearData": {
  "inputs":  [[73, "file"]],     // [節點 id, 欄位名]
  "outputs": [76]                // 哪個節點是輸出
}
```

工作流放進 GPU 機的
`D:\AI\ComfyUI_windows_portable\ComfyUI\user\default\workflows\`，
同事登入後從「應用程式」下拉就選得到。

### 已建好：素材修復放大

`docs/comfyui_gateway/app_restore_upscale.json`
（機器上的檔名是 `素材修復放大.json`）。同事只看得到「選擇影片」+「執行」。

以 SeedVR2 3B int8 為基礎，改了一個關鍵設定：`ResizeImageMaskNode` 從
**「scale by multiplier」換成「scale longer dimension」= 1920**。

> 🔴 **倍率不可以交給使用者。** ×1.5 跑 1280×704 的來源峰值就 15941 MiB / 16311，
> 同一個設定丟一支 1080p 進去（→ 2880×1632）必爆。改成指定長邊之後，
> **不管來源多大輸出都是 1080p**，使用者設不出會爆的值。

實測（已開啟 `split_latent`、21 幀/塊、overlap 4）：
1280×720 / 242 幀 / 10 秒 → 1920×1080，**811 秒（13.5 分）**，峰值 **15424 MiB（95%）**。
約 **1.3 分鐘 / 每秒影片**。同時只能跑一支。

> 🔴 `split_latent` 預設是關的，關著就等於沒有分塊 —— 超過約 5 秒的素材會 OOM。
> 見 §10.5。

### 還沒驗證的兩件事

- **app 在使用者之間怎麼共享** —— 工作流存在 `user/default/` 底下，大家共用同一個
  ComfyUI 實例。沒實測過另一個帳號登入後看不看得到。
- **產出沒有按人分開** —— 全部落在同一個 `output/video/`。

---

## 12. 圖片放大 app

`docs/comfyui_gateway/app_image_upscale.json`（機器上叫 `圖片放大.json`）。
同事只看得到「選圖片」+「執行」。用的是與影片修復**同一組模型**
（`seedvr2_3b_int8` + `seedvr2_ema_vae_fp16`），不需要另外下載。

同樣把倍率換成固定長邊 —— 官方範本預設 **×4**，那對使用者不安全（丟一張
4000 px 的圖進來就是 16000 px）。改成 **`scale longer dimension = 4096`**。

實測（RTX 5060 Ti 16GB）：

| 來源 | 目標長邊 | 時間 | 峰值 VRAM |
|---|---|---|---|
| 768×768 | 2048 | 15 s | 6809 MiB（42%）|
| 768×768 | 4096 | 60 s | 11641 MiB（71%）|
| 640×352（真實照片）| 4096 | 30 s | 8345 MiB（51%）|

圖片比影片便宜非常多（影片是 95%+ 且一次只能一支），所以 4096 有充足餘裕。

> ⚠️ 測試素材要挑對。第一次我用 `example.png`（純色塊抽象圖）比對，
> 上下兩張幾乎一樣 —— **那不是「放大器沒效果」，是那張圖沒有細節可以還原**。
> 換成真實照片（640 px → 4096，6.4 倍）才看得出差別：臉部五官、髮絲、
> 標示牌文字、牆面石材質感全部重建出來。

---

## 13. 用 API 接出去

ComfyUI 的 REST API 可以直接被 Media Guard 呼叫，**認證是現成的** —— 閘門吃的就是
同一把 `jwt_secret` 簽的 JWT：

```python
from core.auth import create_token
cookie = 'og_comfy=' + create_token({'sub': 'mediaguard', 'access_level': 3,
                                     'modules': ['comfyui']})
```

固定的工作流**只要轉一次** API 格式就好，本目錄已經備妥：

| 檔案 | 呼叫端唯一要填的欄位 |
|---|---|
| `api_image_upscale.json`（13 節點）| `graph['1']['inputs']['image']` |
| `api_restore_upscale.json`（20 節點）| `graph['73']['inputs']['file']` |

（用 `run_template.py` 加 `DUMP_API=<路徑>` 可以重新產生。）

可跑的範例：`api_client_demo.py`。實測 `POST /prompt` 回 `prompt_id`、
`node_errors: none`，全程沒有瀏覽器。

| 端點 | 用途 |
|---|---|
| `POST /upload/image` | 上傳輸入檔（影片也走這支）|
| `POST /prompt` | 送出，回 `prompt_id` |
| `GET /history/{id}` | 結果（輸出檔名、成功與否）|
| `GET /view?filename=..&subfolder=..&type=output` | 下載產出 |
| `GET /queue`、`POST /interrupt`、`POST /queue {"clear":true}` | 佇列控制 |
| `WS /ws` | 即時進度 |

`/api/` 前綴也通（`/queue` 與 `/api/queue` 等價）。

**設計上要注意**：這是長工作（10 秒影片＝13.5 分），必須非同步 —— 送出拿
`prompt_id`、記進 DB、背景輪詢 `/history`，不能做成 request-response。
而且同時只能跑一支。

---

## 14. 補幀：兩個 app

`app_interp_smooth.json`（機器上 `影片補幀（更順）.json`）
`app_interp_slowmo.json`（機器上 `影片慢動作.json`）

用 ComfyUI **原生節點**（`FrameInterpolationModelLoader` + `FrameInterpolate`），
不需要自訂節點。模型 `rife_v4.26.safetensors` 只有 **21.6 MB**，放
`D:\AI\models\frame_interpolation\`（該路徑要加進 `extra_model_paths.yaml`，
本來沒有）。

### 為什麼是兩個 app 不是一個開關

範本有個 `enable_fps_multiplier`，切的是兩件**產品上完全不同**的事：

| | 開關 | 24fps / 5 秒的素材變成 |
|---|---|---|
| 更順 | true | **48fps / 5 秒**（片長不變）|
| 慢動作 | false | **24fps / 10 秒**（半速）|

兩者都已實測驗證過輸出規格。做成一個開關等於要同事自己猜，跟「倍率」那個是同一類問題。

### 🔴 更順模式不要用在實拍敘事素材上

24fps 的實拍補到高幀率會產生**肥皂劇效果** —— 電影感消失、像電視劇或家用 DV。
它的適用對象是**動態圖形／VJ 動畫／展場內容**，不是客戶的正片。

### 模型選擇（實測，不是憑印象）

同一支素材、同樣 2×，兩個模型各跑一次：

| 模型 | 10 秒 VJ | 5 秒實拍 | 峰值 VRAM |
|---|---|---|---|
| **rife_v4.26** | **30 s** | **15 s** | **1012 / 628 MiB** |
| film_net_fp16 | 75 s | 45 s | 4404 / 3924 MiB |

抽補出來的影格（輸出的奇數幀才是模型生成的）比對，**兩者看不出差別** ——
包含列車進站那種快速運動也沒有鬼影或撕裂。RIFE 快 3 倍、輕 6 倍，選它。

### 沒有長度上限

| 素材長度 | 時間 | 峰值 VRAM |
|---|---|---|
| 5 秒 / 121 幀 | 15 s | 628 MiB |
| 10 秒 / 242 幀 | 30 s | 1012 MiB |
| **60 秒 / 1452 幀** | **105 s** | **1044 MiB** |

從 10 秒到 60 秒 VRAM 只多 32 MiB —— 它是**逐對影格串流處理**。這跟 SeedVR2
完全不同（那個 5 秒就吃到 98%、10 秒不分塊就 OOM），所以補幀**不需要分塊、
也沒有長度天花板**。約 1.7 秒處理時間 / 每秒影片。

### API

`api_interp_smooth.json` / `api_interp_slowmo.json`（各 10 節點），
呼叫端填 `graph['4']['inputs']['file']`。
