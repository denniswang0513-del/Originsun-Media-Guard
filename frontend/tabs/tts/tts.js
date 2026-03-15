import { appendLog, pickPath, setupDragAndDrop, setupInputDrop, getAgentBaseUrl, resolveDropPath } from '../../js/shared/utils.js';

let _ttsMode = 'standard';      // 'standard' | 'clone' | 'library'
let _ttsRefAudioPath = '';
let _selectedProfileId = null;
let _xttsReady = false;

let _allEdgeVoices = [];

// ─── Init ────────────────────────────────────────────────
export async function initTtsTab() {
    setTtsMode('standard');
    setupTtsDropzone();
    setupInputDrop('tts_output_dir', 'folder');
    await checkXttsStatus();
    await loadVoiceLibrary();
    await loadTtsVoices();
}

// ─── Mode Switching ───────────────────────────────────────
window.setTtsMode = function(mode) {
    _ttsMode = mode;
    ['standard', 'xtts'].forEach(m => {
        const panel = document.getElementById(`tts_panel_${m}`);
        const btn   = document.getElementById(`tts_mode_btn_${m}`);
        if (!panel || !btn) return;
        panel.classList.toggle('hidden', m !== mode);
        btn.classList.toggle('active-mode', m === mode);
        btn.classList.toggle('text-white', m === mode);
        btn.classList.toggle('text-gray-400', m !== mode);
    });

    // Show XTTS banner only for clone / library
    const banner = document.getElementById('xtts_status_banner');
    if (banner) banner.classList.toggle('hidden', mode === 'standard');

    const profileInfo = document.getElementById('tts_selected_profile_info');
    if (profileInfo) profileInfo.classList.toggle('hidden', mode === 'standard');
};

// ─── Advanced Voice Filtering ──────────────────────────────────
const LANG_NAMES = {
    'af': '南非語 Afrikaans', 'am': '阿姆哈拉語 Amharic', 'ar': '阿拉伯語 Arabic', 'az': '亞塞拜然語 Azerbaijani',
    'bg': '保加利亞語 Bulgarian', 'bn': '孟加拉語 Bangla', 'bs': '波士尼亞語 Bosnian',
    'ca': '加泰語 Catalan', 'cs': '捷克語 Czech', 'cy': '威爾斯語 Welsh',
    'da': '丹麥語 Danish', 'de': '德語 German', 'el': '希臘語 Greek',
    'en': '英文 English', 'es': '西班牙語 Spanish', 'et': '愛沙尼亞語 Estonian',
    'fa': '波斯語 Persian', 'fi': '芬蘭語 Finnish', 'fil': '菲律賓語 Filipino',
    'fr': '法語 French', 'ga': '愛爾蘭語 Irish', 'gl': '加利西亞語 Galician',
    'gu': '古吉拉特語 Gujarati', 'he': '希伯來語 Hebrew', 'hi': '印地語 Hindi',
    'hr': '克羅埃西亞語 Croatian', 'hu': '匈牙利語 Hungarian', 'hy': '亞美尼亞語 Armenian',
    'id': '印尼語 Indonesian', 'is': '冰島語 Icelandic', 'it': '義大利語 Italian',
    'ja': '日文 Japanese', 'jv': '爪哇語 Javanese', 'ka': '格魯吉亞語 Georgian',
    'kk': '哈薩克語 Kazakh', 'km': '高棉語 Khmer', 'kn': '卡納達語 Kannada',
    'ko': '韓文 Korean', 'lo': '寮國語 Lao', 'lt': '立陶宛語 Lithuanian',
    'lv': '拉脫維亞語 Latvian', 'mk': '馬其頓語 Macedonian', 'ml': '馬拉雅拉姆語 Malayalam',
    'mn': '蒙古語 Mongolian', 'mr': '馬拉地語 Marathi', 'ms': '馬來語 Malay',
    'mt': '馬耳他語 Maltese', 'my': '緬甸語 Burmese', 'nb': '挪威語 Norwegian',
    'ne': '尼泊爾語 Nepali', 'nl': '荷蘭語 Dutch', 'pa': '旁遮普語 Punjabi',
    'pl': '波蘭語 Polish', 'ps': '普什圖語 Pashto', 'pt': '葡萄牙語 Portuguese',
    'ro': '羅馬尼亞語 Romanian', 'ru': '俄語 Russian', 'si': '僧伽羅語 Sinhala',
    'sk': '斯洛伐克語 Slovak', 'sl': '斯洛文尼亞語 Slovenian', 'so': '索馬利語 Somali',
    'sq': '阿爾巴尼亞語 Albanian', 'sr': '塞爾維亞語 Serbian', 'su': '巽他語 Sundanese',
    'sv': '瑞典語 Swedish', 'sw': '斯瓦希里語 Swahili', 'ta': '坦米爾語 Tamil',
    'te': '泰盧固語 Telugu', 'th': '泰語 Thai', 'tr': '土耳其語 Turkish',
    'uk': '烏克蘭語 Ukrainian', 'ur': '烏爾都語 Urdu', 'uz': '烏茲別克語 Uzbek',
    'vi': '越南語 Vietnamese', 'zh': '中文 Chinese', 'zu': '祖魯語 Zulu',
};

