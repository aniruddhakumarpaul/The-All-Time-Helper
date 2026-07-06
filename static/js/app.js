import { state } from './state.js';
import { api } from './api.js';
import { ui } from './ui.js?v=203';
import { mascot } from './mascot.js';
import { mergeChatsByRecency } from './chat_sync.js?v=203';

const MAX_ATTACHED_CONTEXTS = 6;
const MAX_CONTEXT_CHARS = 6000;
const MAX_TOTAL_CONTEXT_CHARS = 18000;
const CHAT_SYNC_DEBOUNCE_MS = 600;
let syncTimer = null;

function ensureDeletedChatIds() {
    if (!Array.isArray(state.deletedChatIds)) state.deletedChatIds = [];
    return state.deletedChatIds;
}

function syncWindowState() {
    window.chats = state.chats;
    window.activeId = state.activeId;
}

function persistLocalChatCache() {
    if (!state.user?.email) return;
    try {
        localStorage.setItem('helper_chats_v2_' + state.user.email, JSON.stringify(state.chats));
        if (state.activeId) localStorage.setItem('helper_active_chat_v2', state.activeId);
    } catch (error) {
        console.warn('Local chat cache could not be updated:', error);
    }
}

function addAttachedContext(text, kind = 'text') {
    const clean = String(text || '').trim();
    if (!clean || state.attachedContexts.length >= MAX_ATTACHED_CONTEXTS) return false;
    const currentTotal = state.attachedContexts.reduce((total, item) => total + item.text.length, 0);
    const allowed = Math.max(0, Math.min(MAX_CONTEXT_CHARS, MAX_TOTAL_CONTEXT_CHARS - currentTotal));
    if (!allowed) return false;
    const clipped = clean.slice(0, allowed);
    state.attachedContexts.push({ kind, text: clipped });
    if (clipped.length < clean.length) console.warn('Context truncated to keep this request within the model limit');
    return true;
}

function serializeAttachedContext(ctx) {
    return String(ctx?.text || '').slice(0, MAX_CONTEXT_CHARS);
}

function clearPendingComposerDrafts() {
    Object.keys(localStorage).forEach(key => {
        if (key.startsWith('helper_pending_prompt_')) localStorage.removeItem(key);
    });
    state.attachedContexts = [];
    state.currentImages = [];
    state.pendingImageUploads = null;
}

async function waitForPendingImageUploads() {
    if (!state.pendingImageUploads) return state.currentImages;
    await state.pendingImageUploads;
    return state.currentImages;
}

function startUpscalePoller(jobId, container) {
    if (state.activePollers.has(jobId)) return;
    state.activePollers.add(jobId);
    const img = container.querySelector('.chat-rendered-img');
    if (!img) { state.activePollers.delete(jobId); return; }
    if (!img.parentElement.classList.contains('upscale-container')) {
        const wrapper = document.createElement('div');
        wrapper.className = 'upscale-container';
        img.parentNode.insertBefore(wrapper, img);
        wrapper.appendChild(img);
    }
    img.classList.add('upscaling');
    const badge = document.createElement('div');
    badge.className = 'upscale-badge';
    badge.innerHTML = '<div class="spinner" style="width:12px;height:12px;margin-right:5px;border-width:2px;"></div> Enhancing...';
    img.parentElement.appendChild(badge);

    const poll = async () => {
        try {
            const data = await api.checkUpscaleStatus(jobId);
            if (data.success && data.status === 'ready') {
                const hi = new Image();
                hi.src = data.url;
                hi.onload = () => {
                    img.src = data.url;
                    img.classList.remove('upscaling');
                    badge.innerHTML = '✨ 4K Enhanced';
                    badge.classList.add('ready');
                    setTimeout(() => { badge.style.opacity = '0'; setTimeout(() => badge.remove(), 500); }, 4000);
                    state.activePollers.delete(jobId);
                };
            } else if (data.status === 'failed' || data.status === 'missing') {
                img.classList.remove('upscaling');
                badge.remove();
                state.activePollers.delete(jobId);
            } else {
                setTimeout(poll, 2500);
            }
        } catch (_) {
            state.activePollers.delete(jobId);
        }
    };
    poll();
}

function handleChatKey(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        send();
    } else if (event.key === 'Escape') {
        startNewChat();
    }
}

