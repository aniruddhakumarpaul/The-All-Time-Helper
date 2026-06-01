/**
 * app.js — ES6 Module Entry Point & Orchestrator
 * Imports modular components. Contains send/load/save orchestration from main_v3.js.
 */
import { state } from './state.js';
import { api } from './api.js';
import { ui } from './ui.js';
import { mascot } from './mascot.js';

const CHAT_SYNC_DEBOUNCE_MS = 750;
const MAX_ATTACHED_CONTEXTS = 6;
const MAX_CONTEXT_CHARS = 6000;
const MAX_TOTAL_CONTEXT_CHARS = 18000;
let persistTimer = null;
let cloudSyncChain = Promise.resolve();

function finishPageLoader(message = 'Ready') {
    if (typeof window.finishPageLoader === 'function') {
        window.finishPageLoader(message);
        return;
    }

    const loader = document.getElementById('page-loader-overlay');
    if (!loader) return;
    loader.classList.add('fade-out');
    setTimeout(() => loader.remove(), 800);
}

// --- Upscale Poller ---
function startUpscalePoller(jobId, container) {
    if (window.initUpscaleImagePolling && container) {
        window.initUpscaleImagePolling(container);
    }
}

function chatStorageKey() {
    return state.user && state.user.email ? 'helper_chats_v2_' + state.user.email : '';
}

function deletedChatsStorageKey() {
    return state.user && state.user.email ? 'helper_deleted_chats_v2_' + state.user.email : '';
}

function readDeletedChatIds() {
    const key = deletedChatsStorageKey();
    if (!key) return [];
    try {
        const raw = localStorage.getItem(key);
        const ids = raw ? JSON.parse(raw) : [];
        return Array.isArray(ids) ? ids.filter(id => typeof id === 'string') : [];
    } catch (err) {
        return [];
    }
}

function writeDeletedChatIds(ids) {
    const key = deletedChatsStorageKey();
    if (!key) return;
    const unique = Array.from(new Set((ids || []).filter(id => typeof id === 'string')));
    if (unique.length) localStorage.setItem(key, JSON.stringify(unique));
    else localStorage.removeItem(key);
}

function clearPendingComposerDrafts() {
    Object.keys(localStorage)
        .filter(key => key.startsWith('helper_pending_prompt_'))
        .forEach(key => localStorage.removeItem(key));
    state.attachedContexts = [];
    state.currentImages = [];
}

function normalizeAttachedContextText(text) {
    return String(text || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim();
}

function attachedContextTotalChars() {
    return (state.attachedContexts || []).reduce((total, ctx) => total + String(ctx.text || '').length, 0);
}

function addAttachedContext(text, { render = true } = {}) {
    const normalized = normalizeAttachedContextText(text);
    if (!normalized) return false;

    state.attachedContexts = state.attachedContexts || [];
    if (state.attachedContexts.some(ctx => normalizeAttachedContextText(ctx.text) === normalized)) {
        if (render) renderAttachmentsPreview();
        return true;
    }

    if (state.attachedContexts.length >= MAX_ATTACHED_CONTEXTS) {
        alert(`You can attach up to ${MAX_ATTACHED_CONTEXTS} context items at a time.`);
        return false;
    }

    const currentTotal = attachedContextTotalChars();
    const remainingTotal = Math.max(0, MAX_TOTAL_CONTEXT_CHARS - currentTotal);
    if (remainingTotal <= 0) {
        alert('Attached context is already at the safe limit for one model request.');
        return false;
    }

    const limit = Math.min(MAX_CONTEXT_CHARS, remainingTotal);
    const clipped = normalized.length > limit
        ? `${normalized.slice(0, limit).trimEnd()}\n\n[Context truncated to keep this request within the model limit.]`
        : normalized;

    const id = `text-${Date.now()}-${Math.random()}`;
    state.attachedContexts.push({ id, text: clipped });
    if (render) renderAttachmentsPreview();
    return true;
}

function normalizeMessageContent(content) {
    if (typeof content !== 'string') return content;
    let normalized = content.trim();
    let previous = '';
    const duplicateImagePattern = /(!\[[^\]]*\]\([^)]+\))\s*\1/g;
    while (normalized !== previous) {
        previous = normalized;
        normalized = normalized.replace(duplicateImagePattern, '$1');
    }
    if (isCompleteToolResult(normalized)) {
        normalized = normalized.replace(/\s*\[Stopped\]\s*$/i, '').trim();
    }
    return normalized;
}

function extractEmailDraftJson(content) {
    if (typeof content !== 'string' || !content.includes('EMAIL_DRAFT_PAYLOAD:')) return null;
    const payloadPart = content.split('EMAIL_DRAFT_PAYLOAD:', 2)[1] || '';
    const startIdx = payloadPart.indexOf('{');
    if (startIdx === -1) return null;

    let depth = 0;
    let inString = false;
    let escaped = false;
    for (let i = startIdx; i < payloadPart.length; i++) {
        const ch = payloadPart[i];
        if (escaped) {
            escaped = false;
            continue;
        }
        if (ch === '\\') {
            escaped = true;
            continue;
        }
        if (ch === '"') {
            inString = !inString;
            continue;
        }
        if (inString) continue;
        if (ch === '{') depth += 1;
        if (ch === '}') {
            depth -= 1;
            if (depth === 0) return payloadPart.slice(startIdx, i + 1);
        }
    }
    return null;
}

function isCompleteToolResult(content) {
    if (typeof content !== 'string') return false;
    if (/!\[[^\]]*\]\([^)]+\)/.test(content)) return true;
    if (/\b(SIMULATE SUCCESS|LIVE SUCCESS|ALREADY SENT):/i.test(content)) return true;
    const emailJson = extractEmailDraftJson(content);
    if (!emailJson) return false;
    try {
        JSON.parse(emailJson);
        return true;
    } catch (err) {
        return false;
    }
}

