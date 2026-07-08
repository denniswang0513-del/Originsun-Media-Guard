# 商業功能線規劃（N-biz）— 六項直接碰錢的功能（2026-07-08）

> 定位：Work OS Phase N 的商業支線。共同特徵：**全部長在既有基礎設施上**
> （proxy 管線 / NAS / token 分享頁 / 帳務資料 / Whisper 逐字稿 / AI runner 骨架 /
> notifier），不開新戰線。與 N 主線的依賴各自標注；B2 硬依賴 N2 工時。
> 實作慣例照舊：CRM checklist、RBAC 3 處、canary、/simplify。

---

## B1. 看片審批客戶門戶（Client Review Portal）🥇

**商業理由**：修改往返從「LINE 來回猜」變成「時間軸精準標記」→ 同樣人力接更多案。
Frame.io 以此功能為核心賣到 $1.275B；我們的底層（proxy、NAS、web、token 頁）全現成。
附帶財源：**素材保管年費**（影像公司少有的 recurring revenue）。

### 資料模型
- `portal_review_links`：id, project_id(FK), version_label（初剪/一修/定剪…）,
  video_url（proxy 檔，NAS serve）, token（沿用 showcase-edit token 模式）,
  status（待審/修改中/已核准）, expires_at, created_at
- `portal_comments`：review_link_id, timecode_sec, body, author_name（客戶端免登入、
  留名即可）, resolved(bool), created_at
- `portal_deliverables`：project_id, label, file_url, size, download_count —— 交付下載區
- （二階）`portal_retainers`：project_id, plan（保管方案）, fee, renew_at —— 保管年費

### UI 與流程
1. 專案詳情「🎬 送審」→ 選 proxy 檔 + 版本名 → 產 token 連結傳給客戶
2. 客戶頁（手機優先，/review.html 沿用 /expense.html RWD 模式）：
   播放器 + 點時間軸留言 + 一鍵「核准此版」
3. 內部視圖：意見列表（可勾 resolved）、版本歷史、核准狀態回寫專案 phase（N1 掛鉤）
4. 核准 → notifier 通知 PM；全部 resolved → 提示可出下一版
5. 二階：交付下載區 + 下載紀錄；三階：保管年費方案 + 到期提醒

### 依賴與位置
N-now 收尾後最優先。播放器用原生 video 標籤即可起步（proxy 是 H.264）。
RBAC 模組 `portal`；客戶端走 token 不走登入。

---

## B2. 估價複盤迴圈（Quote vs Actual）🔒 依賴 N2

**商業理由**：估價準確度是製作公司生死線。把 N2 工時資料變成錢的關鍵一步。

### 設計
- 結案時自動產「複盤卡」：報價項目（quotation_items）vs 實際
  （timesheets 工時成本 + crm_project_expenses + 外包請款），偏差 % 標色
- `quote_review_snapshots`：project_id, quoted_total, actual_total, breakdown(JSON),
  deviation_pct, note —— 快照留檔，趨勢可查
- 報價編輯時的「歷史提示」：同 ptype（提案庫的類型欄位）歷史平均偏差
  →「同類案歷史實際成本 +N%」inline 提示
- 儀表板卡：估價偏差趨勢 by 類型/季度

### 依賴與位置
硬依賴 N2 工時（成本才完整）；排 N3 之後。純計算進 `core/crm_logic.py` + 單元測試。

---

## B3. 現金流預測 + 付款節點提醒 🥈（小工程、高價值、可先做）

**商業理由**：帳期產業的命。影像業倒帳不多、**忘記請款是常態**。

### 設計
- `payment_milestones`：project_id, label（訂金/期中/尾款…）, amount 或 pct,
  trigger_phase（綁 N1 phase，N1 未上前先手動日期）, due_date, status（未到/待請/已請/已收）,
  invoice_id(FK 可連發票)
- 報價轉專案時自動帶節點模板（30/40/30 等，模板可設定）
- **90 天現金流視圖**：應收節點 + 應付帳款 + 固定成本（新設定鍵 monthly_fixed_costs）
  疊成週粒度圖表，紅色 = 低水位警示
- 提醒：phase 達到 trigger_phase 或 due_date 到 → notifier 推「該請款了」
  （告警體系現成）；逾期未收款升級提醒

### 依賴與位置
獨立可先做；N1 上線後 trigger_phase 自動化才完整。資料 90% 已在帳務模組。

---

## B4. 器材管理（成本真相的最後一塊）

**商業理由**：沒有器材折舊，毛利永遠虛高；稼動率數字讓「買 vs 租」有據可依。

