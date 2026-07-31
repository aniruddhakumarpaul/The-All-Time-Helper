/**
 * ui.js — UI Controller Module
 * All DOM manipulation logic extracted from main_v3.js.
 */
import { state } from './state.js?v=210';
import { sortChatsNewestFirst } from './chat_sync.js?v=203';

const LOGO_DARK_DATA = "/static/img/logo.png";
const LOGO_LIGHT_DATA = "/static/img/logo(2).jpg";

const PREFERENCES_KEY = 'helper_preferences_v1';
const RESPONSE_STYLES = new Set(['adaptive', 'concise', 'deep', 'creative']);
const DEFAULT_PREFERENCES = Object.freeze({
    personalization: true,
    oneWord: false,
    english: true,
    persona: false,
    responseStyle: 'adaptive'
});
let toastTimer = null;

function readPreferences() {
    let stored = {};
    try {
        stored = JSON.parse(localStorage.getItem(PREFERENCES_KEY) || '{}') || {};
    } catch (_) {
        stored = {};
    }
    const legacyStyle = localStorage.getItem('helper_response_style_v1');
    const candidateStyle = stored.responseStyle || legacyStyle || DEFAULT_PREFERENCES.responseStyle;
    return {
        personalization: typeof stored.personalization === 'boolean' ? stored.personalization : DEFAULT_PREFERENCES.personalization,
        oneWord: typeof stored.oneWord === 'boolean' ? stored.oneWord : DEFAULT_PREFERENCES.oneWord,
        english: typeof stored.english === 'boolean' ? stored.english : DEFAULT_PREFERENCES.english,
        persona: typeof stored.persona === 'boolean' ? stored.persona : DEFAULT_PREFERENCES.persona,
        responseStyle: RESPONSE_STYLES.has(candidateStyle) ? candidateStyle : DEFAULT_PREFERENCES.responseStyle
    };
}

function preferencesFromControls() {
    return {
        personalization: Boolean(document.getElementById('t-pers')?.classList.contains('on')),
        oneWord: Boolean(document.getElementById('t-word')?.classList.contains('on')),
        english: Boolean(document.getElementById('t-eng')?.classList.contains('on')),
        persona: Boolean(document.getElementById('persona-toggle')?.checked),
        responseStyle: RESPONSE_STYLES.has(state.responseStyle) ? state.responseStyle : DEFAULT_PREFERENCES.responseStyle
    };
}

function persistPreferences() {
    const preferences = preferencesFromControls();
    state.set('responseStyle', preferences.responseStyle);
    try {
        localStorage.setItem(PREFERENCES_KEY, JSON.stringify(preferences));
        localStorage.setItem('helper_response_style_v1', preferences.responseStyle);
    } catch (error) {
        console.warn('Preferences could not be persisted:', error);
    }
    return preferences;
}

function applySwitchState(id, active) {
    const control = document.getElementById(id);
    if (!control) return;
    control.classList.toggle('on', Boolean(active));
    control.setAttribute('aria-checked', String(Boolean(active)));
}

function loadPreferences() {
    const preferences = readPreferences();
    applySwitchState('t-pers', preferences.personalization);
    applySwitchState('t-word', preferences.oneWord);
    applySwitchState('t-eng', preferences.english);
    const persona = document.getElementById('persona-toggle');
    if (persona) persona.checked = preferences.persona;
    const style = document.getElementById('response-style-setting');
    if (style) style.value = preferences.responseStyle;
    state.set('responseStyle', preferences.responseStyle);
    return preferences;
}

function setResponseStyle(value) {
    const normalized = RESPONSE_STYLES.has(value) ? value : DEFAULT_PREFERENCES.responseStyle;
    state.set('responseStyle', normalized);
    const control = document.getElementById('response-style-setting');
    if (control && control.value !== normalized) control.value = normalized;
    persistPreferences();
    notify('Response style set to ' + normalized + '.', 'success', 1800);
}

