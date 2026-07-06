// email_draft_repair.js
// Additive UI repair layer for draft cards, prompt-context chips, and refresh recovery.
(function () {
    const DRAFT_MIME = 'application/x-helper-email-draft';
    const CONTEXT_MARKER = 'EMAIL_DRAFT_CONTEXT:';
    let appState = null;
    let lastSavedSnapshot = '';

    function inferSubjectAndBody(subject, body) {
        let cleanSubject = String(subject || '').trim();
        let cleanBody = String(body || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim();
        if (!cleanSubject && cleanBody) {
            const rawLines = cleanBody.split('\n');
            const nonEmpty = rawLines.map(line => line.trim()).filter(Boolean);
            if (nonEmpty.length >= 2) {
                cleanSubject = nonEmpty[0].slice(0, 998);
                const firstIndex = rawLines.findIndex(line => line.trim() === nonEmpty[0]);
                cleanBody = rawLines.slice(firstIndex + 1).join('\n').replace(/^\n+/, '').trim();
            }
        }
        return { subject: cleanSubject, body: cleanBody };
    }

    function normalizeDraft(raw) {
        if (!raw || typeof raw !== 'object') return null;
        const attachments = Array.isArray(raw.attachments) ? raw.attachments : [];
        const hasAttachmentPayload = Boolean(raw.attachment_content) || attachments.length > 0;
        const inferred = inferSubjectAndBody(raw.subject, raw.body);
        let filename = hasAttachmentPayload ? String(raw.attachment_filename || '').trim() : '';
        if (filename === 'report.txt' && !raw.attachment_content && !attachments.length) filename = '';
        return {
            recipient: String(raw.recipient || raw.to || '').trim(),
            subject: inferred.subject,
            body: inferred.body,
            tone: String(raw.tone || 'modern').trim() || 'modern',
            attachment_content: raw.attachment_content || null,
            attachment_filename: filename,
            attachments,
        };
    }

    function parseTransferText(raw) {
        const text = String(raw || '').trim();
        if (!text) return null;
        if (typeof window.parseEmailDraftContext === 'function') {
            const parsed = window.parseEmailDraftContext(text);
            if (parsed?.draft) return normalizeDraft(parsed.draft);
        }
        try {
            const json = text.startsWith(CONTEXT_MARKER) ? text.slice(CONTEXT_MARKER.length) : text;
            return normalizeDraft(JSON.parse(json));
        } catch (_) {
            return null;
        }
    }

    function collectDraft(card) {
        if (!card) return null;
        const fields = readFields(card);
        if (fields) return fields;
        if (typeof window.collectEmailDraftForDrag === 'function') {
            const collected = window.collectEmailDraftForDrag(card);
            if (collected) return normalizeDraft(collected);
        }
        try { return normalizeDraft(JSON.parse(card.dataset.emailDraft || '{}')); } catch (_) { return null; }
    }

    function readFields(card) {
        const to = card.querySelector('.email-draft-recipient')?.value;
        const subject = card.querySelector('.email-draft-subject')?.value;
        const body = card.querySelector('.email-draft-body-input')?.value;
        const tone = card.querySelector('.email-draft-tone')?.value;
        if (to === undefined && subject === undefined && body === undefined) return null;
        let base = {};
        try { base = JSON.parse(card.dataset.emailDraft || '{}'); } catch (_) {}
        return normalizeDraft({
            ...base,
            recipient: to ?? base.recipient,
            subject: subject ?? base.subject,
            body: body ?? base.body,
            tone: tone ?? base.tone,
        });
    }

    function writeFields(card, draft) {
        if (!card || !draft) return;
        const to = card.querySelector('.email-draft-recipient');
        const subject = card.querySelector('.email-draft-subject');
        const body = card.querySelector('.email-draft-body-input');
        const tone = card.querySelector('.email-draft-tone');
        const attachmentLabel = card.querySelector('.email-draft-attachment-label');
        const preview = card.querySelector('.email-draft-preview');
        if (to && !to.value.trim()) to.value = draft.recipient || '';
        if (subject && !subject.value.trim()) subject.value = draft.subject || '';
        if (body && body.value.trim() !== draft.body) body.value = draft.body || '';
        if (tone && draft.tone) tone.value = draft.tone;
        if (attachmentLabel && !draft.attachment_filename && !(draft.attachments || []).length) attachmentLabel.textContent = 'None';
        if (preview) preview.srcdoc = safePreview(draft.body || '');
        card.__emailDraft = draft;
        card.dataset.emailDraft = JSON.stringify(draft);
    }

    function safePreview(body) {
        const escaped = String(body || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;')
            .replace(/\n/g, '<br>');
        return '<!doctype html><html><body style="font-family:Arial,sans-serif;line-height:1.55;color:#111827;padding:16px;margin:0">' + escaped + '</body></html>';
    }

    function ensureTray() {
        let tray = document.getElementById('prompt-context-tray');
        if (tray) return tray;
        const prompt = document.getElementById('prompt');
        if (!prompt) return null;
        tray = document.createElement('div');
        tray.id = 'prompt-context-tray';
        tray.style.cssText = 'display:none;gap:8px;flex-wrap:wrap;margin:0 8px 8px;align-items:center;';
        prompt.parentElement?.insertBefore(tray, prompt);
        return tray;
    }

    function renderTray() {
        if (!appState) return;
        const tray = ensureTray();
        if (!tray) return;
        const drafts = (appState.attachedContexts || []).filter(ctx => ctx.kind === 'email_draft');
        tray.textContent = '';
        tray.style.display = drafts.length ? 'flex' : 'none';
        drafts.forEach(ctx => {
            let draft = ctx.draft;
            if (!draft) {
                try { draft = normalizeDraft(JSON.parse(String(ctx.text || '').replace(CONTEXT_MARKER, '') || '{}')); } catch (_) { draft = null; }
            }
            const chip = document.createElement('div');
            chip.className = 'prompt-context-chip email-draft-context-chip';
            chip.style.cssText = 'display:flex;align-items:center;gap:8px;max-width:100%;border:1px solid var(--glass-border);background:rgba(255,255,255,.07);border-radius:999px;padding:7px 10px;color:var(--text-main);font-size:.78rem;';
            const label = document.createElement('span');
            label.textContent = 'Email draft → ' + (draft?.recipient || 'recipient') + ' / ' + (draft?.subject || 'no subject');
            label.style.cssText = 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:520px;';
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.textContent = '×';
            remove.style.cssText = 'border:0;background:transparent;color:var(--text-sub);font-size:1rem;cursor:pointer;line-height:1;';
            remove.addEventListener('click', function () {
                const pos = appState.attachedContexts.indexOf(ctx);
                if (pos >= 0) appState.attachedContexts.splice(pos, 1);
                renderTray();
            });
            chip.append(label, remove);
            tray.appendChild(chip);
        });
    }

    function attachDraft(draft) {
        if (!appState) return false;
        const normalized = normalizeDraft(draft);
        if (!normalized) return false;
        if (!Array.isArray(appState.attachedContexts)) appState.attachedContexts = [];
        const text = CONTEXT_MARKER + JSON.stringify(normalized);
        const exists = appState.attachedContexts.some(ctx => ctx.kind === 'email_draft' && ctx.text === text);
        if (!exists) appState.attachedContexts.push({ kind: 'email_draft', text, draft: normalized });
        renderTray();
        document.getElementById('prompt')?.focus();
        return true;
    }

    function installDrop() {
        const prompt = document.getElementById('prompt');
        if (!prompt || prompt.dataset.emailDraftRepairDrop === 'true') return;
        prompt.dataset.emailDraftRepairDrop = 'true';
        prompt.addEventListener('dragover', function (event) {
            if (event.dataTransfer?.types?.includes(DRAFT_MIME) || event.dataTransfer?.types?.includes('text/plain')) {
                event.preventDefault();
                prompt.classList.add('email-draft-drop-active');
            }
        }, true);
        prompt.addEventListener('dragleave', function () { prompt.classList.remove('email-draft-drop-active'); }, true);
        prompt.addEventListener('drop', function (event) {
            const raw = event.dataTransfer?.getData(DRAFT_MIME) || event.dataTransfer?.getData('text/plain') || '';
            const draft = parseTransferText(raw);
            if (!draft) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            prompt.classList.remove('email-draft-drop-active');
            attachDraft(draft);
        }, true);
    }

    function repairCards(root) {
        const scope = root && typeof root.querySelectorAll === 'function' ? root : document;
        scope.querySelectorAll('.email-draft-card').forEach(function (card) {
            const draft = collectDraft(card);
            if (draft) writeFields(card, draft);
        });
    }

    function installDelegatedButtons() {
        if (!document.body || document.body.dataset.emailDraftRepairButtons === 'true') return;
        document.body.dataset.emailDraftRepairButtons = 'true';
        document.body.addEventListener('click', function (event) {
            const button = event.target?.closest?.('.email-draft-use-context-btn');
            if (!button) return;
            const card = button.closest('.email-draft-card');
            const draft = collectDraft(card);
            if (!draft) return;
            event.preventDefault();
            event.stopPropagation();
            writeFields(card, draft);
            attachDraft(draft);
        }, true);
    }

    function saveLocalCache() {
        if (!appState?.user?.email || !Array.isArray(appState.chats) || !appState.chats.length) return;
        let snapshot = '';
        try { snapshot = JSON.stringify(appState.chats); } catch (_) { return; }
        if (!snapshot || snapshot === lastSavedSnapshot) return;
        lastSavedSnapshot = snapshot;
        localStorage.setItem('helper_chats_v2_' + appState.user.email, snapshot);
        if (appState.activeId) localStorage.setItem('helper_active_chat_v2', appState.activeId);
    }

    function restoreVisibleChat() {
        if (!appState || !Array.isArray(appState.chats) || !appState.chats.length) return;
        const chatArea = document.getElementById('chat-area');
        if (chatArea?.querySelector('.msg')) return;
        const savedId = localStorage.getItem('helper_active_chat_v2');
        const target = appState.chats.find(chat => chat.id === savedId)
            || appState.chats.slice().sort((a, b) => Number(b.updated_at || b.updatedAt || 0) - Number(a.updated_at || a.updatedAt || 0))[0];
        if (target && typeof window.loadChat === 'function') window.loadChat(target.id);
    }

    function installStyle() {
        if (document.getElementById('email-draft-repair-style')) return;
        const style = document.createElement('style');
        style.id = 'email-draft-repair-style';
        style.textContent = '.u-msg .txt [id^="msg-text-"]{white-space:pre-wrap}.email-draft-drop-active{outline:1px solid var(--accent-blue);outline-offset:3px}';
        document.head.appendChild(style);
    }

    function tick() {
        installDrop();
        installDelegatedButtons();
        repairCards(document);
        restoreVisibleChat();
        saveLocalCache();
    }

    function initWithState(state) {
        appState = state;
        window.attachEmailDraftToPrompt = attachDraft;
        window.renderEmailDraftPromptTray = renderTray;
        installStyle();
        tick();
        new MutationObserver(function (mutations) {
            mutations.forEach(function (mutation) { repairCards(mutation.target); });
        }).observe(document.body, { childList: true, subtree: true });
        setInterval(tick, 1000);
        window.addEventListener('beforeunload', saveLocalCache);
        document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'hidden') saveLocalCache(); });
    }

    function init() {
        import('/static/js/state.js').then(function (module) {
            initWithState(module.state);
        }).catch(function (error) {
            console.warn('[EmailDraftRepair] state module failed:', error);
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