const REGION_NAMES = {
    // A-C
    'AE': '阿聯 UAE', 'AF': '阿富汗 AF', 'AL': '阿爾巴尼亞 AL', 'AM': '亞美尼亞 AM', 'AR': '阿根廷 AR', 'AT': '奧地利 AT', 'AU': '澳洲 AU', 'AZ': '亞塞拜然 AZ',
    'BA': '波士尼亞 BA', 'BD': '孟加拉 BD', 'BE': '比利時 BE', 'BG': '保加利亞 BG', 'BH': '巴林 BH', 'BO': '玻利維亞 BO', 'BR': '巴西 BR',
    'CA': '加拿大 CA', 'CH': '瑞士 CH', 'CL': '智利 CL', 'CN': '中國 CN', 'CO': '哥倫比亞 CO', 'CR': '哥斯大黎加 CR', 'CU': '古巴 CU', 'CZ': '捷克 CZ',
    // D-G
    'DE': '德國 DE', 'DK': '丹麥 DK', 'DO': '多明尼加 DO', 'DZ': '阿爾及利亞 DZ',
    'EC': '厄瓜多 EC', 'EE': '愛沙尼亞 EE', 'EG': '埃及 EG', 'ES': '西班牙 ES', 'ET': '衣索比亞 ET',
    'FI': '芬蘭 FI', 'FR': '法國 FR',
    'GB': '英國 GB', 'GE': '喬治亞 GE', 'GQ': '赤道幾內亞 GQ', 'GR': '希臘 GR', 'GT': '瓜地馬拉 GT',
    // H-L
    'HK': '香港 HK', 'HN': '宏都拉斯 HN', 'HR': '克羅埃西亞 HR', 'HU': '匈牙利 HU',
    'ID': '印尼 ID', 'IE': '愛爾蘭 IE', 'IL': '以色列 IL', 'IN': '印度 IN', 'IQ': '伊拉克 IQ', 'IR': '伊朗 IR', 'IS': '冰島 IS', 'IT': '義大利 IT',
    'JO': '約旦 JO', 'JP': '日本 JP',
    'KE': '肯亞 KE', 'KH': '柬埔寨 KH', 'KR': '韓國 KR', 'KW': '科威特 KW', 'KZ': '哈薩克 KZ',
    'LA': '寮國 LA', 'LK': '斯里蘭卡 LK', 'LT': '立陶宛 LT', 'LV': '拉脫維亞 LV', 'LY': '利比亞 LY',
    // M-P
    'MA': '摩洛哥 MA', 'MK': '馬其頓 MK', 'MM': '緬甸 MM', 'MN': '蒙古 MN', 'MT': '馬爾他 MT', 'MX': '墨西哥 MX', 'MY': '馬來西亞 MY',
    'NE': '尼泊爾 NE', 'NG': '奈及利亞 NG', 'NI': '尼加拉瓜 NI', 'NL': '荷蘭 NL', 'NO': '挪威 NO', 'NZ': '紐西蘭 NZ',
    'OM': '阿曼 OM',
    'PA': '巴拿馬 PA', 'PE': '秘魯 PE', 'PH': '菲律賓 PH', 'PK': '巴基斯坦 PK', 'PL': '波蘭 PL', 'PR': '波多黎各 PR', 'PS': '巴勒斯坦 PS', 'PT': '葡萄牙 PT', 'PY': '巴拉圭 PY',
    // Q-Z
    'QA': '卡達 QA',
    'RO': '羅馬尼亞 RO', 'RS': '塞爾維亞 RS', 'RU': '俄羅斯 RU',
    'SA': '沙烏地 SA', 'SE': '瑞典 SE', 'SG': '新加坡 SG', 'SI': '斯洛維尼亞 SI', 'SK': '斯洛伐克 SK', 'SO': '索馬利亞 SO', 'SV': '薩爾瓦多 SV', 'SY': '敘利亞 SY',
    'TH': '泰國 TH', 'TN': '突尼西亞 TN', 'TR': '土耳其 TR', 'TW': '台灣 TW', 'TZ': '坦尚尼亞 TZ',
    'UA': '烏克蘭 UA', 'US': '美國 US', 'UY': '烏拉圭 UY', 'UZ': '烏茲別克 UZ',
    'VE': '委內瑞拉 VE', 'VN': '越南 VN',
    'YE': '葉門 YE',
    'ZA': '南非 ZA', 'ZU': '祖魯 ZU',
};

