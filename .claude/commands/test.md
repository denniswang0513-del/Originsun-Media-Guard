UI / functional regression test runner for Originsun Media Guard Pro.

## 範圍

### ✅ /test 涵蓋

- **Phase 2 純函式 smoke**：core/website helpers（normalizeCredits 對偶 / append_old_slug_if_changed / _credits_summary / _coerce_testimonial_date）
- **Phase 3 HTTP**：master backend endpoints（CRM staff CRUD / showcase-edit token / works admin / redirects merge）
- **Phase 4 Playwright UI**：CRM 人力資源 Tab、showcase-edit autocomplete、CRM 主 SPA 流程

### ❌ 明確不測（人工驗）

| 範圍 | 為什麼 | 何時驗 |
|------|--------|------|
| NAS website-api admin endpoints | 需要 NAS Docker container | publish 後 staging 手測 |
| 對外 Astro `/works/[slug]` | Astro build 期 fetch NAS API | publish 後 PageSpeed 跑 |
| 軟 301 / 硬 301 e2e | 需要 nginx + dist/ | publish 後 curl 對外網域 |

---

## Args

- `--scope=<all|smoke|http|ui>` (default: `all`) — 跑 phase 子集
- `--flow=<crm-staff|showcase-edit|works-admin>` — 限定單一 UI flow
- `--keep-data` — 不清測試資料（debug 用）
- `--no-install` — 跳過 playwright chromium 安裝

---

## Workflow

### Step 1：環境探勘（總是先跑）

並行執行：

1. `[ -f .venv/Scripts/python.exe ] && echo OK_VENV || echo MISSING_VENV` — 沒 .venv 直接 fail
2. `git status -s` — 檢查工作目錄
3. `cat version.json` — 拿目前版本（報告用）

### Step 2：smoke phase（`--scope=smoke` 或 `all`）

跑 `pytest tests/test_simplify_helpers.py -v`：

- `append_old_slug_if_changed` 4 cases（基本 / no-op / numeric skip / 自循環）
- `_credits_summary` 5 cases（空 / single / multi / 超長截斷 / 壞 format）
- `_coerce_testimonial_date` 4 cases（純函式 / 不污染 input）
- `list_redirects` mock session 1 case

報告：通過 N / 失敗 M。失敗就停這個 phase，不跑後續。

### Step 3：HTTP phase（`--scope=http` 或 `all`）

跑 `pytest tests/test_test_skill_http.py -v`（沿用 `tests/conftest.py:real_server` fixture）：

setUp：fixture seed 一個 `__test_<rand>` prefix 的 staff + work + showcase + token

#### CRM staff CRUD
- `GET /api/v1/crm/staff` — 200 + items
- `POST /api/v1/crm/staff` — 建立 + 回 staff dict
- `PUT /api/v1/crm/staff/{id}` — 更新
- `GET /api/v1/crm/staff/{id}/projects` — 確認回傳含 `projects[]` 跟 `credit_only_projects[]`（新形狀）

#### showcase-edit token
- `GET /api/v1/crm/public/showcase-edit/{token}` — 200 + 含 `credit_roles_available[]` + `credit_templates_available[]`
- `GET /api/v1/crm/public/showcase-edit/{token}/staff_search?q=__test` — 200 + items 含 fixture staff
- `POST /api/v1/crm/public/showcase-edit/{token}/staff_quick_add` body `{name, role}` — 201 + 回 staff_id + `resume_visible=False`
- `PUT /api/v1/crm/public/showcase-edit/{token}` body `{credits: [block]}` — 確認 credits 進 DB 為新 block 格式

#### redirects
- `GET /api/v1/crm/public/showcase-edit/{token}` 改 slug → 確認 `public_old_slugs` append
- `GET /api/website/redirects` — items 含 posts + works merge（master 端的 posts 為空也 OK）

tearDown：刪所有 `__test_` prefix 的 staff/work/showcase/token

### Step 4：UI phase（`--scope=ui` 或 `all`）

#### Step 4a：Playwright 安裝（除非 `--no-install`）
```bash
.venv/Scripts/python.exe -m playwright install chromium
```

#### Step 4b：跑 4 個 flow

`pytest tests/test_test_skill_ui.py -v --headed=false`（沿用 `real_server`）：

