# core/schemas/

Phase M 官網模組 Pydantic 請求/回應模型，依模組拆檔（AI 友善）。

**為什麼拆檔**：既有 `core/schemas.py` 已經包含所有模組的 schema，繼續加會失控。Phase M 從此目錄開始拆。

**AI 友善規則**：每檔 ≤ 400 行、按「領域」分檔。

| 檔案 | 內容 |
|---|---|
| `__init__.py` | 集中 re-export 所有 website schema |
| `website.py` | 官網所有 schema（Public + Admin + Contact + Settings + Category + Service） |

**命名慣例**：
- Request：`XxxCreate` / `XxxUpdate`
- Response：`XxxResponse` / `XxxPublicResponse`（對外）/ `XxxAdminResponse`（後台）
- TypeScript interface 則在 `website/src/types/*.ts`