async function loadTtsVoices() {
    try {
        const res = await fetch(getAgentBaseUrl() + '/api/v1/tts/voices');
        const data = await res.json();
        _allEdgeVoices = data.voices || [];
        
        const langs = new Set();
        _allEdgeVoices.forEach(v => {
            const lang = v.Locale.split('-')[0];
            langs.add(lang);
        });
        
        const langSelect = document.getElementById('tts_filter_lang');
        if (langSelect) {
            langSelect.innerHTML = '<option value="all">全部語系 (All)</option>';
            const priority = ['zh', 'en', 'ja', 'ko'];
            Array.from(langs).sort((a, b) => {
                const idxA = priority.indexOf(a);
                const idxB = priority.indexOf(b);
                if (idxA !== -1 && idxB !== -1) return idxA - idxB;
                if (idxA !== -1) return -1;
                if (idxB !== -1) return 1;
                return a.localeCompare(b);
            }).forEach(lang => {
                const label = LANG_NAMES[lang] || lang.toUpperCase();
                const code = lang.toUpperCase();
                langSelect.innerHTML += `<option value="${lang}">${label} ${code}</option>`;
            });
            if (langs.has('zh')) langSelect.value = 'zh';
        }
        
        window.updateTtsFilterRegions();
        window.updateTtsVoices();
    } catch (e) {
        console.error("Failed to load TTS voices:", e);
        const voiceSelect = document.getElementById('tts_voice');
        if (voiceSelect) voiceSelect.innerHTML = '<option value="zh-TW-HsiaoChenNeural">台灣 小晨 (HsiaoChen) — 女聲</option>';
    }
}

window.updateTtsFilterRegions = function() {
    const lang = document.getElementById('tts_filter_lang')?.value || 'all';
    const regionSelect = document.getElementById('tts_filter_region');
    if (!regionSelect) return;
    
    regionSelect.innerHTML = '<option value="all">全部區域</option>';
    if (lang === 'all') return;
    
    const regions = new Set();
    _allEdgeVoices.forEach(v => {
        const parts = v.Locale.split('-');
        if (parts[0] === lang && parts.length > 1) {
            regions.add(parts[1]);
        }
    });
    
    Array.from(regions).sort().forEach(r => {
        const label = REGION_NAMES[r] || r;
        regionSelect.innerHTML += `<option value="${r}">${label}</option>`;
    });
};

