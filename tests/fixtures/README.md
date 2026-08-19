# 測試用 fixture

## einvoice_*.pdf — 電子發票證明聯版面

給 `tests/unit/test_invoice_number_detect.py` 驗「PDF → 發票號碼」整條鏈用
（pypdf 抽字 → CJK 正規化 → 標籤正則）。

**為什麼是 committed fixture 而不是測試時現產**：Windows 的 SelectorEventLoop
（repo 為 asyncpg 穩定性刻意設的，見 `core/loopsetup.py`）不支援 subprocess，
而 playwright 的 sync API 內部要 spawn —— 那幾條測試單獨跑會過、跟著整套跑就炸。

要重新產生（版面有變、或要補新案例）：

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page()
    pg.set_content("<html><meta charset='utf-8'><body style=\"font-family:'MingLiU',serif\">"
                   "<div><span style='letter-spacing:.3em'>發票號碼</span>： DQ45891570</div>"
                   "</body></html>")
    pg.pdf(path="tests/fixtures/einvoice_labelled.pdf", format="A4")
    b.close()
```

| 檔案 | 內容 | 期望 |
|---|---|---|
| `einvoice_labelled.pdf` | 完整證明聯版面，有「發票號碼：」標籤 | `DQ45891570` |
| `einvoice_no_label_single.pdf` | 無標籤，全文只有一組合法號碼 | `DQ45891570` |
| `einvoice_no_label_multi.pdf` | 無標籤，兩組號碼 | `""`（放棄，讓人自己填） |
