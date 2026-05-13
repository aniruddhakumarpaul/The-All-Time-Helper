/**
 * stateManager.js - Centralized Application State
 */

export class StateManager {
    constructor() {
        this.state = {
            botState: 'idle',
            user: null,
            chats: [],
            activeId: null,
            selectedModel: 'gemma4:e2b',
            currentImg: null,
            currentBlobUrl: null,
            isRenaming: false,
            currentSearch: '',
            chatToDelete: null,
            abortC: null,
            isGDown: false
        };

        this.listeners = [];
        this.init();
    }

    init() {
        // Load initial user from LocalStorage
        const savedUser = localStorage.getItem('helper_user_v2');
        if (savedUser) {
            this.state.user = JSON.parse(savedUser);
        }

        // Restore active chat ID
        this.state.activeId = localStorage.getItem('helper_active_chat_v2');
    }

    // --- State Accessors ---
    
    get user() { return this.state.user; }
    set user(val) { 
        this.state.user = val; 
        if (val) localStorage.setItem('helper_user_v2', JSON.stringify(val));
        else localStorage.removeItem('helper_user_v2');
        this.notify();
    }

    get chats() { return this.state.chats; }
    set chats(val) { 
        this.state.chats = val; 
        this.saveToLocal();
        this.notify();
    }

    get activeId() { return this.state.activeId; }
    set activeId(val) { 
        this.state.activeId = val; 
        localStorage.setItem('helper_active_chat_v2', val);
        this.notify();
    }

    get botState() { return this.state.botState; }
    set botState(val) { this.state.botState = val; this.notify(); }

    get abortC() { return this.state.abortC; }
    set abortC(val) { this.state.abortC = val; }

    // --- Helper Methods ---

    saveToLocal() {
        if (this.state.user && this.state.user.email) {
            const key = 'helper_chats_v2_' + this.state.user.email;
            localStorage.setItem(key, JSON.stringify(this.state.chats));
        }
    }

    notify() {
        this.listeners.forEach(cb => cb(this.state));
    }

    subscribe(cb) {
        this.listeners.push(cb);
        cb(this.state);
        return () => {
            this.listeners = this.listeners.filter(l => l !== cb);
        };
    }

    // Get Chat by ID
    getChat(id) {
        return this.state.chats.find(c => c.id === id);
    }

    // Migration logic for legacy keys
    migrateLegacyChats() {
        if (this.state.user && this.state.user.email) {
            const key = 'helper_chats_v2_' + this.state.user.email;
            if (!localStorage.getItem(key) && localStorage.getItem('helper_chats_v2')) {
                const legacy = localStorage.getItem('helper_chats_v2');
                localStorage.setItem(key, legacy);
                localStorage.removeItem('helper_chats_v2');
                return JSON.parse(legacy);
            }
        }
        return null;
    }
}

export const stateManager = new StateManager();
