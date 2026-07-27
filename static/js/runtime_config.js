(function configureRuntimeUi() {
    const value = document.querySelector('meta[name="helper-outside-click-dismiss"]')?.content;
    const outsideClickDismiss = String(value || 'true').toLowerCase() !== 'false';

    window.HelperRuntimeConfig = Object.freeze({ outsideClickDismiss });
    window.helperOutsideClickDismissEnabled = () => outsideClickDismiss;
})();
