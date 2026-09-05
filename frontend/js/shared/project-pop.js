/**
 * js/shared/project-pop.js — 專案打字浮層：分「進行中（預設展開）／已結案（收著，打字會搜到、也可點開）」。
 * owner 2026-09-03（工作日誌）／2026-09-04（零用金）：原生 datalist／select 分不了組，所以是自己的浮層；
 * 任何 `<input data-proj-pick>`／`<textarea data-proj-pick>` 都能掛：工作日誌的格子、零用金的三個專案欄。
 *
 * rows：[{ id, name, label, client, year, closed }]——分組旗標 closed 由後端給（core.project_flow.is_closed），這裡不認狀態字。
 * 選了：input.value = cfg.value(p)（預設整串 label）、data-pid／data-pname 記 id、title 放整串；
 *       dispatch input（帶 _fromPick，宿主的 input 監聽可據此略過）與 change（宿主的存檔就走原本那條）。
 * 鍵盤：浮層開著時 ↓↑ 在浮層裡走、Enter 選、Esc／Tab 關——在 capture 階段吃掉，先於宿主自己的 keydown
 *       （工作日誌的 ↓↑ 是換列）；關著時鍵盤全部還給宿主。手打改了字就清掉 data-pid（不再是選到的那個案）。
 */
import { esc } from './dom.js';

// 顏色全走變數：內部系統深色皮是預設值；員工工作台（/my.html）覆寫成官網風格的白底細框
const CSS = `
.proj-pop { --pp-bg:#1f1f1f; --pp-line:#3a3a3a; --pp-shadow:0 8px 24px rgba(0,0,0,.5); --pp-head:#262626; --pp-head-ink:#9ca3af; --pp-toggle:#60a5fa;
    --pp-ink:#ddd; --pp-sub:#9ca3af; --pp-on:#2a3b55; --pp-on-ink:#fff; --pp-empty:#666;
    position:absolute; z-index:1000; max-width:560px; max-height:320px; overflow-y:auto; background:var(--pp-bg); border:1px solid var(--pp-line); border-radius:6px; box-shadow:var(--pp-shadow); font-size:13px; }
.proj-pop .pp-h { padding:6px 10px; color:var(--pp-head-ink); font-size:11px; letter-spacing:.08em; background:var(--pp-head); position:sticky; top:0; }
.proj-pop .pp-toggle { cursor:pointer; color:var(--pp-toggle); }
.proj-pop .pp-item { padding:6px 10px; color:var(--pp-ink); cursor:pointer; line-height:1.3; }
.proj-pop .pp-item.on, .proj-pop .pp-item:hover { background:var(--pp-on); color:var(--pp-on-ink); }
.proj-pop .pp-sub { color:var(--pp-sub); font-size:11px; }
.proj-pop .pp-empty { padding:6px 10px; color:var(--pp-empty); }`;

let _pop = null;   // 全頁只有一個浮層：{ el, input, cfg, idx, showClosed, flat }

function _ensureCss() {
    if (document.getElementById('proj-pop-css')) return;
    const st = document.createElement('style');
    st.id = 'proj-pop-css';
    st.textContent = CSS;
    document.head.appendChild(st);
}

export function closeProjectPop() {
    if (_pop) { _pop.el.remove(); _pop = null; }
}

function _split(rows, q, showClosed) {
    const needle = (q || '').trim().toLowerCase();
    const hit = p => !needle || `${p.label || ''} ${p.name || ''}`.toLowerCase().includes(needle);
    return { active: rows.filter(p => !p.closed && hit(p)), closed: rows.filter(p => p.closed && hit(p)), show: showClosed || !!needle };
}

function _render(rows) {
    const { el, input } = _pop;
    const { active, closed, show } = _split(rows, input.value, _pop.showClosed);
    const flat = [];
    const item = (p) => {
        flat.push(p);
        const i = flat.length - 1;
        const sub = [p.year, p.client].filter(Boolean).join(' · ');
        return `<div class="pp-item${i === _pop.idx ? ' on' : ''}" data-i="${i}"><div>${esc(p.name || p.label || '')}</div>${sub ? `<div class="pp-sub">${esc(sub)}</div>` : ''}</div>`;
    };
    const [g1, g2] = _pop.cfg.groups;
    let html = `<div class="pp-h">${esc(g1)}（${active.length}）</div>` + (active.length ? active.map(item).join('') : '<div class="pp-empty">沒有符合的</div>');
    html += `<div class="pp-h pp-toggle" data-toggle="1">${esc(g2)}（${closed.length}）${show ? '' : '　點一下展開'}</div>`;
    if (show) html += closed.length ? closed.map(item).join('') : '<div class="pp-empty">沒有符合的</div>';
    _pop.flat = flat;
    el.innerHTML = html;
    const r = input.getBoundingClientRect();
    el.style.left = `${r.left + window.scrollX}px`;
    el.style.top = `${r.bottom + window.scrollY}px`;
    el.style.minWidth = `${Math.max(r.width, 320)}px`;
    el.querySelector('.pp-item.on')?.scrollIntoView({ block: 'nearest' });
}

