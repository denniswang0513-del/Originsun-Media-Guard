# Originsun Media Guard Pro — 測試套件建置指令

> 將以下指令依序貼給 Claude Code 執行。每個指令都是獨立任務，完成後再給下一個。

---

## 🔬 實測參數 (實際執行用)

- **本機測試網址**: `http://localhost:8000/`
- **來源資料夾 (CARD A)**: `U:\20260205_寬微廣告_影片中文化\09_Export\01_Check`
- **來源資料夾 (CARD B)**: `R:\ProjectYaun\20260304_幾莫_鴻海科技獎短影音\09_Export\01_Check`
- **測試目的地**: `D:\Antigravity\OriginsunTranscode\test_zone`

> [!IMPORTANT]
> **測試完成確認無誤後，自動清理測試產生檔案。**

---

## 🔁 自我除錯協議（每個指令都適用）

**每寫完一段程式碼，立刻執行它，不要等到全部寫完才跑。**
遇到錯誤時，依照以下順序自行處理，不要停下來等待指示：

```
1. 執行 → 看到錯誤
2. 讀完整 traceback，找根本原因（不只看最後一行）
3. 假設原因 → 修改 → 再執行
4. 同一個錯誤出現 3 次還沒解決：
   a. 讀相關原始碼（config.py、core/state.py 等）確認實際介面
   b. 不要憑記憶猜測 API，以實際程式碼為準
   c. 修改後再試
5. 嘗試 5 次仍失敗：加 TODO 說明卡關原因，繼續下一個測試案例
```

**每個指令都要注意的常見陷阱：**

- **import 路徑**：從專案根目錄執行 pytest，所有 import 以根目錄為基準
- **async fixture**：pytest-asyncio 的 async fixture 用 `@pytest_asyncio.fixture`，不是 `@pytest.fixture`
- **patch 目標**：patch「被測模組裡 import 進來的名稱」，不是原始定義的位置
  例：`taiwan_normalizer.py` 裡 `import json`，要 patch `utils.taiwan_normalizer.json`
- **Windows 路徑**：用 `os.path.join` 或 `pathlib.Path`，不要寫死 `/` 或 `\`
- **scope 衝突**：function-scope fixture 不能依賴 session-scope fixture

---

## 指令 0：環境準備

安裝測試依賴並建立目錄結構。

**步驟 1 — 安裝套件：**
```
d:\Antigravity\OriginsunTranscode\.venv\Scripts\pip.exe install pytest pytest-asyncio pytest-mock httpx pytest-cov playwright
```
→ 若失敗：確認 .venv 路徑，或改用 `python -m pip install`

**步驟 2 — 安裝 Playwright 瀏覽器：**
```
d:\Antigravity\OriginsunTranscode\.venv\Scripts\playwright.exe install chromium
```
→ 若失敗（網路問題）：加 `--with-deps` 重試

**步驟 3 — 建立目錄（執行這段 Python）：**
```python
from pathlib import Path
for d in ["tests/unit", "tests/integration", "tests/e2e", "tests/e2e/screenshots", "tests/e2e/fixtures"]:
    Path(d).mkdir(parents=True, exist_ok=True)
    (Path(d) / "__init__.py").touch()
Path("tests/__init__.py").touch()
print("完成")
```

**步驟 4 — 寫入 pytest.ini：**
```ini
[pytest]
asyncio_mode = auto
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
markers =
    unit: 單元測試
    integration: 整合測試
    e2e: 端對端測試
    smoke: 冒煙測試
