/**
 * seo.js — SEO / AI SEO 管理（真實資料 CRUD）
 *
 * 7 個 card：
 *   1. 索引開關 + 基本 Meta（綁 settings：seo.indexable / default_title / default_description / og_image / ai_allow）
 *   2. Quick Facts（CRUD website_quick_facts）
 *   3. FAQ（CRUD website_faqs）
 *   4. Testimonials（CRUD website_testimonials）
 *   5. 服務 SEO 描述（連到 services Tab）
 *   6. llms.txt 編輯器（綁 settings：seo.llms_txt_body）
 *   7. SEO 健康分數（讀真實狀態評分）
 *
 * 任一寫入後端會 mark_dirty → 60s debounce 觸發 Astro rebuild → 對外網站更新。
 */
import { websiteFetch, esc, toastOk, toastErr, renderLoadError, readRowPatch, emptyRow } from '../website-utils.js';

const HEALTH_RULES = [
    { id: 'noindex',     label: '允許 Google/Bing 索引（seo.indexable=true）',
      check: s => s.settings['seo.indexable'] === true,
      help: '到「1️⃣ 索引開關」打開 Google/Bing 索引（上線前最後再開）' },
    { id: 'desc_len',    label: 'Meta description 30-200 字',
      check: s => { const d = s.settings['seo.default_description'] || ''; return d.length >= 30 && d.length <= 200; },
      help: '在「1️⃣ 基本 Meta」填入適當長度描述（建議 60-155 字）' },
    { id: 'og_image',    label: 'OG Image 已設定（社群分享預覽）',
      check: s => !!(s.settings['seo.og_image'] || '').trim(),
      help: '在「1️⃣ 基本 Meta」上傳或填入 OG Image URL' },
    { id: 'faq_visible', label: 'FAQ 已輸出 FAQPage JSON-LD',
      check: s => s.faqs.some(f => f.visible),
      help: '在「3️⃣ FAQ」新增至少 1 題並設為可見' },
    { id: 'review',      label: 'Testimonials 已輸出 Review schema',
      check: s => s.testimonials.some(t => t.visible),
      help: '在「4️⃣ Testimonials」新增至少 1 則並設為可見' },
    { id: 'qfacts',      label: 'Quick Facts 已建立（AI 條列事實）',
      check: s => s.quickFacts.some(f => f.visible),
      help: '在「2️⃣ Quick Facts」新增成立年/地點/規模等基本事實' },
    { id: 'awards',      label: '獎項紀錄已建立（站級榮譽）',
      check: s => s.awards.some(a => a.visible),
      help: '在「🏆 獎項紀錄」子視圖新增至少 1 筆並設為可見，會顯示在 /portfolio 頁面頂部' },
    { id: 'sitemap',     label: 'sitemap.xml 已自動生成',
      check: () => true,    // @astrojs/sitemap 已裝
      help: '' },
    { id: 'llms_txt',    label: 'llms.txt 已發布',
      check: s => !!(s.settings['seo.llms_txt_body'] || '').trim(),
      help: '在「6️⃣ llms.txt」填入內容並儲存（範本可一鍵套用）' },
    { id: 'ai_allow',    label: 'robots.txt 允許 AI 爬蟲（GPTBot/ClaudeBot/Perplexity）',
      check: s => s.settings['seo.ai_allow'] === true,
      help: '在「1️⃣ 基本 Meta」打開「允許 AI 爬蟲」開關' },
    { id: 'hreflang',    label: 'hreflang 中英對照已設定',
      check: () => false,   // 待 Stage 4 i18n 路由
      help: '待 i18n 多語路由完成後自動 pass' },
];

let _state = { settings: {}, faqs: [], testimonials: [], quickFacts: [], awards: [], aiRunner: null };
let _container = null;

