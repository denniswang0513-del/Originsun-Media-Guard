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

```bash
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
```

---

## Debug 技巧

- `-s` 讓 print() 顯示在終端
- `--tb=long` 完整 traceback
- E2E 失敗？先看 tests/e2e/screenshots/
- patch 沒生效？先 print 確認函式有沒有被呼叫到
