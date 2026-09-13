# -*- coding: utf-8 -*-
"""開機時跑的一次性遷移／migration／種子 —— 從 main._on_startup 純搬過來（2026-09-13 /health）。

每一段就是原本 `_on_startup` 裡以 `# ── ... ──` 分隔的一段，**逐字不改**（含各自的
try/except、`if state.db_online` 守衛、print 字樣）。順序由 `run_post_db` 決定，
跟原本在 `_on_startup` 裡由上而下一樣；`tests/unit/test_startup_migrations_order.py` 釘著。

SQL 正本仍在 `db/migrations.py`（只有資料）；這裡是「什麼時候、用什麼順序套」。
要加一段：寫一支 `_mNN_*`、掛進 `_POST_DB` 清單尾端、順序測試會跟著要你改。
"""
import asyncio

import core.state as state  # type: ignore
from db import migrations as _MIG  # 純資料，agent 也能 import

try:
    from db.session import get_session_factory
except ImportError:  # 機隊 agent 沒裝 sqlalchemy；下面每段都在 state.db_online 之後才用到它
    get_session_factory = None  # type: ignore


async def run_pre_db() -> None:
    """init_db() 之前要跑的：settings.json 一次性修補（engine 讀 get_database_url → settings.json）。"""
    # ── 一次性遷移（2026-07）：把外洩的舊 DB 密碼 originsun2026 移出 settings.json ──
    # 機隊 8 台無法直接觸及（無 SSH / 各自 jwt_secret / master 無推設定機制），這段在
    # 每台 OTA 重啟時自癒：偵測到 settings.json 仍是舊密碼 → 改成 config 新預設。
    # 必須在 init_db() 之前（engine 讀 get_database_url() → settings.json）。
    # guarded（只在含舊密碼時動）+ idempotent + 例外不擋啟動。
    try:
        from config import load_settings, save_settings, _DEFAULT_SETTINGS
        # 只換「密碼」子字串，保留主機/埠/庫名 —— dev 的 mediaguard_dev 不能被改成
        # 預設的 mediaguard（否則 dev 連到生產庫，破壞 §2.1 dev/prod 隔離）。
        _new_pw = _DEFAULT_SETTINGS["database_url"].split("://originsun:", 1)[-1].split("@", 1)[0]
        _cur_url = load_settings().get("database_url", "")
        if "originsun2026" in _cur_url and _new_pw and _new_pw != "originsun2026":
            save_settings({"database_url": _cur_url.replace("originsun2026", _new_pw)})
            print("[migrate] settings.json 的外洩 DB 密碼已輪替為新值（保留庫名）")
    except Exception as _e_mig:
        print(f"[migrate] DB 密碼輪替略過: {_e_mig}")