export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    _container = container;
    container.innerHTML = '<h2>🔍 SEO / AI SEO 管理</h2><div style="color:#888;padding:20px;">載入中…</div>';

    try {
        const [settings, faqs, testi, qfacts, awards, runner] = await Promise.all([
            websiteFetch('/api/website/admin/settings'),
            websiteFetch('/api/website/admin/faqs'),
            websiteFetch('/api/website/admin/testimonials'),
            websiteFetch('/api/website/admin/quick_facts'),
            websiteFetch('/api/website/admin/awards').catch(() => ({ items: [] })),
            websiteFetch('/api/website/admin/seo/runner/settings').catch(() => null),
        ]);
        if (!isCurrent()) return;
        _state.settings = settings?.settings || {};
        _state.faqs = faqs?.items || [];
        _state.testimonials = testi?.items || [];
        _state.quickFacts = qfacts?.items || [];
        _state.awards = awards?.items || [];
        _state.aiRunner = runner;
    } catch (e) {
        if (!isCurrent()) return;
        // 404 = 新 admin SEO endpoint 在 NAS website-api 不存在 → 大概率部署落後
        const hint = e.status === 404
            ? 'NAS website-api 可能跑舊版（沒 admin_seo router）。請在 master 跑 /publish 同步後端到 NAS。'
            : '';
        renderLoadError(container, '🔍 SEO / AI SEO 管理', e, hint);
        return;
    }

    _renderAll();
}

function _renderAll() {
    _container.innerHTML = `
        <h2>🔍 SEO / AI SEO 管理</h2>
        <div style="display:grid;grid-template-columns:1fr;gap:16px;max-width:1100px;">
            ${_cardAiRunner()}
            ${_cardMeta()}
            ${_cardQuickFacts()}
            ${_cardFAQ()}
            ${_cardTestimonials()}
            ${_cardServicesHint()}
            ${_cardLlmsTxt()}
            ${_cardHealth()}
        </div>
    `;
    _bindDescCounter();
    _bindAiRunner();
}

// ===== 0. AI SEO Runner 排程 =====
function _cardAiRunner() {
    const r = _state.aiRunner;
    if (r === null) {
        // endpoint 不存在（NAS website-api 跑舊版）
        return `<div class="card" style="border-left:3px solid #6b7280;">
            <h3 style="color:#fff;margin:0 0 8px;font-size:15px;">🤖 AI SEO 排程</h3>
            <div style="color:#888;font-size:12px;">NAS website-api 尚未支援此 endpoint，請在 master 跑 /publish 同步後端。</div>
        </div>`;
    }
    const enabled = !!r.enabled;
    const cron = r.cron || '0 3 * * *';
    const batch = r.batch_size || 10;
    const lastAt = r.last_run_at
        ? new Date(r.last_run_at * 1000).toLocaleString('zh-TW')
        : '從未執行';
    const summary = r.last_run_summary || {};
    const sumText = summary && (summary.processed != null)
        ? `處理 ${summary.processed} 筆 / 失敗 ${summary.errors || 0} 筆`
        : '';
    return `<div class="card" style="border-left:3px solid #c8a45c;">
        <h3 style="color:#fff;margin:0 0 12px;font-size:15px;">🤖 AI SEO 排程
            <span style="color:#888;font-size:11px;font-weight:400;margin-left:8px;">
                透過 claude --print 自動補作品 SEO 內容（吃 Max 訂閱額度）
            </span>
        </h3>

        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-bottom:12px;">
            <div>
                <label style="color:#888;font-size:11px;display:block;margin-bottom:6px;">啟用排程</label>
                <label style="display:flex;align-items:center;gap:8px;cursor:pointer;color:#ddd;">
                    <input type="checkbox" id="ai-runner-enabled" ${enabled ? 'checked' : ''} style="width:16px;height:16px;" />
                    <span>${enabled ? '✓ 已啟用' : '⚠ 未啟用（手動觸發仍可用）'}</span>
                </label>
            </div>
            <div>
                <label style="color:#888;font-size:11px;display:block;margin-bottom:6px;">Cron（主機本地時區）</label>
                <input id="ai-runner-cron" type="text" value="${esc(cron)}"
                    placeholder="0 3 * * *" style="width:100%;font-family:ui-monospace,monospace;font-size:12px;" />
                <div style="color:#666;font-size:10px;margin-top:2px;">預設「每日凌晨 3 點」= <code>0 3 * * *</code></div>
            </div>
            <div>
                <label style="color:#888;font-size:11px;display:block;margin-bottom:6px;">單次最多處理</label>
                <input id="ai-runner-batch" type="number" min="1" max="50" value="${batch}" style="width:100%;" />
                <div style="color:#666;font-size:10px;margin-top:2px;">超過此數的待處理作品下次再跑</div>
            </div>
        </div>

        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px;">
            <button id="ai-runner-save" class="btn btn-sm btn-primary">💾 儲存排程設定</button>
            <button id="ai-runner-preview" class="btn btn-sm">👁 預覽（dry-run）</button>
            <button id="ai-runner-now" class="btn btn-sm">▶ 立即執行</button>
        </div>

        <div style="color:#888;font-size:11px;padding-top:8px;border-top:1px solid #2a2a2a;">
            上次執行：<span style="color:#ddd;">${esc(lastAt)}</span>
            ${sumText ? `<span style="color:#666;">·</span> <span style="color:#ddd;">${esc(sumText)}</span>` : ''}
        </div>
    </div>`;
}