##### Flow `crm-staff`：人力資源 Tab 專案紀錄擴充
1. goto `/` 等 `domcontentloaded` + 3 秒
2. 切到「CRM」
3. 切到「人力資源」
4. 點 fixture staff 的 row
5. 切 detail 「📺 專案紀錄」tab
6. assert：
   - 看到 `.crm-stat-chips` 跟 `.crm-stat-chip` × ≥2
   - 若 fixture staff 沒派工 → 看到「外部演員」chip
   - 若 fixture 是 quick_add（created_via='showcase_edit' + phone+email 空）→ 「人員資訊」tab 看到 `.crm-info-box` 提示

##### Flow `showcase-edit`：autocomplete combobox
1. fixture mint token from project
2. goto `/showcase-edit.html?token=<token>`
3. 等 page render
4. 「演職員表」block 假設沒有 → 從 `+ 新增職位` 下拉選一個 role 加 block（fixture 確保 credit_roles_available 有東西，沒就 seed）
5. 在 entry name input click → assert dropdown 浮出
6. 打字 `fill('aaa')` → assert dropdown 重新搜尋（debounce 200ms 內）
7. 點 dropdown 中 fixture staff → assert input 填入 staff name + 綠點出現
8. 點「+ 建立新人員 X」→ assert quick_add modal 開
9. fill name + 點建立 → assert modal 關閉 + entry 自動掛 staff_id

##### Flow `works-admin`：作品集管理 Tab（404 expected）
1. goto `/` → 切「網站管理 → 作品集管理」
2. assert 看到「無法載入：404」+「請在 master 跑 /publish」hint
3. 這是預期行為（NAS only），記錄 skipped

##### 截圖 / 影片
- 失敗時 `page.screenshot(path='tests/reports/<flow>-<test>.png', full_page=True)`
- 失敗時保留 video（reports/videos/）
- 成功時不保留（節省 disk）

### Step 5：產出報告

寫 `tests/reports/test-<YYYYMMDD-HHMMSS>.md`：

```markdown
# /test 報告 — <timestamp>
版本：<version.json>
Scope：<args.scope>

## Summary
- ✅ 通過：N
- ❌ 失敗：M
- ⏭ 跳過：K（NAS-only / Playwright skip）
- ⏱ 總時長：X 秒

## Phase 結果

### Phase 2 smoke
- ✅ `tests/test_simplify_helpers.py::test_append_old_slug_*` (4 cases)
- ❌ `test_credits_summary_truncate` — expected '主演 X · ...'，actual '...'
  - file: tests/test_simplify_helpers.py:42

### Phase 3 HTTP
- ✅ test_staff_crud (4 cases)
- ⏭ test_redirects_merge_works （fixture work 沒 old_slugs，skip）

### Phase 4 UI
- ❌ test_showcase_edit_autocomplete
  - 截圖：tests/reports/screenshots/showcase-edit-autocomplete.png
  - console errors: ["Uncaught ReferenceError: ..."]
  - network: 404 on `/api/v1/crm/public/showcase-edit/foo/staff_search`

## 建議下一步

- 失敗的測試：跑 /bugfix
- 跳過的 NAS-only：跑 /publish 後在 staging 手測
```

報告路徑印給 user。

---

## Fixture 規則（cleanup 不可少）

- 所有測試資料 `name` 開頭加 `__test_` prefix
- 每個 phase 開頭跑 `cleanup_test_data()` — 把上次殘留洗掉
- `--keep-data` flag 跳過 cleanup（debug 用）

---

## 失敗時的判斷邏輯

- Phase 2 失敗 → 後端 helper 邏輯壞掉，**停測** 不跑 Phase 3/4
- Phase 3 失敗 + 4xx/5xx → 可能 schema 改動沒同步、payload 格式不對
- Phase 4 失敗 + console error → 多半是前端邏輯（檢查 console 訊息）
- Phase 4 失敗 + network 4xx → 多半是 backend endpoint 沒部署（NAS-only）

---

## 注意事項

- **不要關掉 master server 中途**：`real_server` fixture session-scoped，所有測試共用
- **不要動 prod 8000 port**：`real_server` 用 18000
- **不要刪除非 `__test_` 開頭資料**：cleanup_test_data 嚴格 prefix 比對
- **失敗時不要自動修補**：報告完後讓 user 決定（失敗有 SEO / data integrity 風險）

---

## 完成後簡短回報

1. 各 phase 通過 / 失敗 / 跳過 count
2. 報告檔案路徑
3. 主要失敗點摘要 + 建議下一步