window.updateTtsVoices = function() {
    const lang = document.getElementById('tts_filter_lang')?.value || 'all';
    const region = document.getElementById('tts_filter_region')?.value || 'all';
    const gender = document.getElementById('tts_filter_gender')?.value || 'all';
    
    const voiceSelect = document.getElementById('tts_voice');
    if (!voiceSelect) return;
    
    let filtered = _allEdgeVoices;
    if (lang !== 'all') filtered = filtered.filter(v => v.Locale.startsWith(lang + '-'));
    if (region !== 'all') filtered = filtered.filter(v => v.Locale.endsWith('-' + region));
    if (gender !== 'all') filtered = filtered.filter(v => v.Gender === gender);
    
    voiceSelect.innerHTML = '';
    
    if (filtered.length === 0) {
        voiceSelect.innerHTML = '<option value="">(無符合條件的聲音)</option>';
        return;
    }
    
    filtered.forEach(v => {
        // Clean up FriendlyName: strip Microsoft prefix and language/country suffix
        const rawName = v.FriendlyName || v.ShortName;
        const shortVoiceName = rawName
            .replace(/^Microsoft\s+/, '')
            .replace(/\s+Online\s+\(Natural\).*$/, '')
            .trim();
        const regionCode = v.Locale.split('-')[1] || '';
        const regionLabel = REGION_NAMES[regionCode] || regionCode;
        const genderCh = v.Gender === 'Female' ? '女聲' : '男聲';
        voiceSelect.innerHTML += `<option value="${v.ShortName}">${regionLabel} - ${shortVoiceName} (${genderCh})</option>`;
    });
    
    // Auto-select Taiwan voice if available
    const twVoice = filtered.find(v => v.Locale === 'zh-TW');
    if (twVoice) voiceSelect.value = twVoice.ShortName;
};

window.previewTtsVoice = function() {
    const voice = document.getElementById('tts_voice')?.value;
    if (!voice) return;
    
    const btn = document.getElementById('btn_tts_preview');
    if (btn) { btn.disabled = true; btn.textContent = '載入中'; }
    
    const audio = new Audio(getAgentBaseUrl() + `/api/v1/tts/preview?voice=${voice}&text=${encodeURIComponent("您好，這是語音試聽。")}`);
    audio.play().then(() => {
        if (btn) btn.textContent = '播放中';
        audio.onended = () => { if (btn) { btn.textContent = '試聽'; btn.disabled = false; } };
    }).catch(e => {
        alert("試聽失敗: " + e.message);
        if (btn) { btn.textContent = '試聽'; btn.disabled = false; }
    });
};

// ─── Duration Estimation ──────────────────────────────────
window.calculateTtsDuration = async function() {
    const textInput = document.getElementById('tts_text_input');
    const btn = document.getElementById('tts_calc_btn');
    const durSpan = document.getElementById('tts_est_duration');
    const rateSpan = document.getElementById('tts_est_rate');
    
    const text = textInput ? textInput.value.trim() : '';
    if (!text) {
        alert('請先輸入你要合成的文字！');
        return;
    }

    const voice = document.getElementById('tts_voice')?.value || 'zh-TW-HsiaoChenNeural';
    const rateUrl = document.getElementById('tts_rate')?.value || '0';
    const pitchUrl = document.getElementById('tts_pitch')?.value || '0';
    
    const rateStr = (rateUrl >= 0 ? '+' : '') + rateUrl + '%';
    const pitchStr = (pitchUrl >= 0 ? '+' : '') + pitchUrl + 'Hz';

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<svg class="animate-spin -ml-1 mr-2 h-3.5 w-3.5 text-white" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg> 計算中...`;
    }

    try {
        const payload = { text, voice, rate: rateStr, pitch: pitchStr };
        const res = await fetch(getAgentBaseUrl() + '/api/v1/tts/estimate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        
        // Format MM:SS.f
        const totalSecs = data.duration_seconds || 0;
        const mins = Math.floor(totalSecs / 60);
        const secs = Math.floor(totalSecs % 60);
        const ms = Math.floor((totalSecs - Math.floor(totalSecs)) * 10); // get 1 decimal
        const formattedDur = `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}.${ms}`;
        
        if (durSpan) durSpan.textContent = formattedDur;
        if (rateSpan) rateSpan.textContent = `${data.chars_per_second} 字/秒`;
        
    } catch (e) {
        console.error("Estimation failed:", e);
        if (durSpan) durSpan.textContent = "Error";
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg> 計算音檔時長`;
        }
    }
};

