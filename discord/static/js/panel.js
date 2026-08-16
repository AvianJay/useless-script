// ============= Panel JS =============
// Requires GUILD_ID and SETTINGS_SCHEMA to be defined by the template.
// window.I18N (web.js.* catalog subset) and window.I18N_LOCALE are injected
// by panel_guild.html; t() falls back to the key itself when missing.

function t(key, params) {
    let value = (window.I18N || {})[key];
    if (value === undefined || value === null) return key;
    if (typeof value === 'object') {
        // 複數項：zh 系語言一律 other；en 在 count === 1 時用 one
        const count = params && params.count;
        const noPlural = /^(zh|ja|ko|th|vi|id|ms)/.test(window.I18N_LOCALE || 'zh-TW');
        let variant = 'other';
        if (count === 0 && value.zero !== undefined) variant = 'zero';
        else if (!noPlural && count === 1 && value.one !== undefined) variant = 'one';
        value = value[variant] !== undefined ? value[variant]
            : (value.other !== undefined ? value.other : Object.values(value)[0]);
    }
    if (params) {
        for (const name in params) {
            value = value.split('{' + name + '}').join(params[name]);
        }
    }
    return value;
}

let currentValues = {};
let channelsCache = null;
let rolesCache = null;
let autoreplyLimitCache = null;
let stickymessageLimitCache = null;

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
    setIndicator(id, 'saving', t('web.js.common.saving'));
    saveTimers[id] = setTimeout(async () => {
        const result = await doSave(module, key, value);
        if (onComplete) onComplete(result);
    }, delay);
}

async function loadStickymessageLimit() {
    if (stickymessageLimitCache !== null) return stickymessageLimitCache;
    const data = await fetchJSON(`/api/panel/guild/${GUILD_ID}/stickymessage_limit`);
    stickymessageLimitCache = Math.max(1, Math.min(25, parseInt(data && data.limit, 10) || 5));
    return stickymessageLimitCache;
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
            setIndicator(id, 'saved', '✓ ' + t('web.js.common.saved'));
            // Update local cache
            if (!currentValues[module]) currentValues[module] = {};
            currentValues[module][key] = data.value;
        } else {
            setIndicator(id, 'error', '✗ ' + (data.error || t('web.js.common.save_failed')));
            showToast(data.error || t('web.js.common.save_failed'), 'error');
        }
        return data;
    } catch (e) {
        setIndicator(id, 'error', '✗ ' + t('web.js.common.network_error'));
        showToast(t('web.js.common.network_error') + ': ' + e.message, 'error');
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
    wrapper.innerHTML = '<div class="loading-spinner">' + t('web.js.common.loading') + '</div>';

    // Load all data in parallel
    const [settings, channels, roles, autoreplyLimit, stickymessageLimit] = await Promise.all([
        loadSettings(),
        loadChannels(),
        loadRoles(),
        loadAutoreplyLimit(),
        SETTINGS_SCHEMA.StickyMessage ? loadStickymessageLimit() : Promise.resolve(5),
    ]);

    if (!settings) { wrapper.innerHTML = '<div class="loading-spinner">' + t('web.js.common.load_failed') + '</div>'; return; }

    wrapper.innerHTML = '';
    const moduleNames = Object.keys(SETTINGS_SCHEMA);

    if (moduleNames.length === 0) {
        wrapper.innerHTML = '<div class="empty-state"><p>' + t('web.js.common.no_modules') + '</p></div>';
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
            const row = buildSettingRow(mod, s, val, channels, roles, autoreplyLimit, stickymessageLimit);
            body.appendChild(row);
        }

        card.appendChild(body);
        wrapper.appendChild(card);
    }

    // Auto-open the first module
    const first = wrapper.querySelector('.module-card');
    if (first) first.classList.add('open');
}

