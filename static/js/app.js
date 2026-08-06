import { state } from './state.js?v=210';
import { api } from './api.js?v=212';
import { ui } from './ui.js?v=220';
import { mascot } from './mascot.js?v=210';
import { mergeChatsByRecency } from './chat_sync.js?v=203';

const MAX_ATTACHED_CONTEXTS = 6;
const MAX_CONTEXT_CHARS = 6000;
const MAX_TOTAL_CONTEXT_CHARS = 18000;
const CHAT_SYNC_DEBOUNCE_MS = 600;
let syncTimer = null;
let syncWarningShown = false;
let navigationRevision = 0;

function escapeHTML(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function ensureDeletedChatIds() {
    if (!Array.isArray(state.deletedChatIds)) state.deletedChatIds = [];
    return state.deletedChatIds;
}

function syncWindowState() {
    window.chats = state.chats;
    window.activeId = state.activeId;
}

function chooseActiveChatId(chats, preferredId) {
    const preferred = String(preferredId || '').trim();
    if (preferred && chats.some(chat => chat.id === preferred)) return preferred;
    return chats[0]?.id || '';
}

function showWelcomeState() {
    const chatArea = document.getElementById('chat-area');
    const welcome = document.getElementById('welcome');
    if (chatArea) {
        chatArea.innerHTML = '';
        chatArea.style.display = 'none';
    }
    if (welcome) welcome.style.display = 'flex';
    document.body.classList.remove('has-active-chat');
    ui.clearImgPreview();
    const prompt = document.getElementById('prompt');
    if (prompt) {
        prompt.value = '';
        prompt.style.height = 'auto';
        prompt.placeholder = 'Ask me anything...';
        prompt.classList.remove('auth-waiting');
    }
}

function scrollChatToLatest(chatArea) {
    if (!chatArea || !chatArea.querySelector('.msg')) return;
    requestAnimationFrame(() => {
        const lastMessage = chatArea.querySelector('.msg:last-child');
        if (lastMessage?.scrollIntoView) {
            lastMessage.scrollIntoView({ block: 'end', inline: 'nearest' });
        }
        chatArea.scrollTop = chatArea.scrollHeight;
        const root = document.scrollingElement || document.documentElement;
        if (root) root.scrollTop = root.scrollHeight;
    });
}

function persistLocalChatCache() {
    if (!state.user?.email) return;
    try {
        localStorage.setItem('helper_chats_v2_' + state.user.email, JSON.stringify(window.helperSanitizeChatsForPersistence?.(state.chats) || state.chats));
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
    state.addAttachedContext({ kind, text: clipped });
    if (clipped.length < clean.length) console.warn('Context truncated to keep this request within the model limit');
    return true;
}

function serializeAttachedContext(ctx) {
    return String(ctx?.text || '').slice(0, MAX_CONTEXT_CHARS);
}

function contextAttachmentReference(ctx) {
    const ref = ctx?.attachmentRef;
    if (!ref || typeof ref !== 'object' || !/^[a-f0-9]{32}$/i.test(String(ref.id || ''))) return null;
    return {
        id: String(ref.id).toLowerCase(),
        name: String(ref.name || 'attachment').slice(0, 160),
        type: String(ref.type || 'application/octet-stream').slice(0, 100),
        ...(Number.isInteger(Number(ref.size)) && Number(ref.size) >= 0 ? { size: Number(ref.size) } : {}),
    };
}

function mergeAttachmentReferences(uploaded, contexts) {
    const merged = [];
    const seen = new Set();
    for (const item of [...(Array.isArray(uploaded) ? uploaded : []), ...(Array.isArray(contexts) ? contexts : [])]) {
        const ref = contextAttachmentReference({ attachmentRef: item?.attachmentRef || item });
        if (!ref || seen.has(ref.id)) continue;
        seen.add(ref.id);
        merged.push(ref);
    }
    return merged;
}

function clearPendingComposerDrafts() {
    Object.keys(localStorage).forEach(key => {
        if (key.startsWith('helper_pending_prompt_')) localStorage.removeItem(key);
    });
    state.replaceAttachedContexts([]);
    state.replaceCurrentImages([]);
    state.setPendingImageUploads(null);
}

function handleComposerFileDrop(files) {
    const input = document.getElementById('img-in');
    if (!input || !files?.length) return false;
    if (typeof DataTransfer !== 'function') {
        ui.notify('File drop is unavailable in this browser. Use the attachment button.', 'error');
        return false;
    }
    const transfer = new DataTransfer();
    Array.from(files).slice(0, 6).forEach(file => transfer.items.add(file));
    input.files = transfer.files;
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
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
    }
}

function startNewChat() {
    navigationRevision += 1;
    window.__helperActiveEmailDraft = null;
    state.setActiveChat(Date.now().toString());
    const chatArea = document.getElementById('chat-area');
    const welcome = document.getElementById('welcome');
    if (chatArea) { chatArea.innerHTML = ''; chatArea.style.display = 'none'; }
    if (welcome) welcome.style.display = 'flex';
    document.body.classList.remove('has-active-chat');
    ui.clearImgPreview();
    const prompt = document.getElementById('prompt');
    if (prompt) { prompt.value = ''; prompt.style.height = 'auto'; }
    ui.renderHist();
    const sidebar = document.getElementById('sidebar');
    if (window.innerWidth <= 850 && sidebar?.classList.contains('open')) ui.toggleSidebar();
    ui.smartFocus('prompt');
    syncWindowState();
}

function loadChat(id, options = {}) {
    const {
        setActiveId = true,
        persistActiveId = true,
        renderHistory = true,
        focusPrompt = true,
        trackNavigation = true,
    } = options;
    const chat = state.chats.find(c => c.id === id);
    if (!chat) return;
    window.__helperActiveEmailDraft = null;
    if (trackNavigation) navigationRevision += 1;
    if (setActiveId) state.setActiveChat(id);
    if (persistActiveId) localStorage.setItem('helper_active_chat_v2', id);
    const chatArea = document.getElementById('chat-area');
    const welcome = document.getElementById('welcome');
    if (chatArea) { chatArea.innerHTML = ''; chatArea.style.display = 'block'; }
    if (welcome) welcome.style.display = 'none';
    document.body.classList.add('has-active-chat');
    ui.clearImgPreview();
    chat.ms.forEach((message, idx) => ui.addMsg(message.r, message.c, message.i, idx, message.m || 'AI Assistant', message.masked, message.attachments || []));
    window.initUpscaleImagePolling?.(chatArea);
    if (renderHistory) ui.renderHist();
    const sidebar = document.getElementById('sidebar');
    if (window.innerWidth <= 850 && sidebar?.classList.contains('open')) ui.toggleSidebar();
    if (focusPrompt) ui.smartFocus('prompt');
    ui.checkAuthMode();
    scrollChatToLatest(chatArea);
    syncWindowState();
}

async function loadUserChats() {
    if (!state.user?.email) return;
    const restoreRevision = navigationRevision;
    const restoreAllowed = restoreRevision === 0;
    const key = 'helper_chats_v2_' + state.user.email;
    let localChats = [];
    let localStr = localStorage.getItem(key);
    if (!localStr && localStorage.getItem('helper_chats_v2')) {
        localStr = localStorage.getItem('helper_chats_v2');
        localStorage.setItem(key, localStr);
        localStorage.removeItem('helper_chats_v2');
    }
    if (localStr) {
        try {
            const parsed = JSON.parse(localStr);
            localChats = Array.isArray(parsed) ? parsed : [];
        } catch (error) {
            console.warn('Local chat cache could not be parsed:', error);
        }
    }
    let remoteChats = [];
    try {
        const data = await api.fetchChats();
        if (data?.success && Array.isArray(data.chats)) {
            remoteChats = data.chats;
        }
    } catch (error) {
        console.error('Cloud fetch failed:', error);
    }
    const mergedChats = mergeChatsByRecency(localChats, remoteChats);
    state.replaceChats(mergedChats);
    syncWindowState();
    if (!restoreAllowed || navigationRevision !== restoreRevision) {
        ui.renderHist();
        return;
    }

    const savedActiveChatId = localStorage.getItem('helper_active_chat_v2');
    const activeChatId = chooseActiveChatId(mergedChats, savedActiveChatId);
    if (!activeChatId) {
        state.setActiveChat(null);
        localStorage.removeItem('helper_active_chat_v2');
        persistLocalChatCache();
        ui.renderHist();
        showWelcomeState();
        syncWindowState();
        return;
    }

    state.setActiveChat(activeChatId);
    persistLocalChatCache();
    ui.renderHist();
    loadChat(activeChatId, {
        setActiveId: false,
        persistActiveId: false,
        renderHistory: false,
        focusPrompt: false,
        trackNavigation: false,
    });
    syncWindowState();
}

async function saveUserChats() {
    if (!state.user?.email) return;
    const deletedIds = ensureDeletedChatIds();
    const payload = { chats: window.helperSanitizeChatsForPersistence?.(state.chats) || state.chats, deleted_chat_ids: deletedIds.slice() };
    persistLocalChatCache();
    const result = await api.syncChats(payload);
    const syncFailed = !result || result.success === false;
    if (syncFailed) {
        if (!syncWarningShown) {
            ui.notify('Conversation sync is temporarily unavailable. Your changes are saved locally.', 'warning', 5200);
            syncWarningShown = true;
        }
    } else {
        deletedIds.length = 0;
        syncWarningShown = false;
    }
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

function validateAuthInput(type) {
    const fields = {
        login: [
            ['l-email', 'Enter your email address.'],
            ['l-pwd', 'Enter your password.']
        ],
        signup: [
            ['s-name', 'Enter your name.'],
            ['s-email', 'Enter your email address.'],
            ['s-pwd', 'Create a password.']
        ],
        verify: [
            ['v-otp', 'Enter the six digit verification code.']
        ]
    };
    for (const [id, message] of fields[type] || []) {
        const input = document.getElementById(id);
        if (!input?.value.trim()) {
            input?.focus();
            ui.notify(message, 'error');
            return false;
        }
        if (input.type === 'email' && !input.checkValidity()) {
            input.focus();
            ui.notify('Enter a valid email address.', 'error');
            return false;
        }
    }
    const password = document.getElementById('s-pwd');
    if (type === 'signup' && password.value.length < 8) {
        password.focus();
        ui.notify('Password must be at least 8 characters.', 'error');
        return false;
    }
    const otp = document.getElementById('v-otp');
    if (type === 'verify' && !/^[0-9]{6}$/.test(otp.value)) {
        otp.focus();
        ui.notify('Enter the complete six digit verification code.', 'error');
        return false;
    }
    return true;
}

async function handleAuth(type) {
    if (!validateAuthInput(type)) return;
    const btn = document.getElementById(type + '-btn');
    const original = btn?.innerHTML;
    if (btn) {
        btn.disabled = true;
        btn.setAttribute('aria-busy', 'true');
        btn.innerHTML = '<div class="spinner" aria-hidden="true"></div><span class="sr-only">Working</span>';
    }
    try {
        const data = await api.handleAuth(type);
        if (data.success) {
            if (type === 'signup' || (type === 'login' && data.unverified)) {
                ui.switchAuth('otp');
                ui.notify('Verification code sent. Check your inbox.', 'success');
            } else {
                state.set('user', data.user);
                localStorage.setItem('helper_user_v2', JSON.stringify(data.user));
                if (data.token) localStorage.setItem('helper_token_v2', data.token);
                document.documentElement.classList.add('is-authenticated');
                const auth = document.getElementById('auth-overlay');
                if (auth) auth.style.display = 'none';
                window.HelperDialogs?.sync();
                await loadUserChats();
                ui.updUI();
                const themeModal = document.getElementById('theme-modal');
                if (!localStorage.getItem('helper_theme_pref') && themeModal) themeModal.style.display = 'flex';
                window.HelperDialogs?.sync();
                ui.smartFocus('prompt');
            }
        } else {
            ui.notify(data.error || 'Check your credentials and try again.', 'error');
        }
    } catch (error) {
        ui.notify(error.message || 'The helper service is unreachable. Try again.', 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.removeAttribute('aria-busy');
            btn.innerHTML = original;
        }
    }
}

async function submitEdit(idx, container) {
    const textarea = container.querySelector('textarea');
    const newText = textarea?.value.trim();
    if (!newText) return;
    const chat = state.chats.find(c => c.id === state.activeId);
    if (!chat) return;
    state.truncateMessages(chat.id, idx);
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
    const contextAttachmentRefs = state.attachedContexts.map(contextAttachmentReference).filter(Boolean);
    const currentAttachments = mergeAttachmentReferences(state.currentImages, contextAttachmentRefs);
    const attachedContextText = state.attachedContexts.map(serializeAttachedContext).filter(Boolean)
        .map((text, index) => `[Attached Context ${index + 1}]\n"""\n${text}\n"""`).join('\n\n');
    const activeDraftContext = attachedContextText.includes('EMAIL_DRAFT_CONTEXT:')
        ? ''
        : (window.getActiveEmailDraftPromptContext?.(userText) || '');
    const contextText = [attachedContextText, activeDraftContext].filter(Boolean).join(String.fromCharCode(10, 10));
    const apiPrompt = [contextText, userText].filter(Boolean).join(String.fromCharCode(10, 10));
    if (!apiPrompt && !currentAttachments.length) return;
    navigationRevision += 1;
    if (!state.activeId) state.setActiveChat(Date.now().toString());

    let chat = state.chats.find(c => c.id === state.activeId);
    if (!chat) {
        chat = { id: state.activeId, title: userText.substring(0, 35) || 'New Chat', ms: [], updated_at: Date.now() };
        state.appendChat(chat);
    }
    syncWindowState();

    const welcome = document.getElementById('welcome');
    const chatArea = document.getElementById('chat-area');
    if (welcome) welcome.style.display = 'none';
    if (chatArea) chatArea.style.display = 'block';
    document.body.classList.add('has-active-chat');

    let isMasked = false;
    if (promptEl.classList.contains('auth-waiting')) isMasked = true;
    else if (chat.ms.length > 0) {
        const last = String(chat.ms[chat.ms.length - 1].c || '').toLowerCase();
        const authKeywords = ['please provide your admin key', 'enter your admin_key', 'provide the password', 'authorize with your key', 'auth_required', 'admin key'];
        isMasked = authKeywords.some(keyword => last.includes(keyword));
    }

    const storedUserText = isMasked ? '[MASKED_SECRET]' : userText;
    ui.addMsg('u', storedUserText, state.currentImg, chat.ms.length, null, isMasked, currentAttachments);
    state.appendMessage(chat.id, {
        r: 'u',
        c: storedUserText,
        attachments: currentAttachments,
        apiPrompt: isMasked ? undefined : apiPrompt,
        masked: isMasked,
    });
    state.markChatUpdated(chat.id);
    requestChatPersist();
    mascot.triggerBotReaction(userText);
    ui.clearImgPreview();
    promptEl.value = '';
    promptEl.style.height = 'auto';
    promptEl.placeholder = 'Ask me anything...';
    promptEl.classList.remove('auth-waiting');
    const stopButton = document.getElementById('stop-btn');
    const sendButton = document.getElementById('main-send-btn');
    if (stopButton) {
        stopButton.style.display = 'flex';
        stopButton.setAttribute('aria-hidden', 'false');
    }
    if (sendButton) {
        sendButton.style.display = 'none';
        sendButton.setAttribute('aria-hidden', 'true');
    }
    chatArea?.setAttribute('aria-busy', 'true');

    const initContent = 'Planning the best route...';
    const modelName = document.getElementById('active-model-name')?.innerText || 'AI Assistant';
    const botText = ui.addMsg('b', initContent, null, chat.ms.length, modelName);
    const botMsg = botText.closest('.msg');
    if (botMsg) botMsg.classList.add('thinking-state');
    botText.innerHTML = `<div class="status-msg"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><circle cx="12" cy="12" r="10"></circle><path d="M12 6v6l4 2"></path></svg> <span id="status-text">${escapeHTML(initContent)}</span></div><div class="typing-indicator"><span></span><span></span><span></span></div>`;
    mascot.updateBotVisuals();
    const mascotEl = document.getElementById('mascot-container');
    if (mascotEl) mascotEl.classList.add('thinking');
    state.set('abortController', new AbortController());

    try {
        const historyForApi = chat.ms.slice(0, -1).map(message => ({
            role: message.r === 'u' ? 'user' : 'assistant',
            content: message.apiPrompt || message.c,
            attachments: message.attachments || [],
            masked: Boolean(message.masked)
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
                pers: Boolean(document.getElementById('t-pers')?.classList.contains('on')),
                response_style: state.responseStyle || 'adaptive'
            }
        }, state.abortController.signal);

        if (response.status === 401) { if (!window.handleHelperUnauthorized?.(response)) ui.signOut(); return; }
        if (!response.ok) {
            const errorText = `I could not reach the selected helper route (status ${response.status}). Please retry or choose another route.`;
            botText.innerText = errorText;
            state.appendMessage(chat.id, { r: 'b', c: errorText, m: modelName });
            state.markChatUpdated(chat.id);
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
            } catch (_) { }
        }
        if (!fullText.trim()) {
            fullText = 'I could not complete that response. Please retry, or switch the route from the composer.';
        }
        if (chat.title && chat.title.trim().length <= 5 && fullText.trim().length > 10) {
            const firstLine = fullText.split('\n')[0];
            state.updateChat(chat.id, { title: firstLine.substring(0, 35).trim() + (firstLine.length > 35 ? '...' : '') });
        }
        state.appendMessage(chat.id, { r: 'b', c: fullText, m: modelName });
        state.markChatUpdated(chat.id);
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
        const stopped = error.name === 'AbortError';
        const failureMessage = stopped
            ? 'Generation stopped.'
            : 'I could not complete that response. Please retry or choose another route.';
        botText.textContent = failureMessage;
        state.appendMessage(chat.id, { r: 'b', c: failureMessage, m: modelName });
        state.markChatUpdated(chat.id);
        await requestChatPersist({ immediate: true });
        ui.notify(failureMessage, stopped ? 'info' : 'error');
    } finally {
        const stopBtn = document.getElementById('stop-btn');
        const sendBtn = document.getElementById('main-send-btn');
        if (stopBtn) {
            stopBtn.style.display = 'none';
            stopBtn.setAttribute('aria-hidden', 'true');
        }
        if (sendBtn) {
            sendBtn.style.display = 'flex';
            sendBtn.setAttribute('aria-hidden', 'false');
        }
        chatArea?.setAttribute('aria-busy', 'false');
        ui.checkAuthMode();
        if (mascotEl) mascotEl.classList.remove('thinking');
        document.querySelectorAll('.thinking-state').forEach(el => el.classList.remove('thinking-state'));
        document.querySelectorAll('.typing-indicator').forEach(el => el.remove());
        state.abortController = null;
        state.currentImg = null;
        state.replaceAttachedContexts([]);
        state.replaceCurrentImages([]);
        state.activeJobId = null;
        syncWindowState();
        if (state.chats.find(c => c.id === state.activeId)?.ms.length <= 2) ui.renderHist();
        ui.checkAuthMode();
    }
}

function stopAI() {
    if (state.activeJobId) api.cancelInferenceJob(state.activeJobId).catch(() => { });
    if (state.abortController) state.abortController.abort();
}

async function deleteSelectedChat() {
    if (!state.chatToDelete) return;
    const deletedId = state.chatToDelete;
    const deletedIds = ensureDeletedChatIds();
    if (!deletedIds.includes(deletedId)) deletedIds.push(deletedId);
    state.deleteChat(deletedId);
    if (state.activeId === deletedId) startNewChat();
    ui.closeDeleteConfirm();
    ui.renderHist();
    syncWindowState();
    await requestChatPersist({ immediate: true });
}

function togglePin(id) {
    const chat = state.chats.find(c => c.id === id);
    if (!chat) return;
    state.updateChat(id, { pinned: !chat.pinned, updated_at: Date.now() });
    ui.renderHist();
    requestChatPersist();
}

function exportChat() {
    const chat = state.chats.find(c => c.id === state.activeId);
    if (!chat || !chat.ms.length) { ui.notify('Start a conversation before exporting.', 'error'); return; }
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
        if (!data.success) throw new Error(data.error || 'Related context could not be retrieved.');
        ui.showNeuralContext(data.results, data.explanation);
    } catch (error) {
        ui.notify(error.message || 'Related context could not be retrieved.', 'error');
    } finally {
        if (mascotEl) mascotEl.classList.remove('thinking');
    }
}