function _pick(p) {
    if (!p || !_pop) return;
    const { input, cfg } = _pop;
    input.value = cfg.value(p);
    input.dataset.pid = p.id || '';
    input.dataset.pname = input.value;
    input.title = p.label || p.name || '';
    closeProjectPop();
    const ev = new Event('input', { bubbles: true });
    ev._fromPick = true;
    input.dispatchEvent(ev);
    input.dispatchEvent(new Event('change', { bubbles: true }));
}

async function _open(input, cfg) {
    const rows = await cfg.options();
    if (document.activeElement !== input) return;      // 抓完選項時人已經離開那一格
    if (!_pop || _pop.input !== input) {
        closeProjectPop();
        const el = document.createElement('div');
        el.className = 'proj-pop';
        el.addEventListener('pointerdown', (ev) => {        // pointerdown：blur 會先於 click 把浮層收掉
            ev.preventDefault();
            const it = ev.target.closest('.pp-item');
            if (it) { _pick(_pop.flat[Number(it.dataset.i)]); return; }
            if (ev.target.closest('[data-toggle]')) { _pop.showClosed = !_pop.showClosed; _pop.idx = -1; _render(rows); }
        });
        document.body.appendChild(el);
        _pop = { el, input, cfg, idx: -1, showClosed: false, flat: [] };
    }
    _pop.rows = rows;
    _render(rows);
}

function _keydown(ev) {
    if (!_pop || ev.target !== _pop.input) return;
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault(); ev.stopPropagation();
        const n = _pop.flat.length; if (!n) return;
        _pop.idx = (_pop.idx + (ev.key === 'ArrowDown' ? 1 : -1) + n) % n;
        _render(_pop.rows);
    } else if (ev.key === 'Enter') {
        if (_pop.idx >= 0) { ev.preventDefault(); ev.stopPropagation(); _pick(_pop.flat[_pop.idx]); }
        else closeProjectPop();
    } else if (ev.key === 'Escape' || ev.key === 'Tab') {
        closeProjectPop();
    }
}

/**
 * 在 root 底下的所有 `[data-proj-pick]`（input／textarea）掛浮層（事件委派，之後 innerHTML 重畫也照用；同一個 root 只掛一次）。
 * cfg.options：() => rows 或 Promise<rows>（每次打開都會問一次，宿主可以回快取）；
 * cfg.value：選到的案要填進格子的字（預設整串 label）；cfg.match：自訂選擇器（預設 input／textarea 的 [data-proj-pick]）。
 */
export function attachProjectPop(root, cfg = {}) {
    const match = cfg.match || 'input[data-proj-pick], textarea[data-proj-pick]';   // 工作紀錄的專案格是會折行的 textarea
    const flag = 'pop_' + match.replace(/[^a-z0-9]/gi, '');     // 同一個 root 可以掛不同欄位（專案、項目），各掛一次
    if (!root || root.dataset[flag]) return;
    root.dataset[flag] = '1';
    _ensureCss();
    // groups：兩段的標題。專案＝進行中／已結案；零用金項目＝常用／其他（owner 2026-09-04：最常用的展開、其他收攏）
    const full = { options: cfg.options || (() => []), value: cfg.value || ((p) => p.label || p.name || ''),
                   groups: cfg.groups || ['進行中', '已結案'] };
    const inputOf = (ev) => (ev.target.closest ? ev.target.closest(match) : null);
    root.addEventListener('keydown', _keydown, true);
    root.addEventListener('focusin', (ev) => { const inp = inputOf(ev); if (inp && !inp.readOnly && !inp.disabled) _open(inp, full); });
    root.addEventListener('input', (ev) => {
        const inp = inputOf(ev); if (!inp || ev._fromPick) return;
        if (inp.dataset.pid && inp.value.trim() !== inp.dataset.pname) { delete inp.dataset.pid; delete inp.dataset.pname; inp.title = ''; }
        if (_pop && _pop.input === inp) { _pop.idx = -1; _render(_pop.rows); } else _open(inp, full);
    });
    root.addEventListener('focusout', (ev) => {
        if (!inputOf(ev)) return;
        setTimeout(() => { if (_pop && document.activeElement !== _pop.input) closeProjectPop(); }, 120);
    });
}
