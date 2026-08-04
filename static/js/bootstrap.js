if (localStorage.getItem('helper_user_v2')) {
    document.documentElement.classList.add('is-authenticated');
}

(function configureBackendBaseUrl() {
    const configured = String(document.documentElement.dataset.apiBase || '').trim();
    const fallback = window.location.origin || 'http://localhost:9000';
    let base;
    try {
        base = new URL(configured || fallback, fallback);
    } catch (_) {
        base = new URL(fallback);
    }
    window.__helperApiBaseUrl = base.toString().replace(/\/$/, '');
    window.helperApiUrl = function helperApiUrl(path) {
        const normalized = '/' + String(path || '').replace(/^\/+/, '');
        return new URL(normalized, window.__helperApiBaseUrl + '/').toString();
    };
})();

window.handleHelperUnauthorized = function handleHelperUnauthorized(response) {
    if (Number(response?.status) !== 401) return false;
    if (window.__helperUnauthorizedHandled) return true;
    window.__helperUnauthorizedHandled = true;
    localStorage.removeItem('helper_user_v2');
    localStorage.removeItem('helper_token_v2');
    localStorage.removeItem('helper_active_chat_v2');
    localStorage.removeItem('helper_active_modal_v2');
    document.documentElement.classList.remove('is-authenticated');
    window.setTimeout(() => window.location.reload(), 0);
    return true;
};
window.__helperFrontendErrors = window.__helperFrontendErrors || [];
window.addEventListener('error', function (event) {
    window.__helperFrontendErrors.push({
        message: event.message,
        source: event.filename,
        line: event.lineno,
        col: event.colno
    });
});
window.addEventListener('unhandledrejection', function (event) {
    window.__helperFrontendErrors.push({
        message: String((event.reason && event.reason.message) || event.reason || 'Unhandled rejection')
    });
});

(function loadSupplementalFrontendExtensions() {
    function injectScript(name, version, marker) {
        if (document.querySelector(`script[data-helper-extension="${marker}"]`)) return;
        var script = document.createElement('script');
        script.src = '/static/js/' + name + '.js?v=' + version;
        script.async = false;
        script.dataset.helperExtension = marker;
        document.body.appendChild(script);
    }

    function inject() {
        injectScript('dialog_manager', '1', 'dialog-manager');
        injectScript('busy_states', '1', 'busy-states');
        injectScript('email_draft_contract', '2', 'email-draft-contract');
        injectScript('email_draft', '6', 'email-draft-core');
        injectScript('email_approval', '4', 'draft-send');
        injectScript('admin_dashboard', '4', 'admin-dashboard');
        injectScript('job_center', '4', 'job-center');
        injectScript('chat_context_reuse', '1', 'chat-context-reuse');
        injectScript('motion_enhancements', '3', 'premium-motion');
        injectScript('composer_context_tray', '9', 'composer-context-tray');
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', inject);
    } else {
        inject();
    }
})();