window.applyThemeChoice = function applyThemeChoice(choice) {
    localStorage.setItem('helper_theme_pref', choice);
    const labels = { light: 'Light', dark: 'Dark', system: 'System' };
    const headerIcon = document.getElementById('current-theme-icon');
    const settingsIcon = document.getElementById('current-theme-icon-settings');
    if (headerIcon) headerIcon.innerText = labels[choice] || 'System';
    if (settingsIcon) settingsIcon.innerText = labels[choice] || 'System';
    const themeMenu = document.getElementById('theme-menu-settings');
    const restoreThemeControl = themeMenu?.contains(document.activeElement);
    document.querySelectorAll('[data-theme-choice]').forEach(option => {
        const selected = option.dataset.themeChoice === choice;
        option.classList.toggle('active', selected);
        if (option.getAttribute('role') === 'menuitemradio') option.setAttribute('aria-checked', String(selected));
        if (option.classList.contains('theme-opt')) option.setAttribute('aria-pressed', String(selected));
    });
    const resolvedTheme = choice === 'system'
        ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
        : choice;
    ui.setThemeUI(resolvedTheme);
    document.querySelectorAll('.dropdown-menu').forEach(menu => { menu.style.display = 'none'; });
    document.getElementById('theme-btn-settings')?.setAttribute('aria-expanded', 'false');
    if (restoreThemeControl) document.getElementById('theme-btn-settings')?.focus();
    document.querySelectorAll('.set-row').forEach(row => row.classList.remove('row-elevated'));
    const themeModal = document.getElementById('theme-modal');
    if (themeModal?.style.display === 'flex') {
        setTimeout(() => {
            themeModal.style.display = 'none';
            window.HelperDialogs?.sync();
        }, 400);
    }
};

