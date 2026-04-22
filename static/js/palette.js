/**
 * palette.js
 * Productivity engine for The All Time Helper - Pro
 */
let palIdx = 0;
let palResults = [];

window.addEventListener('keydown', (e) => {
    // 1. Unified Escape Handler
    if (e.key === 'Escape') {
        closePalette();
        if (window.closeSettings) window.closeSettings();
        if (window.closeImageModal) window.closeImageModal();
        if (window.closeDeleteConfirm) window.closeDeleteConfirm();
    }

    // 2. Open Palette
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        openPalette();
    }
    
    // 3. Palette Navigation
    const palette = document.getElementById('cmd-palette');
    // Using computed style to ensure visibility detection is robust
    const isPalVisible = palette && window.getComputedStyle(palette).display === 'flex';
    
    if (isPalVisible) {
        if (e.key === 'ArrowDown') { e.preventDefault(); palIdx = Math.min(palIdx + 1, palResults.length - 1); renderPal(); }
        if (e.key === 'ArrowUp') { e.preventDefault(); palIdx = Math.max(palIdx - 1, 0); renderPal(); }
        if (e.key === 'Enter') { e.preventDefault(); selectPal(); }
    }
});

function openPalette() {
    const p = document.getElementById('cmd-palette');
    p.style.display = 'flex';
    const input = document.getElementById('pal-in');
    input.value = '';
    input.focus();
    updPal('');
}

function closePalette() {
    document.getElementById('cmd-palette').style.display = 'none';
}

function updPal(q) {
    const list = document.getElementById('pal-results');
    list.innerHTML = '';
    palResults = [];
    palIdx = 0;

    // 1. Actions
    const actions = [
        { t: 'New Chat', i: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>', a: () => startNewChat() },
        { t: 'Dark Theme', i: '🌙', a: () => { if(window.applyThemeChoice) window.applyThemeChoice('dark'); } },
        { t: 'Light Theme', i: '☀️', a: () => { if(window.applyThemeChoice) window.applyThemeChoice('light'); } },
        { t: 'Settings', i: '⚙️', a: () => openSettings() }
    ];

    actions.forEach(act => {
        if (act.t.toLowerCase().includes(q.toLowerCase())) palResults.push(act);
    });

    // 2. Models
    const models = [
        { t: 'Model: Antigravity Pro', i: '💎', a: () => { if(window.selModel) window.selModel('gemini-1.5-pro-latest', 'Antigravity Pro (Cloud)'); } },
        { t: 'Model: Gemma 2', i: '⚡', a: () => { if(window.selModel) window.selModel('gemma2:2b', 'Gemma 2 (Fast&Fun)'); } },
        { t: 'Model: Llama (Sensitive)', i: '🦙', a: () => { if(window.selModel) window.selModel('helper', 'Llama (Sensitive)'); } }
    ];
    models.forEach(m => {
        if (m.t.toLowerCase().includes(q.toLowerCase())) palResults.push(m);
    });

    // 3. Chats
    if (window.chats) {
        window.chats.forEach(c => {
            if (c.title && c.title.toLowerCase().includes(q.toLowerCase())) {
                palResults.push({
                    t: 'Chat: ' + c.title,
                    i: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>',
                    a: () => loadChat(c.id)
                });
            }
        });
    }

    renderPal();
}

function renderPal() {
    const list = document.getElementById('pal-results');
    list.innerHTML = '';
    palResults.slice(0, 10).forEach((res, i) => {
        const div = document.createElement('div');
        div.className = `pal-item ${i === palIdx ? 'selected' : ''}`;
        div.innerHTML = `${res.i} <span>${res.t}</span>`;
        div.onclick = () => { res.a(); closePalette(); };
        list.appendChild(div);
        if (i === palIdx) div.scrollIntoView({ block: 'nearest' });
    });
}

function selectPal() {
    if (palResults[palIdx]) {
        palResults[palIdx].a();
        closePalette();
    }
}
