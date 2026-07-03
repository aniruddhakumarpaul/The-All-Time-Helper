// runtime_recovery.js
// Defensive compatibility layer for the modular frontend. It reconnects visible UI
// controls by stable IDs, data attributes, and legacy class selectors. It delegates
// to the real app.js window bridge whenever available and uses UI-only fallbacks
// for menus/modals/theme so the interface does not feel disconnected.
(function () {
    const READY_TIMEOUT_MS = 7000;
    const POLL_MS = 100;
    const bound = new WeakMap();

    function $(id) { return document.getElementById(id); }
    function all(selector) { return Array.from(document.querySelectorAll(selector)); }
    function has(name) { return typeof window[name] === 'function'; }

    function once(target, eventName, key, handler) {
        if (!target) return;
        let keys = bound.get(target);
        if (!keys) {
            keys = new Set();
            bound.set(target, keys);
        }
        const bindKey = `${eventName}:${key}`;
        if (keys.has(bindKey)) return;
        keys.add(bindKey);
        target.addEventListener(eventName, handler);
    }

    function call(name, ...args) {
        const fn = window[name];
        if (typeof fn === 'function') return fn(...args);
        console.warn(`[RuntimeRecovery] ${name} is not ready yet.`);
        return undefined;
    }

    function callOr(name, fallback, ...args) {
        if (has(name)) return call(name, ...args);
        return fallback(...args);
    }

    function show(el, display = 'flex') { if (el) el.style.display = display; }
    function hide(el) { if (el) el.style.display = 'none'; }
    function toggleDisplay(el, display = 'flex') { if (el) el.style.display = el.style.display === display ? 'none' : display; }

    function fallbackSwitchAuth(view) {
        ['login', 'signup', 'otp'].forEach(name => {
            const form = $(`${name}-form`);
            if (form) form.style.display = name === view ? 'block' : 'none';
        });
        $(view === 'signup' ? 's-name' : view === 'otp' ? 'v-otp' : 'l-email')?.focus();
    }

    function fallbackOpenSettings() { show($('settings-modal')); try { localStorage.setItem('helper_active_modal_v2', 'settings'); } catch (_) {} }
    function fallbackCloseSettings() { hide($('settings-modal')); try { localStorage.removeItem('helper_active_modal_v2'); } catch (_) {} }
    function fallbackToggleSidebar() { const sidebar = $('sidebar'); const isOpen = sidebar?.classList.toggle('open'); document.body.classList.toggle('sidebar-open', Boolean(isOpen)); }
    function fallbackToggleModelMenu() { $('model-menu')?.classList.toggle('active'); }
    function fallbackSelectModel(id, name) { const active = $('active-model-name'); if (active && name) active.innerText = name; $('model-menu')?.classList.remove('active'); window.__selectedModelFallback = id; }
    function fallbackCloseNeural() { $('neural-context-card')?.classList.remove('active'); $('neural-scrim')?.classList.remove('active'); }
    function fallbackCloseImageModal() { const modal = $('image-modal'); $('modal-img')?.classList.remove('is-zoomed'); if (modal) { modal.classList.remove('active'); setTimeout(() => hide(modal), 150); } }

    function fallbackTheme(choice) {
        try { localStorage.setItem('helper_theme_pref', choice); } catch (_) {}
        const systemDark = window.matchMedia?.('(prefers-color-scheme: dark)').matches;
        const theme = choice === 'system' ? (systemDark ? 'dark' : 'light') : choice;
        document.documentElement.setAttribute('data-theme', theme);
        document.body.setAttribute('data-theme', theme);
        const label = $('current-theme-icon-settings');
        if (label) label.innerText = choice === 'light' ? '☀️ Light' : choice === 'dark' ? '🌙 Dark' : '🌓 System';
        all('.dropdown-menu').forEach(menu => hide(menu));
        hide($('theme-modal'));
    }

    function readModelIdFromText(text) {
        const clean = text.toLowerCase();
        if (clean.includes('flash')) return 'gemini-1.5-flash-latest';
        if (clean.includes('antigravity pro')) return 'gemini-1.5-pro-latest';
        if (clean.includes('agentic')) return 'agentic-pro';
        if (clean.includes('gemma 4')) return 'gemma4:e2b';
        if (clean.includes('gemma 2')) return 'gemma2:2b';
        if (clean.includes('mistral')) return 'dolphin-mistral';
        if (clean.includes('llama') || clean.includes('sensitive')) return 'helper';
        if (clean.includes('phi')) return 'phi3';
        if (clean.includes('moondream')) return 'moondream';
        return '';
    }

    function bindAuthControls() {
        once($('login-btn'), 'click', 'login', () => call('handleAuth', 'login'));
        once($('signup-btn'), 'click', 'signup', () => call('handleAuth', 'signup'));
        once($('verify-btn'), 'click', 'verify', () => call('handleAuth', 'verify'));
        once($('l-email'), 'keydown', 'loginEmailKey', e => { if (e.key === 'Enter') $('l-pwd')?.focus(); });
        once($('l-pwd'), 'keydown', 'loginPwdKey', e => { if (e.key === 'Enter') call('handleAuth', 'login'); });
        once($('s-name'), 'keydown', 'signupNameKey', e => { if (e.key === 'Enter') $('s-email')?.focus(); });
        once($('s-email'), 'keydown', 'signupEmailKey', e => { if (e.key === 'Enter') $('s-pwd')?.focus(); });
        once($('s-pwd'), 'keydown', 'signupPwdKey', e => { if (e.key === 'Enter') call('handleAuth', 'signup'); });
        once($('v-otp'), 'input', 'otpInput', e => { e.currentTarget.value = e.currentTarget.value.replace(/[^0-9]/g, '').slice(0, 6); });
        once($('v-otp'), 'keydown', 'otpKey', e => { if (e.key === 'Enter') call('handleAuth', 'verify'); });
        all('[data-auth-view], .auth-btn-link').forEach(el => once(el, 'click', 'authView', () => {
            const view = el.dataset.authView || (/already|sign\s*in/i.test(el.textContent || '') ? 'login' : 'signup');
            if (has('switchAuth')) call('switchAuth', view); else fallbackSwitchAuth(view);
        }));
    }

    function bindNavigationControls() {
        once($('mobile-menu-btn'), 'click', 'sidebar', () => callOr('toggleSidebar', fallbackToggleSidebar));
        once($('sidebar-scrim'), 'click', 'sidebarScrim', () => callOr('toggleSidebar', fallbackToggleSidebar));
        all('#new-chat-btn, .new-chat').forEach(el => once(el, 'click', 'newChat', () => call('startNewChat')));
        all('#open-settings-btn, .set-btn').forEach(el => once(el, 'click', 'settings', () => callOr('openSettings', fallbackOpenSettings)));
        once($('signout-btn'), 'click', 'signout', () => call('signOut'));
        once($('hist-search'), 'input', 'histSearch', event => call('filterHist', event.currentTarget.value));
    }

    function bindModelAndComposerControls() {
        once($('model-toggle'), 'click', 'modelToggle', () => callOr('toggleDropdown', fallbackToggleModelMenu));
        all('[data-model-id], .model-opt').forEach(el => once(el, 'click', 'modelOpt', () => {
            const id = el.dataset.modelId || readModelIdFromText(el.textContent || '');
            const name = el.dataset.modelName || (el.textContent || '').trim();
            if (!id) return;
            if (has('selModel')) call('selModel', id, name); else fallbackSelectModel(id, name);
        }));
        once($('img-in'), 'change', 'imgChange', event => { if (has('previewImg')) call('previewImg', event.currentTarget); });
        once($('main-send-btn'), 'click', 'send', () => call('send'));
        once($('stop-btn'), 'click', 'stop', () => call('stopAI'));
        once($('export-chat-btn'), 'click', 'export', () => call('exportChat'));
        const prompt = $('prompt');
        once(prompt, 'input', 'promptInput', event => { if (has('autoRes')) window.autoRes(event.currentTarget); $('main-send-btn')?.classList.toggle('pulsing', event.currentTarget.value.trim().length > 0); });
        once(prompt, 'keydown', 'promptKey', event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); call('send'); } });
    }

    function bindSettingsAndModals() {
        once($('theme-btn-settings'), 'click', 'themeMenu', event => { if (has('toggleThemeMenu')) window.toggleThemeMenu(event, 'theme-menu-settings'); else toggleDisplay($('theme-menu-settings'), 'flex'); });
        all('[data-theme-choice], .theme-opt, #theme-menu-settings .menu-item').forEach(el => once(el, 'click', 'themeChoice', () => {
            const choice = el.dataset.themeChoice || (/light/i.test(el.textContent || '') ? 'light' : /dark/i.test(el.textContent || '') ? 'dark' : 'system');
            if (has('applyThemeChoice')) window.applyThemeChoice(choice); else fallbackTheme(choice);
        }));
        all('[data-toggle-setting], .set-row .toggle').forEach(el => once(el, 'click', 'toggleSetting', () => call('toggleSet', el.id)));
        const personaItem = document.querySelector('.persona-switch-item');
        once(personaItem, 'click', 'personaItem', event => { if (event.target.closest('.switch')) return; const toggle = $('persona-toggle'); if (!toggle) return; toggle.checked = !toggle.checked; toggle.dispatchEvent(new Event('change')); });
        once($('cancel-delete-btn'), 'click', 'cancelDelete', () => call('closeDeleteConfirm'));
        once($('confirm-del-btn'), 'click', 'confirmDelete', () => { const fn = window.__deleteSelectedChat || window.deleteSelectedChat; if (typeof fn === 'function') fn(); });
        once($('close-neural-btn'), 'click', 'closeNeural', () => callOr('closeNeuralContext', fallbackCloseNeural));
        once($('neural-scrim'), 'click', 'neuralScrim', () => callOr('closeNeuralContext', fallbackCloseNeural));
        const settingsModal = $('settings-modal');
        once(settingsModal, 'click', 'settingsBackdrop', event => { if (event.target === settingsModal) callOr('closeSettings', fallbackCloseSettings); });
        const imageModal = $('image-modal');
        once(imageModal, 'click', 'imageClose', event => { if (event.target === imageModal || event.target.classList?.contains('lightbox-close')) callOr('closeImageModal', fallbackCloseImageModal); });
        once($('modal-img'), 'click', 'modalImgZoom', event => { event.stopPropagation(); event.currentTarget.classList.toggle('is-zoomed'); });
    }

    function bindDelegatedControls() {
        once(document, 'click', 'delegatedClick', event => {
            const preview = event.target.closest?.('[data-preview-src], .chat-img-preview-container');
            if (preview) {
                const src = preview.dataset.previewSrc || preview.querySelector('img')?.src;
                if (src) callOr('openImageModal', () => { show($('image-modal')); const img = $('modal-img'); if (img) img.src = src; }, src);
                return;
            }
            const edit = event.target.closest?.('[data-edit-index]');
            if (edit) { call('startEditPrompt', Number(edit.dataset.editIndex), edit); return; }
            const palette = $('cmd-palette');
            if (palette && event.target === palette && has('closePalette')) window.closePalette();
        });
        once(document, 'keydown', 'globalEscape', event => {
            if (event.key !== 'Escape') return;
            if ($('image-modal')?.classList.contains('active')) return callOr('closeImageModal', fallbackCloseImageModal);
            if ($('settings-modal')?.style.display === 'flex') return callOr('closeSettings', fallbackCloseSettings);
            if ($('delete-confirm-modal')?.style.display === 'flex') return hide($('delete-confirm-modal'));
            if ($('sidebar')?.classList.contains('open')) return callOr('toggleSidebar', fallbackToggleSidebar);
        });
        once(window, 'popstate', 'popState', () => {
            if ($('image-modal')?.classList.contains('active')) return callOr('closeImageModal', fallbackCloseImageModal);
            if ($('settings-modal')?.style.display === 'flex') return callOr('closeSettings', fallbackCloseSettings);
            if ($('sidebar')?.classList.contains('open')) return callOr('toggleSidebar', fallbackToggleSidebar);
        });
    }

    function bindAllControls() {
        bindAuthControls();
        bindNavigationControls();
        bindModelAndComposerControls();
        bindSettingsAndModals();
        bindDelegatedControls();
    }

    function exposeDiagnostic() {
        window.helperRuntimeRecoveryStatus = function () {
            return {
                sendReady: has('send'),
                authReady: has('handleAuth'),
                modelReady: has('selModel'),
                settingsReady: has('openSettings'),
                loadedAt: window.__runtimeRecoveryLoadedAt,
                appBridgeReady: Boolean(window.__helperAppBridgeReady),
                errors: window.__helperFrontendErrors || []
            };
        };
    }

    window.__runtimeRecoveryLoadedAt = new Date().toISOString();
    window.__helperFrontendErrors = window.__helperFrontendErrors || [];
    window.addEventListener('error', event => { window.__helperFrontendErrors.push({ message: event.message, source: event.filename, line: event.lineno, col: event.colno }); });
    window.addEventListener('unhandledrejection', event => { window.__helperFrontendErrors.push({ message: String(event.reason && event.reason.message || event.reason || 'Unhandled rejection') }); });

    document.addEventListener('DOMContentLoaded', () => {
        exposeDiagnostic();
        bindAllControls();
        const started = Date.now();
        const timer = setInterval(() => {
            bindAllControls();
            if ((has('send') && has('handleAuth')) || Date.now() - started > READY_TIMEOUT_MS) {
                clearInterval(timer);
                if (Date.now() - started > READY_TIMEOUT_MS && !has('send')) console.warn('[RuntimeRecovery] app.js did not expose send() within timeout. Check console for module errors.');
            }
        }, POLL_MS);
    });
})();
