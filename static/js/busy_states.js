// busy_states.js
// Lightweight busy indicators for slow non-blocking actions.
(function () {
    const MARKER = '__helperBusyStatesInstalled';
    if (window[MARKER]) return;
    window[MARKER] = true;

    const active = new Map();

    function ensureOverlay(id, className, label) {
        let el = document.getElementById(id);
        if (el) return el;
        el = document.createElement('div');
        el.id = id;
        el.className = className;
        el.innerHTML = `<span class="busy-spinner" aria-hidden="true"></span><span class="busy-label">${label}</span>`;
        document.body.appendChild(el);
        return el;
    }

    function ensureInline(id, parent, label) {
        if (!parent) return null;
        let el = document.getElementById(id);
        if (el) return el;
        el = document.createElement('div');
        el.id = id;
        el.className = 'helper-inline-busy';
        el.innerHTML = `<span class="busy-spinner" aria-hidden="true"></span><span class="busy-label">${label}</span>`;
        parent.appendChild(el);
        return el;
    }

    function setBusy(key, on, options = {}) {
        const current = active.get(key) || 0;
        const next = on ? current + 1 : Math.max(0, current - 1);
        if (next) active.set(key, next);
        else active.delete(key);

        const enabled = next > 0;
        if (key === 'restore') {
            const el = ensureOverlay('chat-restore-busy', 'helper-floating-busy helper-restore-busy', 'Restoring chats');
            el.classList.toggle('is-visible', enabled);
            document.body.classList.toggle('chat-restore-active', enabled);
        }
        if (key === 'context') {
            const card = document.getElementById('neural-context-card');
            const el = ensureInline('neural-context-busy', card, 'Searching memory');
            el?.classList.toggle('is-visible', enabled);
            card?.classList.toggle('context-busy', enabled);
        }
        if (key === 'attachment') {
            const preview = document.getElementById('img-preview-area') || document.querySelector('.pill-bar-container');
            const el = ensureInline('attachment-upload-busy', preview, 'Uploading attachment');
            el?.classList.toggle('is-visible', enabled);
            document.querySelector('.img-btn')?.classList.toggle('is-uploading', enabled);
        }
        if (key === 'email') {
            const card = options.card;
            card?.classList.toggle('email-send-busy', enabled);
        }
    }

    function urlPath(input) {
        try {
            if (typeof input === 'string') return new URL(input, window.location.origin).pathname;
            if (input && input.url) return new URL(input.url, window.location.origin).pathname;
        } catch (_) {}
        return '';
    }

    function wrapFetch() {
        if (window.fetch?.__busyStatesWrapped) return;
        const originalFetch = window.fetch.bind(window);
        async function busyFetch(input, init = {}) {
            const path = urlPath(input);
            let key = '';
            if (path === '/get_chats') key = 'restore';
            else if (path === '/retrieve_context') key = 'context';
            else if (path === '/attachments') key = 'attachment';
            if (key) setBusy(key, true);
            try {
                return await originalFetch(input, init);
            } finally {
                if (key) setBusy(key, false);
            }
        }
        busyFetch.__busyStatesWrapped = true;
        window.fetch = busyFetch;
    }

    function markAttachmentInput() {
        document.getElementById('img-in')?.addEventListener('change', event => {
            if (event.currentTarget?.files?.length) {
                setBusy('attachment', true);
                window.setTimeout(() => setBusy('attachment', false), 900);
            }
        });
    }

    function init() {
        wrapFetch();
        markAttachmentInput();
        window.setHelperBusyState = setBusy;
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
