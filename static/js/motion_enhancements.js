// Small, idempotent DOM enhancer for motion and immediate control feedback.
(function () {
    const MARKER = '__premiumMotionInstalled';
    if (window[MARKER]) return;
    window[MARKER] = true;

    const PRESSABLE_SELECTOR = [
        'button',
        '[role="button"]',
        '#mobile-menu-btn',
        '#model-toggle',
        '.model-opt',
        '.menu-item',
        '.theme-opt',
        '.toggle',
        '.auth-btn-link',
        '.set-btn',
        '.new-chat',
        '.history-item',
        '.img-btn',
        '.persona-switch-item'
    ].join(',');
    const CUSTOM_KEYBOARD_SELECTOR = [
        '#mobile-menu-btn',
        '#model-toggle',
        '.model-opt',
        '.menu-item',
        '.theme-opt',
        '.toggle',
        '.auth-btn-link',
        '#open-settings-btn',
        '.img-btn'
    ].join(',');
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const releaseTimers = new WeakMap();
    let activePointerPress = null;
    let activeKeyboardPress = null;

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
            prompt.style.height = 'auto';
            const next = `${Math.min(Math.max(prompt.scrollHeight, 36), 180)}px`;
            // Restore the measured height even when it matches the previous value.
            // Leaving the inline height at auto collapses the flex composer back to one row.
            prompt.style.height = next;
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

    function syncControlState(control) {
        if (!control) return;
        if (control.id === 'model-toggle') {
            control.setAttribute('aria-controls', 'model-menu');
            control.setAttribute('aria-haspopup', 'listbox');
            control.setAttribute('aria-expanded', String(document.getElementById('model-menu')?.classList.contains('active')));
        }
        if (control.id === 'mobile-menu-btn') {
            const isOpen = Boolean(document.getElementById('sidebar')?.classList.contains('open'));
            control.setAttribute('aria-controls', 'sidebar');
            control.setAttribute('aria-expanded', String(isOpen));
            control.setAttribute('aria-label', isOpen ? 'Close navigation' : 'Open navigation');
        }
        if (control.id === 'open-settings-btn') control.setAttribute('aria-haspopup', 'dialog');
        if (control.classList.contains('toggle')) {
            control.setAttribute('aria-checked', String(control.classList.contains('on')));
        }
        if (control.classList.contains('model-opt')) {
            const selected = localStorage.getItem('helper_model_v3') || 'helper-auto';
            control.setAttribute('aria-selected', String(control.dataset.modelId === selected));
        }
        if (control.classList.contains('theme-opt')) {
            control.setAttribute('aria-pressed', String(control.classList.contains('active')));
        }
    }

    function hydrateControl(control) {
        if (!control || control.dataset.uiFeedback === 'true') {
            if (control) syncControlState(control);
            return;
        }
        control.dataset.uiFeedback = 'true';

        if (control.matches('button')) control.dataset.uiKeyboard = 'native';
        if (control.matches(CUSTOM_KEYBOARD_SELECTOR)) {
            if (!control.matches('button, input, select, textarea, a[href]')) {
                const role = control.classList.contains('toggle')
                    ? 'switch'
                    : (control.classList.contains('model-opt') ? 'option' : 'button');
                control.setAttribute('role', role);
                if (!control.hasAttribute('tabindex')) control.tabIndex = 0;
                control.dataset.uiKeyboard = 'custom';
            }
        }
        syncControlState(control);
    }

    function hydrateControls(root = document) {
        if (root.nodeType === 1 && root.matches?.(PRESSABLE_SELECTOR)) hydrateControl(root);
        root.querySelectorAll?.(PRESSABLE_SELECTOR).forEach(hydrateControl);
        const modelMenu = document.getElementById('model-menu');
        if (modelMenu) modelMenu.setAttribute('role', 'listbox');
    }

    function hydrateMotion(root = document) {
        hydrateIndexedChildren(root, '.msg');
        hydrateIndexedChildren(root, '#context-results > *');
        hydrateIndexedChildren(root, '#pal-results > *');
        hydrateControls(root);
    }

    function findPressable(target) {
        if (!target?.closest) return null;
        const control = target.closest(PRESSABLE_SELECTOR);
        if (!control || control.matches(':disabled, [aria-disabled="true"]')) return null;
        hydrateControl(control);
        return control;
    }

    function setPressVector(control, clientX, clientY, pointerType) {
        const rect = control.getBoundingClientRect();
        const halfWidth = Math.max(rect.width / 2, 1);
        const halfHeight = Math.max(rect.height / 2, 1);
        const strength = pointerType === 'mouse' ? 1.2 : 0.5;
        const offsetX = Math.max(-1, Math.min(1, (clientX - rect.left - halfWidth) / halfWidth)) * strength;
        const offsetY = Math.max(-1, Math.min(1, (clientY - rect.top - halfHeight) / halfHeight)) * strength;
        const compact = Math.min(rect.width, rect.height) <= 56;
        control.style.setProperty('--ui-press-x', `${offsetX.toFixed(2)}px`);
        control.style.setProperty('--ui-press-y', `${offsetY.toFixed(2)}px`);
        control.style.setProperty('--ui-press-scale', compact ? '0.92' : '0.98');
    }

    function beginPress(control, clientX, clientY, pointerType) {
        if (!control || reducedMotion.matches) return;
        const releaseTimer = releaseTimers.get(control);
        if (releaseTimer) window.clearTimeout(releaseTimer);
        control.classList.remove('is-ui-releasing');
        setPressVector(control, clientX, clientY, pointerType);
        control.classList.add('is-ui-pressing');
    }

    function endPress(control, immediate = false) {
        if (!control) return;
        const cleanup = () => {
            control.classList.remove('is-ui-pressing', 'is-ui-releasing');
            control.style.removeProperty('--ui-press-x');
            control.style.removeProperty('--ui-press-y');
            control.style.removeProperty('--ui-press-scale');
            releaseTimers.delete(control);
        };
        const releaseTimer = releaseTimers.get(control);
        if (releaseTimer) window.clearTimeout(releaseTimer);
        if (immediate || reducedMotion.matches || !control.isConnected) {
            cleanup();
            return;
        }
        control.classList.add('is-ui-releasing');
        void control.offsetWidth;
        control.classList.remove('is-ui-pressing');
        releaseTimers.set(control, window.setTimeout(cleanup, 190));
    }

    function bindControlFeedback() {
        document.addEventListener('pointerdown', event => {
            if (!event.isPrimary || event.button !== 0) return;
            const control = findPressable(event.target);
            if (!control) return;
            if (activePointerPress?.control && activePointerPress.control !== control) {
                endPress(activePointerPress.control, true);
            }
            beginPress(control, event.clientX, event.clientY, event.pointerType);
            activePointerPress = {
                control,
                pointerId: event.pointerId,
                startX: event.clientX,
                startY: event.clientY
            };
        }, true);

        document.addEventListener('pointermove', event => {
            const press = activePointerPress;
            if (!press || press.pointerId !== event.pointerId) return;
            if (Math.hypot(event.clientX - press.startX, event.clientY - press.startY) <= 8) return;
            endPress(press.control, true);
            activePointerPress = null;
        }, { capture: true, passive: true });

        document.addEventListener('pointerup', event => {
            if (!activePointerPress || activePointerPress.pointerId !== event.pointerId) return;
            endPress(activePointerPress.control);
            activePointerPress = null;
        }, true);

        document.addEventListener('pointercancel', event => {
            if (!activePointerPress || activePointerPress.pointerId !== event.pointerId) return;
            endPress(activePointerPress.control, true);
            activePointerPress = null;
        }, true);

        document.addEventListener('keydown', event => {
            if (event.repeat || (event.key !== 'Enter' && event.key !== ' ')) return;
            const control = event.target?.closest?.('[data-ui-keyboard]');
            if (!control || control.matches(':disabled, [aria-disabled="true"]')) return;
            const rect = control.getBoundingClientRect();
            beginPress(control, rect.left + rect.width / 2, rect.top + rect.height / 2, 'keyboard');
            activeKeyboardPress = {
                control,
                key: event.key,
                custom: control.dataset.uiKeyboard === 'custom'
            };
            if (activeKeyboardPress.custom) event.preventDefault();
        }, true);

        document.addEventListener('keyup', event => {
            const press = activeKeyboardPress;
            if (!press || press.key !== event.key) return;
            endPress(press.control);
            activeKeyboardPress = null;
            if (press.custom) {
                event.preventDefault();
                press.control.click();
            }
        }, true);

        document.addEventListener('click', event => {
            const control = findPressable(event.target);
            if (!control) return;
            raf(() => {
                syncControlState(control);
                if (control.classList.contains('model-opt')) {
                    document.querySelectorAll('.model-opt').forEach(syncControlState);
                    syncControlState(document.getElementById('model-toggle'));
                }
            });
        }, true);

        window.addEventListener('blur', () => {
            if (activePointerPress) endPress(activePointerPress.control, true);
            if (activeKeyboardPress) endPress(activeKeyboardPress.control, true);
            activePointerPress = null;
            activeKeyboardPress = null;
        });
    }

    function installSignoutFireLifecycle() {
        const signout = document.getElementById('signout-btn');
        if (!signout || signout.dataset.signoutFireLifecycle === 'true') return;
        signout.dataset.signoutFireLifecycle = 'true';
        let settleTimer = null;

        const clearSettleTimer = () => {
            if (settleTimer !== null) window.clearTimeout(settleTimer);
            settleTimer = null;
        };
        const stopAfterSettle = () => {
            clearSettleTimer();
            settleTimer = window.setTimeout(() => {
                signout.classList.remove('signout-fire-active');
                settleTimer = null;
            }, 560);
        };

        signout.addEventListener('mouseenter', () => {
            clearSettleTimer();
            if (!reducedMotion.matches) signout.classList.add('signout-fire-active');
        });
        signout.addEventListener('mouseleave', stopAfterSettle);
        reducedMotion.addEventListener?.('change', event => {
            if (event.matches) {
                clearSettleTimer();
                signout.classList.remove('signout-fire-active');
            }
        });
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

    function observeInteractiveControls() {
        const body = document.body;
        if (!body || body.dataset.interactionObserver === 'true') return;
        body.dataset.interactionObserver = 'true';
        new MutationObserver(records => {
            raf(() => {
                for (const record of records) {
                    for (const node of record.addedNodes) {
                        if (node.nodeType === 1) hydrateControls(node);
                    }
                }
            });
        }).observe(body, { childList: true, subtree: true });
    }

    function observeDisclosureState() {
        [
            ['sidebar', 'mobile-menu-btn'],
            ['model-menu', 'model-toggle']
        ].forEach(([sourceId, controlId]) => {
            const source = document.getElementById(sourceId);
            const control = document.getElementById(controlId);
            if (!source || !control || source.dataset.uiStateObserved === 'true') return;
            source.dataset.uiStateObserved = 'true';
            new MutationObserver(() => syncControlState(control)).observe(source, {
                attributes: true,
                attributeFilter: ['class']
            });
        });
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
        observeInteractiveControls();
        observeDisclosureState();
        bindControlFeedback();
        installSignoutFireLifecycle();
        installContextScanHook();
        window.hydratePremiumMotion = hydrateMotion;
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