function setPersonaEnabled(enabled) {
    const control = document.getElementById('persona-toggle');
    const item = document.querySelector('.persona-switch-item');
    if (control) control.checked = Boolean(enabled);
    if (item) item.classList.toggle('persona-active', Boolean(enabled));
    persistPreferences();
}

function notify(message, tone = 'info', duration = 3200) {
    const region = document.getElementById('app-toast-region');
    if (!region || !message) return;
    window.clearTimeout(toastTimer);
    region.textContent = String(message);
    region.dataset.tone = tone;
    region.classList.remove('is-visible');
    window.requestAnimationFrame(() => region.classList.add('is-visible'));
    toastTimer = window.setTimeout(() => region.classList.remove('is-visible'), duration);
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function safeDomId(value) {
    return String(value ?? '').replace(/[^a-zA-Z0-9_-]/g, '_');
}

function normalizePreviewImageSource(value) {
    const raw = typeof value === 'object' && value !== null
        ? value.data || value.content || value.url || ''
        : String(value || '');
    if (!raw) return { src: '', payload: '' };
    if (/^(?:data:|blob:|https?:|\/static\/)/i.test(raw)) return { src: raw, payload: raw };
    return { src: `data:image/png;base64,${raw}`, payload: raw };
}

function smartFocus(id) {
    if (window.innerWidth > 850) {
        const el = document.getElementById(id);
        if (el) el.focus();
    }
}

function switchAuth(t) {
    ['login', 'signup', 'otp'].forEach(f => {
        const form = document.getElementById(f + '-form');
        if (!form) return;
        const active = f === t;
        form.hidden = !active;
        form.style.display = active ? 'block' : 'none';
    });
    if (t === 'login') document.getElementById('l-email').focus();
    if (t === 'signup') document.getElementById('s-name').focus();
    if (t === 'otp') document.getElementById('v-otp').focus();
}

function updUI() {
    if (state.user) {
        const nameStr = state.user.name || 'Human';
        const initial = nameStr.charAt(0).toUpperCase();
        const sbGreet = document.getElementById('sidebar-greet');
        if (sbGreet) sbGreet.innerText = 'Hello, ' + nameStr;
        const cGreet = document.getElementById('center-greet');
        if (cGreet) {
            cGreet.textContent = 'Hello, ';
            const span = document.createElement('span');
            span.style.background = 'var(--greet-grad)';
            span.style.backgroundClip = 'text';
            span.style.webkitBackgroundClip = 'text';
            span.style.webkitTextFillColor = 'transparent';
            span.textContent = nameStr;
            cGreet.appendChild(span);
        }
        const uInfo = document.getElementById('user-info');
        if (uInfo) uInfo.innerText = state.user.email;
        const settingsInfo = document.getElementById('settings-user-info');
        if (settingsInfo) settingsInfo.innerText = nameStr + ' / ' + state.user.email;
        const avCont = document.getElementById('sidebar-av-container');
        if (avCont) avCont.innerHTML = `<div class="av u-av" style="width: 32px; height: 32px; font-size: 0.8rem;"><span class="initial-letter">${escapeHtml(initial)}</span><span class="full-name">${escapeHtml(nameStr)}</span></div>`;
    }
}

function signOut() {
    localStorage.removeItem('helper_user_v2');
    localStorage.removeItem('helper_token_v2');
    localStorage.removeItem('helper_active_chat_v2');
    localStorage.removeItem('helper_active_modal_v2');
    location.reload();
}

function toggleDropdown() {
    const menu = document.getElementById('model-menu');
    const control = document.getElementById('model-toggle');
    if (!menu) return;
    const restoreFocus = menu.contains(document.activeElement);
    const expanded = menu.classList.toggle('active');
    control?.setAttribute('aria-expanded', String(expanded));
    if (!expanded && restoreFocus) control?.focus();
}

function selModel(id, name) {
    state.selectedModel = id;
    localStorage.setItem('helper_model_v3', id);
    const menu = document.getElementById('model-menu');
    const restoreFocus = menu?.contains(document.activeElement);
    const option = document.querySelector(`[data-model-id="${CSS.escape(id)}"]`);
    document.querySelectorAll('#model-menu [data-model-id]').forEach(candidate => {
        candidate.setAttribute('aria-selected', String(candidate.dataset.modelId === id));
    });
    const displayName = name || option?.dataset.modelName || id;
    document.getElementById('active-model-name').innerText = displayName;
    const privacy = document.getElementById('model-privacy-label');
    if (privacy && option?.dataset.modelMode) privacy.textContent = option.dataset.modelMode;
    window.HelperExperience?.presentModel(id, displayName, option?.dataset.modelMode);
    if (menu) menu.classList.remove('active');
    const control = document.getElementById('model-toggle');
    control?.setAttribute('aria-expanded', 'false');
    if (restoreFocus) control?.focus();
}

function toggleSidebar() {
    const sb = document.getElementById('sidebar');
    const scrim = document.getElementById('sidebar-scrim');
    const control = document.getElementById('mobile-menu-btn');
    if (!sb) return;
    const isOpen = sb.classList.toggle('open');
    document.body.classList.toggle('sidebar-open', isOpen);
    if (control) {
        control.setAttribute('aria-expanded', String(isOpen));
        control.setAttribute('aria-label', isOpen ? 'Close navigation' : 'Open navigation');
    }
    sb.style.transform = '';
    if (scrim) { scrim.style.opacity = ''; scrim.style.display = ''; }
    if (isOpen) history.pushState({ view: 'sidebar' }, "");
}

function openSettings() {
    const modal = document.getElementById('settings-modal');
    if (!modal) return;
    modal.style.display = 'flex';
    localStorage.setItem('helper_active_modal_v2', 'settings');
    history.pushState({ view: 'settings' }, "");
    window.HelperDialogs?.sync();
}

function closeSettings() {
    const modal = document.getElementById('settings-modal');
    if (modal) modal.style.display = 'none';
    localStorage.removeItem('helper_active_modal_v2');
    window.HelperDialogs?.sync();
    if (!window.HelperDialogs) document.getElementById('prompt')?.focus();
}

function toggleSet(id) {
    const control = document.getElementById(id);
    if (!control) return;
    const active = control.classList.toggle('on');
    control.setAttribute('aria-checked', String(active));
    persistPreferences();
    notify((control.getAttribute('aria-label') || 'Setting') + (active ? ' enabled.' : ' disabled.'), 'success', 1800);
}

function addMsg(r, c, i, idx, mName, isMasked = false, attachments = []) {
    const div = document.createElement('div');
    div.className = `msg ${r}-msg entering`;
    div.setAttribute('role', 'article');
    div.setAttribute('aria-label', r === 'u' ? 'Your message' : 'The All Time Helper response');
    setTimeout(() => div.classList.remove('entering'), 600);
    const name = state.user ? state.user.name : 'Human';
    const initial = name.charAt(0).toUpperCase();
    const safeName = escapeHtml(name);
    const safeInitial = escapeHtml(initial);
    const safeIdx = safeDomId(idx);
    const avatarHtml = r === 'u'
        ? `<div class="av u-av"><span class="initial-letter">${safeInitial}</span><span class="full-name">${safeName}</span></div>`
        : `<div class="av b-av" id="bot-av-${safeIdx}">
            <div class="logo-img-wrapper">
                <svg class="orb-svg" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
                <defs>
                    <linearGradient id="orbGrad-${safeIdx}" x1="0%" y1="0%" x2="100%" y2="100%">
                        <stop offset="0%" style="stop-color: var(--orb-color-1); stop-opacity: 1" />
                        <stop offset="100%" style="stop-color: var(--orb-color-2); stop-opacity: 1" />
                    </linearGradient>
                    <filter id="orbGlow-${safeIdx}" x="-50%" y="-50%" width="200%" height="200%">
                        <feGaussianBlur in="SourceGraphic" stdDeviation="5" />
                    </filter>
                </defs>
                <circle cx="50%" cy="50%" r="40" fill="url(#orbGrad-${safeIdx})" filter="url(#orbGlow-${safeIdx})" />
                <circle cx="50%" cy="50%" r="25" fill="url(#orbGrad-${safeIdx})" />
            </svg>
            </div>
            <div class="bot-bubble" id="bot-bubble-${safeIdx}">I am great!</div>
           </div>`;

    let content = r === 'b' ? window.renderMarkdown(c) : escapeHtml(c);
    if (r === 'u' && isMasked) content = '\u2022'.repeat(Math.max(8, String(c || '').length));

    let tools = '';
    if (!isMasked) {
        const editTool = r === 'u' && idx !== undefined
            ? `<button class="tool-icon" type="button" data-edit-index="${Number(idx)}" title="Edit prompt" aria-label="Edit prompt">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
               </button>`
            : '';
        tools = `<div class="msg-tools" aria-label="Message actions">
                    <button class="tool-icon context-drag-handle" type="button" draggable="true" data-context-drag-handle title="Drag this message into the composer as context" aria-label="Drag message into composer as context">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="8" cy="7" r="1.5"></circle><circle cx="16" cy="7" r="1.5"></circle><circle cx="8" cy="12" r="1.5"></circle><circle cx="16" cy="12" r="1.5"></circle><circle cx="8" cy="17" r="1.5"></circle><circle cx="16" cy="17" r="1.5"></circle></svg>
                    </button>
                    ${editTool}
                 </div>`;
    }

    let watermark = '';
    if (r === 'b' && mName) {
        watermark = `<div class="model-watermark" style="font-size: 0.7rem; color: var(--accent-blue); opacity: 0.8; margin-top: 12px; display: flex; align-items: center; gap: 6px; font-weight: 600; font-family: 'Outfit', sans-serif; letter-spacing: 0.3px; border-top: 1px solid rgba(255,255,255,0.05); padding-top: 8px;">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="opacity: 0.7;"><circle cx="12" cy="12" r="10"></circle><path d="M12 8v8M8 12h8"></path></svg>
            <span style="text-transform: uppercase; font-size: 0.65rem;">${escapeHtml(mName)}</span>
        </div>`;
    }

    const preview = normalizePreviewImageSource(Array.isArray(i) ? i[0] : i);
    const attachmentNames = (Array.isArray(attachments) ? attachments : [])
        .map(item => item?.filename || item?.name)
        .filter(Boolean);
    const attachmentChips = attachmentNames
        .map(name => '<span class="message-attachment-chip">' + escapeHtml(name) + '</span>')
        .join('');
    const attachmentHtml = attachmentNames.length
        ? '<div class="message-attachments" aria-label="Attached files">' + attachmentChips + '</div>'
        : '';
    div.innerHTML = `
        <div class="av-wrap">
            ${avatarHtml}
            <div class="av-label" style="font-size: 0.8rem; color: var(--text-sub); font-weight: 600; letter-spacing: 0.5px;">
                ${r === 'u' ? safeName : 'THE ALL TIME HELPER'}
            </div>
        </div>
        <div class="txt" draggable="false">
            <div id="msg-text-${safeIdx}">${content}</div>
            ${attachmentHtml}
            ${preview.src ? `<div class="chat-img-preview-container" data-preview-src="${escapeHtml(preview.src)}" data-preview-payload="${escapeHtml(preview.payload)}"><img src="${escapeHtml(preview.src)}" class="chat-img-preview"></div>` : ''}
            ${watermark}
            ${tools}
        </div>
    `;

    document.getElementById('chat-area').appendChild(div);
    const textContainer = div.querySelector('.txt');
    textContainer?.addEventListener('dragstart', event => {
        if (event.target?.closest?.('[data-context-drag-handle]')) return;
        if (!window.isGDown) { event.preventDefault(); return; }
        handleDragStart(event);
    });
    textContainer?.addEventListener('dragend', handleDragEnd);
    div.querySelector('[data-edit-index]')?.addEventListener('click', event => startEditPrompt(Number(idx), event.currentTarget));

    div.querySelector('[data-preview-src]')?.addEventListener('click', event => openImageModal(event.currentTarget.dataset.previewSrc));
    if (r === 'b') window.hydrateRenderedMarkdown?.(div);
    div.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
    document.getElementById('chat-area').scrollTop = document.getElementById('chat-area').scrollHeight;
    if (mName) console.log(`DEBUG: Rendered watermark for ${mName}`);
    if (r === 'b') checkAuthMode();
    return div.querySelector(`#msg-text-${safeIdx}`);
}

function renderHist() {
    if (state.isRenaming) return;
    const list = document.getElementById('history-list'); if (!list) return;
    list.textContent = '';
    const sorted = sortChatsNewestFirst(state.chats);
    sorted.forEach(c => {
        const title = (c.title || 'New Chat').toLowerCase();
        if (state.currentSearch && !title.includes(state.currentSearch.toLowerCase())) return;
        const div = document.createElement('div');
        div.className = `history-item ${c.id === state.activeId ? 'active-chat' : ''} ${c.pinned ? 'pinned' : ''}`;

        const titleSpan = document.createElement('span');
        titleSpan.className = 'chat-title-text';
        titleSpan.id = `t-${safeDomId(c.id)}`;
        titleSpan.textContent = c.title || 'New Chat';
        titleSpan.setAttribute('role', 'button');
        titleSpan.setAttribute('aria-label', 'Open conversation ' + (c.title || 'New Chat'));
        titleSpan.tabIndex = 0;
        titleSpan.addEventListener('keydown', event => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            event.preventDefault();
            window.loadChat(c.id);
        });
        div.appendChild(titleSpan);

        const actions = document.createElement('div');
        actions.className = 'history-actions';

        const pinBtn = document.createElement('button');
        pinBtn.type = 'button';
        pinBtn.className = `del-chat-btn pin-btn ${c.pinned ? 'active' : ''}`;
        pinBtn.title = c.pinned ? 'Unpin Chat' : 'Pin Chat';
        pinBtn.setAttribute('aria-label', pinBtn.title);
        pinBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v2a2 2 0 0 0 1.27 1.87L11 15.3V21l2-2 2 2v-5.7l6.73-3.43A2 2 0 0 0 21 10z"></path></svg>';
        pinBtn.addEventListener('click', event => { event.stopPropagation(); window.togglePin(c.id); });

        const renameBtn = document.createElement('button');
        renameBtn.type = 'button';
        renameBtn.className = 'del-chat-btn';
        renameBtn.title = 'Rename Chat';
        renameBtn.setAttribute('aria-label', renameBtn.title);
        renameBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>';
        renameBtn.addEventListener('click', event => startRename(c.id, event));

        const deleteBtn = document.createElement('button');
        deleteBtn.type = 'button';
        deleteBtn.className = 'del-chat-btn';
        deleteBtn.title = 'Delete Chat';
        deleteBtn.setAttribute('aria-label', deleteBtn.title);
        deleteBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"></path><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>';
        deleteBtn.addEventListener('click', event => { event.stopPropagation(); showDeleteConfirm(c.id, event); });

        actions.append(pinBtn, renameBtn, deleteBtn);
        div.appendChild(actions);
        div.addEventListener('click', e => { if (!e.target.closest('.del-chat-btn')) window.loadChat(c.id); });
        list.appendChild(div);
    });
}

