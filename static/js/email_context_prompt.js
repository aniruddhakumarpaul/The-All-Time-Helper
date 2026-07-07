import { state } from './state.js';

const DRAFT_MIME = window.__EMAIL_DRAFT_MIME || 'application/x-helper-email-draft';
const CONTEXT_MARKER = 'EMAIL_DRAFT_CONTEXT:';
let lastSavedSnapshot = '';

function normalizeDraft(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const attachments = Array.isArray(raw.attachments) ? raw.attachments : [];
    const hasAttachmentPayload = Boolean(raw.attachment_content) || Boolean(raw.has_attachment_content) || attachments.length > 0;
    return {
        recipient: String(raw.recipient || raw.to || '').trim(),
        subject: String(raw.subject || '').trim(),
        body: String(raw.body || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim(),
        tone: String(raw.tone || 'modern').trim() || 'modern',
        attachment_content: raw.attachment_content || null,
        attachment_filename: hasAttachmentPayload ? String(raw.attachment_filename || '').trim() : '',
        attachment_type: raw.attachment_type || raw.content_type || raw.type || undefined,
        attachment_description: raw.attachment_description || undefined,
        attachments,
        has_attachment_content: Boolean(raw.attachment_content || raw.has_attachment_content),
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

function compactDraft(rawDraft) {
    if (typeof window.compactEmailDraftForPrompt === 'function') {
        const compact = window.compactEmailDraftForPrompt(rawDraft);
        if (compact) return compact;
    }
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

function draftFromTransfer(event) {
    const raw = event.dataTransfer?.getData(DRAFT_MIME)
        || event.dataTransfer?.getData('text/plain')
        || '';
    if (!raw) return null;
    if (typeof window.parseEmailDraftContext === 'function') {
        const parsed = window.parseEmailDraftContext(raw);
        if (parsed?.draft) return normalizeDraft(parsed.draft);
    }
    try {
        const cleaned = raw.startsWith(CONTEXT_MARKER) ? raw.slice(CONTEXT_MARKER.length) : raw;
        return normalizeDraft(JSON.parse(cleaned));
    } catch (_) {
        return null;
    }
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

function makeContextText(draft) {
    const compact = compactDraft(draft);
    return CONTEXT_MARKER + JSON.stringify(compact || normalizeDraft(draft) || {});
}

function renderTray() {
    const tray = ensureTray();
    if (!tray) return;
    const drafts = (state.attachedContexts || []).filter(ctx => ctx.kind === 'email_draft');
    tray.textContent = '';
    tray.style.display = drafts.length ? 'flex' : 'none';
    drafts.forEach(ctx => {
        let draft = ctx.draft;
        if (!draft) {
            try { draft = normalizeDraft(JSON.parse(ctx.text.replace(CONTEXT_MARKER, '') || '{}')); } catch (_) { draft = null; }
        }
        const chip = document.createElement('div');
        chip.className = 'prompt-context-chip email-draft-context-chip';
        chip.style.cssText = 'display:flex;align-items:center;gap:8px;max-width:100%;border:1px solid var(--glass-border);background:rgba(255,255,255,.07);border-radius:999px;padding:7px 10px;color:var(--text-main);font-size:.78rem;';
        const label = document.createElement('span');
        label.textContent = `Email draft → ${draft?.recipient || 'recipient'} / ${draft?.subject || 'no subject'}`;
        label.style.cssText = 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:520px;';
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.textContent = '×';
        remove.title = 'Remove attached email draft context';
        remove.style.cssText = 'border:0;background:transparent;color:var(--text-sub);font-size:1rem;cursor:pointer;line-height:1;';
        remove.addEventListener('click', () => {
            const pos = state.attachedContexts.indexOf(ctx);
            if (pos >= 0) state.attachedContexts.splice(pos, 1);
            renderTray();
        });
        chip.append(label, remove);
        tray.appendChild(chip);
    });
}

function attachEmailDraftToPrompt(rawDraft) {
    const draft = compactDraft(rawDraft);
    if (!draft) return false;
    if (!Array.isArray(state.attachedContexts)) state.attachedContexts = [];
    const text = makeContextText(draft);
    const exists = state.attachedContexts.some(ctx => ctx.kind === 'email_draft' && ctx.text === text);
    if (!exists) state.attachedContexts.push({ kind: 'email_draft', text, draft });
    renderTray();
    const prompt = document.getElementById('prompt');
    if (prompt) prompt.focus();
    return true;
}

function installPromptDrop() {
    const prompt = document.getElementById('prompt');
    if (!prompt || prompt.dataset.emailDraftDrop === 'true') return;
    prompt.dataset.emailDraftDrop = 'true';
    prompt.addEventListener('dragover', event => {
        if (event.dataTransfer?.types?.includes(DRAFT_MIME) || event.dataTransfer?.types?.includes('text/plain')) {
            event.preventDefault();
            prompt.classList.add('email-draft-drop-active');
        }
    }, true);
    prompt.addEventListener('dragleave', () => prompt.classList.remove('email-draft-drop-active'), true);
    prompt.addEventListener('drop', event => {
        const draft = draftFromTransfer(event);
        if (!draft) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        prompt.classList.remove('email-draft-drop-active');
        attachEmailDraftToPrompt(draft);
    }, true);
}

function saveLocalChatCache() {
    if (!state.user?.email || !Array.isArray(state.chats)) return;
    if (!state.chats.length) return;
    let snapshot = '';
    try { snapshot = JSON.stringify(state.chats); } catch (_) { return; }
    if (!snapshot || snapshot === lastSavedSnapshot) return;
    lastSavedSnapshot = snapshot;
    localStorage.setItem('helper_chats_v2_' + state.user.email, snapshot);
    if (state.activeId) localStorage.setItem('helper_active_chat_v2', state.activeId);
}

function installPromptWhitespaceStyle() {
    if (document.getElementById('prompt-whitespace-preserve-style')) return;
    const style = document.createElement('style');
    style.id = 'prompt-whitespace-preserve-style';
    style.textContent = '.u-msg .txt [id^="msg-text-"]{white-space:pre-wrap}.email-draft-drop-active{outline:1px solid var(--accent-blue);outline-offset:3px}';
    document.head.appendChild(style);
}

function init() {
    ensureTray();
    installPromptDrop();
    installPromptWhitespaceStyle();
    renderTray();
    setInterval(() => {
        installPromptDrop();
        saveLocalChatCache();
        if (!(state.attachedContexts || []).some(ctx => ctx.kind === 'email_draft')) renderTray();
    }, 1000);
    window.addEventListener('beforeunload', saveLocalChatCache);
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') saveLocalChatCache(); });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();

window.attachEmailDraftToPrompt = attachEmailDraftToPrompt;
window.renderEmailDraftPromptTray = renderTray;
window.__helperSaveLocalChatCache = saveLocalChatCache;
