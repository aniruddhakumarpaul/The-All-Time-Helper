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
    window.__helperComposerDragOwner = true;

    let renderQueued = false;
    let renderingTray = false;
    let clearAfterSendQueued = false;
    let pendingSentContexts = [];
    let contextTrayExpanded = true;

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

    function decodeAttachmentName(name) {
        const value = String(name || '').trim();
        if (!value) return '';
        try { return decodeURIComponent(value); } catch (_) { return value; }
    }

    function attachmentDescriptionFromName(name) {
        const decoded = decodeAttachmentName(name)
            .replace(/\.(png|jpe?g|webp|gif|bmp|txt|md|pdf)$/i, '')
            .replace(/[_-]+/g, ' ')
            .replace(/\s+/g, ' ')
            .trim();
        return decoded && decoded.toLowerCase() !== 'attachment' ? decoded : '';
    }

    function stripAttachmentPayload(item) {
        if (!item || typeof item !== 'object') return item;
        const next = { ...item };
        if (next.filename && !next.name) next.name = next.filename;
        if (next.name && !next.filename) next.filename = next.name;
        delete next.content;
        delete next.data;
        delete next.bytes;
        delete next.attachment_content;
        delete next.url;
        return next;
    }

    function compactDraftForPrompt(draft) {
        if (typeof window.compactEmailDraftForPrompt === 'function') {
            const compact = window.compactEmailDraftForPrompt(draft);
            if (compact) return compact;
        }
        const raw = draft && typeof draft === 'object' ? draft : {};
        const rawAttachments = Array.isArray(raw.attachments) ? raw.attachments : [];
        const attachmentFilename = raw.attachment_filename
            || (rawAttachments.length && (rawAttachments[0]?.filename || rawAttachments[0]?.name))
            || '';
        const hasAttachmentContent = Boolean(raw.attachment_content || raw.has_attachment_content)
            || rawAttachments.some(item => item?.content || item?.data || item?.attachment_content);
        const compact = {
            recipient: String(raw.recipient || raw.to || '').trim(),
            subject: String(raw.subject || '').trim(),
            body: String(raw.body || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n'),
            tone: String(raw.tone || 'modern').trim() || 'modern',
            attachment_filename: String(attachmentFilename || '').trim(),
        };
        if (raw.attachment_type) compact.attachment_type = raw.attachment_type;
        if (raw.attachment_id || raw.id) compact.attachment_id = raw.attachment_id || raw.id;
        if (raw.attachment_description) compact.attachment_description = String(raw.attachment_description).trim();
        else if (attachmentFilename) compact.attachment_description = attachmentDescriptionFromName(attachmentFilename);
        if (hasAttachmentContent) compact.has_attachment_content = true;
        if (rawAttachments.length) {
            compact.attachments = rawAttachments.map(stripAttachmentPayload).filter(Boolean);
        }
        return compact;
    }

    function emailContextTextFromDraft(draft) {
        return `EMAIL_DRAFT_CONTEXT:${JSON.stringify(compactDraftForPrompt(draft))}`;
    }

    function normalizeContext(item) {
        if (!item || !item.text) return null;
        const kind = ['text', 'image', 'document', 'email', 'widget'].includes(item.kind) ? item.kind : 'text';
        const text = clip(item.text, MAX_ITEM_CHARS);
        if (!text) return null;
        const rawRef = item.attachmentRef || item.attachment_ref;
        const attachmentRef = rawRef && typeof rawRef === 'object' && /^[a-f0-9]{32}$/i.test(String(rawRef.id || ''))
            ? {
                id: String(rawRef.id).toLowerCase(),
                name: clip(rawRef.name || '', 160),
                type: clip(rawRef.type || '', 100),
                size: Number.isInteger(Number(rawRef.size)) && Number(rawRef.size) >= 0 ? Number(rawRef.size) : undefined,
            }
            : undefined;
        const preview = typeof item.preview === 'string' && !/^data:|^blob:/i.test(item.preview) ? item.preview : '';
        return {
            kind,
            title: clip(item.title || labelForKind(kind), 80),
            subtitle: clip(item.subtitle || '', 140),
            text,
            preview,
            sourceId: clip(item.sourceId || '', 120),
            fingerprint: clip(item.fingerprint || fingerprintFor({ kind, text, sourceId: item.sourceId }), 120),
            status: ['ready', 'attaching', 'sending', 'rendering'].includes(item.status) ? item.status : 'ready',
            ...(attachmentRef ? { attachmentRef } : {}),
        };
    }

    function fingerprintFor(item) {
        const source = (item.kind || 'text') + '\u0000' + (item.sourceId || '') + '\u0000' + (item.text || '');
        let hash = 2166136261;
        for (let index = 0; index < source.length; index += 1) {
            hash ^= source.charCodeAt(index);
            hash = Math.imul(hash, 16777619);
        }
        return (item.kind || 'text') + ':' + (hash >>> 0).toString(16) + ':' + source.length;
    }

    function cloneContextItems(items) {
        return (Array.isArray(items) ? items : []).slice(0, MAX_ITEMS).map(normalizeContext).filter(Boolean);
    }

    function totalChars(items) {
        return items.reduce((total, item) => total + String(item.text || '').length, 0);
    }

    function labelForKind(kind) {
        if (kind === 'image') return 'Image Target';
        if (kind === 'document') return 'Document Target';
        if (kind === 'email') return 'Email Widget';
        if (kind === 'widget') return 'Widget Target';
        return 'Text Target';
    }

    function sourceLabelForKind(kind) {
        if (kind === 'image') return 'Image';
        if (kind === 'document') return 'Document';
        if (kind === 'email') return 'Email draft';
        if (kind === 'widget') return 'Widget';
        return 'Chat text';
    }

    function iconSvgForKind(kind) {
        const paths = {
            image: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"></rect><circle cx="8.5" cy="9" r="1.5"></circle><path d="m5 17 4.5-4 3 3 2-2 4.5 4"></path></svg>',
            document: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h8l4 4v14H6z"></path><path d="M14 3v5h5M8 16h8M8 12h5"></path></svg>',
            email: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="3"></rect><path d="m4 7 8 6 8-6"></path></svg>',
            widget: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="4"></rect><path d="M8 9h8M8 13h5M8 17h8"></path></svg>',
            text: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5h14M12 5v14M8 19h8"></path></svg>'
        };
        return paths[kind] || paths.text;
    }

    function contextStatusLabel(status) {
        if (status === 'attaching') return 'Adding';
        if (status === 'sending') return 'Sending';
        if (status === 'rendering') return 'Rendering';
        return 'Ready';
    }
    function contextCardHtml(item, mode = 'composer') {
        const kind = item.kind || 'text';
        const title = item.title || labelForKind(kind);
        const subtitle = item.subtitle || compactText(item.text, mode === 'chat' ? 140 : 90);
        const source = sourceLabelForKind(kind);
        const thumb = kind === 'image' && item.preview
            ? '<img class="composer-context-thumb" src="' + escapeHtml(item.preview) + '" alt="">'
            : '<span class="composer-context-icon">' + iconSvgForKind(kind) + '</span>';
        return '<div class="composer-context-media">' + thumb
            + '<span class="composer-context-state">' + escapeHtml(contextStatusLabel(item.status || 'ready')) + '</span></div>'
            + '<div class="composer-context-meta"><em>' + escapeHtml(source) + '</em><strong>'
            + escapeHtml(title) + '</strong><span>' + escapeHtml(subtitle) + '</span></div>';
    }
    function ensureTray() {
        const container = document.querySelector('.pill-bar-container');
        if (!container) return null;
        let tray = document.getElementById('composer-context-tray');
        if (tray) return tray;
        tray = document.createElement('div');
        tray.id = 'composer-context-tray';
        tray.setAttribute('aria-label', 'Targeted prompt context');
        tray.setAttribute('role', 'list');
        container.insertBefore(tray, container.firstChild);
        return tray;
    }

    function contextItems() {
        const st = state();
        if (!st) return [];
        if (!Array.isArray(st.attachedContexts)) st.set('attachedContexts', []);
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
            tray.textContent = '';
            tray.classList.toggle('has-context', items.length > 0);
            tray.classList.toggle('is-collapsed', items.length > 2 && !contextTrayExpanded);
            if (!items.length) return;

            const header = document.createElement('div');
            header.className = 'composer-context-header';
            const title = document.createElement('span');
            title.className = 'composer-context-summary';
            title.setAttribute('aria-live', 'polite');
            title.textContent = items.length + (items.length === 1 ? ' item attached' : ' items attached');
            header.appendChild(title);
            if (items.length > 2) {
                const toggle = document.createElement('button');
                toggle.type = 'button';
                toggle.className = 'composer-context-expand';
                toggle.setAttribute('aria-expanded', String(contextTrayExpanded));
                toggle.textContent = contextTrayExpanded ? 'Collapse' : 'View';
                toggle.addEventListener('click', () => {
                    contextTrayExpanded = !contextTrayExpanded;
                    scheduleRender();
                });
                header.appendChild(toggle);
            }
            tray.appendChild(header);

            const list = document.createElement('div');
            list.className = 'composer-context-items';
            list.setAttribute('role', 'list');
            for (const [index, item] of items.entries()) {
                const kind = item.kind || 'text';
                const status = item.status || 'ready';
                const chip = document.createElement('div');
                chip.className = 'composer-context-chip composer-context-' + kind + ' is-' + status + (kind === 'email' ? ' email-draft-context-chip' : '');
                chip.dataset.index = String(index);
                chip.dataset.fingerprint = item.fingerprint || fingerprintFor(item);
                chip.setAttribute('role', 'listitem');
                chip.innerHTML = contextCardHtml(item) + '<button type="button" class="composer-context-remove" aria-label="Remove ' + escapeHtml(item.title || 'context') + '"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17"></path></svg></button><span class="composer-context-progress" aria-hidden="true"></span>';
                chip.querySelector('.composer-context-remove')?.addEventListener('click', () => {
                    const st = state();
                    st?.set('attachedContexts', contextItems().filter((_, itemIndex) => itemIndex !== index));
                    scheduleRender();
                });
                list.appendChild(chip);
            }
            tray.appendChild(list);
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
        if (st) st.set('attachedContexts', []);
        const tray = ensureTray();
        if (tray) {
            tray.classList.remove('has-context', 'composer-drop-active', 'is-loading', 'is-attaching', 'is-sending');
            tray.innerHTML = '';
        }
        scheduleRender();
    }

    function markContextReady(target) {
        const items = contextItems();
        const item = items.find(candidate => candidate === target);
        if (item && item.status === 'attaching') {
            item.status = 'ready';
            state()?.touch('attachedContexts');
            scheduleRender();
        }
    }

    function pulseExistingContext(index) {
        const tray = ensureTray();
        const chip = tray?.querySelector('[data-index="' + index + '"]');
        if (!chip) return;
        chip.classList.remove('is-pulsing');
        void chip.offsetWidth;
        chip.classList.add('is-pulsing');
        window.setTimeout(() => chip.classList.remove('is-pulsing'), 560);
    }

    function addContext(item) {
        const st = state();
        const items = contextItems();
        const normalized = normalizeContext(item);
        if (!st || !normalized) return false;
        const existingBySource = normalized.sourceId
            ? items.findIndex(candidate => candidate.kind === normalized.kind && candidate.sourceId === normalized.sourceId)
            : -1;
        const existingByFingerprint = items.findIndex(candidate => (candidate.fingerprint || fingerprintFor(candidate)) === normalized.fingerprint);
        const targetIndex = existingBySource >= 0 ? existingBySource : existingByFingerprint;
        if (targetIndex >= 0) {
            const current = items[targetIndex];
            if (current.text === normalized.text && current.subtitle === normalized.subtitle) {
                pulseExistingContext(targetIndex);
                return true;
            }
            const otherTotal = totalChars(items) - String(current.text || '').length;
            const allowed = Math.min(MAX_ITEM_CHARS, Math.max(0, MAX_TOTAL_CHARS - otherTotal));
            const replacementText = clip(normalized.text, allowed);
            if (!replacementText) {
                window.notify?.('This context would exceed the prompt context limit.', 'error');
                return false;
            }
            const updated = {
                ...current,
                ...normalized,
                text: replacementText,
                status: 'attaching',
                fingerprint: fingerprintFor({ ...normalized, text: replacementText }),
            };
            items.splice(targetIndex, 1, updated);
            st.touch('attachedContexts');
            pulseTrayLoading('is-attaching', 520);
            scheduleRender();
            window.setTimeout(() => markContextReady(updated), 420);
            return true;
        }
        if (items.length >= MAX_ITEMS) return false;
        const remaining = Math.max(0, MAX_TOTAL_CHARS - totalChars(items));
        const text = clip(normalized.text, Math.min(MAX_ITEM_CHARS, remaining));
        if (!text) {
            window.notify?.('This context would exceed the prompt context limit.', 'error');
            return false;
        }
        const stored = { ...normalized, text, fingerprint: fingerprintFor({ ...normalized, text }), status: 'attaching' };
        pulseTrayLoading('is-attaching', 700);
        items.push(stored);
        st.touch('attachedContexts');
        scheduleRender();
        window.setTimeout(() => markContextReady(stored), 420);
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
            state()?.touch('chats');
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
        const attachment = decodeAttachmentName(compactDraft.attachment_filename || (Array.isArray(compactDraft.attachments) && compactDraft.attachments[0]?.filename) || '');
        return {
            kind: 'email',
            sourceId: String(card.dataset.emailDraftRef || ''),
            title: 'Email Draft',
            subtitle: attachment ? subject + ' / ' + attachment : subject,
            text: emailContextTextFromDraft(compactDraft),
        };
    }

    function imageContextFromElement(img) {
        if (!img) return null;
        const metadata = window.__helperImageMetadata?.(img) || {};
        const rawSource = metadata.sourceUrl || img.dataset?.modalUrl || img.currentSrc || img.src || img.getAttribute('src') || '';
        if (!rawSource) return null;
        const alt = img.getAttribute('alt') || img.closest('.msg')?.querySelector('[id^=msg-text-]')?.innerText || 'chat image';
        let safeSource = '';
        try {
            const parsed = new URL(rawSource, window.location.href);
            if (parsed.protocol === 'http:' || parsed.protocol === 'https:') safeSource = parsed.href.slice(0, 2000);
        } catch (_) {}
        const sourceId = String(img.dataset?.imageContextId || safeSource || img.id || '').slice(0, 120);
        return {
            kind: 'image',
            sourceId,
            title: 'Image Target',
            subtitle: compactText(alt, 96),
            preview: safeSource,
            text: safeSource
                ? '[Target Image]\nUse this image as explicit context for the next request.\nImage source: ' + safeSource + '\nImage description/context: ' + alt
                : '[Target Image]\nUse the selected image as explicit context for the next request.\nImage description/context: ' + alt,
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
        const reusable = el?.closest?.('.chat-context-reusable[data-context-json]');
        if (reusable) {
            try { return normalizeContext(JSON.parse(reusable.dataset.contextJson)); } catch (_) { return null; }
        }
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
            text: '[Target Widget]\n' + text,
        };
    }

    function isInteractiveDraftControl(target) {
        return Boolean(target?.closest?.('.email-draft-card button, .email-draft-card input, .email-draft-card textarea, .email-draft-card select, .email-draft-card option, .email-draft-card label, .email-draft-card a, .email-draft-card [contenteditable="true"]'));
    }

    const IMAGE_SELECTOR = [
        'img.chat-rendered-img',
        'img.chat-img-preview',
        '.chat-img-preview-container img',
        '.upscale-container img',
        '[data-image-result] img',
        '.generated-image-result img',
        '.image-search-result img'
    ].join(', ');

    function classifyDragSource(target) {
        const explicitHandle = target?.closest?.('[data-context-drag-handle]');
        if (explicitHandle) {
            const emailCard = explicitHandle.closest('.email-draft-card');
            if (emailCard) return { type: 'email-handle', element: explicitHandle, context: emailDraftContextFromCard(emailCard) };
            const reusable = explicitHandle.closest('.chat-context-reusable[data-context-json]');
            if (reusable) {
                try { return { type: 'reusable-context', element: explicitHandle, context: normalizeContext(JSON.parse(reusable.dataset.contextJson)) }; } catch (_) { return { type: 'reject', element: explicitHandle, context: null }; }
            }
            const textBubble = explicitHandle.closest('.msg .txt');
            if (textBubble) return { type: 'text-handle', element: explicitHandle, context: textContextFromElement(textBubble) };
        }
        const image = target?.closest?.(IMAGE_SELECTOR);
        if (image) return { type: 'image', element: image, context: imageContextFromElement(image) };
        const reusable = target?.closest?.('.chat-context-reusable[data-context-json]');
        if (reusable) {
            try { return { type: 'reusable-context', element: reusable, context: normalizeContext(JSON.parse(reusable.dataset.contextJson)) }; } catch (_) { return { type: 'reject', element: reusable, context: null }; }
        }
        if (target?.closest?.('.email-draft-card') || isInteractiveDraftControl(target)) {
            return { type: 'reject', element: target, context: null };
        }
        const widget = widgetContextFromElement(target);
        if (widget) return { type: 'widget', element: target, context: widget };
        const textBubble = target?.closest?.('.msg .txt');
        if (textBubble && window.isGDown) return { type: 'text-bubble', element: textBubble, context: textContextFromElement(textBubble) };
        return { type: 'reject', element: target, context: null };
    }

    function contextFromDragTarget(target) {
        return classifyDragSource(target).context;
    }

    function markDraggable(root = document) {
        root.querySelectorAll?.('.msg .txt').forEach(el => {
            const grabMode = Boolean(window.isGDown);
            el.setAttribute('draggable', grabMode ? 'true' : 'false');
            el.classList.toggle('composer-draggable-context', grabMode);
        });
        root.querySelectorAll?.('.email-draft-card').forEach(card => {
            card.setAttribute('draggable', 'false');
            card.classList.remove('composer-draggable-context');
        });
        root.querySelectorAll?.('[data-context-drag-handle], ' + IMAGE_SELECTOR + ', .chat-context-reusable').forEach(el => {
            el.setAttribute('draggable', 'true');
            el.classList.add('composer-draggable-context');
        });
    }

    function clearDragState() {
        document.body.classList.remove('composer-context-dragging');
        document.querySelectorAll('.composer-drop-active, .email-draft-drop-active').forEach(el => {
            el.classList.remove('composer-drop-active', 'email-draft-drop-active');
        });
        ensureTray()?.classList.remove('composer-drop-active', 'is-loading');
    }

    function safeImageTransferUrl(img) {
        const candidates = [
            img?.dataset?.modalUrl,
            img?.dataset?.originalUrl,
            img?.currentSrc,
            img?.getAttribute?.('src')
        ];
        for (const candidate of candidates) {
            try {
                const url = new URL(String(candidate || ''), window.location.href);
                if ((url.protocol === 'http:' || url.protocol === 'https:') && !url.href.startsWith('data:') && !url.href.startsWith('blob:')) {
                    return url.href;
                }
            } catch (_) {}
        }
        return '';
    }

    function safeImageMimeType(img, url) {
        const shared = window.__helperImageMetadata?.(img);
        if (shared?.mimeType) return shared.mimeType;
        const explicit = String(img?.dataset?.mimeType || img?.getAttribute?.('type') || '').toLowerCase().trim();
        if (/^image\/(?:png|jpe?g|webp|gif|bmp|svg\+xml)$/.test(explicit)) return explicit;
        const extension = String(url || '').split(/[?#]/, 1)[0].match(/\.([a-z0-9]+)$/i)?.[1]?.toLowerCase();
        return ({ png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp', gif: 'image/gif', bmp: 'image/bmp', svg: 'image/svg+xml' })[extension]
            || (img?.dataset?.generated === 'true' || /pollinations\.ai/i.test(String(url || '')) ? 'image/png' : '');
    }

    function safeImageFilename(img, url, mimeType) {
        let raw = String(img?.dataset?.filename || img?.getAttribute?.('alt') || '').trim()
            || String(url || '').split(/[?#]/, 1)[0].split('/').pop()
            || 'helper-image';
        const generated = img?.dataset?.generated === 'true' || /pollinations\.ai/i.test(String(url || ''));
        if (generated) {
            raw = raw.replace(/\.[A-Za-z0-9]{2,8}$/, '')
                .replace(/^\s*(?:please\s+)?(?:edit|change|update|add|include|make|create|draft|generate|draw|show|design|illustrate)\b[\s,:-]*/i, '')
                .replace(/^\s*(?:an?|the)\s+(?:image|picture|photo|illustration)\s+of\b[\s,:-]*/i, '');
            const stopWords = new Set(['a', 'an', 'and', 'at', 'by', 'for', 'from', 'in', 'into', 'of', 'on', 'or', 'the', 'to', 'with']);
            const words = (raw.match(/[A-Za-z0-9]+/g) || []).filter(word => !stopWords.has(word.toLowerCase()));
            raw = words.slice(0, 6).join('-') || 'generated-image';
        }
        const base = raw.replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 96) || 'helper-image';
        if (/\.[A-Za-z0-9]{2,5}$/.test(base)) return base;
        const extension = ({ 'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp', 'image/gif': 'gif', 'image/bmp': 'bmp', 'image/svg+xml': 'svg' })[mimeType] || 'png';
        return base + '.' + extension;
    }

    function augmentNativeImageTransfer(event, img) {
        const url = safeImageTransferUrl(img);
        const mimeType = safeImageMimeType(img, url);
        if (!url || !mimeType || !event.dataTransfer) return false;
        const filename = safeImageFilename(img, url, mimeType);
        event.dataTransfer.setData('text/uri-list', url + '\r\n');
        event.dataTransfer.setData('DownloadURL', mimeType + ':' + filename + ':' + url);
        event.dataTransfer.setData('text/plain', url);
        return true;
    }

    function installDragSource() {
        document.addEventListener('dragstart', event => {
            const source = classifyDragSource(event.target);
            const context = source.context;
            if (!context || !event.dataTransfer) {
                if (source.type === 'text-bubble' || source.type === 'reject') event.preventDefault();
                return;
            }
            event.dataTransfer.setData(CONTEXT_MIME, JSON.stringify(context));
            if (source.type === 'image') {
                augmentNativeImageTransfer(event, source.element);
            } else {
                event.dataTransfer.setData('text/plain', context.text);
            }
            event.dataTransfer.effectAllowed = 'copy';
            event.stopPropagation();
            document.body.classList.add('composer-context-dragging');
        }, true);
        document.addEventListener('dragend', clearDragState, true);
        window.addEventListener('blur', clearDragState);
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape') clearDragState();
        });
    }

    function parseDrop(event) {
        const rawContext = event.dataTransfer?.getData(CONTEXT_MIME) || '';
        if (rawContext && rawContext.length <= MAX_TOTAL_CHARS + 2048) {
            try { return normalizeContext(JSON.parse(rawContext)); } catch (_) { return null; }
        }
        const rawDraft = event.dataTransfer?.getData(EMAIL_DRAFT_MIME) || '';
        if (rawDraft && rawDraft.length <= MAX_TOTAL_CHARS + 2048) {
            try {
                const draft = JSON.parse(rawDraft);
                const compactDraft = compactDraftForPrompt(draft);
                const subject = compactDraft.subject || 'Email Draft';
                const attachment = decodeAttachmentName(compactDraft.attachment_filename || '');
                return {
                    kind: 'email',
                    title: 'Email Draft',
                    subtitle: attachment ? subject + ' / ' + attachment : subject,
                    text: emailContextTextFromDraft(compactDraft),
                };
            } catch (_) { return null; }
        }
        const text = clip(event.dataTransfer?.getData('text/plain') || '', MAX_ITEM_CHARS);
        if (!text) return null;
        return { kind: 'text', title: 'Dropped Text', subtitle: compactText(text, 120), text: '[Target Text]\n' + text };
    }

    function installDropTarget() {
        const dropSelectors = ['#prompt', '.pill-bar', '.pill-bar-container', '#input-wrap', '#composer-context-tray'];
        let activeTarget = null;
        let dragDepth = 0;

        function targetFromEvent(event) {
            return dropSelectors.map(selector => event.target?.closest?.(selector)).find(Boolean) || null;
        }

        function supported(event) {
            const types = Array.from(event.dataTransfer?.types || []);
            return Boolean(event.dataTransfer?.files?.length)
                || types.includes(CONTEXT_MIME)
                || types.includes(EMAIL_DRAFT_MIME)
                || types.includes('text/plain');
        }

        function activate(target) {
            if (!target) return;
            activeTarget = target;
            target.classList.add('composer-drop-active');
            ensureTray()?.classList.add('composer-drop-active', 'is-loading');
        }

        function clearTarget() {
            if (activeTarget) activeTarget.classList.remove('composer-drop-active');
            activeTarget = null;
            dragDepth = 0;
            ensureTray()?.classList.remove('composer-drop-active', 'is-loading');
        }

        document.addEventListener('dragenter', event => {
            const target = targetFromEvent(event);
            if (!target || !supported(event)) return;
            event.preventDefault();
            dragDepth += 1;
            activate(target);
        }, true);
        document.addEventListener('dragover', event => {
            const target = targetFromEvent(event);
            if (!target || !supported(event)) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = 'copy';
            activate(target);
        }, true);
        document.addEventListener('dragleave', event => {
            if (!activeTarget || activeTarget.contains(event.relatedTarget)) return;
            dragDepth = Math.max(0, dragDepth - 1);
            if (!dragDepth) clearTarget();
        }, true);
        document.addEventListener('drop', event => {
            const target = targetFromEvent(event);
            if (!target) return;
            const files = Array.from(event.dataTransfer?.files || []).slice(0, 6);
            if (files.length) {
                event.preventDefault();
                event.stopPropagation();
                clearTarget();
                if (typeof window.handleComposerFileDrop === 'function') {
                    window.handleComposerFileDrop(files);
                } else {
                    window.notify?.('File upload is not ready. Please use the attachment button.', 'error');
                }
                return;
            }
            const context = parseDrop(event);
            if (!context) {
                event.preventDefault();
                event.stopPropagation();
                clearTarget();
                window.notify?.('Unsupported drop. Use an image, document, text, or context handle.', 'error');
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            clearTarget();
            addContext(context);
            document.getElementById('prompt')?.focus({ preventScroll: true });
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
                card.className = `chat-context-card composer-context-${item.kind || 'text'} is-ready composer-draggable-context chat-context-reusable`;
                card.setAttribute('draggable', 'true');
                card.dataset.contextIndex = String(index);
                card.dataset.contextJson = JSON.stringify(item);
                card.title = 'Drag to reuse this exact context';
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
        window.syncComposerDragSources = () => markDraggable(document);
        window.addComposerContext = addContext;
        window.getComposerContextFromEmailCard = emailDraftContextFromCard;
        window.clearComposerContextTray = clearContexts;
        window.renderComposerContextTray = scheduleRender;
        window.renderChatContextWidgets = renderChatContextWidgets;
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();