function formatMessageForExport(message) {
    const content = normalizeMessageContent(message?.c || '');
    if (!content.includes('EMAIL_DRAFT_PAYLOAD:')) return content;

    const emailJson = extractEmailDraftJson(content);
    if (!emailJson) return content;
    try {
        const draft = JSON.parse(emailJson);
        const prefix = content.split('EMAIL_DRAFT_PAYLOAD:', 1)[0].trim();
        const attachmentList = Array.isArray(draft.attachments) && draft.attachments.length
            ? draft.attachments.map((attachment, index) => ({
                content: attachment?.content || attachment?.attachment_content || '',
                filename: attachment?.filename || attachment?.attachment_filename || `attachment_${index + 1}`
            })).filter(attachment => String(attachment.content || '').trim())
            : (draft.attachment_content ? [{
                content: draft.attachment_content,
                filename: draft.attachment_filename || 'attachment'
            }] : []);
        let attachmentLine = 'Attachment: none';
        if (attachmentList.length) {
            const attachmentSummaries = attachmentList.map((attachment) => {
                const attachmentContent = String(attachment.content || '');
                const attachmentName = attachment.filename || 'attachment';
                if (/^https?:\/\//i.test(attachmentContent)) {
                    return `${attachmentName} (URL)`;
                }
                if (/^data:/i.test(attachmentContent)) {
                    return `${attachmentName} (embedded data URL omitted from export)`;
                }
                if (attachmentContent.length > 256 && /^[A-Za-z0-9+/=\s]+$/.test(attachmentContent)) {
                    const approxKb = Math.round((attachmentContent.replace(/\s/g, '').length * 0.75) / 10.24) / 100;
                    return `${attachmentName} (${approxKb} KB embedded file omitted from export)`;
                }
                return attachmentName;
            });
            attachmentLine = `${attachmentList.length > 1 ? 'Attachments' : 'Attachment'}: ${attachmentSummaries.join(', ')}`;
        }
        return [
            prefix,
            'Email Draft',
            `To: ${draft.recipient || ''}`,
            `Subject: ${draft.subject || ''}`,
            `Tone: ${draft.tone || 'modern'}`,
            '',
            'Body:',
            draft.body || '',
            '',
            attachmentLine
        ].filter((line, idx) => line || idx === 5 || idx === 7).join('\n').trim();
    } catch (err) {
        return content;
    }
}

function normalizeChat(chat) {
    if (!chat || typeof chat !== 'object') return chat;
    if (!Array.isArray(chat.ms)) chat.ms = [];
    chat.ms = chat.ms
        .filter(msg => msg && typeof msg === 'object')
        .map(msg => ({ ...msg, c: normalizeMessageContent(msg.c) }))
        .filter(msg => !(msg.r === 'b' && (!msg.c || !String(msg.c).trim())));

    const ts = Number(chat.updatedAt || chat.updated_at || 0) || Date.now();
    chat.updatedAt = ts;
    chat.updated_at = ts;
    return chat;
}

function normalizeAllChats() {
    state.chats = (state.chats || []).map(normalizeChat);
    window.chats = state.chats;
}

function touchChat(chatId = state.activeId) {
    const chat = state.chats.find(c => c.id === chatId);
    if (!chat) return null;
    const ts = Date.now();
    chat.updatedAt = ts;
    chat.updated_at = ts;
    return chat;
}

function saveLocalChats() {
    const key = chatStorageKey();
    if (!key) return;
    normalizeAllChats();
    localStorage.setItem(key, JSON.stringify(state.chats));
}

function mergeChatsByNewest(localChats, cloudChats) {
    const deleted = new Set(readDeletedChatIds());
    const merged = new Map();
    [...(localChats || []), ...(cloudChats || [])].forEach(rawChat => {
        if (!rawChat || !rawChat.id || deleted.has(rawChat.id)) return;
        const chat = normalizeChat({ ...rawChat, ms: Array.isArray(rawChat.ms) ? rawChat.ms : [] });
        const existing = merged.get(chat.id);
        const existingTs = Number(existing?.updatedAt || existing?.updated_at || 0);
        const chatTs = Number(chat.updatedAt || chat.updated_at || 0);
        if (!existing || chatTs >= existingTs) merged.set(chat.id, chat);
    });
    return Array.from(merged.values()).sort((a, b) => (a.updatedAt || 0) - (b.updatedAt || 0));
}

function flushCloudSync() {
    if (!state.user) return Promise.resolve();
    clearTimeout(persistTimer);
    persistTimer = null;
    saveLocalChats();

    const payload = {
        chats: state.chats.map(chat => normalizeChat({ ...chat, ms: Array.isArray(chat.ms) ? chat.ms : [] })),
        deleted_chat_ids: readDeletedChatIds()
    };

    cloudSyncChain = cloudSyncChain
        .catch(() => {})
        .then(async () => {
            try {
                await api.syncChats(payload);
                if (payload.deleted_chat_ids.length) {
                    const acknowledged = new Set(payload.deleted_chat_ids);
                    writeDeletedChatIds(readDeletedChatIds().filter(id => !acknowledged.has(id)));
                }
            } catch (err) {
                console.error("Cloud chat sync failed, retrying once:", err);
                try {
                    await api.syncChats(payload);
                    if (payload.deleted_chat_ids.length) {
                        const acknowledged = new Set(payload.deleted_chat_ids);
                        writeDeletedChatIds(readDeletedChatIds().filter(id => !acknowledged.has(id)));
                    }
                } catch (retryErr) {
                    console.error("Cloud chat sync retry failed:", retryErr);
                }
            }
        });

    return cloudSyncChain;
}

