"""
api_timesheets.py — 工作追蹤 API（docs/WORK_TRACKING_UI_PLAN.md；前身 N2 階段 0 的工時檢核）

資料進來的路：主控端定時拉整本 Sheet（services/timesheet_puller，公開連結 xlsx export；設定在
/pull）或 Apps Script 推 /ingest（帶 X-Timesheet-Token）→ 都走 services.timesheet_ingest 去重寫入
timesheets → 專案對映走 core.hr_logic.resolve_project（對映表／去客戶前綴，撞案不猜）。
員工自己填走 /mine*（services.timesheet_self，own-scope）。

看的路：/board（每日看板）、/project／/compare（專案檔案、類似專案並排）、/person（人員檔案）、
/summary（burn）、/dashboard、/export.csv；規則全在 core.hr_logic（純函式），這裡只做 I/O。
預算只從 PUT /budgets（Sheet「專案狀態」一次性）與 PUT /project_budget 進。

token：settings.json `timesheet.ingest_token`（首次取用自動生成）。
"""

# 🔴 2026-09-12 起這支只是薄殼：實作在 routers/timesheets/（_shared ＋ sync／mine／ledger／projects／reports／mapping／summary），
#    同 routers/api_crm.py 的做法。URL 全部不變（同一顆 router）。掃原始碼的測試用 _srcscan.timesheets_src()。
#    下面 import 各段是為了把端點掛到 router 上（import 有副作用），不要「順手清掉沒用到的 import」。
from routers.timesheets._shared import (router, SUMMARY_PUBLIC_KEYS, UNMATCHED_PUBLIC_KEYS, _redact_summary,  # noqa: F401
                                        _require_mine_admin, _day_or_422, _has_ts_module, _ts_or_bound, _mine_ident,
                                        _get_or_create_ingest_token, _CANDS_CACHE, _MAX_ROWS_PER_CALL)
from routers.timesheets import sync, mine, ledger, projects, reports, mapping, summary  # noqa: F401,E402
# 端點函式照舊從這個名字拿得到（`from routers import api_timesheets as m; m.burn_summary(...)` 那種直接呼叫的測試）。
# 🔴 要 monkeypatch 端點內部用的 db_factory_or_503／check_admin_or_module，得 patch **子模組**（routers.timesheets.ledger…），
#    patch 這個薄殼的屬性不會影響子模組的全域名字。
from routers.timesheets.sync import *        # noqa: F401,F403,E402
from routers.timesheets.mine import *        # noqa: F401,F403,E402
from routers.timesheets.ledger import *      # noqa: F401,F403,E402
from routers.timesheets.projects import *    # noqa: F401,F403,E402
from routers.timesheets.reports import *     # noqa: F401,F403,E402
from routers.timesheets.mapping import *     # noqa: F401,F403,E402
from routers.timesheets.summary import *     # noqa: F401,F403,E402
