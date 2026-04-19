# website/src/lib/

業務邏輯與 API client。每檔 ≤ 200 行、依領域分檔。

| 檔案 | 職責 |
|---|---|
| `crm-client.ts` | 封裝 fetch 到 NAS website-api（works、featured、categories、services、team、meta） |
| `notion-client.ts` | Notion API 封裝（build 時撈文章、渲染 Notion block） |
| `youtube.ts` | YouTube ID 驗證、縮圖 URL 生成、嵌入 URL 生成 |
| `i18n.ts` | 繁中/英文切換、翻譯字典 |
| `seo.ts` | Schema.org 結構化資料生成（VideoObject、Organization） |

**規則**：所有函式完整 TS type hints。資料結構 interface 定義放 `../types/`。

**呼叫方向**：只能被 `pages/` 或 `components/` 呼叫。`lib/` 之間可以互相 import。
