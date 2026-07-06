// image_retry_guard.js
// Prevent stale/generated image cards from creating long retry storms when providers return 429/415.
(function () {
    const MAX_GUARDED_RETRIES = 3;

    function finishImageFailure(img, loadEl, message) {
        if (loadEl) {
            loadEl.textContent = '';
            const node = document.createElement('div');
            node.style.padding = '16px';
            node.style.color = 'var(--text-sub)';
            node.textContent = message;
            loadEl.appendChild(node);
            loadEl.style.display = 'block';
        }
        if (img) {
            img.dataset.loaded = 'true';
            img.style.display = 'none';
        }
    }

    function installGuard() {
        if (typeof window.handleImgError !== 'function') return false;
        if (window.handleImgError.__imageRetryGuardInstalled) return true;

        const original = window.handleImgError;
        function guardedHandleImgError(img, safeHref, uniqueId) {
            const source = String(safeHref || img?.dataset?.retryUrl || img?.src || '');
            const isPollinations = /pollinations\.ai/i.test(source);
            if (isPollinations && img) {
                const count = Number(img.dataset.guardRetryCount || '0') + 1;
                img.dataset.guardRetryCount = String(count);
                if (count > MAX_GUARDED_RETRIES) {
                    const loadEl = uniqueId ? document.getElementById('load-' + uniqueId) : null;
                    finishImageFailure(img, loadEl, 'Image provider is rate-limited or returned an unsupported response. Try regenerating later.');
                    return;
                }
            }
            return original(img, safeHref, uniqueId);
        }
        guardedHandleImgError.__imageRetryGuardInstalled = true;
        window.handleImgError = guardedHandleImgError;
        return true;
    }

    if (!installGuard()) {
        const timer = setInterval(() => {
            if (installGuard()) clearInterval(timer);
        }, 250);
        setTimeout(() => clearInterval(timer), 10000);
    }
})();
