if (localStorage.getItem('helper_user_v2')) {
    document.documentElement.classList.add('is-authenticated');
}

(function loadRuntimeRecovery() {
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

    document.addEventListener('DOMContentLoaded', function () {
        var script = document.createElement('script');
        script.src = '/static/js/runtime_recovery.js?v=1';
        script.defer = true;
        document.body.appendChild(script);
    });
})();
