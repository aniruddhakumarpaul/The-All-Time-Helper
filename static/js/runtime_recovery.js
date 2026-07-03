// runtime_recovery.js
// Defensive recovery layer for the modular frontend. It restores basic UI control
// wiring when the ES module entrypoint partially fails or a browser caches stale
// handlers after refactors. This file intentionally delegates to window-level
// app functions when they exist and implements only safe UI fallbacks directly.
(function () {
    const READY_TIMEOUT_MS = 7000;
    const POLL_MS = 100;

    function $(id) {
        return document.getElementById(id);
    }

    function once(el, eventName, key, handler) {
        if (!el || el.dataset[key] === '1') return;
        el.dataset[key] = '1';
        el.addEventListener(eventName, handler);
    }

    function call(name, ...args) {
        const fn = window[name];
        if (typeof fn === 'function') {
            return fn(...args);
        }
        console.warn(`[RuntimeRecovery] ${name} is not ready yet.`);
        return undefined;
    }

    function toggleDisplay(el, display = 'flex') {
        if (!el) return;
        el.style.display = el.style.display === display ? 'none' : display;
    }

    function fallbackOpenSettings() {
        const modal = $('settings-modal');
        if (modal) modal.style.display = 'flex';
        try { localStorage.setItem('helper_active_modal_v2', 'settings'); } catch (_) {}
    }

    function fallbackCloseSettings() {
        const modal = $('settings-modal');
        if (modal) modal.style.display = 'none';
        try { localStorage.removeItem('helper_active_modal_v2'); } catch (_) {}
    }

    function fallbackToggleSidebar() {
        const sidebar = $('sidebar');
        const isOpen = sidebar?.classList.toggle('open');
        document.body.classList.toggle('sidebar-open', Boolean(isOpen));
    }

    function fallbackToggleModelMenu() {
        $('model-menu')?.classList.toggle('active');
    }

    function fallbackSelectModel(id, name) {
        const active = $('active-model-name');
        if (active && name) active.innerText = name;
        $('model-menu')?.classList.remove('active');
        window.__selectedModelFallback = id;
    }

    function fallbackTheme(choice) {
        try { localStorage.setItem('helper_theme_pref', choice); } catch (_) {}
        const isDark = choice === 'dark' || (choice === 'system' && window.matchMedia?.('(prefers-color-scheme: dark)').matches);
        document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light');
        document.body.setAttribute('data-theme', isDark ? 'dark' : 'light');
        const label = $('current-theme-icon-settings');
        if (label) label.innerText = choice === 'light' ? '☀️ Light' : choice === 'dark' ? '🌙 Dark' : '🌓 System';
        document.querySelectorAll('.dropdown-menu').forEach(menu => { menu.style.display = 'none'; });
        const onboarding = $('theme-modal');
        if (onboarding?.style.display === 'flex') onboarding.style.display = 'none';
    }

    function bindCoreControls() {
        once($('login-btn'), 'click', 'rrLogin', () => call('handleAuth', 'login'));
        once($('signup-btn'), 'click', 'rrSignup', () => call('handleAuth', 'signup'));
        once($('verify-btn'), 'click', 'rrVerify', () => call('handleAuth', 'verify'));

        document.querySelectorAll('[data-auth-view]').forEach(el => {
            once(el, 'click', 'rrAuthView', () => call('switchAuth', el.dataset.authView));
        });

        once($('mobile-menu-btn'), 'click', 'rrSidebar', () => call('toggleSidebar') || fallbackToggleSidebar());
        once($('sidebar-scrim'), 'click', 'rrSidebarScrim', () => call('toggleSidebar') || fallbackToggleSidebar());
        once($('new-chat-btn'), 'click', 'rrNewChat', () => call('startNewChat'));
        once($('open-settings-btn'), 'click', 'rrSettings', () => call('openSettings') || fallbackOpenSettings());
        once($('signout-btn'), 'click', 'rrSignout', () => call('signOut'));

        once($('model-toggle'), 'click', 'rrModelToggle', () => call('toggleDropdown') || fallbackToggleModelMenu());
        document.querySelectorAll('[data-model-id]').forEach(el => {
            once(el, 'click', 'rrModelOpt', () => call('selModel', el.dataset.modelId, el.dataset.modelName) || fallbackSelectModel(el.dataset.modelId, el.dataset.modelName));
        });

        once($('main-send-btn'), 'click', 'rrSend', () => call('send'));
        once($('stop-btn'), 'click', 'rrStop', () => call('stopAI'));
        once($('export-chat-btn'), 'click', 'rrExport', () => call('exportChat'));
        once($('hist-search'), 'input', 'rrHistSearch', event => call('filterHist', event.currentTarget.value));

        once($('cancel-delete-btn'), 'click', 'rrCancelDelete', () => call('closeDeleteConfirm'));
        once($('confirm-del-btn'), 'click', 'rrConfirmDelete', () => {
            const fn = window.__deleteSelectedChat || window.deleteSelectedChat;
            if (typeof fn === 'function') fn();
        });

        once($('theme-btn-settings'), 'click', 'rrThemeMenu', event => {
            if (typeof window.toggleThemeMenu === 'function') window.toggleThemeMenu(event, 'theme-menu-settings');
            else toggleDisplay($('theme-menu-settings'), 'flex');
        });
        document.querySelectorAll('[data-theme-choice]').forEach(el => {
            once(el, 'click', 'rrThemeChoice', () => {
                if (typeof window.applyThemeChoice === 'function') window.applyThemeChoice(el.dataset.themeChoice);
                else fallbackTheme(el.dataset.themeChoice);
            });
        });

        once($('close-neural-btn'), 'click', 'rrCloseNeural', () => call('closeNeuralContext'));
        once($('neural-scrim'), 'click', 'rrNeuralScrim', () => call('closeNeuralContext'));

        const imageModal = $('image-modal');
        once(imageModal, 'click', 'rrImageClose', event => {
            if (event.target === imageModal || event.target.classList?.contains('lightbox-close')) call('closeImageModal');
        });

        const prompt = $('prompt');
        once(prompt, 'keydown', 'rrPromptKey', event => {
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                call('send');
            }
        });
    }

    function exposeDiagnostic() {
        window.helperRuntimeRecoveryStatus = function () {
            return {
                sendReady: typeof window.send === 'function',
                authReady: typeof window.handleAuth === 'function',
                loadedAt: window.__runtimeRecoveryLoadedAt,
                errors: window.__helperFrontendErrors || []
            };
        };
    }

    window.__runtimeRecoveryLoadedAt = new Date().toISOString();
    window.__helperFrontendErrors = window.__helperFrontendErrors || [];
    window.addEventListener('error', event => {
        window.__helperFrontendErrors.push({ message: event.message, source: event.filename, line: event.lineno, col: event.colno });
    });
    window.addEventListener('unhandledrejection', event => {
        window.__helperFrontendErrors.push({ message: String(event.reason && event.reason.message || event.reason || 'Unhandled rejection') });
    });

    document.addEventListener('DOMContentLoaded', () => {
        exposeDiagnostic();
        bindCoreControls();
        const started = Date.now();
        const timer = setInterval(() => {
            bindCoreControls();
            if ((typeof window.send === 'function' && typeof window.handleAuth === 'function') || Date.now() - started > READY_TIMEOUT_MS) {
                clearInterval(timer);
                if (Date.now() - started > READY_TIMEOUT_MS && typeof window.send !== 'function') {
                    console.warn('[RuntimeRecovery] app.js did not expose send() within timeout. Check console for module errors.');
                }
            }
        }, POLL_MS);
    });
})();
