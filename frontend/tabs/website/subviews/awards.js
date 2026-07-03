/**
 * awards.js — 站級獎項紀錄子視圖（film-centric 改版）
 *
 * /portfolio 頁面頂部「Honors & Awards」榮譽牆來源。以「作品」為中心：
 * 一張卡 = 一部作品（work_type +《work_title》+ work_year）+ 多行獎項/影展文字。
 *
 * 每行獎項 = 一筆 WebsiteAward row（共用 work_type/work_title/work_year）。
 * 提供「📋 批次匯入」貼整段純文字 → 預覽 → 確認匯入。
 *
 * 決議 B：不分「獲獎/入圍」，獎項行是純文字（level 後端固定寫 "獲獎"，前端不顯示）。
 *
 * 寫入後 60 秒對外網站重 build。
 */
import { websiteFetch, esc, toastOk, toastErr, renderLoadError, emptyHint } from '../website-utils.js';

let _awards = [];        // 後端原始 rows（含 id）
let _container = null;
let _films = [];         // 分組後的作品卡（render 用 client 端模型）
let _isCurrent = () => true;

export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    _isCurrent = isCurrent;
    _container = container;
    container.innerHTML = '<h2>🏆 獎項紀錄</h2><div style="color:#888;padding:20px;">載入中…</div>';
    await _reload();
}

async function _reload() {
    try {
        const res = await websiteFetch('/api/website/admin/awards');
        if (!_isCurrent()) return;
        _awards = res?.items || [];
    } catch (e) {
        if (!_isCurrent()) return;
        renderLoadError(_container, '🏆 獎項紀錄', e,
            'NAS website-api 可能跑舊版（沒 admin/awards endpoint）。請在 master 跑 /publish 同步後端到 NAS。');
        return;
    }
    _films = _groupFilms(_awards);
    _renderAll();
}

/**
 * 把 flat rows 依 (work_title + work_type + work_year) 分組成作品卡。
 * 無 work_title 的 row 各自成組（key 用 id）。組內依 sort_order 保留順序。
 * 回傳 client 模型：{ key, work_type, work_title, work_year, lines:[{id, year, name_zh, cert_url}] }
 */
function _groupFilms(rows) {
    const map = new Map();
    for (const a of rows) {
        const wt = (a.work_title || '').trim();
        const ty = (a.work_type || '').trim();
        const wy = a.work_year ?? null;
        const key = wt ? `${wt}|${ty}|${wy ?? ''}` : `__solo_${a.id}`;
        if (!map.has(key)) {
            map.set(key, { key, work_type: ty, work_title: wt, work_year: wy, lines: [] });
        }
        map.get(key).lines.push({ id: a.id, year: a.year, name_zh: a.name_zh, name_en: a.name_en || '', cert_url: a.cert_url || '' });
    }
    const films = Array.from(map.values());
    for (const f of films) {
        f.lines.sort((x, y) => {
            const sx = _rowSort(x.id), sy = _rowSort(y.id);
            return sx - sy || y.year - x.year;
        });
    }
    // 組間依 work_year 新→舊（無則 fallback 最大行年份）
    films.sort((a, b) => _filmYearKey(b) - _filmYearKey(a));
    return films;
}

function _rowSort(id) {
    const r = _awards.find(a => a.id === id);
    return r ? r.sort_order : 0;
}

function _filmYearKey(f) {
    if (f.work_year != null) return f.work_year;
    const ys = f.lines.map(l => l.year).filter(y => typeof y === 'number');
    return ys.length ? Math.max(...ys) : 0;
}