// ─── XTTS Model Status ────────────────────────────────────
async function checkXttsStatus() {
    try {
        const res = await fetch(getAgentBaseUrl() + '/api/v1/tts/xtts_status');
        const data = await res.json();
        const statusText = document.getElementById('xtts_status_text');
        const dlBtn = document.getElementById('btn_download_xtts');
        _xttsReady = data.ready === true;
        if (_xttsReady) {
            if (statusText) statusText.textContent = 'XTTS v2 模型已就緒';
            if (dlBtn) dlBtn.classList.add('hidden');
        } else {
            if (statusText) statusText.textContent = 'XTTS v2 模型未下載（約 2GB，僅克隆模式需要）';
            if (dlBtn) dlBtn.classList.remove('hidden');
        }
    } catch {
        const statusText = document.getElementById('xtts_status_text');
        if (statusText) statusText.textContent = '無法取得 XTTS 狀態（後端可能尚未啟動）';
    }
}

window.downloadXttsModel = async function() {
    const btn = document.getElementById('btn_download_xtts');
    if (btn) { btn.disabled = true; btn.textContent = '下載中...'; }
    const statusText = document.getElementById('xtts_status_text');
    if (statusText) statusText.textContent = '下載中，請稍候（可能需要數分鐘）...';
    try {
        await fetch(getAgentBaseUrl() + '/api/v1/tts/xtts_download', { method: 'POST' });
        if (statusText) statusText.textContent = '下載完成！XTTS v2 已就緒';
        if (btn) { btn.classList.add('hidden'); }
        _xttsReady = true;
    } catch {
        if (statusText) statusText.textContent = '下載失敗，請確認網路並重試';
        if (btn) { btn.disabled = false; btn.textContent = '下載模型 (~2GB)'; }
    }
};

// ─── Reference Audio (Clone Mode) ────────────────────────
async function setupTtsDropzone() {
    const zone = document.getElementById('tts_clone_dropzone');
    if (!zone) return;
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('border-blue-500'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('border-blue-500'));
    zone.addEventListener('drop', async e => {
        e.preventDefault();
        zone.classList.remove('border-blue-500');
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            const path = await resolveDropPath(e, files[0]);
            if (path) setTtsRefAudioPath(path);
        }
    });
}

// Native file picker for reference audio
window.pickTtsReferenceNative = async function() {
    // We use pickPath from utils.js which triggers the native dialog
    await pickPath('tts_ref_audio_path_display', 'file');
    const path = document.getElementById('tts_ref_audio_path_display')?.value;
    if (path) {
        setTtsRefAudioPath(path);
    }
};

window.pickTtsReference = function() {
    // Keep this as fallback or for drag-drop logic
    let inputEl = document.getElementById('_tts_ref_input');
    if (!inputEl) {
        inputEl = document.createElement('input');
        inputEl.type = 'file';
        inputEl.id = '_tts_ref_input';
        inputEl.accept = '.wav,.mp3,.m4a,.aac,.ogg,.flac';
        inputEl.style.display = 'none';
        document.body.appendChild(inputEl);
        inputEl.addEventListener('change', async () => {
            const file = inputEl.files?.[0];
            if (file) {
                const path = await resolveDropPath(null, file);
                setTtsRefAudioPath(path);
            }
        });
    }
    inputEl.click();
};

function setTtsRefAudioPath(path) {
    _ttsRefAudioPath = path;
    const name = path.split(/[\\/]/).pop();
    
    // Sync Display Input
    const displayEl = document.getElementById('tts_ref_audio_path_display');
    if (displayEl) displayEl.value = path;

    const info = document.getElementById('tts_ref_info');
    const nameEl = document.getElementById('tts_ref_name');
    if (info) info.classList.remove('hidden');
    if (nameEl) {
        nameEl.textContent = ` ${name}`;
        nameEl.title = path;
    }
    
    // UI logic: selecting a local reference clears the library selection
    _selectedProfileId = null;
    document.getElementById('tts_selected_profile_info')?.classList.add('hidden');
}

