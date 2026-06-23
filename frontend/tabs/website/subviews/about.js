/**
 * about.js — 關於我們（公司介紹文案 + 團隊成員官網顯示覆寫編輯）
 * 公司文案寫 website_settings (about.* keys)。
 * 團隊成員：名字與原始職稱來自 CRM「人力資源」（正本），此處只編輯「官網顯示覆寫」
 *   （show_on_website + website_title/photo_url/bio/sort_order），永不寫入 CRM 正本。
 *   讀 GET /api/website/admin/team（全部員工），批次 PUT 回去 → mark_dirty → 官網 rebuild。
 *   UX：預設只列「已顯示於官網」的成員當可編卡片；其餘員工（CRM 可能上百人）用搜尋框加入。
 */
import { websiteFetch, esc, toastOk, toastErr, renderLoadError, renderCopyCard, getApiBase } from '../website-utils.js';

// 頭像 src：相對 /uploads/ 路徑（檔案在 NAS）要補 API base 才看得到預覽；絕對網址原樣用。
function _avatarSrc(url) {
    if (!url) return '';
    return /^https?:\/\//.test(url) ? url : `${getApiBase()}${url}`;
}

const _ABOUT_FIELDS = [
    { key: 'about.intro_zh', label: '公司介紹（中文）', long: true, placeholder: '源日影像是一間位於台北的...' },
    { key: 'about.intro_en', label: '公司介紹（英文）', long: true },
    { key: 'about.founded_year', label: '成立年份', placeholder: '2018' },
    { key: 'about.team_intro_zh', label: '團隊介紹（中文）', long: true },
];

// /about 頁面行銷文案（對應 about.astro 的 copy.about.* fallback）
// 註：公司故事 3 段是 about.intro_* 未填時的「預設故事」覆寫；填了 about.intro_* 則優先用那個。
const COPY_BLOCKS = [
    { key: 'hero_title', label: 'Hero 標題', placeholderZh: '關於我們', placeholderEn: 'About Us' },
    { key: 'story_p1', label: '預設故事 · 第 1 段', long: true, placeholderZh: '（公司名）專注於影像製作與行銷規劃。' },
    { key: 'story_p2', label: '預設故事 · 第 2 段', long: true, placeholderZh: '我們相信每一支影片都應該是一則有力的故事…' },
    { key: 'story_p3', label: '預設故事 · 第 3 段', long: true, placeholderZh: '位於台北中山，我們團隊結合了導演、製片…' },
    { key: 'team_heading', label: '團隊區塊標題', placeholderZh: '我們的團隊', placeholderEn: 'Our Team' },
    { key: 'contact_heading', label: '聯絡資訊區塊標題', placeholderZh: '聯絡資訊', placeholderEn: 'Contact' },
    { key: 'cta_button', label: 'CTA 按鈕', placeholderZh: '開始合作', placeholderEn: 'Start a Project' },
];

// 全部員工（含未顯示）快取，供搜尋「加入官網團隊」用。render() 時設定。
let _teamAll = [];

