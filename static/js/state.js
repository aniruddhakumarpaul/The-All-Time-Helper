/**
 * state.js — Reactive Application State Manager
 * 
 * Single source of truth for all mutable application state.
 * Provides a subscribe() mechanism for reactive UI updates.
 */

class AppState {
    constructor() {
        // --- User & Auth ---
        this.user = null;
        
        // --- Chat Data ---
        this.chats = [];
        this.activeId = null;
        this.abortController = null;
        this.activeJobId = null;
        this.currentImg = null;
        this.currentImages = [];
        this.pendingImageUploads = null;
        this.attachedContexts = [];
        this.currentBlobUrl = null;
        this.previewBlobUrls = [];
        this.chatToDelete = null;
        this.isRenaming = false;
        this.currentSearch = '';
        
        // --- UI State ---
        this.selectedModel = 'helper-auto';
        this.responseStyle = 'adaptive';
        this.tiltSettleTimer = null;
        
        // --- Bot Mascot ---
        this.botState = 'idle';
        
        // --- Upscaler ---
        this.activePollers = new Set();
        
        // --- Subscribers ---
        this._listeners = {};
    }
    
    /**
     * Subscribe to state changes on a specific key.
     * @param {string} key - State property name
     * @param {Function} callback - Called with (newValue, oldValue)
     * @returns {Function} Unsubscribe function
     */
    subscribe(key, callback) {
        if (!this._listeners[key]) this._listeners[key] = [];
        this._listeners[key].push(callback);
        return () => {
            this._listeners[key] = this._listeners[key].filter(cb => cb !== callback);
        };
    }
    
    /**
     * Set a state value and notify subscribers.
     * @param {string} key 
     * @param {*} value 
     */
    set(key, value) {
        const old = this[key];
        this[key] = value;
        this.touch(key, value, old);
    }

    /** Notify subscribers after an in-place mutation of an array or object. */
    touch(key, value = this[key], old = value) {
        if (!this._listeners[key]) return;
        this._listeners[key].forEach(cb => {
            try { cb(value, old); } catch (e) { console.error('State listener error [' + key + ']:', e); }
        });
    }

    get(key) {
        return this[key];
    }

    replaceChats(chats) {
        this.set('chats', Array.isArray(chats) ? chats : []);
        return this.chats;
    }

    appendChat(chat) {
        if (!chat || typeof chat !== 'object') return null;
        this.chats.push(chat);
        this.touch('chats');
        return chat;
    }

    updateChat(chatId, patch) {
        const chat = this.chats.find(item => item?.id === chatId);
        if (!chat || !patch || typeof patch !== 'object') return null;
        Object.assign(chat, patch);
        this.touch('chats');
        return chat;
    }

    setActiveChat(chatId) {
        this.set('activeId', chatId || null);
        return this.activeId;
    }

    markChatUpdated(chatId, timestamp = Date.now()) {
        return this.updateChat(chatId, { updated_at: timestamp });
    }

    deleteChat(chatId) {
        this.replaceChats(this.chats.filter(chat => chat?.id !== chatId));
        return this.chats;
    }

    appendMessage(chatId, message) {
        const chat = this.chats.find(item => item?.id === chatId);
        if (!chat || !message || typeof message !== 'object') return null;
        if (!Array.isArray(chat.ms)) chat.ms = [];
        chat.ms.push(message);
        this.touch('chats');
        return message;
    }

    truncateMessages(chatId, index) {
        const chat = this.chats.find(item => item?.id === chatId);
        if (!chat || !Array.isArray(chat.ms)) return null;
        chat.ms = chat.ms.slice(0, Math.max(0, Number(index) || 0));
        this.touch('chats');
        return chat.ms;
    }

    replaceAttachedContexts(contexts) {
        this.set('attachedContexts', Array.isArray(contexts) ? contexts : []);
        return this.attachedContexts;
    }

    addAttachedContext(context) {
        if (!context || typeof context !== 'object') return null;
        this.attachedContexts.push(context);
        this.touch('attachedContexts');
        return context;
    }

