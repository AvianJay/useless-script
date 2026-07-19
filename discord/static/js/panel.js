// ============= Panel JS =============
// Requires GUILD_ID and SETTINGS_SCHEMA to be defined by the template.

let currentValues = {};
let channelsCache = null;
let rolesCache = null;
let autoreplyLimitCache = null;

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

async function loadAutoreplyLimit() {
    if (autoreplyLimitCache !== null) return autoreplyLimitCache;
    const data = await fetchJSON(`/api/panel/guild/${GUILD_ID}/autoreply_limit`);
    autoreplyLimitCache = parseInt(data && data.limit, 10) || 50;
    return autoreplyLimitCache;
}

// ---- Saving ----

let saveTimers = {};

function debounceSave(module, key, value, delay = 600, onComplete = null) {
    const id = `${module}::${key}`;
    clearTimeout(saveTimers[id]);
    setIndicator(id, 'saving', '儲存中...');
    saveTimers[id] = setTimeout(async () => {
        const result = await doSave(module, key, value);
        if (onComplete) onComplete(result);
    }, delay);
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
        return data;
    } catch (e) {
        setIndicator(id, 'error', '✗ 網路錯誤');
        showToast('網路錯誤: ' + e.message, 'error');
        return { success: false, error: e.message };
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
    const [settings, channels, roles, autoreplyLimit] = await Promise.all([
        loadSettings(),
        loadChannels(),
        loadRoles(),
        loadAutoreplyLimit(),
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
            const row = buildSettingRow(mod, s, val, channels, roles, autoreplyLimit);
            body.appendChild(row);
        }

        card.appendChild(body);
        wrapper.appendChild(card);
    }

    // Auto-open the first module
    const first = wrapper.querySelector('.module-card');
    if (first) first.classList.add('open');
}