export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    container.innerHTML = '<h2>👥 關於我們</h2><div style="color:#888;padding:20px;">載入中…</div>';
    let settings = {};
    let team = [];
    try {
        const [s, t] = await Promise.all([
            websiteFetch('/api/website/admin/settings'),
            websiteFetch('/api/website/admin/team'),   // 全部員工（含未顯示），含覆寫值
        ]);
        if (!isCurrent()) return;
        settings = s?.settings || {};
        team = t?.items || [];
    } catch (e) {
        if (!isCurrent()) return;
        renderLoadError(container, '👥 關於我們', e);
        return;
    }

    _teamAll = team;
    // 預設只渲染「已顯示於官網」的成員為可編卡片；其餘用搜尋加入（避免列出全 CRM 上百人）。
    const shown = team.filter(t => t.show_on_website);

    container.innerHTML = `
        <h2>👥 關於我們</h2>

        <div class="card" style="margin-bottom:12px;">
            <h3 style="color:#fff;margin:0 0 12px 0;font-size:14px;">公司介紹文案</h3>
            ${_ABOUT_FIELDS.map(f => `
                <div style="margin-bottom:10px;">
                    <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;">${esc(f.label)}</label>
                    ${f.long
                        ? `<textarea data-key="${esc(f.key)}" rows="3" style="width:100%;resize:vertical;" placeholder="${esc(f.placeholder || '')}">${esc(settings[f.key] || '')}</textarea>`
                        : `<input data-key="${esc(f.key)}" value="${esc(settings[f.key] || '')}" placeholder="${esc(f.placeholder || '')}" style="width:100%;" />`}
                </div>
            `).join('')}
            <button class="btn" onclick="window._websiteSaveAbout()">💾 儲存公司文案</button>
        </div>

        ${renderCopyCard('copy.about', settings, COPY_BLOCKS, { title: '📝 關於頁文案', note: '對應 /about 頁的標題、預設故事段落與 CTA；留空則維持預設文案。' })}

        <div class="card" style="border-left:3px solid #228b22;">
            <h3 style="color:#fff;margin:0 0 4px 0;font-size:14px;">團隊成員（官網顯示）</h3>
            <p style="color:#888;font-size:11px;margin:0 0 12px 0;">
                名字與原始職稱來自 CRM「人力資源」（唯讀，此處不會變更 CRM 正本）。
                這裡設定的是「官網顯示覆寫」：官網職稱／頭像／簡介（留空則沿用 CRM 正本）、排序。
                取消勾選「在官網顯示」即從官網團隊頁移除。儲存後官網會自動 rebuild。
            </p>
            ${_teamAll.length
                ? `<div style="position:relative;margin-bottom:14px;">
                       <input id="team-search" oninput="window._websiteTeamSearch(this.value)" autocomplete="off"
                              placeholder="🔍 搜尋員工姓名以加入官網團隊…（目前顯示 ${shown.length} 人）" style="width:100%;" />
                       <div id="team-search-results" style="position:absolute;left:0;right:0;top:100%;margin-top:2px;background:#1a1a1a;border:1px solid #333;border-radius:6px;max-height:240px;overflow:auto;z-index:20;display:none;"></div>
                   </div>
                   <div id="team-shown" style="display:flex;flex-direction:column;gap:10px;">
                       ${shown.length
                           ? shown.map(t => _renderTeamRow(t)).join('')
                           : '<div id="team-empty" style="color:#666;padding:16px;text-align:center;">尚無團隊成員顯示於官網——用上方搜尋加入。</div>'}
                   </div>
                   <button class="btn" style="margin-top:14px;" onclick="window._websiteSaveTeam()">💾 儲存團隊設定</button>`
                : '<div style="color:#666;padding:20px;text-align:center;">CRM 尚無人員資料，請先到「人力資源」新增員工</div>'}
        </div>
    `;
}