window.toggleThemeMenu = function toggleThemeMenu(event, menuId) {
    event?.stopPropagation();
    const target = menuId || 'theme-menu';
    const menu = document.getElementById(target);
    if (!menu) return;
    const control = event?.currentTarget || document.getElementById('theme-btn-settings');
    const visible = menu.style.display === 'flex';
    document.querySelectorAll('.dropdown-menu').forEach(item => { item.style.display = 'none'; });
    document.getElementById('theme-btn-settings')?.setAttribute('aria-expanded', 'false');
    document.querySelectorAll('.set-row').forEach(row => row.classList.remove('row-elevated'));
    if (!visible) {
        menu.style.display = 'flex';
        control?.setAttribute('aria-expanded', 'true');
        menu.closest('.set-row')?.classList.add('row-elevated');
        requestAnimationFrame(() => menu.querySelector('[aria-checked="true"], [data-theme-choice]')?.focus());
    }
};

window.autoRes = function autoRes(el) {
    if (!el) return;
    const maxHeight = 200;
    el.style.height = '0px';
    const contentHeight = Math.max(el.scrollHeight, 36);
    const nextHeight = Math.min(contentHeight, maxHeight);
    el.style.height = nextHeight + 'px';
    el.style.overflowY = contentHeight > maxHeight ? 'auto' : 'hidden';
};