```

**驗證：** 執行 `pytest --collect-only`
→ 顯示 0 items selected，不應有 ImportError
→ 若有錯誤，修正後再繼續

---

## 指令 1：共用 Fixtures（conftest.py）

**開始前先讀這些檔案，確認實際介面：**
- `config.py` → SETTINGS_PATH 的型別和名稱
- `core/state.py` → global_conflict_action、conflict_event 的實際名稱
- `core/engine_inst.py` → engine 物件的屬性名
- `main.py` → io_app 的名稱

**請寫 `tests/conftest.py`，包含以下 5 個 fixtures：**

**1. `tmp_settings(tmp_path, monkeypatch)`**
- 在 tmp_path 建立 settings.json，含 nas_root / local_root / proxy_root 和 notifications（全部 enabled=False）
- `monkeypatch.setattr("config.SETTINGS_PATH", str(tmp_path / "settings.json"))`
- scope="function"

**2. `tmp_dict(tmp_path)`**
- 建立 taiwan_dict.json，vocab_mapping 3 筆、pronunciation_hacks 2 筆
- return Path 物件

**3. `mock_engine(mocker)`**
- mocker.patch 把 core.engine_inst 的 engine 換成 MagicMock
- 所有 run_*_job 方法 return None，get_xxh64 return "abcd1234abcd1234"
- scope="function"

**4. `async_client(mock_engine)`** ← 用 `@pytest_asyncio.fixture`
- httpx.AsyncClient + ASGITransport 包裝 main.io_app，base_url="http://test"
- scope="function"

**5. `real_server()`**
- subprocess 啟動 uvicorn port 18000，環境變數加 MOCK_FFMPEG=1 和 MOCK_NAS=1
- 輪詢 /api/v1/health 最多 15 秒，yield {"base_url": "http://localhost:18000"}
- teardown：process.terminate() + process.wait(timeout=5)
- scope="session"

**驗證：** `pytest tests/ --collect-only`
→ conftest 載入沒有 ImportError
→ 若出現 "fixture not found"：檢查函式名是否拼錯
→ 若 async_client 有 ScopeMismatch：確認用了 @pytest_asyncio.fixture

---

## 指令 2：Layer 1 — 台灣正音引擎測試

**開始前先讀 `utils/taiwan_normalizer.py`，確認：**
- 函式名稱是否為 normalize_for_taiwan_tts
- 字典路徑怎麼決定（全域常數？__file__ 相對路徑？）
- 是否每次呼叫重新讀檔

**根據讀到的程式碼決定 patch 策略，並在每個測試的 docstring 說明原因：**
- 若有全域常數 DICT_PATH：`mocker.patch("utils.taiwan_normalizer.DICT_PATH", new=tmp_dict)`
- 若函式內直接 open()：patch 包裝好的讀檔函式（若有），或用 monkeypatch.chdir 讓相對路徑指向 tmp_dict

**請寫 `tests/unit/test_taiwan_normalizer.py`，實作以下 8 個測試：**

1. `test_vocab_single` — 視頻→影片
2. `test_vocab_multiple` — 視頻+軟件同時替換
3. `test_pronunciation_hack` — 垃圾→勒色
4. `test_no_match_passthrough` — 無命中原樣回傳
5. `test_empty_string` — 空字串不 crash
6. `test_longest_match_first` — 手機APP 優先匹配長詞
7. `test_hot_reload` — 修改 json 後不重啟也生效
8. `test_combined` — vocab + pronunciation 同一句話

**驗證：** `pytest tests/unit/test_taiwan_normalizer.py -v -s`

→ ModuleNotFoundError：`tests/__init__.py` 存在嗎？pytest.ini 的 testpaths 對嗎？
→ patch 無效（替換沒發生）：重讀 taiwan_normalizer.py，確認字典路徑取得方式，調整 patch 目標
→ test_longest_match_first 失敗且 normalizer 確實沒實作：加 pytest.skip 並加 TODO
→ test_hot_reload 失敗且有快取：加 pytest.skip 並加 TODO

---

## 指令 3：Layer 1 — config.py 測試

**開始前先讀 `config.py`，確認：**
- SETTINGS_PATH 是字串還是 Path
- init_settings() 的預設值結構
- load_settings() 是否有 deep merge
- save_settings() 的參數型別

**請寫 `tests/unit/test_config.py`，實作以下 6 個測試：**

1. `test_init_creates_file` — init_settings() 後檔案存在
2. `test_init_skips_existing` — 已有檔案時不覆蓋（先寫入 marker，init 後 marker 還在）
3. `test_load_returns_dict` — 回傳型別是 dict
4. `test_load_merges_missing_keys` — 只有部分 key 時 load 回來有完整預設 key（若 config.py 沒有 merge，改成確認「不 crash」並加 TODO）
5. `test_save_and_reload` — save → load → 值一致
6. `test_save_partial_preserves_others` — save {"a":1,"b":2} → save {"a":99} → load 後 b 仍存在

**驗證：** `pytest tests/unit/test_config.py -v -s`

→ patch 沒生效：確認 `monkeypatch.setattr("config.SETTINGS_PATH", ...)` 模組路徑正確
→ test_load_merges 失敗且 config.py 確實無 merge：調整測試驗證實際行為，加 TODO

---

## 指令 4：Layer 1 — core_engine 純邏輯測試

**開始前先讀 `core_engine.py`，確認：**
- MediaGuardEngine.__init__ 的必填參數
- get_xxh64 的函式簽名和回傳格式（有無 0x 前綴？大小寫？）
- _pause_event / _stop_event 是否為 threading.Event
- 有無 reset_stop 或類似方法

**請寫 `tests/unit/test_core_engine.py`，實作以下 6 個測試：**

1. `test_xxh64_consistency` — 同一個 1MB 檔案跑兩次結果相同
2. `test_xxh64_different_files` — 不同內容的檔案 hash 不同
3. `test_xxh64_result_format` — 回傳 16 字元 hex 字串（驗證長度和字元集）
4. `test_stop_event` — request_stop() 後 _stop_event.is_set() == True
5. `test_pause_resume` — pause 後 is_set()==False，resume 後 is_set()==True
6. `test_stop_reset` — 若有 reset 方法就測；若無，pytest.skip 並加 TODO

**驗證：** `pytest tests/unit/ -v`
→ 全部應通過
→ MediaGuardEngine 初始化就 crash：看 __init__ 是否立刻呼叫 ffmpeg，找 dry_run 或 mock 方式
→ xxh64 長度不是 16：可能帶 "0x" 或大寫，調整 assertion 符合實際格式

---

## 指令 5：Layer 2 — 系統 API 整合測試

**開始前，先建暫時驗證檔確認 async_client 能用：**

在 `tests/integration/` 建 `_test_ping.py`（前綴 _ 讓 pytest 不自動收集）：
```python
async def test_ping(async_client):
    r = await async_client.get("/api/v1/health")
    print(r.status_code, r.text[:200])