// 單列團隊成員編輯卡：左側正本 reference（唯讀），右側官網覆寫欄位（可編輯）。
function _renderTeamRow(t) {
    const id = esc(t.id);
    const avatar = t.website_photo_url || t.photo_url;
    const canonRole = t.role || '';
    const canonPhoto = t.photo_url || '';
    return `
        <div data-team-id="${id}" style="display:flex;gap:14px;align-items:flex-start;background:#1a1a1a;border:1px solid #333;padding:12px;border-radius:6px;${t.show_on_website ? '' : 'opacity:0.45;'}">
            <div style="flex:0 0 64px;text-align:center;">
                <div id="team-avatar-${id}">
                    ${avatar
                        ? `<img src="${esc(_avatarSrc(avatar))}" style="width:64px;height:64px;border-radius:50%;object-fit:cover;background:#000;" onerror="this.style.display='none'" />`
                        : `<div style="width:64px;height:64px;border-radius:50%;background:#2a2a2a;display:flex;align-items:center;justify-content:center;color:#888;font-size:22px;">${esc((t.name || '?').charAt(0))}</div>`}
                </div>
                <button class="btn btn-sm" style="margin-top:6px;font-size:10px;padding:3px 8px;width:64px;" onclick="window._websiteUploadTeamPhoto('${id}', this)">📤 上傳</button>
            </div>
            <div style="flex:1;min-width:0;">
                <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;flex-wrap:wrap;">
                    <span style="color:#fff;font-weight:600;font-size:14px;">${esc(t.name)}</span>
                    <span style="color:#888;font-size:11px;">CRM 職稱：${esc(canonRole || '（未設定）')}</span>
                    <label style="margin-left:auto;display:inline-flex;align-items:center;gap:5px;color:#ddd;font-size:12px;cursor:pointer;">
                        <input type="checkbox" data-team-field="show_on_website"${t.show_on_website ? ' checked' : ''}
                               onchange="this.closest('[data-team-id]').style.opacity=this.checked?'1':'0.45'" />
                        在官網顯示
                    </label>
                </div>
                <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;">
                    <div>
                        <label style="color:#888;font-size:10px;display:block;margin-bottom:2px;">官網職稱</label>
                        <input data-team-field="website_title" value="${esc(t.website_title || '')}" placeholder="${esc(canonRole || '沿用 CRM 職稱')}" style="width:100%;" />
                    </div>
                    <div>
                        <label style="color:#888;font-size:10px;display:block;margin-bottom:2px;">官網頭像（按左側「上傳」鈕，或貼圖片網址）</label>
                        <input id="team-photo-url-${id}" data-team-field="website_photo_url" value="${esc(t.website_photo_url || '')}" placeholder="${esc(canonPhoto || '沿用 CRM 頭像')}" style="width:100%;" />
                    </div>
                    <div style="flex:0 0 100px;">
                        <label style="color:#888;font-size:10px;display:block;margin-bottom:2px;">排序</label>
                        <input type="number" data-team-field="website_sort_order" value="${Number.isFinite(+t.website_sort_order) ? +t.website_sort_order : 0}" style="width:100%;" />
                    </div>
                </div>
                <div style="margin-top:8px;">
                    <label style="color:#888;font-size:10px;display:block;margin-bottom:2px;">官網簡介（留空沿用 CRM 簡介）</label>
                    <textarea data-team-field="website_bio" rows="2" style="width:100%;resize:vertical;" placeholder="${esc(t.bio || '')}">${esc(t.website_bio || '')}</textarea>
                </div>
            </div>
        </div>`;
}

// 搜尋未顯示於官網的員工（依姓名），點選即加入可編清單並勾選顯示。
window._websiteTeamSearch = (q) => {
    const box = document.getElementById('team-search-results');
    if (!box) return;
    const query = (q || '').trim().toLowerCase();
    if (!query) { box.innerHTML = ''; box.style.display = 'none'; return; }
    const shownIds = new Set(
        [...document.querySelectorAll('#team-shown [data-team-id]')].map(r => r.dataset.teamId)
    );
    const matches = _teamAll
        .filter(t => !shownIds.has(t.id) && (t.name || '').toLowerCase().includes(query))
        .slice(0, 15);
    if (!matches.length) {
        box.innerHTML = '<div style="padding:8px 10px;color:#888;font-size:12px;">找不到符合的員工（或已在清單中）</div>';
        box.style.display = 'block';
        return;
    }
    box.innerHTML = matches.map(t => `
        <div onclick="window._websiteAddTeamMember('${esc(t.id)}')"
             style="padding:7px 10px;cursor:pointer;border-bottom:1px solid #2a2a2a;color:#ddd;font-size:13px;"
             onmouseover="this.style.background='#252525'" onmouseout="this.style.background=''">
            ＋ ${esc(t.name)} <span style="color:#888;font-size:11px;margin-left:6px;">${esc(t.role || '')}</span>
        </div>`).join('');
    box.style.display = 'block';
};