async def _m01_google_oauth_columns() -> None:
    """DB Migration: Google OAuth columns"""
    # ── DB Migration: Google OAuth columns ──
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from sqlalchemy import text
                async with factory() as session:
                    for col, coltype in [
                        ("google_id", "VARCHAR(255)"),
                        ("email", "VARCHAR(255)"),
                        ("avatar_url", "VARCHAR(512)"),
                        ("modules", "JSONB"),          # RBAC v2: 權限直接綁帳號
                        ("access_level", "INTEGER"),   # RBAC v2: 3=管理員, 1=一般
                        ("staff_id", "VARCHAR(32)"),   # N0: 帳號 ↔ crm_staff（個人工作台）
                    ]:
                        try:
                            await session.execute(text(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {coltype}"))
                            await session.commit()
                        except Exception:
                            await session.rollback()
                    # Google-only users have no password — allow NULL
                    try:
                        await session.execute(text("ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL"))
                        await session.commit()
                    except Exception:
                        await session.rollback()
                    # ── RBAC v2 一次性回填：把角色權限複製到 per-user 欄位 ──
                    # idempotent — 只填還是 NULL 的 row，跑多次無害。確保上線後
                    # 沒有人掉權限；之後角色層即可淘汰（Phase 2）。
                    import json as _json_rbac
                    from core.auth import ALL_MODULES as _all_mods
                    _all_mods_sql = _json_rbac.dumps(list(_all_mods))
                    for _bf_sql in _MIG.rbac_v2_backfill(_all_mods_sql):
                        try:
                            await session.execute(text(_bf_sql))
                            await session.commit()
                        except Exception:
                            await session.rollback()
        except Exception:
            pass


async def _m02_me_zone_split_backfill() -> None:
    """員工工作台「今天與這週」拆鑰匙的一次性回填（2026-09-08；規則正本 core.auth.ME_ZONE1_*）"""
    # ── 員工工作台「今天與這週」拆鑰匙的一次性回填（2026-09-08；規則正本 core.auth.ME_ZONE1_*）──
    # 拆之前任一把工作鑰匙就整區出現；拆了之後沒補的人一上線整區消失。只跑一次：settings.json 旗標，
    # 之後 owner 在權限管理收掉的鑰匙不會被補回來。走 _get_all_users／_persist_user（DB＋users.json 雙寫），
    # 不下 raw SQL —— users.json 是登入的退路，只改 DB 會讓兩邊漂開。
    try:
        from config import load_settings as _ls_bf, save_settings as _ss_bf
        _st_bf = _ls_bf()
        if not (_st_bf.get("rbac") or {}).get("me_zone_split_backfilled"):
            from core.auth import ME_ZONE1_BACKFILL_FROM as _bf_from, ME_ZONE1_KEYS as _bf_keys
            from routers.api_auth import _get_all_users as _bf_users, _persist_user as _bf_persist
            _n_bf = 0
            for _u in await _bf_users():
                _mods = list(_u.get("modules") or [])
                if int(_u.get("access_level") or 0) >= 3 or not any(k in _mods for k in _bf_from):
                    continue
                _missing = [k for k in _bf_keys if k not in _mods]
                if not _missing:
                    continue
                _u["modules"] = _mods + _missing
                await _bf_persist(_u)
                _n_bf += 1
            _st_bf.setdefault("rbac", {})["me_zone_split_backfilled"] = True
            _ss_bf(_st_bf)
            print(f"[migrate] 今天與這週拆鑰匙：補發 {_n_bf} 個帳號（只跑這一次）")
    except Exception as _e_bf:
        print(f"[migrate] 今天與這週拆鑰匙回填略過: {_e_bf}")


async def _m03_me_today_zone_backfill() -> None:
    """第二次（同日晚）：「今天與這週」總開關＋「我的一週」拆出來（旗標 rbac.me_today_zone_backfilled）"""
    # ── 第二次（同日晚）：「今天與這週」總開關＋「我的一週」拆出來（旗標 rbac.me_today_zone_backfilled）──
    # 有任一把子鑰匙的人補總開關；有「今天的專案紀錄」的人補「我的一週」（拆之前是同一把）。同上只跑一次。
    try:
        from config import load_settings as _ls_bf2, save_settings as _ss_bf2
        _st_bf2 = _ls_bf2()
        if not (_st_bf2.get("rbac") or {}).get("me_today_zone_backfilled"):
            from core.auth import ME_ZONE_MASTER as _bf2_master, ME_ZONE_MASTER_BACKFILL_FROM as _bf2_from
            from routers.api_auth import _get_all_users as _bf2_users, _persist_user as _bf2_persist
            _n_bf2 = 0
            for _u in await _bf2_users():
                _mods = list(_u.get("modules") or [])
                if int(_u.get("access_level") or 0) >= 3 or not any(k in _mods for k in _bf2_from):
                    continue
                _add = [k for k in ([_bf2_master] + (["me_week_plan"] if "me_worklog" in _mods else [])) if k not in _mods]
                if not _add:
                    continue
                _u["modules"] = _mods + _add
                await _bf2_persist(_u)
                _n_bf2 += 1
            _st_bf2.setdefault("rbac", {})["me_today_zone_backfilled"] = True
            _ss_bf2(_st_bf2)
            print(f"[migrate] 今天與這週總開關：補發 {_n_bf2} 個帳號（只跑這一次）")
    except Exception as _e_bf2:
        print(f"[migrate] 今天與這週總開關回填略過: {_e_bf2}")
    # 階段 4 收鑰匙（捆）不需要回填：讀帳號的咽喉 _enrich_user 與存帳號兩處都 expand_modules，存的形狀無關緊要（收尾 review 拿掉了一段永遠 0 筆的迴圈）


async def _m04_bulletin_columns_and_seed() -> None:
    """公布欄欄位 migration（新欄位 create_all 不補到既有表）+ 種子"""
    # ── 公布欄欄位 migration（新欄位 create_all 不補到既有表）+ 種子 ──
    if state.db_online:
        try:
            from db.session import get_session_factory
            _f_bl = get_session_factory()
            if _f_bl:
                from sqlalchemy import text as _t_bl
                async with _f_bl() as _s_bl:
                    for _c, _ct in [("assignee", "VARCHAR(16) DEFAULT 'me'"),
                                    ("conversation", "JSONB"), ("activity", "TEXT"),
                                    ("assignee_username", "VARCHAR(64)")]:  # N0: 指派到個人
                        try:
                            await _s_bl.execute(_t_bl(
                                f"ALTER TABLE bulletin_items ADD COLUMN IF NOT EXISTS {_c} {_ct}"))
                            await _s_bl.commit()
                        except Exception:
                            await _s_bl.rollback()
                from routers.api_bulletin import seed_if_empty as _seed_bulletin
                async with _f_bl() as _s_bl2:
                    await _seed_bulletin(_s_bl2)
        except Exception:
            pass


async def _m05_api_keys_table() -> None:
    """DB Migration: api_keys table"""
    # ── DB Migration: api_keys table ──
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory2 = get_session_factory()
            if factory2:
                from sqlalchemy import text as _text
                async with factory2() as session:
                    await session.execute(_text("""
                        CREATE TABLE IF NOT EXISTS api_keys (
                            id SERIAL PRIMARY KEY,
                            key_hash VARCHAR(64) NOT NULL UNIQUE,
                            key_prefix VARCHAR(12) NOT NULL,
                            name VARCHAR(64) NOT NULL,
                            username VARCHAR(64) NOT NULL,
                            created_at TIMESTAMPTZ DEFAULT NOW(),
                            expires_at TIMESTAMPTZ,
                            last_used_at TIMESTAMPTZ,
                            is_active BOOLEAN NOT NULL DEFAULT TRUE
                        )
                    """))
                    await session.execute(_text("CREATE INDEX IF NOT EXISTS idx_ak_username ON api_keys(username)"))
                    await session.execute(_text("CREATE INDEX IF NOT EXISTS idx_ak_active ON api_keys(is_active)"))
                    await session.commit()
        except Exception:
            pass


async def _m06_crm_new_columns() -> None:
    """DB Migration: CRM new columns"""
    # ── DB Migration: CRM new columns ──
    if state.db_online:
        try:
            from db.session import get_session_factory
            _f = get_session_factory()
            if _f:
                from sqlalchemy import text as _t
                async with _f() as _s:
                    # ADD COLUMN 清單住 db/migrations.CRM_COLUMNS（只有資料）—— 加欄位去那裡加
                    for tbl, col, coltype in _MIG.CRM_COLUMNS:
                        try:
                            await _s.execute(_t(f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {col} {coltype}"))
                            await _s.commit()
                        except Exception:
                            await _s.rollback()
                    # 既有週誌視為已送出（§13）：status 欄剛加上去是 NULL；程式建的殼一定帶 status，
                    # 所以重跑命中 0 列（冪等）
                    try:
                        await _s.execute(_t(
                            "UPDATE work_journals SET status='submitted', "
                            "submitted_at=COALESCE(updated_at, created_at) WHERE status IS NULL"))
                        await _s.commit()
                    except Exception:
                        await _s.rollback()
                    # 參考影片引用：舊 preprod_proposal_refs → preprod_reference_links
                    # （階段 3 一次性、冪等；舊表保留不再寫入，見 models.py 該類 docstring）
                    try:
                        await _s.execute(_t("""
                            INSERT INTO preprod_reference_links
                                   (id, reference_id, target_type, target_id, created_at)
                            SELECT r.id, r.reference_id, 'proposal', r.proposal_id, CURRENT_TIMESTAMP
                              FROM preprod_proposal_refs r
                             WHERE NOT EXISTS (
                                   SELECT 1 FROM preprod_reference_links l
                                    WHERE l.reference_id = r.reference_id
                                      AND l.target_type = 'proposal'
                                      AND l.target_id = r.proposal_id)
                        """))
                        await _s.commit()
                    except Exception as _mig_err:
                        # 不能靜默：所有讀取都已改查 links，搬遷失敗 = 每個提案的參考片
                        # 清單憑空變空，而且與「使用者自己移除」無法區分
                        print(f"[WARN] reference link backfill failed: {_mig_err}")
                        await _s.rollback()
        except Exception:
            pass


async def _m07_crm_indexes_and_ddl() -> None:
    """DB Migration: CRM 索引 + 冪等 DDL（逐條跑，失敗 rollback 不擋啟動）"""
    # ── DB Migration: CRM 索引 + 冪等 DDL（逐條跑，失敗 rollback 不擋啟動）──
    if state.db_online:
        try:
            from db.session import get_session_factory
            _fi = get_session_factory()
            if _fi:
                from sqlalchemy import text as _ti
                async with _fi() as _si:
                    for idx_sql in _MIG.CRM_INDEXES:
                        try:
                            await _si.execute(_ti(idx_sql))
                            await _si.commit()
                        except Exception:
                            await _si.rollback()
        except Exception:
            pass


async def _m08_proposal_shell_projects() -> None:
    """DB Migration: 提案=專案合體 — 既有提案補殼專案（冪等，2026-08-06）"""
    # ── DB Migration: 提案=專案合體 — 既有提案補殼專案（冪等，2026-08-06） ──
    if state.db_online:
        try:
            from routers.api_proposals import migrate_unlinked_proposals_to_projects
            await migrate_unlinked_proposals_to_projects()
        except Exception as _e_pp:
            # 不能靜默死：沒遷移的提案不會出現在管線專案列（前端提案帶只剩 legacy 提示）
            print(f"[WARN] 提案殼專案遷移失敗: {_e_pp}")


async def _m09_proposals_root_to_db() -> None:
    """settings.json 的 proposals.root → DB settings（一次性，2026-08-07）"""
    # ── settings.json 的 proposals.root → DB settings（一次性，2026-08-07）──
    # NAS 對外容器要讀得到它（客戶的提案分享頁由它 serve）。冪等；DB 已有值
    # 就不動，失敗只是繼續用 settings.json（fallback 仍在）。
    if state.db_online:
        try:
            from routers.crm.proposal_assets import migrate_root_to_db
            await migrate_root_to_db()
        except Exception as _e_pr:
            print(f"[WARN] proposals.root 遷移失敗: {_e_pr}")


async def _m10_reference_folder_rename() -> None:
    """片庫封存資料夾改名 uuid → {片名}_{品牌}（2026-08-06）"""
    # ── 片庫封存資料夾改名 uuid → {片名}_{品牌}（2026-08-06）──
    # 背景跑：逐支 rename 打 NAS，不擋 startup。閘門全在協程自己身上
    # （master gate、factory 為 None 早退、逐筆容錯），這裡不重複判斷。
    if state.db_online:
        from services.reference_archiver import migrate_folder_names
        asyncio.create_task(migrate_folder_names())


async def _m11_cost_lines_table() -> None:
    """DB Migration: crm_project_cost_lines table"""
    # ── DB Migration: crm_project_cost_lines table ──
    if state.db_online:
        try:
            from db.session import get_session_factory
            _fcl = get_session_factory()
            if _fcl:
                from sqlalchemy import text as _tcl
                async with _fcl() as _scl:
                    await _scl.execute(_tcl("""
                        CREATE TABLE IF NOT EXISTS crm_project_cost_lines (
                            id VARCHAR(32) PRIMARY KEY,
                            project_id VARCHAR(32) NOT NULL,
                            phase VARCHAR(32) NOT NULL,
                            item_name VARCHAR(128) NOT NULL,
                            sort_order INTEGER NOT NULL DEFAULT 0,
                            estimated_amount INTEGER,
                            estimated_staff_id VARCHAR(32),
                            estimated_notes VARCHAR(255),
                            actual_amount INTEGER,
                            actual_staff_id VARCHAR(32),
                            actual_notes VARCHAR(255),
                            created_at TIMESTAMPTZ DEFAULT NOW(),
                            updated_at TIMESTAMPTZ DEFAULT NOW()
                        )
                    """))
                    await _scl.execute(_tcl(
                        "CREATE INDEX IF NOT EXISTS idx_costline_project "
                        "ON crm_project_cost_lines(project_id)"
                    ))
                    await _scl.execute(_tcl(
                        "CREATE INDEX IF NOT EXISTS idx_costline_phase "
                        "ON crm_project_cost_lines(project_id, phase)"
                    ))
                    await _scl.commit()
        except Exception:
            pass


async def _m12_project_expenses_columns() -> None:
    """DB Migration: crm_project_expenses new columns"""
    # ── DB Migration: crm_project_expenses new columns ──
    if state.db_online:
        try:
            from db.session import get_session_factory
            _fex = get_session_factory()
            if _fex:
                from sqlalchemy import text as _tex
                async with _fex() as _sex:
                    for col_sql in _MIG.FINANCE_AND_CRM_COLUMNS:
                        try:
                            await _sex.execute(_tex(col_sql))
                            await _sex.commit()
                        except Exception:
                            await _sex.rollback()
        except Exception:
            pass


async def _m13_project_status_8_stage_backfill() -> None:
    """DB Migration: 專案狀態 8 階段化 backfill（一次性冪等）"""
    # ── DB Migration: 專案狀態 8 階段化 backfill（一次性冪等）──
    # 舊 5 值 taxonomy → 新 8 階段。單句 CASE UPDATE + WHERE IN(舊值)：backfill 完成後
    # 舊值不復存在，重跑 WHERE 命中 0 列（no-op）。一次 round-trip 取代 5 句，每次 boot
    # （含機隊 OTA 重啟）只掃一次；失敗只記錄不中斷 startup。
    if state.db_online:
        try:
            from db.session import get_session_factory
            _fst = get_session_factory()
            if _fst:
                from sqlalchemy import text as _tst
                async with _fst() as _sst:
                    _r_st = await _sst.execute(_tst(
                        "UPDATE crm_projects SET status = CASE status "
                        "WHEN '洽談中' THEN '洽詢' WHEN '報價中' THEN '提案' "
                        "WHEN '進行中' THEN '製作' WHEN '已結案' THEN '歸檔' "
                        "WHEN '結案作業' THEN '結案' END "
                        "WHERE status IN ('洽談中','報價中','進行中','已結案','結案作業')"
                    ))
                    await _sst.commit()
                    if _r_st.rowcount:
                        print(f"[startup] 專案狀態 8 階段化 backfill: {_r_st.rowcount} 筆")
        except Exception as _e_st_all:
            print(f"[startup] 專案狀態 backfill migration failed: {_e_st_all}")


async def _m14_cost_line_templates_table() -> None:
    """DB Migration: crm_cost_line_templates table"""
    # ── DB Migration: crm_cost_line_templates table ──
    if state.db_online:
        try:
            from db.session import get_session_factory
            _ftpl = get_session_factory()
            if _ftpl:
                from sqlalchemy import text as _ttpl
                async with _ftpl() as _stpl:
                    await _stpl.execute(_ttpl("""
                        CREATE TABLE IF NOT EXISTS crm_cost_line_templates (
                            id VARCHAR(32) PRIMARY KEY,
                            name VARCHAR(128) NOT NULL,
                            items JSONB,
                            created_at TIMESTAMPTZ DEFAULT NOW()
                        )
                    """))
                    await _stpl.commit()
        except Exception:
            pass


async def _m15_cost_lines_new_columns() -> None:
    """DB Migration: crm_project_cost_lines new columns"""
    # ── DB Migration: crm_project_cost_lines new columns ──
    if state.db_online:
        try:
            from db.session import get_session_factory
            _fcln = get_session_factory()
            if _fcln:
                from sqlalchemy import text as _tcln
                async with _fcln() as _scln:
                    for col_sql in _MIG.COST_LINE_COLUMNS:
                        try:
                            await _scln.execute(_tcln(col_sql))
                            await _scln.commit()
                        except Exception:
                            await _scln.rollback()
        except Exception:
            pass


async def _m16_staff_resume_and_portfolio() -> None:
    """DB Migration: crm_staff resume columns + crm_staff_portfolio table"""
    # ── DB Migration: crm_staff resume columns + crm_staff_portfolio table ──
    if state.db_online:
        try:
            from db.session import get_session_factory
            _fres = get_session_factory()
            if _fres:
                from sqlalchemy import text as _tres
                async with _fres() as _sres:
                    for col_sql in _MIG.STAFF_AND_SHOWCASE_COLUMNS:
                        try:
                            await _sres.execute(_tres(col_sql))
                            await _sres.commit()
                        except Exception:
                            await _sres.rollback()
                    # Create portfolio table
                    await _sres.execute(_tres("""
                        CREATE TABLE IF NOT EXISTS crm_staff_portfolio (
                            id VARCHAR(32) PRIMARY KEY,
                            staff_id VARCHAR(32) NOT NULL,
                            title VARCHAR(256) NOT NULL,
                            url VARCHAR(512) NOT NULL,
                            thumbnail_url VARCHAR(512),
                            role_desc VARCHAR(256),
                            sort_order INTEGER DEFAULT 0,
                            created_at TIMESTAMPTZ DEFAULT NOW()
                        )
                    """))
                    await _sres.commit()
                    # Index on staff_id
                    try:
                        await _sres.execute(_tres(
                            "CREATE INDEX IF NOT EXISTS idx_portfolio_staff_id ON crm_staff_portfolio (staff_id)"
                        ))
                        await _sres.commit()
                    except Exception:
                        await _sres.rollback()
                    # Create project showcase table
                    await _sres.execute(_tres("""
                        CREATE TABLE IF NOT EXISTS crm_project_showcase (
                            id VARCHAR(32) PRIMARY KEY,
                            cover_url VARCHAR(512),
                            description TEXT,
                            video_url VARCHAR(512),
                            gallery JSONB,
                            process_mode VARCHAR(16) NOT NULL DEFAULT 'gallery',
                            process_items JSONB,
                            credits JSONB,
                            slug VARCHAR(128) UNIQUE,
                            published BOOLEAN NOT NULL DEFAULT FALSE,
                            published_at TIMESTAMPTZ,
                            edit_token VARCHAR(512),
                            editable BOOLEAN NOT NULL DEFAULT TRUE,
                            created_at TIMESTAMPTZ DEFAULT NOW(),
                            updated_at TIMESTAMPTZ DEFAULT NOW()
                        )
                    """))
                    await _sres.commit()
                    try:
                        await _sres.execute(_tres(
                            "CREATE UNIQUE INDEX IF NOT EXISTS idx_showcase_slug ON crm_project_showcase (slug) WHERE slug IS NOT NULL AND slug != ''"
                        ))
                        await _sres.commit()
                    except Exception:
                        await _sres.rollback()
                    # AI 參考資料上傳（製作人上傳文件抽文字 + 補充說明 → 餵 AI）
                    for col_sql in _MIG.SHOWCASE_AI_COLUMNS:
                        try:
                            await _sres.execute(_tres(col_sql))
                            await _sres.commit()
                        except Exception:
                            await _sres.rollback()
        except Exception:
            pass


async def _m17_website_phase_m() -> None:
    """Phase M: Website migrations (ALTER crm_projects/crm_staff + 5 new tables) + seed"""
    # ── Phase M: Website migrations (ALTER crm_projects/crm_staff + 5 new tables) + seed ──
    if state.db_online:
        try:
            from db.session import get_session_factory
            _f_web = get_session_factory()
            if _f_web:
                from db.migrations_website import run_website_migrations
                from db.seed_website import seed_website_if_empty
                await run_website_migrations(_f_web)
                await seed_website_if_empty(_f_web)
                # AI SEO runner 排程 loop（每 60s 檢查 cron 是否到期）
                from services.website import seo_runner, post_seo_runner, translation_service, backup_service, social_runner
                seo_runner.start_scheduler_task()        # 作品集
                post_seo_runner.start_scheduler_task()   # 文章（影像專欄）
                translation_service.start_scheduler_task()  # 英文翻譯（transcreation）
                backup_service.start_scheduler_task()    # 資料備份 → Google Drive（只在有 NAS key 的 master 跑）
                social_runner.start_scheduler_task()     # 社群文稿（Phase N-soc；只在有 claude 的 master 跑）
                from services import intel_runner
                intel_runner.start_scheduler_task()       # 產業情報（P-c；只在有 claude 的 master 跑）
                from services.website import watchdog_runner
                watchdog_runner.start_scheduler_task()   # 站點守衛（多 UA 篡改探測 + GSC 收錄；只在有 website/ 的 master 跑）
        except Exception as _e_web:
            print(f"[startup] Website migration/seed failed: {_e_web}")


async def _m18_cost_groups_table_and_backfill() -> None:
    """Phase J-5: crm_project_cost_groups table + backfill 主表"""
    # ── Phase J-5: crm_project_cost_groups table + backfill 主表 ──
    if state.db_online:
        try:
            import uuid as _uuid_cg
            from db.session import get_session_factory
            _fcg = get_session_factory()
            if _fcg:
                from sqlalchemy import text as _tcg
                async with _fcg() as _scg:
                    # 1. 建新表
                    await _scg.execute(_tcg("""
                        CREATE TABLE IF NOT EXISTS crm_project_cost_groups (
                            id VARCHAR(32) PRIMARY KEY,
                            project_id VARCHAR(32) NOT NULL,
                            name VARCHAR(128) NOT NULL,
                            shoot_date TIMESTAMPTZ,
                            notes TEXT,
                            sort_order INTEGER NOT NULL DEFAULT 0,
                            budget_amount INTEGER,
                            misc_budget_amount INTEGER,
                            profit_target_pct INTEGER,
                            created_at TIMESTAMPTZ DEFAULT NOW(),
                            updated_at TIMESTAMPTZ DEFAULT NOW()
                        )
                    """))
                    await _scg.execute(_tcg(
                        "CREATE INDEX IF NOT EXISTS idx_costgroup_project "
                        "ON crm_project_cost_groups(project_id, sort_order)"
                    ))
                    # 2. cost_lines + expenses 加 cost_group_id 欄位
                    for col_sql in _MIG.COST_GROUP_COLUMNS:
                        try:
                            await _scg.execute(_tcg(col_sql))
                        except Exception:
                            pass
                    await _scg.commit()
                    # 3. 只迭代尚未有 cost_group 的專案（migration 完成後此 query 通常回 0 筆）
                    pending = (await _scg.execute(_tcg(
                        "SELECT id FROM crm_projects WHERE id NOT IN "
                        "(SELECT DISTINCT project_id FROM crm_project_cost_groups "
                        " WHERE project_id IS NOT NULL)"
                    ))).fetchall()
                    for (pid,) in pending:
                        try:
                            gid = _uuid_cg.uuid4().hex
                            await _scg.execute(_tcg(
                                "INSERT INTO crm_project_cost_groups (id, project_id, name, sort_order) "
                                "VALUES (:id, :pid, '主表', 0)"
                            ), {"id": gid, "pid": pid})
                            await _scg.execute(_tcg(
                                "UPDATE crm_project_cost_lines SET cost_group_id = :gid "
                                "WHERE project_id = :pid AND cost_group_id IS NULL"
                            ), {"gid": gid, "pid": pid})
                            await _scg.execute(_tcg(
                                "UPDATE crm_project_expenses SET cost_group_id = :gid "
                                "WHERE project_id = :pid AND cost_group_id IS NULL"
                            ), {"gid": gid, "pid": pid})
                            await _scg.commit()
                        except Exception as _e_pid:
                            await _scg.rollback()
                            print(f"[startup] cost_groups backfill for project {pid} failed: {_e_pid}")
        except Exception as _e_cg:
            print(f"[startup] cost_groups migration failed: {_e_cg}")


async def _m19_finance_phase2_tables() -> None:
    """財務管理階段二：科目/對映/銀行帳戶/對帳/調整表"""
    # ── 財務管理階段二：科目/對映/銀行帳戶/對帳/調整表 ──
    # 新表（finance_accounts / finance_category_map / bank_accounts /
    # bank_reconciliations / finance_adjustments，及階段四的 finance_loans /
    # finance_loan_payments）由 init_db 的 Base.metadata.create_all 建；
    # 這裡補既有表新欄位 + 冪等種子（階段四貸款對映也在種子清單內）。
    if state.db_online:
        try:
            from db.session import get_session_factory
            _ffin = get_session_factory()
            if _ffin:
                from sqlalchemy import text as _tfin
                async with _ffin() as _sfin:
                    for col_sql in _MIG.FINANCE_LEDGER_COLUMNS:
                        try:
                            await _sfin.execute(_tfin(col_sql))
                            await _sfin.commit()
                        except Exception:
                            await _sfin.rollback()
                # 種子：科目表 + category 對映（冪等 — 查無才 insert，不覆蓋後台調整）
                from db.seed_finance import seed_finance_stage2
                await seed_finance_stage2(_ffin)
        except Exception as _e_fin:
            print(f"[startup] finance stage2 migration/seed failed: {_e_fin}")


async def _m20_seed_mine_cash_taxonomy() -> None:
    """種子：私帳收支分類樹（冪等；含把 entry 回填到葉節點）"""
    # ── 種子：私帳收支分類樹（冪等；含把 entry 回填到葉節點）──────────
    # 🔴 獨立 try —— 這是私帳那本的分類值域，掛了不該連累 startup 或財務種子。
    if state.db_online:
        try:
            from db.session import get_session_factory
            _ftax = get_session_factory()
            if _ftax:
                from db.seed_cash_taxonomy import seed_cash_taxonomy
                await seed_cash_taxonomy(_ftax)
                # 🔴 科目對映的帳本回填要**排在分類樹種好之後** —— 判定規則就是
                # 「這個類別在不在私帳的分類樹裡」。
                from db.seed_finance import backfill_category_map_entity
                await backfill_category_map_entity(_ftax)
        except Exception as _e_tax:
            print(f"[startup] cash taxonomy seed failed: {_e_tax}")


async def _m21_seed_work_stages() -> None:
    """種子：工作階段（每個分類自己的階段清單；**只在表空時寫**，之後 owner 在編輯器改）"""
    # ── 種子：工作階段（每個分類自己的階段清單；**只在表空時寫**，之後 owner 在編輯器改）──
    if state.db_online:
        try:
            from db.session import get_session_factory
            _fws = get_session_factory()
            if _fws:
                from routers.crm.work_stages import seed_if_empty as _seed_stages
                async with _fws() as _sws:
                    await _seed_stages(_sws)
        except Exception as _e_ws:
            print(f"[startup] work stage seed failed: {_e_ws}")


async def _backfill_by_parent(session) -> tuple:
    """私帳案收入分案記帳的舊資料回填本體 → (1:1 認了幾案, N:1 認出幾案, 待認領幾案)。
    冪等：有 by_parent 或 by_parent_pending 的列不再碰。1:1 只認「上次鏡射過來的量」；N:1 用掛給 owner
    的成本行逐案認（owner＝唯一一個有 finance_mine 且綁了人員檔的帳號），加總對得上合約額才認，
    否則標 by_parent_pending 由 owner 逐案「推送→取代」認領。"""
    n1 = nn = pend = 0
    from sqlalchemy import select
    from db.models import CrmProject, User
    from core.ledger import MINE_MODULE
    from core.auth import expand_modules
    from core.ledger_project import (BILLING_MIRROR_SOURCE, BY_PARENT_PENDING_KEY, MIRROR_SOURCE,
                                     billing_mode_of, legacy_claim, norm_detail, parent_shares,
                                     set_parent_share)
    from routers.crm._shared import mine_parent_links
    from routers.crm.project_links import _mirror_contract, _mirror_preview
    mine_ids = [i for (i,) in (await session.execute(
        select(CrmProject.id).where(CrmProject.entity == "mine"))).all()]
    links = {k: v for k, v in (await mine_parent_links(session, mine_ids)).items() if v}
    rows = (await session.execute(
        select(CrmProject).where(CrmProject.id.in_(list(links) or [""])))).scalars().all()
    owner_sid = None        # 只在真的碰到沒認領的 N:1 時才去查 users（第一次開機之後都不會）
    for t in rows:
        parents = links.get(t.id)
        if not parents:
            continue
        keep = norm_detail(t.ledger_detail)
        if parent_shares(keep) or keep.get(BY_PARENT_PENDING_KEY) is True:
            continue
        contract = int(t.contract_amount or 0)
        if len(parents) == 1:
            # 只認「上次鏡射過來的量」（mirror_total）；合約額比它大的是 owner 自己加的
            keep = legacy_claim(keep, contract, parents[0][0])
            n1 += 1
        else:
            if owner_sid is None:
                owners = [u.staff_id for u in (await session.execute(
                    select(User).where(User.staff_id.isnot(None)))).scalars().all()
                          if MINE_MODULE in expand_modules(u.modules or [])]
                owner_sid = owners[0] if len(owners) == 1 else ""
            claimed = []
            if owner_sid:
                for pid, _name in parents:
                    p, mir, _l = await _mirror_preview(session, pid, owner_sid)
                    src = BILLING_MIRROR_SOURCE.get(billing_mode_of(p.billing_mode), "")
                    claimed.append((pid, _mirror_contract(p, mir, src), mir["split"], int(mir["total"] or 0)))
            amounts = [a for _p, a, _s, _t in claimed]
            if amounts and min(amounts) > 0 and sum(amounts) <= contract:
                for pid, amount, split, total in claimed:
                    keep, _c = set_parent_share(keep, contract, pid, amount, split,
                                                synced_total=total,
                                                source=keep.get("source") or MIRROR_SOURCE, claim=True)
                nn += 1
            else:
                keep[BY_PARENT_PENDING_KEY] = True
                pend += 1
        t.ledger_detail = keep
    return n1, nn, pend


async def _m22_ledger_by_parent_backfill() -> None:
    """私帳案收入分案記帳（ledger_detail.by_parent）的舊資料回填（owner 2026-09-13「為何不加起來？」）"""
    if state.db_online:
        try:
            factory = get_session_factory()
            if factory:
                async with factory() as session:
                    n1, nn, pend = await _backfill_by_parent(session)
                    if n1 or nn or pend:
                        await session.commit()
                if n1 or nn or pend:
                    print(f"[migrate] 私帳分案記帳回填：1:1 {n1} 案、N:1 認出 {nn} 案、待認領 {pend} 案")
        except Exception as _e_bp:
            print(f"[migrate] 私帳分案記帳回填略過: {_e_bp}")


_POST_DB = [
    _m01_google_oauth_columns,
    _m02_me_zone_split_backfill,
    _m03_me_today_zone_backfill,
    _m04_bulletin_columns_and_seed,
    _m05_api_keys_table,
    _m06_crm_new_columns,
    _m07_crm_indexes_and_ddl,
    _m08_proposal_shell_projects,
    _m09_proposals_root_to_db,
    _m10_reference_folder_rename,
    _m11_cost_lines_table,
    _m12_project_expenses_columns,
    _m13_project_status_8_stage_backfill,
    _m14_cost_line_templates_table,
    _m15_cost_lines_new_columns,
    _m16_staff_resume_and_portfolio,
    _m17_website_phase_m,
    _m18_cost_groups_table_and_backfill,
    _m19_finance_phase2_tables,
    _m20_seed_mine_cash_taxonomy,
    _m21_seed_work_stages,
    _m22_ledger_by_parent_backfill,
]


async def run_post_db() -> None:
    """init_db() 之後、背景工之前：照 _POST_DB 的順序逐段跑。各段自己守 db_online 與例外。"""
    for step in _POST_DB:
        await step()
