// ============= Panel JS =============
// Requires GUILD_ID and SETTINGS_SCHEMA to be defined by the template.

let currentValues = {};
let channelsCache = null;
let rolesCache = null;

// ---- Data fetching ----

async function fetchJSON(url) {
    const res = await fetch(url);
    if (!res.ok) {
        if (res.status === 401) { window.location.href = '/panel/login'; return null; }
        throw new Error(`HTTP ${res.status}`);
    }
    return res.json();
}

async function loadChannels() {
    if (channelsCache) return channelsCache;
    channelsCache = await fetchJSON(`/api/panel/guild/${GUILD_ID}/channels`);
    return channelsCache;
}

async function loadRoles() {
    if (rolesCache) return rolesCache;
    rolesCache = await fetchJSON(`/api/panel/guild/${GUILD_ID}/roles`);
    return rolesCache;
}

async function loadSettings() {
    currentValues = await fetchJSON(`/api/panel/guild/${GUILD_ID}/settings`);
    return currentValues;
}

// ---- Saving ----

let saveTimers = {};

function debounceSave(module, key, value, delay = 600) {
    const id = `${module}::${key}`;
    clearTimeout(saveTimers[id]);
    setIndicator(id, 'saving', '儲存中...');
    saveTimers[id] = setTimeout(() => doSave(module, key, value), delay);
}