function startRename(id, e) {
    e.stopPropagation();
    if (state.isRenaming) return;
    state.isRenaming = true;
    const span = document.getElementById('t-' + safeDomId(id));
    if (!span || span.querySelector('input')) return;
    const old = span.innerText;
    const anchor = span.closest('.history-item') || span;
    span.textContent = '';
    span.classList.add('is-renaming');

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'rename-in rename-popout';
    input.value = old;
    input.size = Math.max(12, old.length + 2);
    input.style.width = String(Math.max(12, old.length + 2)) + 'ch';
    input.id = 'edit-' + safeDomId(id);
    input.setAttribute('aria-label', 'Rename conversation');
    input.addEventListener('click', event => event.stopPropagation());
    document.body.appendChild(input);

    const reposition = () => {
        const rect = anchor.getBoundingClientRect();
        const available = Math.max(180, window.innerWidth - rect.right - 24);
        input.style.maxWidth = available + 'px';
        input.style.left = Math.min(rect.right + 12, window.innerWidth - input.offsetWidth - 12) + 'px';
        input.style.top = Math.max(12, Math.min(rect.top, window.innerHeight - input.offsetHeight - 12)) + 'px';
    };
    const cleanup = () => {
        document.getElementById('history-list')?.removeEventListener('scroll', reposition);
        window.removeEventListener('resize', reposition);
        input.remove();
    };
    const finish = () => {
        const value = input.value;
        cleanup();
        saveRename(id, value);
    };
    document.getElementById('history-list')?.addEventListener('scroll', reposition, { passive: true });
    window.addEventListener('resize', reposition);
    input.addEventListener('blur', finish, { once: true });
    reposition();
    input.focus();
    input.select();
    input.onkeydown = (ev) => {
        if (ev.key === 'Enter') { ev.preventDefault(); ev.stopPropagation(); input.blur(); }
        if (ev.key === 'Escape') {
            ev.preventDefault();
            ev.stopPropagation();
            cleanup();
            state.isRenaming = false;
            renderHist();
        }
    };
}