```
執行 `pytest tests/integration/_test_ping.py -v -s`，確認通過後刪掉這個檔案。

**請寫 `tests/integration/test_api_system.py`，實作以下 7 個測試：**

1. `test_health_returns_200` — GET /api/v1/health，200，JSON 含 "status"
2. `test_version_format` — GET /api/v1/version，用 re.match 確認 x.y.z 格式
3. `test_status_structure` — GET /api/v1/status，先 print(data.keys()) 確認欄位名，再 assert
4. `test_settings_roundtrip` — load → 加 `__test_marker__` → save → load → 確認 marker 存在
5. `test_list_dir_videos` — tmp_path 建 3 個 .mp4 + 1 個 .txt → list_dir → 回傳 3 筆
6. `test_list_dir_bad_path` — 不存在的路徑 → status != 500（不應 crash）
7. `test_stop_when_idle` — POST /api/v1/control/stop → status 200-204

**驗證：** `pytest tests/integration/test_api_system.py -v -s`

→ async_client fixture 找不到：conftest.py 用了 @pytest_asyncio.fixture 嗎？
→ /api/settings/load 回 404：讀 routers/api_system.py 確認實際路徑
→ status 欄位 assertion 失敗：看 print 輸出，用實際欄位名調整
→ list_dir 回傳格式不是 list：print(r.json()) 看實際格式，調整 assertion

---

## 指令 6：Layer 2 — 任務佇列與 Socket 事件測試

**開始前先讀：**
- `core/worker.py` — _background_worker 完整邏輯
- `core/state.py` — worker_busy 的實際變數名
- `core/logger.py` — _emit_sync 的完整路徑

**emit 攔截策略（重要）：**
因為 worker 在執行緒裡用 _emit_sync 而非直接 await sio.emit，正確攔截方式：
```python
emitted = []
mocker.patch("core.logger._emit_sync", side_effect=lambda e, d=None: emitted.append((e, d)))
```
若路徑不對，先跑一次看 traceback，再調整模組路徑。

**BackupRequest 最小合法 payload（根據 CLAUDE.md Schema）：**
```python
MINIMAL_BACKUP = {
    "task_type": "backup", "project_name": "TestProject",
    "local_root": "/tmp/local", "nas_root": "/tmp/nas", "proxy_root": "/tmp/proxy",
    "cards": [["/tmp/card1", "Card_A"]],
    "do_hash": False, "do_transcode": False, "do_concat": False, "do_report": False
}
```

**請寫 `tests/integration/test_task_queue.py`，實作以下 3 個測試：**

1. `test_job_enqueued` — POST job → 立刻 GET status → worker_busy==True 或 queue_size>0（任一）
2. `test_task_done_event` — mock run_backup_job + 攔截 emit → POST job → asyncio.sleep(1.0) → 確認收到 ("task_status", {"status": "done"})
3. `test_serial_execution` — mock slow_job（sleep 0.3s）→ 連送兩個 job → sleep(1.0) → 確認 log == ["start","end","start","end"]

**驗證：** `pytest tests/integration/test_task_queue.py -v -s`

→ done_events 是空的：_emit_sync patch 路徑不對，在 core/logger.py 找實際定義位置
→ slow_job 是同步但用 await 呼叫：看 run_backup_job 是否為 sync，改用 time.sleep
→ serial log 是 ["start","start","end","end"]：讀 worker.py 確認串行機制

---

## 指令 7：Layer 2 — 衝突解決流程測試

**開始前先讀 `core/worker.py` 裡的 `_on_conflict` 函式，確認：**
- 是 def 還是 async def
- timeout 秒數和變數名（可能叫 CONFLICT_TIMEOUT）
- 從 state 讀哪些變數
- 回傳值型別

**請寫 `tests/integration/test_conflict_resolution.py`，實作以下 4 個測試：**

1. `test_global_skip` — state.global_conflict_action="skip" → _on_conflict 回傳 "skip"，sio.emit 沒被呼叫
2. `test_global_overwrite` — 同上，"overwrite"
3. `test_manual_resolution` — 另一個 thread 0.05s 後 set conflict_event → _on_conflict 回傳 "copy"
4. `test_timeout_defaults_skip` — patch CONFLICT_TIMEOUT=0.3 → 不觸發 event → 回傳 "skip"，elapsed < 1.0s

**驗證：** `pytest tests/integration/test_conflict_resolution.py -v -s`

→ _on_conflict 不在模組層級：讀 worker.py 確認位置，調整 import
→ CONFLICT_TIMEOUT 名稱不對：grep worker.py 找 timeout 字串
→ test_manual_resolution 卡住：讀 _on_conflict 裡的等待機制，確認 conflict_event 使用方式

---

## 指令 8：E2E 測試 — Playwright 基礎設定

**開始前先讀 `frontend/index.html`，找出：**
- 頁籤按鈕的 HTML（class? id? data-tab? 按鈕文字？）
- section 的切換機制（display:none? class toggle?）
- Socket 連線狀態指示器的 selector

**請寫 `tests/e2e/conftest.py`：**
```python
import pytest
from playwright.sync_api import sync_playwright