window.clearTtsRef = function() {
    _ttsRefAudioPath = '';
    const displayEl = document.getElementById('tts_ref_audio_path_display');
    if (displayEl) displayEl.value = '';
    document.getElementById('tts_ref_info')?.classList.add('hidden');
};

// ─── Voice Library ────────────────────────────────────────
async function loadVoiceLibrary() {
    const list = document.getElementById('tts_library_list');
    if (!list) return;
    try {
        const res = await fetch(getAgentBaseUrl() + '/api/v1/voice_profiles');
        const profiles = await res.json();
        if (!profiles.length) {
            list.innerHTML = '<p class="text-gray-500 text-sm text-center py-4">NAS 聲音庫是空的，先用「聲音克隆」模式建立第一個角色。</p>';
            return;
        }
        list.innerHTML = profiles.map(p => `
          <div class="flex items-center justify-between bg-[#1e1e1e] border border-[#3a3a3a] rounded-lg px-3 py-2 text-sm">
            <div>
              <span class="font-semibold text-white">${p.name}</span>
              <span class="text-gray-500 text-xs ml-2">${p.description || ''}</span>
              ${p.cached_locally ? '<span class="ml-2 text-xs text-green-400">已快取</span>' : ''}
            </div>
            <div class="flex gap-2">
              ${!p.cached_locally ? `<button onclick="cacheProfile('${p.id}')" class="bg-[#1f538d] hover:bg-[#2a6cbf] text-white text-xs py-1 px-2 rounded transition-colors">快取至本機</button>` : ''}
              <button onclick="selectProfile('${p.id}','${p.name}')" class="bg-[#333] hover:bg-[#444] text-white text-xs py-1 px-2 rounded transition-colors">選用</button>
              <button onclick="deleteProfile('${p.id}')" class="text-red-400 hover:text-red-300 text-xs px-1">刪除</button>
            </div>
          </div>`).join('');
    } catch {
        list.innerHTML = '<p class="text-red-400 text-sm text-center py-4">無法連線至 NAS 聲音庫</p>';
    }
}

window.selectProfile = function(id, name) {
    _selectedProfileId = id;
    const info = document.getElementById('tts_selected_profile_info');
    const nameEl = document.getElementById('tts_selected_profile_name');
    if (info) info.classList.remove('hidden');
    if (nameEl) nameEl.textContent = name;
    
    // UI logic: selecting a library profile clears the local reference file
    clearTtsRef();
};

window.cacheProfile = async function(id) {
    try {
        await fetch(getAgentBaseUrl() + `/api/v1/voice_profiles/${id}/cache`, { method: 'POST' });
        await loadVoiceLibrary();
    } catch(e) { alert('快取失敗: ' + e.message); }
};

window.deleteProfile = async function(id) {
    if (!confirm('確定要刪除這個聲音角色嗎？')) return;
    try {
        await fetch(getAgentBaseUrl() + `/api/v1/voice_profiles/${id}`, { method: 'DELETE' });
        await loadVoiceLibrary();
    } catch(e) { alert('刪除失敗: ' + e.message); }
};

window.saveTtsToLibrary = async function() {
    if (!_ttsRefAudioPath) { alert('請先選取參考音訊'); return; }
    const name = prompt('請為這個聲音角色命名：');
    if (!name) return;
    const desc = prompt('描述（可留空）：') || '';
    try {
        const res = await fetch(getAgentBaseUrl() + '/api/v1/voice_profiles', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, description: desc, reference_audio: _ttsRefAudioPath, language: 'zh' })
        });
        const data = await res.json();
        if (data.id) {
            alert(`已存入 NAS 聲音庫：${name}`);
            await loadVoiceLibrary();
        }
    } catch(e) { alert('儲存失敗: ' + e.message); }
};

