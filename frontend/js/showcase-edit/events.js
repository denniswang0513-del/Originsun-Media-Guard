// ────────────────────────────────────────────────────────────────────────────
// /showcase-edit.html 作品編輯器 —— _bindEvents：每次重畫後綁一次的所有互動（上傳、影片列、系列、分類、儲存…）
//
// ⚠ 這是「傳統 script」不是 module：showcase-edit.html 底部依序 <script src> 載入 js/showcase-edit/*.js，
//   六支共用同一個全域詞法環境，頂層的 const／let／function 跨檔直接看得見（原本是同一段 inline script，
//   2026-09-12 原樣切開、零改寫；同 my.html → js/my/ 的拆法）。載入順序＝原本由上而下的執行順序，不可調換。
// ────────────────────────────────────────────────────────────────────────────
function _bindEvents() {
    const sc = DATA || {};

    // === Dirty tracker + master save ===
    // setDirty(key)：累積「哪些東西沒存」的 key 集合 → save-bar 顯示中文明細。
    // key=false 清空；key=true/未知字串 → 通用「變更」。所有 [data-field] 自帶欄位 key。
    const FIELD_LABELS = {
        public_title: '標題', public_client: '客戶', public_year: '年份',
        video_url: '主影片', description: '描述', credits_text: 'Credits 文字',
        series_id: '系列', published: '公開', public_featured: '首頁精選',
        public_noindex: 'noindex',
        _extra_videos: '附加影片', _old_slugs: '301 轉址', _seo: 'SEO 覆寫',
        _cats: '分類/標籤', _ai_notes: '補充說明', _misc: '變更',
    };
    const _dirtyKeys = new Set();
    const _statusEl = document.getElementById('save-status');
    const _btnMaster = document.getElementById('btn-master-save');
    const setDirty = (key) => {
        if (key === false) { _dirtyKeys.clear(); _dirtyEls.clear(); _catsDraft = null; }
        else _dirtyKeys.add(typeof key === 'string' ? key : '_misc');
        if (_statusEl) {
            if (!_dirtyKeys.size) {
                _statusEl.textContent = '✓ 所有變更已儲存';
                _statusEl.style.color = 'var(--text3)';
            } else {
                const labels = [...new Set([..._dirtyKeys].map(k => FIELD_LABELS[k] || FIELD_LABELS._misc))];
                const shown = labels.slice(0, 4).join('、') + (labels.length > 4 ? '…' : '');
                _statusEl.textContent = `有未儲存的變更：${shown}（${labels.length} 項）`;
                _statusEl.style.color = '#f59e0b';
            }
        }
        _updateChecklist();
    };
    window._scSetDirty = setDirty;   // 給 _bindEvents 外的功能用（分類小測驗套用）
    document.querySelectorAll('[data-field]').forEach(el => {
        const evt = (el.type === 'checkbox' || el.tagName === 'SELECT') ? 'change' : 'input';
        el.addEventListener(evt, () => { _markDirtyEl('f:' + el.dataset.field); setDirty(el.dataset.field); });
    });
    // 補充說明併入 master save（獨立儲存鈕已廢）
    document.getElementById('inp-ai-ref-notes')?.addEventListener('input', () => { _markDirtyEl('id:inp-ai-ref-notes'); setDirty('_ai_notes'); });
    // 公開勾選的必備警示由 setDirty('published') → _updateChecklist → _updatePublishWarn 覆蓋，毋須獨立監聽
    // 對外標題即時連動到頂部 page-title + parent iframe panel 標題 — 三處同步
    const _titleInp = document.getElementById('inp-public-title');
    const _titleEl = document.getElementById('page-title');
    if (_titleInp) {
        const _projName = DATA.project_name || '';
        const _syncTitle = () => {
            const t = _titleInp.value.trim() || _projName;
            if (_titleEl) _titleEl.textContent = t;
            // 通知 parent admin iframe panel 上方「編輯：XXX」label 跟進
            try { window.parent?.postMessage({ type: 'showcase-title-change', title: t }, '*'); } catch (_) {}
        };
        _titleInp.addEventListener('input', _syncTitle);
        _syncTitle();  // 初次載入也 sync 一次（iframe 開啟時 parent 標題會帶 client prefix）
    }
    // === 複製預覽連結到剪貼簿 ===
    document.getElementById('btn-copy-url')?.addEventListener('click', async () => {
        const inp = document.getElementById('preview-url-input');
        if (!inp) return;
        const btn = document.getElementById('btn-copy-url');
        const orig = btn.textContent;
        try {
            await navigator.clipboard.writeText(inp.value);
        } catch (_) {
            // navigator.clipboard 在 http:// 沒有 — fallback 用 select+execCommand
            inp.select();
            document.execCommand('copy');
        }
        btn.textContent = '✓ 已複製';
        setTimeout(() => { btn.textContent = orig; }, 1500);
    });

    // === 客戶 autocomplete combobox ===
    (() => {
        const inp = document.getElementById('inp-public-client');
        const dd = document.getElementById('client-dropdown');
        if (!inp || !dd) return;
        let timer = null;
        let seq = 0;  // race protection — 慢網路下舊 fetch 後到不蓋掉新 dropdown

        const renderItems = (items, q) => {
            if (!items || !items.length) {
                dd.innerHTML = `<div style="padding:10px;color:var(--text3);font-size:12px;text-align:center;">${q ? '無符合客戶' : '尚無客戶資料'}</div>`;
                dd.style.display = 'block';
                return;
            }
            dd.innerHTML = items.map(c => `
                <div class="client-item" data-name="${_esc(c.short_name)}"
                     style="padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px;"
                     onmouseover="this.style.background='var(--bg3)'"
                     onmouseout="this.style.background=''">
                    <div style="flex:1;min-width:0;">
                        <div style="color:var(--text);font-size:13px;font-weight:500;">${_esc(c.short_name)}</div>
                        ${c.full_name ? `<div style="color:var(--text3);font-size:11px;">${_esc(c.full_name)}</div>` : ''}
                    </div>
                </div>
            `).join('');
            dd.querySelectorAll('.client-item').forEach(it => {
                it.addEventListener('mousedown', (ev) => {
                    ev.preventDefault();
                    inp.value = it.dataset.name;
                    setDirty('public_client');
                    dd.style.display = 'none';
                });
            });
            dd.style.display = 'block';
        };

        const search = async (q) => {
            const mySeq = ++seq;
            try {
                const res = await fetch(`${API}/clients_search?q=${encodeURIComponent(q)}`);
                if (!res.ok) return;
                const data = await res.json();
                if (mySeq !== seq) return;
                renderItems(data.items || [], q);
            } catch (_) {}
        };

        inp.addEventListener('focus', () => search(''));
        inp.addEventListener('input', () => {
            if (timer) clearTimeout(timer);
            timer = setTimeout(() => search(inp.value.trim()), 200);
        });
        inp.addEventListener('blur', () => {
            // 150ms 延遲讓 mousedown 先觸發
            setTimeout(() => { dd.style.display = 'none'; }, 150);
        });
    })();

    // 系列下拉「+ 建立新系列…」→ prompt 名稱 → 建立隱藏系列並選取
    const _serSel = document.getElementById('inp-series-id');
    _serSel?.addEventListener('change', async () => {
        if (_serSel.value !== '__new__') return;
        const s = await _quickCreateSeries();
        if (s) _ensureSeriesOption(_serSel, s.id);
        else _serSel.value = DATA.series_id != null ? String(DATA.series_id) : '';
        setDirty('series_id');
    });

    // textarea 舊 URL — 任何鍵盤動作 mark dirty（內容轉成 list 在 master save 時做）
    document.getElementById('inp-old-slugs')?.addEventListener('input', () => { _markDirtyEl('id:inp-old-slugs'); setDirty('_old_slugs'); });

    // SEO 覆寫欄位 — 4 個 input/textarea 任一動就 mark dirty
    ['inp-seo-title', 'inp-seo-desc', 'inp-seo-keywords', 'inp-seo-canonical'].forEach(id => {
        document.getElementById(id)?.addEventListener('input', () => { _markDirtyEl('id:' + id); setDirty('_seo'); });
    });

    // 「請 AI 重新生」— mark needs_ai_review=True，下次排程自動跑（不耗 LLM 額度）
    document.getElementById('btn-request-ai-review')?.addEventListener('click', async () => {
        const btn = document.getElementById('btn-request-ai-review');
        const orig = btn.textContent;
        btn.disabled = true;
        btn.textContent = '請求中...';
        try {
            const res = await fetch(`${API}/request_ai_review`, { method: 'POST' });
            if (!res.ok) throw new Error(await res.text() || '請求失敗');
            btn.textContent = '✓ 已加入 AI 待生清單';
            _toast('已標記，下次排程自動補');
            if (DATA && DATA.seo) DATA.seo.needs_ai_review = true;
            await _load();
        } catch (e) {
            btn.textContent = orig;
            btn.disabled = false;
            alert('請求失敗：' + e.message);
        }
    });

    // 「立即執行此作品」— 直接呼 claude --print，30-60 秒，耗 Max 額度
    document.getElementById('btn-run-ai-now')?.addEventListener('click', async () => {
        const btn = document.getElementById('btn-run-ai-now');
        const orig = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'Claude 撰寫中…';
        try {
            const res = await fetch(`${API}/run_ai_now`, { method: 'POST' });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || data.error || '執行失敗');
            if (data.status === 'busy') {
                _toast('已有 AI runner 在跑，請稍後再試');
            } else if (data.processed > 0) {
                _toast('AI 已生成，重新載入內容');
                await _load();
            } else {
                const w = (data.works || [])[0];
                _toast(`未生成：${w?.detail || data.error || '請看 master log'}`);
            }
        } catch (e) {
            alert('執行失敗：' + e.message);
        } finally {
            btn.disabled = false;
            btn.textContent = orig;
        }
    });

    // Master save：收所有 [data-field] 現值 + old_slugs 一次 PUT
    // （「儲存並發布」= 同一 handler，只是放在發布 stage 旁）
    const _masterSave = async () => {
        const payload = {};
        document.querySelectorAll('[data-field]').forEach(el => {
            const f = el.dataset.field;
            if (el.type === 'checkbox') {
                payload[f] = el.checked;
            } else if (el.type === 'number') {
                const n = el.value === '' ? null : Number(el.value);
                payload[f] = Number.isNaN(n) ? null : n;
            } else {
                payload[f] = el.value;
            }
        });
        // 防呆：系列下拉停在「+ 建立新系列…」哨兵值時，回退為目前已存的系列
        if (payload.series_id === '__new__') payload.series_id = DATA.series_id ?? '';
        // old_slugs 從 textarea 解析：一行一個、trim、過濾空行
        const oldUrlsRaw = document.getElementById('inp-old-slugs')?.value || '';
        payload.public_old_slugs = oldUrlsRaw.split(/\r?\n/).map(s => s.trim()).filter(Boolean);

        // 附加影片 — 動態列收集；空 url 列不送（後端亦會再正規化一次）
        payload.extra_videos = [...document.querySelectorAll('#extra-videos-list .extra-video-row')].map(row => ({
            url: (row.querySelector('.extra-video-url')?.value || '').trim(),
            caption: (row.querySelector('.extra-video-caption')?.value || '').trim(),
        })).filter(v => v.url);

        // 作品分類 / 標籤 — toggle handler 已即時更新 DATA.selected_category_ids
        payload.category_ids = DATA.selected_category_ids || [];

        // SEO 覆寫欄位 — 4 個輸入；keywords 接受 ASCII 半形 + 中文全形 + 頓號分隔
        const kwRaw = document.getElementById('inp-seo-keywords')?.value || '';
        payload.seo = {
            seo_title: (document.getElementById('inp-seo-title')?.value || '').trim() || null,
            seo_description: (document.getElementById('inp-seo-desc')?.value || '').trim() || null,
            keywords: kwRaw.split(/[,，、]/).map(s => s.trim()).filter(Boolean),
            canonical_url: (document.getElementById('inp-seo-canonical')?.value || '').trim() || null,
        };
        _btnMaster.disabled = true;
        const orig = _btnMaster.textContent;
        _btnMaster.textContent = '儲存中...';
        try {
            // 補充說明（AI 參考資料 notes）跟著 master save 一起送（獨立端點，變了才打）
            const notesEl = document.getElementById('inp-ai-ref-notes');
            if (notesEl && notesEl.value !== (DATA.ai_reference_notes || '')) {
                const nr = await fetch(API + '/ai_reference', {
                    method: 'PUT', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ notes: notesEl.value }),
                });
                if (!nr.ok) throw new Error('補充說明儲存失敗');
            }
            await _save(payload);
            setDirty(false);
            _btnMaster.textContent = '✓ 已儲存';
            setTimeout(() => { _btnMaster.textContent = orig; _btnMaster.disabled = false; }, 1500);
            await _reload();
            // 通知 parent admin Tab：作品標題 / 客戶 / 公開狀態都可能變了，去 reload 列表
            try { window.parent?.postMessage({ type: 'showcase-saved' }, '*'); } catch (_) {}
        } catch (e) {
            alert(e.message);
            _btnMaster.textContent = orig;
            _btnMaster.disabled = false;
        }
    };
    _btnMaster?.addEventListener('click', _masterSave);
    document.getElementById('btn-publish-save')?.addEventListener('click', _masterSave);

    // 離頁前未存提示
    window.addEventListener('beforeunload', (e) => {
        if (_dirtyKeys.size) { e.preventDefault(); e.returnValue = ''; }
    });

    // === 主影片 provider 偵測提示（Vimeo/FB 將內嵌、其他連結 link-out）===
    // dirty 追蹤已由 [data-field] 通用 handler 處理，這裡只管提示文字
    const _videoInp = document.getElementById('inp-video');
    if (_videoInp) {
        _videoInp.addEventListener('input', _updateVideoHint);  // input 已涵蓋打字/貼上/autofill
        _updateVideoHint();   // 初始渲染也跑一次（既有值直接顯示提示）
    }

    // === 附加影片：動態列（in-memory 編輯，master save 一次收集）===
    // 列可動態增刪 → input dirty 與刪除鈕都走事件委派，新插入的列免重綁
    const _evList = document.getElementById('extra-videos-list');
    if (_evList) {
        _evList.addEventListener('input', () => { _markDirtyEl('rows:extra-videos'); setDirty('_extra_videos'); });
        _evList.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-action="del-extra-video"]');
            if (!btn) return;
            btn.closest('.extra-video-row')?.remove();
            _markDirtyEl('rows:extra-videos'); setDirty('_extra_videos');
        });
    }
    document.getElementById('btn-add-extra-video')?.addEventListener('click', () => {
        if (!_evList) return;
        _evList.insertAdjacentHTML('beforeend', _extraVideoRowHtml({ url: '', caption: '' }));
        _evList.querySelector('.extra-video-row:last-child .extra-video-url')?.focus();
        _markDirtyEl('rows:extra-videos'); setDirty('_extra_videos');
    });

    // Gallery upload
    document.getElementById('btn-add-gallery')?.addEventListener('click', () => document.getElementById('inp-gallery').click());
    if (document.getElementById('inp-gallery')) document.getElementById('inp-gallery').accept = 'image/' + '*';   // 不寫在模板：原始碼裡的 image 加斜線星號會讓 _srcscan.js_code_only 整段吃掉
    document.getElementById('inp-gallery')?.addEventListener('change', async (e) => {
        for (const file of e.target.files) {
            try { await _upload('/gallery', file); } catch (err) { alert(err.message); }
        }
        await _reload();
    });

    // Gallery delete
    document.querySelectorAll('[data-action="del-gallery"]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const gallery = [...(sc.gallery || [])];
            gallery.splice(parseInt(btn.dataset.idx), 1);
            try { await _save({ gallery }); await _reload(); } catch (e) { alert(e.message); }
        });
    });

    // Featured image upload → 寫 CrmProject.public_featured_image（回 {url}）
    document.getElementById('btn-add-featured')?.addEventListener('click', () => document.getElementById('inp-featured').click());
    if (document.getElementById('inp-featured')) document.getElementById('inp-featured').accept = 'image/' + '*';   // 不寫在模板：原始碼裡的 image 加斜線星號會讓 _srcscan.js_code_only 整段吃掉
    document.getElementById('inp-featured')?.addEventListener('change', async (e) => {
        if (e.target.files[0]) {
            try { await _upload('/featured_image', e.target.files[0]); await _reload(); } catch (err) { alert(err.message); }
        }
    });

    // Featured image remove → PUT public_featured_image="" 清空
    document.querySelector('[data-action="del-featured"]')?.addEventListener('click', async () => {
        try { await _save({ public_featured_image: '' }); await _reload(); } catch (e) { alert(e.message); }
    });

    // AI 協助撰寫 SEO 描述
    document.getElementById('btn-ai-desc')?.addEventListener('click', () => _aiDescStart());

    // === AI 參考資料 ===
    // 上傳：多檔逐一 POST /ai_reference/upload（multipart field 'file'）→ 全部完成後 _reload
    document.getElementById('btn-add-ai-ref')?.addEventListener('click', () => document.getElementById('inp-ai-ref').click());
    document.getElementById('inp-ai-ref')?.addEventListener('change', async (e) => {
        const files = [...e.target.files];
        if (!files.length) return;
        const _u = document.getElementById('ai-ref-uploading');
        if (_u) _u.style.display = 'inline';
        for (const file of files) {
            try { await _upload('/ai_reference/upload', file); }
            catch (err) { _toast(err.message); }
        }
        if (_u) _u.style.display = 'none';
        await _reload();
    });
    // 刪除單一參考檔：DELETE /ai_reference/file body {name}
    document.querySelectorAll('[data-action="del-ai-ref"]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const name = btn.dataset.name;
            try {
                const res = await fetch(API + '/ai_reference/file', {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name }),
                });
                if (!res.ok) { const err = await res.json().catch(() => ({})); throw new Error(err.detail || '刪除失敗'); }
                await _reload();
            } catch (e) { _toast(e.message); }
        });
    });
    // （「儲存補充說明」獨立按鈕已廢 — notes 併入 master save，見 _masterSave）

    // Process upload
    document.getElementById('btn-add-process')?.addEventListener('click', () => document.getElementById('inp-process').click());
    if (document.getElementById('inp-process')) document.getElementById('inp-process').accept = 'image/' + '*';   // 不寫在模板：原始碼裡的 image 加斜線星號會讓 _srcscan.js_code_only 整段吃掉
    document.getElementById('inp-process')?.addEventListener('change', async (e) => {
        if (e.target.files[0]) {
            try { await _upload('/process', e.target.files[0]); await _reload(); } catch (err) { alert(err.message); }
        }
    });

    // Process：從影像紀錄挑選（劇組已收檔的照片直接轉進創作過程，免重新上傳）
    document.getElementById('btn-pick-media-log')?.addEventListener('click', () => _mlpOpen());

    // Process delete
    document.querySelectorAll('[data-action="del-process"]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const items = [...(sc.process_items || [])];
            items.splice(parseInt(btn.dataset.idx), 1);
            try { await _save({ process_items: items }); await _reload(); } catch (e) { alert(e.message); }
        });
    });

    // === Credits 雙模式 toggle（結構化 ↔ 純文字）===
    const _switchCreditsMode = async (mode) => {
        try {
            await _save({ credits_mode: mode });
            await _reload();
        } catch (e) { alert(e.message); }
    };
    document.getElementById('btn-mode-block')?.addEventListener('click', () => {
        if (DATA.credits_mode === 'block') return;
        _switchCreditsMode('block');
    });
    document.getElementById('btn-mode-text')?.addEventListener('click', () => {
        if (DATA.credits_mode === 'text') return;
        _switchCreditsMode('text');
    });
    // 純文字模式 — 個別儲存按鈕已移除，改走 master save

    // === Credits block 編輯器 ===
    const _saveBlocks = async (blocks) => {
        try { await _save({ credits: blocks }); await _reload(); }
        catch (e) { alert(e.message); }
    };

    // 新增職位 dropdown（含「自訂分類」選項）
    document.getElementById('sel-add-role')?.addEventListener('change', async (ev) => {
        const val = ev.target.value;
        if (!val) return;
        const blocks = _currentBlocks();
        if (val === '__custom__') {
            const nameZh = (prompt('自訂分類中文名（例：友情客串）') || '').trim();
            if (!nameZh) { ev.target.value = ''; return; }
            const nameEn = (prompt('自訂分類英文名（可空）') || '').trim();
            blocks.push({ role_id: null, name_zh: nameZh, name_en: nameEn, entries: [] });
        } else {
            const roleId = parseInt(val);
            const role = (DATA.credit_roles_available || []).find(r => r.id === roleId);
            if (!role) { ev.target.value = ''; return; }
            blocks.push({ role_id: role.id, name_zh: role.name_zh, name_en: role.name_en || '', entries: [] });
        }
        ev.target.value = '';
        await _saveBlocks(blocks);
    });

    // 套用模板：把 template 的 roles 中尚未在 credits 的逐一加入
    document.getElementById('sel-apply-tpl')?.addEventListener('change', async (ev) => {
        const val = ev.target.value;
        if (!val) return;
        const tplId = parseInt(val);
        const tpl = (DATA.credit_templates_available || []).find(t => t.id === tplId);
        ev.target.value = '';
        if (!tpl) return;
        const blocks = _currentBlocks();
        const used = new Set(blocks.map(b => b.role_id).filter(x => x != null));
        let added = 0;
        for (const role of (tpl.roles || [])) {
            if (used.has(role.id)) continue;
            blocks.push({ role_id: role.id, name_zh: role.name_zh, name_en: role.name_en || '', entries: [] });
            used.add(role.id);
            added++;
        }
        if (!added) { _toast('模板職位皆已存在'); return; }
        await _saveBlocks(blocks);
    });

    // 從派工帶入：把此專案的派工人員（crm_project_staff）灌成 credits 區塊。
    // window.confirm：確定＝附加（保留現有 credits）；取消＝取代（清空後重建）。
    document.getElementById('btn-carry-in-credits')?.addEventListener('click', async () => {
        const append = confirm('從派工帶入 credits\n\n確定＝附加到現有 credits\n取消＝取代（清空現有後重建）');
        const mode = append ? 'append' : 'replace';
        const btn = document.getElementById('btn-carry-in-credits');
        if (btn) { btn.disabled = true; btn.textContent = '帶入中…'; }
        try {
            const res = await fetch(API + '/carry_in', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode }),
            });
            if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || '帶入失敗'); }
            await _reload();
            _toast('已從派工帶入 credits');
        } catch (e) {
            alert(e.message);
            if (btn) { btn.disabled = false; btn.textContent = '從派工帶入'; }
        }
    });

    // 刪除整個 block
    document.querySelectorAll('[data-action="del-block"]').forEach(btn => {
        btn.addEventListener('click', async () => {
            if (!confirm('刪除這個職位區塊？')) return;
            const bIdx = parseInt(btn.dataset.bidx);
            const blocks = _currentBlocks();
            blocks.splice(bIdx, 1);
            await _saveBlocks(blocks);
        });
    });

    document.querySelectorAll('[data-action="add-entry"]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const bIdx = parseInt(btn.dataset.bidx);
            const blocks = _currentBlocks();
            if (!blocks[bIdx]) return;
            (blocks[bIdx].entries = blocks[bIdx].entries || []).push({ duty: '', name: '', resume_url: '' });
            await _saveBlocks(blocks);
        });
    });

    document.querySelectorAll('[data-action="del-entry"]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const bIdx = parseInt(btn.dataset.bidx);
            const eIdx = parseInt(btn.dataset.eidx);
            const blocks = _currentBlocks();
            if (!blocks[bIdx] || !blocks[bIdx].entries) return;
            blocks[bIdx].entries.splice(eIdx, 1);
            await _saveBlocks(blocks);
        });
    });

    // 編輯 duty / name — blur 時 save
    // name 改變且原本掛 staff_id → 自動清連結（避免「掛 staff #17 但 name 變成別人」的髒資料）
    const _onEntryFieldBlur = async (input, field) => {
        const bIdx = parseInt(input.dataset.bidx);
        const eIdx = parseInt(input.dataset.eidx);
        const blocks = _currentBlocks();
        const entry = blocks[bIdx]?.entries?.[eIdx];
        if (!entry) return;
        const newVal = input.value.trim();
        if ((entry[field] || '') === newVal) return;  // 沒變不存
        entry[field] = newVal;
        if (field === 'name' && entry.staff_id) {
            entry.staff_id = null;
            entry.resume_url = '';
            _toast('已解除人員連結，姓名保留為純文字');
        }
        await _saveBlocks(blocks);
    };
    document.querySelectorAll('.credit-duty').forEach(inp => {
        inp.addEventListener('blur', () => _onEntryFieldBlur(inp, 'duty'));
    });

    // === Staff combobox ===
    const _closeStaffDropdown = (input) => {
        const cell = input.closest('.credit-name-cell');
        if (!cell) return;
        const dd = cell.querySelector('.credit-name-dropdown');
        if (dd) dd.remove();
    };

    const _selectStaff = async (input, s) => {
        const bIdx = parseInt(input.dataset.bidx);
        const eIdx = parseInt(input.dataset.eidx);
        const blocks = _currentBlocks();
        if (!blocks[bIdx]?.entries?.[eIdx]) return;
        blocks[bIdx].entries[eIdx] = {
            ...blocks[bIdx].entries[eIdx],
            name: s.name,
            staff_id: s.id,
            resume_url: s.resume_visible ? `/resume.html?id=${s.id}` : '',
        };
        _closeStaffDropdown(input);
        await _saveBlocks(blocks);
    };

    const _openQuickAddModal = (prefilledName, targetInput) => {
        const m = document.getElementById('staff-quick-add-modal');
        document.getElementById('qa-name').value = prefilledName || '';

        const sel = document.getElementById('qa-role');
        sel.innerHTML = '<option value="">— 不指定 —</option>' +
            (DATA.credit_roles_available || []).map(r =>
                `<option value="${_esc(r.name_zh)}">${_esc(r.name_zh)}${r.name_en ? ' / ' + _esc(r.name_en) : ''}</option>`
            ).join('');
        sel.value = '';

        m.classList.add('show');
        document.getElementById('qa-name').focus();

        document.getElementById('qa-cancel').onclick = () => { m.classList.remove('show'); };
        document.getElementById('qa-submit').onclick = async () => {
            const name = document.getElementById('qa-name').value.trim();
            const role = document.getElementById('qa-role').value;
            if (!name) { alert('姓名必填'); return; }
            try {
                const newStaff = await _quickAddStaff(name, role);
                m.classList.remove('show');
                await _selectStaff(targetInput, newStaff);
            } catch (e) {
                alert(e.message);
            }
        };
    };

    const _renderStaffDropdown = (input, items, q) => {
        const cell = input.closest('.credit-name-cell');
        if (!cell) return;
        const old = cell.querySelector('.credit-name-dropdown');
        if (old) old.remove();

        const itemsHtml = (items || []).map(s => {
            const avatar = s.photo_url
                ? `<img src="${_esc(s.photo_url)}" style="width:24px;height:24px;border-radius:50%;object-fit:cover;">`
                : `<div style="width:24px;height:24px;border-radius:50%;background:var(--bg3);display:flex;align-items:center;justify-content:center;color:var(--text2);font-size:11px;">${_esc((s.name || '').charAt(0))}</div>`;
            const nameColor = s.resume_visible ? 'var(--success)' : 'var(--text)';
            const dot = `<span class="sc-staff-dot" style="background:${s.resume_visible ? 'var(--success)' : 'var(--border2)'};"></span>`;
            // 沒有金額檢視權時後端把 daily_rate 的鍵刪掉（core/money.py），
            // 原本的 `|| 0` 會印成「日費 0」＝謊報。這頁還會用 token 給外部人
            // 開，更不該自己補一個數字上去 —— 沒有就整段不畫。
            const rate = s.daily_rate == null ? '' : ` · 日費 ${s.daily_rate}`;
            return `<div class="credit-staff-item" data-staff-id="${_esc(s.id)}"
                         style="display:flex;align-items:center;gap:10px;padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--border);"
                         onmouseover="this.style.background='var(--bg3)'"
                         onmouseout="this.style.background=''">
                    ${avatar}
                    <div style="flex:1;min-width:0;">
                        <div style="color:${nameColor};font-size:13px;font-weight:500;">
                            ${dot} ${_esc(s.name)}
                        </div>
                        <div style="color:var(--text3);font-size:11px;">
                            ${_esc(s.role || '—')}${rate}
                        </div>
                    </div>
                </div>`;
        }).join('');

        const hasQuery = !!(q && q.length > 0);
        const createHtml = hasQuery
            ? `<div class="credit-staff-create" data-name="${_esc(q)}"
                    style="display:flex;align-items:center;gap:10px;padding:8px 12px;cursor:pointer;color:var(--accent);background:var(--bg3);"
                    onmouseover="this.style.opacity='0.8'"
                    onmouseout="this.style.opacity=''">
                    + 建立新人員「${_esc(q)}」
                </div>`
            : '';

        const emptyHtml = (!items || items.length === 0) && !hasQuery
            ? `<div style="padding:12px;color:var(--text3);font-size:12px;text-align:center;">無符合人員</div>`
            : '';

        const dd = document.createElement('div');
        dd.className = 'credit-name-dropdown';
        dd.style.cssText = 'position:absolute;top:100%;left:0;right:0;z-index:100;background:var(--bg2);border:1px solid var(--border2);border-radius:4px;margin-top:2px;max-height:280px;overflow-y:auto;box-shadow:0 4px 12px rgba(0,0,0,0.4);';
        dd.innerHTML = itemsHtml + emptyHtml + createHtml;
        cell.appendChild(dd);

        // bind item click（用 mousedown 搶在 input blur 前）
        dd.querySelectorAll('.credit-staff-item').forEach(it => {
            it.addEventListener('mousedown', (e) => {
                e.preventDefault();
                const sid = it.dataset.staffId;
                const picked = (items || []).find(x => String(x.id) === String(sid));
                if (picked) _selectStaff(input, picked);
            });
        });
        const createEl = dd.querySelector('.credit-staff-create');
        if (createEl) {
            createEl.addEventListener('mousedown', (e) => {
                e.preventDefault();
                _closeStaffDropdown(input);
                _openQuickAddModal(createEl.dataset.name || '', input);
            });
        }
    };

    // name input：focus / input / blur
    // _searchSeq 防 race — 慢網路下舊 fetch 後到別覆蓋新 dropdown
    let _searchTimer = null;
    let _searchSeq = 0;
    document.querySelectorAll('.credit-name-input').forEach(inp => {
        inp.addEventListener('focus', async () => {
            const mySeq = ++_searchSeq;
            try {
                const items = await _searchStaff('');
                if (mySeq !== _searchSeq) return;
                _renderStaffDropdown(inp, items, '');
            } catch (_) {}
        });
        inp.addEventListener('input', () => {
            if (_searchTimer) clearTimeout(_searchTimer);
            const q = inp.value.trim();
            const mySeq = ++_searchSeq;
            _searchTimer = setTimeout(async () => {
                try {
                    const items = await _searchStaff(q);
                    if (mySeq !== _searchSeq) return;
                    _renderStaffDropdown(inp, items, q);
                } catch (_) {}
            }, 200);
        });
        inp.addEventListener('blur', () => {
            // 150ms 延遲讓 dropdown click 先觸發
            setTimeout(() => _closeStaffDropdown(inp), 150);
            // 仍跑既有 name 欄位 save（包含 staff_id 自動清除）
            _onEntryFieldBlur(inp, 'name');
        });
    });

    // 綠點解除連結
    document.querySelectorAll('.credit-link-dot').forEach(dot => {
        dot.addEventListener('click', async (e) => {
            e.stopPropagation();
            if (!confirm('解除此人員連結？姓名會保留為純文字。')) return;
            const bIdx = parseInt(dot.dataset.bidx);
            const eIdx = parseInt(dot.dataset.eidx);
            const blocks = _currentBlocks();
            const entry = blocks[bIdx]?.entries?.[eIdx];
            if (!entry) return;
            entry.staff_id = null;
            entry.resume_url = '';
            await _saveBlocks(blocks);
        });
    });

    // Block 拖曳排序（HTML5 native drag）
    let _dragBIdx = null;
    document.querySelectorAll('.credit-block').forEach(el => {
        el.addEventListener('dragstart', (e) => {
            _dragBIdx = parseInt(el.dataset.bidx);
            if (e.dataTransfer) {
                e.dataTransfer.effectAllowed = 'move';
                try { e.dataTransfer.setData('text/plain', String(_dragBIdx)); } catch {}
            }
            el.style.opacity = '0.4';
        });
        el.addEventListener('dragend', () => { el.style.opacity = ''; });
        el.addEventListener('dragover', (e) => {
            e.preventDefault();
            if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
        });
        el.addEventListener('drop', async (e) => {
            e.preventDefault();
            e.stopPropagation();
            const targetIdx = parseInt(el.dataset.bidx);
            if (_dragBIdx == null || _dragBIdx === targetIdx) { _dragBIdx = null; return; }
            const blocks = _currentBlocks();
            const moved = blocks.splice(_dragBIdx, 1)[0];
            // splice 後 target 位置可能左移：放回 target 之前。
            const insertAt = targetIdx > _dragBIdx ? targetIdx - 1 : targetIdx;
            blocks.splice(insertAt, 0, moved);
            _dragBIdx = null;
            await _saveBlocks(blocks);
        });
    });

    // Website category / tag toggle — in-memory; master save 一次提交 category_ids
    document.querySelectorAll('[data-action="toggle-cat"]').forEach(el => {
        el.addEventListener('click', () => {
            const id = parseInt(el.dataset.id);
            const cur = new Set(DATA.selected_category_ids || []);
            const on = !cur.has(id);
            if (on) cur.add(id); else cur.delete(id);
            DATA.selected_category_ids = [...cur];
            _catsDraft = [...cur];
            _setChipState(el, on);
            _markDirtyEl('data:cats'); setDirty('_cats');
        });
    });

    // 分類小測驗（onclick 賦值 = 可重複 bind 不疊加）
    const quizBtn = document.getElementById('btn-cat-quiz');
    if (quizBtn) quizBtn.onclick = _quizOpen;
    const quizModal = document.getElementById('cat-quiz-modal');
    if (quizModal) quizModal.onclick = (e) => { if (e.target === quizModal) _quizClose(); };
}

/** 分類/標籤 chip 的視覺狀態（點擊 toggle 與小測驗套用共用） */
function _setChipState(el, on) {
    el.classList.toggle('tag-on', on);
    const name = el.textContent.replace(/^✓\s*/, '');
    el.textContent = on ? `✓ ${name}` : name;
}