@pytest.fixture(scope="session")
def browser_context():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        yield context
        context.close()
        browser.close()

@pytest.fixture(scope="session")
def page(browser_context, real_server):
    p = browser_context.new_page()
    p.on("console", lambda msg: print(f"[BROWSER {msg.type}] {msg.text}"))
    p.goto(real_server["base_url"] + "/")
    p.wait_for_load_state("networkidle")
    yield p
    p.close()
```

**請寫 `tests/e2e/test_ui_basic.py`（標記 `pytestmark = pytest.mark.e2e`），實作以下 5 個測試：**

每個測試開始和結束都呼叫 `page.screenshot(path=f"tests/e2e/screenshots/{test_name}.png")`。

1. `test_page_title` — page.title() 含 "originsun"（不分大小寫）
2. `test_all_tabs_visible` — 先 print 所有 button 文字，再確認 7 個頁籤都存在；找不到時說明哪個不存在
3. `test_tab_switching` — 點「驗證」→ print 所有 section 的 visible 狀態 → 確認驗證 section 可見
4. `test_backup_tab_fields` — 切備份頁籤 → print 所有 input 的 id/placeholder → 確認專案名稱欄位和開始按鈕存在
5. `test_socket_connected` — print 連線相關元素 → wait_for_selector（根據 print 結果調整），timeout=8000

**驗證：** `pytest tests/e2e/test_ui_basic.py -v -s -m e2e`

→ 第一次跑幾乎一定有 selector 不對，這是正常的
→ 看 print 輸出和 screenshots/ 截圖，確認實際 HTML 後調整 selector
→ 不要盲目猜 selector，看截圖再改

---

## 指令 9：E2E 測試 — 任務觸發與進度條

**步驟 1 — 在 `core_engine.py` 加 MOCK_FFMPEG gate：**

在 `run_transcode_job` 方法開頭加：
```python
if os.environ.get("MOCK_FFMPEG") == "1":
    self._logger("MOCK: transcode skipped")
    return