function _bindAiRunner() {
    const enabledEl = document.getElementById('ai-runner-enabled');
    const cronEl = document.getElementById('ai-runner-cron');
    const batchEl = document.getElementById('ai-runner-batch');
    if (!enabledEl) return; // endpoint 不可用時 _cardAiRunner 是 fallback、無這些 input

    document.getElementById('ai-runner-save')?.addEventListener('click', async () => {
        try {
            await websiteFetch('/api/website/admin/seo/runner/settings', {
                method: 'PUT',
                body: {
                    enabled: enabledEl.checked,
                    cron: cronEl.value.trim() || '0 3 * * *',
                    batch_size: Math.max(1, Math.min(50, Number(batchEl.value) || 10)),
                },
            });
            toastOk('排程設定已儲存');
        } catch (e) { toastErr('儲存失敗：' + (e.message || e)); }
    });

    document.getElementById('ai-runner-preview')?.addEventListener('click', async () => {
        try {
            const r = await websiteFetch('/api/website/admin/seo/runner/run?dry_run=1', { method: 'POST' });
            const n = (r.works || []).length;
            toastOk(`Dry-run：將處理 ${n} 筆作品（不實際送 LLM）`);
        } catch (e) { toastErr('預覽失敗：' + (e.message || e)); }
    });

    document.getElementById('ai-runner-now')?.addEventListener('click', async () => {
        const btn = document.getElementById('ai-runner-now');
        const orig = btn.textContent;
        btn.disabled = true;
        btn.textContent = '⏳ 執行中…（每筆約 30-60 秒）';
        try {
            const r = await websiteFetch('/api/website/admin/seo/runner/run', { method: 'POST' });
            if (r.status === 'busy') {
                toastErr('已有 AI runner 在跑');
            } else {
                const failed = (r.works || []).find(w => w.status !== 'ok' && w.status !== 'would_process');
                const detail = failed ? `（首件錯誤：${failed.detail || failed.status}）` : '';
                if ((r.errors || 0) > 0) {
                    toastErr(`處理 ${r.processed} 筆 / 失敗 ${r.errors} 筆 ${detail}`);
                } else {
                    toastOk(`完成：處理 ${r.processed} 筆`);
                }
                // 重 load 顯示 last_run
                _state.aiRunner = await websiteFetch('/api/website/admin/seo/runner/settings');
                _renderAll();
            }
        } catch (e) {
            toastErr('執行失敗：' + (e.message || e));
        } finally {
            btn.disabled = false;
            btn.textContent = orig;
        }
    });
}


