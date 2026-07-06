// chat_refresh_repair.js
// Refresh-safe recovery for cases where chats exist server-side but the visible UI reloads blank.
(function () {
    let stateRef = null;
    let recoveryInFlight = false;
    let lastRecoveryAt = 0;

    function getUserEmail() {
        try {
            const user = stateRef?.user || JSON.parse(localStorage.getItem('helper_user_v2') || 'null');
            return user?.email || '';
        } catch (_) {
            return '';
        }
    }

    function localKey() {
        const email = getUserEmail();
        return email ? 'helper_chats_v2_' + email : '';
    }

    function normalizeChat(chat) {
        if (!chat || typeof chat !== 'object') return null;
        const id = String(chat.id || '').trim();
        if (!id) return null;
        const ms = Array.isArray(chat.ms) ? chat.ms : Array.isArray(chat.messages) ? chat.messages : [];
        const updated = Number(chat.updated_at || chat.updatedAt || 0) || 0;
        return {
            ...chat,
            id,
            title: String(chat.title || 'New Chat'),
            ms,
            updated_at: updated,
            updatedAt: updated,
        };
    }

    function mergeChats(localChats, remoteChats) {
        const byId = new Map();
        [...(localChats || []), ...(remoteChats || [])].forEach(raw => {
            const chat = normalizeChat(raw);
            if (!chat) return;
            const existing = byId.get(chat.id);
            if (!existing) {
                byId.set(chat.id, chat);
                return;
            }
            const chatScore = [Number(chat.updated_at || 0), (chat.ms || []).length];
            const existingScore = [Number(existing.updated_at || 0), (existing.ms || []).length];
            if (chatScore[0] > existingScore[0] || (chatScore[0] === existingScore[0] && chatScore[1] > existingScore[1])) {
                byId.set(chat.id, chat);
            }
        });
        return Array.from(byId.values()).sort((a, b) => Number(a.updated_at || 0) - Number(b.updated_at || 0));
    }

    function getLocalChats() {
        const key = localKey();
        if (!key) return [];
        try {
            const parsed = JSON.parse(localStorage.getItem(key) || '[]');
            return Array.isArray(parsed) ? parsed : [];
        } catch (_) {
            return [];
        }
    }

    function saveLocalChats(chats) {
        const key = localKey();
        if (!key || !Array.isArray(chats) || !chats.length) return;
        localStorage.setItem(key, JSON.stringify(chats));
    }

    function setStateChats(chats) {
        if (!stateRef || !Array.isArray(chats) || !chats.length) return;
        stateRef.chats = chats;
        window.chats = chats;
        if (typeof window.renderHist === 'function') window.renderHist();
    }

    function hasVisibleMessages() {
        return Boolean(document.getElementById('chat-area')?.querySelector('.msg'));
    }

    function pickChat(chats) {
        if (!Array.isArray(chats) || !chats.length) return null;
        const savedId = localStorage.getItem('helper_active_chat_v2');
        return chats.find(chat => chat.id === savedId)
            || chats.slice().sort((a, b) => Number(b.updated_at || b.updatedAt || 0) - Number(a.updated_at || a.updatedAt || 0))[0];
    }

    function openBestChat(chats, force) {
        if (!force && hasVisibleMessages()) return;
        const target = pickChat(chats);
        if (!target) return;
        localStorage.setItem('helper_active_chat_v2', target.id);
        if (stateRef) {
            stateRef.activeId = target.id;
            window.activeId = target.id;
        }
        if (typeof window.loadChat === 'function') window.loadChat(target.id);
    }

    async function fetchRemoteChats() {
        const token = localStorage.getItem('helper_token_v2') || '';
        if (!token) return [];
        const response = await fetch('/get_chats', {
            headers: {
                'Authorization': 'Bearer ' + token,
                'ngrok-skip-browser-warning': '69420',
            },
            cache: 'no-store',
        });
        if (!response.ok) return [];
        const data = await response.json().catch(() => ({}));
        if (!data || data.success === false || !Array.isArray(data.chats)) return [];
        return data.chats;
    }

    async function recoverChats(reason, forceOpen = false) {
        const now = Date.now();
        if (recoveryInFlight || now - lastRecoveryAt < 1500) return;
        recoveryInFlight = true;
        lastRecoveryAt = now;
        try {
            const localChats = mergeChats(stateRef?.chats || [], getLocalChats());
            if (localChats.length) {
                setStateChats(localChats);
                saveLocalChats(localChats);
                openBestChat(localChats, forceOpen);
            }

            const remoteChats = await fetchRemoteChats();
            const merged = mergeChats(localChats, remoteChats);
            if (merged.length) {
                setStateChats(merged);
                saveLocalChats(merged);
                openBestChat(merged, forceOpen || !hasVisibleMessages());
                console.info('[ChatRefreshRepair] recovered chats:', merged.length, reason || 'refresh');
            }
        } catch (error) {
            console.warn('[ChatRefreshRepair] recovery failed:', error);
        } finally {
            recoveryInFlight = false;
        }
    }

    function installRecoveryTriggers() {
        setTimeout(() => recoverChats('initial', true), 800);
        setTimeout(() => recoverChats('delayed', !hasVisibleMessages()), 2500);
        setInterval(() => {
            const hasStateChats = Array.isArray(stateRef?.chats) && stateRef.chats.length > 0;
            const hasLocalChats = getLocalChats().length > 0;
            if (!hasVisibleMessages() && (hasStateChats || hasLocalChats)) recoverChats('blank-ui', true);
        }, 3000);
        window.addEventListener('focus', () => recoverChats('focus', !hasVisibleMessages()));
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') recoverChats('visible', !hasVisibleMessages());
        });
    }

    function initWithState(state) {
        stateRef = state;
        window.__recoverChatsFromServer = () => recoverChats('manual', true);
        installRecoveryTriggers();
    }

    function init() {
        if (window.__helperState) {
            initWithState(window.__helperState);
            return;
        }
        import('/static/js/state.js').then(module => initWithState(module.state)).catch(error => {
            console.warn('[ChatRefreshRepair] state module unavailable:', error);
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