### 資料模型
- `equipment`：id, name, category（機身/鏡頭/燈光/收音/週邊）, serial, purchase_date,
  purchase_cost, depreciation_months（直線攤提）, status（在庫/出勤/維修/除役）,
  note, cover_url
- `equipment_checkouts`：equipment_id, project_id, person(crm_staff), out_at, due_at,
  returned_at, condition_note —— 領用/歸還
- `equipment_maintenance`：equipment_id, date, cost, note —— 保養履歷

### UI 與流程
- 前期製作群組 tab「🎥 器材庫」：卡片牆 + 狀態篩選 + 領用/歸還快速操作
  （手機掃描/點選歸還，沿用 RWD 頁模式）
- 專案連動：專案詳情列本案領用器材；**折舊攤提**（使用天數 × 日折舊）計入專案成本
  → 毛利計算補上這塊
- 稼動率報表：每件器材年度使用天數/率，低稼動高價品標出（該租不該買）
- 逾期未還提醒走 notifier

### 依賴與位置
獨立。RBAC 模組 `preprod_equipment`。

---

## B5. 內部素材庫（Footage Library — 你有別人沒有的底牌）

**商業理由**：重用一段素材 = 省一天拍攝；長線可長出 stock footage 授權副業。
Whisper 逐字稿 + metadata + 縮圖條**已經在產**，只差可檢索介面。

### 設計
- `footage_index`：id, project_id, file_path（NAS）, duration, resolution, fps,
  shot_date, transcript(TEXT，來自既有 SRT/TXT), tags(JSON), thumb_strip_url, created_at
- 建索引管線：掃描既有報表/轉檔產物回填 + 備份/轉檔完成 hook 增量寫入
  （素材進線自動化的一部分，與 STRATEGY 自動化梯隊 #2 同一條）
- 搜尋：Postgres `pg_trgm` 起步（中文可用、零新依賴），查 transcript + tags + 專案名；
  之後需要再升級斷詞
- UI：後期製作群組 tab「🎞️ 素材庫」：關鍵字搜尋 → 命中片段列表（縮圖條 + 逐字稿
  命中上下文 + 時間點）→ 一鍵開資料夾 / 複製路徑
- 二階：AI tag（claude 看縮圖條補場景標籤）；三階：對外 stock 授權（遠期，先不做）

### 依賴與位置
獨立；與「素材進線自動化」（備份→轉檔→逐字稿）合流實作最省。RBAC `footage`。

---

## B6. 客戶關懷 runner + 年度成效報告（第五個 AI runner）

**商業理由**：最便宜的案源是舊客戶。年度成效報告本身就是續約觸發器；
同一套基礎設施可包裝成「幫客戶代營運」的**新服務線**（賣服務，不賣軟體）。

### 設計
- runner 骨架照抄（`_runner_util` + kill switch + dev enabled=false + 連續失敗告警）
- 觸發規則表 `care_rules`：結案後 N 月回訪提醒、客戶年度回顧、長期未互動警示
  （clients.updated_at + 專案歷史即時算）
- 產出走「建議佇列」模式（同社群工作台）：runner 產「關懷任務卡 + AI 草擬訊息」
  → 業務審核 → 寄出（SMTP 現成）或轉公布欄待辦
- **年度成效報告**：每年 12 月 runner 汇总每客戶當年專案 + 作品連結 + （二階）
  YouTube/FB 公開觀看數 → claude 產報告草稿 → 審核後寄送
- 三階（商業）：把社群 runner + 成效報告包裝成客戶代營運方案（定價/SOP 是 owner 作業）

### 依賴與位置
獨立；建議排在社群階段二穩定運行之後（同一套模式的第五次複製，成本最低）。

---

## 建議順序與依賴總表

| 段 | 內容 | 依賴 | 粗估 | 何時做 |
|----|------|------|------|--------|
| B3 | 現金流預測 + 付款節點 | 無（N1 後更完整） | 1-2 session | **可立即插隊** |
| B1 | 看片審批門戶（核心審批流） | 無 | 2-3 session | N-now 收尾後最優先 |
| B1-2 | 交付下載區 + 保管年費 | B1 | 1 session | B1 穩定後 |
| B4 | 器材管理 | 無 | 1-2 session | 穿插 |
| B5 | 內部素材庫 | 與素材進線自動化合流 | 2 session | 穿插 |
| B6 | 客戶關懷 runner | 社群階段二穩定後 | 1-2 session | Q4 |
| B2 | 估價複盤迴圈 | **N2 工時** | 1 session | N3 之後 |

**與主線的關係**：B 線全部是支線，穿插在 N 階段空檔做；唯 B2 鎖在 N2 後。
優先建議 B3 → B1（一個顧經營安全、一個顧對外體驗與新財源）。