// ===== 1. Meta + 索引開關 =====
function _cardMeta() {
    const s = _state.settings;
    const indexable = s['seo.indexable'] === true;
    const aiAllow = s['seo.ai_allow'] === true;
    const title = s['seo.default_title'] || '';
    const desc = s['seo.default_description'] || '';
    const ogImg = s['seo.og_image'] || '';
    const descLen = desc.length;
    const descColor = descLen >= 30 && descLen <= 200 ? '#4ade80' : '#f59e0b';

    return `<div class="card" style="border-left:3px solid #10b981;">
        <h3 style="color:#fff;margin:0 0 12px;font-size:15px;">1️⃣ 索引開關 + 基本 Meta</h3>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:12px;">
            <div>
                <label style="color:#888;font-size:11px;display:block;margin-bottom:6px;">搜尋引擎索引</label>
                <label style="display:flex;align-items:center;gap:8px;cursor:pointer;color:#ddd;">
                    <input type="checkbox" id="seo-indexable" ${indexable ? 'checked' : ''} style="width:16px;height:16px;" />
                    <span>允許 Google/Bing 索引</span>
                </label>
                <div style="color:${indexable ? '#4ade80' : '#f59e0b'};font-size:11px;margin-top:4px;">
                    ${indexable ? '✓ 對外允許索引' : '⚠ 目前 noindex（開發/staging 期）'}
                </div>
            </div>
            <div>
                <label style="color:#888;font-size:11px;display:block;margin-bottom:6px;">AI 爬蟲</label>
                <label style="display:flex;align-items:center;gap:8px;cursor:pointer;color:#ddd;">
                    <input type="checkbox" id="seo-ai-allow" ${aiAllow ? 'checked' : ''} style="width:16px;height:16px;" />
                    <span>允許 GPTBot / ClaudeBot / Perplexity / Google-Extended</span>
                </label>
            </div>
        </div>

        <div style="margin-bottom:12px;">
            <label style="color:#888;font-size:11px;display:block;margin-bottom:6px;">OG Image URL（社群分享預覽圖）</label>
            <input id="seo-og-image" type="text" value="${esc(ogImg)}" placeholder="https://..." style="width:100%;" />
        </div>

        <div style="margin-bottom:12px;">
            <label style="color:#888;font-size:11px;display:block;margin-bottom:6px;">預設 Title</label>
            <input id="seo-title" type="text" value="${esc(title)}" style="width:100%;" />
        </div>

        <div>
            <label style="color:#888;font-size:11px;display:flex;justify-content:space-between;margin-bottom:6px;">
                <span>預設 Description（建議 30-200 字）</span>
                <span id="seo-desc-count" style="color:${descColor};">${descLen} 字</span>
            </label>
            <textarea id="seo-desc" rows="3" style="width:100%;">${esc(desc)}</textarea>
        </div>

        <div style="margin-top:12px;">
            <button class="btn" onclick="window._seo.saveMeta()">💾 儲存 Meta 設定</button>
        </div>
    </div>`;
}


// ===== 2. Quick Facts =====
function _cardQuickFacts() {
    const rows = _state.quickFacts.map(f => `
        <tr>
            <td><input data-id="${f.id}" data-field="label_zh" value="${esc(f.label_zh)}" style="width:100%;" /></td>
            <td><input data-id="${f.id}" data-field="value" value="${esc(f.value)}" style="width:100%;" /></td>
            <td><input type="number" data-id="${f.id}" data-field="sort_order" value="${f.sort_order}" style="width:60px;" /></td>
            <td style="text-align:center;"><input type="checkbox" data-id="${f.id}" data-field="visible" ${f.visible ? 'checked' : ''} /></td>
            <td style="text-align:right;">
                <button class="btn btn-sm" onclick="window._seo.saveQF(${f.id})">💾</button>
                <button class="btn btn-sm btn-danger" onclick="window._seo.deleteQF(${f.id})">🗑</button>
            </td>
        </tr>
    `).join('');
    return `<div class="card" style="border-left:3px solid #8b5cf6;">
        <h3 style="color:#fff;margin:0 0 8px;font-size:15px;">2️⃣ Quick Facts
            <span style="color:#888;font-size:11px;font-weight:400;">· ${_state.quickFacts.length} 條 · AI 搜尋最愛撿的條列事實</span>
        </h3>
        <div style="display:grid;grid-template-columns:1fr 2fr 80px auto;gap:8px;margin-bottom:8px;align-items:end;">
            <div><label style="color:#888;font-size:11px;">標籤（如「成立年份」）</label>
                <input id="qf-new-label" type="text" style="width:100%;" /></div>
            <div><label style="color:#888;font-size:11px;">內容（如「2014」）</label>
                <input id="qf-new-value" type="text" style="width:100%;" /></div>
            <div><label style="color:#888;font-size:11px;">排序</label>
                <input id="qf-new-sort" type="number" value="0" style="width:100%;" /></div>
            <button class="btn" onclick="window._seo.createQF()">+ 新增</button>
        </div>
        <table>
            <thead><tr><th style="width:30%;">標籤</th><th>內容</th><th style="width:60px;">排序</th><th style="width:60px;">可見</th><th></th></tr></thead>
            <tbody>${rows || emptyRow(5, '尚無 Quick Fact，新增上方第一條')}</tbody>
        </table>
    </div>`;
}