function requestChatPersist({ immediate = false } = {}) {
    saveLocalChats();
    if (immediate) return flushCloudSync();
    clearTimeout(persistTimer);
    persistTimer = setTimeout(() => flushCloudSync(), CHAT_SYNC_DEBOUNCE_MS);
    return Promise.resolve();
}

// --- Chat Key Handler ---
function handleChatKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    else if (e.key === 'Escape') startNewChat();
}

window.handleChatKey = handleChatKey;

// --- Core: Start New Chat ---
function startNewChat() {
    state.set('activeId', Date.now().toString());
    state.set('activeJobId', null);
    document.getElementById('chat-area').innerHTML = '';
    document.getElementById('chat-area').style.display = 'none';
    document.getElementById('welcome').style.display = 'flex';
    ui.clearImgPreview();
    if (window.clearContextPreview) window.clearContextPreview();
    const p = document.getElementById('prompt');
    if (p) { p.value = ''; p.style.height = 'auto'; }
    ui.renderHist();
    if (window.innerWidth <= 850 && document.getElementById('sidebar').classList.contains('open')) ui.toggleSidebar();
    ui.smartFocus('prompt');
}

// --- Core: Load Chat ---
function loadChat(id) {
    state.set('activeId', id);
    state.set('activeJobId', null);
    localStorage.setItem('helper_active_chat_v2', id);
    const chat = state.chats.find(c => c.id === id);
    if (!chat) {
        startNewChat();
        return;
    }
    document.getElementById('chat-area').innerHTML = '';
    document.getElementById('chat-area').style.display = 'block';
    document.getElementById('welcome').style.display = 'none';
    ui.clearImgPreview();
    if (window.clearContextPreview) window.clearContextPreview();
    chat.ms.forEach((m, idx) => ui.addMsg(m.r, m.c, m.i, idx, m.m || 'AI Assistant', m.masked));
    ui.renderHist();
    if (window.innerWidth <= 850 && document.getElementById('sidebar').classList.contains('open')) ui.toggleSidebar();
    
    // Restore pending prompt if one was typed but not sent during the current page session.
    const pendingPrompt = localStorage.getItem('helper_pending_prompt_' + id);
    const promptEl = document.getElementById('prompt');
    if (promptEl) {
        promptEl.value = pendingPrompt || '';
        if (window.autoRes) window.autoRes(promptEl);
    }
    
    ui.smartFocus('prompt');
    ui.checkAuthMode();

    // Restore scroll position
    const savedScroll = localStorage.getItem('helper_scroll_pos_' + id);
    const chatArea = document.getElementById('chat-area');
    if (chatArea) {
        if (savedScroll !== null) {
            chatArea.scrollTop = parseFloat(savedScroll);
            setTimeout(() => {
                chatArea.scrollTop = parseFloat(savedScroll);
            }, 50);
        } else {
            chatArea.scrollTop = chatArea.scrollHeight;
        }
    }
}

// --- Core: Save/Load User Chats ---
async function loadUserChats() {
    if (!state.user || !state.user.email) return;
    const key = chatStorageKey();
    let localStr = localStorage.getItem(key);
    if (!localStr && localStorage.getItem('helper_chats_v2')) {
        localStr = localStorage.getItem('helper_chats_v2');
        localStorage.setItem(key, localStr); localStorage.removeItem('helper_chats_v2');
    }
    let localChats = [];
    if (localStr) {
        try {
            localChats = JSON.parse(localStr);
        } catch (err) {
            localChats = [];
        }
        state.chats = mergeChatsByNewest(localChats, []); window.chats = state.chats; ui.renderHist();
        const sId = localStorage.getItem('helper_active_chat_v2');
        if (sId && state.chats.find(c => c.id === sId)) loadChat(sId);
    }
    try {
        const data = await api.fetchChats();
        if (data && data.success && data.chats) {
            state.chats = mergeChatsByNewest(state.chats, data.chats);
            window.chats = state.chats;
            saveLocalChats();
            ui.renderHist();
            const sId = localStorage.getItem('helper_active_chat_v2');
            if (sId && state.chats.find(c => c.id === sId)) loadChat(sId);
        }
    } catch (e) { console.error("Cloud fetch failed:", e); }
}

async function saveUserChats() {
    return requestChatPersist({ immediate: true });
}

function replaceGeneratedImageUrlInChats(jobId, localUrl, originalUrl = '') {
    if (!jobId || !localUrl) return;

    const uidMarker = `uid=${jobId}`;
    const escapedUidMarker = `uid%3D${encodeURIComponent(jobId)}`;
    let changed = false;

    state.chats.forEach(chat => {
        (chat.ms || []).forEach(msg => {
            if (!msg || typeof msg.c !== 'string') return;

            const previous = msg.c;
            msg.c = msg.c.replace(/!\[([^\]]*)\]\(([^)]*)\)/g, (match, alt, url) => {
                let decodedUrl = url;
                try {
                    decodedUrl = decodeURIComponent(url);
                } catch (err) {
                    decodedUrl = url;
                }

                const matchesJob =
                    url.includes(uidMarker) ||
                    url.includes(escapedUidMarker) ||
                    decodedUrl.includes(uidMarker) ||
                    (originalUrl && (url === originalUrl || decodedUrl === originalUrl));

                return matchesJob ? `![${alt}](${localUrl})` : match;
            });

            if (msg.c !== previous) changed = true;
        });
    });

    if (changed) {
        state.chats.forEach(chat => {
            if ((chat.ms || []).some(msg => typeof msg.c === 'string' && msg.c.includes(localUrl))) {
                touchChat(chat.id);
            }
        });
        requestChatPersist({ immediate: false }).catch(err => console.error("Failed to persist local generated image URL:", err));
    }
}

