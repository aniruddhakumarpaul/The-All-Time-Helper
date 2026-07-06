if (localStorage.getItem('helper_user_v2')) {
    document.documentElement.classList.add('is-authenticated');
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

(function loadSupplementalFrontendExtension() {
    function inject() {
        if (document.querySelector('script[data-helper-extension="draft-send"]')) return;
        var script = document.createElement('script');
        script.src = '/static/js/' + 'email_' + 'approval.js?v=1';
        script.defer = true;
        script.dataset.helperExtension = 'draft-send';
        document.body.appendChild(script);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', inject);
    } else {
        inject();
    }
})();