function saveRename(id, val) {
    if (!state.isRenaming) return;
    const chat = state.chats.find(c => c.id === id);
    if (chat && val.trim()) {
        state.updateChat(id, { title: val.trim() });
        window.requestChatPersist?.();
    }
    state.isRenaming = false;
    renderHist();
}

function filterHist(q) { state.currentSearch = q; renderHist(); }

function checkAuthMode() {
    console.log("DEBUG: checkAuthMode running for activeId:", state.activeId);
    const chat = state.chats.find(c => c.id === state.activeId);
    const promptIn = document.getElementById('prompt');
    if (!chat || !promptIn) {
        console.warn("DEBUG: checkAuthMode failed - no chat or promptEl");
        const allMsgs = document.querySelectorAll('.b-msg .txt');
        if (allMsgs.length > 0) {
            const lastTxt = allMsgs[allMsgs.length - 1].innerText.toLowerCase();
            if (["auth_required", "admin key", "provide your key"].some(kw => lastTxt.includes(kw))) {
                applyAuthUI(promptIn);
                return;
            }
        }
        return;
    }
    const lastMsg = chat.ms.length > 0 ? chat.ms[chat.ms.length - 1] : null;
    const authKeywords = ["please provide your admin key", "enter your admin_key", "provide the password", "authorize with your key", "auth_required", "admin key is missing", "incorrect admin key", "provide your admin key"];
    const needsAuth = lastMsg && lastMsg.r === 'b' && authKeywords.some(kw => lastMsg.c.toLowerCase().includes(kw));

    if (needsAuth) {
        applyAuthUI(promptIn);
    } else {
        promptIn.placeholder = "Ask me anything...";
        promptIn.classList.remove('auth-waiting');
    }
}

