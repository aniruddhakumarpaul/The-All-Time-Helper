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
        injectScript('email_approval', '1', 'draft-send');
        injectScript('admin_dashboard', '1', 'admin-dashboard');
        injectScript('job_center', '1', 'job-center');
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', inject);
    } else {
        inject();
    }
})();