async function doSave(module, key, value) {
    const id = `${module}::${key}`;
    try {
        const res = await fetch(`/api/panel/guild/${GUILD_ID}/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ module, key, value }),
        });
        const data = await res.json();
        if (data.success) {
            setIndicator(id, 'saved', '✓ 已儲存');
            // Update local cache
            if (!currentValues[module]) currentValues[module] = {};
            currentValues[module][key] = data.value;
        } else {
            setIndicator(id, 'error', '✗ ' + (data.error || '保存失敗'));
            showToast(data.error || '保存失敗', 'error');
        }
    } catch (e) {
        setIndicator(id, 'error', '✗ 網路錯誤');
        showToast('網路錯誤: ' + e.message, 'error');
    }
}

function setIndicator(id, cls, text) {
    const el = document.querySelector(`.save-indicator[data-id="${id}"]`);
    if (!el) return;
    el.className = `save-indicator ${cls}`;
    el.textContent = text;
    if (cls === 'saved') {
        setTimeout(() => { el.className = 'save-indicator'; el.textContent = ''; }, 2500);
    }
}

// ---- Render ----

async function render() {
    const wrapper = document.getElementById('settings-wrapper');
    wrapper.innerHTML = '<div class="loading-spinner">正在載入設定...</div>';

    // Load all data in parallel
    const [settings, channels, roles] = await Promise.all([
        loadSettings(),
        loadChannels(),
        loadRoles(),
    ]);

    if (!settings) { wrapper.innerHTML = '<div class="loading-spinner">載入失敗</div>'; return; }

    wrapper.innerHTML = '';
    const moduleNames = Object.keys(SETTINGS_SCHEMA);

    if (moduleNames.length === 0) {
        wrapper.innerHTML = '<div class="empty-state"><p>沒有可配置的模組</p></div>';
        return;
    }

    for (const mod of moduleNames) {
        const schema = SETTINGS_SCHEMA[mod];
        if (!schema.settings || schema.settings.length === 0) continue;

        const card = document.createElement('div');
        card.className = 'module-card';

        // Header
        const header = document.createElement('div');
        header.className = 'module-header';
        header.innerHTML = `
            <span class="module-icon">${schema.icon || '⚙️'}</span>
            <div style="flex:1">
                <div class="module-title">${schema.display_name}</div>
                ${schema.description ? `<div class="module-desc">${schema.description}</div>` : ''}
            </div>
            <span class="module-chevron">❯</span>
        `;
        header.addEventListener('click', () => card.classList.toggle('open'));
        card.appendChild(header);

        // Body
        const body = document.createElement('div');
        body.className = 'module-body';

        for (const s of schema.settings) {
            const val = settings[mod] ? settings[mod][s.database_key] : s.default;
            const row = buildSettingRow(mod, s, val, channels, roles);
            body.appendChild(row);
        }

        card.appendChild(body);
        wrapper.appendChild(card);
    }

    // Auto-open the first module
    const first = wrapper.querySelector('.module-card');
    if (first) first.classList.add('open');
}

function buildSettingRow(mod, s, value, channels, roles) {
    const row = document.createElement('div');
    row.className = 'setting-row';
    if (s.type === 'autoreply_list' || s.type === 'automod_config' || s.type === 'webverify_config') row.classList.add('setting-row-column');

    const id = `${mod}::${s.database_key}`;

    row.innerHTML = `
        <div class="setting-label-group">
            <div class="setting-label">${s.display}<span class="save-indicator" data-id="${id}"></span></div>
            ${s.description ? `<div class="setting-desc">${s.description}</div>` : ''}
        </div>
        <div class="setting-control" id="ctrl-${CSS.escape(id)}"></div>
    `;

    const ctrl = row.querySelector('.setting-control');

    switch (s.type) {
        case 'channel':
        case 'voice_channel':
        case 'category':
            ctrl.appendChild(buildChannelSelect(mod, s, value, channels));
            break;
        case 'role':
            ctrl.appendChild(buildRoleSelect(mod, s, value, roles));
            break;
        case 'role_list':
            ctrl.appendChild(buildRoleListSelect(mod, s, value, roles));
            break;
        case 'channel_list':
            ctrl.appendChild(buildChannelListSelect(mod, s, value, channels));
            break;
        case 'autoreply_list':
            ctrl.appendChild(buildAutoreplyListEditor(mod, s, value, channels));
            break;
        case 'automod_config':
            ctrl.appendChild(buildAutomodConfigEditor(mod, s, value, channels));
            break;
        case 'webverify_config':
            ctrl.appendChild(buildWebverifyConfigEditor(mod, s, value, channels, roles));
            break;
        case 'boolean':
            ctrl.appendChild(buildToggle(mod, s, value));
            break;
        case 'select':
            ctrl.appendChild(buildSelect(mod, s, value));
            break;
        case 'text':
            ctrl.appendChild(buildTextarea(mod, s, value));
            break;
        case 'number':
        case 'float':
            ctrl.appendChild(buildNumberInput(mod, s, value));
            break;
        case 'string':
        default:
            ctrl.appendChild(buildTextInput(mod, s, value));
            break;
    }

    return row;
}

// ---- Control builders ----

function buildChannelSelect(mod, s, value, channels) {
    const sel = document.createElement('select');
    sel.className = 'form-select';
    sel.innerHTML = '<option value="none">未設定</option>';

    const typeFilter = {
        'channel': ['text', 'news'],
        'voice_channel': ['voice', 'stage_voice'],
        'category': ['category'],
    };
    const allowed = typeFilter[s.type] || [];

    for (const ch of channels) {
        if (allowed.length && !allowed.includes(ch.type)) continue;
        const prefix = ch.category ? `[${ch.category}] ` : '';
        const typeIcon = ch.type === 'voice' ? '🔊 ' : ch.type === 'category' ? '📁 ' : '# ';
        const opt = document.createElement('option');
        opt.value = ch.id;
        opt.textContent = `${typeIcon}${prefix}${ch.name}`;
        if (String(value) === String(ch.id)) opt.selected = true;
        sel.appendChild(opt);
    }

    sel.addEventListener('change', () => debounceSave(mod, s.database_key, sel.value, 100));
    return sel;
}

function buildRoleSelect(mod, s, value, roles) {
    const sel = document.createElement('select');
    sel.className = 'form-select';
    sel.innerHTML = '<option value="none">未設定</option>';

    for (const r of roles) {
        const opt = document.createElement('option');
        opt.value = r.id;
        opt.textContent = `@ ${r.name}`;
        if (String(value) === String(r.id)) opt.selected = true;
        sel.appendChild(opt);
    }

    sel.addEventListener('change', () => debounceSave(mod, s.database_key, sel.value, 100));
    return sel;
}

function buildRoleListSelect(mod, s, value, roles) {
    const selected = Array.isArray(value) ? value.map(String) : [];
    const container = document.createElement('div');
    container.className = 'role-list-container';

    // Tag display area
    const tagsWrap = document.createElement('div');
    tagsWrap.className = 'role-tags';
    container.appendChild(tagsWrap);

    // Add dropdown
    const sel = document.createElement('select');
    sel.className = 'form-select';
    sel.innerHTML = '<option value="">➕ 新增身分組...</option>';
    container.appendChild(sel);

    function renderTags() {
        tagsWrap.innerHTML = '';
        if (selected.length === 0) {
            tagsWrap.innerHTML = '<span class="role-tag-empty">尚未新增任何身分組</span>';
        }
        for (const rid of selected) {
            const role = roles.find(r => String(r.id) === rid);
            const tag = document.createElement('span');
            tag.className = 'role-tag';
            tag.textContent = role ? `@ ${role.name}` : `ID: ${rid}`;
            const removeBtn = document.createElement('button');
            removeBtn.className = 'role-tag-remove';
            removeBtn.textContent = '×';
            removeBtn.addEventListener('click', () => {
                const idx = selected.indexOf(rid);
                if (idx > -1) selected.splice(idx, 1);
                renderTags();
                rebuildOptions();
                debounceSave(mod, s.database_key, [...selected], 100);
            });
            tag.appendChild(removeBtn);
            tagsWrap.appendChild(tag);
        }
    }

    function rebuildOptions() {
        sel.innerHTML = '<option value="">➕ 新增身分組...</option>';
        for (const r of roles) {
            if (selected.includes(String(r.id))) continue;
            const opt = document.createElement('option');
            opt.value = r.id;
            opt.textContent = `@ ${r.name}`;
            sel.appendChild(opt);
        }
    }

    sel.addEventListener('change', () => {
        if (sel.value) {
            selected.push(sel.value);
            renderTags();
            rebuildOptions();
            debounceSave(mod, s.database_key, [...selected], 100);
        }
    });

    renderTags();
    rebuildOptions();
    return container;
}

function buildChannelListSelect(mod, s, value, channels) {
    const selected = Array.isArray(value) ? value.map(String) : [];
    const container = document.createElement('div');
    container.className = 'role-list-container';

    const tagsWrap = document.createElement('div');
    tagsWrap.className = 'role-tags';
    container.appendChild(tagsWrap);

    const sel = document.createElement('select');
    sel.className = 'form-select';
    sel.innerHTML = '<option value="">➕ 新增頻道...</option>';
    container.appendChild(sel);

    const allowedTypes = ['text', 'news'];
    const allowedChannels = channels.filter(ch => allowedTypes.includes(ch.type));

    function renderTags() {
        tagsWrap.innerHTML = '';
        if (selected.length === 0) {
            tagsWrap.innerHTML = '<span class="role-tag-empty">尚未新增任何頻道</span>';
        }
        for (const cid of selected) {
            const ch = channels.find(c => String(c.id) === cid);
            const tag = document.createElement('span');
            tag.className = 'role-tag';
            const prefix = ch && ch.category ? `[${ch.category}] ` : '';
            const typeIcon = ch && (ch.type === 'voice' || ch.type === 'stage_voice') ? '🔊 ' : '# ';
            tag.textContent = ch ? `${typeIcon}${prefix}${ch.name}` : `ID: ${cid}`;
            const removeBtn = document.createElement('button');
            removeBtn.className = 'role-tag-remove';
            removeBtn.textContent = '×';
            removeBtn.addEventListener('click', () => {
                const idx = selected.indexOf(cid);
                if (idx > -1) selected.splice(idx, 1);
                renderTags();
                rebuildOptions();
                debounceSave(mod, s.database_key, [...selected], 100);
            });
            tag.appendChild(removeBtn);
            tagsWrap.appendChild(tag);
        }
    }

    function rebuildOptions() {
        sel.innerHTML = '<option value="">➕ 新增頻道...</option>';
        for (const ch of allowedChannels) {
            if (selected.includes(String(ch.id))) continue;
            const prefix = ch.category ? `[${ch.category}] ` : '';
            const typeIcon = ch.type === 'voice' ? '🔊 ' : ch.type === 'category' ? '📁 ' : '# ';
            const opt = document.createElement('option');
            opt.value = ch.id;
            opt.textContent = `${typeIcon}${prefix}${ch.name}`;
            sel.appendChild(opt);
        }
    }

    sel.addEventListener('change', () => {
        if (sel.value) {
            selected.push(sel.value);
            renderTags();
            rebuildOptions();
            debounceSave(mod, s.database_key, [...selected], 100);
        }
    });

    renderTags();
    rebuildOptions();
    return container;
}

const AUTOREPLY_MODE_OPTIONS = [
    { value: 'contains', label: '包含' },
    { value: 'equals', label: '完全匹配' },
    { value: 'starts_with', label: '開始於' },
    { value: 'ends_with', label: '結束於' },
    { value: 'regex', label: '正規表達式' },
];
const AUTOREPLY_CHANNEL_MODE_OPTIONS = [
    { value: 'all', label: '所有頻道' },
    { value: 'whitelist', label: '白名單' },
    { value: 'blacklist', label: '黑名單' },
];

function buildAutoreplyListEditor(mod, s, value, channels) {
    const list = Array.isArray(value) ? value.map(r => ({
        trigger: Array.isArray(r.trigger) ? r.trigger : (r.trigger ? String(r.trigger).split(',') : []),
        response: Array.isArray(r.response) ? r.response : (r.response ? String(r.response).split(',') : []),
        mode: r.mode || 'contains',
        reply: !!r.reply,
        channel_mode: r.channel_mode || 'all',
        channels: Array.isArray(r.channels) ? r.channels.map(String) : [],
        random_chance: Math.max(1, Math.min(100, parseInt(r.random_chance, 10) || 100)),
    })) : [];

    const container = document.createElement('div');
    container.className = 'autoreply-list-editor';

    function serializeRule(rule) {
        return {
            trigger: rule.trigger,
            response: rule.response,
            mode: rule.mode,
            reply: rule.reply,
            channel_mode: rule.channel_mode,
            channels: rule.channels,
            random_chance: rule.random_chance,
        };
    }

    function save() {
        debounceSave(mod, s.database_key, list.map(serializeRule), 500);
    }

    function buildRuleCard(rule, index) {
        const card = document.createElement('div');
        card.className = 'autoreply-rule-card';

        const triggerInput = document.createElement('input');
        triggerInput.type = 'text';
        triggerInput.className = 'form-input';
        triggerInput.placeholder = '觸發字串，逗號分隔多個';
        triggerInput.value = (rule.trigger || []).join(', ');
        triggerInput.addEventListener('input', () => {
            rule.trigger = triggerInput.value.split(',').map(t => t.trim()).filter(Boolean);
            save();
        });

        const responseInput = document.createElement('input');
        responseInput.type = 'text';
        responseInput.className = 'form-input';
        responseInput.placeholder = '回覆內容，逗號分隔多個（隨機選一）';
        responseInput.value = (rule.response || []).join(', ');
        responseInput.addEventListener('input', () => {
            rule.response = responseInput.value.split(',').map(r => r.trim()).filter(Boolean);
            save();
        });

        const modeSelect = document.createElement('select');
        modeSelect.className = 'form-select';
        AUTOREPLY_MODE_OPTIONS.forEach(opt => {
            const o = document.createElement('option');
            o.value = opt.value;
            o.textContent = opt.label;
            if (rule.mode === opt.value) o.selected = true;
            modeSelect.appendChild(o);
        });
        modeSelect.addEventListener('change', () => { rule.mode = modeSelect.value; save(); });

        const replyWrap = document.createElement('div');
        replyWrap.className = 'toggle-wrapper';
        const replyLabel = document.createElement('label');
        replyLabel.className = 'toggle';
        const replyCheck = document.createElement('input');
        replyCheck.type = 'checkbox';
        replyCheck.checked = rule.reply;
        replyCheck.addEventListener('change', () => { rule.reply = replyCheck.checked; save(); });
        const replySlider = document.createElement('span');
        replySlider.className = 'toggle-slider';
        replyLabel.appendChild(replyCheck);
        replyLabel.appendChild(replySlider);
        replyWrap.appendChild(replyLabel);

        const channelModeSelect = document.createElement('select');
        channelModeSelect.className = 'form-select';
        AUTOREPLY_CHANNEL_MODE_OPTIONS.forEach(opt => {
            const o = document.createElement('option');
            o.value = opt.value;
            o.textContent = opt.label;
            if (rule.channel_mode === opt.value) o.selected = true;
            channelModeSelect.appendChild(o);
        });
        channelModeSelect.addEventListener('change', () => { rule.channel_mode = channelModeSelect.value; save(); });

        const allowedTypes = ['text', 'news'];
        const allowedChannels = channels.filter(ch => allowedTypes.includes(ch.type));
        const channelTagsWrap = document.createElement('div');
        channelTagsWrap.className = 'role-tags role-tags-sm';
        const channelSel = document.createElement('select');
        channelSel.className = 'form-select';
        channelSel.innerHTML = '<option value="">➕ 指定頻道</option>';
        allowedChannels.forEach(ch => {
            if (rule.channels.includes(String(ch.id))) return;
            const opt = document.createElement('option');
            opt.value = ch.id;
            opt.textContent = (ch.category ? `[${ch.category}] ` : '') + ch.name;
            channelSel.appendChild(opt);
        });
        function renderChannelTags() {
            channelTagsWrap.innerHTML = '';
            (rule.channels || []).forEach(cid => {
                const ch = channels.find(c => String(c.id) === cid);
                const tag = document.createElement('span');
                tag.className = 'role-tag';
                tag.textContent = ch ? ch.name : cid;
                const rm = document.createElement('button');
                rm.className = 'role-tag-remove';
                rm.textContent = '×';
                rm.addEventListener('click', () => {
                    rule.channels = rule.channels.filter(id => id !== cid);
                    renderChannelTags();
                    channelSel.innerHTML = '<option value="">➕ 指定頻道</option>';
                    allowedChannels.forEach(ch => {
                        if (rule.channels.includes(String(ch.id))) return;
                        const o = document.createElement('option');
                        o.value = ch.id;
                        o.textContent = (ch.category ? `[${ch.category}] ` : '') + ch.name;
                        channelSel.appendChild(o);
                    });
                    save();
                });
                tag.appendChild(rm);
                channelTagsWrap.appendChild(tag);
            });
        }
        channelSel.addEventListener('change', () => {
            if (channelSel.value) {
                rule.channels = rule.channels || [];
                rule.channels.push(channelSel.value);
                renderChannelTags();
                channelSel.innerHTML = '<option value="">➕ 指定頻道</option>';
                allowedChannels.forEach(ch => {
                    if (rule.channels.includes(String(ch.id))) return;
                    const o = document.createElement('option');
                    o.value = ch.id;
                    o.textContent = (ch.category ? `[${ch.category}] ` : '') + ch.name;
                    channelSel.appendChild(o);
                });
                save();
            }
        });
        renderChannelTags();

        const chanceInput = document.createElement('input');
        chanceInput.type = 'number';
        chanceInput.className = 'form-input';
        chanceInput.min = 1;
        chanceInput.max = 100;
        chanceInput.value = rule.random_chance;
        chanceInput.style.width = '4rem';
        chanceInput.addEventListener('input', () => {
            rule.random_chance = Math.max(1, Math.min(100, parseInt(chanceInput.value, 10) || 100));
            save();
        });

        const deleteBtn = document.createElement('button');
        deleteBtn.type = 'button';
        deleteBtn.className = 'btn-autoreply-remove';
        deleteBtn.textContent = '刪除';
        deleteBtn.addEventListener('click', () => {
            const i = list.indexOf(rule);
            if (i > -1) list.splice(i, 1);
            container.removeChild(card);
            save();
        });

        card.innerHTML = '';
        const row1 = document.createElement('div');
        row1.className = 'autoreply-rule-row';
        row1.appendChild(triggerInput);
        card.appendChild(row1);
        const row2 = document.createElement('div');
        row2.className = 'autoreply-rule-row';
        row2.appendChild(responseInput);
        card.appendChild(row2);
        const row3 = document.createElement('div');
        row3.className = 'autoreply-rule-row autoreply-rule-meta';
        row3.appendChild(document.createTextNode('模式 '));
        row3.appendChild(modeSelect);
        row3.appendChild(document.createTextNode(' 回覆原訊息 '));
        row3.appendChild(replyWrap);
        row3.appendChild(document.createTextNode(' 頻道 '));
        row3.appendChild(channelModeSelect);
        row3.appendChild(channelTagsWrap);
        row3.appendChild(channelSel);
        row3.appendChild(document.createTextNode(' 機率% '));
        row3.appendChild(chanceInput);
        row3.appendChild(deleteBtn);
        card.appendChild(row3);
        return card;
    }

    list.forEach(rule => container.appendChild(buildRuleCard(rule)));

    const addBtn = document.createElement('button');
    addBtn.type = 'button';
    addBtn.className = 'btn-autoreply-add';
    addBtn.textContent = '➕ 新增一則';
    addBtn.addEventListener('click', () => {
        const newRule = {
            trigger: [],
            response: [],
            mode: 'contains',
            reply: false,
            channel_mode: 'all',
            channels: [],
            random_chance: 100,
        };
        list.push(newRule);
        container.insertBefore(buildRuleCard(newRule, list.length - 1), addBtn);
        save();
    });
    container.appendChild(addBtn);

    return container;
}

const AUTOMOD_FEATURES = [
    { id: 'scamtrap', label: '🪤 詐騙陷阱', desc: '蜜罐頻道，在該頻道發訊者自動處置', fields: [
        { key: 'channel_id', label: '陷阱頻道', type: 'channel', default: '' },
        { key: 'action', label: '處置動作', type: 'string', default: 'delete 請不要在此頻道發送訊息。', placeholder: '例: delete 請勿在此發言' },
    ]},
    { id: 'escape_punish', label: '🏃 逃避責任懲處', desc: '禁言期間離開者額外懲處', fields: [
        { key: 'punishment', label: '懲處方式', type: 'select', options: [{ value: 'ban', label: '封禁' }], default: 'ban' },
        { key: 'duration', label: '持續時間 (如 0、7d)', type: 'string', default: '0' },
    ]},
    { id: 'too_many_h1', label: '📢 標題過多', desc: 'Markdown 大標題總字數上限', fields: [
        { key: 'max_length', label: '最大字數', type: 'number', default: '20', min: 1 },
        { key: 'action', label: '處置動作', type: 'string', default: 'warn', placeholder: '例: warn 或 mute 10m' },
    ]},
    { id: 'too_many_emojis', label: '😂 表情符號過多', desc: '單則訊息 emoji 數量上限', fields: [
        { key: 'max_emojis', label: '最大數量', type: 'number', default: '10', min: 1 },
        { key: 'action', label: '處置動作', type: 'string', default: 'warn' },
    ]},
    { id: 'anti_uispam', label: '📲 用戶安裝應用程式濫用', desc: 'User Install 指令觸發頻率', fields: [
        { key: 'max_count', label: '時間內最大觸發次數', type: 'number', default: '5', min: 1 },
        { key: 'time_window', label: '時間窗口 (秒)', type: 'number', default: '60', min: 1 },
        { key: 'action', label: '處置動作', type: 'string', default: 'delete {user}，請勿濫用用戶安裝的應用程式指令。' },
    ]},
    { id: 'anti_raid', label: '🚨 防突襲', desc: '短時間內大量加入偵測', fields: [
        { key: 'max_joins', label: '時間內最大加入數', type: 'number', default: '5', min: 1 },
        { key: 'time_window', label: '時間窗口 (秒)', type: 'number', default: '60', min: 1 },
        { key: 'action', label: '處置動作', type: 'string', default: 'kick 突襲偵測自動封禁' },
    ]},
    { id: 'anti_spam', label: '🔁 防刷頻', desc: '相似訊息刷頻偵測', fields: [
        { key: 'max_messages', label: '最大相似訊息數', type: 'number', default: '5', min: 1 },
        { key: 'time_window', label: '時間窗口 (秒)', type: 'number', default: '30', min: 1 },
        { key: 'similarity', label: '相似度 (%)', type: 'number', default: '75', min: 1, max: 100 },
        { key: 'action', label: '處置動作', type: 'string', default: 'mute 10m 刷頻自動禁言, delete {user}，請勿刷頻。' },
    ]},
];

function buildAutomodConfigEditor(mod, s, value, channels) {
    const config = typeof value === 'object' && value !== null ? { ...value } : {};
    const container = document.createElement('div');
    container.className = 'automod-config-editor';

    function getFeat(featId) {
        if (!config[featId]) config[featId] = { enabled: false };
        return config[featId];
    }

    function save() {
        const out = {};
        for (const k of Object.keys(config)) {
            out[k] = { ...config[k] };
        }
        debounceSave(mod, s.database_key, out, 500);
    }

    function setFeatValue(featId, key, val) {
        getFeat(featId)[key] = val;
        save();
    }

    for (const feat of AUTOMOD_FEATURES) {
        const card = document.createElement('div');
        card.className = 'automod-feature-card';
        const featData = getFeat(feat.id);

        const header = document.createElement('div');
        header.className = 'automod-feature-header';
        const title = document.createElement('span');
        title.className = 'automod-feature-title';
        title.textContent = feat.label;
        const enableWrap = document.createElement('div');
        enableWrap.className = 'toggle-wrapper';
        const enableLabel = document.createElement('label');
        enableLabel.className = 'toggle';
        const enableCheck = document.createElement('input');
        enableCheck.type = 'checkbox';
        enableCheck.checked = !!featData.enabled;
        enableCheck.addEventListener('change', () => {
            featData.enabled = enableCheck.checked;
            save();
        });
        const enableSlider = document.createElement('span');
        enableSlider.className = 'toggle-slider';
        enableLabel.appendChild(enableCheck);
        enableLabel.appendChild(enableSlider);
        enableWrap.appendChild(enableLabel);
        header.appendChild(title);
        header.appendChild(enableWrap);
        card.appendChild(header);

        if (feat.desc) {
            const descEl = document.createElement('div');
            descEl.className = 'automod-feature-desc';
            descEl.textContent = feat.desc;
            card.appendChild(descEl);
        }

        const body = document.createElement('div');
        body.className = 'automod-feature-body';
        for (const field of feat.fields) {
            const row = document.createElement('div');
            row.className = 'automod-feature-field';
            const lab = document.createElement('label');
            lab.className = 'automod-feature-field-label';
            lab.textContent = field.label + '：';
            row.appendChild(lab);
            const cur = featData[field.key] != null ? String(featData[field.key]) : (field.default || '');
            if (field.type === 'channel') {
                const sel = document.createElement('select');
                sel.className = 'form-select';
                sel.innerHTML = '<option value="">未設定</option>';
                const allowed = channels.filter(ch => ['text', 'news'].includes(ch.type));
                for (const ch of allowed) {
                    const opt = document.createElement('option');
                    opt.value = ch.id;
                    opt.textContent = (ch.category ? '[' + ch.category + '] ' : '') + ch.name;
                    if (String(cur) === String(ch.id)) opt.selected = true;
                    sel.appendChild(opt);
                }
                sel.addEventListener('change', () => setFeatValue(feat.id, field.key, sel.value || ''));
                row.appendChild(sel);
            } else if (field.type === 'select') {
                const sel = document.createElement('select');
                sel.className = 'form-select';
                for (const o of (field.options || [])) {
                    const opt = document.createElement('option');
                    opt.value = o.value;
                    opt.textContent = o.label;
                    if (cur === o.value) opt.selected = true;
                    sel.appendChild(opt);
                }
                sel.addEventListener('change', () => setFeatValue(feat.id, field.key, sel.value));
                row.appendChild(sel);
            } else if (field.type === 'number') {
                const input = document.createElement('input');
                input.type = 'number';
                input.className = 'form-input';
                input.value = cur;
                if (field.min != null) input.min = field.min;
                if (field.max != null) input.max = field.max;
                input.style.width = '5rem';
                input.addEventListener('input', () => setFeatValue(feat.id, field.key, input.value));
                row.appendChild(input);
            } else {
                const input = document.createElement('input');
                input.type = 'text';
                input.className = 'form-input';
                input.value = cur;
                input.placeholder = field.placeholder || '';
                input.style.flex = '1';
                input.addEventListener('input', () => setFeatValue(feat.id, field.key, input.value));
                row.appendChild(input);
            }
            body.appendChild(row);
        }
        card.appendChild(body);
        container.appendChild(card);
    }

    return container;
}

function buildWebverifyConfigEditor(mod, s, value, channels, roles) {
    const config = typeof value === 'object' && value !== null ? { ...value } : {};
    if (!config.notify) config.notify = { type: 'dm', channel_id: null, title: '伺服器網頁驗證', message: '請點擊下方按鈕進行網頁驗證：' };
    if (!config.webverify_country_alert) config.webverify_country_alert = { enabled: false, mode: 'blacklist', countries: [], channel_id: null };

    const container = document.createElement('div');
    container.className = 'webverify-config-editor';

    function save() {
        const out = {
            enabled: !!config.enabled,
            captcha_type: config.captcha_type || 'turnstile',
            unverified_role_id: config.unverified_role_id || null,
            autorole_enabled: !!config.autorole_enabled,
            autorole_trigger: (config.autorole_trigger || 'always').toString().trim(),
            min_age: Math.max(0, parseInt(config.min_age, 10) || 7),
            notify: { ...config.notify },
            webverify_country_alert: { ...config.webverify_country_alert },
        };
        debounceSave(mod, s.database_key, out, 500);
    }

    function addRow(labelText, control) {
        const row = document.createElement('div');
        row.className = 'webverify-field-row';
        const lab = document.createElement('label');
        lab.className = 'webverify-field-label';
        lab.textContent = labelText + '：';
        row.appendChild(lab);
        row.appendChild(control);
        return row;
    }

    const enabledWrap = document.createElement('div');
    enabledWrap.className = 'toggle-wrapper';
    const enabledLabel = document.createElement('label');
    enabledLabel.className = 'toggle';
    const enabledCheck = document.createElement('input');
    enabledCheck.type = 'checkbox';
    enabledCheck.checked = !!config.enabled;
    enabledCheck.addEventListener('change', () => { config.enabled = enabledCheck.checked; save(); });
    enabledLabel.appendChild(enabledCheck);
    const enSpan = document.createElement('span');
    enSpan.className = 'toggle-slider';
    enabledLabel.appendChild(enSpan);
    enabledWrap.appendChild(enabledLabel);
    container.appendChild(addRow('啟用網頁驗證', enabledWrap));

    const captchaSelect = document.createElement('select');
    captchaSelect.className = 'form-select';
    [ { v: 'none', l: '無' }, { v: 'turnstile', l: 'Cloudflare Turnstile' }, { v: 'recaptcha', l: 'Google reCAPTCHA' } ].forEach(o => {
        const opt = document.createElement('option');
        opt.value = o.v;
        opt.textContent = o.l;
        if ((config.captcha_type || 'turnstile') === o.v) opt.selected = true;
        captchaSelect.appendChild(opt);
    });
    captchaSelect.addEventListener('change', () => { config.captcha_type = captchaSelect.value; save(); });
    container.appendChild(addRow('CAPTCHA 類型', captchaSelect));

    const roleSelect = document.createElement('select');
    roleSelect.className = 'form-select';
    roleSelect.innerHTML = '<option value="">未設定</option>';
    for (const r of roles) {
        const opt = document.createElement('option');
        opt.value = r.id;
        opt.textContent = '@ ' + r.name;
        if (String(config.unverified_role_id) === String(r.id)) opt.selected = true;
        roleSelect.appendChild(opt);
    }
    roleSelect.addEventListener('change', () => { config.unverified_role_id = roleSelect.value || null; save(); });
    container.appendChild(addRow('未驗證成員身分組', roleSelect));

    const autoroleWrap = document.createElement('div');
    autoroleWrap.className = 'toggle-wrapper';
    const autoroleLabel = document.createElement('label');
    autoroleLabel.className = 'toggle';
    const autoroleCheck = document.createElement('input');
    autoroleCheck.type = 'checkbox';
    autoroleCheck.checked = !!config.autorole_enabled;
    autoroleCheck.addEventListener('change', () => { config.autorole_enabled = autoroleCheck.checked; save(); });
    autoroleLabel.appendChild(autoroleCheck);
    const asl = document.createElement('span');
    asl.className = 'toggle-slider';
    autoroleLabel.appendChild(asl);
    autoroleWrap.appendChild(autoroleLabel);
    container.appendChild(addRow('自動分配未驗證角色', autoroleWrap));

    const triggerInput = document.createElement('input');
    triggerInput.type = 'text';
    triggerInput.className = 'form-input';
    triggerInput.placeholder = 'always 或 age_check+no_history 等';
    triggerInput.value = (config.autorole_trigger || 'always').toString();
    triggerInput.addEventListener('input', () => { config.autorole_trigger = triggerInput.value; save(); });
    container.appendChild(addRow('觸發條件', triggerInput));

    const minAgeInput = document.createElement('input');
    minAgeInput.type = 'number';
    minAgeInput.className = 'form-input';
    minAgeInput.min = 0;
    minAgeInput.value = config.min_age != null ? config.min_age : 7;
    minAgeInput.style.width = '5rem';
    minAgeInput.addEventListener('input', () => { config.min_age = minAgeInput.value; save(); });
    container.appendChild(addRow('最小帳號年齡 (天)', minAgeInput));

    const notifyTypeSelect = document.createElement('select');
    notifyTypeSelect.className = 'form-select';
    [ { v: 'dm', l: '私訊' }, { v: 'channel', l: '頻道' }, { v: 'both', l: '都要' } ].forEach(o => {
        const opt = document.createElement('option');
        opt.value = o.v;
        opt.textContent = o.l;
        if ((config.notify.type || 'dm') === o.v) opt.selected = true;
        notifyTypeSelect.appendChild(opt);
    });
    notifyTypeSelect.addEventListener('change', () => { config.notify.type = notifyTypeSelect.value; save(); });
    container.appendChild(addRow('驗證通知方式', notifyTypeSelect));

    const notifyChannelSelect = document.createElement('select');
    notifyChannelSelect.className = 'form-select';
    notifyChannelSelect.innerHTML = '<option value="">未設定</option>';
    const textChannels = channels.filter(ch => ['text', 'news'].includes(ch.type));
    for (const ch of textChannels) {
        const opt = document.createElement('option');
        opt.value = ch.id;
        opt.textContent = (ch.category ? '[' + ch.category + '] ' : '') + ch.name;
        if (String(config.notify.channel_id) === String(ch.id)) opt.selected = true;
        notifyChannelSelect.appendChild(opt);
    }
    notifyChannelSelect.addEventListener('change', () => { config.notify.channel_id = notifyChannelSelect.value || null; save(); });
    container.appendChild(addRow('通知頻道', notifyChannelSelect));

    const notifyTitleInput = document.createElement('input');
    notifyTitleInput.type = 'text';
    notifyTitleInput.className = 'form-input';
    notifyTitleInput.value = (config.notify.title || '伺服器網頁驗證').toString();
    notifyTitleInput.addEventListener('input', () => { config.notify.title = notifyTitleInput.value; save(); });
    container.appendChild(addRow('通知標題', notifyTitleInput));

    const notifyMsgInput = document.createElement('textarea');
    notifyMsgInput.className = 'form-textarea';
    notifyMsgInput.rows = 2;
    notifyMsgInput.value = (config.notify.message || '請點擊下方按鈕進行網頁驗證：').toString();
    notifyMsgInput.addEventListener('input', () => { config.notify.message = notifyMsgInput.value; save(); });
    container.appendChild(addRow('通知內容', notifyMsgInput));

    const countrySection = document.createElement('div');
    countrySection.className = 'webverify-country-section';
    const countryTitle = document.createElement('div');
    countryTitle.className = 'webverify-section-title';
    countryTitle.textContent = '地區警示';
    countrySection.appendChild(countryTitle);

    const countryEnabledWrap = document.createElement('div');
    countryEnabledWrap.className = 'toggle-wrapper';
    const countryEnabledLabel = document.createElement('label');
    countryEnabledLabel.className = 'toggle';
    const countryEnabledCheck = document.createElement('input');
    countryEnabledCheck.type = 'checkbox';
    countryEnabledCheck.checked = !!config.webverify_country_alert.enabled;
    countryEnabledCheck.addEventListener('change', () => { config.webverify_country_alert.enabled = countryEnabledCheck.checked; save(); });
    countryEnabledLabel.appendChild(countryEnabledCheck);
    const coSpan = document.createElement('span');
    coSpan.className = 'toggle-slider';
    countryEnabledLabel.appendChild(coSpan);
    countryEnabledWrap.appendChild(countryEnabledLabel);
    countrySection.appendChild(addRow('啟用地區警示', countryEnabledWrap));

    const countryModeSelect = document.createElement('select');
    countryModeSelect.className = 'form-select';
    [ { v: 'blacklist', l: '黑名單（列出的國家觸發警示）' }, { v: 'whitelist', l: '白名單（未列出的國家觸發警示）' } ].forEach(o => {
        const opt = document.createElement('option');
        opt.value = o.v;
        opt.textContent = o.l;
        if ((config.webverify_country_alert.mode || 'blacklist') === o.v) opt.selected = true;
        countryModeSelect.appendChild(opt);
    });
    countryModeSelect.addEventListener('change', () => { config.webverify_country_alert.mode = countryModeSelect.value; save(); });
    countrySection.appendChild(addRow('模式', countryModeSelect));

    const countriesInput = document.createElement('input');
    countriesInput.type = 'text';
    countriesInput.className = 'form-input';
    countriesInput.placeholder = 'US,CN,RU';
    countriesInput.value = Array.isArray(config.webverify_country_alert.countries) ? config.webverify_country_alert.countries.join(',') : '';
    countriesInput.addEventListener('input', () => {
        config.webverify_country_alert.countries = countriesInput.value.split(',').map(c => c.trim().toUpperCase()).filter(Boolean);
        save();
    });
    countrySection.appendChild(addRow('國家代碼 (逗號分隔)', countriesInput));

    const countryChannelSelect = document.createElement('select');
    countryChannelSelect.className = 'form-select';
    countryChannelSelect.innerHTML = '<option value="">未設定</option>';
    for (const ch of textChannels) {
        const opt = document.createElement('option');
        opt.value = ch.id;
        opt.textContent = (ch.category ? '[' + ch.category + '] ' : '') + ch.name;
        if (String(config.webverify_country_alert.channel_id) === String(ch.id)) opt.selected = true;
        countryChannelSelect.appendChild(opt);
    }
    countryChannelSelect.addEventListener('change', () => { config.webverify_country_alert.channel_id = countryChannelSelect.value || null; save(); });
    countrySection.appendChild(addRow('警示頻道', countryChannelSelect));

    container.appendChild(countrySection);

    return container;
}

function buildToggle(mod, s, value) {
    const wrap = document.createElement('div');
    wrap.className = 'toggle-wrapper';
    const label = document.createElement('label');
    label.className = 'toggle';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = !!value;
    const slider = document.createElement('span');
    slider.className = 'toggle-slider';
    label.appendChild(input);
    label.appendChild(slider);
    wrap.appendChild(label);

    input.addEventListener('change', () => debounceSave(mod, s.database_key, input.checked, 50));
    return wrap;
}

function buildSelect(mod, s, value) {
    const sel = document.createElement('select');
    sel.className = 'form-select';

    for (const opt of (s.options || [])) {
        const o = document.createElement('option');
        o.value = opt.value;
        o.textContent = opt.label;
        if (String(value) === String(opt.value)) o.selected = true;
        sel.appendChild(o);
    }

    sel.addEventListener('change', () => debounceSave(mod, s.database_key, sel.value, 100));
    return sel;
}

function buildTextarea(mod, s, value) {
    const ta = document.createElement('textarea');
    ta.className = 'form-textarea';
    ta.value = value || '';
    ta.placeholder = s.display || '';
    ta.addEventListener('input', () => debounceSave(mod, s.database_key, ta.value));
    return ta;
}

function buildNumberInput(mod, s, value) {
    const input = document.createElement('input');
    input.type = 'number';
    input.className = 'form-input';
    input.value = value != null ? value : '';
    if (s.min != null) input.min = s.min;
    if (s.max != null) input.max = s.max;
    if (s.type === 'float') input.step = '0.01';
    input.addEventListener('input', () => debounceSave(mod, s.database_key, input.value));
    return input;
}

function buildTextInput(mod, s, value) {
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'form-input';
    input.value = value != null ? value : '';
    input.placeholder = s.default != null ? String(s.default) : '';
    input.addEventListener('input', () => debounceSave(mod, s.database_key, input.value));
    return input;
}

// ---- Toast ----

function showToast(msg, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = msg;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.animation = 'toastOut 0.3s ease forwards';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ---- Init ----
document.addEventListener('DOMContentLoaded', render);
