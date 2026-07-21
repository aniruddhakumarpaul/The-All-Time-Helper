const PALETTE_RESULT_LIMIT = 30;
let palIdx = 0;
let palResults = [];

function iconSvg(path) {
    return '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' + path + '</svg>';
}

function paletteItem(title, meta, icon, action, keywords) {
    return {
        t: title,
        meta: meta,
        i: icon,
        a: action,
        search: [title, meta, keywords || ''].join(' ').toLowerCase(),
    };
}

function focusComposer() {
    const prompt = document.getElementById('prompt');
    if (!prompt) return;
    prompt.focus({ preventScroll: false });
    const end = prompt.value.length;
    prompt.setSelectionRange?.(end, end);
}

function paletteActions() {
    return [
        paletteItem('New Chat', 'Conversation', iconSvg('<path d="M12 5v14M5 12h14"></path>'), () => window.startNewChat?.(), 'start reset'),
        paletteItem('Focus Composer', 'Navigation', iconSvg('<path d="M4 5h16v12H8l-4 4V5z"></path>'), focusComposer, 'prompt message type'),
        paletteItem('Export Current Chat', 'Conversation', iconSvg('<path d="M12 3v12M7 10l5 5 5-5M5 19h14"></path>'), () => window.exportChat?.(), 'download markdown'),
        paletteItem('Active Tasks', 'Workspace', iconSvg('<path d="M9 11l3 3L22 4"></path><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"></path>'), () => window.openJobCenter?.(), 'jobs queue running'),
        paletteItem('System Status', 'Workspace', iconSvg('<path d="M4 19V9M10 19V5M16 19v-7M22 19V3"></path>'), () => window.openAdminOpsDashboard?.(), 'health diagnostics agents'),
        paletteItem('Settings', 'Workspace', iconSvg('<circle cx="12" cy="12" r="3"></circle><path d="M4 12h2M18 12h2M12 4v2M12 18v2"></path>'), () => window.openSettings?.(), 'preferences'),
        paletteItem('Clear Attachment', 'Composer', iconSvg('<path d="M5 7h14M9 7V5h6v2M8 7l1 13h6l1-13"></path>'), () => window.clearImgPreview?.(), 'remove file image'),
        paletteItem('Stop Current Generation', 'Assistant', iconSvg('<rect x="7" y="7" width="10" height="10"></rect>'), () => window.stopAI?.(), 'cancel response'),
    ];
}

function paletteThemes() {
    return [
        paletteItem('Dark Theme', 'Appearance', 'D', () => window.applyThemeChoice?.('dark'), 'night'),
        paletteItem('Light Theme', 'Appearance', 'L', () => window.applyThemeChoice?.('light'), 'day'),
        paletteItem('System Theme', 'Appearance', 'S', () => window.applyThemeChoice?.('system'), 'automatic'),
    ];
}

function paletteModels() {
    return [
        { t: 'Helper Auto', i: 'A', id: 'helper-auto', name: 'Helper Auto', meta: 'Automatic route' },
        { t: 'Gemma 4 Cloud', i: 'G', id: 'agentic-pro', name: 'Gemma 4 Cloud (Free)', meta: 'Cloud route' },
        { t: 'Cloud Code Partner', i: 'C', id: 'openrouter-free-code', name: 'Cloud Code Partner (Free)', meta: 'Code route' },
        { t: 'Private Vision', i: 'V', id: 'gemma4:e2b', name: 'Private Vision', meta: 'On-device route' },
        { t: 'Private Fast', i: 'F', id: 'gemma2:2b', name: 'Private Fast', meta: 'On-device route' },
        { t: 'Personal Model', i: 'P', id: 'helper', name: 'Personal Model', meta: 'On-device route' },
    ].map(model => ({
        ...paletteItem('Route: ' + model.t, model.meta, model.i, () => window.selModel?.(model.id, model.name), 'model assistant'),
        id: model.id,
        name: model.name,
    }));
}

function paletteChats(query) {
    const chats = Array.isArray(window.chats) ? window.chats : [];
    const normalized = String(query || '').toLowerCase();
    return chats
        .filter(chat => chat.title && (!normalized || chat.title.toLowerCase().includes(normalized)))
        .slice(0, normalized ? 20 : 4)
        .map(chat => paletteItem(
            chat.title,
            'Saved conversation',
            iconSvg('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>'),
            () => window.loadChat?.(chat.id),
            'chat history'
        ));
}

function installModelMenuFromPalette() {
    const menu = document.getElementById('model-menu');
    if (!menu || menu.dataset.paletteModelMenu === 'true') return;
    if (menu.querySelector('[data-model-id]')) {
        menu.dataset.paletteModelMenu = 'true';
        return;
    }
    menu.dataset.paletteModelMenu = 'true';
    const models = paletteModels();
    menu.textContent = '';
    const cloudHeader = document.createElement('div');
    cloudHeader.className = 'dropdown-header';
    cloudHeader.textContent = 'HELPER ROUTES';
    menu.appendChild(cloudHeader);
    models.slice(0, 3).forEach(model => menu.appendChild(modelMenuButton(model)));
    const localHeader = document.createElement('div');
    localHeader.className = 'dropdown-header dropdown-header-local';
    localHeader.textContent = 'PRIVATE / ON DEVICE';
    menu.appendChild(localHeader);
    models.slice(3).forEach(model => menu.appendChild(modelMenuButton(model)));
}

function modelMenuButton(model) {
    const option = document.createElement('button');
    option.type = 'button';
    option.className = 'model-opt';
    option.setAttribute('role', 'option');
    option.setAttribute('aria-selected', 'false');
    option.dataset.modelId = model.id;
    option.dataset.modelName = model.name;
    option.textContent = model.name;
    option.addEventListener('click', model.a);
    return option;
}

