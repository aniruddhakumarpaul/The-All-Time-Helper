/**
 * ui.js — UI Controller Module
 * All DOM manipulation logic extracted from main_v3.js.
 */
import { state } from './state.js';

const LOGO_DATA = "/static/img/logo.png";
const LOGO_LIGHT_DATA = "/static/img/logo(2).jpg";

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

function smartFocus(id) {
    if (window.innerWidth > 850) {
        const el = document.getElementById(id);
        if (el) el.focus();
    }
}

function switchAuth(t) {
    ['login', 'signup', 'otp'].forEach(f => { document.getElementById(f + '-form').style.display = (f === t ? 'block' : 'none'); });
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
    if (menu) menu.classList.toggle('active');
}

function selModel(id, name) {
    state.selectedModel = id;
    document.getElementById('active-model-name').innerText = name;
    const menu = document.getElementById('model-menu');
    if (menu) menu.classList.remove('active');
}

function toggleSidebar() {
    const sb = document.getElementById('sidebar');
    const scrim = document.getElementById('sidebar-scrim');
    const isOpen = sb.classList.toggle('open');
    document.body.classList.toggle('sidebar-open', isOpen);
    if (sb) sb.style.transform = '';
    if (scrim) { scrim.style.opacity = ''; scrim.style.display = ''; }
    if (isOpen) history.pushState({ view: 'sidebar' }, "");
}

function openSettings() {
    document.getElementById('settings-modal').style.display = 'flex';
    localStorage.setItem('helper_active_modal_v2', 'settings');
    history.pushState({ view: 'settings' }, "");
}

function closeSettings() {
    document.getElementById('settings-modal').style.display = 'none';
    localStorage.removeItem('helper_active_modal_v2');
    document.getElementById('prompt').focus();
}

function toggleSet(id) { document.getElementById(id).classList.toggle('on'); }

