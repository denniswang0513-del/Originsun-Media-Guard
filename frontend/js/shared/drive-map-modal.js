// drive-map-modal.js — 磁碟代號 ↔ UNC 對應設定 modal（全軟體層級）
//
// 2026-07-22 自 backup tab 搬入右上角使用者選單（owner：這是全軟體功能不該綁備份頁）。
// 後端：GET /api/v1/drive_map（有效表+預設表）、POST 同路徑存完整期望表（admin）。
// 任務進件（enqueue/同步端點）自動依此表把 T:\ 等翻成 UNC — 見 core/drive_map.py。

function _esc(s) {
    return String(s ?? '').replace(/</g, '&lt;').replace(/"/g, '&quot;');
}

function _rowHtml(letter = '', unc = '') {
    return `
        <div class="flex gap-2 items-center dm-row">
            <input type="text" maxlength="1" value="${_esc(letter)}" placeholder="T"
                class="dm-letter w-12 text-center bg-[#1e1e1e] border border-[#555] rounded px-2 py-1.5 text-sm uppercase focus:border-blue-500">
            <span class="text-gray-500 text-sm">:\\</span>
            <span class="text-gray-500">=</span>
            <input type="text" value="${_esc(unc)}" placeholder="\\\\192.168.1.132\\ShareName"
                class="dm-unc flex-1 bg-[#1e1e1e] border border-[#555] rounded px-2 py-1.5 text-sm focus:border-blue-500">
            <button type="button" class="dm-del text-red-400 hover:text-red-300 font-bold px-2">X</button>
        </div>`;
}

function _ensureModal() {
    let modal = document.getElementById('drivemap_modal');
    if (modal) return modal;
    modal = document.createElement('div');
    modal.id = 'drivemap_modal';
    modal.className = 'hidden fixed inset-0 z-[95]';
    modal.style.background = 'rgba(0,0,0,.65)';
    modal.innerHTML = `
        <div class="max-w-2xl mx-auto mt-[8vh] bg-[#222] border border-[#444] rounded-lg p-5 shadow-2xl">
            <div class="flex justify-between items-center mb-1">
                <h3 class="text-sm font-semibold text-gray-200">磁碟對應設定</h3>
                <button id="dm_close" class="text-gray-500 hover:text-gray-200 px-2">✕</button>
            </div>
            <div class="text-[11px] text-gray-500 mb-3 leading-relaxed">
                送出備份/轉檔/串帶等任務時，路徑開頭的磁碟代號會依此表自動翻成 UNC
                （例：T:\\ → \\\\192.168.1.132\\Project_Longterm\\），機器有沒有掛磁碟都不影響。
                不在表內的字母（本機碟、讀卡槽）不受影響。預設對應隨版本統一下發；此處修改儲存於本機。
            </div>
            <div id="dm_rows" class="flex flex-col gap-2 max-h-[46vh] overflow-y-auto pr-1"></div>
            <div class="flex justify-between items-center mt-4">
                <button id="dm_add"
                    class="bg-[#333] hover:bg-[#444] text-gray-300 border border-[#555] rounded px-3 py-1.5 text-xs transition">+ 新增對應</button>
                <div class="flex gap-2">
                    <button id="dm_cancel"
                        class="bg-transparent text-gray-400 border border-[#555] rounded px-4 py-1.5 text-xs transition hover:text-gray-200">取消</button>
                    <button id="dm_save"
                        class="bg-blue-600 hover:bg-blue-500 text-white rounded px-4 py-1.5 text-xs transition">儲存</button>
                </div>
            </div>
        </div>`;
    document.body.appendChild(modal);

    const close = () => modal.classList.add('hidden');
    modal.querySelector('#dm_close').addEventListener('click', close);
    modal.querySelector('#dm_cancel').addEventListener('click', close);
    modal.addEventListener('click', e => { if (e.target === modal) close(); });
    modal.querySelector('#dm_rows').addEventListener('click', e => {
        if (e.target.classList.contains('dm-del')) e.target.closest('.dm-row')?.remove();
    });
    modal.querySelector('#dm_add').addEventListener('click', () => {
        const rows = modal.querySelector('#dm_rows');
        rows.querySelector('.dm-empty')?.remove();
        rows.insertAdjacentHTML('beforeend', _rowHtml());
    });
    modal.querySelector('#dm_save').addEventListener('click', _save);
    return modal;
}

export async function openDriveMapModal() {
    const modal = _ensureModal();
    const rows = modal.querySelector('#dm_rows');
    rows.innerHTML = '<div class="dm-empty text-xs text-gray-500">載入中...</div>';
    modal.classList.remove('hidden');
    try {
        const res = await fetch('/api/v1/drive_map');
        const data = await res.json();
        const map = data.map || {};
        const letters = Object.keys(map).sort();
        rows.innerHTML = letters.map(l => _rowHtml(l, map[l])).join('')
            || '<div class="dm-empty text-xs text-gray-500">尚無對應 — 按「+ 新增對應」</div>';
    } catch (err) {
        rows.innerHTML = `<div class="dm-empty text-xs text-red-400">載入失敗：${_esc(err.message)}</div>`;
    }
}

async function _save() {
    const modal = document.getElementById('drivemap_modal');
    const map = {};
    for (const row of modal.querySelectorAll('#dm_rows .dm-row')) {
        const letter = row.querySelector('.dm-letter').value.trim().toUpperCase();
        const unc = row.querySelector('.dm-unc').value.trim();
        if (!letter && !unc) continue;   // 空列忽略
        if (!/^[A-Z]$/.test(letter)) { alert(`磁碟代號需為單一英文字母：「${letter}」`); return; }
        if (!unc.startsWith('\\\\')) { alert(`${letter}: 的對應需為 \\\\ 開頭的 UNC 路徑`); return; }
        map[letter] = unc;
    }
    try {
        const res = await fetch('/api/v1/drive_map', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(window._authToken ? { 'Authorization': 'Bearer ' + window._authToken } : {}),
            },
            body: JSON.stringify({ drive_map: map }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { alert('儲存失敗：' + (data.detail || res.status)); return; }
        modal.classList.add('hidden');
        if (typeof window.appendLog === 'function') {
            window.appendLog(`磁碟對應已更新（${Object.keys(map).length} 組）`, 'system');
        }
    } catch (err) {
        alert('儲存失敗：' + err.message);
    }
}

// 右上角使用者選單（auth-state.js 非 module）由此呼叫
window._openDriveMap = openDriveMapModal;
