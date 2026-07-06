// latest_view_guard.js
// After a refresh restore, keep the restored conversation positioned at the newest turn.
(function () {
    let lastScrollAt = 0;
    let observer = null;

    function chatArea() {
        return document.getElementById('chat-area');
    }

    function scrollToLatest(reason) {
        const area = chatArea();
        if (!area || !area.querySelector('.msg')) return;
        const now = Date.now();
        if (now - lastScrollAt < 250) return;
        lastScrollAt = now;

        requestAnimationFrame(() => {
            const last = area.querySelector('.msg:last-child');
            if (last && typeof last.scrollIntoView === 'function') {
                last.scrollIntoView({ block: 'end', inline: 'nearest' });
            }
            area.scrollTop = area.scrollHeight;
            const root = document.scrollingElement || document.documentElement;
            if (root) root.scrollTop = root.scrollHeight;
            window.__latestViewGuardReason = reason || 'restore';
        });
    }

    function installMutationObserver() {
        const area = chatArea();
        if (!area || observer) return;
        observer = new MutationObserver((mutations) => {
            if (mutations.some(mutation => mutation.addedNodes && mutation.addedNodes.length)) {
                scrollToLatest('messages-added');
            }
        });
        observer.observe(area, { childList: true, subtree: false });
    }

    function installImageFallback() {
        document.addEventListener('error', event => {
            const img = event.target;
            if (!img || img.tagName !== 'IMG') return;
            const src = String(img.currentSrc || img.src || '');
            if (!src.includes('/static/uploads/')) return;
            img.dataset.loaded = 'true';
            img.style.display = 'none';
            const parent = img.closest('.img-wrap') || img.parentElement;
            if (parent && !parent.querySelector('.missing-local-image-note')) {
                const note = document.createElement('div');
                note.className = 'missing-local-image-note';
                note.style.cssText = 'padding:12px;border:1px solid var(--glass-border);border-radius:12px;color:var(--text-sub);font-size:.82rem;';
                note.textContent = 'This old local image file is no longer available.';
                parent.appendChild(note);
            }
        }, true);
    }

    function patchLoadChat() {
        if (typeof window.loadChat !== 'function' || window.loadChat.__latestViewGuardPatched) return false;
        const original = window.loadChat;
        window.loadChat = function loadChatAndScrollLatest(id) {
            const result = original.apply(this, arguments);
            setTimeout(() => scrollToLatest('loadChat'), 0);
            setTimeout(() => scrollToLatest('loadChat-late'), 350);
            return result;
        };
        window.loadChat.__latestViewGuardPatched = true;
        return true;
    }

    function install() {
        installMutationObserver();
        installImageFallback();
        if (!patchLoadChat()) {
            const timer = setInterval(() => {
                installMutationObserver();
                if (patchLoadChat()) clearInterval(timer);
            }, 250);
            setTimeout(() => clearInterval(timer), 8000);
        }
        setTimeout(() => scrollToLatest('initial'), 900);
        setTimeout(() => scrollToLatest('delayed'), 2200);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
    else install();
})();