function startNewChat() {
    state.set('activeId', Date.now().toString());
    const chatArea = document.getElementById('chat-area');
    const welcome = document.getElementById('welcome');
    if (chatArea) { chatArea.innerHTML = ''; chatArea.style.display = 'none'; }
    if (welcome) welcome.style.display = 'flex';
    ui.clearImgPreview();
    const prompt = document.getElementById('prompt');
    if (prompt) { prompt.value = ''; prompt.style.height = 'auto'; }
    ui.renderHist();
    const sidebar = document.getElementById('sidebar');
    if (window.innerWidth <= 850 && sidebar?.classList.contains('open')) ui.toggleSidebar();
    ui.smartFocus('prompt');
    syncWindowState();
}

function loadChat(id) {
    const chat = state.chats.find(c => c.id === id);
    if (!chat) return;
    state.set('activeId', id);
    localStorage.setItem('helper_active_chat_v2', id);
    const chatArea = document.getElementById('chat-area');
    const welcome = document.getElementById('welcome');
    if (chatArea) { chatArea.innerHTML = ''; chatArea.style.display = 'block'; }
    if (welcome) welcome.style.display = 'none';
    ui.clearImgPreview();
    chat.ms.forEach((message, idx) => ui.addMsg(message.r, message.c, message.i, idx, message.m || 'AI Assistant', message.masked));
    window.initUpscaleImagePolling?.(chatArea);
    ui.renderHist();
    const sidebar = document.getElementById('sidebar');
    if (window.innerWidth <= 850 && sidebar?.classList.contains('open')) ui.toggleSidebar();
    ui.smartFocus('prompt');
    ui.checkAuthMode();
    syncWindowState();
}

async function loadUserChats() {
    if (!state.user?.email) return;
    const key = 'helper_chats_v2_' + state.user.email;
    let localStr = localStorage.getItem(key);
    if (!localStr && localStorage.getItem('helper_chats_v2')) {
        localStr = localStorage.getItem('helper_chats_v2');
        localStorage.setItem(key, localStr);
        localStorage.removeItem('helper_chats_v2');
    }
    if (localStr) {
        try {
            const parsed = JSON.parse(localStr);
            state.chats = mergeChatsByRecency(Array.isArray(parsed) ? parsed : [], []);
            syncWindowState();
            ui.renderHist();
            const savedId = localStorage.getItem('helper_active_chat_v2');
            if (savedId && state.chats.find(c => c.id === savedId)) loadChat(savedId);
        } catch (error) {
            console.warn('Local chat cache could not be parsed:', error);
        }
    }
    try {
        const data = await api.fetchChats();
        if (data?.success && Array.isArray(data.chats)) {
            state.chats = mergeChatsByRecency(state.chats, data.chats);
            syncWindowState();
            persistLocalChatCache();
            ui.renderHist();
            const savedId = localStorage.getItem('helper_active_chat_v2');
            if (savedId && state.chats.find(c => c.id === savedId)) loadChat(savedId);
        }
    } catch (error) {
        console.error('Cloud fetch failed:', error);
    }
}

async function saveUserChats() {
    if (!state.user?.email) return;
    const deletedIds = ensureDeletedChatIds();
    const payload = { chats: state.chats, deleted_chat_ids: deletedIds.slice() };
    persistLocalChatCache();
    const result = await api.syncChats(payload);
    if (!result || result.success !== false) deletedIds.length = 0;
    syncWindowState();
    return result;
}

function requestChatPersist({ immediate = false } = {}) {
    if (syncTimer) clearTimeout(syncTimer);
    persistLocalChatCache();
    if (immediate) return saveUserChats();
    syncTimer = setTimeout(() => { saveUserChats().catch(error => console.warn('Chat sync failed:', error)); }, CHAT_SYNC_DEBOUNCE_MS);
    return Promise.resolve();
}

async function handleAuth(type) {
    const btn = document.getElementById(type + '-btn');
    const original = btn?.innerHTML;
    if (btn) { btn.disabled = true; btn.innerHTML = '<div class="spinner"></div>'; }
    try {
        const data = await api.handleAuth(type);
        if (data.success) {
            if (type === 'signup' || (type === 'login' && data.unverified)) {
                ui.switchAuth('otp');
            } else {
                state.set('user', data.user);
                localStorage.setItem('helper_user_v2', JSON.stringify(data.user));
                if (data.token) localStorage.setItem('helper_token_v2', data.token);
                const auth = document.getElementById('auth-overlay');
                if (auth) auth.style.display = 'none';
                await loadUserChats();
                ui.updUI();
                const themeModal = document.getElementById('theme-modal');
                if (!localStorage.getItem('helper_theme_pref') && themeModal) themeModal.style.display = 'flex';
                ui.smartFocus('prompt');
            }
        } else {
            alert(data.error || 'Check credentials');
        }
    } catch (error) {
        alert('Connection Error: ' + error.message);
    } finally {
        if (btn) { btn.disabled = false; btn.innerHTML = original; }
    }
}

