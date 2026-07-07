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
        script.src = '/static/js/email_context_prompt.js?v=2';
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

    function parseEmailDraftContext(text) {
        const source = String(text || '');
        const found = findMarker(source);
        if (!found) return null;
        const afterMarker = found.index + found.marker.length;
        const jsonStart = source.indexOf('{', afterMarker);
        if (jsonStart === -1) return null;
        const jsonEnd = findJsonEnd(source, jsonStart);
        if (jsonEnd === -1) return null;
        try {
            const rawJson = source.slice(jsonStart, jsonEnd);
            const draft = normalizeDraft(JSON.parse(rawJson));
            if (!draft) return null;
            return {
                marker: found.marker,
                draft,
                rawJson,
                start: found.index,
                end: jsonEnd,
                before: source.slice(0, found.index).trim(),
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

    function inputStyle(extra = '') {
        return `width:100%;box-sizing:border-box;border:1px solid var(--glass-border);border-radius:10px;background:rgba(0,0,0,.18);color:var(--text-main);padding:8px 10px;font:inherit;font-size:.84rem;${extra}`;
    }

    function field(labelText, control) {
        const label = document.createElement('label');
        label.textContent = labelText;
        label.style.cssText = 'font-size:0.68rem;color:var(--text-sub);font-weight:800;letter-spacing:.08em;align-self:center;';
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
        const attachmentValue = card.querySelector('.email-draft-attachment-label');
        const preview = card.querySelector('.email-draft-preview');

        if (toInput) current.recipient = toInput.value.trim();
        if (subjectInput) current.subject = subjectInput.value.trim();
        if (toneSelect) current.tone = toneSelect.value || 'modern';
        if (bodyInput) current.body = bodyInput.value.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
        if (!current.attachment_content && !current.has_attachment_content && !(current.attachments || []).length) current.attachment_filename = '';

        current = storeDraftOnCard(card, current) || current;
        if (attachmentValue) attachmentValue.textContent = attachmentLabel(current) || 'None';
        if (preview) preview.srcdoc = renderSafeBodyHtml(current.body || '');
        return current;
    }

    function buildEmailDraftCard(draft) {
        const card = document.createElement('div');
        card.className = 'email-draft-card';
        card.setAttribute('draggable', 'true');
        card.style.cssText = 'margin:14px 0;padding:16px;border:1px solid var(--glass-border);border-radius:16px;background:rgba(255,255,255,0.045);box-shadow:0 12px 30px rgba(0,0,0,0.18);cursor:grab;max-width:100%;';

        const current = normalizeDraft(draft) || draft;
        storeDraftOnCard(card, current);

        const header = document.createElement('div');
        header.className = 'email-draft-header';
        header.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px;';
        const title = document.createElement('strong');
        title.textContent = 'Email Draft';
        title.style.cssText = 'font-size:0.95rem;color:var(--text-main);letter-spacing:.02em;';
        const hint = document.createElement('span');
        hint.textContent = 'Editable • drag or attach to prompt';
        hint.style.cssText = 'font-size:0.72rem;color:var(--text-sub);';
        header.append(title, hint);

        const grid = document.createElement('div');
        grid.className = 'email-draft-grid';
        grid.style.cssText = 'display:grid;grid-template-columns:minmax(90px,auto) 1fr;gap:8px 12px;margin-bottom:12px;';

        const toInput = document.createElement('input');
        toInput.className = 'email-draft-input email-draft-recipient';
        toInput.value = current.recipient || '';
        toInput.style.cssText = inputStyle();

        const subjectInput = document.createElement('input');
        subjectInput.className = 'email-draft-input email-draft-subject';
        subjectInput.value = current.subject || '';
        subjectInput.style.cssText = inputStyle();

        const toneSelect = document.createElement('select');
        toneSelect.className = 'email-draft-input email-draft-tone';
        toneSelect.style.cssText = inputStyle();
        ['formal', 'modern', 'informal'].forEach(tone => {
            const option = document.createElement('option');
            option.value = tone;
            option.textContent = tone;
            if ((current.tone || 'modern') === tone) option.selected = true;
            toneSelect.appendChild(option);
        });

        const attachmentValue = document.createElement('div');
        attachmentValue.className = 'email-draft-attachment-label';
        attachmentValue.textContent = attachmentLabel(current) || 'None';
        attachmentValue.style.cssText = 'font-size:0.84rem;color:var(--text-main);word-break:break-word;';

        for (const pair of [field('TO', toInput), field('SUBJECT', subjectInput), field('EMAIL TONE', toneSelect), field('ATTACHMENTS', attachmentValue)]) {
            grid.append(pair[0], pair[1]);
        }

        const bodyLabel = document.createElement('label');
        bodyLabel.textContent = 'BODY';
        bodyLabel.style.cssText = 'display:block;margin-top:8px;margin-bottom:6px;font-size:0.68rem;color:var(--text-sub);font-weight:800;letter-spacing:.08em;';
        const body = document.createElement('textarea');
        body.className = 'email-draft-body-input';
        body.value = current.body || '';
        body.rows = Math.max(4, Math.min(12, String(current.body || '').split('\n').length + 2));
        body.style.cssText = inputStyle('resize:vertical;min-height:110px;line-height:1.45;white-space:pre-wrap;');

        const previewLabel = document.createElement('label');
        previewLabel.textContent = 'LIVE HTML PREVIEW';
        previewLabel.style.cssText = bodyLabel.style.cssText;
        const iframe = document.createElement('iframe');
        iframe.className = 'email-draft-preview';
        iframe.setAttribute('sandbox', '');
        iframe.srcdoc = renderSafeBodyHtml(current.body || '');
        iframe.style.cssText = 'width:100%;min-height:160px;border:0;border-radius:12px;background:#fff;';

        const actions = document.createElement('div');
        actions.className = 'email-draft-actions';
        actions.style.cssText = 'display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:12px;';
        const useBtn = document.createElement('button');
        useBtn.type = 'button';
        useBtn.className = 'email-draft-use-context-btn';
        useBtn.textContent = 'Use in prompt';
        useBtn.style.cssText = 'border:1px solid var(--glass-border);border-radius:999px;padding:9px 15px;background:rgba(255,255,255,.08);color:var(--text-main);font-weight:800;cursor:pointer;';
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

    function hydrateEmailDraftCards(rootEl) {
        if (!rootEl || typeof rootEl.querySelectorAll !== 'function') return;
        const cards = rootEl.matches?.('.email-draft-card') ? [rootEl] : Array.from(rootEl.querySelectorAll('.email-draft-card'));
        cards.forEach(card => {
            const draft = syncDraftFromCard(card);
            if (!draft) return;
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
                if (latest && typeof window.attachEmailDraftToPrompt === 'function') window.attachEmailDraftToPrompt(latest);
            });
            card.addEventListener('dragstart', event => {
                if (isInteractiveDraftControl(event.target)) return;
                const emailDraft = syncDraftFromCard(card);
                if (!emailDraft || !event.dataTransfer) return;
                const transferDraft = compactEmailDraftForPrompt(emailDraft) || emailDraft;
                event.dataTransfer.setData(DRAFT_MIME, JSON.stringify(transferDraft));
                event.dataTransfer.setData('text/plain', `EMAIL_DRAFT_CONTEXT:${JSON.stringify(transferDraft)}`);
                event.dataTransfer.effectAllowed = 'copy';
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
    window.__EMAIL_DRAFT_MIME = DRAFT_MIME;
})();