window.addVoiceToLibrary = function() {
    pickTtsReference();
};

// ─── Output Dir ───────────────────────────────────────────
window.pickTtsOutputDir = function() {
    pickPath('tts_output_dir', 'folder');
};

// ─── Submit Job ───────────────────────────────────────────
window.submitTtsJob = async function() {
    const text = document.getElementById('tts_text_input')?.value?.trim();
    const outputDir = document.getElementById('tts_output_dir')?.value?.trim();
    const outputName = document.getElementById('tts_output_name')?.value?.trim() || 'tts_output';
    
    // progress elements
    const progArea = document.getElementById('tts_progress_area');
    const progLabel = document.getElementById('tts_prog_label');
    const progBar = document.getElementById('tts_prog_bar');
    const progPct = document.getElementById('tts_prog_pct');

    if (!text) { alert('請輸入要合成的文字'); return; }
    if (!outputDir) { alert('請選擇輸出目錄'); return; }

    let endpoint = getAgentBaseUrl() + '/api/v1/tts_jobs';
    let payload = { text, output_dir: outputDir, output_name: outputName };

    if (_ttsMode === 'standard') {
        payload.voice = document.getElementById('tts_voice')?.value;
        payload.rate  = parseInt(document.getElementById('tts_rate')?.value || '0');
        payload.pitch = parseInt(document.getElementById('tts_pitch')?.value || '0');
    } else if (_ttsMode === 'xtts') {
        if (!_xttsReady) { alert('XTTS v2 模型尚未下載，請先下載模型'); return; }
        
        if (_ttsRefAudioPath) {
            endpoint = getAgentBaseUrl() + '/api/v1/tts_jobs/clone';
            payload.reference_audio = _ttsRefAudioPath;
        } else if (_selectedProfileId) {
            endpoint = getAgentBaseUrl() + '/api/v1/tts_jobs/profile';
            payload.profile_id = _selectedProfileId;
        } else {
            alert('請先在左側拖曳參考音訊，或在右側選取 NAS 庫存聲音');
            return;
        }
    }

    // Start UI Progress Simulation
    if (progArea) progArea.classList.remove('hidden');
    if (progLabel) progLabel.textContent = '正在產生語音...';
    if (progBar) {
        progBar.style.width = '2%';
        progBar.style.background = 'linear-gradient(90deg, #3b82f6, #60a5fa)';
    }
    if (progPct) progPct.textContent = '2%';

    let currentPct = 2;
    const progressInterval = setInterval(() => {
        // Asymptotic curve growing to 95%
        if (currentPct < 95) {
            currentPct += (99 - currentPct) * 0.05;
            const displayPct = Math.round(currentPct);
            if (progBar) progBar.style.width = displayPct + '%';
            if (progPct) progPct.textContent = displayPct + '%';
        }
    }, 500);

    const btn = document.getElementById('btn_start_tts');
    if (btn) { btn.disabled = true; btn.classList.add('opacity-50', 'cursor-not-allowed'); }

    try {
        const res = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        
        clearInterval(progressInterval);
        
        if (res.ok) {
            if (progBar) {
                progBar.style.width = '100%';
                progBar.style.background = 'linear-gradient(90deg, #059669, #10b981)'; // Green success
            }
            if (progPct) progPct.textContent = '100%';
            if (progLabel) {
                const outName = data.output ? data.output.split(/[\\/]/).pop() : outputName;
                progLabel.innerHTML = `完成！儲存至: <span class="text-green-400 font-mono">${outName}</span>`;
            }
        } else {
            throw new Error(data.detail || '未知的後端錯誤');
        }
    } catch(e) {
        clearInterval(progressInterval);
        if (progBar) {
            progBar.style.width = '100%';
            progBar.style.background = 'linear-gradient(90deg, #ef4444, #f87171)'; // Red error
        }
        if (progPct) progPct.textContent = 'Err';
        if (progLabel) progLabel.textContent = `失敗：${e.message}`;
    } finally {
        if (btn) { btn.disabled = false; btn.classList.remove('opacity-50', 'cursor-not-allowed'); }
    }
};