function buildSettingRow(mod, s, value, channels, roles, autoreplyLimit, stickymessageLimit) {
    const row = document.createElement('div');
    row.className = 'setting-row';
    if (['autoreply_list', 'automod_config', 'webverify_config', 'fixlink_config', 'antibeast_config', 'stickymessage_config'].includes(s.type)) {
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
        case 'stickymessage_config':
            ctrl.appendChild(buildStickymessageConfigEditor(mod, s, value, channels, stickymessageLimit));
            break;
        case 'moderation_announcement_config':
            ctrl.appendChild(buildModerationAnnouncementEditor(mod, s, value));
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
    sel.innerHTML = '<option value="none">' + t('web.js.common.unset') + '</option>';

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
    sel.innerHTML = '<option value="none">' + t('web.js.common.unset') + '</option>';

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
    sel.innerHTML = '<option value="">➕ ' + t('web.js.common.add_role') + '</option>';
    container.appendChild(sel);

    function renderTags() {
        tagsWrap.innerHTML = '';
        if (selected.length === 0) {
            tagsWrap.innerHTML = '<span class="role-tag-empty">' + t('web.js.common.no_roles') + '</span>';
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
        sel.innerHTML = '<option value="">➕ ' + t('web.js.common.add_role') + '</option>';
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
    sel.innerHTML = '<option value="">➕ ' + t('web.js.common.add_channel') + '</option>';
    container.appendChild(sel);

    const allowedTypes = ['text', 'news'];
    const allowedChannels = channels.filter(ch => allowedTypes.includes(ch.type));

    function renderTags() {
        tagsWrap.innerHTML = '';
        if (selected.length === 0) {
            tagsWrap.innerHTML = '<span class="role-tag-empty">' + t('web.js.common.no_channels') + '</span>';
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
        sel.innerHTML = '<option value="">➕ ' + t('web.js.common.add_channel') + '</option>';
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

function buildStickymessageConfigEditor(mod, s, value, channels, limit = 5) {
    const allowedChannels = channels.filter(ch => ['text', 'news'].includes(ch.type));
    const parsedQuietSeconds = parseInt(value && value.quiet_seconds, 10);
    const parsedMinIntervalSeconds = parseInt(value && value.min_interval_seconds, 10);
    const config = {
        quiet_seconds: Math.max(0, Math.min(300, Number.isFinite(parsedQuietSeconds) ? parsedQuietSeconds : 10)),
        min_interval_seconds: Math.max(5, Math.min(3600, Number.isFinite(parsedMinIntervalSeconds) ? parsedMinIntervalSeconds : 30)),
        entries: Array.isArray(value && value.entries) ? value.entries.map(entry => ({
            channel_id: String(entry.channel_id || ''),
            content: String(entry.content || ''),
            allow_mentions: !!entry.allow_mentions,
        })) : [],
    };
    const container = document.createElement('div');
    container.className = 'stickymessage-editor';

    const timing = document.createElement('div');
    timing.className = 'stickymessage-timing';
    const quietInput = document.createElement('input');
    quietInput.type = 'number';
    quietInput.min = '0';
    quietInput.max = '300';
    quietInput.className = 'form-input';
    quietInput.value = config.quiet_seconds;
    const intervalInput = document.createElement('input');
    intervalInput.type = 'number';
    intervalInput.min = '5';
    intervalInput.max = '3600';
    intervalInput.className = 'form-input';
    intervalInput.value = config.min_interval_seconds;
    const timingSave = document.createElement('button');
    timingSave.type = 'button';
    timingSave.className = 'btn-autoreply-add';
    timingSave.textContent = t('web.js.sticky.save_timing');
    timingSave.addEventListener('click', async () => {
        config.quiet_seconds = parseInt(quietInput.value, 10);
        config.min_interval_seconds = parseInt(intervalInput.value, 10);
        await saveConfig();
    });
    timing.appendChild(makeLabeledControl(t('web.js.sticky.quiet_label'), quietInput));
    timing.appendChild(makeLabeledControl(t('web.js.sticky.interval_label'), intervalInput));
    timing.appendChild(timingSave);
    container.appendChild(timing);

    const status = document.createElement('div');
    status.className = 'stickymessage-status';
    container.appendChild(status);
    const cards = document.createElement('div');
    cards.className = 'stickymessage-list';
    container.appendChild(cards);
    const addButton = document.createElement('button');
    addButton.type = 'button';
    addButton.className = 'btn-autoreply-add';
    addButton.textContent = '＋ ' + t('web.js.sticky.add');
    container.appendChild(addButton);

    function makeLabeledControl(labelText, control) {
        const wrap = document.createElement('label');
        wrap.className = 'stickymessage-field';
        const label = document.createElement('span');
        label.textContent = labelText;
        wrap.appendChild(label);
        wrap.appendChild(control);
        return wrap;
    }

    function payload() {
        return {
            quiet_seconds: parseInt(config.quiet_seconds, 10),
            min_interval_seconds: parseInt(config.min_interval_seconds, 10),
            entries: config.entries.map(entry => ({
                channel_id: entry.channel_id,
                content: entry.content.trim(),
                allow_mentions: !!entry.allow_mentions,
            })),
        };
    }

    async function saveConfig() {
        const result = await doSave(mod, s.database_key, payload());
        if (result.success && result.value) {
            config.quiet_seconds = result.value.quiet_seconds;
            config.min_interval_seconds = result.value.min_interval_seconds;
            config.entries = result.value.entries.map(entry => ({
                channel_id: String(entry.channel_id),
                content: String(entry.content),
                allow_mentions: !!entry.allow_mentions,
            }));
            renderCards();
        }
        return result;
    }

    async function publish(channelId, button) {
        button.disabled = true;
        const originalText = button.textContent;
        button.textContent = t('web.js.sticky.publishing');
        try {
            const response = await fetch(`/api/panel/guild/${GUILD_ID}/stickymessage/publish`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel_id: channelId }),
            });
            const data = await response.json();
            if (!data.success) throw new Error(data.error || t('web.js.sticky.publish_failed'));
            showToast(t('web.js.sticky.published'), 'success');
        } catch (error) {
            showToast(t('web.js.sticky.publish_failed') + ': ' + error.message, 'error');
        } finally {
            button.disabled = false;
            button.textContent = originalText;
        }
    }

    function channelSelect(entry, index) {
        const select = document.createElement('select');
        select.className = 'form-select';
        select.innerHTML = '<option value="">' + t('web.js.sticky.pick_channel') + '</option>';
        for (const channel of allowedChannels) {
            if (config.entries.some((other, otherIndex) => otherIndex !== index && other.channel_id === String(channel.id))) continue;
            const option = document.createElement('option');
            option.value = channel.id;
            option.textContent = `${channel.category ? `[${channel.category}] ` : ''}# ${channel.name}`;
            if (entry.channel_id === String(channel.id)) option.selected = true;
            select.appendChild(option);
        }
        select.addEventListener('change', () => { entry.channel_id = select.value; });
        return select;
    }

    function renderCards() {
        cards.innerHTML = '';
        status.textContent = t('web.js.sticky.status', {current: config.entries.length, limit: limit, next: limit + 1});
        addButton.disabled = config.entries.length >= limit || config.entries.length >= 25;
        if (config.entries.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'stickymessage-empty';
            empty.textContent = t('web.js.sticky.empty');
            cards.appendChild(empty);
            return;
        }

        config.entries.forEach((entry, index) => {
            const card = document.createElement('div');
            card.className = 'stickymessage-card';
            if (index >= limit) card.classList.add('is-paused');

            const heading = document.createElement('div');
            heading.className = 'stickymessage-card-heading';
            const title = document.createElement('strong');
            title.textContent = t('web.js.sticky.item_title', {index: index + 1}) + (index >= limit ? t('web.js.sticky.over_limit') : '');
            heading.appendChild(title);
            const moveWrap = document.createElement('span');
            const up = document.createElement('button');
            up.type = 'button';
            up.className = 'btn-autoreply-add';
            up.textContent = '↑';
            up.disabled = index === 0;
            up.addEventListener('click', async () => {
                [config.entries[index - 1], config.entries[index]] = [config.entries[index], config.entries[index - 1]];
                await saveConfig();
            });
            const down = document.createElement('button');
            down.type = 'button';
            down.className = 'btn-autoreply-add';
            down.textContent = '↓';
            down.disabled = index === config.entries.length - 1;
            down.addEventListener('click', async () => {
                [config.entries[index + 1], config.entries[index]] = [config.entries[index], config.entries[index + 1]];
                await saveConfig();
            });
            moveWrap.appendChild(up);
            moveWrap.appendChild(down);
            heading.appendChild(moveWrap);
            card.appendChild(heading);

            card.appendChild(makeLabeledControl(t('web.js.sticky.channel'), channelSelect(entry, index)));
            const textarea = document.createElement('textarea');
            textarea.className = 'form-textarea';
            textarea.maxLength = 2000;
            textarea.rows = 4;
            textarea.value = entry.content;
            textarea.addEventListener('input', () => { entry.content = textarea.value; });
            card.appendChild(makeLabeledControl(t('web.js.sticky.content_label'), textarea));

            const mentionLabel = document.createElement('label');
            mentionLabel.className = 'stickymessage-mentions';
            const mentionInput = document.createElement('input');
            mentionInput.type = 'checkbox';
            mentionInput.checked = entry.allow_mentions;
            mentionInput.addEventListener('change', () => { entry.allow_mentions = mentionInput.checked; });
            mentionLabel.appendChild(mentionInput);
            mentionLabel.appendChild(document.createTextNode(' ' + t('web.js.sticky.mentions_label')));
            card.appendChild(mentionLabel);

            const actions = document.createElement('div');
            actions.className = 'stickymessage-actions';
            const save = document.createElement('button');
            save.type = 'button';
            save.className = 'btn-autoreply-add';
            save.textContent = t('web.js.sticky.save_publish');
            save.addEventListener('click', saveConfig);
            const publishButton = document.createElement('button');
            publishButton.type = 'button';
            publishButton.className = 'btn-autoreply-add';
            publishButton.textContent = t('web.js.sticky.manual_publish');
            publishButton.disabled = !entry.channel_id || index >= limit;
            publishButton.addEventListener('click', () => publish(entry.channel_id, publishButton));
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'btn-autoreply-remove';
            remove.textContent = t('web.js.common.delete');
            remove.addEventListener('click', async () => {
                if (!window.confirm(t('web.js.sticky.confirm_delete'))) return;
                config.entries.splice(index, 1);
                await saveConfig();
            });
            actions.appendChild(save);
            actions.appendChild(publishButton);
            actions.appendChild(remove);
            card.appendChild(actions);
            cards.appendChild(card);
        });
    }

    addButton.addEventListener('click', () => {
        if (config.entries.length >= limit || config.entries.length >= 25) return;
        config.entries.push({ channel_id: '', content: '', allow_mentions: false });
        renderCards();
    });

    renderCards();
    return container;
}

const AUTOREPLY_MODE_OPTIONS = [
    { value: 'contains', label: t('web.js.autoreply.mode_contains') },
    { value: 'equals', label: t('web.js.autoreply.mode_equals') },
    { value: 'starts_with', label: t('web.js.autoreply.mode_starts') },
    { value: 'ends_with', label: t('web.js.autoreply.mode_ends') },
    { value: 'regex', label: t('web.js.autoreply.mode_regex') },
];
const AUTOREPLY_CHANNEL_MODE_OPTIONS = [
    { value: 'all', label: t('web.js.autoreply.ch_all') },
    { value: 'whitelist', label: t('web.js.autoreply.ch_whitelist') },
    { value: 'blacklist', label: t('web.js.autoreply.ch_blacklist') },
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
                removeBtn.textContent = t('web.js.common.delete');
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
        limitNote.textContent = t('web.js.autoreply.limit_note', {current: list.length, limit: MAX_AUTOREPLY_RULES});
        addBtn.disabled = list.length >= MAX_AUTOREPLY_RULES;
        addBtn.textContent = list.length >= MAX_AUTOREPLY_RULES ? t('web.js.autoreply.limit_reached', {limit: MAX_AUTOREPLY_RULES}) : t('web.js.autoreply.add');
    }

    function buildRuleCard(rule) {
        const card = document.createElement('div');
        card.className = 'autoreply-rule-card';

        const triggerEditor = createMultiValueEditor(rule.trigger || [], t('web.js.autoreply.trigger'), t('web.js.autoreply.trigger_ph'), t('web.js.autoreply.trigger_add'), t('web.js.autoreply.trigger_empty'), next => {
            rule.trigger = next;
            save();
        });

        const responseEditor = createMultiValueEditor(rule.response || [], t('web.js.autoreply.response'), t('web.js.autoreply.response_ph'), t('web.js.autoreply.response_add'), t('web.js.autoreply.response_empty'), next => {
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
            channelSel.innerHTML = '<option value="">' + t('web.js.autoreply.pick_channel') + '</option>';
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
        deleteBtn.textContent = t('web.js.common.delete');
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
        row3.appendChild(document.createTextNode(t('web.js.autoreply.mode') + ' '));
        row3.appendChild(modeSelect);
        row3.appendChild(document.createTextNode(' ' + t('web.js.autoreply.reply') + ' '));
        row3.appendChild(replyWrap);
        row3.appendChild(document.createTextNode(' ' + t('web.js.autoreply.channel') + ' '));
        row3.appendChild(channelModeSelect);
        row3.appendChild(channelTagsWrap);
        row3.appendChild(channelSel);
        row3.appendChild(document.createTextNode(' ' + t('web.js.autoreply.chance') + ' '));
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
    { label: t('web.js.action.preset_delete'), value: 'delete' },
    { label: t('web.js.action.preset_delete_warn'), value: 'delete {user}，請注意你的行為。' },
    { label: t('web.js.action.preset_warn'), value: 'warn {user}，請注意你的行為。' },
    { label: t('web.js.action.preset_mute10'), value: 'mute 10m 違規' },
    { label: t('web.js.action.preset_to10'), value: 'to 10m 違規' },
    { label: t('web.js.action.preset_mute1h'), value: 'mute 1h 違規' },
    { label: t('web.js.action.preset_kick'), value: 'kick 違規' },
    { label: t('web.js.action.preset_ban'), value: 'ban 0 0 違規' },
    { label: t('web.js.action.preset_ban1d'), value: 'ban 1d 7d 違規' },
    { label: t('web.js.action.preset_fv1d'), value: 'force_verify 1d' },
    { label: t('web.js.action.preset_smm'), value: 'smm' },
];

async function analyzeActionInput(action, feature = '') {
    const response = await fetch(`/api/panel/guild/${GUILD_ID}/action-preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, feature }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

function renderActionAnalysis(container, analysis, { saved = false, onConfirm = null } = {}) {
    container.innerHTML = '';
    container.className = 'action-analysis';
    if (!analysis || !analysis.valid) {
        container.classList.add('error');
        container.textContent = (analysis && analysis.error) || t('web.js.action.parse_failed');
        return;
    }

    if (analysis.requires_confirmation && !saved) container.classList.add('warning');
    else container.classList.add(saved ? 'saved' : 'valid');

    const title = document.createElement('div');
    title.className = 'action-analysis-title';
    title.textContent = saved
        ? t('web.js.action.saved_will_run')
        : (analysis.requires_confirmation ? analysis.confirmation : t('web.js.action.will_run'));
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
        button.textContent = t('web.js.action.confirm_use', {action: analysis.normalized});
        button.addEventListener('click', onConfirm);
        container.appendChild(button);
    }
}

const AUTOMOD_FEATURES = [
    { id: 'scamtrap', label: '🪤 ' + t('web.js.automod.scamtrap'), desc: t('web.js.automod.scamtrap_desc'), fields: [
        { key: 'channel_id', label: t('web.js.automod.f_trap_channel'), type: 'channel', default: '' },
        { key: 'action', label: t('web.js.automod.f_action'), type: 'string', default: 'delete {user} 是最後一個被封禁的帳號，不要在這裡講話！, ban {user} 5s 12h [自動封禁] 疑似被盜帳號', placeholder: t('web.js.automod.ph_example') },
    ]},
    { id: 'escape_punish', label: '🏃 ' + t('web.js.automod.escape'), desc: t('web.js.automod.escape_desc'), fields: [
        { key: 'punishment', label: t('web.js.automod.f_punishment'), type: 'select', options: [{ value: 'ban', label: t('web.js.automod.f_ban') }], default: 'ban' },
        { key: 'duration', label: t('web.js.automod.f_duration'), type: 'string', default: '0' },
    ]},
    { id: 'too_many_h1', label: '📢 ' + t('web.js.automod.h1'), desc: t('web.js.automod.h1_desc'), fields: [
        { key: 'max_length', label: t('web.js.automod.f_max_length'), type: 'number', default: '20', min: 1 },
        { key: 'action', label: t('web.js.automod.f_action'), type: 'string', default: 'warn', placeholder: t('web.js.automod.ph_example') },
        { key: 'ignore_channels', label: t('web.js.automod.f_ignore_channels'), type: 'channel_list', default: [] },
    ]},
    { id: 'too_many_emojis', label: '😂 ' + t('web.js.automod.emojis'), desc: t('web.js.automod.emojis_desc'), fields: [
        { key: 'max_emojis', label: t('web.js.automod.f_max_emojis'), type: 'number', default: '10', min: 1 },
        { key: 'action', label: t('web.js.automod.f_action'), type: 'string', default: 'warn' },
        { key: 'ignore_channels', label: t('web.js.automod.f_ignore_channels'), type: 'channel_list', default: [] },
    ]},
    { id: 'anti_invite_link', label: '🔗 ' + t('web.js.automod.invite'), desc: t('web.js.automod.invite_desc'), fields: [
        { key: 'allow_current_server', label: t('web.js.automod.f_allow_current'), type: 'boolean', default: false },
        { key: 'action', label: t('web.js.automod.f_action'), type: 'string', default: 'delete {user}，請勿發送其他伺服器的邀請連結。' },
        { key: 'ignore_channels', label: t('web.js.automod.f_ignore_channels'), type: 'channel_list', default: [] },
    ]},
    { id: 'anti_uispam', label: '📲 ' + t('web.js.automod.uispam'), desc: t('web.js.automod.uispam_desc'), fields: [
        { key: 'max_count', label: t('web.js.automod.f_max_count'), type: 'number', default: '5', min: 1 },
        { key: 'time_window', label: t('web.js.automod.f_time_window'), type: 'number', default: '60', min: 1 },
        { key: 'action', label: t('web.js.automod.f_action'), type: 'string', default: 'delete {user}，請勿濫用用戶安裝的應用程式指令。, mute 10m 濫用用戶安裝指令' },
        { key: 'ignore_channels', label: t('web.js.automod.f_ignore_channels'), type: 'channel_list', default: [] },
    ]},
    { id: 'anti_raid', label: '🚨 ' + t('web.js.automod.raid'), desc: t('web.js.automod.raid_desc'), fields: [
        { key: 'max_joins', label: t('web.js.automod.f_max_joins'), type: 'number', default: '5', min: 1 },
        { key: 'time_window', label: t('web.js.automod.f_time_window'), type: 'number', default: '60', min: 1 },
        { key: 'action', label: t('web.js.automod.f_action'), type: 'string', default: 'kick 突襲偵測自動踢出' },
    ]},
    { id: 'anti_spam', label: '🔁 ' + t('web.js.automod.spam'), desc: t('web.js.automod.spam_desc'), fields: [
        { key: 'max_messages', label: t('web.js.automod.f_max_messages'), type: 'number', default: '5', min: 1 },
        { key: 'time_window', label: t('web.js.automod.f_time_window'), type: 'number', default: '30', min: 1 },
        { key: 'similarity', label: t('web.js.automod.f_similarity'), type: 'number', default: '75', min: 1, max: 100 },
        { key: 'action', label: t('web.js.automod.f_action'), type: 'string', default: 'mute 10m 刷頻自動禁言, delete {user}，請勿刷頻。' },
        { key: 'ignore_channels', label: t('web.js.automod.f_ignore_channels'), type: 'channel_list', default: [] },
    ]},
    { id: 'automod_detect', label: '🛡️ ' + t('web.js.automod.detect'), desc: t('web.js.automod.detect_desc'), fields: [
        { key: 'log_channel', label: t('web.js.automod.f_log_channel'), type: 'channel', default: '' },
        { key: 'action', label: t('web.js.automod.f_extra_action'), type: 'string', default: '', placeholder: t('web.js.automod.ph_optional') },
        { key: 'filter_rule', label: t('web.js.automod.f_filter_rule'), type: 'string', default: '', placeholder: t('web.js.automod.ph_filter') },
        { key: 'filter_action_type', label: t('web.js.automod.f_filter_action'), type: 'string', default: '', placeholder: 'block|alert|timeout|block_interactions' },
    ]},
    { id: 'flagged_user', label: '🚩 ' + t('web.js.automod.flagged'), desc: t('web.js.automod.flagged_desc'), fields: [
        { key: 'log_channel', label: t('web.js.automod.f_log_channel'), type: 'channel', default: '' },
        { key: 'action', label: t('web.js.automod.f_action'), type: 'string', default: '', placeholder: t('web.js.automod.ph_optional') },
        { key: 'action_source', label: t('web.js.automod.f_action_source'), type: 'select', default: 'both', options: [
            { value: 'both', label: t('web.js.automod.src_both') },
            { value: 'local', label: t('web.js.automod.src_local') },
            { value: 'api', label: t('web.js.automod.src_api') },
        ]},
        { key: 'local_match_mode', label: t('web.js.automod.f_match_mode'), type: 'select', default: 'active', options: [
            { value: 'active', label: t('web.js.automod.match_active') },
            { value: 'history', label: t('web.js.automod.match_history') },
        ]},
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
        sel.innerHTML = '<option value="">➕ ' + t('web.js.common.add_channel') + '</option>';
        container.appendChild(sel);

        const allowedChannels = channels.filter(ch => ['text', 'news'].includes(ch.type));

        function renderTags() {
            tagsWrap.innerHTML = '';
            if (selected.length === 0) {
                tagsWrap.innerHTML = '<span class="role-tag-empty">' + t('web.js.common.no_channels') + '</span>';
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
            sel.innerHTML = '<option value="">➕ ' + t('web.js.common.add_channel') + '</option>';
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
                sel.innerHTML = '<option value="">' + t('web.js.common.unset') + '</option>';
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
                input.placeholder = field.placeholder || t('web.js.action.input_ph');
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
                        analysisBox.textContent = t('web.js.action.empty');
                        if (persist) {
                            featData[field.key] = '';
                            save();
                        }
                        return;
                    }
                    analysisBox.className = 'action-analysis loading';
                    analysisBox.textContent = t('web.js.action.parsing');
                    try {
                        const analysis = await analyzeActionInput(clean, feat.id);
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
                                            analysisBox.textContent = (result && result.error) || t('web.js.common.save_failed');
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
                                analysisBox.textContent = (result && result.error) || t('web.js.common.save_failed');
                            }
                        });
                    } catch (error) {
                        if (currentRevision !== revision) return;
                        analysisBox.className = 'action-analysis error';
                        analysisBox.textContent = t('web.js.action.check_failed', {error: error.message});
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

function buildModerationAnnouncementEditor(mod, s, value) {
    const defaults = cloneConfig(s.default || {});
    const config = {
        template: value && value.template != null ? String(value.template) : String(defaults.template || ''),
        case_id_format: value && value.case_id_format != null
            ? String(value.case_id_format)
            : String(defaults.case_id_format || '{roc_year}{sequence:04d}'),
    };
    const container = document.createElement('div');
    container.className = 'compound-editor moderation-announcement-editor';

    const templateInput = document.createElement('textarea');
    templateInput.className = 'form-textarea moderation-template-input';
    templateInput.rows = 12;
    templateInput.maxLength = 4000;
    templateInput.value = config.template;
    container.appendChild(buildCompoundField(t('web.js.modann.template'), templateInput));

    const caseFormatInput = document.createElement('input');
    caseFormatInput.type = 'text';
    caseFormatInput.className = 'form-input';
    caseFormatInput.maxLength = 100;
    caseFormatInput.value = config.case_id_format;
    caseFormatInput.placeholder = '{roc_year}-{sequence:04d}';
    container.appendChild(buildCompoundField(t('web.js.modann.case_format'), caseFormatInput));

    const help = document.createElement('div');
    help.className = 'compound-empty moderation-template-help';
    help.textContent = t('web.js.modann.help');
    container.appendChild(help);

    const actions = document.createElement('div');
    actions.className = 'compound-actions';
    const previewButton = document.createElement('button');
    previewButton.type = 'button';
    previewButton.className = 'compound-button primary';
    previewButton.textContent = t('web.js.modann.preview');
    const resetButton = document.createElement('button');
    resetButton.type = 'button';
    resetButton.className = 'compound-button danger';
    resetButton.textContent = t('web.js.modann.reset');
    actions.appendChild(previewButton);
    actions.appendChild(resetButton);
    container.appendChild(actions);

    const preview = document.createElement('div');
    preview.className = 'moderation-announcement-preview';
    container.appendChild(preview);

    function currentConfig() {
        return {
            template: templateInput.value,
            case_id_format: caseFormatInput.value,
        };
    }

    function save() {
        Object.assign(config, currentConfig());
        debounceSave(mod, s.database_key, cloneConfig(config));
    }

    function appendTextBlock(text) {
        if (!text) return;
        const block = document.createElement('pre');
        block.className = 'moderation-preview-content';
        block.textContent = text;
        preview.appendChild(block);
    }

    function appendEmbedCard(embed) {
        if (!embed) return;
        const card = document.createElement('div');
        card.className = 'moderation-preview-embed';
        const color = Number.isFinite(embed.color) ? embed.color : 0x5865F2;
        card.style.borderLeftColor = '#' + color.toString(16).padStart(6, '0');
        if (embed.author && embed.author.name) {
            const author = document.createElement('div');
            author.className = 'moderation-preview-author';
            author.textContent = embed.author.name;
            card.appendChild(author);
        }
        if (embed.title) {
            const title = document.createElement('div');
            title.className = 'moderation-preview-title';
            title.textContent = embed.title;
            card.appendChild(title);
        }
        if (embed.description) appendPreviewText(card, embed.description, 'moderation-preview-description');
        for (const field of (embed.fields || [])) {
            const fieldWrap = document.createElement('div');
            fieldWrap.className = 'moderation-preview-field';
            appendPreviewText(fieldWrap, field.name || '', 'moderation-preview-field-name');
            appendPreviewText(fieldWrap, field.value || '', 'moderation-preview-field-value');
            card.appendChild(fieldWrap);
        }
        if (embed.image && embed.image.url) {
            const image = document.createElement('img');
            image.className = 'moderation-preview-image';
            image.src = embed.image.url;
            image.alt = 'Embed image preview';
            card.appendChild(image);
        }
        if (embed.footer && embed.footer.text) {
            appendPreviewText(card, embed.footer.text, 'moderation-preview-footer');
        }
        preview.appendChild(card);
    }

    function appendPreviewText(parent, text, className) {
        const element = document.createElement('div');
        element.className = className;
        element.textContent = text;
        parent.appendChild(element);
    }

    async function renderPreview() {
        previewButton.disabled = true;
        preview.textContent = t('web.js.modann.generating');
        try {
            const response = await fetch(`/api/panel/guild/${GUILD_ID}/moderation-announcement-preview`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(currentConfig()),
            });
            const data = await response.json();
            preview.innerHTML = '';
            if (!response.ok || !data.success) {
                preview.textContent = data.error || t('web.js.modann.preview_failed');
                preview.classList.add('error');
                return;
            }
            preview.classList.remove('error');
            const caseInfo = document.createElement('div');
            caseInfo.className = 'compound-empty';
            caseInfo.textContent = t('web.js.modann.case_estimate', {case_id: data.case_id});
            preview.appendChild(caseInfo);
            appendTextBlock(data.content);
            appendEmbedCard(data.embed);
        } catch (error) {
            preview.textContent = t('web.js.modann.preview_error') + error.message;
            preview.classList.add('error');
        } finally {
            previewButton.disabled = false;
        }
    }

    templateInput.addEventListener('input', save);
    caseFormatInput.addEventListener('input', save);
    previewButton.addEventListener('click', renderPreview);
    resetButton.addEventListener('click', () => {
        templateInput.value = String(defaults.template || '');
        caseFormatInput.value = String(defaults.case_id_format || '{roc_year}{sequence:04d}');
        save();
        renderPreview();
    });
    return container;
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
    general.appendChild(buildCompoundToggle(t('web.js.fixlink.enable'), config.enabled, checked => { config.enabled = checked; save(); }));
    general.appendChild(buildCompoundToggle(t('web.js.fixlink.remove_tracker'), config.remove_tracker, checked => { config.remove_tracker = checked; save(); }));
    general.appendChild(buildCompoundToggle(t('web.js.fixlink.webhook_mode'), config.webhook_mode, checked => { config.webhook_mode = checked; save(); }));
    general.appendChild(buildCompoundToggle(t('web.js.fixlink.webhook_only'), config.webhook_only_with_tracker, checked => {
        config.webhook_only_with_tracker = checked;
        save();
    }));
    container.appendChild(general);

    const builtinSection = document.createElement('div');
    builtinSection.className = 'compound-section';
    const builtinTitle = document.createElement('div');
    builtinTitle.className = 'compound-section-title';
    builtinTitle.textContent = t('web.js.fixlink.builtin');
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
        const toggle = buildCompoundToggle(t('web.js.fixlink.enabled'), enabled, checked => {
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
        card.appendChild(buildCompoundField(t('web.js.fixlink.preferred'), select));
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
    customTitle.textContent = t('web.js.fixlink.custom_title', {current: config.custom_platforms.length, max: maxCustom});
    const addButton = document.createElement('button');
    addButton.type = 'button';
    addButton.className = 'compound-button primary';
    addButton.textContent = t('web.js.fixlink.add_custom');
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
        customTitle.textContent = t('web.js.fixlink.custom_title', {current: config.custom_platforms.length, max: maxCustom});
        addButton.disabled = config.custom_platforms.length + newDrafts.length >= maxCustom;
        const entries = [
            ...config.custom_platforms.map(item => ({ item: cloneConfig(item), isNew: false })),
            ...newDrafts.map(item => ({ item, isNew: true })),
        ];
        if (entries.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'compound-empty';
            empty.textContent = t('web.js.fixlink.no_custom');
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
            title.textContent = isNew ? t('web.js.fixlink.add_custom') : (item.name || t('web.js.fixlink.unnamed'));
            heading.appendChild(title);
            card.appendChild(heading);

            const nameInput = textControl(item.name, { placeholder: t('web.js.fixlink.name') });
            const originsInput = textControl((item.origins || []).join('\n'), { multiline: true, placeholder: 'example.com' });
            const pathsInput = textControl((item.path_prefixes || []).join('\n'), { multiline: true, placeholder: '/post/' });
            const keepInput = textControl((item.keep_query_keys || []).join('\n'), { multiline: true, placeholder: 'id\nlang' });
            const fixerNameInput = textControl(item.fixer.name, { placeholder: t('web.js.fixlink.fixer_name') });
            const endpointInput = textControl(item.fixer.endpoint, { placeholder: 'https://fix.example.com/embed' });
            const sourceParamInput = textControl(item.fixer.source_param || 'url', { placeholder: 'url' });
            const staticQuery = item.fixer.static_query || {};
            const staticInput = textControl(Object.entries(staticQuery).map(([key, val]) => `${key}=${val}`).join('\n'), {
                multiline: true,
                placeholder: 'v=1\nmode=embed',
            });
            card.appendChild(buildCompoundField(t('web.js.fixlink.name'), nameInput));
            card.appendChild(buildCompoundField(t('web.js.fixlink.origins'), originsInput));
            card.appendChild(buildCompoundField(t('web.js.fixlink.paths'), pathsInput));
            card.appendChild(buildCompoundField(t('web.js.fixlink.keep_query'), keepInput));
            card.appendChild(buildCompoundField(t('web.js.fixlink.fixer_name'), fixerNameInput));
            card.appendChild(buildCompoundField('HTTPS endpoint', endpointInput));
            card.appendChild(buildCompoundField(t('web.js.fixlink.source_param'), sourceParamInput));
            card.appendChild(buildCompoundField(t('web.js.fixlink.static_query'), staticInput));

            const actions = document.createElement('div');
            actions.className = 'compound-actions';
            const saveButton = document.createElement('button');
            saveButton.type = 'button';
            saveButton.className = 'compound-button primary';
            saveButton.textContent = t('web.js.fixlink.save_platform');
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
                const enabledToggle = buildCompoundToggle(t('web.js.fixlink.enable_platform'), !config.disabled_platforms.includes(customKey), checked => {
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
            removeButton.textContent = isNew ? t('web.js.common.cancel') : t('web.js.common.delete');
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
    general.appendChild(buildCompoundToggle(t('web.js.antibeast.enable'), config.enabled, checked => { config.enabled = checked; save(); }));
    general.appendChild(buildCompoundToggle(t('web.js.antibeast.enable_kick'), config.kick.enabled, checked => { config.kick.enabled = checked; save(); }));
    general.appendChild(buildCompoundToggle(t('web.js.antibeast.only_everyone'), config.kick.only_everyone_here, checked => {
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
    limits.appendChild(buildCompoundField(t('web.js.antibeast.threshold'), threshold));
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
    limits.appendChild(buildCompoundField(t('web.js.antibeast.window'), windowInput));
    container.appendChild(limits);

    const roleSection = document.createElement('div');
    roleSection.className = 'compound-section';
    const roleTitle = document.createElement('div');
    roleTitle.className = 'compound-section-title';
    roleTitle.textContent = t('web.js.antibeast.bypass');
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
            empty.textContent = t('web.js.antibeast.no_bypass');
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
        roleSelect.innerHTML = '<option value="">' + t('web.js.antibeast.add_bypass') + '</option>';
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
    actionTitle.textContent = t('web.js.antibeast.action');
    actionSection.appendChild(actionTitle);
    const actionEditor = document.createElement('div');
    actionEditor.className = 'action-input-editor';
    const actionInput = document.createElement('input');
    actionInput.type = 'text';
    actionInput.className = 'form-input';
    actionInput.value = config.kick.action;
    actionInput.placeholder = t('web.js.action.input_ph');
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
            analysisBox.textContent = t('web.js.action.required');
            return;
        }
        analysisBox.className = 'action-analysis loading';
        analysisBox.textContent = t('web.js.action.parsing');
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
                            else renderActionAnalysis(analysisBox, { valid: false, error: (result && result.error) || t('web.js.common.save_failed') });
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
                else renderActionAnalysis(analysisBox, { valid: false, error: (result && result.error) || t('web.js.common.save_failed') });
            });
        } catch (error) {
            if (currentRevision !== revision) return;
            renderActionAnalysis(analysisBox, { valid: false, error: t('web.js.action.check_failed', {error: error.message}) });
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
    container.appendChild(addRow(t('web.js.webverify.enable'), enabledWrap));

    const captchaSelect = document.createElement('select');
    captchaSelect.className = 'form-select';
    [ { v: 'none', l: t('web.js.webverify.captcha_none') }, { v: 'turnstile', l: 'Cloudflare Turnstile' }, { v: 'recaptcha', l: 'Google reCAPTCHA' } ].forEach(o => {
        const opt = document.createElement('option');
        opt.value = o.v;
        opt.textContent = o.l;
        if ((config.captcha_type || 'turnstile') === o.v) opt.selected = true;
        captchaSelect.appendChild(opt);
    });
    captchaSelect.addEventListener('change', () => { config.captcha_type = captchaSelect.value; save(); });
    container.appendChild(addRow(t('web.js.webverify.captcha'), captchaSelect));

    const roleSelect = document.createElement('select');
    roleSelect.className = 'form-select';
    roleSelect.innerHTML = '<option value="">' + t('web.js.common.unset') + '</option>';
    for (const r of roles) {
        const opt = document.createElement('option');
        opt.value = r.id;
        opt.textContent = '@ ' + r.name;
        if (String(config.unverified_role_id) === String(r.id)) opt.selected = true;
        roleSelect.appendChild(opt);
    }
    roleSelect.addEventListener('change', () => { config.unverified_role_id = roleSelect.value || null; save(); });
    container.appendChild(addRow(t('web.js.webverify.unverified_role'), roleSelect));

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
    container.appendChild(addRow(t('web.js.webverify.autorole'), autoroleWrap));

    const triggerInput = document.createElement('input');
    triggerInput.type = 'text';
    triggerInput.className = 'form-input';
    triggerInput.placeholder = t('web.js.webverify.trigger_ph');
    triggerInput.value = (config.autorole_trigger || 'always').toString();
    triggerInput.addEventListener('input', () => { config.autorole_trigger = triggerInput.value; save(); });
    container.appendChild(addRow(t('web.js.webverify.trigger'), triggerInput));

    const minAgeInput = document.createElement('input');
    minAgeInput.type = 'number';
    minAgeInput.className = 'form-input';
    minAgeInput.min = 0;
    minAgeInput.value = config.min_age != null ? config.min_age : 7;
    minAgeInput.style.width = '5rem';
    minAgeInput.addEventListener('input', () => { config.min_age = minAgeInput.value; save(); });
    container.appendChild(addRow(t('web.js.webverify.min_age'), minAgeInput));

    const notifyTypeSelect = document.createElement('select');
    notifyTypeSelect.className = 'form-select';
    [ { v: 'dm', l: t('web.js.webverify.notify_dm') }, { v: 'channel', l: t('web.js.webverify.notify_channel') }, { v: 'both', l: t('web.js.webverify.notify_both') } ].forEach(o => {
        const opt = document.createElement('option');
        opt.value = o.v;
        opt.textContent = o.l;
        if ((config.notify.type || 'dm') === o.v) opt.selected = true;
        notifyTypeSelect.appendChild(opt);
    });
    notifyTypeSelect.addEventListener('change', () => { config.notify.type = notifyTypeSelect.value; save(); });
    container.appendChild(addRow(t('web.js.webverify.notify_type'), notifyTypeSelect));

    const notifyChannelSelect = document.createElement('select');
    notifyChannelSelect.className = 'form-select';
    notifyChannelSelect.innerHTML = '<option value="">' + t('web.js.common.unset') + '</option>';
    const textChannels = channels.filter(ch => ['text', 'news'].includes(ch.type));
    for (const ch of textChannels) {
        const opt = document.createElement('option');
        opt.value = ch.id;
        opt.textContent = (ch.category ? '[' + ch.category + '] ' : '') + ch.name;
        if (String(config.notify.channel_id) === String(ch.id)) opt.selected = true;
        notifyChannelSelect.appendChild(opt);
    }
    notifyChannelSelect.addEventListener('change', () => { config.notify.channel_id = notifyChannelSelect.value || null; save(); });
    container.appendChild(addRow(t('web.js.webverify.notify_ch'), notifyChannelSelect));

    const notifyTitleInput = document.createElement('input');
    notifyTitleInput.type = 'text';
    notifyTitleInput.className = 'form-input';
    notifyTitleInput.value = (config.notify.title || '伺服器網頁驗證').toString();
    notifyTitleInput.addEventListener('input', () => { config.notify.title = notifyTitleInput.value; save(); });
    container.appendChild(addRow(t('web.js.webverify.notify_title'), notifyTitleInput));

    const notifyMsgInput = document.createElement('textarea');
    notifyMsgInput.className = 'form-textarea';
    notifyMsgInput.rows = 2;
    notifyMsgInput.value = (config.notify.message || '請點擊下方按鈕進行網頁驗證：').toString();
    notifyMsgInput.addEventListener('input', () => { config.notify.message = notifyMsgInput.value; save(); });
    container.appendChild(addRow(t('web.js.webverify.notify_msg'), notifyMsgInput));

    const countrySection = document.createElement('div');
    countrySection.className = 'webverify-country-section';
    const countryTitle = document.createElement('div');
    countryTitle.className = 'webverify-section-title';
    countryTitle.textContent = t('web.js.webverify.country');
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
    countrySection.appendChild(addRow(t('web.js.webverify.country_enable'), countryEnabledWrap));

    const countryModeSelect = document.createElement('select');
    countryModeSelect.className = 'form-select';
    [ { v: 'blacklist', l: t('web.js.webverify.country_blacklist') }, { v: 'whitelist', l: t('web.js.webverify.country_whitelist') } ].forEach(o => {
        const opt = document.createElement('option');
        opt.value = o.v;
        opt.textContent = o.l;
        if ((config.webverify_country_alert.mode || 'blacklist') === o.v) opt.selected = true;
        countryModeSelect.appendChild(opt);
    });
    countryModeSelect.addEventListener('change', () => { config.webverify_country_alert.mode = countryModeSelect.value; save(); });
    countrySection.appendChild(addRow(t('web.js.webverify.country_mode'), countryModeSelect));

    const countriesInput = document.createElement('input');
    countriesInput.type = 'text';
    countriesInput.className = 'form-input';
    countriesInput.placeholder = 'US,CN,RU';
    countriesInput.value = Array.isArray(config.webverify_country_alert.countries) ? config.webverify_country_alert.countries.join(',') : '';
    countriesInput.addEventListener('input', () => {
        config.webverify_country_alert.countries = countriesInput.value.split(',').map(c => c.trim().toUpperCase()).filter(Boolean);
        save();
    });
    countrySection.appendChild(addRow(t('web.js.webverify.country_codes'), countriesInput));

    const countryChannelSelect = document.createElement('select');
    countryChannelSelect.className = 'form-select';
    countryChannelSelect.innerHTML = '<option value="">' + t('web.js.common.unset') + '</option>';
    for (const ch of textChannels) {
        const opt = document.createElement('option');
        opt.value = ch.id;
        opt.textContent = (ch.category ? '[' + ch.category + '] ' : '') + ch.name;
        if (String(config.webverify_country_alert.channel_id) === String(ch.id)) opt.selected = true;
        countryChannelSelect.appendChild(opt);
    }
    countryChannelSelect.addEventListener('change', () => { config.webverify_country_alert.channel_id = countryChannelSelect.value || null; save(); });
    countrySection.appendChild(addRow(t('web.js.webverify.country_ch'), countryChannelSelect));

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
    ta.placeholder = s.default_display != null ? String(s.default_display) : (s.display || '');
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
    // default_i18n_key 設定：以渲染後的在地化預設當 placeholder；
    // 欄位留空即儲存 null（後端會拒絕寫入等同渲染預設的值）
    input.placeholder = s.default_display != null ? String(s.default_display)
        : (s.default != null ? String(s.default) : '');
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