// ===== 3. FAQ =====
function _cardFAQ() {
    const visibleCount = _state.faqs.filter(f => f.visible).length;
    const rows = _state.faqs.map(f => `
        <tr>
            <td style="color:#666;font-size:11px;">#${f.id}</td>
            <td><input data-id="${f.id}" data-field="question_zh" value="${esc(f.question_zh)}" style="width:100%;" /></td>
            <td><textarea data-id="${f.id}" data-field="answer_zh" rows="2" style="width:100%;font-size:12px;">${esc(f.answer_zh)}</textarea></td>
            <td><input type="number" data-id="${f.id}" data-field="sort_order" value="${f.sort_order}" style="width:60px;" /></td>
            <td style="text-align:center;"><input type="checkbox" data-id="${f.id}" data-field="visible" ${f.visible ? 'checked' : ''} /></td>
            <td style="text-align:right;white-space:nowrap;">
                <button class="btn btn-sm" onclick="window._seo.saveFAQ(${f.id})">💾</button>
                <button class="btn btn-sm btn-danger" onclick="window._seo.deleteFAQ(${f.id})">🗑</button>
            </td>
        </tr>
    `).join('');
    return `<div class="card" style="border-left:3px solid #3b82f6;">
        <h3 style="color:#fff;margin:0 0 8px;font-size:15px;">3️⃣ FAQ 管理
            <span style="color:#888;font-size:11px;font-weight:400;">· ${visibleCount}/${_state.faqs.length} 顯示中 · 將輸出 FAQPage JSON-LD</span>
        </h3>
        <div style="display:grid;grid-template-columns:1fr 2fr auto;gap:8px;margin-bottom:8px;align-items:end;">
            <div><label style="color:#888;font-size:11px;">問題（中）</label>
                <input id="faq-new-q" type="text" style="width:100%;" placeholder="一支影片大約多久？" /></div>
            <div><label style="color:#888;font-size:11px;">答案（中）</label>
                <textarea id="faq-new-a" rows="1" style="width:100%;" placeholder="商業廣告 4-6 週、紀錄片 8-12 週..."></textarea></div>
            <button class="btn" onclick="window._seo.createFAQ()">+ 新增</button>
        </div>
        <table>
            <thead><tr><th style="width:50px;">#</th><th style="width:25%;">問題</th><th>答案</th><th style="width:60px;">排序</th><th style="width:60px;">顯示</th><th></th></tr></thead>
            <tbody>${rows || emptyRow(6, '尚無 FAQ')}</tbody>
        </table>
    </div>`;
}


// ===== 4. Testimonials =====
function _cardTestimonials() {
    const visibleCount = _state.testimonials.filter(t => t.visible).length;
    const avg = _state.testimonials.length
        ? (_state.testimonials.reduce((sum, t) => sum + t.rating, 0) / _state.testimonials.length).toFixed(1)
        : '—';
    const rows = _state.testimonials.map(t => `
        <tr>
            <td><input data-id="${t.id}" data-field="author_zh" value="${esc(t.author_zh)}" style="width:100%;" placeholder="客戶姓名" /></td>
            <td><input data-id="${t.id}" data-field="role_zh" value="${esc(t.role_zh || '')}" style="width:100%;" placeholder="職稱" /></td>
            <td><input data-id="${t.id}" data-field="company" value="${esc(t.company || '')}" style="width:100%;" placeholder="公司" /></td>
            <td><input type="number" data-id="${t.id}" data-field="rating" value="${t.rating}" min="1" max="5" style="width:50px;" /></td>
            <td><textarea data-id="${t.id}" data-field="content_zh" rows="2" style="width:100%;font-size:12px;" placeholder="證言內容...">${esc(t.content_zh || '')}</textarea></td>
            <td style="text-align:center;"><input type="checkbox" data-id="${t.id}" data-field="visible" ${t.visible ? 'checked' : ''} /></td>
            <td style="text-align:right;white-space:nowrap;">
                <button class="btn btn-sm" onclick="window._seo.saveT(${t.id})">💾</button>
                <button class="btn btn-sm btn-danger" onclick="window._seo.deleteT(${t.id})">🗑</button>
            </td>
        </tr>
    `).join('');
    return `<div class="card" style="border-left:3px solid #f59e0b;">
        <h3 style="color:#fff;margin:0 0 8px;font-size:15px;">4️⃣ Testimonials 管理
            <span style="color:#888;font-size:11px;font-weight:400;">· ${visibleCount}/${_state.testimonials.length} 顯示中 · 平均評分 ★${avg}</span>
        </h3>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr 70px 2fr auto;gap:8px;margin-bottom:8px;align-items:end;">
            <input id="t-new-author" type="text" placeholder="客戶姓名" />
            <input id="t-new-role" type="text" placeholder="職稱" />
            <input id="t-new-company" type="text" placeholder="公司" />
            <input id="t-new-rating" type="number" value="5" min="1" max="5" placeholder="評分" />
            <input id="t-new-content" type="text" placeholder="證言內容" />
            <button class="btn" onclick="window._seo.createT()">+ 新增</button>
        </div>
        <table>
            <thead><tr><th>客戶</th><th>職稱</th><th>公司</th><th>評分</th><th>內容</th><th style="width:50px;">顯示</th><th></th></tr></thead>
            <tbody>${rows || emptyRow(7, '尚無證言')}</tbody>
        </table>
    </div>`;
}