function _renderAll() {
    const visibleCount = _awards.filter(a => a.visible).length;
    const filmsHtml = _films.length
        ? _films.map((f, i) => _filmCardHtml(f, i)).join('')
        : emptyHint('尚無作品。用上方「+ 新增作品」或「批次匯入」建立。', { padding: 30 });

    _container.innerHTML = `
        <h2>🏆 獎項紀錄 <span style="color:#888;font-size:13px;font-weight:400;">· ${_films.length} 部作品 · ${visibleCount} 行顯示中</span></h2>
        <div style="color:#aaa;font-size:12px;margin-bottom:12px;">
            以作品為中心：一張卡 = 一部作品（類型 +《標題》+ 年度）+ 多行獎項/影展文字（純文字，不分獲獎/入圍）。
            儲存後 60 秒內對外網站 /portfolio 頁面頂部會更新榮譽牆。
        </div>

        ${_bulkPanelHtml()}

        <div style="margin:14px 0;">
            <button class="btn" onclick="window._awardsAddFilm()">+ 新增作品</button>
        </div>

        <div id="aw-films">${filmsHtml}</div>
    `;
}

function _bulkPanelHtml() {
    return `
        <div class="card" style="border-left:3px solid #3b82f6;margin-bottom:8px;">
            <h3 style="color:#fff;margin:0 0 4px 0;font-size:14px;">📋 批次匯入</h3>
            <p style="color:#888;font-size:11px;margin:0 0 8px 0;">
                貼整段「歷年作品（獎項）」。規則：<code>2024 |</code> 設群組年度；<code>類型《標題》</code> 開新作品；其餘行 = 該作品的一行獎項。先「預覽」確認解析正確再「確認匯入」。
            </p>
            <textarea id="aw-bulk-text" rows="6" style="width:100%;resize:vertical;font-family:monospace;font-size:12px;" placeholder="2024 |\n紀錄短片《新生重建》\n2024 臺南岸內製片所 - 金岸內首獎…\n2025 青春影展"></textarea>
            <div style="margin-top:8px;display:flex;gap:8px;">
                <button class="btn btn-sm" onclick="window._awardsBulkPreview()">🔍 預覽</button>
                <button class="btn btn-sm btn-primary" id="aw-bulk-confirm" style="display:none;" onclick="window._awardsBulkConfirm()">✅ 確認匯入</button>
            </div>
            <div id="aw-bulk-preview" style="margin-top:10px;"></div>
        </div>
    `;
}

function _filmCardHtml(f, idx) {
    // 獎項行 = 一個 textarea，一行一個獎項（純文字）。年份儲存時自動從行首 YYYY 抓、否則用作品年度。
    // 英文為第二個 textarea，逐行對齊中文（第 N 行英文 = 第 N 行中文）；獎名是專有名詞，AI 不代翻，人工填。
    const linesText = f.lines.map(l => l.name_zh || '').join('\n');
    const linesTextEn = f.lines.map(l => l.name_en || '').join('\n');
    const rows = Math.max(3, f.lines.length + 1);
    return `
        <div class="card" data-film="${idx}" style="border-left:3px solid #c8a45c;margin-bottom:12px;">
            <div style="display:grid;grid-template-columns:160px 1fr 90px auto;gap:8px;align-items:end;margin-bottom:10px;">
                <div><label style="color:#888;font-size:11px;">類型</label>
                    <input data-film-field="work_type" value="${esc(f.work_type || '')}" placeholder="劇情短片" style="width:100%;" /></div>
                <div><label style="color:#888;font-size:11px;">作品標題 *（不含《》）</label>
                    <input data-film-field="work_title" value="${esc(f.work_title || '')}" placeholder="自主揮棒" style="width:100%;" /></div>
                <div><label style="color:#888;font-size:11px;">作品年度</label>
                    <input data-film-field="work_year" type="number" min="1900" max="2100" value="${f.work_year ?? ''}" placeholder="2024" style="width:100%;" /></div>
                <div style="text-align:right;white-space:nowrap;">
                    <button class="btn btn-sm" onclick="window._awardsSaveFilm(${idx})">💾 儲存作品</button>
                    <button class="btn btn-sm btn-danger" onclick="window._awardsDeleteFilm(${idx})">🗑 刪除</button>
                </div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
                <div>
                    <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">獎項 / 影展 中文（一行一個，可直接貼上）</label>
                    <textarea data-film-field="lines_text" rows="${rows}" style="width:100%;resize:vertical;font-size:13px;line-height:1.6;" placeholder="2024 臺南岸內製片所 - 金岸內首獎&#10;2025 青春影展&#10;2025 Eye Catcher Global - The Best student award">${esc(linesText)}</textarea>
                </div>
                <div>
                    <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">獎項英文（可選 · 逐行對齊左欄 · 留空行前台顯示中文）</label>
                    <textarea data-film-field="lines_text_en" rows="${rows}" style="width:100%;resize:vertical;font-size:13px;line-height:1.6;" placeholder="2024 Tainan An-Nei Film Studio — Grand Prize&#10;2025 Youth Film Festival&#10;2025 Eye Catcher Global — Best Student Award">${esc(linesTextEn)}</textarea>
                </div>
            </div>
            <div style="color:#666;font-size:10.5px;margin-top:3px;">英文行數需與中文一致才能儲存（否則對齊會錯位）。</div>
        </div>
    `;
}

