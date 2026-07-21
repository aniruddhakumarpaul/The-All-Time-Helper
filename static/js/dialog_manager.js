// dialog_manager.js
// Central modal focus, background isolation, and focus restoration.
(function () {
    const MARKER = '__helperDialogManagerInstalled';
    if (window[MARKER]) return;
    window[MARKER] = true;

    const DIALOG_SELECTOR = '[data-helper-dialog]';
    const FOCUSABLE_SELECTOR = [
        'a[href]',
        'button:not([disabled])',
        'input:not([disabled]):not([type="hidden"])',
        'select:not([disabled])',
        'textarea:not([disabled])',
        '[tabindex]:not([tabindex="-1"])',
        '[contenteditable="true"]'
    ].join(',');

    const returnTargets = new WeakMap();
    const originalInert = new Map();
    let currentDialog = null;
    let scheduled = false;

    function isVisible(element) {
        if (!element || element.hidden) return false;
        const style = window.getComputedStyle(element);
        return style.display !== 'none' && style.visibility !== 'hidden';
    }

    function topVisibleDialog() {
        const visible = Array.from(document.querySelectorAll(DIALOG_SELECTOR)).filter(isVisible);
        return visible.reduce((top, candidate) => {
            if (!top) return candidate;
            const topZ = Number.parseInt(window.getComputedStyle(top).zIndex, 10) || 0;
            const candidateZ = Number.parseInt(window.getComputedStyle(candidate).zIndex, 10) || 0;
            return candidateZ >= topZ ? candidate : top;
        }, null);
    }

    function focusableElements(dialog) {
        return Array.from(dialog?.querySelectorAll(FOCUSABLE_SELECTOR) || []).filter(element => {
            if (element.closest('[aria-hidden="true"]')) return false;
            const style = window.getComputedStyle(element);
            return style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length > 0;
        });
    }

    function clearBackgroundIsolation() {
        for (const [element, wasInert] of originalInert.entries()) {
            element.inert = wasInert;
        }
        originalInert.clear();
    }

    function isolateBackground(dialog) {
        clearBackgroundIsolation();
        for (const child of Array.from(document.body.children)) {
            if (child.tagName === 'SCRIPT' || child.tagName === 'STYLE') continue;
            if (child.id === 'app-toast-region') continue;
            if (dialog.id === 'neural-context-card' && child.id === 'neural-scrim') continue;
            if (child === dialog || child.contains(dialog)) continue;
            originalInert.set(child, Boolean(child.inert));
            child.inert = true;
        }
    }

    function restoreFocus(dialog) {
        const target = returnTargets.get(dialog);
        returnTargets.delete(dialog);
        const fallback = document.getElementById('prompt');
        const destination = target?.isConnected && !target.inert && target.getClientRects().length > 0 ? target : fallback;
        if (!destination || !destination.isConnected || destination.inert || destination.getClientRects().length === 0) return;
        window.requestAnimationFrame(() => {
            if (destination.isConnected && !destination.inert) destination.focus({ preventScroll: true });
        });
    }

    function focusDialog(dialog) {
        if (!dialog || dialog.contains(document.activeElement)) return;
        const target = dialog.querySelector('[data-dialog-initial-focus]') || focusableElements(dialog)[0];
        window.requestAnimationFrame(() => {
            if (!isVisible(dialog)) return;
            if (target) target.focus({ preventScroll: true });
            else {
                dialog.tabIndex = -1;
                dialog.focus({ preventScroll: true });
            }
        });
    }

    function sync() {
        scheduled = false;
        const next = topVisibleDialog();
        document.querySelectorAll(DIALOG_SELECTOR).forEach(dialog => {
            const hidden = dialog !== next;
            if (dialog.getAttribute('aria-hidden') !== String(hidden)) {
                dialog.setAttribute('aria-hidden', String(hidden));
            }
        });

        if (next === currentDialog) {
            if (next) isolateBackground(next);
            return;
        }
        const previous = currentDialog;
        currentDialog = next;

        if (next) {
            returnTargets.set(next, document.activeElement);
            isolateBackground(next);
            document.body.classList.add('helper-dialog-open');
            focusDialog(next);
        } else {
            clearBackgroundIsolation();
            document.body.classList.remove('helper-dialog-open');
            if (previous) restoreFocus(previous);
        }
    }

    function scheduleSync() {
        if (scheduled) return;
        scheduled = true;
        window.requestAnimationFrame(sync);
    }

    function isTop(dialogOrId) {
        const dialog = typeof dialogOrId === 'string'
            ? document.getElementById(dialogOrId)
            : dialogOrId;
        return Boolean(dialog && dialog === topVisibleDialog());
    }

    document.addEventListener('keydown', event => {
        if (event.key !== 'Tab' || !currentDialog || !isVisible(currentDialog)) return;
        const focusable = focusableElements(currentDialog);
        if (!focusable.length) {
            event.preventDefault();
            currentDialog.focus();
            return;
        }

        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (!currentDialog.contains(document.activeElement)) {
            event.preventDefault();
            first.focus();
        } else if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }, true);

    function observe() {
        if (!document.body) return;
        new MutationObserver(scheduleSync).observe(document.body, {
            attributes: true,
            attributeFilter: ['class', 'style', 'hidden'],
            childList: true,
            subtree: true
        });
        scheduleSync();
    }

    window.HelperDialogs = {
        isTop,
        sync: scheduleSync,
        getActive: () => currentDialog
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', observe, { once: true });
    } else {
        observe();
    }
})();
