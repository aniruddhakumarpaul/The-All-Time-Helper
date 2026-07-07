// motion_enhancements.js
// Small, idempotent DOM enhancer for premium motion hooks.
(function () {
    const MARKER = '__premiumMotionInstalled';
    if (window[MARKER]) return;
    window[MARKER] = true;

    function raf(fn) {
        window.requestAnimationFrame ? window.requestAnimationFrame(fn) : window.setTimeout(fn, 0);
    }

    function hydratePrompt() {
        const prompt = document.getElementById('prompt');
        if (!prompt) return;
        let scheduled = false;
        function update() {
            scheduled = false;
            const hasText = Boolean(String(prompt.value || '').trim());
            document.body.classList.toggle('prompt-has-text', hasText);
            const previous = prompt.style.height;
            prompt.style.height = 'auto';
            const next = `${Math.min(Math.max(prompt.scrollHeight, 36), 180)}px`;
            if (previous !== next) prompt.style.height = next;
        }
        function schedule() {
            if (scheduled) return;
            scheduled = true;
            raf(update);
        }
        prompt.addEventListener('input', schedule, { passive: true });
        prompt.addEventListener('focus', schedule, { passive: true });
        schedule();
    }

    function hydrateIndexedChildren(root, selector) {
        root.querySelectorAll?.(selector).forEach((node, index) => {
            if (node.dataset.motionHydrated === 'true') return;
            node.dataset.motionHydrated = 'true';
            node.style.setProperty('--motion-index', String(Math.min(index, 8)));
        });
    }

    function hydrateMotion(root = document) {
        hydrateIndexedChildren(root, '.msg');
        hydrateIndexedChildren(root, '#context-results > *');
        hydrateIndexedChildren(root, '#pal-results > *');
    }

    function observeRoot(root, selector) {
        if (!root || root.dataset.motionObserver === 'true') return;
        root.dataset.motionObserver = 'true';
        new MutationObserver(records => {
            raf(() => {
                for (const record of records) {
                    for (const node of record.addedNodes) {
                        if (node.nodeType === 1) hydrateMotion(node);
                    }
                }
                hydrateIndexedChildren(root, selector);
            });
        }).observe(root, { childList: true, subtree: true });
    }

    function observeMotionAreas() {
        observeRoot(document.getElementById('chat-area'), '.msg');
        observeRoot(document.getElementById('context-results'), ':scope > *');
        observeRoot(document.getElementById('pal-results'), ':scope > *');
    }

    function installContextScanHook() {
        const card = document.getElementById('neural-context-card');
        const trigger = document.getElementById('neural-scrim');
        if (!card || card.dataset.motionScanHooked === 'true') return;
        card.dataset.motionScanHooked = 'true';
        const scan = () => {
            card.classList.remove('motion-scan');
            void card.offsetWidth;
            card.classList.add('motion-scan');
            window.setTimeout(() => card.classList.remove('motion-scan'), 1300);
        };
        card.addEventListener('transitionend', scan, { passive: true });
        trigger?.addEventListener('transitionend', scan, { passive: true });
    }

    function init() {
        document.body.classList.add('motion-ready');
        hydratePrompt();
        hydrateMotion(document);
        observeMotionAreas();
        installContextScanHook();
        window.hydratePremiumMotion = hydrateMotion;
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