// ── 讀卡：work_type/work_title/work_year + 把 textarea 拆成 lines:[{year, name_zh}] ──
// 年份：行首有 YYYY 就用、否則用作品年度、再否則今年。
function _readFilmCard(idx) {
    const card = _container.querySelector(`[data-film="${idx}"]`);
    if (!card) return null;
    const get = (f) => card.querySelector(`[data-film-field="${f}"]`)?.value ?? '';
    const work_type = get('work_type').trim();
    const work_title = get('work_title').trim();
    const wyRaw = get('work_year').trim();
    const work_year = wyRaw ? Number(wyRaw) : null;
    // 中文獎項行是真相；英文逐「第 N 個非空行」對齊（blank line 不計）。
    const zhLines = get('lines_text').split('\n').map(s => s.trim()).filter(Boolean);
    const enLines = get('lines_text_en').split('\n').map(s => s.trim()).filter(Boolean);
    const enMismatch = enLines.length > 0 && enLines.length !== zhLines.length;
    const lines = zhLines.map((name_zh, i) => {
        const m = name_zh.match(/^(\d{4})\b/);
        const year = m ? Number(m[1]) : (work_year || new Date().getFullYear());
        return { name_zh, year, name_en: enLines[i] || null };
    });
    return { work_type, work_title, work_year, lines, enMismatch, zhCount: zhLines.length, enCount: enLines.length };
}

// ── 作品卡操作 ──

window._awardsAddFilm = () => {
    _films.unshift({ key: `__new_${Date.now()}`, work_type: '', work_title: '', work_year: null, lines: [] });
    _renderAll();
};

window._awardsSaveFilm = async (idx) => {
    const data = _readFilmCard(idx);
    if (!data) { toastErr('找不到作品卡'); return; }
    if (!data.work_title) { toastErr('作品標題必填'); return; }
    if (!data.lines.length) { toastErr('至少要一行獎項'); return; }
    if (data.enMismatch) {
        toastErr(`英文 ${data.enCount} 行與中文 ${data.zhCount} 行不一致 — 請逐行對齊，或清空英文欄`);
        return;
    }

    // textarea 是該作品獎項的唯一真相 → 整批重建：先 POST 新行、再刪舊行
    //（中途失敗不會掉資料，最多殘留舊行，重存即修正）。
    const film = _films[idx];
    const oldIds = (film?.lines || []).map(l => l.id).filter(x => x != null);

    try {
        let baseSort = _nextSortOrder();
        for (const l of data.lines) {
            await websiteFetch('/api/website/admin/awards', {
                method: 'POST',
                body: {
                    year: l.year, name_zh: l.name_zh, name_en: l.name_en || null, level: '獲獎',
                    work_type: data.work_type || null,
                    work_title: data.work_title, work_year: data.work_year,
                    sort_order: baseSort++, visible: true,
                },
            });
        }
        for (const id of oldIds) {
            await websiteFetch(`/api/website/admin/awards/${id}`, { method: 'DELETE' });
        }
        toastOk('已儲存作品');
        await _reload();
    } catch (e) { toastErr(e.message); }
};