function initTheme() {
    window.applyThemeChoice(localStorage.getItem('helper_theme_pref') || 'system');
}

function initSidebarSwipe() {
    const sidebar = document.getElementById('sidebar');
    const scrim = document.getElementById('sidebar-scrim');
    if (!sidebar || !scrim) return;
    let startX = 0;
    let startY = 0;
    let currentX = 300;
    let dragging = false;
    let horizontal = false;
    sidebar.addEventListener('touchstart', event => {
        if (!sidebar.classList.contains('open') || window.innerWidth > 992) return;
        startX = event.touches[0].clientX;
        startY = event.touches[0].clientY;
        currentX = 300;
        dragging = true;
        horizontal = false;
        sidebar.style.transition = 'none';
        scrim.style.transition = 'none';
    }, { passive: true });
    sidebar.addEventListener('touchmove', event => {
        if (!dragging) return;
        const deltaX = event.touches[0].clientX - startX;
        const deltaY = event.touches[0].clientY - startY;
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
        window.syncComposerDragSources?.();
    }
    document.addEventListener('keydown', event => {
        if (event.target.closest?.('input, textarea, [contenteditable="true"]')) return;
        if (event.ctrlKey || event.metaKey || event.altKey || event.repeat) return;
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
    on('sidebar-scrim', 'click', () => {
        if (window.helperOutsideClickDismissEnabled?.() !== false) ui.toggleSidebar();
    });
    on('main-logo-img', 'click', mascot.jiggleLogo);
    on('hist-search', 'input', event => ui.filterHist(event.currentTarget.value));
    on('open-settings-btn', 'click', ui.openSettings);
    on('model-toggle', 'click', ui.toggleDropdown);
    on('model-toggle', 'keydown', event => {
        if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
        event.preventDefault();
        const menu = document.getElementById('model-menu');
        const options = Array.from(menu?.querySelectorAll('[data-model-id]') || []);
        if (!menu || options.length === 0) return;
        menu.classList.add('active');
        event.currentTarget.setAttribute('aria-expanded', 'true');
        const selected = menu.querySelector('[aria-selected="true"]');
        requestAnimationFrame(() => (event.key === 'ArrowUp' ? options.at(-1) : selected || options[0])?.focus());
    });
    on('model-menu', 'keydown', event => {
        const options = Array.from(event.currentTarget.querySelectorAll('[data-model-id]'));
        if (event.key === 'Escape') {
            event.preventDefault();
            event.currentTarget.classList.remove('active');
            document.getElementById('model-toggle')?.setAttribute('aria-expanded', 'false');
            document.getElementById('model-toggle')?.focus();
            return;
        }
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) || options.length === 0) return;
        event.preventDefault();
        const current = Math.max(0, options.indexOf(document.activeElement));
        const next = event.key === 'Home' ? 0
            : event.key === 'End' ? options.length - 1
                : event.key === 'ArrowDown' ? (current + 1) % options.length
                    : (current - 1 + options.length) % options.length;
        options[next].focus();
    });
    document.querySelectorAll('[data-model-id]').forEach(el => el.addEventListener('click', () => ui.selModel(el.dataset.modelId, el.dataset.modelName)));
    on('attach-files-btn', 'click', () => document.getElementById('img-in')?.click());
    on('img-in', 'change', event => {
        const input = event.currentTarget;
        ui.previewImg(input);
        state.setPendingImageUploads(api.uploadAttachments(input.files)
            .then(items => { state.replaceCurrentImages(items); })
            .catch(error => { state.replaceCurrentImages([]); ui.notify(error.message || 'Attachment upload failed.', 'error'); })
            .finally(() => { state.setPendingImageUploads(null); }));
    });
    on('stop-btn', 'click', stopAI);
    on('export-chat-btn', 'click', exportChat);
    on('main-send-btn', 'click', send);
    on('neural-scrim', 'click', () => {
        if (window.helperOutsideClickDismissEnabled?.() !== false) ui.closeNeuralContext();
    });
    on('close-neural-btn', 'click', ui.closeNeuralContext);
    on('theme-btn-settings', 'click', event => window.toggleThemeMenu(event, 'theme-menu-settings'));
    on('theme-menu-settings', 'keydown', event => {
        const options = Array.from(event.currentTarget.querySelectorAll('[data-theme-choice]'));
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            event.currentTarget.style.display = 'none';
            event.currentTarget.closest('.set-row')?.classList.remove('row-elevated');
            const trigger = document.getElementById('theme-btn-settings');
            trigger?.setAttribute('aria-expanded', 'false');
            trigger?.focus();
            return;
        }
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) || options.length === 0) return;
        event.preventDefault();
        const current = Math.max(0, options.indexOf(document.activeElement));
        const next = event.key === 'Home' ? 0
            : event.key === 'End' ? options.length - 1
                : event.key === 'ArrowDown' ? (current + 1) % options.length
                    : (current - 1 + options.length) % options.length;
        options[next].focus();
    });
    on('close-settings-btn', 'click', ui.closeSettings);
    on('response-style-setting', 'change', event => ui.setResponseStyle(event.currentTarget.value));
    document.querySelectorAll('[data-theme-choice]').forEach(el => el.addEventListener('click', () => window.applyThemeChoice(el.dataset.themeChoice)));
    document.querySelectorAll('[data-toggle-setting]').forEach(el => el.addEventListener('click', () => ui.toggleSet(el.id)));
    document.querySelectorAll('.set-row').forEach(row => {
        const toggle = row.querySelector('[data-toggle-setting]');
        if (!toggle) return;
        row.addEventListener('click', event => {
            if (!event.target.closest('[data-toggle-setting]')) toggle.click();
        });
    });
    on('signout-btn', 'click', ui.signOut);
    on('cancel-delete-btn', 'click', ui.closeDeleteConfirm);
    on('confirm-del-btn', 'click', deleteSelectedChat);

    const settingsModal = document.getElementById('settings-modal');
    settingsModal?.addEventListener('click', event => {
        if (window.helperOutsideClickDismissEnabled?.() !== false && event.target === settingsModal) ui.closeSettings();
    });
    const palette = document.getElementById('cmd-palette');
    palette?.addEventListener('click', event => {
        if (window.helperOutsideClickDismissEnabled?.() !== false && event.target === palette) window.closePalette?.();
    });
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
        if (window.helperOutsideClickDismissEnabled?.() === false) return;
        const sidebar = document.getElementById('sidebar');
        if (window.innerWidth <= 850 && sidebar?.classList.contains('open') && !sidebar.contains(event.target) && !document.getElementById('mobile-menu-btn')?.contains(event.target)) {
            ui.toggleSidebar();
        }

        const themeMenu = document.getElementById('theme-menu-settings');
        const themeControl = document.getElementById('theme-btn-settings');
        if (themeMenu?.style.display === 'flex' && !themeMenu.contains(event.target) && !themeControl?.contains(event.target)) {
            themeMenu.style.display = 'none';
            themeControl?.setAttribute('aria-expanded', 'false');
            themeMenu.closest('.set-row')?.classList.remove('row-elevated');
        }

        const modelMenu = document.getElementById('model-menu');
        const modelControl = document.getElementById('model-toggle');
        if (modelMenu?.classList.contains('active') && !modelMenu.contains(event.target) && !modelControl?.contains(event.target)) {
            modelMenu.classList.remove('active');
            modelControl?.setAttribute('aria-expanded', 'false');
        }
    });

    window.addEventListener('popstate', () => {
        if (document.getElementById('image-modal')?.classList.contains('active')) { ui.closeImageModal(); return; }
        if (document.getElementById('neural-context-card')?.classList.contains('active')) { ui.closeNeuralContext(); return; }
        if (document.getElementById('settings-modal')?.style.display === 'flex') { ui.closeSettings(); return; }
        if (document.getElementById('delete-confirm-modal')?.style.display === 'flex') { ui.closeDeleteConfirm(); return; }
        if (document.getElementById('sidebar')?.classList.contains('open')) ui.toggleSidebar();
    });

    window.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        const canClose = id => !window.HelperDialogs || window.HelperDialogs.isTop(id);
        if (canClose('image-modal') && document.getElementById('image-modal')?.classList.contains('active')) { ui.closeImageModal(); return; }
        if (canClose('neural-context-card') && document.getElementById('neural-context-card')?.classList.contains('active')) { ui.closeNeuralContext(); return; }
        if (canClose('theme-modal') && document.getElementById('theme-modal')?.style.display === 'flex') {
            window.applyThemeChoice(localStorage.getItem('helper_theme_pref') || 'system');
            return;
        }
        if (canClose('settings-modal') && document.getElementById('settings-modal')?.style.display === 'flex') { ui.closeSettings(); return; }
        if (canClose('delete-confirm-modal') && document.getElementById('delete-confirm-modal')?.style.display === 'flex') { ui.closeDeleteConfirm(); return; }
        if (!window.HelperDialogs?.getActive() && document.getElementById('sidebar')?.classList.contains('open')) ui.toggleSidebar();
    });
}

