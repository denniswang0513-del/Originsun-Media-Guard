// ────────────────────────────────────────────────────────────────────────────
// /showcase-edit.html 作品編輯器 —— 從影像紀錄挑選（創作過程）＋靜態 modal 綁定＋開頁 _load()（一定載最後）
//
// ⚠ 這是「傳統 script」不是 module：showcase-edit.html 底部依序 <script src> 載入 js/showcase-edit/*.js，
//   六支共用同一個全域詞法環境，頂層的 const／let／function 跨檔直接看得見（原本是同一段 inline script，
//   2026-09-12 原樣切開、零改寫；同 my.html → js/my/ 的拆法）。載入順序＝原本由上而下的執行順序，不可調換。
// ────────────────────────────────────────────────────────────────────────────
// === 從影像紀錄挑選（創作過程）===
// 劇組已上傳到「影像紀錄」的工作照 → 結案上架直接挑進官網創作過程，免重新上傳。
// GET  {API}/media-log-files        → {files:[{id,thumb_url,filename,category,created_at}]}（僅有縮圖的圖片，新→舊）
// POST {API}/process/from-media-log body {file_ids:[...]}（≤50）
//      → {status,imported,process_items(全量新清單，直接取代本地清單重繪)}；404=沒有可轉檔案、502=原檔讀取失敗
const _MLP_MAX = 50;
let _mlpSelected = new Set();   // 選取中的 file id（字串型；送出時數字字串轉回 number）

function _mlpFmtDate(iso) {
    const d = new Date(iso || '');
    if (isNaN(d.getTime())) return '';
    return String(d.getMonth() + 1).padStart(2, '0') + '/' + String(d.getDate()).padStart(2, '0');
}

function _mlpUpdateFooter() {
    const cnt = document.getElementById('mlp-count');
    const btn = document.getElementById('mlp-submit');
    if (cnt) cnt.textContent = `已選 ${_mlpSelected.size} 張`;
    if (btn) btn.disabled = _mlpSelected.size === 0;
}

async function _mlpOpen() {
    const m = document.getElementById('media-log-pick-modal');
    const grid = document.getElementById('mlp-grid');
    _mlpSelected = new Set();
    const sb = document.getElementById('mlp-submit');
    if (sb) sb.textContent = '加入創作過程';
    _mlpUpdateFooter();
    grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;color:var(--text3);font-size:13px;padding:40px 0;">載入中…</div>';
    if (EMBED) {
        _mlpPlace();
        // 跟著父視窗捲動走：開著時父頁捲動/縮放 → 重新定位（capture 才吃得到
        // overlay 內層容器的 scroll）。同源才掛得上；關閉時 _mlpClose 拆。
        if (!_mlpPlaceBound) {
            _mlpPlaceBound = () => requestAnimationFrame(_mlpPlace);
            try {
                window.parent.addEventListener('scroll', _mlpPlaceBound, true);
                window.parent.addEventListener('resize', _mlpPlaceBound);
            } catch (_) {}
        }
    }
    m.classList.add('show');
    // 每次開啟都重新 fetch — 收檔是進行式，清單要拿最新的
    try {
        const res = await fetch(API + '/media-log-files');
        if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || '載入失敗'); }
        const files = (await res.json()).files || [];
        if (!files.length) {
            grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;color:var(--text3);font-size:13px;padding:40px 0;">影像紀錄還沒有圖片 — 到專案的影像紀錄 tab 或公開連結先收檔</div>';
            return;
        }
        grid.innerHTML = files.map(f => `
            <div class="sc-mlp-item" data-id="${_esc(String(f.id))}" title="${_esc(f.filename || '')}">
                <span class="sc-mlp-check">✓</span>
                <img src="${_esc(f.thumb_url)}" alt="" loading="lazy">
                <div class="sc-mlp-meta">${_esc(f.category || '未分類')} · ${_mlpFmtDate(f.created_at)}</div>
            </div>`).join('');
        grid.querySelectorAll('.sc-mlp-item').forEach(el => {
            el.addEventListener('click', () => {
                const id = el.dataset.id;
                if (_mlpSelected.has(id)) { _mlpSelected.delete(id); el.classList.remove('on'); }
                else {
                    if (_mlpSelected.size >= _MLP_MAX) { _toast(`一次最多 ${_MLP_MAX} 張`); return; }
                    _mlpSelected.add(id); el.classList.add('on');
                }
                _mlpUpdateFooter();
            });
        });
    } catch (e) {
        grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;color:var(--danger);font-size:13px;padding:40px 0;">${_esc(e.message)}</div>`;
    }
}

// embed 定位：iframe 高=內容高，fixed/vh 以整個超長 iframe 計 — 同源直接讀
// 父視窗可視區，overlay 釘在「父視窗目前看得到的那一片」置中；modal 高限
// 可視高內（格線內捲、footer 按鈕恆在）。捲動時由 listener 重呼叫 = 跟著視窗走。
let _mlpPlaceBound = null;
function _mlpPlace() {
    const m = document.getElementById('media-log-pick-modal');
    let top = 0;
    let vh = window.innerHeight;
    try {
        const fr = window.frameElement?.getBoundingClientRect();
        if (fr && window.parent) {
            top = Math.max(0, -fr.top);
            vh = Math.min(window.parent.innerHeight - Math.max(fr.top, 0),
                          document.documentElement.scrollHeight - top);
        }
    } catch (_) { /* cross-origin 父頁（理論上不會）→ 退回 iframe 視角 */ }
    m.style.position = 'absolute';
    m.style.top = top + 'px';
    m.style.height = Math.max(vh, 280) + 'px';
    m.style.alignItems = 'center';
    m.style.paddingTop = '0';
    const box = m.firstElementChild;
    if (box) box.style.maxHeight = Math.max(vh - 40, 240) + 'px';
}

function _mlpClose() {
    document.getElementById('media-log-pick-modal').classList.remove('show');
    if (_mlpPlaceBound) {
        try {
            window.parent.removeEventListener('scroll', _mlpPlaceBound, true);
            window.parent.removeEventListener('resize', _mlpPlaceBound);
        } catch (_) {}
        _mlpPlaceBound = null;
    }
}

async function _mlpSubmit() {
    if (!_mlpSelected.size) return;
    const btn = document.getElementById('mlp-submit');
    const n = _mlpSelected.size;
    btn.disabled = true;
    btn.textContent = '加入中…';
    try {
        const res = await fetch(API + '/process/from-media-log', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_ids: [..._mlpSelected].map(x => (/^\d+$/.test(x) ? Number(x) : x)) }),
        });
        if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || '加入失敗'); }
        const data = await res.json();
        _mlpClose();
        // 契約：回應帶全量新 process_items → 直接取代本地清單重繪（同既有上傳後刷新效果）
        if (DATA && Array.isArray(data.process_items)) { DATA.process_items = data.process_items; _render(); }
        else await _reload();
        _toast(`已加入 ${data.imported != null ? data.imported : n} 張`);
    } catch (e) {
        alert(e.message);
        btn.disabled = false;
    } finally {
        btn.textContent = '加入創作過程';
    }
}

// 靜態 modal 只綁一次（modal 節點在 body、不隨 _render 重建）：取消/送出/點背景/Esc
document.getElementById('mlp-cancel').addEventListener('click', _mlpClose);
document.getElementById('mlp-submit').addEventListener('click', _mlpSubmit);
document.getElementById('media-log-pick-modal').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) _mlpClose();
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.getElementById('media-log-pick-modal').classList.contains('show')) _mlpClose();
});

_load();
