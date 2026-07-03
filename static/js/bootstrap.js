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