function applyAuthUI(promptIn) {
    console.log("DEBUG: Auth required detected! Applying UI...");
    promptIn.placeholder = "ENTER ADMIN KEY TO AUTHORIZE THIS ACTION...";
    promptIn.classList.add('auth-waiting');
    if (window.jiggleLogo) window.jiggleLogo();
    smartFocus('prompt');
}

function startEditPrompt(idx, btn) {
    console.log("DEBUG: Editing prompt", idx);
    const chat = state.chats.find(c => c.id === state.activeId);
    const msg = chat.ms[idx];
    const txtDiv = document.getElementById(`msg-text-${safeDomId(idx)}`);
    if (!txtDiv) { console.error("DEBUG: txtDiv not found"); return; }
    txtDiv.textContent = '';
    const textarea = document.createElement('textarea');
    textarea.className = 'edit-area';
    textarea.setAttribute('aria-label', 'Edit message');
    textarea.value = msg.c;
    const controls = document.createElement('div');
    controls.className = 'edit-controls';
    const submit = document.createElement('button');
    submit.type = 'button';
    submit.className = 'auth-btn edit-btn';
    submit.textContent = 'Save & Submit';
    submit.addEventListener('click', () => window.submitEdit?.(Number(idx), txtDiv));
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'auth-btn edit-btn edit-btn-cancel';
    cancel.textContent = 'Cancel';
    cancel.addEventListener('click', () => cancelEdit(Number(idx)));
    controls.append(submit, cancel);
    txtDiv.append(textarea, controls);
}