// ===== 5. Services hint =====
function _cardServicesHint() {
    return `<div class="card" style="border-left:3px solid #ec4899;">
        <h3 style="color:#fff;margin:0 0 8px;font-size:15px;">5️⃣ 服務項目 SEO 描述</h3>
        <p style="color:#aaa;font-size:13px;line-height:1.7;margin:0 0 10px;">
            服務項目的長文案與關鍵字請到
            <a href="#" onclick="window.websiteSwitchSubview('services');return false;" style="color:#3b82f6;">🧩 服務項目</a>
            Tab 編輯。Stage 2 起會把 services 自動輸出 Service schema。
        </p>
        <button class="btn btn-sm btn-ghost" onclick="window.websiteSwitchSubview('services')">前往服務項目 Tab →</button>
    </div>`;
}


// ===== 6. llms.txt =====
function _cardLlmsTxt() {
    const body = _state.settings['seo.llms_txt_body'] || '';
    return `<div class="card" style="border-left:3px solid #06b6d4;">
        <h3 style="color:#fff;margin:0 0 8px;font-size:15px;">6️⃣ llms.txt 編輯器
            <span style="color:#888;font-size:11px;font-weight:400;">· 2024 AI SEO 標準 · 將儲存至 settings 並由 Astro endpoint serve</span>
        </h3>
        <textarea id="seo-llms-txt" rows="14" style="width:100%;font-family:monospace;font-size:12px;">${esc(body)}</textarea>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px;">
            <span id="seo-llms-count" style="color:#666;font-size:11px;">${body.length} 字元</span>
            <div style="display:flex;gap:8px;">
                <button class="btn btn-sm btn-ghost" onclick="window._seo.llmsTemplate()">📝 套用範本</button>
                <button class="btn btn-sm btn-ghost" onclick="window._seo.llmsPreview()">👁 預覽 /llms.txt</button>
                <button class="btn" onclick="window._seo.saveLlms()">💾 儲存</button>
            </div>
        </div>
    </div>`;
}


// ===== 7. Health =====
function _cardHealth() {
    const results = HEALTH_RULES.map(r => ({ ...r, pass: r.check(_state) }));
    const passed = results.filter(r => r.pass).length;
    const total = results.length;
    const pct = Math.round((passed / total) * 100);
    const color = pct >= 80 ? '#4ade80' : pct >= 50 ? '#f59e0b' : '#f87171';
    const lis = results.map(r => `
        <li style="display:flex;gap:10px;padding:8px 0;border-bottom:1px solid #2a2a2a;">
            <span style="color:${r.pass ? '#4ade80' : '#f87171'};font-size:16px;width:20px;flex-shrink:0;">${r.pass ? '✓' : '✗'}</span>
            <div style="flex:1;">
                <div style="color:#ddd;font-size:13px;">${esc(r.label)}</div>
                ${!r.pass && r.help ? `<div style="color:#888;font-size:11px;margin-top:2px;">→ ${esc(r.help)}</div>` : ''}
            </div>
        </li>
    `).join('');
    return `<div class="card" style="border-left:3px solid ${color};">
        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:12px;">
            <h3 style="color:#fff;margin:0;font-size:15px;">7️⃣ SEO 健康分數</h3>
            <div style="color:${color};font-size:28px;font-weight:700;">${pct}<span style="font-size:14px;">%</span>
                <span style="color:#888;font-size:12px;font-weight:400;margin-left:6px;">${passed}/${total}</span>
            </div>
        </div>
        <ul style="list-style:none;margin:0;padding:0;">${lis}</ul>
        <div style="margin-top:12px;display:flex;gap:8px;">
            <a href="https://search.google.com/test/rich-results" target="_blank" class="btn btn-sm btn-ghost">🔗 Google Rich Results 測試</a>
            <a href="https://pagespeed.web.dev/" target="_blank" class="btn btn-sm btn-ghost">🔗 PageSpeed Insights</a>
        </div>
    </div>`;
}


