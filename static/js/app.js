/**
 * app.js — ES6 Module Entry Point & Orchestrator
 * Imports modular components. Contains send/load/save orchestration from main_v3.js.
 */
import { state } from './state.js';
import { api } from './api.js';
import { ui } from './ui.js';
import { mascot } from './mascot.js';

// --- Upscale Poller ---
function startUpscalePoller(jobId, container) {
    if (state.activePollers.has(jobId)) return;
    state.activePollers.add(jobId);
    const img = container.querySelector('.chat-rendered-img');
    if (!img) { state.activePollers.delete(jobId); return; }
    if (!img.parentElement.classList.contains('upscale-container')) {
        const w = document.createElement('div'); w.className = 'upscale-container';
        img.parentNode.insertBefore(w, img); w.appendChild(img);
    }
    img.classList.add('upscaling');
    const badge = document.createElement('div'); badge.className = 'upscale-badge';
    badge.innerHTML = '<div class="spinner" style="width:12px;height:12px;margin-right:5px;border-width:2px;"></div> Enhancing...';
    img.parentElement.appendChild(badge);
    const poll = async () => {
        try {
            const data = await api.checkUpscaleStatus(jobId);
            if (data.success && data.status === 'ready') {
                const hi = new Image(); hi.src = data.url;
                hi.onload = () => { img.src = data.url; img.classList.remove('upscaling'); badge.innerHTML = '✨ 4K Enhanced'; badge.classList.add('ready'); setTimeout(() => { badge.style.opacity = '0'; setTimeout(() => badge.remove(), 500); }, 4000); state.activePollers.delete(jobId); };
            } else if (data.status === 'failed') { img.classList.remove('upscaling'); badge.remove(); state.activePollers.delete(jobId); }
            else setTimeout(poll, 2500);
        } catch (e) { state.activePollers.delete(jobId); }
    };
    poll();
}

// --- Chat Key Handler ---
function handleChatKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    else if (e.key === 'Escape') startNewChat();
}

// --- Core: Start New Chat ---
function startNewChat() {
    state.set('activeId', Date.now().toString());
    document.getElementById('chat-area').innerHTML = '';
    document.getElementById('chat-area').style.display = 'none';
    document.getElementById('welcome').style.display = 'flex';
    ui.clearImgPreview();
    const p = document.getElementById('prompt');
    if (p) { p.value = ''; p.style.height = 'auto'; }
    ui.renderHist();
    if (window.innerWidth <= 850 && document.getElementById('sidebar').classList.contains('open')) ui.toggleSidebar();
    ui.smartFocus('prompt');
}

// --- Core: Load Chat ---
function loadChat(id) {
    state.set('activeId', id);
    localStorage.setItem('helper_active_chat_v2', id);
    const chat = state.chats.find(c => c.id === id);
    document.getElementById('chat-area').innerHTML = '';
    document.getElementById('chat-area').style.display = 'block';
    document.getElementById('welcome').style.display = 'none';
    ui.clearImgPreview();
    chat.ms.forEach((m, idx) => ui.addMsg(m.r, m.c, m.i, idx, m.m || 'AI Assistant', m.masked));
    ui.renderHist();
    if (window.innerWidth <= 850 && document.getElementById('sidebar').classList.contains('open')) ui.toggleSidebar();
    ui.smartFocus('prompt');
    ui.checkAuthMode();
}

// --- Core: Save/Load User Chats ---
async function loadUserChats() {
    if (!state.user || !state.user.email) return;
    const key = 'helper_chats_v2_' + state.user.email;
    let localStr = localStorage.getItem(key);
    if (!localStr && localStorage.getItem('helper_chats_v2')) {
        localStr = localStorage.getItem('helper_chats_v2');
        localStorage.setItem(key, localStr); localStorage.removeItem('helper_chats_v2');
    }
    if (localStr) {
        state.chats = JSON.parse(localStr); window.chats = state.chats; ui.renderHist();
        const sId = localStorage.getItem('helper_active_chat_v2');
        if (sId && state.chats.find(c => c.id === sId)) loadChat(sId);
    }
    try {
        const data = await api.fetchChats();
        if (data && data.success && data.chats) {
            if (data.chats.length > 0 || state.chats.length === 0) {
                state.chats = data.chats; window.chats = state.chats;
                localStorage.setItem(key, JSON.stringify(state.chats)); ui.renderHist();
            }
        }
    } catch (e) { console.error("Cloud fetch failed:", e); }
}