function buildSettingRow(mod, s, value, channels, roles, autoreplyLimit) {
    const row = document.createElement('div');
    row.className = 'setting-row';
    if (['autoreply_list', 'automod_config', 'webverify_config', 'fixlink_config', 'antibeast_config'].includes(s.type)) {
        row.classList.add('setting-row-column');
    }

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
            ctrl.appendChild(buildAutoreplyListEditor(mod, s, value, channels, autoreplyLimit));
            break;
        case 'automod_config':
            ctrl.appendChild(buildAutomodConfigEditor(mod, s, value, channels));
            break;
        case 'webverify_config':
            ctrl.appendChild(buildWebverifyConfigEditor(mod, s, value, channels, roles));
            break;
        case 'fixlink_config':
            ctrl.appendChild(buildFixlinkConfigEditor(mod, s, value));
            break;
        case 'antibeast_config':
            ctrl.appendChild(buildAntibeastConfigEditor(mod, s, value, roles));
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

function buildAutoreplyListEditor(mod, s, value, channels, autoreplyLimit = 50) {
    const MAX_AUTOREPLY_RULES = Math.max(1, parseInt(autoreplyLimit, 10) || 50);
    const list = Array.isArray(value) ? value.map(r => ({
        trigger: Array.isArray(r.trigger) ? r.trigger.map(v => String(v).trim()).filter(Boolean) : (r.trigger ? String(r.trigger).split(',').map(v => v.trim()).filter(Boolean) : []),
        response: Array.isArray(r.response) ? r.response.map(v => String(v).trim()).filter(Boolean) : (r.response ? String(r.response).split(',').map(v => v.trim()).filter(Boolean) : []),
        mode: r.mode || 'contains',
        reply: !!r.reply,
        channel_mode: r.channel_mode || 'all',
        channels: Array.isArray(r.channels) ? r.channels.map(String) : [],
        random_chance: Math.max(1, Math.min(100, parseInt(r.random_chance, 10) || 100)),
    })) : [];

    const container = document.createElement('div');
    container.className = 'autoreply-list-editor';

    const cardsWrap = document.createElement('div');
    cardsWrap.className = 'autoreply-rule-list';
    container.appendChild(cardsWrap);

    const limitNote = document.createElement('div');
    limitNote.className = 'autoreply-limit-note';
    container.appendChild(limitNote);

    function serializeRule(rule) {
        return {
            trigger: (rule.trigger || []).map(v => String(v).trim()).filter(Boolean),
            response: (rule.response || []).map(v => String(v).trim()).filter(Boolean),
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

    function createMultiValueEditor(items, titleText, placeholderText, addLabel, emptyText, onChange) {
        const values = Array.isArray(items) ? items : [];
        const wrap = document.createElement('div');
        wrap.className = 'autoreply-multi-field';

        const title = document.createElement('div');
        title.className = 'autoreply-field-title';
        title.textContent = titleText;
        wrap.appendChild(title);

        const listWrap = document.createElement('div');
        listWrap.className = 'autoreply-multi-list';
        wrap.appendChild(listWrap);

        const addRow = document.createElement('div');
        addRow.className = 'autoreply-multi-add-row';
        const addBtn = document.createElement('button');
        addBtn.type = 'button';
        addBtn.className = 'btn-autoreply-add btn-autoreply-add-inline';
        addBtn.textContent = addLabel;
        addRow.appendChild(addBtn);
        wrap.appendChild(addRow);

        function publish() {
            onChange(values.map(v => String(v).trim()).filter(Boolean));
        }

        function render() {
            listWrap.innerHTML = '';
            if (values.length === 0) {
                const empty = document.createElement('div');
                empty.className = 'autoreply-multi-empty';
                empty.textContent = emptyText;
                listWrap.appendChild(empty);
            }

            values.forEach((item, index) => {
                const row = document.createElement('div');
                row.className = 'autoreply-multi-item';

                const input = document.createElement('input');
                input.type = 'text';
                input.className = 'form-input';
                input.placeholder = placeholderText;
                input.value = item;
                input.addEventListener('input', () => {
                    values[index] = input.value;
                    publish();
                });

                const removeBtn = document.createElement('button');
                removeBtn.type = 'button';
                removeBtn.className = 'btn-autoreply-remove';
                removeBtn.textContent = '刪除';
                removeBtn.addEventListener('click', () => {
                    values.splice(index, 1);
                    publish();
                    render();
                });

                row.appendChild(input);
                row.appendChild(removeBtn);
                listWrap.appendChild(row);
            });
        }

        addBtn.addEventListener('click', () => {
            values.push('');
            publish();
            render();
            const inputs = listWrap.querySelectorAll('input');
            const lastInput = inputs[inputs.length - 1];
            if (lastInput) lastInput.focus();
        });

        render();
        return wrap;
    }

    function renderRules() {
        cardsWrap.innerHTML = '';
        list.forEach(rule => cardsWrap.appendChild(buildRuleCard(rule)));
        limitNote.textContent = `已設定 ${list.length} / ${MAX_AUTOREPLY_RULES} 筆規則`;
        addBtn.disabled = list.length >= MAX_AUTOREPLY_RULES;
        addBtn.textContent = list.length >= MAX_AUTOREPLY_RULES ? `已達上限 (${MAX_AUTOREPLY_RULES})` : '新增一筆自動回覆';
    }

    function buildRuleCard(rule) {
        const card = document.createElement('div');
        card.className = 'autoreply-rule-card';

        const triggerEditor = createMultiValueEditor(rule.trigger || [], '觸發條件', '輸入一條觸發文字', '新增觸發', '尚未設定觸發條件', next => {
            rule.trigger = next;
            save();
        });

        const responseEditor = createMultiValueEditor(rule.response || [], '回覆內容', '輸入一條回覆內容', '新增回覆', '尚未設定回覆內容', next => {
            rule.response = next;
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

        function rebuildChannelOptions() {
            channelSel.innerHTML = '<option value="">選擇頻道</option>';
            allowedChannels.forEach(ch => {
                if ((rule.channels || []).includes(String(ch.id))) return;
                const opt = document.createElement('option');
                opt.value = ch.id;
                opt.textContent = (ch.category ? `[${ch.category}] ` : '') + ch.name;
                channelSel.appendChild(opt);
            });
        }

        function renderChannelTags() {
            channelTagsWrap.innerHTML = '';
            (rule.channels || []).forEach(cid => {
                const ch = channels.find(c => String(c.id) === cid);
                const tag = document.createElement('span');
                tag.className = 'role-tag';
                tag.textContent = ch ? ch.name : cid;
                const rm = document.createElement('button');
                rm.className = 'role-tag-remove';
                rm.textContent = '?';
                rm.addEventListener('click', () => {
                    rule.channels = (rule.channels || []).filter(id => id !== cid);
                    renderChannelTags();
                    rebuildChannelOptions();
                    save();
                });
                tag.appendChild(rm);
                channelTagsWrap.appendChild(tag);
            });
        }

        channelSel.addEventListener('change', () => {
            if (!channelSel.value) return;
            rule.channels = rule.channels || [];
            rule.channels.push(channelSel.value);
            renderChannelTags();
            rebuildChannelOptions();
            save();
        });

        rebuildChannelOptions();
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
            save();
            renderRules();
        });

        const row1 = document.createElement('div');
        row1.className = 'autoreply-rule-row';
        row1.appendChild(triggerEditor);
        card.appendChild(row1);

        const row2 = document.createElement('div');
        row2.className = 'autoreply-rule-row';
        row2.appendChild(responseEditor);
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

    const addBtn = document.createElement('button');
    addBtn.type = 'button';
    addBtn.className = 'btn-autoreply-add';
    addBtn.addEventListener('click', () => {
        if (list.length >= MAX_AUTOREPLY_RULES) return;
        list.push({
            trigger: [],
            response: [],
            mode: 'contains',
            reply: false,
            channel_mode: 'all',
            channels: [],
            random_chance: 100,
        });
        save();
        renderRules();
    });
    container.appendChild(addBtn);

    renderRules();
    return container;
}

const ACTION_INPUT_SUGGESTIONS = [
    { label: '刪除訊息', value: 'delete' },
    { label: '刪除訊息並公開警告', value: 'delete {user}，請注意你的行為。' },
    { label: '公開警告', value: 'warn {user}，請注意你的行為。' },
    { label: '禁言 10 分鐘', value: 'mute 10m 違規' },
    { label: '禁言 10 分鐘（to 短寫）', value: 'to 10m 違規' },
    { label: '禁言 1 小時', value: 'mute 1h 違規' },
    { label: '踢出', value: 'kick 違規' },
    { label: '永久封禁', value: 'ban 0 0 違規' },
    { label: '封禁 1 天並刪除 7 天訊息', value: 'ban 1d 7d 違規' },
    { label: '強制驗證 1 天', value: 'force_verify 1d' },
    { label: '發送懲處公告', value: 'smm' },
];

async function analyzeActionInput(action) {
    const response = await fetch(`/api/panel/guild/${GUILD_ID}/action-preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

function renderActionAnalysis(container, analysis, { saved = false, onConfirm = null } = {}) {
    container.innerHTML = '';
    container.className = 'action-analysis';
    if (!analysis || !analysis.valid) {
        container.classList.add('error');
        container.textContent = (analysis && analysis.error) || '無法解析動作指令。';
        return;
    }

    if (analysis.requires_confirmation && !saved) container.classList.add('warning');
    else container.classList.add(saved ? 'saved' : 'valid');

    const title = document.createElement('div');
    title.className = 'action-analysis-title';
    title.textContent = saved
        ? '已儲存，實際會執行：'
        : (analysis.requires_confirmation ? analysis.confirmation : '實際會執行：');
    container.appendChild(title);

    const list = document.createElement('ol');
    list.className = 'action-preview-list';
    for (const line of (analysis.preview || [])) {
        const item = document.createElement('li');
        item.textContent = line;
        list.appendChild(item);
    }
    container.appendChild(list);

    if (analysis.requires_confirmation && !saved && onConfirm) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'action-confirm-button';
        button.textContent = `是，使用 ${analysis.normalized}`;
        button.addEventListener('click', onConfirm);
        container.appendChild(button);
    }
}

const AUTOMOD_FEATURES = [
    { id: 'scamtrap', label: '🪤 詐騙陷阱', desc: '蜜罐頻道，在該頻道發訊者自動處置', fields: [
        { key: 'channel_id', label: '陷阱頻道', type: 'channel', default: '' },
        { key: 'action', label: '處置動作', type: 'string', default: 'delete {user} 是最後一個被封禁的帳號，不要在這裡講話！, ban {user} 5s 12h [自動封禁] 疑似被盜帳號', placeholder: '例: delete 請勿在此發言' },
    ]},
    { id: 'escape_punish', label: '🏃 逃避責任懲處', desc: '禁言期間離開者額外懲處', fields: [
        { key: 'punishment', label: '懲處方式', type: 'select', options: [{ value: 'ban', label: '封禁' }], default: 'ban' },
        { key: 'duration', label: '持續時間 (如 0、7d)', type: 'string', default: '0' },
    ]},
    { id: 'too_many_h1', label: '📢 標題過多', desc: 'Markdown 大標題總字數上限', fields: [
        { key: 'max_length', label: '最大字數', type: 'number', default: '20', min: 1 },
        { key: 'action', label: '處置動作', type: 'string', default: 'warn', placeholder: '例: warn 或 mute 10m' },
        { key: 'ignore_channels', label: '忽略頻道', type: 'channel_list', default: [] },
    ]},
    { id: 'too_many_emojis', label: '😂 表情符號過多', desc: '單則訊息 emoji 數量上限', fields: [
        { key: 'max_emojis', label: '最大數量', type: 'number', default: '10', min: 1 },
        { key: 'action', label: '處置動作', type: 'string', default: 'warn' },
        { key: 'ignore_channels', label: '忽略頻道', type: 'channel_list', default: [] },
    ]},
    { id: 'anti_invite_link', label: '🔗 邀請連結', desc: '偵測 Discord 邀請連結，可選擇是否允許本伺服器連結', fields: [
        { key: 'allow_current_server', label: '允許本伺服器連結', type: 'boolean', default: false },
        { key: 'action', label: '處置動作', type: 'string', default: 'delete {user}，請勿發送其他伺服器的邀請連結。' },
        { key: 'ignore_channels', label: '忽略頻道', type: 'channel_list', default: [] },
    ]},
    { id: 'anti_uispam', label: '📲 用戶安裝應用程式濫用', desc: 'User Install 指令觸發頻率', fields: [
        { key: 'max_count', label: '時間內最大觸發次數', type: 'number', default: '5', min: 1 },
        { key: 'time_window', label: '時間窗口 (秒)', type: 'number', default: '60', min: 1 },
        { key: 'action', label: '處置動作', type: 'string', default: 'delete {user}，請勿濫用用戶安裝的應用程式指令。, mute 10m 濫用用戶安裝指令' },
        { key: 'ignore_channels', label: '忽略頻道', type: 'channel_list', default: [] },
    ]},
    { id: 'anti_raid', label: '🚨 防突襲', desc: '短時間內大量加入偵測', fields: [
        { key: 'max_joins', label: '時間內最大加入數', type: 'number', default: '5', min: 1 },
        { key: 'time_window', label: '時間窗口 (秒)', type: 'number', default: '60', min: 1 },
        { key: 'action', label: '處置動作', type: 'string', default: 'kick 突襲偵測自動踢出' },
    ]},
    { id: 'anti_spam', label: '🔁 防刷頻', desc: '相似訊息刷頻偵測', fields: [
        { key: 'max_messages', label: '最大相似訊息數', type: 'number', default: '5', min: 1 },
        { key: 'time_window', label: '時間窗口 (秒)', type: 'number', default: '30', min: 1 },
        { key: 'similarity', label: '相似度 (%)', type: 'number', default: '75', min: 1, max: 100 },
        { key: 'action', label: '處置動作', type: 'string', default: 'mute 10m 刷頻自動禁言, delete {user}，請勿刷頻。' },
        { key: 'ignore_channels', label: '忽略頻道', type: 'channel_list', default: [] },
    ]},
    { id: 'automod_detect', label: '🛡️ AutoMod 偵測', desc: '偵測 Discord 原生 AutoMod 規則觸發，發送通知並可執行額外處置', fields: [
        { key: 'log_channel', label: '通知頻道', type: 'channel', default: '' },
        { key: 'action', label: '額外處置動作', type: 'string', default: '', placeholder: '可選，例: mute 10m 違規' },
        { key: 'filter_rule', label: '規則名稱過濾', type: 'string', default: '', placeholder: '多個用 | 分隔，留空=全部' },
        { key: 'filter_action_type', label: '動作類型過濾', type: 'string', default: '', placeholder: 'block|alert|timeout|block_interactions' },
    ]},
];

function buildAutomodConfigEditor(mod, s, value, channels) {
    const config = typeof value === 'object' && value !== null ? { ...value } : {};
    const container = document.createElement('div');
    container.className = 'automod-config-editor';

    function toBoolean(val) {
        if (typeof val === 'string') {
            return ['true', '1', 'yes', 'on'].includes(val.trim().toLowerCase());
        }
        return !!val;
    }

    function getFeat(featId) {
        if (!config[featId]) config[featId] = { enabled: false };
        return config[featId];
    }

    function save(onComplete = null) {
        const out = {};
        for (const k of Object.keys(config)) {
            out[k] = { ...config[k] };
        }
        debounceSave(mod, s.database_key, out, 500, onComplete);
    }

    function setFeatValue(featId, key, val) {
        getFeat(featId)[key] = val;
        save();
    }

    function normalizeChannelListValue(raw) {
        if (Array.isArray(raw)) return raw.map(String).filter(Boolean);
        if (raw == null || raw === '') return [];
        if (typeof raw === 'string') {
            const trimmed = raw.trim();
            if (!trimmed) return [];
            try {
                const parsed = JSON.parse(trimmed);
                if (Array.isArray(parsed)) return parsed.map(String).filter(Boolean);
            } catch (_) {
                return trimmed.match(/\d+/g) || [];
            }
        }
        return [];
    }

    function buildAutomodChannelListEditor(initialValue, onChange) {
        const selected = normalizeChannelListValue(initialValue);
        const container = document.createElement('div');
        container.className = 'role-list-container';

        const tagsWrap = document.createElement('div');
        tagsWrap.className = 'role-tags';
        container.appendChild(tagsWrap);

        const sel = document.createElement('select');
        sel.className = 'form-select';
        sel.innerHTML = '<option value="">➕ 新增頻道...</option>';
        container.appendChild(sel);

        const allowedChannels = channels.filter(ch => ['text', 'news'].includes(ch.type));

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
                tag.textContent = ch ? `# ${prefix}${ch.name}` : `ID: ${cid}`;
                const removeBtn = document.createElement('button');
                removeBtn.className = 'role-tag-remove';
                removeBtn.textContent = '×';
                removeBtn.addEventListener('click', () => {
                    const idx = selected.indexOf(cid);
                    if (idx > -1) selected.splice(idx, 1);
                    renderTags();
                    rebuildOptions();
                    onChange([...selected]);
                });
                tag.appendChild(removeBtn);
                tagsWrap.appendChild(tag);
            }
        }

        function rebuildOptions() {
            sel.innerHTML = '<option value="">➕ 新增頻道...</option>';
            for (const ch of allowedChannels) {
                if (selected.includes(String(ch.id))) continue;
                const opt = document.createElement('option');
                opt.value = ch.id;
                opt.textContent = (ch.category ? `[${ch.category}] ` : '') + ch.name;
                sel.appendChild(opt);
            }
        }

        sel.addEventListener('change', () => {
            if (!sel.value) return;
            selected.push(sel.value);
            renderTags();
            rebuildOptions();
            onChange([...selected]);
        });

        renderTags();
        rebuildOptions();
        return container;
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
        enableCheck.checked = toBoolean(featData.enabled);
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
            const rawCur = featData[field.key] != null ? featData[field.key] : field.default;
            const cur = rawCur != null ? String(rawCur) : '';
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
            } else if (field.type === 'boolean') {
                const wrap = document.createElement('label');
                wrap.className = 'toggle';
                const input = document.createElement('input');
                input.type = 'checkbox';
                input.checked = toBoolean(rawCur);
                input.addEventListener('change', () => setFeatValue(feat.id, field.key, input.checked));
                const slider = document.createElement('span');
                slider.className = 'toggle-slider';
                wrap.appendChild(input);
                wrap.appendChild(slider);
                row.appendChild(wrap);
            } else if (field.type === 'channel_list') {
                row.appendChild(buildAutomodChannelListEditor(rawCur, val => setFeatValue(feat.id, field.key, val)));
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
            } else if (field.key === 'action') {
                const editor = document.createElement('div');
                editor.className = 'action-input-editor';
                const input = document.createElement('input');
                input.type = 'text';
                input.className = 'form-input';
                input.value = cur;
                input.placeholder = field.placeholder || '選擇建議或輸入，例如 mute 10m 違規';
                const listId = `action-suggestions-${feat.id}`;
                input.setAttribute('list', listId);
                const datalist = document.createElement('datalist');
                datalist.id = listId;
                for (const suggestion of ACTION_INPUT_SUGGESTIONS) {
                    const option = document.createElement('option');
                    option.value = suggestion.value;
                    option.label = suggestion.label;
                    datalist.appendChild(option);
                }
                const analysisBox = document.createElement('div');
                analysisBox.className = 'action-analysis';
                let previewTimer = null;
                let revision = 0;

                async function previewAction(raw, persist) {
                    const currentRevision = ++revision;
                    const clean = raw.trim();
                    if (!clean) {
                        analysisBox.className = 'action-analysis';
                        analysisBox.textContent = '尚未設定動作。';
                        if (persist) {
                            featData[field.key] = '';
                            save();
                        }
                        return;
                    }
                    analysisBox.className = 'action-analysis loading';
                    analysisBox.textContent = '正在解析動作...';
                    try {
                        const analysis = await analyzeActionInput(clean);
                        if (currentRevision !== revision) return;
                        if (analysis.requires_confirmation) {
                            renderActionAnalysis(analysisBox, analysis, {
                                onConfirm: () => {
                                    input.value = analysis.normalized;
                                    featData[field.key] = analysis.normalized;
                                    save(result => {
                                        if (result && result.success) {
                                            renderActionAnalysis(analysisBox, analysis, { saved: true });
                                        } else {
                                            analysisBox.className = 'action-analysis error';
                                            analysisBox.textContent = (result && result.error) || '儲存失敗。';
                                        }
                                    });
                                },
                            });
                            return;
                        }
                        if (!analysis.valid || !persist) {
                            renderActionAnalysis(analysisBox, analysis);
                            return;
                        }
                        input.value = analysis.normalized;
                        featData[field.key] = analysis.normalized;
                        save(result => {
                            if (result && result.success) {
                                renderActionAnalysis(analysisBox, analysis, { saved: true });
                            } else {
                                analysisBox.className = 'action-analysis error';
                                analysisBox.textContent = (result && result.error) || '儲存失敗。';
                            }
                        });
                    } catch (error) {
                        if (currentRevision !== revision) return;
                        analysisBox.className = 'action-analysis error';
                        analysisBox.textContent = `無法檢查動作：${error.message}`;
                    }
                }

                input.addEventListener('input', () => {
                    clearTimeout(previewTimer);
                    previewTimer = setTimeout(() => previewAction(input.value, true), 350);
                });
                editor.appendChild(input);
                editor.appendChild(datalist);
                editor.appendChild(analysisBox);
                row.appendChild(editor);
                if (cur) previewAction(cur, false);
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

function cloneConfig(value) {
    return JSON.parse(JSON.stringify(value || {}));
}

function buildCompoundField(labelText, control) {
    const row = document.createElement('div');
    row.className = 'compound-field-row';
    const label = document.createElement('label');
    label.className = 'compound-field-label';
    label.textContent = labelText;
    row.appendChild(label);
    row.appendChild(control);
    return row;
}

function buildCompoundToggle(labelText, checked, onChange) {
    const wrap = document.createElement('label');
    wrap.className = 'toggle';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = !!checked;
    input.addEventListener('change', () => onChange(input.checked));
    const slider = document.createElement('span');
    slider.className = 'toggle-slider';
    wrap.appendChild(input);
    wrap.appendChild(slider);
    return buildCompoundField(labelText, wrap);
}

function buildFixlinkConfigEditor(mod, s, value) {
    let config = cloneConfig(value);
    config.disabled_platforms = Array.isArray(config.disabled_platforms) ? config.disabled_platforms.map(String) : [];
    config.preferred_fixers = config.preferred_fixers || {};
    config.custom_platforms = Array.isArray(config.custom_platforms) ? config.custom_platforms : [];
    const platforms = Array.isArray(s.platforms) ? s.platforms : [];
    const maxCustom = parseInt(s.max_custom_platforms, 10) || 10;
    const container = document.createElement('div');
    container.className = 'compound-config-editor fixlink-config-editor';

    function save(delay = 150, onComplete = null) {
        debounceSave(mod, s.database_key, cloneConfig(config), delay, result => {
            if (result && result.success && result.value) config = cloneConfig(result.value);
            if (onComplete) onComplete(result);
        });
    }

    const general = document.createElement('div');
    general.className = 'compound-section';
    general.appendChild(buildCompoundToggle('啟用 FixLink', config.enabled, checked => { config.enabled = checked; save(); }));
    general.appendChild(buildCompoundToggle('移除追蹤參數', config.remove_tracker, checked => { config.remove_tracker = checked; save(); }));
    general.appendChild(buildCompoundToggle('使用 Webhook 替換訊息', config.webhook_mode, checked => { config.webhook_mode = checked; save(); }));
    general.appendChild(buildCompoundToggle('Webhook 僅處理含追蹤碼連結', config.webhook_only_with_tracker, checked => {
        config.webhook_only_with_tracker = checked;
        save();
    }));
    container.appendChild(general);

    const builtinSection = document.createElement('div');
    builtinSection.className = 'compound-section';
    const builtinTitle = document.createElement('div');
    builtinTitle.className = 'compound-section-title';
    builtinTitle.textContent = '內建平台';
    builtinSection.appendChild(builtinTitle);
    const builtinGrid = document.createElement('div');
    builtinGrid.className = 'compound-card-grid';
    for (const platform of platforms) {
        const card = document.createElement('div');
        card.className = 'compound-card compact';
        const heading = document.createElement('div');
        heading.className = 'compound-card-header';
        const name = document.createElement('strong');
        name.textContent = platform.name;
        heading.appendChild(name);
        const enabled = !config.disabled_platforms.includes(platform.name);
        const toggle = buildCompoundToggle('啟用', enabled, checked => {
            const disabled = new Set(config.disabled_platforms);
            if (checked) disabled.delete(platform.name);
            else disabled.add(platform.name);
            config.disabled_platforms = [...disabled];
            save();
        });
        toggle.classList.add('compound-inline-toggle');
        heading.appendChild(toggle);
        card.appendChild(heading);
        const select = document.createElement('select');
        select.className = 'form-select';
        for (const fixer of (platform.fixers || [])) {
            const option = document.createElement('option');
            option.value = fixer;
            option.textContent = fixer;
            option.selected = fixer === (config.preferred_fixers[platform.name] || platform.default_fixer);
            select.appendChild(option);
        }
        select.addEventListener('change', () => {
            config.preferred_fixers[platform.name] = select.value;
            save();
        });
        card.appendChild(buildCompoundField('主要修復服務', select));
        builtinGrid.appendChild(card);
    }
    builtinSection.appendChild(builtinGrid);
    container.appendChild(builtinSection);

    const customSection = document.createElement('div');
    customSection.className = 'compound-section';
    const customHeader = document.createElement('div');
    customHeader.className = 'compound-section-header';
    const customTitle = document.createElement('div');
    customTitle.className = 'compound-section-title';
    customTitle.textContent = `自訂平台 (${config.custom_platforms.length}/${maxCustom})`;
    const addButton = document.createElement('button');
    addButton.type = 'button';
    addButton.className = 'compound-button primary';
    addButton.textContent = '新增自訂平台';
    customHeader.appendChild(customTitle);
    customHeader.appendChild(addButton);
    customSection.appendChild(customHeader);
    const customList = document.createElement('div');
    customList.className = 'compound-list';
    customSection.appendChild(customList);
    container.appendChild(customSection);
    let newDrafts = [];

    function textControl(value, { multiline = false, placeholder = '' } = {}) {
        const input = document.createElement(multiline ? 'textarea' : 'input');
        if (!multiline) input.type = 'text';
        input.className = multiline ? 'form-textarea' : 'form-input';
        input.value = value || '';
        input.placeholder = placeholder;
        return input;
    }

    function renderCustomPlatforms() {
        customList.innerHTML = '';
        customTitle.textContent = `自訂平台 (${config.custom_platforms.length}/${maxCustom})`;
        addButton.disabled = config.custom_platforms.length + newDrafts.length >= maxCustom;
        const entries = [
            ...config.custom_platforms.map(item => ({ item: cloneConfig(item), isNew: false })),
            ...newDrafts.map(item => ({ item, isNew: true })),
        ];
        if (entries.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'compound-empty';
            empty.textContent = '尚未新增自訂平台。';
            customList.appendChild(empty);
            return;
        }
        entries.forEach(({ item, isNew }, entryIndex) => {
            item.fixer = item.fixer || {};
            const card = document.createElement('div');
            card.className = 'compound-card';
            const heading = document.createElement('div');
            heading.className = 'compound-card-header';
            const title = document.createElement('strong');
            title.textContent = isNew ? '新增自訂平台' : (item.name || '未命名平台');
            heading.appendChild(title);
            card.appendChild(heading);

            const nameInput = textControl(item.name, { placeholder: '平台名稱' });
            const originsInput = textControl((item.origins || []).join('\n'), { multiline: true, placeholder: 'example.com' });
            const pathsInput = textControl((item.path_prefixes || []).join('\n'), { multiline: true, placeholder: '/post/' });
            const keepInput = textControl((item.keep_query_keys || []).join('\n'), { multiline: true, placeholder: 'id\nlang' });
            const fixerNameInput = textControl(item.fixer.name, { placeholder: 'Fixer 名稱' });
            const endpointInput = textControl(item.fixer.endpoint, { placeholder: 'https://fix.example.com/embed' });
            const sourceParamInput = textControl(item.fixer.source_param || 'url', { placeholder: 'url' });
            const staticQuery = item.fixer.static_query || {};
            const staticInput = textControl(Object.entries(staticQuery).map(([key, val]) => `${key}=${val}`).join('\n'), {
                multiline: true,
                placeholder: 'v=1\nmode=embed',
            });
            card.appendChild(buildCompoundField('平台名稱', nameInput));
            card.appendChild(buildCompoundField('來源網域 (每行一個)', originsInput));
            card.appendChild(buildCompoundField('路徑前綴 (每行一個)', pathsInput));
            card.appendChild(buildCompoundField('保留 query keys', keepInput));
            card.appendChild(buildCompoundField('Fixer 名稱', fixerNameInput));
            card.appendChild(buildCompoundField('HTTPS endpoint', endpointInput));
            card.appendChild(buildCompoundField('來源 URL 參數名', sourceParamInput));
            card.appendChild(buildCompoundField('靜態 query', staticInput));

            const actions = document.createElement('div');
            actions.className = 'compound-actions';
            const saveButton = document.createElement('button');
            saveButton.type = 'button';
            saveButton.className = 'compound-button primary';
            saveButton.textContent = '儲存平台';
            saveButton.addEventListener('click', async () => {
                const candidate = {
                    id: item.id || undefined,
                    name: nameInput.value,
                    origins: originsInput.value,
                    path_prefixes: pathsInput.value,
                    keep_query_keys: keepInput.value,
                    fixer: {
                        name: fixerNameInput.value,
                        endpoint: endpointInput.value,
                        source_param: sourceParamInput.value,
                        static_query: staticInput.value,
                    },
                };
                const next = cloneConfig(config);
                if (isNew) next.custom_platforms.push(candidate);
                else next.custom_platforms = next.custom_platforms.map(existing => existing.id === item.id ? candidate : existing);
                const result = await doSave(mod, s.database_key, next);
                if (result && result.success) {
                    config = cloneConfig(result.value);
                    if (isNew) newDrafts = newDrafts.filter(draft => draft !== item);
                    renderCustomPlatforms();
                }
            });
            actions.appendChild(saveButton);

            if (!isNew) {
                const customKey = `custom:${item.id}`;
                const enabledToggle = buildCompoundToggle('啟用平台', !config.disabled_platforms.includes(customKey), checked => {
                    const disabled = new Set(config.disabled_platforms);
                    if (checked) disabled.delete(customKey);
                    else disabled.add(customKey);
                    config.disabled_platforms = [...disabled];
                    save();
                });
                enabledToggle.classList.add('compound-inline-toggle');
                actions.appendChild(enabledToggle);
            }

            const removeButton = document.createElement('button');
            removeButton.type = 'button';
            removeButton.className = 'compound-button danger';
            removeButton.textContent = isNew ? '取消' : '刪除';
            removeButton.addEventListener('click', async () => {
                if (isNew) {
                    newDrafts = newDrafts.filter(draft => draft !== item);
                    renderCustomPlatforms();
                    return;
                }
                const next = cloneConfig(config);
                next.custom_platforms = next.custom_platforms.filter(existing => existing.id !== item.id);
                next.disabled_platforms = next.disabled_platforms.filter(key => key !== `custom:${item.id}`);
                const result = await doSave(mod, s.database_key, next);
                if (result && result.success) {
                    config = cloneConfig(result.value);
                    renderCustomPlatforms();
                }
            });
            actions.appendChild(removeButton);
            card.appendChild(actions);
            customList.appendChild(card);
        });
    }

    addButton.addEventListener('click', () => {
        if (config.custom_platforms.length + newDrafts.length >= maxCustom) return;
        newDrafts.push({ fixer: { source_param: 'url' } });
        renderCustomPlatforms();
    });
    renderCustomPlatforms();
    return container;
}

function buildAntibeastConfigEditor(mod, s, value, roles) {
    const config = cloneConfig(value);
    config.bypass_roles = Array.isArray(config.bypass_roles) ? config.bypass_roles.map(String) : [];
    config.kick = config.kick || {};
    if (!config.kick.action) config.kick.action = 'kick AntiBeast: {time_window} 秒內觸發 {trigger_count} 次';
    const container = document.createElement('div');
    container.className = 'compound-config-editor antibeast-config-editor';

    function save(delay = 150, onComplete = null) {
        debounceSave(mod, s.database_key, cloneConfig(config), delay, onComplete);
    }

    const general = document.createElement('div');
    general.className = 'compound-section';
    general.appendChild(buildCompoundToggle('啟用 AntiBeast', config.enabled, checked => { config.enabled = checked; save(); }));
    general.appendChild(buildCompoundToggle('啟用連續觸發處置', config.kick.enabled, checked => { config.kick.enabled = checked; save(); }));
    general.appendChild(buildCompoundToggle('處置只計算 @everyone / @here', config.kick.only_everyone_here, checked => {
        config.kick.only_everyone_here = checked;
        save();
    }));
    container.appendChild(general);

    const limits = document.createElement('div');
    limits.className = 'compound-section';
    const threshold = document.createElement('input');
    threshold.type = 'number';
    threshold.className = 'form-input';
    threshold.min = '1';
    threshold.max = '20';
    threshold.value = String(config.kick.threshold || 2);
    threshold.addEventListener('change', () => {
        config.kick.threshold = Math.max(1, Math.min(20, parseInt(threshold.value, 10) || 2));
        threshold.value = String(config.kick.threshold);
        save();
    });
    limits.appendChild(buildCompoundField('時間窗口內觸發次數', threshold));
    const windowInput = document.createElement('input');
    windowInput.type = 'number';
    windowInput.className = 'form-input';
    windowInput.min = '5';
    windowInput.max = '3600';
    windowInput.value = String(config.kick.time_window || 10);
    windowInput.addEventListener('change', () => {
        config.kick.time_window = Math.max(5, Math.min(3600, parseInt(windowInput.value, 10) || 10));
        windowInput.value = String(config.kick.time_window);
        save();
    });
    limits.appendChild(buildCompoundField('時間窗口 (秒)', windowInput));
    container.appendChild(limits);

    const roleSection = document.createElement('div');
    roleSection.className = 'compound-section';
    const roleTitle = document.createElement('div');
    roleTitle.className = 'compound-section-title';
    roleTitle.textContent = '繞過身分組';
    roleSection.appendChild(roleTitle);
    const tags = document.createElement('div');
    tags.className = 'role-tags';
    const roleSelect = document.createElement('select');
    roleSelect.className = 'form-select';
    roleSection.appendChild(tags);
    roleSection.appendChild(roleSelect);
    container.appendChild(roleSection);

    function renderRoles() {
        tags.innerHTML = '';
        if (!config.bypass_roles.length) {
            const empty = document.createElement('span');
            empty.className = 'role-tag-empty';
            empty.textContent = '尚未設定繞過身分組';
            tags.appendChild(empty);
        }
        for (const roleId of config.bypass_roles) {
            const role = roles.find(item => String(item.id) === String(roleId));
            const tag = document.createElement('span');
            tag.className = 'role-tag';
            tag.textContent = role ? role.name : `ID: ${roleId}`;
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'role-tag-remove';
            remove.textContent = '×';
            remove.addEventListener('click', () => {
                config.bypass_roles = config.bypass_roles.filter(item => item !== roleId);
                renderRoles();
                save();
            });
            tag.appendChild(remove);
            tags.appendChild(tag);
        }
        roleSelect.innerHTML = '<option value="">新增繞過身分組...</option>';
        for (const role of roles) {
            if (config.bypass_roles.includes(String(role.id))) continue;
            const option = document.createElement('option');
            option.value = role.id;
            option.textContent = role.name;
            roleSelect.appendChild(option);
        }
    }
    roleSelect.addEventListener('change', () => {
        if (!roleSelect.value) return;
        config.bypass_roles.push(String(roleSelect.value));
        renderRoles();
        save();
    });
    renderRoles();

    const actionSection = document.createElement('div');
    actionSection.className = 'compound-section';
    const actionTitle = document.createElement('div');
    actionTitle.className = 'compound-section-title';
    actionTitle.textContent = 'Moderate 動作指令';
    actionSection.appendChild(actionTitle);
    const actionEditor = document.createElement('div');
    actionEditor.className = 'action-input-editor';
    const actionInput = document.createElement('input');
    actionInput.type = 'text';
    actionInput.className = 'form-input';
    actionInput.value = config.kick.action;
    actionInput.placeholder = '選擇建議或輸入，例如 mute 10m 違規';
    const listId = 'antibeast-action-suggestions';
    actionInput.setAttribute('list', listId);
    const datalist = document.createElement('datalist');
    datalist.id = listId;
    for (const suggestion of ACTION_INPUT_SUGGESTIONS) {
        const option = document.createElement('option');
        option.value = suggestion.value;
        option.label = suggestion.label;
        datalist.appendChild(option);
    }
    const analysisBox = document.createElement('div');
    analysisBox.className = 'action-analysis';
    let previewTimer = null;
    let revision = 0;

    async function previewAction(raw, persist) {
        const currentRevision = ++revision;
        const clean = raw.trim();
        if (!clean) {
            analysisBox.className = 'action-analysis error';
            analysisBox.textContent = '動作指令不能留空。';
            return;
        }
        analysisBox.className = 'action-analysis loading';
        analysisBox.textContent = '正在解析動作...';
        try {
            const analysis = await analyzeActionInput(clean);
            if (currentRevision !== revision) return;
            if (analysis.requires_confirmation) {
                renderActionAnalysis(analysisBox, analysis, {
                    onConfirm: () => {
                        actionInput.value = analysis.normalized;
                        config.kick.action = analysis.normalized;
                        save(0, result => {
                            if (result && result.success) renderActionAnalysis(analysisBox, analysis, { saved: true });
                            else renderActionAnalysis(analysisBox, { valid: false, error: (result && result.error) || '儲存失敗。' });
                        });
                    },
                });
                return;
            }
            if (!analysis.valid || !persist) {
                renderActionAnalysis(analysisBox, analysis);
                return;
            }
            actionInput.value = analysis.normalized;
            config.kick.action = analysis.normalized;
            save(0, result => {
                if (result && result.success) renderActionAnalysis(analysisBox, analysis, { saved: true });
                else renderActionAnalysis(analysisBox, { valid: false, error: (result && result.error) || '儲存失敗。' });
            });
        } catch (error) {
            if (currentRevision !== revision) return;
            renderActionAnalysis(analysisBox, { valid: false, error: `無法檢查動作：${error.message}` });
        }
    }
    actionInput.addEventListener('input', () => {
        clearTimeout(previewTimer);
        previewTimer = setTimeout(() => previewAction(actionInput.value, true), 350);
    });
    actionEditor.appendChild(actionInput);
    actionEditor.appendChild(datalist);
    actionEditor.appendChild(analysisBox);
    actionSection.appendChild(actionEditor);
    container.appendChild(actionSection);
    previewAction(config.kick.action, false);
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