function cancelEdit(idx) {
    const chat = state.chats.find(c => c.id === state.activeId);
    if (!chat || !chat.ms[idx]) return;
    const msg = chat.ms[idx];
    const txtDiv = document.getElementById(`msg-text-${safeDomId(idx)}`);
    if (txtDiv) {
        if (msg.r === 'b') {
            txtDiv.innerHTML = window.renderMarkdown(msg.c);
            window.hydrateRenderedMarkdown?.(txtDiv);
        } else {
            txtDiv.innerHTML = escapeHtml(msg.c);
        }
        if (msg.r === 'b') txtDiv.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
    }
}

function previewImg(i) {
    const files = Array.from(i.files || []);
    if (!files.length) return;
    for (const url of state.previewBlobUrls || []) URL.revokeObjectURL(url);
    state.previewBlobUrls = [];
    state.currentBlobUrl = null;
    state.currentImg = null;
    const area = document.getElementById('img-preview-area');
    if (!area) return;
    area.style.display = 'flex';
    area.textContent = '';
    let firstImage = null;
    files.forEach(file => {
        const wrapper = document.createElement('div');
        wrapper.className = 'img-thumb-wrap attachment-preview-item';
        wrapper.title = file.name;
        if (file.type.startsWith('image/')) {
            const image = document.createElement('img');
            const previewUrl = URL.createObjectURL(file);
            state.previewBlobUrls.push(previewUrl);
            image.src = previewUrl;
            image.className = 'img-thumb';
            wrapper.appendChild(image);
            if (!firstImage) firstImage = file;
        } else {
            const label = document.createElement('span');
            label.className = 'attachment-file-label';
            label.textContent = (file.type === 'application/pdf' ? 'PDF' : 'FILE') + '  ' + file.name;
            wrapper.appendChild(label);
        }
        area.appendChild(wrapper);
    });
    if (firstImage) {
        state.currentBlobUrl = state.previewBlobUrls[0] || null;
        const reader = new FileReader();
        reader.onload = event => { state.currentImg = String(event.target.result || '').split(',')[1] || null; };
        reader.readAsDataURL(firstImage);
        selModel('helper-auto', 'Helper Auto');
    }
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'img-remove-btn';
    remove.setAttribute('aria-label', 'Remove attached files');
    remove.textContent = 'x';
    remove.addEventListener('click', clearImgPreview);
    area.appendChild(remove);
}