async function saveUserChats() {
    if (!state.user) return;
    localStorage.setItem('helper_chats_v2_' + state.user.email, JSON.stringify(state.chats));
    await api.syncChats(state.chats);
}

// --- Core: Handle Auth (with button loading state) ---
async function handleAuth(t) {
    const btn = document.getElementById(t + '-btn');
    const orig = btn.innerHTML;
    btn.disabled = true; btn.innerHTML = '<div class="spinner"></div>';
    try {
        const data = await api.handleAuth(t);
        if (data.success) {
            if (t === 'signup' || (t === 'login' && data.unverified)) ui.switchAuth('otp');
            else {
                state.set('user', data.user);
                localStorage.setItem('helper_user_v2', JSON.stringify(data.user));
                if (data.token) localStorage.setItem('helper_token_v2', data.token);
                document.getElementById('auth-overlay').style.display = 'none';
                loadUserChats(); ui.updUI();
                if (!localStorage.getItem('helper_theme_pref')) document.getElementById('theme-modal').style.display = 'flex';
                ui.smartFocus('prompt');
            }
        } else alert(data.error || 'Check credentials');
    } catch (e) { alert('Connection Error: ' + e.message); }
    finally { btn.disabled = false; btn.innerHTML = orig; }
}

// --- Core: Submit Edit ---
async function submitEdit(idx, container) {
    const newText = container.querySelector('textarea').value.trim();
    if (!newText) return;
    let chat = state.chats.find(c => c.id === state.activeId);
    if (!chat) return;
    chat.ms = chat.ms.slice(0, idx);
    loadChat(state.activeId);
    mascot.triggerBotReaction(newText);
    document.getElementById('prompt').value = newText;
    send();
}