// ===== 共用：保存設定 =====
async function _saveSettings(patch) {
    const result = await websiteFetch('/api/website/admin/settings', {
        method: 'PUT',
        body: { values: patch },
    });
    Object.assign(_state.settings, patch);
    return result;
}

function _bindDescCounter() {
    const el = document.getElementById('seo-desc');
    const counter = document.getElementById('seo-desc-count');
    if (el && counter) {
        el.addEventListener('input', () => {
            const n = el.value.length;
            counter.textContent = `${n} 字`;
            counter.style.color = (n >= 30 && n <= 200) ? '#4ade80' : '#f59e0b';
        });
    }
    const llms = document.getElementById('seo-llms-txt');
    const llmsCount = document.getElementById('seo-llms-count');
    if (llms && llmsCount) {
        llms.addEventListener('input', () => {
            llmsCount.textContent = `${llms.value.length} 字元`;
        });
    }
}


// ===== window._seo namespace（避免散落 9 個 window._seoXxx 全域函式） =====
// HTML 用 onclick="window._seo.xxx()" 觸發；不用 ES module export 是因為
// HTML 內 inline handler 拿不到 import 進來的 binding。
const _seo = (window._seo = window._seo || {});


// Card 1：Meta 儲存
_seo.saveMeta = async () => {
    try {
        await _saveSettings({
            'seo.indexable': document.getElementById('seo-indexable').checked,
            'seo.ai_allow': document.getElementById('seo-ai-allow').checked,
            'seo.og_image': document.getElementById('seo-og-image').value.trim(),
            'seo.default_title': document.getElementById('seo-title').value.trim(),
            'seo.default_description': document.getElementById('seo-desc').value.trim(),
        });
        toastOk('已儲存 Meta 設定（60 秒後對外網站重 build）');
        _renderAll();
    } catch (e) { toastErr(e.message); }
};


// Card 2：Quick Facts
_seo.createQF = async () => {
    const body = {
        label_zh: document.getElementById('qf-new-label').value.trim(),
        value: document.getElementById('qf-new-value').value.trim(),
        sort_order: Number(document.getElementById('qf-new-sort').value || 0),
        visible: true,
    };
    if (!body.label_zh || !body.value) { toastErr('標籤與內容必填'); return; }
    try {
        const created = await websiteFetch('/api/website/admin/quick_facts', { method: 'POST', body });
        _state.quickFacts.push(created);
        toastOk('已新增');
        _renderAll();
    } catch (e) { toastErr(e.message); }
};

_seo.saveQF = async (id) => {
    try {
        const updated = await websiteFetch(`/api/website/admin/quick_facts/${id}`, {
            method: 'PUT', body: readRowPatch('.card [data-id]', id),
        });
        const idx = _state.quickFacts.findIndex(f => f.id === id);
        if (idx >= 0) _state.quickFacts[idx] = updated;
        toastOk('已更新');
    } catch (e) { toastErr(e.message); }
};

_seo.deleteQF = async (id) => {
    if (!confirm('確定刪除？')) return;
    try {
        await websiteFetch(`/api/website/admin/quick_facts/${id}`, { method: 'DELETE' });
        _state.quickFacts = _state.quickFacts.filter(f => f.id !== id);
        toastOk('已刪除');
        _renderAll();
    } catch (e) { toastErr(e.message); }
};


// Card 3：FAQ
_seo.createFAQ = async () => {
    const body = {
        question_zh: document.getElementById('faq-new-q').value.trim(),
        answer_zh: document.getElementById('faq-new-a').value.trim(),
        sort_order: _state.faqs.length,
        visible: true,
    };
    if (!body.question_zh || !body.answer_zh) { toastErr('問題與答案必填'); return; }
    try {
        const created = await websiteFetch('/api/website/admin/faqs', { method: 'POST', body });
        _state.faqs.push(created);
        toastOk('已新增 FAQ');
        _renderAll();
    } catch (e) { toastErr(e.message); }
};