```
在 `run_backup_job` 的 ffmpeg 呼叫前加同樣保護（只跳過 ffmpeg，仍執行複製邏輯）。

在 `tests/conftest.py` 的 `real_server` fixture 裡，subprocess.Popen 時加：
```python
env = os.environ.copy()
env["MOCK_FFMPEG"] = "1"
env["MOCK_NAS"] = "1"
```

**步驟 2 — 準備測試用小 mp4（用專案內的 ffmpeg.exe）：**
```
d:\Antigravity\OriginsunTranscode\ffmpeg.exe -f lavfi -i color=black:s=64x64:d=1 -c:v libx264 -t 1 tests/e2e/fixtures/tiny.mp4
```

**請寫 `tests/e2e/test_task_flow.py`（標記 `pytestmark = pytest.mark.e2e`），實作以下 3 個測試：**

每個測試在關鍵步驟都存截圖到 `tests/e2e/screenshots/`。

1. `test_transcode_shows_progress` — 複製 tiny.mp4 到 tmp_path/src，用 JS evaluate 注入路徑到 input，點開始，wait_for_selector 進度條（timeout=5000）
2. `test_task_done_log` — 同上流程，wait_for_function 等 terminal 出現「完成」或「done」（timeout=15000）
3. `test_conflict_dialog` — 在 src 和 dst 都放 tiny.mp4 製造衝突，觸發備份，wait_for_selector 對話框，確認有「跳過」和「覆蓋」按鈕，點「跳過」，確認對話框消失

**驗證：** `pytest tests/e2e/test_task_flow.py -v -s -m e2e`

→ 找不到 input：先看截圖，用 JS evaluate 打印所有 input 的 id/placeholder 確認 selector
→ 進度條沒出現：確認 MOCK_FFMPEG=1 有傳入服務，任務有正常完成
→ JS evaluate 設值沒反應：input 可能有事件監聽，加 `dispatchEvent(new Event('input', {bubbles:true}))`

---

## 指令 10：Smoke Test

**請寫 `tests/test_smoke.py`：**

```python
import pytest, httpx

pytestmark = pytest.mark.smoke

@pytest.fixture(scope="module")
def client(real_server):
    with httpx.Client(base_url=real_server["base_url"], timeout=10.0) as c:
        yield c

def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200, f"Health 失敗：{r.text}"

def test_version(client):
    r = client.get("/api/v1/version")
    assert r.status_code == 200
    assert "version" in r.json(), f"回傳：{r.json()}"

def test_settings(client):
    r = client.get("/api/settings/load")
    assert r.status_code == 200
    assert isinstance(r.json(), dict), f"不是 dict：{r.json()}"

