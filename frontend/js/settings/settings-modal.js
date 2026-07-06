// ─── Settings Modal (extracted from app.js) ─── //

function showInstallModal() {
    document.getElementById('install-modal').classList.remove('hidden');
}

// Global tab-switch function (called from inline onclick on tab buttons)
function switchSettingsTab(tabId, event) {
    document.querySelectorAll('#settingsModal .tab-content').forEach(el => el.style.display = 'none');
    document.querySelectorAll('#settingsModal .tab-btn').forEach(el => el.classList.remove('active'));
    document.getElementById('tab_' + tabId).style.display = 'block';
    (event || window.event).currentTarget.classList.add('active');
    if (tabId === 'user_mgmt' && typeof window._loadUserList === 'function') window._loadUserList();
}

// Wrapped in DOMContentLoaded so modal HTML (placed after this script)
// is fully parsed before we try to bind event listeners.
document.addEventListener('DOMContentLoaded', () => {
    const modal = document.getElementById('settingsModal');

    // ── Load settings when modal opens ───────────────────────
    document.getElementById('btnOpenSettings').addEventListener('click', async () => {
        modal.style.display = 'flex';
        try {
            const res = await fetch('/api/settings/load');
            if (res.ok) {
                const data = await res.json();
                const n = data.notifications || {};
                const t = data.message_templates || {};
                document.getElementById('gchat_webhook').value = n.google_chat_webhook || '';
                document.getElementById('custom_webhook').value = n.custom_webhook_url || '';
                document.getElementById('tpl_backup_success').value = t.backup_success || '';
                document.getElementById('tpl_report_success').value = t.report_success || '';
                document.getElementById('tpl_transcode_success').value = t.transcode_success || '';
                document.getElementById('tpl_concat_success').value = t.concat_success || '';
                document.getElementById('tpl_verify_success').value = t.verify_success || '';
                document.getElementById('tpl_transcribe_success').value = t.transcribe_success || '';
                // ── Load channel toggles ──────────────────────────────────────
                const ch = data.notification_channels || {};
                const tabs = ['backup', 'report', 'transcode', 'concat', 'verify', 'transcribe'];
                tabs.forEach(tab => {
                    const cfg = ch[tab] || { gchat: true };
                    document.querySelectorAll(`.ch-toggle[data-tab="${tab}"]`).forEach(el => {
                        const channel = el.dataset.ch;
                        const isOn = cfg[channel] !== undefined ? cfg[channel] : (channel === 'gchat');
                        el.classList.toggle('on', isOn);
                        el.classList.toggle('off', !isOn);
                    });
                });
            }
        } catch (e) { /* silent fail */ }
    });

    document.getElementById('btnCloseSettings').onclick = () => modal.style.display = 'none';
    document.getElementById('btnCancelSettings').onclick = () => modal.style.display = 'none';

    // ── Save settings ────────────────────────────────────────
    document.getElementById('btnSaveSettings').addEventListener('click', async () => {
        // LINE Notify 服務已終止（2025-03-31），通道與 token 欄位已移除
        const settingsData = {
            notifications: {
                google_chat_webhook: document.getElementById('gchat_webhook').value,
                custom_webhook_url: document.getElementById('custom_webhook').value,
            },
            message_templates: {
                backup_success: document.getElementById('tpl_backup_success').value,
                report_success: document.getElementById('tpl_report_success').value,
                transcode_success: document.getElementById('tpl_transcode_success').value,
                concat_success: document.getElementById('tpl_concat_success').value,
                verify_success: document.getElementById('tpl_verify_success').value,
                transcribe_success: document.getElementById('tpl_transcribe_success').value,
            },
            // ── Channel toggles ──────────────────────────────
            notification_channels: Object.fromEntries(
                ['backup', 'report', 'transcode', 'concat', 'verify', 'transcribe'].map(tab => [
                    tab,
                    {
                        gchat: document.querySelector(`.ch-toggle.gchat[data-tab="${tab}"]`)?.classList.contains('on') ?? true,
                    }
                ])
            ),
        };
        try {
            const response = await fetch('/api/settings/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settingsData)
            });
            if (response.ok) {
                alert('✅ 設定已成功儲存！');
                modal.style.display = 'none';
            } else {
                alert('❌ 儲存失敗，請檢查伺服器連線。');
            }
        } catch (error) {
            console.error('儲存設定發生錯誤:', error);
            alert('❌ 儲存發生例外錯誤！');
        }
    });
    // ── Restart Agent（已移至下拉選單 window._restartAgent）──
    // ── Channel toggle pills ────────────────────────────────
    document.getElementById('settingsModal').addEventListener('click', e => {
        const t = e.target.closest('.ch-toggle');
        if (!t) return;
        t.classList.toggle('on');
        t.classList.toggle('off');
    });
});

// Expose on window
window.showInstallModal = showInstallModal;
window.switchSettingsTab = switchSettingsTab;