_seo.saveFAQ = async (id) => {
    try {
        const updated = await websiteFetch(`/api/website/admin/faqs/${id}`, {
            method: 'PUT', body: readRowPatch('.card [data-id]', id),
        });
        const idx = _state.faqs.findIndex(f => f.id === id);
        if (idx >= 0) _state.faqs[idx] = updated;
        toastOk('已更新');
    } catch (e) { toastErr(e.message); }
};

_seo.deleteFAQ = async (id) => {
    if (!confirm('確定刪除此 FAQ？')) return;
    try {
        await websiteFetch(`/api/website/admin/faqs/${id}`, { method: 'DELETE' });
        _state.faqs = _state.faqs.filter(f => f.id !== id);
        toastOk('已刪除');
        _renderAll();
    } catch (e) { toastErr(e.message); }
};


// Card 4：Testimonials
_seo.createT = async () => {
    const body = {
        author_zh: document.getElementById('t-new-author').value.trim(),
        role_zh: document.getElementById('t-new-role').value.trim() || null,
        company: document.getElementById('t-new-company').value.trim() || null,
        rating: Number(document.getElementById('t-new-rating').value || 5),
        content_zh: document.getElementById('t-new-content').value.trim() || null,
        sort_order: _state.testimonials.length,
        visible: true,
    };
    if (!body.author_zh) { toastErr('客戶姓名必填'); return; }
    try {
        const created = await websiteFetch('/api/website/admin/testimonials', { method: 'POST', body });
        _state.testimonials.push(created);
        toastOk('已新增證言');
        _renderAll();
    } catch (e) { toastErr(e.message); }
};

_seo.saveT = async (id) => {
    try {
        const updated = await websiteFetch(`/api/website/admin/testimonials/${id}`, {
            method: 'PUT', body: readRowPatch('.card [data-id]', id),
        });
        const idx = _state.testimonials.findIndex(t => t.id === id);
        if (idx >= 0) _state.testimonials[idx] = updated;
        toastOk('已更新');
    } catch (e) { toastErr(e.message); }
};

_seo.deleteT = async (id) => {
    if (!confirm('確定刪除此證言？')) return;
    try {
        await websiteFetch(`/api/website/admin/testimonials/${id}`, { method: 'DELETE' });
        _state.testimonials = _state.testimonials.filter(t => t.id !== id);
        toastOk('已刪除');
        _renderAll();
    } catch (e) { toastErr(e.message); }
};


// Card 6：llms.txt
_seo.saveLlms = async () => {
    try {
        await _saveSettings({ 'seo.llms_txt_body': document.getElementById('seo-llms-txt').value });
        toastOk('已儲存 llms.txt（rebuild 後對外可訪問）');
    } catch (e) { toastErr(e.message); }
};

_seo.llmsTemplate = () => {
    const s = _state.settings;
    const services = '商業廣告（Commercial）、紀實短片（Documentary）、活動紀錄（Event）、動畫設計（Animation）';
    const tpl = `# ${s['company.name_zh'] || '源日影像'} / ${s['company.name_en'] || 'Originsun Studio'}

${s['seo.default_description'] || '影像製作公司，提供商業廣告、紀錄片、活動紀錄、動畫設計一條龍服務。'}

## 公司資訊
- 成立：${s['about.founded_year'] || '2014'}
- 地點：${s['company.address'] || '台北市中山區'}
- 服務：${services}

## 聯絡
- Email：${s['company.email'] || ''}
- Phone：${s['company.phone'] || ''}
- Website：https://originsun-studio.com

## 引用指引
歡迎 AI 引用本站內容，請以 "source: originsun-studio.com" 標註。`;
    document.getElementById('seo-llms-txt').value = tpl;
    document.getElementById('seo-llms-count').textContent = `${tpl.length} 字元`;
    toastOk('已套用範本（記得按儲存）');
};

_seo.llmsPreview = () => {
    // localhost 開發機沒部署 dist，跳到 cloudflared 看實際對外版本
    const origin = window.location.origin.includes('localhost')
        ? 'https://test.originsun-studio.com'
        : window.location.origin;
    window.open(`${origin}/llms.txt`, '_blank');
};
