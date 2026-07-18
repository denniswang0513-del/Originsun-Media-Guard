// showcase-overlay.js — 「編輯作品」slide-over 共用殼（嵌 /showcase-edit.html iframe）
// 供 官網管理›作品集（works.js）與 專案管理›結案收件匣（crm-projects-closing.js）共用，
// 取代兩邊近重複的 overlay 實作。postMessage 協定（editor → parent）：
//   showcase-saved        → onSaved 回呼（列表刷新）
//   showcase-title-change → 標題列「編輯：XXX」即時同步
// 編輯器 iframe 端自動偵測 embed（無需帶 ?embed=1）。

let _installed = false;
let _cbs = { onSaved: null, onClose: null };

function _install() {
    if (_installed) return;
    _installed = true;
    window.addEventListener('message', (e) => {
        const t = e && e.data && e.data.type;
        if (t === 'showcase-saved') {
            _cbs.onSaved?.();
        } else if (t === 'showcase-title-change') {
            const titleEl = document.getElementById('showcase-overlay-title');
            if (titleEl && e.data.title) titleEl.textContent = `編輯：${e.data.title}`;
        }
    });
}

function _ensure() {
    _install();
    if (document.getElementById('showcase-edit-overlay')) return;
    const overlay = document.createElement('div');
    overlay.id = 'showcase-edit-overlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:9000;display:none;align-items:stretch;justify-content:flex-end;';
    overlay.innerHTML = `
        <div style="width:80%;max-width:1000px;height:100%;background:#1a1a1a;border-left:1px solid #3a3a3a;display:flex;flex-direction:column;box-shadow:-8px 0 24px rgba(0,0,0,0.6);">
            <div style="display:flex;align-items:center;gap:12px;padding:10px 14px;border-bottom:1px solid #3a3a3a;background:#2a2a2a;flex-shrink:0;">
                <strong id="showcase-overlay-title" style="color:#fff;font-size:14px;flex:1;">編輯作品</strong>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" id="showcase-overlay-close" type="button">關閉並重新整理</button>
            </div>
            <iframe id="showcase-overlay-iframe" style="flex:1;width:100%;border:0;background:#1e1e1e;"></iframe>
        </div>`;
    overlay.addEventListener('click', (e) => { if (e.target === overlay) closeShowcaseOverlay(); });
    document.body.appendChild(overlay);
    document.getElementById('showcase-overlay-close').addEventListener('click', () => closeShowcaseOverlay());
}

export function openShowcaseOverlay(url, title, { onSaved = null, onClose = null } = {}) {
    _ensure();
    _cbs = { onSaved, onClose };
    document.getElementById('showcase-overlay-title').textContent = title || '編輯作品';
    document.getElementById('showcase-overlay-iframe').src = url;
    document.getElementById('showcase-edit-overlay').style.display = 'flex';
}

export async function closeShowcaseOverlay() {
    const overlay = document.getElementById('showcase-edit-overlay');
    if (!overlay || overlay.style.display === 'none') return;
    overlay.style.display = 'none';
    document.getElementById('showcase-overlay-iframe').src = 'about:blank';
    const cb = _cbs.onClose;
    _cbs = { onSaved: null, onClose: null };   // 兩者皆一次性 — 關閉後遲到的 postMessage 不觸發舊回呼
    if (cb) await cb();
}
