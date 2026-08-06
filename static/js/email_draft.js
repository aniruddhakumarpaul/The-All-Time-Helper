// email_draft.js
// Restores and upgrades the email-draft frontend surface produced by backend agent tools.
(function () {
    const EXTENSION_MARKER = '__helperEmailDraftInstalled';
    if (window[EXTENSION_MARKER]) {
        if (document.readyState !== 'loading') window.hydrateEmailDraftCards?.(document);
        return;
    }
    window[EXTENSION_MARKER] = true;

    const MARKERS = ['EMAIL_DRAFT_CONTEXT:', 'EMAIL_DRAFT_PAYLOAD:'];
    const DRAFT_MIME = 'application/x-helper-email-draft';
    const DRAFT_REGISTRY = window.__helperEmailDraftRegistry instanceof Map
        ? window.__helperEmailDraftRegistry
        : new Map();
    window.__helperEmailDraftRegistry = DRAFT_REGISTRY;
    let draftRefCounter = 0;

    function escapeHTML(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function loadPromptContextModule() {
        if (document.querySelector('script[data-helper-extension="draft-context-prompt"]')) return;
        const script = document.createElement('script');
        script.type = 'module';
        script.src = '/static/js/email_context_prompt.js?v=4';
        script.dataset.helperExtension = 'draft-context-prompt';
        document.body.appendChild(script);
    }

    function findMarker(text) {
        const source = String(text || '');
        let best = null;
        for (const marker of MARKERS) {
            const index = source.indexOf(marker);
            if (index !== -1 && (!best || index < best.index)) best = { marker, index };
        }
        return best;
    }

    function findJsonEnd(source, start) {
        let depth = 0;
        let inString = false;
        let escaped = false;
        for (let index = start; index < source.length; index += 1) {
            const char = source[index];
            if (inString) {
                if (escaped) escaped = false;
                else if (char === '\\') escaped = true;
                else if (char === '"') inString = false;
                continue;
            }
            if (char === '"') { inString = true; continue; }
            if (char === '{') depth += 1;
            if (char === '}') {
                depth -= 1;
                if (depth === 0) return index + 1;
            }
        }
        return -1;
    }

    function normalizeDraft(raw) {
        if (window.helperEmailDraftContract?.normalize) return window.helperEmailDraftContract.normalize(raw);
        if (!raw || typeof raw !== 'object') return null;
        const attachments = Array.isArray(raw.attachments) ? raw.attachments : [];
        const hasPayloadAttachment = Boolean(raw.attachment_content) || Boolean(raw.has_attachment_content) || attachments.length > 0;
        const filename = hasPayloadAttachment ? String(raw.attachment_filename || '').trim() : '';
        return {
            recipient: String(raw.recipient || raw.to || '').trim(),
            subject: String(raw.subject || '').trim(),
            body: String(raw.body || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim(),
            tone: String(raw.tone || 'modern').trim() || 'modern',
            attachment_content: raw.attachment_content ?? null,
            attachment_filename: filename && filename !== 'report.txt' ? filename : '',
            attachment_type: raw.attachment_type || raw.content_type || raw.type || undefined,
            attachments,
            has_attachment_content: Boolean(raw.attachment_content || raw.has_attachment_content),
            attachment_description: raw.attachment_description || undefined,
        };
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

    function compactEmailDraftForPrompt(rawDraft) {
        const draft = normalizeDraft(rawDraft);
        if (!draft) return null;
        const hasAttachmentContent = Boolean(draft.attachment_content)
            || Boolean(draft.has_attachment_content)
            || (draft.attachments || []).some(item => item?.content || item?.data || item?.attachment_content);
        const compact = {
            recipient: draft.recipient,
            subject: draft.subject,
            body: draft.body,
            tone: draft.tone,
            attachment_filename: draft.attachment_filename,
            attachments: (draft.attachments || []).map(stripAttachmentPayload).filter(Boolean),
        };
        if (draft.attachment_type) compact.attachment_type = draft.attachment_type;
        if (draft.attachment_description) compact.attachment_description = draft.attachment_description;
        if (hasAttachmentContent) compact.has_attachment_content = true;
        return compact;
    }

    function isCompoundEmailMediaRequest(text) {
        const value = String(text || '').replace(/\s+/g, ' ').toLowerCase().trim();
        if (!value) return false;
        const hasGeneration = /\b(generate|create|draw|paint|render|make|produce)\b/.test(value);
        const hasVisual = /\b(image|photo|picture|artwork|illustration)\b/.test(value);
        const hasAttachment = /\b(attach|add|include)\b/.test(value);
        const hasEmailSurface = /\b(email|mail|draft|widget|template)\b/.test(value);
        return hasGeneration && hasVisual && hasAttachment && hasEmailSurface;
    }

    function isEmailDraftWorkflowFollowup(text) {
        const value = String(text || '').toLowerCase();
        const hasAny = words => words.some(word => value.includes(word));
        const delivery = hasAny(['send this', 'send the', 'email it', 'dispatch', 'deliver', 'approve and send']);
        const attachment = hasAny(['attach', 'include', 'add'])
            && hasAny(['image', 'photo', 'picture', 'reference', 'refernce', 'artwork']);
        const update = hasAny(['update', 'change', 'edit', 'rewrite', 'fill', 'set'])
            && hasAny(['draft', 'email', 'mail', 'subject', 'body', 'recipient', 'tone']);
        const research = hasAny(['current factual', 'latest facts', 'recent facts'])
            && hasAny(['draft', 'email', 'mail', 'add']);
        return isCompoundEmailMediaRequest(value) || delivery || attachment || update || research;
    }

    function resolveActiveEmailDraft() {
        const visibleCards = Array.from(document.querySelectorAll('#chat-area .email-draft-card'))
            .filter(card => !card.hidden && card.offsetParent !== null);
        for (const card of visibleCards.reverse()) {
            const draft = syncDraftFromCard(card);
            if (draft) return draft;
        }
        for (const draft of Array.from(DRAFT_REGISTRY.values()).reverse()) {
            const normalized = normalizeDraft(draft);
            if (normalized) return normalized;
        }
        const globalDraft = normalizeDraft(window.__helperActiveEmailDraft);
        if (globalDraft) return globalDraft;
        const contexts = window.__helperState?.attachedContexts || [];
        for (const context of contexts.slice().reverse()) {
            if (context?.kind !== 'email_draft' && context?.kind !== 'email') continue;
            const draft = context.draft || parseEmailDraftContext(context.text)?.draft;
            const normalized = normalizeDraft(draft);
            if (normalized) return normalized;
        }
        return null;
    }
    function getActiveEmailDraftPromptContext(text) {
        if (!isEmailDraftWorkflowFollowup(text)) return '';
        const compact = compactEmailDraftForPrompt(resolveActiveEmailDraft());
        if (!compact) {
            console.debug('[WorkflowContext] injection_failed reason=active_draft_unresolved');
            return '';
        }
        return 'EMAIL_DRAFT_CONTEXT:' + JSON.stringify(compact);
    }
    function nextDraftRef() {
        draftRefCounter += 1;
        return `email-draft-${Date.now()}-${draftRefCounter}-${Math.random().toString(36).slice(2, 8)}`;
    }

    function storeDraftOnCard(card, draft) {
        if (!card) return null;
        const current = normalizeDraft(draft);
        if (!current) return null;
        const ref = card.dataset.emailDraftRef || nextDraftRef();
        DRAFT_REGISTRY.set(ref, current);
        card.dataset.emailDraftRef = ref;
        card.__emailDraft = current;
        window.__helperActiveEmailDraft = current;
        while (DRAFT_REGISTRY.size > 64) DRAFT_REGISTRY.delete(DRAFT_REGISTRY.keys().next().value);
        card.dataset.emailDraft = JSON.stringify(compactEmailDraftForPrompt(current));
        return current;
    }

    function draftFromCardStore(card) {
        if (!card) return null;
        if (card.__emailDraft) return normalizeDraft(card.__emailDraft);
        const ref = card.dataset.emailDraftRef;
        if (ref && DRAFT_REGISTRY.has(ref)) return normalizeDraft(DRAFT_REGISTRY.get(ref));
        try { return normalizeDraft(JSON.parse(card.dataset.emailDraft || '{}')); } catch (_) { return null; }
    }

    function findEmailDraftCandidate(value) {
        if (!value || typeof value !== 'object') return null;
        if (!Array.isArray(value) && (value.recipient || value.to)
            && Object.prototype.hasOwnProperty.call(value, 'subject')
            && Object.prototype.hasOwnProperty.call(value, 'body')) return value;
        if (Array.isArray(value)) {
            for (const item of value) {
                const found = findEmailDraftCandidate(item);
                if (found) return found;
            }
            return null;
        }
        for (const item of Object.values(value)) {
            const found = findEmailDraftCandidate(item);
            if (found) return found;
        }
        return null;
    }

    function parseEmailDraftContext(text) {
        const source = String(text || '');
        const found = findMarker(source);
        const isUnmarkedJson = !found && source.trimStart().startsWith('{');
        if (!found && !isUnmarkedJson) return null;
        const jsonStart = found ? source.indexOf('{', found.index + found.marker.length) : source.indexOf('{');
        if (jsonStart === -1) return null;
        const jsonEnd = findJsonEnd(source, jsonStart);
        if (jsonEnd === -1) return null;
        try {
            const rawJson = source.slice(jsonStart, jsonEnd);
            const parsed = JSON.parse(rawJson);
            const rawDraft = found ? parsed : findEmailDraftCandidate(parsed);
            const draft = normalizeDraft(rawDraft);
            if (!draft || (!found && (!draft.recipient || !draft.subject))) return null;
            return {
                marker: found?.marker || null,
                draft,
                rawJson,
                start: found ? found.index : jsonStart,
                end: jsonEnd,
                before: source.slice(0, found ? found.index : jsonStart).trim(),
                after: source.slice(jsonEnd).trim()
            };
        } catch (error) {
            console.warn('[EmailDraft] Invalid draft payload:', error);
            return null;
        }
    }

    function parseDraftFromTransfer(raw) {
        const text = String(raw || '').trim();
        if (!text) return null;
        if (text.startsWith('{')) {
            try { return normalizeDraft(JSON.parse(text)); } catch (_) { return null; }
        }
        return parseEmailDraftContext(text)?.draft || null;
    }

    function stripInternalEmailDraftMarkers(text) {
        const parsed = parseEmailDraftContext(text);
        if (!parsed) return String(text || '');
        return [parsed.before, parsed.after].filter(Boolean).join('\n\n').trim();
    }

    function renderSafeBodyHtml(body) {
        const escaped = escapeHTML(body || '');
        return `<!doctype html><html><head><meta charset="utf-8"><style>body{font-family:Arial,sans-serif;line-height:1.55;color:#111827;padding:16px;margin:0;white-space:normal}pre{white-space:pre-wrap;background:#f3f4f6;padding:12px;border-radius:8px}code{font-family:Consolas,monospace}</style></head><body>${escaped.replace(/\n/g, '<br>')}</body></html>`;
    }

    function attachmentLabel(draft) {
        const names = [];
        if (draft.attachment_filename) names.push(draft.attachment_filename);
        for (const item of draft.attachments || []) {
            const name = item.filename || item.name;
            if (name && !names.includes(name)) names.push(name);
        }
        if (!names.length && (draft.attachment_content || draft.has_attachment_content || (draft.attachments || []).length)) return '1 attachment';
        return names.join(', ');
    }

    function attachmentImageType(item) {
        return /^image\/(?:jpeg|png|webp|gif)$/i.test(String(item?.mime_type || item?.content_type || item?.type || ''));
    }

    function safeAttachmentUrl(item) {
        const candidate = item?.url || (typeof item?.content === 'string' && /^https?:\/\//i.test(item.content) ? item.content : '');
        try {
            const raw = String(candidate || '');
            const url = /^https?:/i.test(raw) ? new URL(raw) : new URL(raw, window.location.origin);
            return url.protocol === 'http:' || url.protocol === 'https:' ? url.href.slice(0, 2000) : '';
        } catch (_) {
            return '';
        }
    }

    function attachmentSourceLabel(item) {
        const source = String(item?.source || '').toLowerCase();
        if (source === 'generated') return 'Generated';
        if (source === 'reference' || source === 'remote') return 'Reference';
        if (source === 'upload') return 'Uploaded';
        return 'Attachment';
    }

    function attachmentCategory(item) {
        const mime = String(item?.mime_type || item?.content_type || item?.type || 'file').toLowerCase();
        if (mime === 'application/pdf') return 'PDF document';
        if (mime === 'text/plain' || mime === 'text/markdown') return 'Text document';
        if (mime.startsWith('image/')) return mime.split('/')[1].toUpperCase() + ' image';
        return mime.split('/').pop().replace(/[-_]+/g, ' ') || 'File';
    }

    function attachmentIdentity(item, index) {
        return String(item?.id || (attachmentImageType(item) ? safeAttachmentUrl(item) : '') || item?.sha256 || item?.filename || 'attachment-' + index).slice(0, 160);
    }

    function attachmentIcon(item) {
        const mime = String(item?.mime_type || '').toLowerCase();
        if (mime.startsWith('image/')) return '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"></rect><circle cx="8.5" cy="9" r="1.5"></circle><path d="m5 17 4.5-4 3 3 2-2 4.5 4"></path></svg>';
        if (mime === 'application/pdf') return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h8l4 4v14H6z"></path><path d="M14 3v5h5M8 16h8M8 12h5"></path></svg>';
        return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h8l4 4v14H6z"></path><path d="M14 3v5h5"></path></svg>';
    }

    async function resolveOwnerAttachmentUrl(item) {
        if (!item?.id || typeof window.helperApiUrl !== 'function') return null;
        try {
            const response = await fetch(window.helperApiUrl('/attachments/' + encodeURIComponent(item.id)), {
                headers: { Authorization: 'Bearer ' + (localStorage.getItem('helper_token_v2') || '') }
            });
            if (!response.ok) return null;
            const blob = await response.blob();
            if (!/^image\//i.test(blob.type)) return null;
            return { url: URL.createObjectURL(blob), revoke: true };
        } catch (_) {
            return null;
        }
    }

    function attachmentPromptContext(item, index) {
        const isImage = attachmentImageType(item);
        const url = isImage ? safeAttachmentUrl(item) : '';
        const remoteDocumentUrl = !isImage && Boolean(safeAttachmentUrl(item));
        const name = String(item?.filename || item?.name || 'Attached file').slice(0, 160);
        const description = String(item?.description || '').slice(0, 320);
        const mime = String(item?.mime_type || item?.content_type || item?.type || 'application/octet-stream').slice(0, 100);
        const attachmentRef = item?.id && /^[a-f0-9]{32}$/i.test(String(item.id))
            ? {
                id: String(item.id).toLowerCase(),
                name: name.slice(0, 160),
                type: mime.slice(0, 100),
                size: Number.isInteger(Number(item.size)) && Number(item.size) >= 0 ? Number(item.size) : undefined,
            }
            : undefined;
        const sourceCategory = attachmentSourceLabel(item);
        const availability = item?.available === false
            ? 'Unavailable'
            : attachmentRef
                ? 'Owner-scoped attachment available'
                : isImage && url
                    ? 'Remote image available'
                    : 'Metadata only';
        const sourceRef = attachmentRef ? 'Owner attachment reference: ' + attachmentRef.id : '';
        const imageSource = isImage && url ? 'Image source: ' + url : '';
        const documentNotice = remoteDocumentUrl && !attachmentRef
            ? 'This remote document is referenced by metadata only; its contents are not attached.'
            : '';
        return {
            kind: isImage ? 'image' : 'document',
            sourceId: 'attachment:' + attachmentIdentity(item, index),
            title: name,
            subtitle: [attachmentCategory(item), sourceCategory].join(' / '),
            preview: isImage ? url : '',
            text: '[Attached File]\nFilename: ' + name
                + '\nMIME type: ' + mime
                + '\nSource category: ' + sourceCategory
                + '\nAvailability state: ' + availability
                + '\n' + [sourceRef, imageSource, documentNotice, description].filter(Boolean).join('\n'),
            ...(attachmentRef ? { attachmentRef } : {}),
        };
    }

    function attachAttachmentToPrompt(card, index) {
        const draft = syncDraftFromCard(card);
        const item = draft?.attachments?.[index];
        if (!item || item.available === false || typeof window.addComposerContext !== 'function') {
            window.notify?.('This attachment is unavailable.', 'error');
            return false;
        }
        const accepted = window.addComposerContext(attachmentPromptContext(item, index));
        if (accepted) window.notify?.('Attachment added to the next prompt.', 'success', 1600);
        return Boolean(accepted);
    }

    async function openAttachmentPreview(card, index, button) {
        const draft = syncDraftFromCard(card);
        const item = draft?.attachments?.[index];
        if (!item || !attachmentImageType(item) || item.available === false) {
            window.notify?.('Preview is unavailable for this attachment.', 'error');
            return;
        }
        button?.setAttribute('aria-busy', 'true');
        const originalUrl = safeAttachmentUrl(item);
        let resolved = originalUrl ? { url: originalUrl, revoke: false } : await resolveOwnerAttachmentUrl(item);
        button?.removeAttribute('aria-busy');
        if (!resolved?.url) {
            window.notify?.('This image attachment is unavailable.', 'error');
            return;
        }
        const previewUrl = originalUrl && typeof window.helperApiUrl === 'function'
            ? window.helperApiUrl('/api/image_proxy?url=' + encodeURIComponent(originalUrl))
            : resolved.url;
        window.openImageModal(previewUrl, {
            filename: String(item.filename || item.name || 'Attached image'),
            mimeType: String(item.mime_type || item.content_type || item.type || 'image/png'),
            source: attachmentSourceLabel(item),
            sourceUrl: originalUrl,
            downloadUrl: originalUrl || resolved.url,
            downloadable: Boolean(originalUrl || resolved.revoke),
            copyable: Boolean(originalUrl),
            revokeUrl: resolved.revoke ? resolved.url : '',
            context: attachmentPromptContext(item, index)
        });
    }

    function renderAttachmentSummary(container, draft) {
        if (!container) return;
        container.textContent = '';
        const attachments = Array.isArray(draft?.attachments) ? draft.attachments : [];
        if (!attachments.length) {
            const empty = document.createElement('span');
            empty.className = 'email-draft-attachment-empty';
            empty.textContent = 'No attachments';
            container.appendChild(empty);
            return;
        }
        attachments.forEach((item, index) => {
            const row = document.createElement('div');
            row.className = 'email-draft-attachment-row';
            const available = item.available !== false;
            const image = attachmentImageType(item);
            const chip = document.createElement(available ? 'button' : 'div');
            chip.type = available ? 'button' : undefined;
            chip.className = 'email-draft-attachment-chip';
            chip.dataset.attachmentIndex = String(index);
            chip.setAttribute('aria-label', image ? 'Preview ' + String(item.filename || item.name || 'image') : 'Use ' + String(item.filename || item.name || 'attachment') + ' in prompt');
            chip.title = available ? (image ? 'Preview image attachment' : 'Use document attachment in prompt') : 'Attachment unavailable';
            if (!available) chip.setAttribute('aria-disabled', 'true');
            const icon = document.createElement('span');
            icon.className = 'email-draft-attachment-icon';
            icon.innerHTML = attachmentIcon(item);
            const copy = document.createElement('span');
            copy.className = 'email-draft-attachment-copy';
            const name = document.createElement('strong');
            name.textContent = String(item.filename || item.name || 'Attached file');
            const type = document.createElement('small');
            type.textContent = [attachmentCategory(item), attachmentSourceLabel(item), available ? '' : 'Unavailable'].filter(Boolean).join(' / ');
            copy.append(name, type);
            chip.append(icon, copy);
            row.appendChild(chip);
            if (available) {
                const use = document.createElement('button');
                use.type = 'button';
                use.className = 'email-draft-attachment-use';
                use.dataset.attachmentIndex = String(index);
                use.setAttribute('aria-label', 'Use ' + String(item.filename || item.name || 'attachment') + ' in prompt');
                use.textContent = 'Use';
                use.addEventListener('click', event => {
                    event.preventDefault();
                    event.stopPropagation();
                    attachAttachmentToPrompt(container.closest('.email-draft-card'), index);
                });
                row.appendChild(use);
            }
            container.appendChild(row);
        });
    }
    function field(labelText, control) {
        const label = document.createElement('label');
        label.className = 'email-draft-field-label';
        label.textContent = labelText;
        if (control.id) label.htmlFor = control.id;
        return [label, control];
    }

    function syncDraftFromCard(card) {
        if (!card) return null;
        let current = draftFromCardStore(card) || {
            recipient: '', subject: '', body: '', tone: 'modern', attachment_content: null, attachment_filename: '', attachments: []
        };

        const toInput = card.querySelector('.email-draft-recipient');
        const subjectInput = card.querySelector('.email-draft-subject');
        const toneSelect = card.querySelector('.email-draft-tone');
        const bodyInput = card.querySelector('.email-draft-body-input');
        const attachmentValue = card.querySelector('.email-draft-attachments');
        const preview = card.querySelector('.email-draft-preview');

        if (toInput) current.recipient = toInput.value.trim();
        if (subjectInput) current.subject = subjectInput.value.trim();
        if (toneSelect) current.tone = toneSelect.value || 'modern';
        if (bodyInput) current.body = bodyInput.value.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
        if (!current.attachment_content && !current.has_attachment_content && !(current.attachments || []).length) current.attachment_filename = '';

        current = storeDraftOnCard(card, current) || current;
        if (attachmentValue) renderAttachmentSummary(attachmentValue, current);
        if (preview) preview.srcdoc = renderSafeBodyHtml(current.body || '');
        return current;
    }

    function buildEmailDraftCard(draft) {
        const card = document.createElement('div');
        card.className = 'email-draft-card';
        card.setAttribute('draggable', 'false');

        const current = normalizeDraft(draft) || draft;
        storeDraftOnCard(card, current);
        const idPrefix = card.dataset.emailDraftRef || nextDraftRef();

        const header = document.createElement('div');
        header.className = 'email-draft-header';

        const titleWrap = document.createElement('div');
        titleWrap.className = 'email-draft-title-wrap';
        const title = document.createElement('strong');
        title.textContent = 'Email Draft';
        const status = document.createElement('span');
        status.className = 'email-draft-status';
        status.textContent = 'Editable draft';
        titleWrap.append(title, status);

        const headerTools = document.createElement('div');
        headerTools.className = 'email-draft-header-tools';
        const hint = document.createElement('span');
        hint.className = 'email-draft-hint';
        hint.textContent = 'Drag the handle or use the prompt button';
        const handle = document.createElement('button');
        handle.type = 'button';
        handle.className = 'email-draft-drag-handle';
        handle.setAttribute('draggable', 'true');
        handle.setAttribute('data-context-drag-handle', 'true');
        handle.setAttribute('aria-label', 'Drag this email draft into the prompt');
        handle.title = 'Drag this email draft into the prompt';
        handle.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5h2v2H8V5Zm6 0h2v2h-2V5ZM8 11h2v2H8v-2Zm6 0h2v2h-2v-2ZM8 17h2v2H8v-2Zm6 0h2v2h-2v-2Z"></path></svg>';
        headerTools.append(hint, handle);
        header.append(titleWrap, headerTools);

        const grid = document.createElement('div');
        grid.className = 'email-draft-grid';

        const toInput = document.createElement('input');
        toInput.id = idPrefix + '-recipient';
        toInput.className = 'email-draft-input email-draft-recipient';
        toInput.type = 'email';
        toInput.autocomplete = 'off';
        toInput.placeholder = 'name@example.com';
        toInput.value = current.recipient || '';
        toInput.setAttribute('value', toInput.value);

        const subjectInput = document.createElement('input');
        subjectInput.id = idPrefix + '-subject';
        subjectInput.className = 'email-draft-input email-draft-subject';
        subjectInput.type = 'text';
        subjectInput.autocomplete = 'off';
        subjectInput.placeholder = 'Subject';
        subjectInput.value = current.subject || '';
        subjectInput.setAttribute('value', subjectInput.value);

        const toneSelect = document.createElement('select');
        toneSelect.id = idPrefix + '-tone';
        toneSelect.className = 'email-draft-input email-draft-tone';
        ['formal', 'modern', 'informal'].forEach(tone => {
            const option = document.createElement('option');
            option.value = tone;
            option.textContent = tone;
            option.selected = (current.tone || 'modern') === tone;
            toneSelect.appendChild(option);
        });

        const attachmentValue = document.createElement('div');
        attachmentValue.id = idPrefix + '-attachments';
        attachmentValue.className = 'email-draft-attachments';
        renderAttachmentSummary(attachmentValue, current);

        for (const pair of [
            field('TO', toInput),
            field('SUBJECT', subjectInput),
            field('EMAIL TONE', toneSelect),
            field('ATTACHMENTS', attachmentValue)
        ]) {
            grid.append(pair[0], pair[1]);
        }

        const bodyLabel = document.createElement('label');
        bodyLabel.className = 'email-draft-section-label';
        bodyLabel.textContent = 'BODY';
        bodyLabel.htmlFor = idPrefix + '-body';
        const body = document.createElement('textarea');
        body.id = idPrefix + '-body';
        body.className = 'email-draft-body-input';
        body.placeholder = 'Write the message body...';
        body.value = current.body || '';
        body.rows = Math.max(4, Math.min(12, String(current.body || '').split('\n').length + 2));

        const previewLabel = document.createElement('label');
        previewLabel.className = 'email-draft-section-label';
        previewLabel.textContent = 'LIVE HTML PREVIEW';
        previewLabel.htmlFor = idPrefix + '-preview';
        const iframe = document.createElement('iframe');
        iframe.id = idPrefix + '-preview';
        iframe.className = 'email-draft-preview';
        iframe.title = 'Live email body preview';
        iframe.setAttribute('sandbox', '');
        iframe.srcdoc = renderSafeBodyHtml(current.body || '');

        const actions = document.createElement('div');
        actions.className = 'email-draft-actions';
        const useBtn = document.createElement('button');
        useBtn.type = 'button';
        useBtn.className = 'email-draft-use-context-btn';
        useBtn.textContent = 'Use in prompt';
        useBtn.setAttribute('aria-label', 'Use this email draft in the next prompt');
        actions.appendChild(useBtn);

        card.append(header, grid, bodyLabel, body, previewLabel, iframe, actions);
        hydrateEmailDraftCards(card);
        return card;
    }

    function buildEmailDraftHtml(draft) {
        return buildEmailDraftCard(draft).outerHTML;
    }

    function isInteractiveDraftControl(target) {
        return Boolean(target?.closest?.('.email-draft-card button, .email-draft-card input, .email-draft-card textarea, .email-draft-card select, .email-draft-card option, .email-draft-card label, .email-draft-card a, .email-draft-card [contenteditable="true"]'));
    }

    function collectEmailDraftForDrag(card) {
        if (!card) return null;
        const fromCard = syncDraftFromCard(card);
        if (fromCard) return normalizeDraft(fromCard);
        return draftFromCardStore(card);
    }

    function buildEmailDraftDragContext(message, widgetEl = null) {
        const widgetDraft = collectEmailDraftForDrag(widgetEl);
        if (widgetDraft) return widgetDraft;
        const parsed = parseEmailDraftContext(typeof message === 'string' ? message : (message?.c || message?.content || ''));
        return parsed?.draft || null;
    }

    function getVisibleUserMessageContent(message, element = null) {
        const raw = typeof message === 'string' ? message : (message?.c || message?.content || element?.innerText || '');
        return stripInternalEmailDraftMarkers(raw);
    }

    function showDraftContextPanel(draft) {
        const card = document.getElementById('neural-context-card');
        const container = document.getElementById('context-results');
        const scrim = document.getElementById('neural-scrim');
        if (!card || !container || !scrim) return false;
        container.textContent = '';
        const label = document.createElement('span');
        label.className = 'source-label';
        label.textContent = 'Email Draft Context';
        container.appendChild(label);
        const draftCard = buildEmailDraftCard(draft);
        container.appendChild(draftCard);
        hydrateEmailDraftCards(container);
        window.hydrateEmailDraftApprovalButtons?.(container);
        card.classList.add('active');
        scrim.classList.add('active');
        return true;
    }

    function supersedeEarlierDraftCards(currentCard) {
        if (!currentCard?.isConnected) return;
        const chatArea = document.getElementById('chat-area');
        if (!chatArea?.contains(currentCard)) return;
        chatArea.querySelectorAll('.email-draft-card').forEach(card => {
            if (card === currentCard) {
                card.hidden = false;
                card.removeAttribute('data-email-draft-superseded');
                return;
            }
            card.hidden = true;
            card.dataset.emailDraftSuperseded = 'true';
        });
    }
    function hydrateEmailDraftCards(rootEl) {
        if (!rootEl || typeof rootEl.querySelectorAll !== 'function') return;
        const cards = rootEl.matches?.('.email-draft-card') ? [rootEl] : Array.from(rootEl.querySelectorAll('.email-draft-card'));
        cards.forEach(card => {
            if (!card.isConnected) return;
            const draft = syncDraftFromCard(card);
            if (!draft) return;
            if (card.__attachmentActionsBound !== 'true') {
                card.__attachmentActionsBound = 'true';
                card.addEventListener('click', event => {
                    const chip = event.target?.closest?.('.email-draft-attachment-chip');
                    if (!chip || event.target?.closest?.('.email-draft-attachment-use')) return;
                    const index = Number(chip.dataset.attachmentIndex);
                    const current = syncDraftFromCard(card)?.attachments?.[index];
                    if (!current || current.available === false) return;
                    if (attachmentImageType(current)) openAttachmentPreview(card, index, chip);
                    else attachAttachmentToPrompt(card, index);
                });
            }
            supersedeEarlierDraftCards(card);
            if (card.dataset.emailDraftHydrated === 'true') return;
            card.dataset.emailDraftHydrated = 'true';
            const sync = () => syncDraftFromCard(card);
            card.querySelectorAll('.email-draft-recipient, .email-draft-subject, .email-draft-tone, .email-draft-body-input').forEach(el => {
                el.addEventListener('input', sync);
                el.addEventListener('change', sync);
            });
            card.querySelector('.email-draft-use-context-btn')?.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                const latest = syncDraftFromCard(card);
                if (latest && typeof window.attachEmailDraftToPrompt === 'function') {
                    window.attachEmailDraftToPrompt(latest, card.dataset.emailDraftRef || '');
                }
            });
        });
        window.hydrateEmailDraftApprovalButtons?.(rootEl);
    }

    function installMascotDraftDrop() {
        const mascot = document.getElementById('mascot-container');
        if (!mascot || mascot.dataset.emailDraftDrop === 'true') return;
        mascot.dataset.emailDraftDrop = 'true';
        mascot.addEventListener('drop', event => {
            const raw = event.dataTransfer?.getData(DRAFT_MIME) || event.dataTransfer?.getData('text/plain') || '';
            const draft = parseDraftFromTransfer(raw);
            if (!draft) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            mascot.classList.remove('mascot-drop-active');
            showDraftContextPanel(draft);
        }, true);
    }

    const originalRenderMarkdown = window.renderMarkdown;
    window.renderMarkdown = function renderMarkdownWithEmailDraft(text) {
        const parsed = parseEmailDraftContext(text);
        if (!parsed) return originalRenderMarkdown ? originalRenderMarkdown(text) : escapeHTML(text);
        const visibleText = stripInternalEmailDraftMarkers(text);
        const visibleHtml = visibleText && originalRenderMarkdown ? originalRenderMarkdown(visibleText) : escapeHTML(visibleText);
        return `${visibleHtml}${visibleHtml ? '<br>' : ''}${buildEmailDraftHtml(parsed.draft)}`;
    };

    const originalHydrate = window.hydrateRenderedMarkdown;
    window.hydrateRenderedMarkdown = function hydrateRenderedMarkdownWithEmailDraft(rootEl) {
        if (typeof originalHydrate === 'function') originalHydrate(rootEl);
        hydrateEmailDraftCards(rootEl);
    };

    function initEmailDraftFrontend() {
        loadPromptContextModule();
        hydrateEmailDraftCards(document);
        installMascotDraftDrop();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initEmailDraftFrontend);
    else initEmailDraftFrontend();

    window.parseEmailDraftContext = parseEmailDraftContext;
    window.stripInternalEmailDraftMarkers = stripInternalEmailDraftMarkers;
    window.buildEmailDraftDragContext = buildEmailDraftDragContext;
    window.collectEmailDraftForDrag = collectEmailDraftForDrag;
    window.syncEmailDraftFromCard = syncDraftFromCard;
    window.hydrateEmailDraftCards = hydrateEmailDraftCards;
    window.getVisibleUserMessageContent = getVisibleUserMessageContent;
    window.showDraftContextPanel = showDraftContextPanel;
    window.compactEmailDraftForPrompt = compactEmailDraftForPrompt;
    window.renderAttachmentSummary = renderAttachmentSummary;
    window.getActiveEmailDraftPromptContext = getActiveEmailDraftPromptContext;
    window.resolveActiveEmailDraft = resolveActiveEmailDraft;
    window.isCompoundEmailMediaRequest = isCompoundEmailMediaRequest;
    window.isEmailDraftWorkflowFollowup = isEmailDraftWorkflowFollowup;
    window.__EMAIL_DRAFT_MIME = DRAFT_MIME;
})();