function clearImgPreview() {
    for (const url of state.previewBlobUrls || []) URL.revokeObjectURL(url);
    state.previewBlobUrls = [];
    state.currentBlobUrl = null; state.currentImg = null;
    state.replaceCurrentImages([]);
    state.setPendingImageUploads(null);
    document.getElementById('img-in').value = '';
    const area = document.getElementById('img-preview-area');
    area.style.display = 'none'; area.innerHTML = '';
}

function showDeleteConfirm(id, e) {
    if (e) e.stopPropagation(); state.chatToDelete = id;
    document.getElementById('delete-confirm-modal').style.display = 'flex';
    window.HelperDialogs?.sync();
}

function closeDeleteConfirm() {
    document.getElementById('delete-confirm-modal').style.display = 'none';
    window.HelperDialogs?.sync();
    state.chatToDelete = null;
}

function openImageModal(src) {
    const modal = document.getElementById('image-modal');
    const image = document.getElementById('modal-img');
    if (!modal || !image) return;
    image.src = src;
    image.classList.remove('is-zoomed');
    modal.style.display = 'flex';
    setTimeout(() => {
        modal.classList.add('active');
        window.HelperDialogs?.sync();
    }, 10);
    history.pushState({ view: 'image' }, "");
}

function closeImageModal() {
    const modal = document.getElementById('image-modal');
    const image = document.getElementById('modal-img');
    if (!modal) return;
    modal.classList.remove('active');
    image?.classList.remove('is-zoomed');
    setTimeout(() => {
        modal.style.display = 'none';
        window.HelperDialogs?.sync();
    }, 300);
}

