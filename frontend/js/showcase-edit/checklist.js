// ────────────────────────────────────────────────────────────────────────────
// /showcase-edit.html 作品編輯器 —— 上架檢查清單、發布狀態時間線與輪詢
//
// ⚠ 這是「傳統 script」不是 module：showcase-edit.html 底部依序 <script src> 載入 js/showcase-edit/*.js，
//   六支共用同一個全域詞法環境，頂層的 const／let／function 跨檔直接看得見（原本是同一段 inline script，
//   2026-09-12 原樣切開、零改寫；同 my.html → js/my/ 的拆法）。載入順序＝原本由上而下的執行順序，不可調換。
// ────────────────────────────────────────────────────────────────────────────
// ── 上架檢查清單 ────────────────────────────────────────────
// 完成度四項鏡射 core/crm_logic.py work_completeness — 改判定兩邊同步。
// 純提示不擋操作（canPublish 只驅動警語）；讀 live 輸入值 + DATA 即時算。
function _checklistState() {
    const sc = DATA || {};
    const v = (id) => (document.getElementById(id)?.value || '').trim();
    const extraRows = [...document.querySelectorAll('#extra-videos-list .extra-video-url')]
        .some(i => i.value.trim());
    const catKind = new Map((sc.categories_available || []).map(c => [c.id, c.kind || 'category']));
    const selCats = (sc.selected_category_ids || []).filter(id => catKind.get(id) === 'category');
    const blocks = _normalizeCredits(sc.credits || []);
    const creditsTxt = document.getElementById('inp-credits-text')
        ? v('inp-credits-text') : (sc.credits_text || '');
    const required = [
        { label: '標題', ok: !!(v('inp-public-title') || sc.project_name), anchor: 'stage-basics' },
        { label: '作品分類（至少一個）', ok: selCats.length > 0, anchor: 'sec-website-cats' },
    ];
    const completeness = [
        { label: '影片', ok: !!(v('inp-video') || sc.youtube_id || extraRows), anchor: 'sec-video' },
        { label: '圖片', ok: !!((sc.gallery || []).length || sc.public_featured_image || sc.cover_url), anchor: 'sec-gallery' },
        { label: '描述', ok: !!(document.getElementById('inp-desc') ? v('inp-desc') : (sc.description || '')), anchor: 'sec-desc' },
        { label: 'Credits', ok: !!(blocks.length || creditsTxt), anchor: 'stage-credits' },
    ];
    const bonus = [
        { label: 'SEO（覆寫或 AI 生成）', ok: !!(v('inp-seo-title') || v('inp-seo-desc') || (sc.seo && sc.seo.narrative_long)), anchor: 'stage-seo' },
        { label: '精選圖', ok: !!sc.public_featured_image, anchor: 'sec-featured' },
        { label: '系列歸屬（選填）', ok: !!(document.getElementById('inp-series-id')?.value), anchor: 'sec-website-series' },
    ];
    return { required, completeness, bonus, canPublish: required.every(r => r.ok) };
}

function _jumpTo(anchor) {
    const el = document.getElementById(anchor);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    el.classList.add('sc-highlight');
    setTimeout(() => el.classList.remove('sc-highlight'), 1500);
}

function _updatePublishWarn(st) {
    const warn = document.getElementById('sc-publish-warn');
    if (!warn) return;
    st = st || _checklistState();
    const pub = document.getElementById('inp-published')?.checked;
    if (pub && !st.canPublish) {
        const missing = st.required.filter(r => !r.ok).map(r => r.label).join('、');
        warn.textContent = `已勾「公開」但必備項目未完成：${missing} — 仍可儲存，建議補齊後再發布`;
        warn.classList.add('show');
    } else {
        warn.classList.remove('show');
    }
}