window.replaceGeneratedImageUrlInChats = replaceGeneratedImageUrlInChats;
window.addEventListener('upscale-image-ready', event => {
    const detail = event.detail || {};
    replaceGeneratedImageUrlInChats(detail.jobId, detail.localUrl, detail.originalUrl || '');
});

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

    // Show spinner on the Save & Submit button
    const saveBtn = container.querySelector('.edit-btn');
    const origHtml = saveBtn ? saveBtn.innerHTML : 'Save & Submit';
    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.innerHTML = '<div class="spinner" style="width:12px;height:12px;margin-right:5px;border-width:2px;"></div> Re-processing...';
    }

    // Trigger pop and jiggle animations on the mascot/chat agent
    mascot.popBot();
    setTimeout(() => mascot.hitBot(), 500);
    const mContainer = document.getElementById('mascot-container');
    if (mContainer) mContainer.classList.add('thinking');

    // Wait for 1000ms to allow the premium animation to play out
    await new Promise(resolve => setTimeout(resolve, 1000));

    // Remove thinking class (send() will restore it once streaming starts)
    if (mContainer) mContainer.classList.remove('thinking');

    // Extract original image payload before slicing history
    const originalMsg = chat.ms[idx];

    // Clear any existing currentImages and attachedContexts to avoid merging with new pending attachments
    if (state.currentImages) {
        state.currentImages.forEach(img => {
            if (img.blobUrl) URL.revokeObjectURL(img.blobUrl);
        });
    }
    state.currentImages = [];
    state.attachedContexts = [];

    // Populate state.currentImages with the original message's image(s)
    if (originalMsg && originalMsg.i) {
        const imgs = Array.isArray(originalMsg.i) ? originalMsg.i : [originalMsg.i];
        imgs.forEach(base64 => {
            state.currentImages.push({
                id: `img-${Date.now()}-${Math.random()}`,
                base64: base64,
                blobUrl: null
            });
        });
    }

    // Extract original context(s) before slicing history
    if (originalMsg && originalMsg.c) {
        const parsed = window.parseAttachedContexts(originalMsg.c);
        if (parsed.contexts && parsed.contexts.length > 0) {
            parsed.contexts.forEach(ctx => {
                if (!state.attachedContexts.some(c => c.text === ctx.text)) {
                    addAttachedContext(ctx.text, { render: false });
                }
            });
        }
    }

    chat.ms = chat.ms.slice(0, idx);

    // Save current images and contexts to restore
    const savedImages = [...state.currentImages];
    const savedContexts = [...state.attachedContexts];

    // Truncate the DOM to remove the edited message and subsequent ones
    const chatArea = document.getElementById('chat-area');
    if (chatArea) {
        const msgDivs = Array.from(chatArea.children);
        while (msgDivs.length > idx) {
            const last = msgDivs.pop();
            last.remove();
        }
    }

    // Clean up inputs and state
    ui.clearImgPreview();
    if (window.clearContextPreview) window.clearContextPreview();
    ui.renderHist();
    if (window.innerWidth <= 850 && document.getElementById('sidebar').classList.contains('open')) ui.toggleSidebar();
    ui.smartFocus('prompt');
    ui.checkAuthMode();

    // Restore state
    state.currentImages = savedImages;
    state.attachedContexts = savedContexts;

    mascot.triggerBotReaction(newText);
    document.getElementById('prompt').value = newText;
    send();
}