    removeAttachedContext(predicate) {
        const matcher = typeof predicate === 'function' ? predicate : item => item === predicate;
        const before = this.attachedContexts.length;
        const filtered = this.attachedContexts.filter((item, index) => !matcher(item, index));
        if (filtered.length === before) return false;
        this.replaceAttachedContexts(filtered);
        return true;
    }

    replaceCurrentImages(images) {
        this.set('currentImages', Array.isArray(images) ? images : []);
        return this.currentImages;
    }

    setPendingImageUploads(promise) {
        this.set('pendingImageUploads', promise || null);
        return this.pendingImageUploads;
    }
}

// Singleton instance
const state = new AppState();

function stripAttachmentPayloadForPersistence(item) {
    if (!item || typeof item !== 'object') return item;
    const next = { ...item };
    delete next.content;
    delete next.data;
    delete next.bytes;
    delete next.attachment_content;
    return next;
}

function findJsonEnd(source, start) {
    let depth = 0;
    let inString = false;
    let escaped = false;
    for (let index = start; index < source.length; index += 1) {
        const char = source[index];
        if (inString) {
            if (escaped) escaped = false;
            else if (char === '\\') escaped = true;
            else if (char === '"') inString = false;
            continue;
        }
        if (char === '"') { inString = true; continue; }
        if (char === '{') depth += 1;
        if (char === '}') {
            depth -= 1;
            if (depth === 0) return index + 1;
        }
    }
    return -1;
}

function redactDraftMarkerText(value) {
    let text = String(value || '');
    for (const marker of ['EMAIL_DRAFT_CONTEXT:', 'EMAIL_DRAFT_PAYLOAD:']) {
        const markerIndex = text.indexOf(marker);
        if (markerIndex < 0) continue;
        const jsonStart = markerIndex + marker.length;
        const source = text.slice(jsonStart).trimStart();
        const offset = text.slice(jsonStart).length - source.length;
        const jsonEnd = findJsonEnd(source, 0);
        if (jsonEnd < 0) continue;
        try {
            const draft = JSON.parse(source.slice(0, jsonEnd));
            const next = { ...draft };
            const hadContent = Boolean(next.attachment_content);
            delete next.attachment_content;
            if (Array.isArray(next.attachments)) {
                next.attachments = next.attachments.map(stripAttachmentPayloadForPersistence);
            }
            if (hadContent) next.has_attachment_content = true;
            text = text.slice(0, jsonStart + offset) + JSON.stringify(next) + source.slice(jsonEnd);
        } catch (_) {
            // Keep malformed historical text unchanged rather than losing the conversation.
        }
    }
    return text;
}

function sanitizeChatsForPersistence(chats) {
    return (Array.isArray(chats) ? chats : []).map(chat => ({
        ...chat,
        ms: (Array.isArray(chat?.ms) ? chat.ms : []).map(message => {
            const next = { ...message };
            if (next.masked) {
                next.c = '[MASKED_SECRET]';
                delete next.apiPrompt;
            }
            delete next.i;
            delete next.img;
            if (Array.isArray(next.attachments)) {
                next.attachments = next.attachments.map(stripAttachmentPayloadForPersistence);
            }
            if (typeof next.c === 'string') next.c = redactDraftMarkerText(next.c);
            if (typeof next.apiPrompt === 'string') next.apiPrompt = redactDraftMarkerText(next.apiPrompt);
            return next;
        })
    }));
}

window.helperSanitizeChatsForPersistence = sanitizeChatsForPersistence;
function loadEmailDraftRepairLayer() {
    if (document.querySelector('script[data-helper-extension="email-draft-repair"]')) return;
    const script = document.createElement('script');
    script.src = '/static/js/email_draft_repair.js?v=4';
    script.defer = true;
    script.dataset.helperExtension = 'email-draft-repair';
    document.body.appendChild(script);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', loadEmailDraftRepairLayer);
else loadEmailDraftRepairLayer();

// Expose to window for legacy inline handlers
window.botState = state.botState;
window.chats = state.chats;
window.activeId = state.activeId;
window.__helperState = state;

export { state, AppState };
