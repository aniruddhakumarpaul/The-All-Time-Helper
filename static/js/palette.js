let palIdx = 0;
let palResults = [];

function iconSvg(path) {
    return `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">${path}</svg>`;
}

function paletteActions() {
    return [
        { t: 'New Chat', i: iconSvg('<line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line>'), a: () => window.startNewChat?.() },
        { t: 'Export Current Chat', i: '⬇️', a: () => window.exportChat?.() },
        { t: 'Settings', i: '⚙️', a: () => window.openSettings?.() },
        { t: 'Clear Image Attachment', i: '🧹', a: () => window.clearImgPreview?.() },
        { t: 'Stop Current Generation', i: '⏹️', a: () => window.stopAI?.() },
        { t: 'Dark Theme', i: '🌙', a: () => window.applyThemeChoice?.('dark') },
        { t: 'Light Theme', i: '☀️', a: () => window.applyThemeChoice?.('light') },
        { t: 'System Theme', i: '🌓', a: () => window.applyThemeChoice?.('system') },
    ];
}

function paletteModels() {
    return [
        { t: 'Model: Free Agentic Workflow', i: '🆓', id: 'agentic-pro', name: 'Free Agentic Workflow (OpenRouter)' },
        { t: 'Model: Laguna XS Code Free', i: '🏊', id: 'openrouter-laguna-code', name: 'Laguna XS Code Free (OpenRouter)' },
        { t: 'Model: North Mini Code Free', i: '⌨️', id: 'openrouter-free-code', name: 'North Mini Code Free (OpenRouter)' },
        { t: 'Model: Nemotron Nano Free', i: '🧠', id: 'openrouter-nemotron-free', name: 'Nemotron Nano Free (OpenRouter)' },
        { t: 'Model: OpenRouter Auto', i: '🧭', id: 'openrouter-auto', name: 'OpenRouter Auto' },
        { t: 'Model: GLM 5.2 Paid Agentic', i: '💎', id: 'openrouter-glm-agentic', name: 'GLM 5.2 Paid Agentic (OpenRouter)' },
        { t: 'Model: Claude Sonnet 5 Paid', i: '💎', id: 'openrouter-claude-sonnet-5', name: 'Claude Sonnet 5 Paid (OpenRouter)' },
        { t: 'Model: Kimi K2.7 Code Paid', i: '⌨️', id: 'openrouter-kimi-code', name: 'Kimi K2.7 Code Paid (OpenRouter)' },
        { t: 'Model: Gemma 4 Local', i: '🔷', id: 'gemma4:e2b', name: 'Gemma 4' },
        { t: 'Model: Gemma 2 Local', i: '⚡', id: 'gemma2:2b', name: 'Gemma 2 (Fast&Fun)' },
        { t: 'Model: Mistral Local', i: '🌊', id: 'dolphin-mistral', name: 'Mistral (Uncensored)' },
        { t: 'Model: Llama Sensitive Local', i: '🦙', id: 'helper', name: 'Llama (Sensitive)' },
        { t: 'Model: Phi 3 Local', i: 'φ', id: 'phi3', name: 'Phi 3' },
        { t: 'Model: Moondream Vision Local', i: '👁️', id: 'moondream', name: 'Moondream (Vision)' },
    ].map(model => ({
        ...model,
        a: () => window.selModel?.(model.id, model.name),
    }));
}

function paletteChats(query) {
    const chats = Array.isArray(window.chats) ? window.chats : [];
    return chats
        .filter(chat => chat.title && chat.title.toLowerCase().includes(query.toLowerCase()))
        .slice(0, 20)
        .map(chat => ({
            t: 'Chat: ' + chat.title,
            i: iconSvg('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>'),
            a: () => window.loadChat?.(chat.id),
        }));
}

function installModelMenuFromPalette() {
    const menu = document.getElementById('model-menu');
    if (!menu || menu.dataset.paletteModelMenu === 'true') return;
    menu.dataset.paletteModelMenu = 'true';
    const models = paletteModels();
    menu.textContent = '';
    const cloudHeader = document.createElement('div');
    cloudHeader.className = 'dropdown-header';
    cloudHeader.textContent = 'Cloud via OpenRouter';
    menu.appendChild(cloudHeader);
    models.slice(0, 8).forEach(model => {
        const option = document.createElement('div');
        option.className = 'model-opt';
        option.dataset.modelId = model.id;
        option.dataset.modelName = model.name;
        option.textContent = model.t.replace('Model: ', '');
        option.addEventListener('click', model.a);
        menu.appendChild(option);
    });
    const localHeader = document.createElement('div');
    localHeader.className = 'dropdown-header';
    localHeader.style.borderTop = '1px solid var(--glass-border)';
    localHeader.textContent = 'Local (Private)';
    menu.appendChild(localHeader);
    models.slice(8).forEach(model => {
        const option = document.createElement('div');
        option.className = 'model-opt';
        option.dataset.modelId = model.id;
        option.dataset.modelName = model.name;
        option.textContent = model.t.replace('Model: ', '').replace(' Local', '');
        option.addEventListener('click', model.a);
        menu.appendChild(option);
    });
}

window.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
        closePalette();
        window.closeSettings?.();
        window.closeImageModal?.();
        window.closeDeleteConfirm?.();
    }

    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        openPalette();
    }

    const palette = document.getElementById('cmd-palette');
    const isVisible = palette && window.getComputedStyle(palette).display === 'flex';
    if (!isVisible) return;

    if (event.key === 'ArrowDown') { event.preventDefault(); palIdx = Math.min(palIdx + 1, palResults.length - 1); renderPal(); }
    if (event.key === 'ArrowUp') { event.preventDefault(); palIdx = Math.max(palIdx - 1, 0); renderPal(); }
    if (event.key === 'Enter') { event.preventDefault(); selectPal(); }
});

function openPalette() {
    const palette = document.getElementById('cmd-palette');
    const input = document.getElementById('pal-in');
    if (!palette || !input) return;
    palette.style.display = 'flex';
    input.value = '';
    input.focus();
    updPal('');
}

function closePalette() {
    const palette = document.getElementById('cmd-palette');
    if (palette) palette.style.display = 'none';
}

function updPal(query) {
    const normalized = String(query || '').trim().toLowerCase();
    palIdx = 0;
    palResults = [
        ...paletteActions(),
        ...paletteModels(),
        ...paletteChats(normalized),
    ].filter(item => item.t.toLowerCase().includes(normalized));
    renderPal();
}

function renderPal() {
    const list = document.getElementById('pal-results');
    if (!list) return;
    list.textContent = '';
    palResults.slice(0, 10).forEach((result, index) => {
        const row = document.createElement('div');
        row.className = `pal-item ${index === palIdx ? 'selected' : ''}`;
        const icon = document.createElement('span');
        icon.className = 'pal-icon';
        icon.innerHTML = result.i;
        const label = document.createElement('span');
        label.textContent = result.t;
        row.append(icon, label);
        row.addEventListener('click', () => { result.a(); closePalette(); });
        list.appendChild(row);
        if (index === palIdx) row.scrollIntoView({ block: 'nearest' });
    });
}

function selectPal() {
    if (!palResults[palIdx]) return;
    palResults[palIdx].a();
    closePalette();
}

document.addEventListener('DOMContentLoaded', installModelMenuFromPalette);
window.openPalette = openPalette;
window.closePalette = closePalette;
window.updPal = updPal;
window.installModelMenuFromPalette = installModelMenuFromPalette;