// --- Core: Send Message ---
// --- Core: Send Message ---
async function send() {
    const p = document.getElementById('prompt').value.trim();
    const hasImages = state.currentImages && state.currentImages.length > 0;
    const hasContexts = state.attachedContexts && state.attachedContexts.length > 0;
    if (!p && !hasImages && !hasContexts) return;
    if (!state.activeId) state.set('activeId', Date.now().toString());

    // Clear state persistence keys for this chat on send
    localStorage.removeItem('helper_pending_prompt_' + state.activeId);
    localStorage.removeItem('helper_scroll_pos_' + state.activeId);

    let chat = state.chats.find(c => c.id === state.activeId);
    if (!chat) {
        const ts = Date.now();
        chat = { id: state.activeId, title: p.substring(0, 35) || "Context Prompt", ms: [], updatedAt: ts, updated_at: ts };
        state.chats.push(chat);
    }
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

    let finalPrompt = p;
    if (hasContexts) {
        const formattedContexts = state.attachedContexts.map((ctx, idx) => `[Attached Context ${idx + 1}]\n"""\n${ctx.text}\n"""`).join('\n\n');
        finalPrompt = `${formattedContexts}\n\n${p || "Review the attached context above."}`;
    }

    const base64List = hasImages ? state.currentImages.map(img => img.base64) : [];
    const uiImgPayload = base64List.length === 1 ? base64List[0] : (base64List.length > 1 ? base64List : null);

    ui.addMsg('u', finalPrompt, uiImgPayload, chat.ms.length, null, isMasked);
    chat.ms.push({ r: 'u', c: finalPrompt, i: uiImgPayload, masked: isMasked });
    touchChat(chat.id);
    requestChatPersist({ immediate: false });
    mascot.triggerBotReaction(p || "Attached Context");
    
    window.clearImgPreview();
    window.clearContextPreview();
    
    promptEl.value = ''; promptEl.style.height = 'auto';
    document.getElementById('stop-btn').style.display = 'flex';
    document.getElementById('main-send-btn').style.display = 'none';
    promptEl.placeholder = "Message The All Time Helper...";
    promptEl.classList.remove('auth-waiting');
    state.set('activeJobId', null);

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
    let fullTxt = '';

    try {
        const res = await api.streamChat({
            prompt: finalPrompt, history: chat.ms, model: state.selectedModel, img: uiImgPayload, name: state.user.name,
            persona: document.getElementById('persona-toggle').checked, isMasked,
            sys: {
                english: document.getElementById('t-eng').classList.contains('on'),
                oneword: document.getElementById('t-word').classList.contains('on'),
                pers: document.getElementById('t-pers').classList.contains('on'),
                email_tone: state.get('emailTone')
            }
        }, state.abortController.signal);
        if (res.status === 401) { ui.signOut(); return; }
        if (!res.ok) {
            const errTxt = `System Error ${res.status}: Backend overloaded. Try again.`;
            bTxt.innerText = errTxt;
            chat.ms.push({ r: 'b', c: errTxt, m: mName });
            touchChat(chat.id);
            requestChatPersist({ immediate: true });
            return;
        }
        const reader = res.body.getReader(); let buffer = ''; const decoder = new TextDecoder("utf-8");
        while (true) {
            const { done, value } = await reader.read(); if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n'); buffer = lines.pop();
            lines.forEach(line => {
                const tl = line.trim(); if (!tl || tl.startsWith('<')) return;
                try {
                    const j = JSON.parse(tl);
                    if (j.active_agent) {
                        state.set('activeAgent', j.active_agent);
                    }
                    if (j.job_id) {
                        state.set('activeJobId', j.job_id);
                    }
                    if (j.status) {
                        let se = bTxt.querySelector('#status-text');
                        if (!se) { const sd = document.createElement('div'); sd.className = 'status-msg'; sd.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><circle cx="12" cy="12" r="10"></circle><path d="M12 6v6l4 2"></path></svg> <span id="status-text">${j.status}</span>`; bTxt.prepend(sd); }
                        else se.innerText = j.status;
                        return;
                    }
                    if (j.message && j.message.content) {
                        if (fullTxt === '') { bTxt.querySelector('.typing-indicator')?.remove(); bTxt.querySelector('.status-msg')?.remove(); bTxt.closest('.msg').classList.remove('thinking-state'); }
                        fullTxt += j.message.content; ui.renderBotMessage(bTxt, fullTxt, chat.ms.length);
                    }
                } catch (e) { if (tl.length > 5) console.warn("Dropped:", tl); }
            });
        }
        if (buffer.trim()) { try { const j = JSON.parse(buffer); if (j.message && j.message.content) fullTxt += j.message.content; } catch (e) { } }
        fullTxt = normalizeMessageContent(fullTxt);
        if (chat.title && chat.title.trim().length <= 5 && fullTxt.trim().length > 10) {
            const fl = fullTxt.split('\n')[0]; chat.title = fl.substring(0, 35).trim() + (fl.length > 35 ? '...' : '');
        }
        if (fullTxt.trim()) {
            chat.ms.push({ r: 'b', c: fullTxt, m: mName });
        }
        ui.renderBotMessage(bTxt, fullTxt, chat.ms.length);
        if (typeof hljs !== 'undefined') {
            bTxt.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
        }
        touchChat(chat.id);
        requestChatPersist({ immediate: true });
    } catch (e) {
        const partialText = normalizeMessageContent(fullTxt);
        const stoppedText = partialText
            ? (isCompleteToolResult(partialText) ? partialText : normalizeMessageContent(`${partialText}\n\n[Stopped]`))
            : "Request stopped.";
        if (isCompleteToolResult(stoppedText)) {
            ui.renderBotMessage(bTxt, stoppedText, chat.ms.length);
        } else {
            bTxt.innerText = stoppedText;
        }
        chat.ms.push({ r: 'b', c: stoppedText, m: mName });
        touchChat(chat.id);
        requestChatPersist({ immediate: true });
    }
    finally {
        document.getElementById('stop-btn').style.display = 'none';
        document.getElementById('main-send-btn').style.display = 'flex';
        ui.checkAuthMode(); if (m) m.classList.remove('thinking');
        document.querySelectorAll('.thinking-state').forEach(el => el.classList.remove('thinking-state'));
        document.querySelectorAll('.typing-indicator').forEach(ti => ti.remove());
        state.abortController = null; state.currentImg = null;
        state.set('activeJobId', null);
        state.set('activeAgent', null);
        window.activeId = state.activeId;
        if (state.chats.find(c => c.id === state.activeId)?.ms.length <= 2) ui.renderHist();
        ui.checkAuthMode();
    }
}

async function stopAI() {
    const jobId = state.get('activeJobId');
    if (state.abortController) state.abortController.abort();
    if (jobId) {
        try {
            await api.cancelChatJob(jobId);
        } catch (err) {
            console.warn("Backend cancel request failed:", err);
        }
    }
}

// --- Toggle Pin & Export ---
function togglePin(id) {
    const chat = state.chats.find(c => c.id === id);
    if (chat) {
        chat.pinned = !chat.pinned;
        touchChat(id);
        requestChatPersist({ immediate: false });
        ui.renderHist();
    }
}

function confirmDeleteChat() {
    const id = state.chatToDelete;
    if (!id) return;
    const deletedIds = readDeletedChatIds();
    writeDeletedChatIds([...deletedIds, id]);
    state.chats = state.chats.filter(c => c.id !== id);
    window.chats = state.chats;
    requestChatPersist({ immediate: true });
    if (state.activeId === id) startNewChat();
    else ui.renderHist();
    ui.closeDeleteConfirm();
}

function exportChat() {
    const chat = state.chats.find(c => c.id === state.activeId);
    if (!chat || !chat.ms.length) return;
    let md = `# ${chat.title || 'Conversation'}\n\n`;
    chat.ms.forEach(m => {
        md += `### ${m.r === 'u' ? 'User' : 'Assistant'}\n${formatMessageForExport(m)}\n\n---\n\n`;
    });
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
window.applyThemeChoice = function (choice) {
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
window.toggleThemeMenu = function (e, menuId) {
    if (e) e.stopPropagation(); const target = menuId || 'theme-menu'; const menu = document.getElementById(target); if (!menu) return;
    const vis = menu.style.display === 'flex';
    document.querySelectorAll('.dropdown-menu').forEach(m => m.style.display = 'none');
    document.querySelectorAll('.set-row').forEach(r => r.classList.remove('row-elevated'));
    if (!vis) { menu.style.display = 'flex'; const pr = menu.closest('.set-row'); if (pr) pr.classList.add('row-elevated'); }
};
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => { if (localStorage.getItem('helper_theme_pref') === 'system') ui.setThemeUI(e.matches ? 'dark' : 'light'); });
function initTheme() { window.applyThemeChoice(localStorage.getItem('helper_theme_pref') || 'system'); }

// --- Auto-resize ---
window.autoRes = function (el) { if (!el) return; el.style.height = 'auto'; el.style.height = el.scrollHeight + 'px'; };

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

// --- Neural Drag Helpers ---
function handleMessageDragStart(e, idx) {
    const textEl = document.getElementById(`msg-text-${idx}`);
    if (textEl) {
        e.dataTransfer.setData("text/plain", textEl.innerText);
        const msgEl = textEl.closest('.msg');
        if (msgEl) {
            if (e.dataTransfer.setDragImage) {
                // Use a transparent pixel to hide the default browser drag image
                const img = new Image();
                img.src = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
                e.dataTransfer.setDragImage(img, 0, 0);
            }
            
            // Create a custom 100% opaque floating element on the page
            const ghost = msgEl.cloneNode(true);
            ghost.id = 'custom-drag-ghost';
            ghost.classList.add('drag-snapshot');
            ghost.style.position = 'fixed';
            ghost.style.pointerEvents = 'none';
            ghost.style.zIndex = '99999';
            ghost.style.margin = '0';
            ghost.style.display = 'none';
            document.body.appendChild(ghost);
            
            // Track coordinates smoothly
            const onDrag = (ev) => {
                if (ev.clientX > 0 && ev.clientY > 0) {
                    ghost.style.display = 'block';
                    ghost.style.left = (ev.clientX - 20) + 'px';
                    ghost.style.top = (ev.clientY - 20) + 'px';
                }
            };
            
            msgEl.addEventListener('drag', onDrag);
            
            // Clean up when drag ends
            msgEl.addEventListener('dragend', () => {
                msgEl.removeEventListener('drag', onDrag);
                ghost.remove();
            }, { once: true });
            
            setTimeout(() => msgEl.classList.add('dragging'), 0);
        }
        document.getElementById('mascot-container')?.classList.add('mascot-drop-active');
        document.body.classList.add('neural-grab-active');
    }
}

// Ensure the body class is always cleaned up on drag end
function handleMessageDragEnd(e, idx) {
    const textEl = document.getElementById(`msg-text-${idx}`);
    if (textEl) {
        const msgEl = textEl.closest('.msg');
        if (msgEl) msgEl.classList.remove('dragging');
    }
    document.getElementById('mascot-container')?.classList.remove('mascot-drop-active');
    document.body.classList.remove('neural-grab-active');
}

// --- Pull to Refresh ---
function initPullRefresh() {
    let tsy = 0, tdy = 0;
    window.addEventListener('touchstart', e => { const y = e.touches[0].pageY; tsy = ((window.scrollY === 0 || document.getElementById('chat-area').scrollTop === 0) && y < 60) ? y : 999999; }, { passive: true });
    window.addEventListener('touchmove', e => { tdy = e.touches[0].pageY - tsy; if (tdy > 0 && (window.scrollY === 0 || document.getElementById('chat-area').scrollTop === 0)) { const ind = document.getElementById('pull-indicator'); if (ind) { const p = Math.min(tdy, 180); ind.style.top = (p - 60) + 'px'; ind.style.opacity = Math.min(p / 120, 1); } } }, { passive: true });
    window.addEventListener('touchend', () => { if (tdy > 120) location.reload(); else { const ind = document.getElementById('pull-indicator'); if (ind) { ind.style.top = '-60px'; ind.style.opacity = '0'; } } tdy = 0; });
}

// --- Drag-and-Drop Image Attachments ---
function handleImageDragStart(e, base64Data) {
    e.dataTransfer.setData("text/plain", "ATTACH_IMAGE");
    e.dataTransfer.setData("image-base64", base64Data);
    e.dataTransfer.setData("image-filename", `chat_image_${Date.now()}.png`);
}

function clearContextPreview() {
    state.attachedContexts = [];
    renderAttachmentsPreview();
}

function clearImgPreview() {
    if (state.currentImages) {
        state.currentImages.forEach(img => {
            if (img.blobUrl) URL.revokeObjectURL(img.blobUrl);
        });
    }
    state.currentImages = [];
    const imgIn = document.getElementById('img-in');
    if (imgIn) imgIn.value = '';
    renderAttachmentsPreview();
}

function renderAttachmentsPreview() {
    const area = document.getElementById('img-preview-area');
    if (!area) return;
    area.innerHTML = '';
    
    const hasImages = state.currentImages && state.currentImages.length > 0;
    const hasContexts = state.attachedContexts && state.attachedContexts.length > 0;
    
    if (!hasImages && !hasContexts) {
        area.style.display = 'none';
        return;
    }
    
    area.style.display = 'flex';
    
    if (hasImages) {
        state.currentImages.forEach(img => {
            const thumb = document.createElement('div');
            thumb.className = 'img-thumb-wrap';
            thumb.id = img.id;
            thumb.innerHTML = `
                <img src="${img.blobUrl || 'data:image/png;base64,' + img.base64}" class="img-thumb">
                <button class="img-remove-btn" onclick="window.removeAttachment('${img.id}')">✕</button>
            `;
            area.appendChild(thumb);
        });
    }
    
    if (hasContexts) {
        state.attachedContexts.forEach(ctx => {
            const displaySnippet = ctx.text.length > 25 ? ctx.text.substring(0, 25).trim() + '...' : ctx.text.trim();
            const chip = document.createElement('div');
            chip.className = 'context-thumb-wrap';
            chip.id = ctx.id;
            chip.innerHTML = `
                <div class="context-thumb-icon">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--accent-blue)" stroke-width="2.5" style="vertical-align: middle;">
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                        <polyline points="14 2 14 8 20 8"></polyline>
                    </svg>
                </div>
                <span class="context-thumb-text">Context: "${displaySnippet}"</span>
                <button class="img-remove-btn" onclick="window.removeAttachment('${ctx.id}')">✕</button>
            `;
            area.appendChild(chip);
        });
    }
}

function removeAttachment(id) {
    if (id.startsWith('img-')) {
        const found = state.currentImages.find(img => img.id === id);
        if (found && found.blobUrl) URL.revokeObjectURL(found.blobUrl);
        state.currentImages = state.currentImages.filter(img => img.id !== id);
    } else if (id.startsWith('text-')) {
        state.attachedContexts = state.attachedContexts.filter(ctx => ctx.id !== id);
    }
    renderAttachmentsPreview();
}

function previewImg(i) {
    if (i.files) {
        for (let idx = 0; idx < i.files.length; idx++) {
            const file = i.files[idx];
            if (file.type.startsWith('image/')) {
                const reader = new FileReader();
                reader.onload = (e) => {
                    const id = `img-${Date.now()}-${Math.random()}`;
                    const base64 = e.target.result.split(',')[1];
                    const blobUrl = URL.createObjectURL(file);
                    state.currentImages = state.currentImages || [];
                    state.currentImages.push({ id, base64, blobUrl });
                    renderAttachmentsPreview();
                    ui.selModel('gemma4:e2b', 'Gemma 4');
                };
                reader.readAsDataURL(file);
            }
        }
    }
}

// ==================== DOMContentLoaded ====================
document.addEventListener('DOMContentLoaded', async () => {
    try {
        console.log("DEBUG: app.js orchestrator initializing...");
        initTheme();
        clearPendingComposerDrafts();
        
        // Initialize Default Email Tone
        const currentTone = state.get('emailTone') || 'modern';
        const toneNames = { modern: 'Modern', formal: 'Formal', informal: 'Informal' };
        
        // Custom temporary bridge function for early bindings during init
        const tempApply = (val, name) => {
            state.set('emailTone', val);
            const text = document.getElementById('current-tone-text-settings');
            if (text) text.innerText = name;
            const menu = document.getElementById('tone-menu-settings');
            if (menu) {
                menu.querySelectorAll('.menu-item').forEach(opt => {
                    if (opt.innerText.toLowerCase().includes(name.toLowerCase())) {
                        opt.classList.add('active');
                    } else {
                        opt.classList.remove('active');
                    }
                });
            }
        };
        tempApply(currentTone, toneNames[currentTone] || 'Modern');
        
        document.getElementById('active-model-name').innerText = 'Gemma 4';

        // Specialist badge subscription
        state.subscribe('activeAgent', (val) => {
            const badge = document.getElementById('specialist-badge');
            const text = document.getElementById('specialist-name');
            if (badge && text) {
                if (val) {
                    badge.style.display = 'inline-flex';
                    badge.className = `specialist-badge ${val}`;
                    text.innerText = val.toUpperCase() + ' ACTIVE';
                } else {
                    badge.style.display = 'none';
                }
            }
        });

        const savedUser = localStorage.getItem('helper_user_v2');
        if (savedUser) {
            try {
                state.set('user', JSON.parse(savedUser));
                document.getElementById('auth-overlay').style.display = 'none';
                await loadUserChats();
                if (localStorage.getItem('helper_active_modal_v2') === 'settings') ui.openSettings();
                ui.updUI();
                if (!localStorage.getItem('helper_theme_pref')) document.getElementById('theme-modal').style.display = 'flex';
                ui.smartFocus('prompt');
            } catch (err) {
                console.warn('Failed to restore saved user state, clearing local auth cache:', err);
                localStorage.removeItem('helper_user_v2');
                localStorage.removeItem('helper_token_v2');
                localStorage.removeItem('helper_active_chat_v2');
                localStorage.removeItem('helper_active_modal_v2');
                document.getElementById('auth-overlay').style.display = 'flex';
                ui.renderHist();
                document.getElementById('l-email')?.focus();
            }
        } else { document.getElementById('l-email').focus(); ui.renderHist(); }

        // Mouse tracking
        mascot.bindMouseListeners();

        // Prompt input & Drag-and-Drop Image Attachments
        const promptIn = document.getElementById('prompt');
        const sendBtn = document.getElementById('main-send-btn');
        const promptContainer = document.querySelector('.prompt-container') || promptIn;
        if (promptIn) {
            promptIn.addEventListener('input', () => { 
                window.autoRes(promptIn); 
                sendBtn?.classList.toggle('pulsing', promptIn.value.trim().length > 0); 
                if (state.activeId) {
                    localStorage.setItem('helper_pending_prompt_' + state.activeId, promptIn.value);
                }
            });
            promptIn.addEventListener('keydown', handleChatKey);
        }

        const chatArea = document.getElementById('chat-area');
        if (chatArea) {
            chatArea.addEventListener('scroll', () => {
                if (state.activeId) {
                    localStorage.setItem('helper_scroll_pos_' + state.activeId, chatArea.scrollTop);
                }
            });
        }

        const confirmDelBtn = document.getElementById('confirm-del-btn');
        if (confirmDelBtn) confirmDelBtn.addEventListener('click', confirmDeleteChat);
        if (promptContainer) {
            promptContainer.addEventListener('dragover', (e) => {
                e.preventDefault();
                promptContainer.classList.add('prompt-drag-over');
            });
            promptContainer.addEventListener('dragleave', () => {
                promptContainer.classList.remove('prompt-drag-over');
            });
            promptContainer.addEventListener('drop', (e) => {
                e.preventDefault();
                promptContainer.classList.remove('prompt-drag-over');
                promptContainer.classList.add('prompt-drop-success');
                setTimeout(() => promptContainer.classList.remove('prompt-drop-success'), 450);

                // Case 1: Drop image from chat history
                const textVal = e.dataTransfer.getData("text/plain");
                if (textVal === "ATTACH_IMAGE") {
                    const base64 = e.dataTransfer.getData("image-base64");
                    if (base64) {
                        const id = `img-${Date.now()}-${Math.random()}`;
                        state.currentImages = state.currentImages || [];
                        state.currentImages.push({ id, base64, blobUrl: null });
                        renderAttachmentsPreview();
                        ui.selModel('gemma4:e2b', 'Gemma 4');
                    }
                    return;
                }

                // Case 2: Drop local file from operating system
                if (e.dataTransfer.files && e.dataTransfer.files[0]) {
                    for (let i = 0; i < e.dataTransfer.files.length; i++) {
                        const file = e.dataTransfer.files[i];
                        if (file.type.startsWith('image/')) {
                            const reader = new FileReader();
                            reader.onload = (ev) => {
                                const id = `img-${Date.now()}-${Math.random()}`;
                                const base64 = ev.target.result.split(',')[1];
                                const blobUrl = URL.createObjectURL(file);
                                state.currentImages = state.currentImages || [];
                                state.currentImages.push({ id, base64, blobUrl });
                                renderAttachmentsPreview();
                                ui.selModel('gemma4:e2b', 'Gemma 4');
                            };
                            reader.readAsDataURL(file);
                        }
                    }
                    return;
                }

                // Case 3: Drop text (from message drag handle or other source)
                if (textVal) {
                    if (addAttachedContext(textVal)) {
                        const sendBtn = document.getElementById('main-send-btn');
                        if (sendBtn) sendBtn.classList.add('pulsing');
                    }
                }
            });
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

            // Close tone-menu-settings when clicking outside
            const tms = document.getElementById('tone-menu-settings');
            if (tms && tms.style.display === 'flex' && !tms.contains(e.target) && !document.getElementById('tone-btn-settings')?.contains(e.target)) {
                tms.style.display = 'none';
                tms.closest('.set-row')?.classList.remove('row-elevated');
            }
        });

        // Image zoom
        (function () {
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
        initSidebarSwipe();
        initPullRefresh();

        // --- Window Bridge ---
        window.handleMessageDragStart = handleMessageDragStart;
        window.handleMessageDragEnd = handleMessageDragEnd;
        window.handleImageDragStart = handleImageDragStart;
        window.handleAuth = handleAuth;
        window.switchAuth = ui.switchAuth;
        window.signOut = ui.signOut;
        window.toggleDropdown = ui.toggleDropdown;
        window.selModel = ui.selModel;
        window.applyToneChoice = function (val, name) {
            state.set('emailTone', val);
            const text = document.getElementById('current-tone-text-settings');
            if (text) text.innerText = name;
            const menu = document.getElementById('tone-menu-settings');
            if (menu) {
                menu.querySelectorAll('.menu-item').forEach(opt => {
                    if (opt.innerText.toLowerCase().includes(name.toLowerCase())) {
                        opt.classList.add('active');
                    } else {
                        opt.classList.remove('active');
                    }
                });
                menu.style.display = 'none';
                menu.closest('.set-row')?.classList.remove('row-elevated');
            }
        };
        window.toggleToneDropdownSettings = function (e) {
            if (e) e.stopPropagation();
            const menu = document.getElementById('tone-menu-settings');
            if (!menu) return;
            const vis = menu.style.display === 'flex';
            document.querySelectorAll('.dropdown-menu').forEach(m => m.style.display = 'none');
            document.querySelectorAll('.set-row').forEach(r => r.classList.remove('row-elevated'));
            if (!vis) {
                menu.style.display = 'flex';
                menu.closest('.set-row')?.classList.add('row-elevated');
            }
        };
        window.send = send;
        window.startNewChat = startNewChat;
        window.loadChat = loadChat;
        window.showDeleteConfirm = ui.showDeleteConfirm;
        window.closeDeleteConfirm = ui.closeDeleteConfirm;
        window.clearImgPreview = clearImgPreview;
        window.clearContextPreview = clearContextPreview;
        window.previewImg = previewImg;
        window.removeAttachment = removeAttachment;
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
        window.confirmDeleteChat = confirmDeleteChat;
        window.exportChat = exportChat;
        window.renderHist = ui.renderHist;
        window.chats = state.chats;
        window.activeId = state.activeId;
        window.persistChatMutation = function (chatId, { immediate = false } = {}) {
            touchChat(chatId || state.activeId);
            return requestChatPersist({ immediate });
        };
        window.updateSavedBotMessage = function (idx, content, { immediate = false } = {}) {
            const chat = state.chats.find(c => c.id === state.activeId);
            if (!chat || !chat.ms[idx]) return;
            chat.ms[idx].c = normalizeMessageContent(content);
            touchChat(chat.id);
            return requestChatPersist({ immediate });
        };

        console.log("DEBUG: app.js orchestrator ready.");
    } catch (e) { console.error("Critical Runtime Error:", e); }
    finally {
        finishPageLoader('Loaded');
    }
});
