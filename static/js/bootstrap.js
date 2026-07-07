if (localStorage.getItem('helper_user_v2')) {
    document.documentElement.classList.add('is-authenticated');
}

function removeLegacyPromptThemeButton() {
    document.getElementById('theme-btn')?.remove();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', removeLegacyPromptThemeButton);
} else {
    removeLegacyPromptThemeButton();
}

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
        script.defer = true;
        script.dataset.helperExtension = marker;
        document.body.appendChild(script);
    }

    function inject() {
        removeLegacyPromptThemeButton();
        injectScript('busy_states', '1', 'busy-states');
        injectScript('email_approval', '2', 'draft-send');
        injectScript('admin_dashboard', '1', 'admin-dashboard');
        injectScript('job_center', '1', 'job-center');
        injectScript('chat_context_reuse', '1', 'chat-context-reuse');
        injectScript('motion_enhancements', '1', 'premium-motion');
        injectScript('composer_context_tray', '5', 'composer-context-tray');
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', inject);
    } else {
        inject();
    }
})();
