// email_draft.js
// Restores the email-draft frontend surface that is produced by backend agent tools.
// The backend emits EMAIL_DRAFT_PAYLOAD / EMAIL_DRAFT_CONTEXT markers; this module
// turns them into safe UI cards and preserves drag/drop context for follow-up work.
(function () {
    const MARKERS = ['EMAIL_DRAFT_CONTEXT:', 'EMAIL_DRAFT_PAYLOAD:'];
    const DRAFT_MIME = 'application/x-helper-email-draft';

    function escapeHTML(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
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
            if (char === '"') {
                inString = true;
                continue;
            }
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
        return {
            recipient: String(raw.recipient || raw.to || '').trim(),
            subject: String(raw.subject || '').trim(),
            body: String(raw.body || '').trim(),
            tone: String(raw.tone || 'modern').trim() || 'modern',
            attachment_content: raw.attachment_content ?? null,
            attachment_filename: String(raw.attachment_filename || '').trim(),
            attachments,
        };
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
                after: source.slice(jsonEnd).trim(),
            };
        } catch (error) {
            console.warn('[EmailDraft] Invalid draft payload:', error);
            return null;
        }
    }

    function stripInternalEmailDraftMarkers(text) {
        const parsed = parseEmailDraftContext(text);
        if (!parsed) return String(text || '');
        return [parsed.before, parsed.after].filter(Boolean).join('\n\n').trim();
    }

    function renderSafeBodyHtml(body) {
        const escaped = escapeHTML(body || '');
        return `<!doctype html><html><head><meta charset="utf-8"><style>body{font-family:Arial,sans-serif;line-height:1.55;color:#111827;padding:16px;margin:0}pre{white-space:pre-wrap;background:#f3f4f6;padding:12px;border-radius:8px}code{font-family:Consolas,monospace}</style></head><body>${escaped.replace(/\n/g, '<br>')}</body></html>`;
    }

    function attachmentLabel(draft) {
        const names = [];
        if (draft.attachment_filename) names.push(draft.attachment_filename);
        for (const item of draft.attachments || []) {
            const name = item.filename || item.name;
            if (name && !names.includes(name)) names.push(name);
        }
        if (!names.length && (draft.attachment_content || (draft.attachments || []).length)) return '1 attachment';
        return names.join(', ');
    }

    function buildEmailDraftCard(draft) {
        const card = document.createElement('div');
        card.className = 'email-draft-card';
        card.setAttribute('draggable', 'true');
        card.dataset.emailDraft = JSON.stringify(draft);

        const header = document.createElement('div');
        header.className = 'email-draft-header';
        const title = document.createElement('strong');
        title.textContent = 'Email Draft';
        const hint = document.createElement('span');
        hint.textContent = 'Drag to reuse context';
        header.append(title, hint);

        const grid = document.createElement('div');
        grid.className = 'email-draft-grid';
        const rows = [
            ['TO', draft.recipient || '—'],
            ['SUBJECT', draft.subject || '—'],
            ['EMAIL TONE', draft.tone || 'modern'],
            ['ATTACHMENTS', attachmentLabel(draft) || 'None'],
        ];
        for (const [labelText, valueText] of rows) {
            const label = document.createElement('label');
            label.textContent = labelText;
            const value = document.createElement('div');
            value.textContent = valueText;
            grid.append(label, value);
        }

        const bodyLabel = document.createElement('label');
        bodyLabel.textContent = 'BODY';
        const body = document.createElement('pre');
        body.className = 'email-draft-body';
        body.textContent = draft.body || '';

        const previewLabel = document.createElement('label');
        previewLabel.textContent = 'LIVE HTML PREVIEW';
        const iframe = document.createElement('iframe');
        iframe.className = 'email-draft-preview';
        iframe.setAttribute('sandbox', '');
        iframe.srcdoc = renderSafeBodyHtml(draft.body || '');

        card.append(header, grid, bodyLabel, body, previewLabel, iframe);
        card.__emailDraft = draft;
        return card;
    }

    function buildEmailDraftHtml(draft) {
        return buildEmailDraftCard(draft).outerHTML;
    }

    function collectEmailDraftForDrag(card) {
        if (!card) return null;
        if (card.__emailDraft) return card.__emailDraft;
        try {
            return normalizeDraft(JSON.parse(card.dataset.emailDraft || '{}'));
        } catch (_) {
            return null;
        }
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

    function hydrateEmailDraftCards(rootEl) {
        if (!rootEl || typeof rootEl.querySelectorAll !== 'function') return;
        rootEl.querySelectorAll('.email-draft-card').forEach(card => {
            if (card.dataset.emailDraftHydrated === 'true') return;
            card.dataset.emailDraftHydrated = 'true';
            const draft = collectEmailDraftForDrag(card);
            if (!draft) return;
            card.__emailDraft = draft;
            card.addEventListener('dragstart', event => {
                const emailDraft = collectEmailDraftForDrag(card);
                if (!emailDraft || !event.dataTransfer) return;
                event.dataTransfer.setData(DRAFT_MIME, JSON.stringify(emailDraft));
                event.dataTransfer.setData("text/plain", `EMAIL_DRAFT_CONTEXT:${JSON.stringify(emailDraft)}`);
                event.dataTransfer.effectAllowed = 'copy';
            });
        });
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

    window.parseEmailDraftContext = parseEmailDraftContext;
    window.stripInternalEmailDraftMarkers = stripInternalEmailDraftMarkers;
    window.buildEmailDraftDragContext = buildEmailDraftDragContext;
    window.collectEmailDraftForDrag = collectEmailDraftForDrag;
    window.getVisibleUserMessageContent = getVisibleUserMessageContent;
    window.__EMAIL_DRAFT_MIME = DRAFT_MIME;
})();