function addMsg(r, c, i, idx, mName, isMasked = false) {
    const div = document.createElement('div');
    div.className = `msg ${r}-msg entering`;
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
    if (r === 'u' && isMasked) content = '•'.repeat(Math.max(8, String(c || '').length));

    let tools = '';
    if (r === 'u' && idx !== undefined && !isMasked) {
        tools = `<div class="msg-tools">
                    <div class="tool-icon" onclick="startEditPrompt(${Number(idx)}, this)" title="Edit Prompt">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
                    </div>
                 </div>`;
    }

    let watermark = '';
    if (r === 'b' && mName) {
        watermark = `<div class="model-watermark" style="font-size: 0.7rem; color: var(--accent-blue); opacity: 0.8; margin-top: 12px; display: flex; align-items: center; gap: 6px; font-weight: 600; font-family: 'Outfit', sans-serif; letter-spacing: 0.3px; border-top: 1px solid rgba(255,255,255,0.05); padding-top: 8px;">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="opacity: 0.7;"><circle cx="12" cy="12" r="10"></circle><path d="M12 8v8M8 12h8"></path></svg>
            <span style="text-transform: uppercase; font-size: 0.65rem;">${escapeHtml(mName)}</span>
        </div>`;
    }

    div.innerHTML = `
        <div class="av-wrap">
            ${avatarHtml}
            <div class="av-label" style="font-size: 0.8rem; color: var(--text-sub); font-weight: 600; letter-spacing: 0.5px;">
                ${r === 'u' ? safeName : 'THE ALL TIME HELPER'}
            </div>
        </div>
        <div class="txt" draggable="false" ondragstart="if(!window.isGDown) { event.preventDefault(); return false; } handleDragStart(event, this.innerText)" ondragend="handleDragEnd(event)">
            <div id="msg-text-${safeIdx}">${content}</div>
            ${i ? `<div class="chat-img-preview-container" onclick="openImageModal('data:image/png;base64,${escapeHtml(i)}')"><img src="data:image/png;base64,${escapeHtml(i)}" class="chat-img-preview"></div>` : ''}
            ${watermark}
            ${tools}
        </div>
    `;

    document.getElementById('chat-area').appendChild(div);
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
    const sorted = state.chats.slice().reverse().sort((a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0));
    sorted.forEach(c => {
        const title = (c.title || 'New Chat').toLowerCase();
        if (state.currentSearch && !title.includes(state.currentSearch.toLowerCase())) return;
        const div = document.createElement('div');
        div.className = `history-item ${c.id === state.activeId ? 'active-chat' : ''} ${c.pinned ? 'pinned' : ''}`;

        const titleSpan = document.createElement('span');
        titleSpan.className = 'chat-title-text';
        titleSpan.id = `t-${safeDomId(c.id)}`;
        titleSpan.textContent = c.title || 'New Chat';
        div.appendChild(titleSpan);

        const actions = document.createElement('div');
        actions.className = 'history-actions';

        const pinBtn = document.createElement('button');
        pinBtn.className = `del-chat-btn pin-btn ${c.pinned ? 'active' : ''}`;
        pinBtn.title = c.pinned ? 'Unpin Chat' : 'Pin Chat';
        pinBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v2a2 2 0 0 0 1.27 1.87L11 15.3V21l2-2 2 2v-5.7l6.73-3.43A2 2 0 0 0 21 10z"></path></svg>';
        pinBtn.addEventListener('click', event => { event.stopPropagation(); window.togglePin(c.id); });

        const renameBtn = document.createElement('button');
        renameBtn.className = 'del-chat-btn';
        renameBtn.title = 'Rename Chat';
        renameBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>';
        renameBtn.addEventListener('click', event => startRename(c.id, event));

        const deleteBtn = document.createElement('button');
        deleteBtn.className = 'del-chat-btn';
        deleteBtn.title = 'Delete Chat';
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
    state.isRenaming = true;
    const span = document.getElementById(`t-${safeDomId(id)}`);
    if (!span || span.querySelector('input')) return;
    const old = span.innerText;
    span.textContent = '';
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'rename-in';
    input.value = old;
    input.id = `edit-${safeDomId(id)}`;
    input.addEventListener('click', event => event.stopPropagation());
    span.appendChild(input);
    input.focus();
    input.onblur = () => saveRename(id, input.value);
    input.onkeydown = (ev) => {
        if (ev.key === 'Enter') { ev.stopPropagation(); saveRename(id, input.value); }
        if (ev.key === 'Escape') { ev.stopPropagation(); state.isRenaming = false; renderHist(); }
    };
}

function saveRename(id, val) {
    if (!state.isRenaming) return;
    const chat = state.chats.find(c => c.id === id);
    if (chat && val.trim()) { chat.title = val.trim(); }
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
        promptIn.placeholder = "Message The All Time Helper...";
        promptIn.classList.remove('auth-waiting');
    }
}

function applyAuthUI(promptIn) {
    console.log("DEBUG: Auth required detected! Applying UI...");
    promptIn.placeholder = "🔒 ENTER ADMIN KEY TO AUTHORIZE ACTION...";
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
    const oldText = msg.c;
    txtDiv.innerHTML = `
        <textarea class="edit-area">${escapeHtml(oldText)}</textarea>
        <div class="edit-controls">
            <button class="auth-btn edit-btn" onclick="submitEdit(${Number(idx)}, this.parentElement.parentElement)">Save & Submit</button>
            <button class="auth-btn edit-btn edit-btn-cancel" onclick="cancelEdit(${Number(idx)})">Cancel</button>
        </div>
    `;
}

function cancelEdit(idx) {
    const chat = state.chats.find(c => c.id === state.activeId);
    if (!chat || !chat.ms[idx]) return;
    const msg = chat.ms[idx];
    const txtDiv = document.getElementById(`msg-text-${safeDomId(idx)}`);
    if (txtDiv) {
        txtDiv.innerHTML = msg.r === 'b' ? window.renderMarkdown(msg.c) : escapeHtml(msg.c);
        if (msg.r === 'b') txtDiv.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
    }
}

function previewImg(i) {
    if (i.files && i.files[0]) {
        const reader = new FileReader();
        reader.onload = (e) => {
            state.currentImg = e.target.result.split(',')[1];
            if (state.currentBlobUrl) URL.revokeObjectURL(state.currentBlobUrl);
            state.currentBlobUrl = URL.createObjectURL(i.files[0]);
            const area = document.getElementById('img-preview-area');
            area.style.display = 'flex';
            area.innerHTML = `<div class="img-thumb-wrap"><img src="${state.currentBlobUrl}" class="img-thumb"><button class="img-remove-btn" onclick="clearImgPreview()">✕</button></div>`;
            selModel('moondream', 'Moondream (Vision)');
        };
        reader.readAsDataURL(i.files[0]);
    }
}

function clearImgPreview() {
    if (state.currentBlobUrl) URL.revokeObjectURL(state.currentBlobUrl);
    state.currentBlobUrl = null; state.currentImg = null;
    document.getElementById('img-in').value = '';
    const area = document.getElementById('img-preview-area');
    area.style.display = 'none'; area.innerHTML = '';
}

function showDeleteConfirm(id, e) {
    if (e) e.stopPropagation(); state.chatToDelete = id;
    document.getElementById('delete-confirm-modal').style.display = 'flex';
}

function closeDeleteConfirm() {
    document.getElementById('delete-confirm-modal').style.display = 'none';
    state.chatToDelete = null;
}

function openImageModal(src) {
    const m = document.getElementById('image-modal'); const img = document.getElementById('modal-img');
    if (m && img) { img.src = src; img.classList.remove('is-zoomed'); m.style.display = 'flex'; setTimeout(() => m.classList.add('active'), 10); history.pushState({ view: 'image' }, ""); }
}

function closeImageModal() {
    const m = document.getElementById('image-modal'); const img = document.getElementById('modal-img');
    if (m) { m.classList.remove('active'); img?.classList.remove('is-zoomed'); setTimeout(() => m.style.display = 'none', 300); }
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
}

function closeNeuralContext() {
    const card = document.getElementById('neural-context-card');
    const scrim = document.getElementById('neural-scrim');
    if (card) card.classList.remove('active');
    if (scrim) scrim.classList.remove('active');
}

function setThemeUI(theme) {
    document.body.setAttribute('data-theme', theme);
    const isDark = theme === 'dark';
    document.getElementById('main-logo-img').src = isDark ? LOGO_DATA : LOGO_LIGHT_DATA;
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
    addMsg, renderHist, startRename, saveRename, filterHist,
    checkAuthMode, startEditPrompt, cancelEdit,
    previewImg, clearImgPreview,
    showDeleteConfirm, closeDeleteConfirm,
    openImageModal, closeImageModal,
    showNeuralContext, closeNeuralContext,
    setThemeUI, handleDragStart, handleDragEnd
};

export { ui };
