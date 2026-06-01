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
        this.activeJobId = null;
        this.abortController = null;
        this.currentImg = null;
        this.currentBlobUrl = null;
        this.attachedContext = null;
        this.attachedContexts = [];
        this.currentImages = [];
        this.chatToDelete = null;
        this.isRenaming = false;
        this.currentSearch = '';
        
        // --- UI State ---
        this.selectedModel = 'gemma4:e2b';
        this.emailTone = 'modern';
        this.activeAgent = null;
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
        if (this._listeners[key]) {
            this._listeners[key].forEach(cb => {
                try { cb(value, old); } catch (e) { console.error(`State listener error [${key}]:`, e); }
            });
        }
    }
    
    get(key) {
        return this[key];
    }
}

// Singleton instance
const state = new AppState();

// Expose to window for legacy inline handlers
window.botState = state.botState;
window.chats = state.chats;
window.activeId = state.activeId;

export { state, AppState };