function _updateChecklist() {
    const box = document.getElementById('sc-checklist');
    if (!box || !EDITABLE) return;
    const st = _checklistState();
    const saved = localStorage.getItem('sc-checklist-open');
    const open = saved != null ? saved === '1' : !(EMBED || window.innerWidth < 1100);
    const row = (i) => `<div class="sc-check-row" data-anchor="${i.anchor}">
        <span class="${i.ok ? 'ok' : 'no'}">${i.ok ? '✓' : '○'}</span><span>${i.label}</span></div>`;
    const group = (t, items) => `<div class="sc-check-group">${t}</div>` + items.map(row).join('');
    const okN = [...st.required, ...st.completeness].filter(i => i.ok).length;
    const total = st.required.length + st.completeness.length;
    box.innerHTML = `<div class="sc-check-card">
        <div class="sc-check-head" id="sc-check-toggle"><span>上架檢查清單</span>
            <span style="color:var(--text3);font-weight:400;">${okN}/${total} ${open ? '▾' : '▸'}</span></div>
        <div class="sc-check-body" style="display:${open ? '' : 'none'};">
            ${group('必備', st.required)}
            ${group('內容完成度', st.completeness)}
            ${group('加分', st.bonus)}
            <div class="sc-check-group">AI 捷徑</div>
            <div class="sc-check-links">
                <button class="btn btn-sm" type="button" data-jump="sec-desc">AI 描述</button>
                <button class="btn btn-sm" type="button" data-quiz="1">分類建議</button>
                <button class="btn btn-sm" type="button" data-jump="sec-seo-ai">AI SEO</button>
            </div>
        </div></div>`;
    box.querySelectorAll('.sc-check-row').forEach(r => { r.onclick = () => _jumpTo(r.dataset.anchor); });
    box.querySelectorAll('[data-jump]').forEach(b => { b.onclick = () => _jumpTo(b.dataset.jump); });
    const qz = box.querySelector('[data-quiz]');
    if (qz) qz.onclick = () => _quizOpen();
    document.getElementById('sc-check-toggle').onclick = () => {
        localStorage.setItem('sc-checklist-open', open ? '0' : '1');
        _updateChecklist();
    };
    _updatePublishWarn(st);
}

// ── 發布狀態時間線（解「存了不知道上了沒」）────────────────────
// 輪詢 token /status（白名單資料）；發布中/待發布/驗證中 5s、平時 30s、分頁隱藏暫停。
let _statusTimer = null;
let _pollGen = 0;   // 世代 token — fetch 進行中（timer 空窗）時重啟輪詢不會殘留舊 loop
async function _statusPollOnce() {
    const box = document.getElementById('sec-publish-status');
    if (!box) return 30000;
    let st = null;
    try {
        const r = await fetch(API + '/status');
        if (r.ok) st = await r.json();
    } catch (_) {}
    if (!st) { box.textContent = ''; return 30000; }
    const rb = st.rebuild || {};
    const lines = [];
    if (!st.published) {
        lines.push('尚未公開 — 勾選上方「公開」並儲存後，約 1 分鐘內自動發布上線');
    } else {
        if (rb.state === 'running') lines.push('發布中…');
        else if (rb.pending_count > 0) lines.push(
            `${rb.auto_fires_in_sec != null ? rb.auto_fires_in_sec + ' 秒後自動發布' : '等待自動發布'}（${rb.pending_count} 筆變動待發布）`);
        if (st.verified_at) {
            const t = new Date(st.verified_at).toLocaleString('zh-TW');
            lines.push(`已上線 ✓ ${t}${st.public_url
                ? ` ・ <a href="${_esc(st.public_url)}" target="_blank" style="color:var(--accent);">${_esc(st.public_url)}</a>` : ''}`);
        } else {
            lines.push('驗證中 — 下次發布時自動驗證上線狀態');
        }
    }
    box.innerHTML = lines.join('<br>');
    const fast = rb.state === 'running' || rb.pending_count > 0 || (st.published && !st.verified_at);
    return fast ? 5000 : 30000;
}
function _startStatusPolling() {
    if (!EDITABLE) return;
    if (_statusTimer) clearTimeout(_statusTimer);
    const gen = ++_pollGen;
    const loop = async () => {
        if (gen !== _pollGen) return;               // 已被新一輪取代
        if (document.hidden) return;                // 分頁隱藏即停，visibilitychange 回來重啟
        const next = await _statusPollOnce();
        if (gen !== _pollGen) return;
        _statusTimer = setTimeout(loop, next);
    };
    loop();
    if (!window.__scVisBound) {
        window.__scVisBound = true;
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden && EDITABLE) _startStatusPolling();
        });
    }
}

async function _reload() {
    try {
        const res = await fetch(API);
        if (res.ok) { DATA = await res.json(); _render(); }
    } catch (_) {}
}