function initImageModal() {
    const image = document.getElementById('modal-img');
    const container = document.getElementById('image-modal');
    if (!image || !container) return;
    image.onclick = event => { event.stopPropagation(); image.classList.toggle('is-zoomed'); };
    document.getElementById('image-modal-close')?.addEventListener('click', () => ui.closeImageModal());
    document.getElementById('image-modal-download')?.addEventListener('click', () => {
        const meta = container.__imageMeta || {};
        const candidateUrl = meta.downloadUrl || meta.sourceUrl || meta.src;
        const url = window.__helperSafeImageUrl?.(candidateUrl) || (meta.downloadable && /^blob:/i.test(String(candidateUrl || '')) ? String(candidateUrl) : '');
        if (!url) return ui.notify('Download is unavailable for this image.', 'error');
        const link = document.createElement('a');
        link.href = url;
        link.download = String(meta.filename || 'helper-image');
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.click();
    });
    document.getElementById('image-modal-copy')?.addEventListener('click', async () => {
        const meta = container.__imageMeta || {};
        const candidateUrl = meta.downloadUrl || meta.sourceUrl || meta.src;
        const url = window.__helperSafeImageUrl?.(candidateUrl) || (meta.downloadable && /^blob:/i.test(String(candidateUrl || '')) ? String(candidateUrl) : '');
        if (!url || !navigator.clipboard?.writeText) return ui.notify('Copy link is unavailable for this image.', 'error');
        try {
            await navigator.clipboard.writeText(url);
            ui.notify('Image link copied.', 'success', 1600);
        } catch (_) {
            ui.notify('Copy link is unavailable in this browser.', 'error');
        }
    });
    document.getElementById('image-modal-use')?.addEventListener('click', event => {
        const context = container.__imageMeta?.context;
        if (!context || typeof window.addComposerContext !== 'function') return;
        if (window.addComposerContext(context)) ui.notify('Image added to the next prompt.', 'success', 1600);
        event.currentTarget.blur();
    });
    container.onclick = event => {
        const backdropClick = event.target === container && window.helperOutsideClickDismissEnabled?.() !== false;
        if (backdropClick) {
            image.classList.remove('is-zoomed');
            ui.closeImageModal();
        }
    };
}
function installWindowBridge() {
    window.handleComposerFileDrop = handleComposerFileDrop;
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
        ui.loadPreferences();
        bindStaticEvents();
        const savedModel = localStorage.getItem('helper_model_v3') || 'helper-auto';
        const savedModelOption = document.querySelector(`[data-model-id="${CSS.escape(savedModel)}"]`);
        const activeModel = savedModelOption ? savedModel : 'helper-auto';
        const activeOption = savedModelOption || document.querySelector('[data-model-id="helper-auto"]');
        ui.selModel(activeModel, activeOption?.dataset.modelName || 'Helper Auto');

        const savedUser = localStorage.getItem('helper_user_v2');
        if (savedUser) {
            state.set('user', JSON.parse(savedUser));
            const auth = document.getElementById('auth-overlay');
            if (auth) auth.style.display = 'none';
            window.HelperDialogs?.sync();
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
            prompt.addEventListener('input', () => {
                window.autoRes(prompt);
                sendBtn?.classList.toggle('pulsing', prompt.value.trim().length > 0);
            });
            prompt.addEventListener('keydown', handleChatKey);
            window.autoRes(prompt);
        }
        const personaToggle = document.getElementById('persona-toggle');
        const personaItem = document.querySelector('.persona-switch-item');
        const syncPersona = () => {
            if (!personaToggle) return;
            personaItem?.classList.toggle('persona-active', personaToggle.checked);
        };
        if (personaToggle) {
            personaToggle.addEventListener('change', () => {
                syncPersona();
                ui.setPersonaEnabled(personaToggle.checked);
            });
            syncPersona();
        }

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