window._awardsDeleteFilm = async (idx) => {
    const film = _films[idx];
    if (!film) return;
    const ids = (film.lines || []).map(l => l.id).filter(x => x != null);
    if (ids.length && !confirm(`確定刪除作品《${film.work_title || ''}》及其 ${ids.length} 行獎項？`)) return;
    try {
        for (const id of ids) {
            await websiteFetch(`/api/website/admin/awards/${id}`, { method: 'DELETE' });
        }
        // 沒 row 的純前端空卡直接移除
        _films.splice(idx, 1);
        if (ids.length) {
            toastOk('已刪除作品');
            await _reload();
        } else {
            _renderAll();
        }
    } catch (e) { toastErr(e.message); }
};

function _nextSortOrder() {
    const max = _awards.reduce((m, a) => Math.max(m, a.sort_order || 0), -1);
    return max + 1;
}

// ── 批次匯入 ──

window._awardsBulkPreview = async () => {
    const text = document.getElementById('aw-bulk-text')?.value || '';
    const box = document.getElementById('aw-bulk-preview');
    const confirmBtn = document.getElementById('aw-bulk-confirm');
    if (!text.trim()) { toastErr('請先貼上文字'); return; }
    box.innerHTML = '<div style="color:#888;font-size:12px;">解析中…</div>';
    try {
        const res = await websiteFetch('/api/website/admin/awards/bulk_import', {
            method: 'POST',
            body: { text, dry_run: true, now_year: new Date().getFullYear() },
        });
        box.innerHTML = _renderPreview(res);
        if (confirmBtn) confirmBtn.style.display = (res.total_works > 0) ? 'inline-block' : 'none';
    } catch (e) {
        box.innerHTML = `<div style="color:#f87171;font-size:12px;">預覽失敗：${esc(e.message)}</div>`;
    }
};

function _renderPreview(res) {
    const works = res.works || [];
    const warnings = res.warnings || [];
    const warnHtml = warnings.length
        ? `<div style="color:#fbbf24;font-size:12px;margin-bottom:8px;">⚠️ ${warnings.map(esc).join('<br>')}</div>`
        : '';
    const worksHtml = works.map(w => {
        const heading = (w.work_type ? esc(w.work_type) : '') + `《${esc(w.work_title || '')}》`
            + (w.work_year != null ? ` <span style="color:#888;">(${w.work_year})</span>` : '');
        const lines = (w.lines || []).map(l =>
            `<li style="color:#bbb;font-size:12px;"><span style="color:#c8a45c;">${esc(String(l.year))}</span> ${esc(l.name_zh)}</li>`
        ).join('');
        return `<div style="margin-bottom:8px;">
            <div style="color:#fff;font-size:13px;font-weight:600;">${heading}</div>
            <ul style="margin:4px 0 0 18px;">${lines || '<li style="color:#666;font-size:12px;">（無獎項行）</li>'}</ul>
        </div>`;
    }).join('');

    return `
        ${warnHtml}
        <div style="color:#aaa;font-size:12px;margin-bottom:6px;">
            解析結果：<strong style="color:#fff;">${res.total_works}</strong> 部作品 ·
            <strong style="color:#fff;">${res.total_lines}</strong> 行獎項
        </div>
        <div style="max-height:340px;overflow-y:auto;border:1px solid #333;border-radius:6px;padding:10px;background:#141414;">
            ${worksHtml || '<div style="color:#666;font-size:12px;">未解析出任何作品。</div>'}
        </div>
    `;
}

window._awardsBulkConfirm = async () => {
    const text = document.getElementById('aw-bulk-text')?.value || '';
    if (!text.trim()) { toastErr('請先貼上文字'); return; }
    if (!confirm('確認匯入？將新增獎項 rows（不會覆蓋現有資料）。')) return;
    try {
        const res = await websiteFetch('/api/website/admin/awards/bulk_import', {
            method: 'POST',
            body: { text, dry_run: false, now_year: new Date().getFullYear() },
        });
        toastOk(`已匯入 ${res.created} 行獎項（${res.total_works} 部作品）`);
        const ta = document.getElementById('aw-bulk-text');
        if (ta) ta.value = '';
        await _reload();
    } catch (e) { toastErr(e.message); }
};