// --- Core: Send Message ---
async function send() {
    const p = document.getElementById('prompt').value.trim();
    if (!p && !state.currentImg) return;
    if (!state.activeId) state.set('activeId', Date.now().toString());
    let chat = state.chats.find(c => c.id === state.activeId);
    if (!chat) { chat = { id: state.activeId, title: p.substring(0, 35), ms: [] }; state.chats.push(chat); }
    window.activeId = state.activeId;
    document.getElementById('welcome').style.display = 'none';
    document.getElementById('chat-area').style.display = 'block';

    let isMasked = false;
    const promptEl = document.getElementById('prompt');
    if (promptEl && promptEl.classList.contains('auth-waiting')) isMasked = true;
    else if (chat.ms.length > 0) {
        const last = chat.ms[chat.ms.length - 1].c.toLowerCase();
        const authKws = ["please provide your admin key", "enter your admin_key", "provide the password", "authorize with your key", "auth_required", "admin key"];
        if (authKws.some(kw => last.includes(kw))) isMasked = true;
    }

    ui.addMsg('u', p, state.currentImg, chat.ms.length, null, isMasked);
    chat.ms.push({ r: 'u', c: p, i: state.currentImg, masked: isMasked });
    mascot.triggerBotReaction(p);
    ui.clearImgPreview();
    promptEl.value = ''; promptEl.style.height = 'auto';
    document.getElementById('stop-btn').style.display = 'flex';
    document.getElementById('main-send-btn').style.display = 'none';
    promptEl.placeholder = "Message The All Time Helper...";
    promptEl.classList.remove('auth-waiting');

    let initContent = '...';
    const isLocal = state.selectedModel !== 'agentic-pro' && !state.selectedModel.includes('gemini');
    if (isLocal) initContent = 'Thinking... (Local Agent initializing tools, may take 10-20s)';
    const mName = document.getElementById('active-model-name').innerText;
    const bTxt = ui.addMsg('b', initContent, null, chat.ms.length, mName);
    const parentMsg = bTxt.closest('.msg');
    if (parentMsg) parentMsg.classList.add('thinking-state');
    bTxt.innerHTML = `<div class="status-msg"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><circle cx="12" cy="12" r="10"></circle><path d="M12 6v6l4 2"></path></svg> <span id="status-text">${initContent}</span></div><div class="typing-indicator"><span></span><span></span><span></span></div>`;
    mascot.updateBotVisuals();
    const m = document.getElementById('mascot-container');
    if (m) m.classList.add('thinking');
    state.set('abortController', new AbortController());

    try {
        const res = await api.streamChat({
            prompt: p, history: chat.ms, model: state.selectedModel, img: state.currentImg, name: state.user.name,
            persona: document.getElementById('persona-toggle').checked, isMasked,
            sys: { english: document.getElementById('t-eng').classList.contains('on'), oneword: document.getElementById('t-word').classList.contains('on'), pers: document.getElementById('t-pers').classList.contains('on') }
        }, state.abortController.signal);
        if (res.status === 401) { ui.signOut(); return; }
        if (!res.ok) {
            const errTxt = `System Error ${res.status}: Backend overloaded. Try again.`;
            bTxt.innerText = errTxt; chat.ms.push({ r: 'b', c: errTxt }); saveUserChats(); return;
        }
        const reader = res.body.getReader(); let fullTxt = '', buffer = ''; const decoder = new TextDecoder("utf-8");
        while (true) {
            const { done, value } = await reader.read(); if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n'); buffer = lines.pop();
            lines.forEach(line => {
                const tl = line.trim(); if (!tl || tl.startsWith('<')) return;
                try {
                    const j = JSON.parse(tl);
                    if (j.job_id) {
                        state.set('activeJobId', j.job_id);
                        return;
                    }
                    if (j.status) {
                        let se = bTxt.querySelector('#status-text');
                        if (!se) { const sd = document.createElement('div'); sd.className = 'status-msg'; sd.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><circle cx="12" cy="12" r="10"></circle><path d="M12 6v6l4 2"></path></svg> <span id="status-text">${j.status}</span>`; bTxt.prepend(sd); }
                        else se.innerText = j.status;
                        return;
                    }
                    if (j.message && j.message.content) {
                        if (fullTxt === '') { bTxt.querySelector('.typing-indicator')?.remove(); bTxt.querySelector('.status-msg')?.remove(); bTxt.closest('.msg').classList.remove('thinking-state'); }
                        fullTxt += j.message.content; bTxt.innerHTML = window.renderMarkdown(fullTxt);
                    }
                } catch (e) { if (tl.length > 5) console.warn("Dropped:", tl); }
            });
        }
        if (buffer.trim()) { try { const j = JSON.parse(buffer); if (j.message && j.message.content) fullTxt += j.message.content; } catch (e) {} }
        if (chat.title && chat.title.trim().length <= 5 && fullTxt.trim().length > 10) {
            const fl = fullTxt.split('\n')[0]; chat.title = fl.substring(0, 35).trim() + (fl.length > 35 ? '...' : '');
        }
        chat.ms.push({ r: 'b', c: fullTxt, m: mName });
        bTxt.innerHTML = window.renderMarkdown(fullTxt);
        bTxt.querySelectorAll('img').forEach(img => { if (img.src.includes('uid=')) { const jId = new URLSearchParams(img.src.split('?')[1]).get('uid'); if (jId) startUpscalePoller(jId, bTxt); } });
        bTxt.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
        saveUserChats();
    } catch (e) { bTxt.innerText += " [Stopped]"; }
    finally {
        document.getElementById('stop-btn').style.display = 'none';
        document.getElementById('main-send-btn').style.display = 'flex';
        ui.checkAuthMode(); if (m) m.classList.remove('thinking');
        document.querySelectorAll('.thinking-state').forEach(el => el.classList.remove('thinking-state'));
        document.querySelectorAll('.typing-indicator').forEach(ti => ti.remove());
        state.abortController = null; state.currentImg = null;
        state.activeJobId = null;
        window.activeId = state.activeId;
        if (state.chats.find(c => c.id === state.activeId)?.ms.length <= 2) ui.renderHist();
        ui.checkAuthMode();
    }
}

function stopAI() {
    if (state.activeJobId) api.cancelInferenceJob(state.activeJobId).catch(() => {});
    if (state.abortController) state.abortController.abort();
}

// --- Toggle Pin & Export ---
function togglePin(id) {
    const chat = state.chats.find(c => c.id === id);
    if (chat) { chat.pinned = !chat.pinned; saveUserChats(); ui.renderHist(); }
}
function exportChat() {
    const chat = state.chats.find(c => c.id === state.activeId);
    if (!chat || !chat.ms.length) return;
    let md = `# ${chat.title || 'Conversation'}\n\n`;
    chat.ms.forEach(m => { md += `### ${m.r === 'u' ? 'User' : 'Assistant'}\n${m.c}\n\n---\n\n`; });
    const blob = new Blob([md], { type: 'text/markdown' }); const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = `chat_${state.activeId}.md`; a.click(); URL.revokeObjectURL(url);
}

// --- Neural Context Retrieval ---
async function retrieveContext(text) {
    const m = document.getElementById('mascot-container'); if (m) m.classList.add('thinking');
    try {
        const data = await api.retrieveContext(text);
        if (data.success) ui.showNeuralContext(data.results, data.explanation);
    } finally { if (m) m.classList.remove('thinking'); }
}

// --- Theme Engine ---
window.applyThemeChoice = function(choice) {
    localStorage.setItem('helper_theme_pref', choice);
    const iconMap = { light: '☀️', dark: '🌙', system: '🌓' };
    const labels = { light: 'Light', dark: 'Dark', system: 'System' };
    const ti = document.getElementById('current-theme-icon'); if (ti) ti.innerText = iconMap[choice] || '🌓';
    const ts = document.getElementById('current-theme-icon-settings'); if (ts) ts.innerText = (iconMap[choice] || '🌓') + ' ' + (labels[choice] || 'System');
    document.querySelectorAll('.theme-opt, .menu-item').forEach(o => { o.classList.remove('active'); if (o.innerText.toLowerCase().includes(choice)) o.classList.add('active'); });
    if (choice === 'system') { ui.setThemeUI(window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'); } else ui.setThemeUI(choice);
    document.querySelectorAll('.dropdown-menu').forEach(m => m.style.display = 'none');
    document.querySelectorAll('.set-row').forEach(r => r.classList.remove('row-elevated'));
    if (document.getElementById('theme-modal').style.display === 'flex') setTimeout(() => document.getElementById('theme-modal').style.display = 'none', 400);
};
window.toggleThemeMenu = function(e, menuId) {
    if (e) e.stopPropagation(); const target = menuId || 'theme-menu'; const menu = document.getElementById(target); if (!menu) return;
    const vis = menu.style.display === 'flex';
    document.querySelectorAll('.dropdown-menu').forEach(m => m.style.display = 'none');
    document.querySelectorAll('.set-row').forEach(r => r.classList.remove('row-elevated'));
    if (!vis) { menu.style.display = 'flex'; const pr = menu.closest('.set-row'); if (pr) pr.classList.add('row-elevated'); }
};
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => { if (localStorage.getItem('helper_theme_pref') === 'system') ui.setThemeUI(e.matches ? 'dark' : 'light'); });
function initTheme() { window.applyThemeChoice(localStorage.getItem('helper_theme_pref') || 'system'); }

// --- Auto-resize ---
window.autoRes = function(el) { if (!el) return; el.style.height = 'auto'; el.style.height = el.scrollHeight + 'px'; };

// --- Sidebar Swipe ---
function initSidebarSwipe() {
    const sb = document.getElementById('sidebar'), scr = document.getElementById('sidebar-scrim');
    let sX = 0, cX = 300, isD = false, isH = false;
    if (!sb || !scr) return;
    sb.addEventListener('touchstart', e => { if (!sb.classList.contains('open') || window.innerWidth > 992) return; sX = e.touches[0].clientX; cX = 300; isD = true; isH = false; sb.style.transition = 'none'; scr.style.transition = 'none'; }, { passive: true });
    sb.addEventListener('touchmove', e => { if (!isD) return; const dX = e.touches[0].clientX - sX, dY = e.touches[0].clientY - sX; if (!isH) { if (Math.abs(dX) > Math.abs(dY) * 1.5) isH = true; else if (Math.abs(dY) > 5) { isD = false; return; } else return; } cX = Math.min(300, Math.max(0, 300 + dX)); sb.style.transform = `translateX(${cX}px)`; scr.style.opacity = cX / 300; }, { passive: true });
    sb.addEventListener('touchend', () => { if (!isD) return; isD = false; sb.style.transition = 'transform 0.4s cubic-bezier(0.25,0.8,0.25,1)'; scr.style.transition = 'opacity 0.4s ease'; if (cX < 200) { sb.style.transform = 'translateX(0px)'; scr.style.opacity = '0'; setTimeout(() => { sb.classList.remove('open'); document.body.classList.remove('sidebar-open'); sb.style.transform = ''; sb.style.transition = ''; scr.style.opacity = ''; scr.style.transition = ''; }, 400); } else { sb.style.transform = 'translateX(300px)'; scr.style.opacity = '1'; setTimeout(() => { sb.style.transition = ''; scr.style.transition = ''; }, 400); } });
    sb.onclick = e => e.stopPropagation();
}

// --- Neural Grab (Hold G) ---
function initNeuralGrab() {
    window.isGDown = false;
    function upd(on) {
        window.isGDown = on;
        document.querySelectorAll('.msg .txt').forEach(m => { m.setAttribute('draggable', on ? 'true' : 'false'); m.classList.toggle('grab-mode', on); });
        document.body.classList.toggle('neural-grab-active', on);
    }
    document.addEventListener('keydown', e => { if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return; if (e.key.toLowerCase() === 'g' && !window.isGDown) upd(true); });
    document.addEventListener('keyup', e => { if (e.key.toLowerCase() === 'g') upd(false); });
    window.addEventListener('blur', () => upd(false));
}

// --- Pull to Refresh ---
function initPullRefresh() {
    let tsy = 0, tdy = 0;
    window.addEventListener('touchstart', e => { const y = e.touches[0].pageY; tsy = ((window.scrollY === 0 || document.getElementById('chat-area').scrollTop === 0) && y < 60) ? y : 999999; }, { passive: true });
    window.addEventListener('touchmove', e => { tdy = e.touches[0].pageY - tsy; if (tdy > 0 && (window.scrollY === 0 || document.getElementById('chat-area').scrollTop === 0)) { const ind = document.getElementById('pull-indicator'); if (ind) { const p = Math.min(tdy, 180); ind.style.top = (p - 60) + 'px'; ind.style.opacity = Math.min(p / 120, 1); } } }, { passive: true });
    window.addEventListener('touchend', () => { if (tdy > 120) location.reload(); else { const ind = document.getElementById('pull-indicator'); if (ind) { ind.style.top = '-60px'; ind.style.opacity = '0'; } } tdy = 0; });
}

// ==================== DOMContentLoaded ====================
document.addEventListener('DOMContentLoaded', () => {
    try {
        console.log("DEBUG: app.js orchestrator initializing...");
        initTheme();
        document.getElementById('active-model-name').innerText = 'Gemma 4';

        const savedUser = localStorage.getItem('helper_user_v2');
        if (savedUser) {
            state.set('user', JSON.parse(savedUser));
            document.getElementById('auth-overlay').style.display = 'none';
            loadUserChats();
            if (localStorage.getItem('helper_active_modal_v2') === 'settings') ui.openSettings();
            ui.updUI();
            if (!localStorage.getItem('helper_theme_pref')) document.getElementById('theme-modal').style.display = 'flex';
            ui.smartFocus('prompt');
        } else { document.getElementById('l-email').focus(); ui.renderHist(); }

        // Mouse tracking
        mascot.bindMouseListeners();

        // Prompt input
        const promptIn = document.getElementById('prompt');
        const sendBtn = document.getElementById('main-send-btn');
        if (promptIn) {
            promptIn.addEventListener('input', () => { window.autoRes(promptIn); sendBtn?.classList.toggle('pulsing', promptIn.value.trim().length > 0); });
            promptIn.addEventListener('keydown', handleChatKey);
        }

        // Persona toggle
        const pt = document.getElementById('persona-toggle'), pi = document.querySelector('.persona-switch-item');
        function syncP() { if (pt && pi) pi.classList.toggle('persona-active', pt.checked); }
        if (pt) { pt.addEventListener('change', syncP); syncP(); }

        // Click-outside handlers
        document.addEventListener('click', e => {
            const sb = document.getElementById('sidebar');
            if (window.innerWidth <= 850 && sb?.classList.contains('open') && !sb.contains(e.target) && !document.getElementById('mobile-menu-btn')?.contains(e.target)) ui.toggleSidebar();
            const tm = document.getElementById('theme-menu');
            if (tm && tm.style.display === 'flex' && !tm.contains(e.target) && !document.getElementById('theme-btn')?.contains(e.target) && !document.getElementById('theme-btn-settings')?.contains(e.target)) tm.style.display = 'none';
            const mm = document.getElementById('model-menu');
            if (mm && mm.classList.contains('active') && !mm.contains(e.target) && !document.getElementById('model-toggle')?.contains(e.target)) mm.classList.remove('active');
        });

        // Image zoom
        (function() {
            const img = document.getElementById('modal-img'), cont = document.getElementById('image-modal');
            if (!img || !cont) return;
            img.onclick = e => { e.stopPropagation(); img.classList.toggle('is-zoomed'); };
            cont.onclick = e => { if (e.target === cont || e.target.classList.contains('lightbox-close')) { img.classList.remove('is-zoomed'); ui.closeImageModal(); } };
        })();

        // Popstate
        window.addEventListener('popstate', () => {
            if (document.getElementById('image-modal')?.classList.contains('active')) { ui.closeImageModal(); return; }
            if (document.getElementById('settings-modal')?.style.display === 'flex') { ui.closeSettings(); return; }
            if (document.getElementById('sidebar')?.classList.contains('open')) { ui.toggleSidebar(); return; }
            const c = document.getElementById('delete-confirm-modal'); if (c && c.style.display === 'flex') c.style.display = 'none';
        });

        // Escape key
        window.addEventListener('keydown', e => {
            if (e.key === 'Escape') {
                if (document.getElementById('image-modal')?.classList.contains('active')) { ui.closeImageModal(); return; }
                if (document.getElementById('settings-modal')?.style.display === 'flex') { ui.closeSettings(); return; }
                if (document.getElementById('delete-confirm-modal')?.style.display === 'flex') { document.getElementById('delete-confirm-modal').style.display = 'none'; return; }
                if (document.getElementById('sidebar')?.classList.contains('open')) ui.toggleSidebar();
            }
        });

        mascot.initMascotDrop(retrieveContext);
        initNeuralGrab();
        initSidebarSwipe();
        initPullRefresh();

        // --- Window Bridge ---
        window.handleAuth = handleAuth;
        window.switchAuth = ui.switchAuth;
        window.signOut = ui.signOut;
        window.toggleDropdown = ui.toggleDropdown;
        window.selModel = ui.selModel;
        window.send = send;
        window.startNewChat = startNewChat;
        window.loadChat = loadChat;
        window.showDeleteConfirm = ui.showDeleteConfirm;
        window.closeDeleteConfirm = ui.closeDeleteConfirm;
        window.clearImgPreview = ui.clearImgPreview;
        window.previewImg = ui.previewImg;
        window.toggleSidebar = ui.toggleSidebar;
        window.triggerBotReaction = mascot.triggerBotReaction;
        window.startEditPrompt = ui.startEditPrompt;
        window.cancelEdit = ui.cancelEdit;
        window.submitEdit = submitEdit;
        window.openSettings = ui.openSettings;
        window.closeSettings = ui.closeSettings;
        window.handleChatKey = handleChatKey;
        window.stopAI = stopAI;
        window.openImageModal = ui.openImageModal;
        window.closeImageModal = ui.closeImageModal;
        window.toggleSet = ui.toggleSet;
        window.filterHist = ui.filterHist;
        window.startRename = ui.startRename;
        window.closeNeuralContext = ui.closeNeuralContext;
        window.handleDragStart = ui.handleDragStart;
        window.handleDragEnd = ui.handleDragEnd;
        window.jiggleLogo = mascot.jiggleLogo;
        window.togglePin = togglePin;
        window.exportChat = exportChat;
        window.renderHist = ui.renderHist;
        window.chats = state.chats;
        window.activeId = state.activeId;

        console.log("DEBUG: app.js orchestrator ready.");
    } catch (e) { console.error("Critical Runtime Error:", e); }
});