function showNeuralContext(results, explanation) {
    const card = document.getElementById('neural-context-card');
    const cont = document.getElementById('context-results');
    const scrim = document.getElementById('neural-scrim');
    if (!card || !cont || !scrim) return;
    cont.textContent = '';
    if (explanation) {
        const box = document.createElement('div'); box.className = 'neural-insight-box';
        const header = document.createElement('div'); header.className = 'insight-header'; header.textContent = 'Neural Insight';
        const body = document.createElement('div'); body.className = 'insight-text'; body.textContent = explanation;
        box.append(header, body);
        cont.appendChild(box);
    }
    const label = document.createElement('span'); label.className = 'source-label'; label.innerText = 'Technical Source Snippets'; cont.appendChild(label);
    if (!results?.length) {
        const empty = document.createElement('p');
        empty.style.textAlign = 'center';
        empty.style.color = 'var(--text-sub)';
        empty.style.padding = '20px';
        empty.textContent = 'No direct neural links found.';
        cont.appendChild(empty);
    } else results.forEach(res => {
        const div = document.createElement('div'); div.className = 'context-snippet';
        const meta = document.createElement('span'); meta.className = 'context-meta'; meta.textContent = res.metadata?.type || 'DOCUMENT';
        const content = document.createElement('div');
        content.style.maxHeight = '150px';
        content.style.overflowY = 'auto';
        content.style.fontSize = '0.85rem';
        content.style.color = 'var(--text-main)';
        content.textContent = res.content || '';
        div.append(meta, content);
        cont.appendChild(div);
    });
    card.classList.add('active'); scrim.classList.add('active');
    window.HelperDialogs?.sync();
}

function closeNeuralContext() {
    const card = document.getElementById('neural-context-card');
    const scrim = document.getElementById('neural-scrim');
    if (card) card.classList.remove('active');
    if (scrim) scrim.classList.remove('active');
    window.HelperDialogs?.sync();
}

function setThemeUI(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    document.body.setAttribute('data-theme', theme);
    const logo = document.getElementById('main-logo-img');
    const isDark = theme === 'dark';
    if (logo) logo.src = isDark ? LOGO_DARK_DATA : LOGO_LIGHT_DATA;
    const favicon = document.getElementById('app-favicon');
    if (favicon) {
        favicon.href = isDark ? LOGO_DARK_DATA : LOGO_LIGHT_DATA;
        favicon.type = isDark ? 'image/png' : 'image/jpeg';
    }
}

function handleDragStart(e) {
    e.dataTransfer.setData('text/plain', e.currentTarget.innerText);
    e.currentTarget.parentElement.classList.add('dragging');
    document.getElementById('mascot-container')?.classList.add('mascot-drop-active');
}

function handleDragEnd(e) {
    e.currentTarget.parentElement.classList.remove('dragging');
    document.getElementById('mascot-container')?.classList.remove('mascot-drop-active');
}

const ui = {
    smartFocus, switchAuth, updUI, signOut, toggleDropdown, selModel,
    toggleSidebar, openSettings, closeSettings, toggleSet,
    loadPreferences, persistPreferences, setResponseStyle, setPersonaEnabled, notify,
    addMsg, renderHist, startRename, saveRename, filterHist,
    checkAuthMode, startEditPrompt, cancelEdit,
    previewImg, clearImgPreview,
    showDeleteConfirm, closeDeleteConfirm,
    openImageModal, closeImageModal,
    showNeuralContext, closeNeuralContext,
    setThemeUI, handleDragStart, handleDragEnd, normalizePreviewImageSource
};

export { ui };
