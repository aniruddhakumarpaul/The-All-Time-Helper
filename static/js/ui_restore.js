// ui_restore.js
(function () {
    let stateRef = null;
    let busy = false;
    let lastRun = 0;

    function userEmail() {
        try {
            const user = stateRef?.user || JSON.parse(localStorage.getItem('helper_user_v2') || 'null');
            return user?.email || '';
        } catch (_) { return ''; }
    }

    function cacheKey() {
        const email = userEmail();
        return email ? 'helper_chats_v2_' + email : '';
    }

    function normalize(item) {
        if (!item || typeof item !== 'object') return null;
        const id = String(item.id || '').trim();
        if (!id) return null;
        const ms = Array.isArray(item.ms) ? item.ms : Array.isArray(item.messages) ? item.messages : [];
        const updated = Number(item.updated_at || item.updatedAt || 0) || 0;
        return { ...item, id, title: String(item.title || 'New Chat'), ms, updated_at: updated, updatedAt: updated };
    }

    function merge(a, b) {
        const byId = new Map();
        [...(a || []), ...(b || [])].forEach(raw => {
            const item = normalize(raw);
            if (!item) return;
            const old = byId.get(item.id);
            if (!old || Number(item.updated_at || 0) > Number(old.updated_at || 0) || (item.ms || []).length > (old.ms || []).length) byId.set(item.id, item);
        });
        return Array.from(byId.values()).sort((x, y) => Number(x.updated_at || 0) - Number(y.updated_at || 0));
    }

    function readLocal() {
        const key = cacheKey();
        if (!key) return [];
        try {
            const parsed = JSON.parse(localStorage.getItem(key) || '[]');
            return Array.isArray(parsed) ? parsed : [];
        } catch (_) { return []; }
    }

    function writeLocal(items) {
        const key = cacheKey();
        if (key && Array.isArray(items) && items.length) localStorage.setItem(key, JSON.stringify(items));
    }

    function apply(items) {
        if (!stateRef || !Array.isArray(items) || !items.length) return;
        stateRef.chats = items;
        window.chats = items;
        if (typeof window.renderHist === 'function') window.renderHist();
    }

    function visible() {
        return Boolean(document.getElementById('chat-area')?.querySelector('.msg'));
    }

    function choose(items) {
        const saved = localStorage.getItem('helper_active_chat_v2');
        return items.find(item => item.id === saved) || items.slice().sort((a, b) => Number(b.updated_at || 0) - Number(a.updated_at || 0))[0];
    }

    function open(items, force) {
        if (!force && visible()) return;
        const target = choose(items || []);
        if (!target) return;
        localStorage.setItem('helper_active_chat_v2', target.id);
        stateRef.activeId = target.id;
        window.activeId = target.id;
        if (typeof window.loadChat === 'function') window.loadChat(target.id);
    }

    async function remote() {
        const token = localStorage.getItem('helper_token_v2') || '';
        if (!token) return [];
        const res = await fetch('/get_chats', {
            headers: { 'Authorization': 'Bearer ' + token, 'ngrok-skip-browser-warning': '69420' },
            cache: 'no-store'
        });
        if (!res.ok) return [];
        const data = await res.json().catch(() => ({}));
        return data && data.success !== false && Array.isArray(data.chats) ? data.chats : [];
    }

    async function run(reason, forceOpen) {
        const now = Date.now();
        if (busy || now - lastRun < 1200) return;
        busy = true;
        lastRun = now;
        try {
            const local = merge(stateRef?.chats || [], readLocal());
            if (local.length) { apply(local); writeLocal(local); open(local, forceOpen); }
            const cloud = await remote();
            const combined = merge(local, cloud);
            if (combined.length) {
                apply(combined);
                writeLocal(combined);
                open(combined, forceOpen || !visible());
                console.info('[UIRestore] applied', combined.length, reason || 'load');
            }
        } catch (err) {
            console.warn('[UIRestore] failed', err);
        } finally {
            busy = false;
        }
    }

    function start(state) {
        stateRef = state;
        window.__restoreVisibleChats = () => run('manual', true);
        setTimeout(() => run('initial', true), 800);
        setTimeout(() => run('late', !visible()), 2500);
        setInterval(() => { if (!visible()) run('blank', true); }, 3000);
        window.addEventListener('focus', () => run('focus', !visible()));
    }

    function init() {
        if (window.__helperState) return start(window.__helperState);
        import('/static/js/state.js').then(mod => start(mod.state)).catch(err => console.warn('[UIRestore] state unavailable', err));
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