// 把搜尋選到的員工加入可編清單（預設勾選「在官網顯示」）。
window._websiteAddTeamMember = (id) => {
    const t = _teamAll.find(x => x.id === id);
    const cont = document.getElementById('team-shown');
    if (!t || !cont) return;
    if (cont.querySelector(`[data-team-id="${(window.CSS && CSS.escape) ? CSS.escape(id) : id}"]`)) return; // 已在清單
    const empty = document.getElementById('team-empty');
    if (empty) empty.remove();
    cont.insertAdjacentHTML('beforeend', _renderTeamRow({ ...t, show_on_website: true }));
    const si = document.getElementById('team-search');
    if (si) si.value = '';
    const box = document.getElementById('team-search-results');
    if (box) { box.innerHTML = ''; box.style.display = 'none'; }
};

// 上傳官網頭像 → 後端即時寫 website_photo_url + rebuild；前端更新預覽與 URL 欄位。
window._websiteUploadTeamPhoto = (id, btn) => {
    const fi = document.createElement('input');
    fi.type = 'file';
    fi.accept = 'image/*';
    fi.onchange = async () => {
        const f = fi.files && fi.files[0];
        if (!f) return;
        const orig = btn.textContent;
        btn.disabled = true; btn.textContent = '⏳';
        try {
            const fd = new FormData();
            fd.append('file', f);
            const r = await websiteFetch(`/api/website/admin/team/${encodeURIComponent(id)}/upload-photo`, { method: 'POST', body: fd });
            const urlInput = document.getElementById(`team-photo-url-${id}`);
            if (urlInput) urlInput.value = r.url;
            const box = document.getElementById(`team-avatar-${id}`);
            if (box) box.innerHTML = `<img src="${esc(_avatarSrc(r.url))}?t=${Date.now()}" style="width:64px;height:64px;border-radius:50%;object-fit:cover;background:#000;" />`;
            toastOk('頭像已上傳並儲存，官網將自動 rebuild');
        } catch (e) {
            toastErr('上傳失敗：' + (e.message || e));
        } finally {
            btn.disabled = false; btn.textContent = orig;
        }
    };
    fi.click();
};

window._websiteSaveAbout = async () => {
    const values = {};
    document.querySelectorAll('#website-content [data-key]').forEach(el => {
        values[el.dataset.key] = el.value;
    });
    try {
        const r = await websiteFetch('/api/website/admin/settings', { method: 'PUT', body: { values } });
        toastOk(`已更新 ${r.updated} 項`);
    } catch (e) { toastErr(e.message); }
};

// 批次儲存團隊官網顯示覆寫 — 掃每列 [data-team-id]（= 目前在清單中的成員），收 [data-team-field] 成 items。
// 只送 show_on_website + website_*（不含正本 name/role/photo_url/bio）→ 後端再次過濾白名單。
// 未列出的員工不送 → 後端不動其 show_on_website（維持 false）。
window._websiteSaveTeam = async () => {
    const items = [];
    document.querySelectorAll('#website-content [data-team-id]').forEach(row => {
        const item = { id: row.dataset.teamId };
        row.querySelectorAll('[data-team-field]').forEach(el => {
            const f = el.dataset.teamField;
            if (el.type === 'checkbox') {
                item[f] = el.checked;
            } else if (el.type === 'number') {
                const n = Number(el.value);
                item[f] = Number.isNaN(n) ? 0 : n;
            } else {
                item[f] = el.value;
            }
        });
        items.push(item);
    });
    if (!items.length) { toastOk('沒有團隊成員需要儲存'); return; }
    try {
        const r = await websiteFetch('/api/website/admin/team', { method: 'PUT', body: { items } });
        toastOk(`已更新 ${r.updated} 位團隊成員，官網將自動 rebuild`);
    } catch (e) { toastErr(e.message); }
};