def test_frontend(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "originsun" in r.text.lower(), f"開頭：{r.text[:200]}"

def test_socketio_endpoint(client):
    r = client.get("/socket.io/?EIO=4&transport=polling")
    assert r.status_code != 404, f"Socket.IO 不存在：{r.status_code}"
```

**驗證：** `pytest tests/test_smoke.py -v -s -m smoke`

→ real_server 啟動失敗：`netstat -an | findstr 18000` 確認 port 沒被佔用
→ /socket.io/ 回 404：確認 main.py 裡 socketio 掛載路徑
→ test_frontend 找不到 "originsun"：print(r.text[:500]) 看實際回傳

---

## 指令 11：新功能測試範本

**請建立 `tests/TEMPLATE.md`：**

```markdown
# 新功能測試 Checklist

每次新增功能，全部打勾才 commit。

## Step 1：純邏輯（有新 utility 函式時）
- [ ] tests/unit/test_<模組名>.py
- 不依賴 ffmpeg / NAS / 網路，執行 < 100ms
- `pytest tests/unit -v` 全過

## Step 2：API
- [ ] tests/integration/test_api_<功能>.py
- 測三個情境：正常 / 必填欄位缺少 / 路徑不存在
- `pytest tests/integration -v` 全過

## Step 3：Socket 事件（若新增 emit 事件）
- [ ] 在 test_task_queue.py 加一個測試
- 確認事件名稱和 payload 結構

## Step 4：Smoke
- [ ] `pytest -m smoke -v` 全過

## Step 5：E2E（若有新 UI）
- [ ] tests/e2e/test_<功能>.py
- 測：元素存在 / 可操作 / 結果出現，截圖存 screenshots/

---

## 常用指令

# 寫完邏輯立刻跑（最快）
pytest tests/unit -v

# 整合測試
pytest tests/integration -v

# 全部（排除 E2E）
pytest tests/unit tests/integration tests/test_smoke.py -v

# Smoke（確認服務在線）
pytest -m smoke -v

# E2E
pytest -m e2e -v

# 覆蓋率報告
pytest tests/unit tests/integration --cov=. --cov-report=html

# 單一測試（-s 看 print 輸出）
pytest tests/unit/test_taiwan_normalizer.py::test_hot_reload -v -s

---

## Debug 技巧

- `-s` 讓 print() 顯示在終端
- `--tb=long` 完整 traceback
- E2E 失敗？先看 tests/e2e/screenshots/
- patch 沒生效？先 print 確認函式有沒有被呼叫到
```

---

## 指令 12：CI 設定（GitHub Actions）

**請建立 `.github/workflows/test.yml`：**

```yaml
name: Tests

on:
  push:
    branches: [main, dev]
  pull_request:
    branches: [main, dev]

jobs:
  unit-and-integration:
    runs-on: ubuntu-latest
    env:
      MOCK_FFMPEG: "1"
      MOCK_NAS: "1"
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install deps
        run: |
          pip install pytest pytest-asyncio pytest-mock httpx pytest-cov playwright
          playwright install chromium
      - name: Unit tests
        run: pytest tests/unit -v --tb=short
      - name: Integration tests
        run: pytest tests/integration -v --tb=short

  smoke:
    runs-on: ubuntu-latest
    needs: unit-and-integration
    env:
      MOCK_FFMPEG: "1"
      MOCK_NAS: "1"
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install deps
        run: pip install pytest httpx uvicorn fastapi python-socketio
      - name: Start server
        run: |
          uvicorn main:io_app --host 0.0.0.0 --port 18000 &
          sleep 8
      - name: Smoke tests
        run: pytest -m smoke -v --tb=short

  e2e:
    runs-on: ubuntu-latest
    needs: smoke
    if: github.event_name == 'pull_request'
    env:
      MOCK_FFMPEG: "1"
      MOCK_NAS: "1"
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install deps
        run: |
          pip install pytest playwright httpx
          playwright install chromium --with-deps
      - name: Start server
        run: |
          uvicorn main:io_app --host 0.0.0.0 --port 18000 &
          sleep 8
      - name: E2E tests
        run: pytest -m e2e -v --tb=short
      - name: Upload screenshots on failure
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: e2e-screenshots
          path: tests/e2e/screenshots/
          retention-days: 7
```

**驗證 yaml 語法：**
```
python -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml')); print('yaml OK')"
```
→ 縮排錯誤：yaml 用空格，不用 tab
→ 服務沒啟動：sleep 8 不夠，改 sleep 15，或加輪詢健康檢查

---

## 指令 13：Layer 2 — 專案總覽 API 測試（佇列 + 機器狀態）

**開始前先讀這些檔案，確認實際介面：**
- `routers/api_queue.py` → GET /api/v1/queue、POST reorder、POST/DELETE urgent 的路徑與 payload
- `core/state.py` → JobState 的 priority / urgent 欄位、get_queued_jobs / reorder_jobs / set_job_urgent / unset_job_urgent
- `core/worker.py` → enqueue_job 的呼叫方式、_try_dispatch / _pick_next_job 的邏輯
- `routers/api_system.py` → GET /api/v1/health 回傳結構（cpu_percent, memory_percent, current_tasks, version）
- `config.py` → load_settings / save_settings 對 agents 的處理

**請寫 `tests/integration/test_api_queue.py`，實作以下 8 個測試：**

1. `test_queue_empty` — GET /api/v1/queue → 200，回傳空 list
2. `test_queue_returns_queued_jobs` — 用 mock_engine 送 2 個 backup job → GET /api/v1/queue → 確認回傳 2 筆，status 為 queued 或 running
3. `test_reorder_queue` — 送 3 個 job → POST /api/v1/queue/reorder { ordered_job_ids: [id3, id1, id2] } → GET queue → 確認順序 id3, id1, id2
4. `test_reorder_invalid_ids` — POST reorder 帶不存在的 job_id → 不 crash（200 或 4xx 皆可，不可 500）
5. `test_set_urgent` — 送 3 個 job → POST /api/v1/queue/{第3個id}/urgent → GET queue → 第3個排到最前 + urgent==True
6. `test_unset_urgent` — 承上 → DELETE /api/v1/queue/{id}/urgent → urgent==False，位置不變
7. `test_health_structure` — GET /api/v1/health → 確認回傳包含 status, hostname, cpu_percent, memory_percent, worker_busy, active_job_count, current_tasks, version
8. `test_agents_settings_roundtrip` — load settings → 加一個 agent {id, name, url} → save → load → 確認 agent 存在且不遺失其他設定

**驗證：** `pytest tests/integration/test_api_queue.py -v -s`

→ queue API 回 404：確認 main.py 有 include api_queue.router
→ reorder 後順序沒變：print job 的 priority 值，確認 reorder_jobs 有正確設定
→ urgent 後沒排到最前：確認 set_job_urgent 有設定 priority 為最小值 - 1
→ agents 存了但 load 後消失：確認 load_settings 有保留 agents key

---

## 指令 14：E2E 測試 — 專案總覽 Tab UI

**開始前先讀這些檔案，確認實際 DOM：**
- `frontend/tabs/projects/projects.html` → section header 文字、container id、form 結構
- `frontend/tabs/projects/projects.js` → window.projectsTab 暴露的方法、CSS class 前綴 pj-
- `frontend/tabs/projects/projects.css` → 卡片、佇列列、拖曳狀態的 class 名

**請寫 `tests/e2e/test_projects_tab.py`（標記 `pytestmark = pytest.mark.e2e`），實作以下 8 個測試：**

每個測試在關鍵步驟都存截圖到 `tests/e2e/screenshots/`。

1. `test_projects_tab_exists` — 找到「專案總覽」頁籤按鈕並點擊 → 確認 pj-container 可見
2. `test_section_headers` — 確認 4 個 section header 存在且順序正確：機器狀態 → 進行中 / 排隊中 → 排隊等待 → 歷史紀錄
3. `test_settings_panel_toggle` — 點「⚙ 系統參數」→ settings panel 顯示 → 確認有 4 個 select（backup, transcode, transcribe, concat）→ 再點一次 → 隱藏
4. `test_add_agent_form` — 點「+ 新增機器」→ 表單顯示 → 確認有名稱、IP、port 輸入框和新增/取消按鈕 → 點取消 → 表單隱藏
5. `test_add_agent_success` — 打開表單 → 填入名稱「Test-PC」和 IP「192.168.1.200」→ 點新增 → 確認 pj-machine-card 出現 → 確認卡片顯示名稱「Test-PC」
6. `test_add_two_agents` — 新增第一台「PC-X」→ 新增第二台「PC-Y」→ 確認兩張卡片都存在 → 重新整理頁面 → 切回專案總覽 → 確認兩張卡片仍存在
7. `test_remove_agent` — 新增一台 → 點卡片上的 ✕ 按鈕 → 確認 confirm dialog → 接受 → 確認卡片消失
8. `test_empty_states` — 無機器時顯示「尚未設定任何機器 + 新增第一台」→ 無活躍任務顯示「目前沒有進行中的任務」→ 無排隊顯示「目前沒有排隊中的任務」

**注意事項：**
- 所有 CSS class 使用 `pj-` 前綴（如 `.pj-machine-card`、`.pj-queue-row`）
- 機器卡片 id 格式為 `pj-agent-{id}`
- 表單 id 為 `pj-add-agent-form`
- 新增/移除 agent 後要等待 fetch 完成，建議 `page.wait_for_timeout(500)`
- 每個測試結束後清除測試 agents（透過 API 還原 settings）

**驗證：** `pytest tests/e2e/test_projects_tab.py -v -s -m e2e`

→ 找不到「專案總覽」按鈕：先 print 所有 tab 按鈕文字，確認實際文字（可能有 emoji 前綴）
→ section header 文字不匹配：用 page.evaluate 抓 .pj-section-header 的 textContent，確認實際內容
→ add agent 後卡片沒出現：確認 JS 的 addAgent() 有被正確觸發（form_input 可能需要 dispatchEvent）
→ remove agent 的 confirm 卡住：用 page.on("dialog", lambda d: d.accept())
→ test_add_two_agents 重整後消失：確認 server 已重啟並載入新版 config.py（含 agents 在 _DEFAULT_SETTINGS）

---

## 執行順序

```
指令 0  → 確認：pytest --collect-only 沒有 ImportError
指令 1  → 確認：pytest --collect-only 顯示 0 items（正常）
指令 2  → 確認：pytest tests/unit/test_taiwan_normalizer.py -v 全過
指令 3  → 確認：pytest tests/unit/test_config.py -v 全過
指令 4  → 確認：pytest tests/unit/ -v 全過
指令 5  → 確認：pytest tests/integration/test_api_system.py -v 全過
指令 6  → 確認：pytest tests/integration/test_task_queue.py -v 全過
指令 7  → 確認：pytest tests/integration/ -v 全過
指令 8  → 確認：pytest -m e2e tests/e2e/test_ui_basic.py -v -s
指令 9  → 確認：pytest -m e2e tests/e2e/ -v -s
指令 10 → 確認：pytest -m smoke -v 全過
指令 11 → 建立 TEMPLATE.md
指令 12 → 確認：python -c "import yaml; yaml.safe_load(...)" 無錯誤
指令 13 → 確認：pytest tests/integration/test_api_queue.py -v 全過
指令 14 → 確認：pytest -m e2e tests/e2e/test_projects_tab.py -v -s
指令 15 → 確認：pytest tests/integration/test_api_validate_paths.py -v 全過
指令 16 → 確認：pytest -m e2e tests/e2e/test_validate_remote_paths.py -v -s
```

---

## 指令 15：Layer 2 — 遠端路徑驗證 API 測試

> **2026-03-18 新增**：轉 Proxy / 串帶指定遠端算力主機時，提交前先驗證路徑是否可存取

**開始前先讀這些檔案，確認實際介面：**
- `core/schemas.py` → `ValidatePathsRequest` 的欄位
- `routers/api_system.py` → `POST /api/v1/validate_paths` 的回傳結構
- `frontend/js/shared/utils.js` → `validateRemotePaths()` 的簽名與回傳格式
- `frontend/tabs/transcode/transcode.js` → `submitTranscode()` 中路徑驗證的位置
- `frontend/tabs/concat/concat.js` → `submitConcat()` 中路徑驗證的位置

**請寫 `tests/integration/test_api_validate_paths.py`，實作以下 8 個測試：**

1. `test_validate_existing_path` — POST /api/v1/validate_paths { paths: ["C:\\Windows"] } → 200，drive_exists=true, path_exists=true
2. `test_validate_nonexistent_drive` — paths: ["Z:\\test"] → drive_exists=false, path_exists=false（若 Z: 碰巧存在則改用 X: 或其他不存在的磁碟機）
3. `test_validate_existing_drive_missing_path` — paths: ["C:\\NonExistentFolder_Test_12345"] → drive_exists=true, path_exists=false
4. `test_validate_multiple_mixed` — 一次送 4 個路徑（2 存在 + 1 磁碟機不存在 + 1 路徑不存在）→ 確認每個結果正確
5. `test_validate_empty_paths` — paths: [] → 200，results 為空 dict
6. `test_validate_unc_path` — paths: ["\\\\192.168.1.132\\Container"] → drive 為空字串，drive_exists=true（因為空 drive 預設 true），path_exists 依實際環境
7. `test_validate_real_sources` — 使用實測參數的 CARD_A 和 CARD_B 路徑 → 確認兩者 drive_exists=true，path_exists=true
8. `test_validate_real_dest` — 使用測試目的地 `D:\Antigravity\OriginsunTranscode\test_zone` → 若不存在先建立 → 驗證 → 測試後清理

**實測參數（與 🔬 區一致）：**
```python
CARD_A = r"U:\20260205_寬微廣告_影片中文化\09_Export\01_Check"
CARD_B = r"R:\ProjectYaun\20260304_幾莫_鴻海科技獎短影音\09_Export\01_Check"
DEST   = r"D:\Antigravity\OriginsunTranscode\test_zone"
```

**驗證：** `pytest tests/integration/test_api_validate_paths.py -v -s`

→ Z: 碰巧存在：改用 `chr(d)` 迴圈找一個不存在的磁碟機字母
→ UNC path 結果非預期：print 回傳確認 `os.path.splitdrive` 對 UNC 的處理方式，調整 assertion
→ CARD_A/CARD_B 路徑不存在（不同機器）：加 `pytest.mark.skipif` 條件跳過

---

## 指令 16：前端整合 — validateRemotePaths 瀏覽器測試

> **開始前先讀 `frontend/js/shared/utils.js` 中的 `validateRemotePaths()` 函式**

**請寫 `tests/e2e/test_validate_remote_paths.py`（標記 `pytestmark = pytest.mark.e2e`），實作以下 4 個測試：**

每個測試透過 `page.evaluate()` 呼叫 `window.validateRemotePaths()`。

1. `test_fe_validate_nonexistent_drive` — `validateRemotePaths('localhost:18000', ['Z:\\test'])` → ok=false, errors 包含「磁碟機」
2. `test_fe_validate_missing_path` — `validateRemotePaths('localhost:18000', ['C:\\NonExistent_Test_999'])` → ok=false, errors 包含「路徑不存在」
3. `test_fe_validate_existing_paths` — `validateRemotePaths('localhost:18000', ['C:\\Windows', 'C:\\Users'])` → ok=true
4. `test_fe_validate_unreachable_host` — `validateRemotePaths('192.168.99.99:8000', ['C:\\test'])` → 應拋出錯誤（fetch 失敗），用 try-catch 包裝確認 error 被正確處理

**驗證：** `pytest tests/e2e/test_validate_remote_paths.py -v -s -m e2e`

→ window.validateRemotePaths 未定義：確認 utils.js 底部有 `window.validateRemotePaths = validateRemotePaths`
→ evaluate 超時：async 函式需用 `page.evaluate("(async () => { ... })()")` 格式
→ unreachable host 測試超時：加 AbortController + setTimeout(3000) 控制 fetch 超時

---

## 指令 17：清理測試環境

**測試完成確認無誤後，執行以下指令清理 `test_zone`：**

```powershell
Remove-Item -Path "d:\Antigravity\OriginsunTranscode\test_zone\*" -Recurse -Force
```