function paletteVisible() {
    const palette = document.getElementById('cmd-palette');
    return Boolean(palette && window.getComputedStyle(palette).display === 'flex');
}

function openPalette() {
    const palette = document.getElementById('cmd-palette');
    const input = document.getElementById('pal-in');
    if (!palette || !input) return;
    if (paletteVisible()) {
        closePalette();
        return;
    }
    const activeDialog = window.HelperDialogs?.getActive();
    if (activeDialog && activeDialog !== palette) return;
    palette.style.display = 'flex';
    input.setAttribute('aria-expanded', 'true');
    input.value = '';
    updPal('');
    window.HelperDialogs?.sync();
    window.requestAnimationFrame(() => input.focus({ preventScroll: true }));
}

function closePalette() {
    const palette = document.getElementById('cmd-palette');
    const input = document.getElementById('pal-in');
    if (palette) palette.style.display = 'none';
    input?.setAttribute('aria-expanded', 'false');
    input?.removeAttribute('aria-activedescendant');
    window.HelperDialogs?.sync();
}

function updPal(query) {
    const normalized = String(query || '').trim().toLowerCase();
    const candidates = [...paletteActions(), ...paletteModels(), ...paletteThemes(), ...paletteChats(normalized)];
    palResults = candidates
        .filter(item => !normalized || item.search.includes(normalized))
        .slice(0, PALETTE_RESULT_LIMIT);
    palIdx = palResults.length ? 0 : -1;
    renderPal();
}

function renderPal() {
    const list = document.getElementById('pal-results');
    const count = document.getElementById('pal-count');
    if (!list) return;
    list.textContent = '';
    if (count) count.textContent = palResults.length ? String(palResults.length) + ' available' : 'No matches';

    if (!palResults.length) {
        const empty = document.createElement('div');
        empty.className = 'pal-empty';
        empty.setAttribute('role', 'status');
        empty.textContent = 'No commands or conversations match your search.';
        list.appendChild(empty);
        updatePaletteSelection(false);
        return;
    }

    palResults.forEach((result, index) => {
        const row = document.createElement('button');
        row.type = 'button';
        row.id = 'pal-option-' + index;
        row.className = 'pal-item';
        row.setAttribute('role', 'option');
        row.dataset.paletteIndex = String(index);

        const icon = document.createElement('span');
        icon.className = 'pal-icon';
        if (String(result.i).startsWith('<svg')) icon.innerHTML = result.i;
        else icon.textContent = result.i;

        const copy = document.createElement('span');
        copy.className = 'pal-copy';
        const label = document.createElement('strong');
        label.textContent = result.t;
        const meta = document.createElement('small');
        meta.textContent = result.meta;
        copy.append(label, meta);

        row.append(icon, copy);
        row.addEventListener('pointerenter', () => setPaletteIndex(index, false));
        row.addEventListener('click', () => runPaletteResult(index));
        list.appendChild(row);
    });
    updatePaletteSelection(false);
}

function setPaletteIndex(index, shouldScroll = true) {
    if (!palResults.length) return;
    palIdx = Math.max(0, Math.min(index, palResults.length - 1));
    updatePaletteSelection(shouldScroll);
}

function updatePaletteSelection(shouldScroll = true) {
    const input = document.getElementById('pal-in');
    const rows = Array.from(document.querySelectorAll('#pal-results .pal-item'));
    rows.forEach((row, index) => {
        const selected = index === palIdx;
        row.classList.toggle('selected', selected);
        row.setAttribute('aria-selected', String(selected));
    });
    const active = rows[palIdx];
    if (active) {
        input?.setAttribute('aria-activedescendant', active.id);
        if (shouldScroll) active.scrollIntoView({ block: 'nearest' });
    } else {
        input?.removeAttribute('aria-activedescendant');
    }
}

function runPaletteResult(index) {
    const result = palResults[index];
    if (!result) return;
    closePalette();
    result.a();
}

function selectPal() {
    runPaletteResult(palIdx);
}

function installPaletteEvents() {
    const palette = document.getElementById('cmd-palette');
    const input = document.getElementById('pal-in');
    const close = document.getElementById('pal-close');
    input?.addEventListener('input', event => updPal(event.currentTarget.value));
    close?.addEventListener('click', closePalette);
    palette?.addEventListener('pointerdown', event => {
        if (event.target === palette) closePalette();
    });
}

window.addEventListener('keydown', event => {
    const visible = paletteVisible();
    const palette = document.getElementById('cmd-palette');

    if (event.key === 'Escape' && visible) {
        if (window.HelperDialogs && !window.HelperDialogs.isTop(palette)) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        closePalette();
        return;
    }

    if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === 'k') {
        const activeDialog = window.HelperDialogs?.getActive();
        const auth = document.getElementById('auth-overlay');
        const authVisible = auth && window.getComputedStyle(auth).display !== 'none';
        if ((activeDialog && activeDialog !== palette) || authVisible) return;
        event.preventDefault();
        openPalette();
        return;
    }

    if (!visible) return;
    if (event.key === 'ArrowDown') {
        event.preventDefault();
        setPaletteIndex(palIdx + 1);
    } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        setPaletteIndex(palIdx - 1);
    } else if (event.key === 'Home') {
        event.preventDefault();
        setPaletteIndex(0);
    } else if (event.key === 'End') {
        event.preventDefault();
        setPaletteIndex(palResults.length - 1);
    } else if (event.key === 'Enter') {
        event.preventDefault();
        selectPal();
    }
});

document.addEventListener('DOMContentLoaded', () => {
    installModelMenuFromPalette();
    installPaletteEvents();
});
window.openPalette = openPalette;
window.closePalette = closePalette;
window.updPal = updPal;
window.installModelMenuFromPalette = installModelMenuFromPalette;