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