async function submitEdit(idx, container) {
    const textarea = container.querySelector('textarea');
    const newText = textarea?.value.trim();
    if (!newText) return;
    const chat = state.chats.find(c => c.id === state.activeId);
    if (!chat) return;
    chat.ms = chat.ms.slice(0, idx);
    await requestChatPersist({ immediate: true });
    loadChat(state.activeId);
    mascot.triggerBotReaction(newText);
    const prompt = document.getElementById('prompt');
    if (prompt) prompt.value = newText;
    await send();
}

async function send() {
    const promptEl = document.getElementById('prompt');
    if (!promptEl) return;
    const userText = promptEl.value.trim();
    await waitForPendingImageUploads();
    const currentAttachments = state.currentImages.slice();
    const contextText = state.attachedContexts.map(serializeAttachedContext).filter(Boolean)
        .map((text, index) => `[Attached Context ${index + 1}]\n"""\n${text}\n"""`).join('\n\n');
    const apiPrompt = [contextText, userText].filter(Boolean).join('\n\n');
    if (!apiPrompt && !currentAttachments.length) return;
    if (!state.activeId) state.set('activeId', Date.now().toString());

    let chat = state.chats.find(c => c.id === state.activeId);
    if (!chat) {
        chat = { id: state.activeId, title: userText.substring(0, 35) || 'New Chat', ms: [], updated_at: Date.now() };
        state.chats.push(chat);
    }
    syncWindowState();

    const welcome = document.getElementById('welcome');
    const chatArea = document.getElementById('chat-area');
    if (welcome) welcome.style.display = 'none';
    if (chatArea) chatArea.style.display = 'block';

    let isMasked = false;
    if (promptEl.classList.contains('auth-waiting')) isMasked = true;
    else if (chat.ms.length > 0) {
        const last = String(chat.ms[chat.ms.length - 1].c || '').toLowerCase();
        const authKeywords = ['please provide your admin key', 'enter your admin_key', 'provide the password', 'authorize with your key', 'auth_required', 'admin key'];
        isMasked = authKeywords.some(keyword => last.includes(keyword));
    }

    ui.addMsg('u', userText, state.currentImg, chat.ms.length, null, isMasked);
    chat.ms.push({ r: 'u', c: userText, i: state.currentImg, attachments: currentAttachments, apiPrompt, masked: isMasked });
    chat.updated_at = Date.now();
    requestChatPersist();
    mascot.triggerBotReaction(userText);
    ui.clearImgPreview();
    promptEl.value = '';
    promptEl.style.height = 'auto';
    promptEl.placeholder = 'Message The All Time Helper...';
    promptEl.classList.remove('auth-waiting');
    document.getElementById('stop-btn').style.display = 'flex';
    document.getElementById('main-send-btn').style.display = 'none';

    let initContent = '...';
    const isLocal = state.selectedModel !== 'agentic-pro' && !state.selectedModel.includes('gemini');
    if (isLocal) initContent = 'Thinking... (Local Agent initializing tools, may take 10-20s)';
    const modelName = document.getElementById('active-model-name')?.innerText || 'AI Assistant';
    const botText = ui.addMsg('b', initContent, null, chat.ms.length, modelName);
    const botMsg = botText.closest('.msg');
    if (botMsg) botMsg.classList.add('thinking-state');
    botText.innerHTML = `<div class="status-msg"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><circle cx="12" cy="12" r="10"></circle><path d="M12 6v6l4 2"></path></svg> <span id="status-text">${initContent}</span></div><div class="typing-indicator"><span></span><span></span><span></span></div>`;
    mascot.updateBotVisuals();
    const mascotEl = document.getElementById('mascot-container');
    if (mascotEl) mascotEl.classList.add('thinking');
    state.set('abortController', new AbortController());

    try {
        const historyForApi = chat.ms.map(message => ({
            role: message.r === 'u' ? 'user' : 'assistant',
            content: message.apiPrompt || message.c,
            attachments: message.attachments || []
        }));
        const response = await api.streamChat({
            prompt: apiPrompt,
            history: historyForApi,
            model: state.selectedModel,
            img: null,
            attachments: currentAttachments,
            name: state.user?.name || 'Human',
            persona: Boolean(document.getElementById('persona-toggle')?.checked),
            isMasked,
            sys: {
                english: Boolean(document.getElementById('t-eng')?.classList.contains('on')),
                oneword: Boolean(document.getElementById('t-word')?.classList.contains('on')),
                pers: Boolean(document.getElementById('t-pers')?.classList.contains('on'))
            }
        }, state.abortController.signal);

        if (response.status === 401) { ui.signOut(); return; }
        if (!response.ok) {
            const errorText = `System Error ${response.status}: Backend overloaded. Try again.`;
            botText.innerText = errorText;
            chat.ms.push({ r: 'b', c: errorText, m: modelName });
            chat.updated_at = Date.now();
            await requestChatPersist({ immediate: true });
            return;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let fullText = '';
        let buffer = '';
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();
            for (const line of lines) {
                const trimmed = line.trim();
                if (!trimmed || trimmed.startsWith('<')) continue;
                try {
                    const item = JSON.parse(trimmed);
                    if (item.job_id) { state.set('activeJobId', item.job_id); continue; }
                    if (item.status) {
                        let statusEl = botText.querySelector('#status-text');
                        if (!statusEl) {
                            const statusDiv = document.createElement('div');
                            statusDiv.className = 'status-msg';
                            statusDiv.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><circle cx="12" cy="12" r="10"></circle><path d="M12 6v6l4 2"></path></svg> <span id="status-text"></span>`;
                            botText.prepend(statusDiv);
                            statusEl = statusDiv.querySelector('#status-text');
                        }
                        statusEl.innerText = item.status;
                        continue;
                    }
                    if (item.message?.content) {
                        if (!fullText) {
                            botText.querySelector('.typing-indicator')?.remove();
                            botText.querySelector('.status-msg')?.remove();
                            botText.closest('.msg')?.classList.remove('thinking-state');
                        }
                        fullText += item.message.content;
                        botText.innerHTML = window.renderMarkdown(fullText);
                        window.hydrateRenderedMarkdown?.(botText);
                    }
                } catch (error) {
                    if (trimmed.length > 5) console.warn('Dropped stream line:', trimmed, error);
                }
            }
        }
        if (buffer.trim()) {
            try {
                const item = JSON.parse(buffer);
                if (item.message?.content) fullText += item.message.content;
            } catch (_) {}
        }
        if (chat.title && chat.title.trim().length <= 5 && fullText.trim().length > 10) {
            const firstLine = fullText.split('\n')[0];
            chat.title = firstLine.substring(0, 35).trim() + (firstLine.length > 35 ? '...' : '');
        }
        chat.ms.push({ r: 'b', c: fullText, m: modelName });
        chat.updated_at = Date.now();
        botText.innerHTML = window.renderMarkdown(fullText);
        window.hydrateRenderedMarkdown?.(botText);
        botText.querySelectorAll('img').forEach(img => {
            if (img.src.includes('uid=')) {
                const jobId = new URLSearchParams(img.src.split('?')[1]).get('uid');
                if (jobId) startUpscalePoller(jobId, botText);
            }
        });
        botText.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
        await requestChatPersist({ immediate: true });
    } catch (error) {
        if (error.name === 'AbortError') botText.innerText += ' [Stopped]';
        else botText.innerText += ` [Error: ${error.message}]`;
    } finally {
        const stopBtn = document.getElementById('stop-btn');
        const sendBtn = document.getElementById('main-send-btn');
        if (stopBtn) stopBtn.style.display = 'none';
        if (sendBtn) sendBtn.style.display = 'flex';
        ui.checkAuthMode();
        if (mascotEl) mascotEl.classList.remove('thinking');
        document.querySelectorAll('.thinking-state').forEach(el => el.classList.remove('thinking-state'));
        document.querySelectorAll('.typing-indicator').forEach(el => el.remove());
        state.abortController = null;
        state.currentImg = null;
        state.attachedContexts = [];
        state.currentImages = [];
        state.activeJobId = null;
        syncWindowState();
        if (state.chats.find(c => c.id === state.activeId)?.ms.length <= 2) ui.renderHist();
        ui.checkAuthMode();
    }
}

function stopAI() {
    if (state.activeJobId) api.cancelInferenceJob(state.activeJobId).catch(() => {});
    if (state.abortController) state.abortController.abort();
}

async function deleteSelectedChat() {
    if (!state.chatToDelete) return;
    const deletedId = state.chatToDelete;
    const deletedIds = ensureDeletedChatIds();
    if (!deletedIds.includes(deletedId)) deletedIds.push(deletedId);
    state.chats = state.chats.filter(chat => chat.id !== deletedId);
    if (state.activeId === deletedId) startNewChat();
    ui.closeDeleteConfirm();
    ui.renderHist();
    syncWindowState();
    await requestChatPersist({ immediate: true });
}

function togglePin(id) {
    const chat = state.chats.find(c => c.id === id);
    if (!chat) return;
    chat.pinned = !chat.pinned;
    chat.updated_at = Date.now();
    ui.renderHist();
    requestChatPersist();
}

function exportChat() {
    const chat = state.chats.find(c => c.id === state.activeId);
    if (!chat || !chat.ms.length) return;
    let md = `# ${chat.title || 'Conversation'}\n\n`;
    chat.ms.forEach(message => { md += `### ${message.r === 'u' ? 'User' : 'Assistant'}\n${message.c}\n\n---\n\n`; });
    const blob = new Blob([md], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `chat_${state.activeId}.md`;
    link.click();
    URL.revokeObjectURL(url);
}

async function retrieveContext(text) {
    const mascotEl = document.getElementById('mascot-container');
    if (mascotEl) mascotEl.classList.add('thinking');
    try {
        const data = await api.retrieveContext(text);
        if (data.success) ui.showNeuralContext(data.results, data.explanation);
    } finally {
        if (mascotEl) mascotEl.classList.remove('thinking');
    }
}

window.applyThemeChoice = function applyThemeChoice(choice) {
    localStorage.setItem('helper_theme_pref', choice);
    const iconMap = { light: '☀️', dark: '🌙', system: '🌓' };
    const labels = { light: 'Light', dark: 'Dark', system: 'System' };
    const headerIcon = document.getElementById('current-theme-icon');
    const settingsIcon = document.getElementById('current-theme-icon-settings');
    if (headerIcon) headerIcon.innerText = iconMap[choice] || '🌓';
    if (settingsIcon) settingsIcon.innerText = (iconMap[choice] || '🌓') + ' ' + (labels[choice] || 'System');
    document.querySelectorAll('.theme-opt, .menu-item').forEach(option => {
        option.classList.remove('active');
        if (option.innerText.toLowerCase().includes(choice)) option.classList.add('active');
    });
    const resolvedTheme = choice === 'system'
        ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
        : choice;
    ui.setThemeUI(resolvedTheme);
    document.querySelectorAll('.dropdown-menu').forEach(menu => { menu.style.display = 'none'; });
    document.querySelectorAll('.set-row').forEach(row => row.classList.remove('row-elevated'));
    const themeModal = document.getElementById('theme-modal');
    if (themeModal?.style.display === 'flex') setTimeout(() => { themeModal.style.display = 'none'; }, 400);
};

window.toggleThemeMenu = function toggleThemeMenu(event, menuId) {
    if (event) event.stopPropagation();
    const target = menuId || 'theme-menu';
    const menu = document.getElementById(target);
    if (!menu) return;
    const visible = menu.style.display === 'flex';
    document.querySelectorAll('.dropdown-menu').forEach(item => { item.style.display = 'none'; });
    document.querySelectorAll('.set-row').forEach(row => row.classList.remove('row-elevated'));
    if (!visible) {
        menu.style.display = 'flex';
        menu.closest('.set-row')?.classList.add('row-elevated');
    }
};

window.autoRes = function autoRes(el) {
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = el.scrollHeight + 'px';
};

function initTheme() {
    window.applyThemeChoice(localStorage.getItem('helper_theme_pref') || 'system');
}

function initSidebarSwipe() {
    const sidebar = document.getElementById('sidebar');
    const scrim = document.getElementById('sidebar-scrim');
    if (!sidebar || !scrim) return;
    let startX = 0;
    let currentX = 300;
    let dragging = false;
    let horizontal = false;
    sidebar.addEventListener('touchstart', event => {
        if (!sidebar.classList.contains('open') || window.innerWidth > 992) return;
        startX = event.touches[0].clientX;
        currentX = 300;
        dragging = true;
        horizontal = false;
        sidebar.style.transition = 'none';
        scrim.style.transition = 'none';
    }, { passive: true });
    sidebar.addEventListener('touchmove', event => {
        if (!dragging) return;
        const deltaX = event.touches[0].clientX - startX;
        const deltaY = event.touches[0].clientY - startX;
        if (!horizontal) {
            if (Math.abs(deltaX) > Math.abs(deltaY) * 1.5) horizontal = true;
            else if (Math.abs(deltaY) > 5) { dragging = false; return; }
            else return;
        }
        currentX = Math.min(300, Math.max(0, 300 + deltaX));
        sidebar.style.transform = `translateX(${currentX}px)`;
        scrim.style.opacity = currentX / 300;
    }, { passive: true });
    sidebar.addEventListener('touchend', () => {
        if (!dragging) return;
        dragging = false;
        sidebar.style.transition = 'transform 0.4s cubic-bezier(0.25,0.8,0.25,1)';
        scrim.style.transition = 'opacity 0.4s ease';
        if (currentX < 200) {
            sidebar.style.transform = 'translateX(0px)';
            scrim.style.opacity = '0';
            setTimeout(() => {
                sidebar.classList.remove('open');
                document.body.classList.remove('sidebar-open');
                sidebar.style.transform = '';
                sidebar.style.transition = '';
                scrim.style.opacity = '';
                scrim.style.transition = '';
            }, 400);
        } else {
            sidebar.style.transform = 'translateX(300px)';
            scrim.style.opacity = '1';
            setTimeout(() => { sidebar.style.transition = ''; scrim.style.transition = ''; }, 400);
        }
    });
    sidebar.onclick = event => event.stopPropagation();
}

function initNeuralGrab() {
    window.isGDown = false;
    function update(on) {
        window.isGDown = on;
        document.querySelectorAll('.msg .txt').forEach(message => {
            message.setAttribute('draggable', on ? 'true' : 'false');
            message.classList.toggle('grab-mode', on);
        });
        document.body.classList.toggle('neural-grab-active', on);
    }
    document.addEventListener('keydown', event => {
        if (event.target.tagName === 'INPUT' || event.target.tagName === 'TEXTAREA') return;
        if (event.key.toLowerCase() === 'g' && !window.isGDown) update(true);
    });
    document.addEventListener('keyup', event => { if (event.key.toLowerCase() === 'g') update(false); });
    window.addEventListener('blur', () => update(false));
}

function initPullRefresh() {
    let startY = 0;
    let deltaY = 0;
    window.addEventListener('touchstart', event => {
        const y = event.touches[0].pageY;
        const chatArea = document.getElementById('chat-area');
        startY = ((window.scrollY === 0 || chatArea?.scrollTop === 0) && y < 60) ? y : 999999;
    }, { passive: true });
    window.addEventListener('touchmove', event => {
        deltaY = event.touches[0].pageY - startY;
        const chatArea = document.getElementById('chat-area');
        if (deltaY > 0 && (window.scrollY === 0 || chatArea?.scrollTop === 0)) {
            const indicator = document.getElementById('pull-indicator');
            if (indicator) {
                const progress = Math.min(deltaY, 180);
                indicator.style.top = (progress - 60) + 'px';
                indicator.style.opacity = Math.min(progress / 120, 1);
            }
        }
    }, { passive: true });
    window.addEventListener('touchend', () => {
        if (deltaY > 120) location.reload();
        else {
            const indicator = document.getElementById('pull-indicator');
            if (indicator) { indicator.style.top = '-60px'; indicator.style.opacity = '0'; }
        }
        deltaY = 0;
    });
}

function bindStaticEvents() {
    const on = (id, eventName, handler) => document.getElementById(id)?.addEventListener(eventName, handler);
    on('login-btn', 'click', () => handleAuth('login'));
    on('signup-btn', 'click', () => handleAuth('signup'));
    on('verify-btn', 'click', () => handleAuth('verify'));
    document.querySelectorAll('[data-auth-view]').forEach(el => el.addEventListener('click', () => ui.switchAuth(el.dataset.authView)));
    on('l-email', 'keydown', event => { if (event.key === 'Enter') document.getElementById('l-pwd')?.focus(); });
    on('l-pwd', 'keydown', event => { if (event.key === 'Enter') handleAuth('login'); });
    on('s-name', 'keydown', event => { if (event.key === 'Enter') document.getElementById('s-email')?.focus(); });
    on('s-email', 'keydown', event => { if (event.key === 'Enter') document.getElementById('s-pwd')?.focus(); });
    on('s-pwd', 'keydown', event => { if (event.key === 'Enter') handleAuth('signup'); });
    on('v-otp', 'input', event => { event.currentTarget.value = event.currentTarget.value.replace(/[^0-9]/g, '').slice(0, 6); });
    on('v-otp', 'keydown', event => { if (event.key === 'Enter') handleAuth('verify'); });
    document.querySelectorAll('#new-chat-btn, .new-chat').forEach(el => el.addEventListener('click', startNewChat));
    on('mobile-menu-btn', 'click', ui.toggleSidebar);
    on('sidebar-scrim', 'click', ui.toggleSidebar);
    on('main-logo-img', 'click', mascot.jiggleLogo);
    on('hist-search', 'input', event => ui.filterHist(event.currentTarget.value));
    document.querySelectorAll('#open-settings-btn, .set-btn').forEach(el => el.addEventListener('click', ui.openSettings));
    on('model-toggle', 'click', ui.toggleDropdown);
    document.querySelectorAll('[data-model-id]').forEach(el => el.addEventListener('click', () => ui.selModel(el.dataset.modelId, el.dataset.modelName)));
    on('img-in', 'change', event => {
        const input = event.currentTarget;
        ui.previewImg(input);
        state.pendingImageUploads = api.uploadAttachments(input.files)
            .then(items => { state.currentImages = items; })
            .catch(error => { state.currentImages = []; alert(error.message); })
            .finally(() => { state.pendingImageUploads = null; });
    });
    on('stop-btn', 'click', stopAI);
    on('export-chat-btn', 'click', exportChat);
    on('main-send-btn', 'click', send);
    on('neural-scrim', 'click', ui.closeNeuralContext);
    on('close-neural-btn', 'click', ui.closeNeuralContext);
    on('theme-btn-settings', 'click', event => window.toggleThemeMenu(event, 'theme-menu-settings'));
    document.querySelectorAll('[data-theme-choice]').forEach(el => el.addEventListener('click', () => window.applyThemeChoice(el.dataset.themeChoice)));
    document.querySelectorAll('[data-toggle-setting]').forEach(el => el.addEventListener('click', () => ui.toggleSet(el.id)));
    on('signout-btn', 'click', ui.signOut);
    on('cancel-delete-btn', 'click', ui.closeDeleteConfirm);
    on('confirm-del-btn', 'click', deleteSelectedChat);

    const personaItem = document.querySelector('.persona-switch-item');
    personaItem?.addEventListener('click', event => {
        if (event.target.closest('.switch')) return;
        const toggle = document.getElementById('persona-toggle');
        if (!toggle) return;
        toggle.checked = !toggle.checked;
        toggle.dispatchEvent(new Event('change'));
    });
    const settingsModal = document.getElementById('settings-modal');
    settingsModal?.addEventListener('click', event => { if (event.target === settingsModal) ui.closeSettings(); });
    const palette = document.getElementById('cmd-palette');
    palette?.addEventListener('click', event => { if (event.target === palette) window.closePalette?.(); });
    on('pal-in', 'input', event => window.updPal?.(event.currentTarget.value));
    on('prompt', 'drop', event => {
        const textVal = event.dataTransfer?.getData('text/plain') || '';
        if (!textVal) return;
        event.preventDefault();
        addAttachedContext(textVal);
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Enter' && event.target?.classList?.contains('rename-in')) {
            setTimeout(() => requestChatPersist({ immediate: true }), 0);
        }
    });
    document.addEventListener('focusout', event => {
        if (event.target?.classList?.contains('rename-in')) {
            setTimeout(() => requestChatPersist({ immediate: true }), 0);
        }
    });
}

function bindGlobalDismissals() {
    document.addEventListener('click', event => {
        const sidebar = document.getElementById('sidebar');
        if (window.innerWidth <= 850 && sidebar?.classList.contains('open') && !sidebar.contains(event.target) && !document.getElementById('mobile-menu-btn')?.contains(event.target)) ui.toggleSidebar();
        const themeMenu = document.getElementById('theme-menu');
        if (themeMenu && themeMenu.style.display === 'flex' && !themeMenu.contains(event.target) && !document.getElementById('theme-btn')?.contains(event.target) && !document.getElementById('theme-btn-settings')?.contains(event.target)) themeMenu.style.display = 'none';
        const modelMenu = document.getElementById('model-menu');
        if (modelMenu?.classList.contains('active') && !modelMenu.contains(event.target) && !document.getElementById('model-toggle')?.contains(event.target)) modelMenu.classList.remove('active');
    });
    window.addEventListener('popstate', () => {
        if (document.getElementById('image-modal')?.classList.contains('active')) { ui.closeImageModal(); return; }
        if (document.getElementById('settings-modal')?.style.display === 'flex') { ui.closeSettings(); return; }
        if (document.getElementById('sidebar')?.classList.contains('open')) { ui.toggleSidebar(); return; }
        const confirm = document.getElementById('delete-confirm-modal');
        if (confirm?.style.display === 'flex') confirm.style.display = 'none';
    });
    window.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        if (document.getElementById('image-modal')?.classList.contains('active')) { ui.closeImageModal(); return; }
        if (document.getElementById('settings-modal')?.style.display === 'flex') { ui.closeSettings(); return; }
        if (document.getElementById('delete-confirm-modal')?.style.display === 'flex') { document.getElementById('delete-confirm-modal').style.display = 'none'; return; }
        if (document.getElementById('sidebar')?.classList.contains('open')) ui.toggleSidebar();
    });
}

function initImageModal() {
    const image = document.getElementById('modal-img');
    const container = document.getElementById('image-modal');
    if (!image || !container) return;
    image.onclick = event => { event.stopPropagation(); image.classList.toggle('is-zoomed'); };
    container.onclick = event => {
        if (event.target === container || event.target.classList.contains('lightbox-close')) {
            image.classList.remove('is-zoomed');
            ui.closeImageModal();
        }
    };
}

function installWindowBridge() {
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
    window.requestChatPersist = requestChatPersist;
    window.deleteSelectedChat = deleteSelectedChat;
    syncWindowState();
    window.__helperAppBridgeReady = true;
}

window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', event => {
    if (localStorage.getItem('helper_theme_pref') === 'system') ui.setThemeUI(event.matches ? 'dark' : 'light');
});

installWindowBridge();

document.addEventListener('DOMContentLoaded', () => {
    try {
        console.log('DEBUG: app.js orchestrator initializing...');
        installWindowBridge();
        clearPendingComposerDrafts();
        initTheme();
        bindStaticEvents();
        const activeModel = document.getElementById('active-model-name');
        if (activeModel) activeModel.innerText = 'Gemma 4';

        const savedUser = localStorage.getItem('helper_user_v2');
        if (savedUser) {
            state.set('user', JSON.parse(savedUser));
            const auth = document.getElementById('auth-overlay');
            if (auth) auth.style.display = 'none';
            loadUserChats();
            if (localStorage.getItem('helper_active_modal_v2') === 'settings') ui.openSettings();
            ui.updUI();
            const themeModal = document.getElementById('theme-modal');
            if (!localStorage.getItem('helper_theme_pref') && themeModal) themeModal.style.display = 'flex';
            ui.smartFocus('prompt');
        } else {
            document.getElementById('l-email')?.focus();
            ui.renderHist();
        }

        mascot.bindMouseListeners();
        const prompt = document.getElementById('prompt');
        const sendBtn = document.getElementById('main-send-btn');
        if (prompt) {
            prompt.addEventListener('input', () => { window.autoRes(prompt); sendBtn?.classList.toggle('pulsing', prompt.value.trim().length > 0); });
            prompt.addEventListener('keydown', handleChatKey);
        }
        const personaToggle = document.getElementById('persona-toggle');
        const personaItem = document.querySelector('.persona-switch-item');
        const syncPersona = () => { if (personaToggle && personaItem) personaItem.classList.toggle('persona-active', personaToggle.checked); };
        if (personaToggle) { personaToggle.addEventListener('change', syncPersona); syncPersona(); }

        bindGlobalDismissals();
        initImageModal();
        mascot.initMascotDrop(retrieveContext);
        initNeuralGrab();
        initSidebarSwipe();
        initPullRefresh();
        installWindowBridge();
        console.log('DEBUG: app.js orchestrator ready.');
    } catch (error) {
        console.error('Critical Runtime Error:', error);
    }
});
