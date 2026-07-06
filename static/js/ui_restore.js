// ui_restore.js
// Disabled: chat restore is now owned by static/js/app.js.
// This file remains as a compatibility no-op because older cached pages may still import it.
(function () {
    window.__restoreVisibleChats = function restoreVisibleChatsNoop() {
        console.info('[UIRestore] disabled; app.js owns chat restore.');
    };
})();
