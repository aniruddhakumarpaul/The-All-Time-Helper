// composer_context_tray.js
// Drag chat text, images, and widgets into the prompt area as targeted context chips.
(function () {
    const EXTENSION_MARKER = '__composerContextTrayInstalled';
    const CONTEXT_MIME = 'application/x-helper-composer-context';
    const EMAIL_DRAFT_MIME = 'application/x-helper-email-draft';
    const MAX_ITEMS = 6;
    const MAX_ITEM_CHARS = 6000;
    const MAX_TOTAL_CHARS = 18000;

    if (window[EXTENSION_MARKER]) return;
    window[EXTENSION_MARKER] = true;

    let renderQueued = false;
    let renderingTray = false;
    let clearAfterSendQueued = false;

    function state() {
        return window.__helperState || null;
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function clip(value, size) {
        return String(value || '').trim().slice(0, size);
    }

    function totalChars(items) {
        return items.reduce((total, item) => total + String(item.text || '').length, 0);
    }

    function labelForKind(kind) {
        if (kind === 'image') return 'Image Target';
        if (kind === 'email') return 'Email Widget';
        if (kind === 'widget') return 'Widget Target';
        return 'Text Target';
    }

    function ensureTray() {
        const container = document.querySelector('.pill-bar-container');
        if (!container) return null;
        let tray = document.getElementById('composer-context-tray');
        if (tray) return tray;
        tray = document.createElement('div');
        tray.id = 'composer-context-tray';
        tray.setAttribute('aria-label', 'Targeted prompt context');
        container.insertBefore(tray, container.firstChild);
        return tray;
    }

    function contextItems() {
        const st = state();
        if (!st) return [];
        if (!Array.isArray(st.attachedContexts)) st.attachedContexts = [];
        return st.attachedContexts;
    }

    function renderTray() {
        if (renderingTray) return;
        const tray = ensureTray();
        if (!tray) return;
        const items = contextItems();
        renderingTray = true;
        try {
            tray.innerHTML = '';
            tray.classList.toggle('has-context', items.length > 0);
            if (!items.length) return;
            for (const [index, item] of items.entries()) {
                const kind = item.kind || 'text';
                const chip = document.createElement('div');
                chip.className = `composer-context-chip composer-context-${kind}`;
                chip.dataset.index = String(index);
                const title = item.title || labelForKind(kind);
                const subtitle = item.subtitle || clip(item.text, 90).replace(/\s+/g, ' ');
                const icon = kind === 'image' ? '▧' : kind === 'email' ? '✉' : kind === 'widget' ? '◈' : '¶';
                const thumb = kind === 'image' && item.preview
                    ? `<img class="composer-context-thumb" src="${escapeHtml(item.preview)}" alt="">`
                    : `<span class="composer-context-icon">${escapeHtml(icon)}</span>`;
                chip.innerHTML = `
                    ${thumb}
                    <div class="composer-context-meta">
                        <strong>${escapeHtml(title)}</strong>
                        <span>${escapeHtml(subtitle)}</span>
                    </div>
                    <button type="button" class="composer-context-remove" aria-label="Remove context">×</button>
                `;
                chip.querySelector('.composer-context-remove')?.addEventListener('click', () => {
                    contextItems().splice(index, 1);
                    scheduleRender();
                });
                tray.appendChild(chip);
            }
        } finally {
            renderingTray = false;
        }
    }

    function scheduleRender() {
        if (renderQueued) return;
        renderQueued = true;
        requestAnimationFrame(() => {
            renderQueued = false;
            renderTray();
        });
    }

    function clearContexts() {
        const st = state();
        if (st && Array.isArray(st.attachedContexts)) st.attachedContexts = [];
        const tray = ensureTray();
        if (tray) {
            tray.classList.remove('has-context', 'composer-drop-active');
            tray.innerHTML = '';
        }
        scheduleRender();
    }

    function scheduleClearAfterSend() {
        if (clearAfterSendQueued) return;
        clearAfterSendQueued = true;
        let attempts = 0;
        const tick = () => {
            const prompt = document.getElementById('prompt');
            const stopBtn = document.getElementById('stop-btn');
            const sendBtn = document.getElementById('main-send-btn');
            const promptCleared = !prompt || !String(prompt.value || '').trim();
            const requestStarted = Boolean(state()?.abortController)
                || stopBtn?.style.display === 'flex'
                || sendBtn?.style.display === 'none';
            if ((promptCleared && requestStarted) || attempts >= 120) {
                clearAfterSendQueued = false;
                clearContexts();
                return;
            }
            attempts += 1;
            setTimeout(tick, 50);
        };
        setTimeout(tick, 0);
    }

    function addContext(item) {
        const items = contextItems();
        if (!item || !item.text || items.length >= MAX_ITEMS) return false;
        const remaining = Math.max(0, MAX_TOTAL_CHARS - totalChars(items));
        if (!remaining) return false;
        const text = clip(item.text, Math.min(MAX_ITEM_CHARS, remaining));
        if (!text) return false;
        items.push({
            kind: item.kind || 'text',
            title: clip(item.title || labelForKind(item.kind), 80),
            subtitle: clip(item.subtitle || '', 140),
            text,
            preview: item.preview || '',
        });
        scheduleRender();
        return true;
    }

    function emailDraftContextFromCard(card) {
        if (!card) return null;
        let draft = null;
        if (typeof window.collectEmailDraftForDrag === 'function') draft = window.collectEmailDraftForDrag(card);
        if (!draft) {
            try { draft = JSON.parse(card.dataset.emailDraft || '{}'); } catch (_) { draft = null; }
        }
        if (!draft || typeof draft !== 'object') return null;
        const subject = String(draft.subject || 'Email Draft').trim() || 'Email Draft';
        const attachment = draft.attachment_filename || (Array.isArray(draft.attachments) && draft.attachments[0]?.filename) || '';
        return {
            kind: 'email',
            title: 'Email Draft',
            subtitle: attachment ? `${subject} • ${attachment}` : subject,
            text: `EMAIL_DRAFT_CONTEXT:${JSON.stringify(draft)}`,
        };
    }

    function imageContextFromElement(img) {
        if (!img) return null;
        const src = img.currentSrc || img.src || img.getAttribute('src') || '';
        if (!src) return null;
        const alt = img.getAttribute('alt') || img.closest('.msg')?.querySelector('[id^="msg-text-"]')?.innerText || 'chat image';
        return {
            kind: 'image',
            title: 'Image Target',
            subtitle: clip(alt.replace(/\s+/g, ' '), 96),
            preview: src,
            text: `[Target Image]\nUse this image as explicit context for the next request.\nImage source: ${src}\nImage description/context: ${alt}`,
        };
    }

    function textContextFromElement(el) {
        if (!el) return null;
        const textNode = el.querySelector('[id^="msg-text-"]') || el;
        let text = textNode.innerText || textNode.textContent || '';
        if (typeof window.stripInternalEmailDraftMarkers === 'function') text = window.stripInternalEmailDraftMarkers(text);
        text = clip(text.replace(/\n{3,}/g, '\n\n'), MAX_ITEM_CHARS);
        if (!text) return null;
        const role = el.closest('.u-msg') ? 'User Text' : el.closest('.b-msg') ? 'Assistant Text' : 'Text Target';
        return {
            kind: 'text',
            title: role,
            subtitle: clip(text.replace(/\s+/g, ' '), 120),
            text: `[Target Text]\n${text}`,
        };
    }

    function widgetContextFromElement(el) {
        const emailCard = el?.closest?.('.email-draft-card');
        if (emailCard) return emailDraftContextFromCard(emailCard);
        const widget = el?.closest?.('.neural-insight-box, .context-snippet, .ops-item, .job-item');
        if (!widget) return null;
        const text = clip(widget.innerText || widget.textContent || '', MAX_ITEM_CHARS);
        if (!text) return null;
        return {
            kind: 'widget',
            title: 'Widget Target',
            subtitle: clip(text.replace(/\s+/g, ' '), 120),
            text: `[Target Widget]\n${text}`,
        };
    }

    function contextFromDragTarget(target) {
        const widget = widgetContextFromElement(target);
        if (widget) return widget;
        const img = target?.closest?.('img.chat-rendered-img, img.chat-img-preview, .chat-img-preview-container img, .upscale-container img');
        if (img) return imageContextFromElement(img);
        const textBubble = target?.closest?.('.msg .txt');
        if (textBubble) return textContextFromElement(textBubble);
        return null;
    }

    function markDraggable(root = document) {
        root.querySelectorAll?.('.msg .txt, img.chat-rendered-img, img.chat-img-preview, .chat-img-preview-container img, .email-draft-card').forEach(el => {
            el.setAttribute('draggable', 'true');
            el.classList.add('composer-draggable-context');
        });
    }

    function installDragSource() {
        document.addEventListener('dragstart', event => {
            const context = contextFromDragTarget(event.target);
            if (!context || !event.dataTransfer) return;
            event.stopImmediatePropagation();
            event.dataTransfer.setData(CONTEXT_MIME, JSON.stringify(context));
            event.dataTransfer.setData('text/plain', context.text);
            event.dataTransfer.effectAllowed = 'copy';
            document.body.classList.add('composer-context-dragging');
        }, true);
        document.addEventListener('dragend', () => {
            document.body.classList.remove('composer-context-dragging');
            document.querySelectorAll('.composer-drop-active').forEach(el => el.classList.remove('composer-drop-active'));
        }, true);
    }

    function parseDrop(event) {
        const rawContext = event.dataTransfer?.getData(CONTEXT_MIME) || '';
        if (rawContext) {
            try { return JSON.parse(rawContext); } catch (_) { return null; }
        }
        const rawDraft = event.dataTransfer?.getData(EMAIL_DRAFT_MIME) || '';
        if (rawDraft) {
            try {
                const draft = JSON.parse(rawDraft);
                return { kind: 'email', title: 'Email Draft', subtitle: draft.subject || 'Email Draft', text: `EMAIL_DRAFT_CONTEXT:${JSON.stringify(draft)}` };
            } catch (_) { return null; }
        }
        const text = event.dataTransfer?.getData('text/plain') || '';
        if (!text.trim()) return null;
        return { kind: 'text', title: 'Dropped Text', subtitle: clip(text.replace(/\s+/g, ' '), 120), text: `[Target Text]\n${text}` };
    }

    function installDropTarget() {
        const dropSelectors = ['#prompt', '.pill-bar', '.pill-bar-container', '#input-wrap'];
        function targetFromEvent(event) {
            return dropSelectors.map(sel => event.target.closest?.(sel)).find(Boolean);
        }
        document.addEventListener('dragover', event => {
            const target = targetFromEvent(event);
            if (!target) return;
            event.preventDefault();
            if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
            ensureTray()?.classList.add('composer-drop-active');
        }, true);
        document.addEventListener('dragleave', event => {
            if (!event.relatedTarget || !document.querySelector('#input-wrap')?.contains(event.relatedTarget)) {
                ensureTray()?.classList.remove('composer-drop-active');
            }
        }, true);
        document.addEventListener('drop', event => {
            const target = targetFromEvent(event);
            if (!target) return;
            const context = parseDrop(event);
            if (!context) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            addContext(context);
            ensureTray()?.classList.remove('composer-drop-active');
            document.getElementById('prompt')?.focus();
        }, true);
    }

    function installSourceObserver() {
        markDraggable(document);
        const observedRoots = [
            document.getElementById('chat-area'),
            document.getElementById('context-results'),
            document.getElementById('settings-modal'),
            document.getElementById('admin-ops-modal'),
            document.getElementById('job-center-modal'),
        ].filter(Boolean);
        const observer = new MutationObserver(records => {
            if (renderingTray) return;
            let changed = false;
            for (const record of records) {
                if (record.target?.closest?.('#composer-context-tray')) continue;
                for (const node of record.addedNodes) {
                    if (node.nodeType !== 1) continue;
                    if (node.closest?.('#composer-context-tray')) continue;
                    markDraggable(node);
                    changed = true;
                }
            }
            if (changed) scheduleRender();
        });
        for (const root of observedRoots) {
            observer.observe(root, { childList: true, subtree: true });
        }
        document.getElementById('main-send-btn')?.addEventListener('click', scheduleClearAfterSend);
        document.getElementById('prompt')?.addEventListener('keydown', event => {
            if (event.key === 'Enter' && !event.shiftKey) scheduleClearAfterSend();
        });
    }

    function init() {
        ensureTray();
        renderTray();
        installDragSource();
        installDropTarget();
        installSourceObserver();
        window.addComposerContext = addContext;
        window.clearComposerContextTray = clearContexts;
        window.renderComposerContextTray = scheduleRender;
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
