# website/src/types/

TypeScript interface 集中目錄。Schema-first 設計 — 資料結構是 single source of truth。

| 檔案 | 內容 |
|---|---|
| `project.ts` | `IPublicProject` / `IPublicProjectDetail`（對應後端 Pydantic） |
| `category.ts` | `ICategory`（zh/en、slug、count） |
| `api.ts` | API Response wrapper（`IAPIResponse<T>` 等泛型） |
| `settings.ts` | `IWebsiteMeta`（site title、OG image 等）|
| `notion.ts` | Notion block 型別（撈部落格用） |

**命名慣例**：interface 以 `I` 開頭、type alias 以 `T` 開頭。

**對應後端**：每個 TS interface 都有一份對應的 Pydantic schema 在 `core/schemas/website.py`。兩邊結構盡量 1:1 對齊。
