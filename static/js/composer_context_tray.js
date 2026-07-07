// composer_context_tray.js
// Drag chat text, images, and widgets into the prompt area as targeted context chips.
(function () {
    const EXTENSION_MARKER = '__composerContextTrayInstalled';
    const CONTEXT_MIME = 'application/x-helper-composer-context';
    const EMAIL_DRAFT_MIME = 'application/x-helper-email-draft';
    const MAX_ITEMS = 6;
    const MAX_ITEM_CHARS = 6000;
    const MAX_TOTAL_CHARS = 18000;
    const MAX_VISIBLE_CHARS = 180;

    if (window[EXTENSION_MARKER]) return;
    window[EXTENSION_MARKER] = true;

    let renderQueued = false;
    let renderingTray = false;
    let clearAfterSendQueued = false;
    let pendingSentContexts = [];

    function state() {
        return window.__helperState || null;
    }

    function activeChat() {
        const st = state();
        if (!st?.activeId || !Array.isArray(st.chats)) return null;
        return st.chats.find(chat => String(chat.id) === String(st.activeId)) || null;
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

    function compactText(value, size = MAX_VISIBLE_CHARS) {
        return clip(String(value || '').replace(/\s+/g, ' '), size);
    }

    function stripAttachmentPayload(item) {
        if (!item || typeof item !== 'object') return item;
        const next = { ...item };
        delete next.content;
        delete next.data;
        delete next.bytes;
        return next;
    }

    function compactDraftForPrompt(draft) {
        const raw = draft && typeof draft === 'object' ? draft : {};
        const attachmentFilename = raw.attachment_filename
            || (Array.isArray(raw.attachments) && (raw.attachments[0]?.filename || raw.attachments[0]?.name))
            || '';
        const compact = {
            recipient: String(raw.recipient || raw.to || '').trim(),
            subject: String(raw.subject || '').trim(),
            body: String(raw.body || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n'),
            tone: String(raw.tone || 'modern').trim() || 'modern',
            attachment_filename: String(attachmentFilename || '').trim(),
        };
        if (raw.attachment_type) compact.attachment_type = raw.attachment_type;
        if (raw.attachment_id || raw.id) compact.attachment_id = raw.attachment_id || raw.id;
        if (Array.isArray(raw.attachments)) {
            compact.attachments = raw.attachments.map(stripAttachmentPayload).filter(Boolean);
        }
        return compact;
    }

    function emailContextTextFromDraft(draft) {
        return `EMAIL_DRAFT_CONTEXT:${JSON.stringify(compactDraftForPrompt(draft))}`;
    }

    function normalizeContext(item) {
        if (!item || !item.text) return null;
        return {
            kind: item.kind || 'text',
            title: clip(item.title || labelForKind(item.kind), 80),
            subtitle: clip(item.subtitle || '', 140),
            text: clip(item.text || '', MAX_ITEM_CHARS),
            preview: item.preview || '',
            status: item.status || 'ready',
        };
    }

    function cloneContextItems(items) {
        return (Array.isArray(items) ? items : []).slice(0, MAX_ITEMS).map(normalizeContext).filter(Boolean);
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

    function sourceLabelForKind(kind) {
        if (kind === 'image') return 'Image';
        if (kind === 'email') return 'Email draft';
        if (kind === 'widget') return 'Widget';
        return 'Chat text';
    }

    function iconForKind(kind) {
        if (kind === 'image') return '▧';
        if (kind === 'email') return '✉';
        if (kind === 'widget') return '◈';
        return '¶';
    }

    function contextCardHtml(item, mode = 'composer') {
        const kind = item.kind || 'text';
        const title = item.title || labelForKind(kind);
        const subtitle = item.subtitle || compactText(item.text, mode === 'chat' ? 140 : 90);
        const source = sourceLabelForKind(kind);
        const thumb = kind === 'image' && item.preview
            ? `<img class="composer-context-thumb" src="${escapeHtml(item.preview)}" alt="">`
            : `<span class="composer-context-icon">${escapeHtml(iconForKind(kind))}</span>`;
        return `
            <div class="composer-context-media">
                ${thumb}
                <span class="composer-context-dot" aria-hidden="true"></span>
            </div>
            <div class="composer-context-meta">
                <em>${escapeHtml(source)}</em>
                <strong>${escapeHtml(title)}</strong>
                <span>${escapeHtml(subtitle)}</span>
            </div>
        `;
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

    function setTrayBusy(isBusy, className = 'is-loading') {
        const tray = ensureTray();
        if (!tray) return;
        tray.classList.toggle(className, Boolean(isBusy));
    }

    function pulseTrayLoading(className = 'is-loading', duration = 520) {
        setTrayBusy(true, className);
        window.clearTimeout(ensureTray()?._contextBusyTimer);
        const tray = ensureTray();
        if (!tray) return;
        tray._contextBusyTimer = window.setTimeout(() => setTrayBusy(false, className), duration);
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
                const status = item.status || 'ready';
                const chip = document.createElement('div');
                chip.className = `composer-context-chip composer-context-${kind} is-${status}`;
                chip.dataset.index = String(index);
                chip.innerHTML = `${contextCardHtml(item)}<button type="button" class="composer-context-remove" aria-label="Remove context">×</button><span class="composer-context-progress" aria-hidden="true"></span>`;
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
            tray.classList.remove('has-context', 'composer-drop-active', 'is-loading', 'is-attaching', 'is-sending');
            tray.innerHTML = '';
        }
        scheduleRender();
    }

    function markLastContextReady() {
        const items = contextItems();
        const last = items[items.length - 1];
        if (last && last.status === 'attaching') {
            last.status = 'ready';
            scheduleRender();
        }
    }

    function addContext(item) {
        const items = contextItems();
        const normalized = normalizeContext(item);
        if (!normalized || items.length >= MAX_ITEMS) return false;
        const remaining = Math.max(0, MAX_TOTAL_CHARS - totalChars(items));
        if (!remaining) return false;
        const text = normalized.kind === 'email'
            ? normalized.text
            : clip(normalized.text, Math.min(MAX_ITEM_CHARS, remaining));
        if (!text) return false;
        pulseTrayLoading('is-attaching', 700);
        items.push({
            kind: normalized.kind,
            title: normalized.title,
            subtitle: normalized.subtitle,
            text,
            preview: normalized.preview || '',
            status: 'attaching',
        });
        scheduleRender();
        window.setTimeout(markLastContextReady, 420);
        return true;
    }

    function attachPendingContextsToLatestUserMessage() {
        if (!pendingSentContexts.length) return false;
        const chat = activeChat();
        if (!chat || !Array.isArray(chat.ms) || !chat.ms.length) return false;
        for (let idx = chat.ms.length - 1; idx >= 0; idx -= 1) {
            const message = chat.ms[idx];
            if (message?.r !== 'u') continue;
            if (!Array.isArray(message.contexts) || !message.contexts.length) {
                message.contexts = cloneContextItems(pendingSentContexts).map(item => ({ ...item, status: 'ready' }));
            }
            renderChatContextWidgets();
            return true;
        }
        return false;
    }

    function scheduleClearAfterSend() {
        if (clearAfterSendQueued) return;
        pendingSentContexts = cloneContextItems(contextItems());
        if (!pendingSentContexts.length) return;
        setTrayBusy(true, 'is-sending');
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
            const attached = attachPendingContextsToLatestUserMessage();
            if ((attached && promptCleared && requestStarted) || attempts >= 120) {
                clearAfterSendQueued = false;
                clearContexts();
                pendingSentContexts = [];
                return;
            }
            attempts += 1;
            setTimeout(tick, 50);
        };
        setTimeout(tick, 0);
    }

    function emailDraftContextFromCard(card) {
        if (!card) return null;
        let draft = null;
        if (typeof window.collectEmailDraftForDrag === 'function') draft = window.collectEmailDraftForDrag(card);
        if (!draft) {
            try { draft = JSON.parse(card.dataset.emailDraft || '{}'); } catch (_) { draft = null; }
        }
        if (!draft || typeof draft !== 'object') return null;
        const compactDraft = compactDraftForPrompt(draft);
        const subject = String(compactDraft.subject || 'Email Draft').trim() || 'Email Draft';
        const attachment = compactDraft.attachment_filename || (Array.isArray(compactDraft.attachments) && compactDraft.attachments[0]?.filename) || '';
        return {
            kind: 'email',
            title: 'Email Draft',
            subtitle: attachment ? `${subject} • ${attachment}` : subject,
            text: emailContextTextFromDraft(compactDraft),
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
            subtitle: compactText(alt, 96),
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
            subtitle: compactText(text, 120),
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
            subtitle: compactText(text, 120),
            text: `[Target Widget]\n${text}`,
        };
    }

    function isInteractiveDraftControl(target) {
        return Boolean(target?.closest?.('.email-draft-card button, .email-draft-card input, .email-draft-card textarea, .email-draft-card select, .email-draft-card option, .email-draft-card label, .email-draft-card a, .email-draft-card [contenteditable="true"]'));
    }

    function contextFromDragTarget(target) {
        if (isInteractiveDraftControl(target)) return null;
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
            try { return normalizeContext(JSON.parse(rawContext)); } catch (_) { return null; }
        }
        const rawDraft = event.dataTransfer?.getData(EMAIL_DRAFT_MIME) || '';
        if (rawDraft) {
            try {
                const draft = JSON.parse(rawDraft);
                const compactDraft = compactDraftForPrompt(draft);
                const subject = compactDraft.subject || 'Email Draft';
                const attachment = compactDraft.attachment_filename || '';
                return { kind: 'email', title: 'Email Draft', subtitle: attachment ? `${subject} • ${attachment}` : subject, text: emailContextTextFromDraft(compactDraft) };
            } catch (_) { return null; }
        }
        const text = event.dataTransfer?.getData('text/plain') || '';
        if (!text.trim()) return null;
        return { kind: 'text', title: 'Dropped Text', subtitle: compactText(text, 120), text: `[Target Text]\n${text}` };
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
            ensureTray()?.classList.add('composer-drop-active', 'is-loading');
        }, true);
        document.addEventListener('dragleave', event => {
            if (!event.relatedTarget || !document.querySelector('#input-wrap')?.contains(event.relatedTarget)) {
                ensureTray()?.classList.remove('composer-drop-active', 'is-loading');
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
            window.setTimeout(() => ensureTray()?.classList.remove('is-loading'), 260);
            document.getElementById('prompt')?.focus();
        }, true);
    }

    function renderChatContextWidgets() {
        const chat = activeChat();
        const chatArea = document.getElementById('chat-area');
        if (!chat || !chatArea || !Array.isArray(chat.ms)) return;
        const messages = Array.from(chatArea.querySelectorAll('.msg'));
        messages.forEach((node, index) => {
            const message = chat.ms[index];
            const contexts = cloneContextItems(message?.contexts || []);
            const txt = node.querySelector('.txt');
            if (!txt || node.querySelector('.chat-context-strip')) return;
            if (!contexts.length || message?.r !== 'u') return;
            const strip = document.createElement('div');
            strip.className = 'chat-context-strip is-rendering';
            strip.setAttribute('aria-label', 'Context used for this prompt');
            strip.innerHTML = `<div class="chat-context-title">Targeted Context</div>`;
            for (const item of contexts) {
                const card = document.createElement('div');
                card.className = `chat-context-card composer-context-${item.kind || 'text'} is-ready`;
                card.innerHTML = contextCardHtml(item, 'chat');
                strip.appendChild(card);
            }
            txt.insertBefore(strip, txt.firstChild);
            window.setTimeout(() => strip.classList.remove('is-rendering'), 450);
        });
    }

    function installSourceObserver() {
        markDraggable(document);
        renderChatContextWidgets();
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
            if (changed) {
                scheduleRender();
                setTimeout(renderChatContextWidgets, 0);
            }
        });
        for (const root of observedRoots) {
            observer.observe(root, { childList: true, subtree: true });
        }
        document.getElementById('main-send-btn')?.addEventListener('click', scheduleClearAfterSend);
        document.getElementById('prompt')?.addEventListener('keydown', event => {
            if (event.key === 'Enter' && !event.shiftKey) scheduleClearAfterSend();
        });
        window.addEventListener('popstate', () => setTimeout(renderChatContextWidgets, 0));
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
        window.renderChatContextWidgets = renderChatContextWidgets;
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
