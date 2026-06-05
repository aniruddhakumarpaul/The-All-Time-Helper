/**
 * ui.js — UI Controller Module
 * All DOM manipulation logic extracted from main_v3.js.
 */
import { state } from './state.js';
import { api } from './api.js';

const LOGO_DATA = "/static/img/logo.png";
const LOGO_LIGHT_DATA = "/static/img/logo(2).jpg";

/** Escapes HTML entities to prevent XSS injection via innerHTML. */
function escapeHTML(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function extractJsonAfterMarker(content, marker) {
    if (typeof content !== 'string' || !content.includes(marker)) return null;
    const payloadPart = content.split(marker, 2)[1] || '';
    const startIdx = payloadPart.indexOf('{');
    if (startIdx === -1) return null;

    let depth = 0;
    let inString = false;
    let escaped = false;
    for (let i = startIdx; i < payloadPart.length; i++) {
        const ch = payloadPart[i];
        if (escaped) {
            escaped = false;
            continue;
        }
        if (ch === '\\') {
            escaped = true;
            continue;
        }
        if (ch === '"') {
            inString = !inString;
            continue;
        }
        if (inString) continue;
        if (ch === '{') depth += 1;
        if (ch === '}') {
            depth -= 1;
            if (depth === 0) return payloadPart.slice(startIdx, i + 1);
        }
    }
    return null;
}

function formatAttachedContextSheetText(rawText, idx = 0) {
    const text = String(rawText || '').trim();
    if (!text) return `Context ${idx + 1}`;

    const jsonText = extractJsonAfterMarker(text, 'EMAIL_DRAFT_CONTEXT:');
    if (jsonText) {
        try {
            const draft = JSON.parse(jsonText);
            const lines = [];

            lines.push(`Email draft: ${draft.subject || 'Untitled email'}`);

            if (draft.recipient) {
                lines.push(`To: ${draft.recipient}`);
            }

            if (draft.tone) {
                lines.push(`Tone: ${draft.tone}`);
            }

            if (draft.body) {
                lines.push('');
                lines.push('Body:');
                lines.push(String(draft.body));
            }

            if (Array.isArray(draft.attachments) && draft.attachments.length) {
                lines.push('');
                lines.push('Draft attachments:');
                draft.attachments.forEach((att, i) => {
                    lines.push(`- ${att.name || att.filename || `Attachment ${i + 1}`}`);
                });
            }

            return lines.join('\n');
        } catch (err) {
            return text;
        }
    }

    return text;
}

function formatAttachedContextCardTitle(rawText, idx = 0) {
    const sheetText = formatAttachedContextSheetText(rawText, idx);
    const firstLine = String(sheetText || '').split(/\r?\n/).find(line => line.trim()) || `Context ${idx + 1}`;
    return firstLine.length > 80 ? `${firstLine.slice(0, 80).trimEnd()}…` : firstLine;
}

function formatAttachedContextCardSnippet(rawText, idx = 0) {
    const sheetText = formatAttachedContextSheetText(rawText, idx);
    const lines = String(sheetText || '').split(/\r?\n/).map(line => line.trim()).filter(Boolean);
    if (lines.length <= 1) return '';
    const snippet = lines.slice(1, 3).join(' · ');
    return snippet.length > 100 ? `${snippet.slice(0, 100).trimEnd()}…` : snippet;
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
        if (cGreet) cGreet.innerHTML = `Hello, <span style="background: var(--greet-grad); background-clip: text; -webkit-background-clip: text; -webkit-text-fill-color: transparent;">${escapeHTML(nameStr)}</span>`;
        const uInfo = document.getElementById('user-info');
        if (uInfo) uInfo.innerText = state.user.email;
        const avCont = document.getElementById('sidebar-av-container');
        if (avCont) avCont.innerHTML = `<div class="av u-av" style="width: 32px; height: 32px; font-size: 0.8rem;"><span class="initial-letter">${escapeHTML(initial)}</span><span class="full-name">${escapeHTML(nameStr)}</span></div>`;
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
    const avatarHtml = r === 'u'
        ? `<div class="av u-av"><span class="initial-letter">${initial}</span><span class="full-name">${name}</span></div>`
        : `<div class="av b-av" id="bot-av-${idx}">
            <div class="logo-img-wrapper">
                <svg class="orb-svg" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
                <defs>
                    <linearGradient id="orbGrad-${idx}" x1="0%" y1="0%" x2="100%" y2="100%">
                        <stop offset="0%" style="stop-color: var(--orb-color-1); stop-opacity: 1" />
                        <stop offset="100%" style="stop-color: var(--orb-color-2); stop-opacity: 1" />
                    </linearGradient>
                    <filter id="orbGlow-${idx}" x="-50%" y="-50%" width="200%" height="200%">
                        <feGaussianBlur in="SourceGraphic" stdDeviation="5" />
                    </filter>
                </defs>
                <circle cx="50%" cy="50%" r="40" fill="url(#orbGrad-${idx})" filter="url(#orbGlow-${idx})" />
                <circle cx="50%" cy="50%" r="25" fill="url(#orbGrad-${idx})" />
            </svg>
            </div>
            <div class="bot-bubble" id="bot-bubble-${idx}">I am great!</div>
           </div>`;

    let displayContent = c;
    let contextHtml = '';
    
    if (r === 'u') {
        const parsed = window.parseAttachedContexts(c);
        displayContent = parsed.cleanText;
        
        if (parsed.contexts && parsed.contexts.length > 0) {
            contextHtml = `<div class="msg-attached-contexts">`;
            parsed.contexts.forEach(ctx => {
                const displayTitle = formatAttachedContextCardTitle(ctx.text, ctx.index);
                const displaySnippet = formatAttachedContextCardSnippet(ctx.text, ctx.index);
                const safeText = escapeHTML(formatAttachedContextSheetText(ctx.text, ctx.index));
                const uniqueCtxId = `ctx-${idx}-${ctx.index}`;
                contextHtml += `
                    <div class="msg-context-card" onclick="window.openAttachedContextSheet('${uniqueCtxId}')">
                        <div class="msg-context-header">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                                <polyline points="14 2 14 8 20 8"></polyline>
                            </svg>
                            <span>${escapeHTML(displayTitle)}</span>
                        </div>
                        ${displaySnippet ? `<div class="msg-context-snippet">${escapeHTML(displaySnippet)}</div>` : ''}
                        <div class="msg-context-full" id="${uniqueCtxId}" style="display: none;">${safeText}</div>
                    </div>
                `;
            });
            contextHtml += `</div>`;
        }
    }

    let content = r === 'b' ? '' : displayContent.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    if (r === 'u' && isMasked) content = '•'.repeat(Math.max(8, displayContent.length));

    let tools = '';
    if (idx !== undefined) {
        let editTool = '';
        if (r === 'u' && !isMasked) {
            editTool = `<div class="tool-icon" onclick="startEditPrompt(${idx}, this)" title="Edit Prompt">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
                        </div>`;
        }
        
        const dragTool = `<div class="tool-icon drag-handle" draggable="true" ondragstart="window.handleMessageDragStart(event, ${idx})" ondragend="window.handleMessageDragEnd(event, ${idx})" title="Drag text payload">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                <circle cx="9" cy="5" r="1"></circle>
                                <circle cx="9" cy="12" r="1"></circle>
                                <circle cx="9" cy="19" r="1"></circle>
                                <circle cx="15" cy="5" r="1"></circle>
                                <circle cx="15" cy="12" r="1"></circle>
                                <circle cx="15" cy="19" r="1"></circle>
                            </svg>
                         </div>`;
        tools = `<div class="msg-tools">${editTool}${dragTool}</div>`;
    }

    let watermark = '';
    if (r === 'b' && mName) {
        watermark = `<div class="model-watermark" style="font-size: 0.7rem; color: var(--accent-blue); opacity: 0.8; margin-top: 12px; display: flex; align-items: center; gap: 6px; font-weight: 600; font-family: 'Outfit', sans-serif; letter-spacing: 0.3px; border-top: 1px solid rgba(255,255,255,0.05); padding-top: 8px;">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="opacity: 0.7;"><circle cx="12" cy="12" r="10"></circle><path d="M12 8v8M8 12h8"></path></svg>
            <span style="text-transform: uppercase; font-size: 0.65rem;">${mName}</span>
        </div>`;
    }

    div.innerHTML = `
        <div class="av-wrap">
            ${avatarHtml}
            <div class="av-label" style="font-size: 0.8rem; color: var(--text-sub); font-weight: 600; letter-spacing: 0.5px;">
                ${r === 'u' ? (state.user ? state.user.name : 'Human') : 'THE ALL TIME HELPER'}
            </div>
        </div>
        <div class="txt">
            ${contextHtml}
            <div id="msg-text-${idx}" class="msg-text">${content}</div>
            ${i ? (Array.isArray(i) ? i : [i]).map(item => {
                const isMetadata = typeof item === 'object' && item !== null;
                const isOmitted = typeof item === 'string' && item.startsWith('[');
                if (isOmitted) {
                    return `
                        <div class="chat-attachment-omitted" style="padding: 8px 12px; background: rgba(255, 255, 255, 0.05); border: 1px solid var(--glass-border); border-radius: 8px; font-size: 0.8rem; margin: 8px 0; color: var(--text-sub);">
                            💾 ${item}
                        </div>
                    `;
                }
                if (isMetadata) {
                    return `
                        <div class="chat-attachment-chip" style="display: inline-flex; align-items: center; gap: 8px; background: rgba(255, 255, 255, 0.05); border: 1px solid var(--glass-border); padding: 8px 12px; border-radius: 10px; color: var(--text-main); font-size: 0.85rem; margin: 8px 4px 8px 0;">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="color: var(--accent-blue);">
                                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path>
                            </svg>
                            <span style="font-weight: 500;">${escapeHTML(item.name || 'attachment.png')}</span>
                            ${item.size ? `<span style="opacity: 0.6; font-size: 0.75rem;">(${Math.round(item.size / 10.24) / 100} KB)</span>` : ''}
                        </div>
                    `;
                }
                const src = item.startsWith('data:') ? item : `data:image/png;base64,${item}`;
                return `
                    <div class="chat-img-preview-container" onclick="window.openImageModal('${src}')" draggable="true" ondragstart="window.handleImageDragStart(event, '${item}')">
                        <img src="${src}" class="chat-img-preview" style="cursor: grab;">
                    </div>
                `;
            }).join('') : ''}
            ${watermark}
            ${tools}
        </div>
    `;

    document.getElementById('chat-area').appendChild(div);
    const textEl = div.querySelector(`#msg-text-${idx}`);
    if (r === 'b') {
        renderBotMessage(textEl, c, idx);
    }
    if (typeof hljs !== 'undefined') {
        div.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
    }
    document.getElementById('chat-area').scrollTop = document.getElementById('chat-area').scrollHeight;
    if (mName) console.log(`DEBUG: Rendered watermark for ${mName}`);
    if (r === 'b') checkAuthMode();
    return textEl;
}

function renderHist() {
    if (state.isRenaming) return;
    const list = document.getElementById('history-list'); if (!list) return;
    list.innerHTML = '';
    const sorted = state.chats.slice().reverse().sort((a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0));
    sorted.forEach(c => {
        const title = (c.title || 'New Chat').toLowerCase();
        if (state.currentSearch && !title.includes(state.currentSearch.toLowerCase())) return;
        const div = document.createElement('div');
        div.className = `history-item ${c.id === state.activeId ? 'active-chat' : ''} ${c.pinned ? 'pinned' : ''}`;
        let titleContent = `<span class="chat-title-text" id="t-${c.id}">${c.title || 'New Chat'}</span>`;
        div.innerHTML = `
            ${titleContent}
            <div class="history-actions">
                <button class="del-chat-btn pin-btn ${c.pinned ? 'active' : ''}" onclick="event.stopPropagation(); togglePin('${c.id}')" title="${c.pinned ? 'Unpin' : 'Pin'} Chat">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v2a2 2 0 0 0 1.27 1.87L11 15.3V21l2-2 2 2v-5.7l6.73-3.43A2 2 0 0 0 21 10z"></path></svg>
                </button>
                <button class="del-chat-btn" onclick="event.stopPropagation(); startRename('${c.id}', event)" title="Rename Chat">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
                </button>
                <button class="del-chat-btn" onclick="event.stopPropagation(); showDeleteConfirm('${c.id}', event)" title="Delete Chat">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"></path><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
                </button>
            </div>`;
        div.onclick = (e) => { if (!e.target.closest('.del-chat-btn')) window.loadChat(c.id); };
        list.appendChild(div);
    });
}

function startRename(id, e) {
    e.stopPropagation();
    state.isRenaming = true;
    const span = document.getElementById(`t-${id}`);
    if (span.querySelector('input')) return;
    const old = span.innerText;
    span.textContent = '';
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'rename-in';
    input.value = old;
    input.id = `edit-${id}`;
    input.onclick = (e) => e.stopPropagation();
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
    if (chat && val.trim()) {
        chat.title = val.trim();
        if (window.persistChatMutation) window.persistChatMutation(id, { immediate: false });
    }
    state.isRenaming = false;
    renderHist();
}

function filterHist(q) { state.currentSearch = q; renderHist(); }

function checkAuthMode() {
    const chat = state.chats.find(c => c.id === state.activeId);
    const promptIn = document.getElementById('prompt');
    if (!chat || !promptIn) return;
    const lastMsg = chat.ms.length > 0 ? chat.ms[chat.ms.length - 1] : null;
    const authKeywords = ["please provide your admin key", "enter your admin_key", "provide the password", "authorize with your key", "auth_required", "admin key is missing", "incorrect admin key", "provide your admin key"];
    const needsAuth = lastMsg && lastMsg.r === 'b' && lastMsg.c && typeof lastMsg.c === 'string' && authKeywords.some(kw => lastMsg.c.toLowerCase().includes(kw));
    
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
    const txtDiv = document.getElementById(`msg-text-${idx}`);
    if (!txtDiv) { console.error("DEBUG: txtDiv not found"); return; }
    const oldText = typeof window.getVisibleUserMessageContent === 'function'
        ? window.getVisibleUserMessageContent(msg)
        : window.parseAttachedContexts(msg.c).cleanText;
    txtDiv.innerHTML = `
        <textarea class="edit-area">${oldText}</textarea>
        <div class="edit-controls">
            <button class="auth-btn edit-btn" onclick="submitEdit(${idx}, this.parentElement.parentElement)">Save & Submit</button>
            <button class="auth-btn edit-btn edit-btn-cancel" onclick="cancelEdit(${idx})">Cancel</button>
        </div>
    `;
}

function cancelEdit(idx) {
    const chat = state.chats.find(c => c.id === state.activeId);
    if (!chat || !chat.ms[idx]) return;
    const msg = chat.ms[idx];
    const txtDiv = document.getElementById(`msg-text-${idx}`);
    if (txtDiv) {
        if (msg.r === 'b') {
            txtDiv.innerHTML = window.renderMarkdown(msg.c);
            if (typeof hljs !== 'undefined') {
                txtDiv.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
            }
        } else {
            const visible = typeof window.getVisibleUserMessageContent === 'function'
                ? window.getVisibleUserMessageContent(msg)
                : window.parseAttachedContexts(msg.c).cleanText;
            txtDiv.innerHTML = visible.replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }
    }
}

function previewImg(i) {
    if (typeof window !== 'undefined' && window.previewImg && window.previewImg !== previewImg) {
        return window.previewImg(i);
    }
    console.warn('Image upload is not initialized yet.');
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
    const title = card.querySelector('.context-title');
    if (title) title.innerText = 'Neural Context Link';
    cont.innerHTML = '';
    if (explanation) {
        const box = document.createElement('div'); box.className = 'neural-insight-box';
        box.innerHTML = `<div class="insight-header"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg> Neural Insight</div><div class="insight-text">${explanation}</div>`;
        cont.appendChild(box);
    }
    const label = document.createElement('span'); label.className = 'source-label'; label.innerText = 'Technical Source Snippets'; cont.appendChild(label);
    if (!results?.length) cont.innerHTML += '<p style="text-align:center; color:var(--text-sub); padding: 20px;">No direct neural links found.</p>';
    else results.forEach(res => {
        const div = document.createElement('div'); div.className = 'context-snippet';
        div.innerHTML = `<span class="context-meta">${escapeHTML(res.metadata?.type || 'DOCUMENT')}</span><div style="max-height: 150px; overflow-y: auto; font-size: 0.85rem; color: var(--text-main);">${escapeHTML(res.content)}</div>`;
        cont.appendChild(div);
    });
    card.classList.add('active'); scrim.classList.add('active');
}

function openAttachedContextSheet(id) {
    const el = document.getElementById(id);
    const card = document.getElementById('neural-context-card');
    const cont = document.getElementById('context-results');
    const scrim = document.getElementById('neural-scrim');
    if (!el || !card || !cont || !scrim) return;

    const title = card.querySelector('.context-title');
    const cardEl = el.closest('.msg-context-card');
    const cardTitle = cardEl?.querySelector('.msg-context-header span')?.textContent?.trim();
    if (title) title.innerText = cardTitle || 'Attached Context';

    cont.innerHTML = '';
    const body = document.createElement('pre');
    body.className = 'attached-context-sheet';
    body.style.margin = '0';
    body.style.whiteSpace = 'pre-wrap';
    body.style.wordBreak = 'break-word';
    body.style.fontFamily = 'monospace';
    body.style.fontSize = '0.9rem';
    body.style.lineHeight = '1.6';
    body.style.color = 'var(--text-main)';
    body.style.background = 'rgba(255,255,255,0.04)';
    body.style.border = '1px solid var(--glass-border)';
    body.style.borderRadius = '14px';
    body.style.padding = '16px';
    body.textContent = el.textContent || '';
    cont.appendChild(body);

    card.classList.add('active');
    scrim.classList.add('active');
}

function closeNeuralContext() {
    const card = document.getElementById('neural-context-card');
    const scrim = document.getElementById('neural-scrim');
    if (card) card.classList.remove('active');
    if (scrim) scrim.classList.remove('active');
}

function setThemeUI(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    document.body.setAttribute('data-theme', theme);
    const isDark = theme === 'dark';
    const logo = document.getElementById('main-logo-img');
    if (logo) logo.src = isDark ? LOGO_DATA : LOGO_LIGHT_DATA;
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



function renderBotMessage(el, content, idx) {
    if (!el) return;
    
    if (content.includes("EMAIL_DRAFT_PAYLOAD:")) {
        const parts = content.split("EMAIL_DRAFT_PAYLOAD:");
        const textBefore = parts[0].trim();
        const payloadStr = parts[1].trim();
        
        let draft = null;
        try {
            let cleanPayload = payloadStr.trim();
            const startIdx = cleanPayload.indexOf("{");
            const endIdx = cleanPayload.lastIndexOf("}");
            if (startIdx !== -1 && endIdx !== -1 && endIdx > startIdx) {
                cleanPayload = cleanPayload.substring(startIdx, endIdx + 1);
            }
            draft = JSON.parse(cleanPayload);
        } catch (e) {
            // Incomplete JSON during streaming
        }
        
        const cleanTextBefore = (text) => {
            if (!text) return '';
            return text
                .replace(/```json[\s\S]*?```/gi, '')
                .replace(/```[\s\S]*?```/gi, '')
                .replace(/\{[\s\S]*?\}/g, '')
                .trim();
        };

        if (draft) {
            const cleanedText = cleanTextBefore(textBefore);
            if (window.resetUpscaleImagePolling) {
                window.resetUpscaleImagePolling(el);
            }
            el.innerHTML = cleanedText ? window.renderMarkdown(cleanedText) : '';
            if (window.initUpscaleImagePolling) {
                window.initUpscaleImagePolling(el);
            }
            
            const normalizeDraftAttachments = () => {
                const rawAttachments = Array.isArray(draft.attachments) ? draft.attachments : [];
                const normalized = rawAttachments
                    .map((attachment, index) => ({
                        id: attachment?.id || '',
                        content: attachment?.content || attachment?.attachment_content || attachment?.data || '',
                        filename: attachment?.filename || attachment?.attachment_filename || attachment?.name || `attachment_${index + 1}.png`,
                        name: attachment?.name || attachment?.filename || `attachment_${index + 1}.png`,
                        type: attachment?.type || attachment?.content_type || '',
                        size: attachment?.size || null,
                        sha256: attachment?.sha256 || ''
                    }))
                    .filter(attachment => attachment.id || String(attachment.content || '').trim());
                if (!normalized.length && draft.attachment_content) {
                    normalized.push({
                        content: draft.attachment_content,
                        filename: draft.attachment_filename || 'report.txt'
                    });
                }
                return normalized;
            };
            const attachmentList = normalizeDraftAttachments();
            const primaryAttachment = attachmentList[0] || null;
            const attachmentContent = primaryAttachment?.content || null;
            const attachmentFilename = primaryAttachment?.filename || draft.attachment_filename || 'report.txt';
            const attachmentRowsHtml = attachmentList.map((attachment) => {
                const attachmentText = String(attachment.content || '').trim();
                const filename = attachment.filename || 'attachment.png';
                const attachmentIsUrl = /^https?:\/\//i.test(attachmentText);
                const attachmentIsDataUrl = /^data:/i.test(attachmentText);
                const attachmentLooksBase64 = !attachmentIsUrl && !attachmentIsDataUrl && attachmentText.length >= 64 && /^[A-Za-z0-9+/=\s]+$/.test(attachmentText);
                const attachmentCanPreview = attachmentIsDataUrl || attachmentLooksBase64;
                const attachmentSizeLabel = attachment.size
                    ? `${Math.round(attachment.size / 10.24) / 100} KB`
                    : attachment.id
                        ? 'stored securely'
                        : attachmentIsUrl
                    ? 'not downloaded'
                    : attachmentCanPreview
                        ? `${Math.round((attachmentIsDataUrl ? attachmentText.length : attachmentText.replace(/\s/g, '').length * 0.75) / 10.24) / 100} KB`
                        : 'invalid data';
                const attachmentExt = (filename.split('.').pop() || 'png').toLowerCase();
                const attachmentPreviewSrc = attachmentIsDataUrl
                    ? attachmentText
                    : `data:image/${attachmentExt === 'jpg' ? 'jpeg' : attachmentExt};base64,${attachmentText}`;
                return `
                    <div style="display: flex; flex-direction: column; gap: 8px; align-items: flex-start;">
                        <div class="email-attachment-chip" style="display: flex; align-items: center; gap: 8px; background: rgba(255, 255, 255, 0.05); border: 1px solid var(--glass-border); padding: 8px 12px; border-radius: 10px; color: var(--text-main); font-size: 0.85rem;">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="color: var(--accent-blue);">
                                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path>
                            </svg>
                            <span style="font-weight: 500; font-family: 'Outfit', sans-serif;">${escapeHTML(filename)}</span>
                            <span style="opacity: 0.6; font-size: 0.75rem;">(${attachmentSizeLabel})</span>
                        </div>
                        ${attachmentCanPreview && /\.(jpe?g|png|gif|webp|svg)$/i.test(filename) ? `
                            <div class="email-attachment-preview" style="max-width: 150px; border-radius: 8px; overflow: hidden; border: 1px solid var(--glass-border); margin-top: 4px; box-shadow: 0 4px 10px rgba(0,0,0,0.15);">
                                <img src="${attachmentPreviewSrc}" style="width: 100%; height: auto; display: block;">
                            </div>
                        ` : ''}
                    </div>
                `;
            }).join('');
            
            const card = document.createElement('div');
            card.className = 'email-widget-container';
            
            const toneOptions = ['modern', 'formal', 'informal'];
            const selectOptions = toneOptions.map(t => 
                `<option value="${t}" ${draft.tone === t ? 'selected' : ''}>${t.charAt(0).toUpperCase() + t.slice(1)}</option>`
            ).join('');
            
            card.innerHTML = `
                <div class="email-field-row">
                    <label>To</label>
                    <input type="text" class="email-input email-to-input" value="${escapeHTML(draft.recipient || '')}">
                </div>
                <div class="email-field-row">
                    <label>Subject</label>
                    <input type="text" class="email-input email-subject-input" value="${escapeHTML(draft.subject || '')}">
                </div>
                <div class="email-field-row">
                    <label>Email Tone</label>
                    <select class="email-input email-tone-select">
                        ${selectOptions}
                    </select>
                </div>
                <div class="email-field-row">
                    <label>Body</label>
                    <textarea class="email-textarea email-body-input">${escapeHTML(draft.body || '')}</textarea>
                </div>
                ${attachmentList.length ? `
                <div class="email-field-row">
                    <label>${attachmentList.length > 1 ? 'Attachments' : 'Attachment'}</label>
                    <div style="display: flex; flex-direction: column; gap: 8px; align-items: flex-start;">
                        ${attachmentRowsHtml}
                    </div>
                </div>
                ` : ''}
                <div class="email-preview-header">Live HTML Preview</div>
                <div class="email-iframe-wrapper">
                    <iframe class="email-preview-iframe" sandbox=""></iframe>
                </div>
                <div class="email-actions">
                    <button class="email-widget-btn send-email-btn">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                            <line x1="22" y1="2" x2="11" y2="13"></line>
                            <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
                        </svg>
                        <span>Send Email</span>
                    </button>
                </div>
            `;
            el.appendChild(card);
            card.__emailDraft = draft;
            if (window.initUpscaleImagePolling) {
                window.initUpscaleImagePolling(el);
            }

            const persistDraft = ({ immediate = false } = {}) => {
                draft.recipient = card.querySelector('.email-to-input')?.value.trim() || '';
                draft.subject = card.querySelector('.email-subject-input')?.value.trim() || '';
                draft.body = card.querySelector('.email-body-input')?.value || '';
                draft.tone = card.querySelector('.email-tone-select')?.value || 'modern';
                card.__emailDraft = draft;
                if (window.updateSavedBotMessage) {
                    window.updateSavedBotMessage(idx, `EMAIL_DRAFT_PAYLOAD:${JSON.stringify(draft)}`, { immediate });
                }
            };

            let persistTimeout;
            const debouncedPersistDraft = () => {
                clearTimeout(persistTimeout);
                persistTimeout = setTimeout(() => persistDraft({ immediate: false }), 350);
            };
            
            // --- Drag and Drop Attachment Target ---
            card.addEventListener('dragover', (e) => {
                e.preventDefault();
                card.style.border = '2px dashed var(--accent-blue)';
                card.style.background = 'rgba(99, 102, 241, 0.05)';
            });
            card.addEventListener('dragleave', () => {
                card.style.border = '';
                card.style.background = '';
            });
            card.addEventListener('drop', (e) => {
                e.preventDefault();
                card.style.border = '';
                card.style.background = '';
                const type = e.dataTransfer.getData("text/plain");
                if (type === "ATTACH_IMAGE") {
                    const base64 = e.dataTransfer.getData("image-base64");
                    const filename = e.dataTransfer.getData("image-filename");
                    
                    // Update the draft payload directly
                    draft.attachment_content = base64;
                    draft.attachment_filename = filename;
                    draft.attachments = [{ content: base64, filename }];
                    card.__emailDraft = draft;
                    if (window.updateSavedBotMessage) {
                        window.updateSavedBotMessage(idx, `EMAIL_DRAFT_PAYLOAD:${JSON.stringify(draft)}`, { immediate: false });
                    }
                    
                    // Re-render message block
                    renderBotMessage(el, `EMAIL_DRAFT_PAYLOAD:${JSON.stringify(draft)}`, idx);
                }
            });
            
            const iframe = card.querySelector('.email-preview-iframe');
            
            const updatePreview = async () => {
                const bodyVal = card.querySelector('.email-body-input').value;
                const toneVal = card.querySelector('.email-tone-select').value;
                try {
                    const html = await api.renderEmailPreview(bodyVal, toneVal);
                    const safeHtml = String(html || '').replace(/<script[\s\S]*?<\/script>/gi, '');
                    iframe.srcdoc = safeHtml;
                } catch (err) {
                    console.error("Error updating preview:", err);
                }
            };
            
            let timeout;
            const debouncedUpdate = () => {
                clearTimeout(timeout);
                timeout = setTimeout(updatePreview, 300);
            };
            
            card.querySelectorAll('.email-to-input, .email-subject-input, .email-body-input').forEach(input => {
                input.addEventListener('input', () => {
                    debouncedPersistDraft();
                    if (input.classList.contains('email-body-input')) debouncedUpdate();
                });
            });
            card.querySelector('.email-tone-select').addEventListener('change', () => {
                persistDraft({ immediate: false });
                updatePreview();
            });
            
            updatePreview();
            
            const sendBtn = card.querySelector('.send-email-btn');
            sendBtn.addEventListener('click', async () => {
                const toVal = card.querySelector('.email-to-input').value.trim();
                const subjectVal = card.querySelector('.email-subject-input').value.trim();
                const bodyVal = card.querySelector('.email-body-input').value.trim();
                const toneVal = card.querySelector('.email-tone-select').value;
                
                if (!toVal || !subjectVal || !bodyVal) {
                    alert("All fields (To, Subject, Body) are required.");
                    return;
                }
                persistDraft({ immediate: true });
                
                sendBtn.disabled = true;
                sendBtn.innerHTML = '<div class="spinner" style="width:14px;height:14px;margin-right:5px;border-width:2px;"></div> Sending...';
                card.querySelectorAll('.email-input, .email-textarea').forEach(el => el.disabled = true);
                
                try {
                    const attachmentsForSend = Array.isArray(draft.attachments) && draft.attachments.length ? draft.attachments : null;
                    const res = await api.sendEmailDirect(toVal, subjectVal, bodyVal, toneVal, attachmentContent, attachmentFilename, null, attachmentsForSend);
                    if (res.success) {
                        const successHtml = `
                            <div class="email-success-alert">
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                                    <polyline points="20 6 9 17 4 12"></polyline>
                                </svg>
                                <span>${res.result || 'Email sent successfully!'}</span>
                            </div>
                        `;
                        card.outerHTML = successHtml;
                        if (window.updateSavedBotMessage) {
                            window.updateSavedBotMessage(idx, res.result || 'Email sent successfully!', { immediate: true });
                        }
                        checkAuthMode();
                    } else {
                        sendBtn.disabled = false;
                        sendBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg><span>Send Email</span>';
                        card.querySelectorAll('.email-input, .email-textarea').forEach(el => el.disabled = false);
                        
                        const isAuthErr = (res.detail && (res.detail.includes("Admin key required") || res.detail.includes("Unauthorized"))) || 
                                          (res.error && res.error.includes("AUTH_REQUIRED"));
                        if (isAuthErr) {
                            const adminKey = prompt("🔒 Admin Authorization Required. Please enter your Admin Key:");
                            if (adminKey) {
                                sendBtn.disabled = true;
                                sendBtn.innerHTML = '<div class="spinner" style="width:14px;height:14px;margin-right:5px;border-width:2px;"></div> Sending...';
                                card.querySelectorAll('.email-input, .email-textarea').forEach(el => el.disabled = true);
                                try {
                                    const attachmentsForRetry = Array.isArray(draft.attachments) && draft.attachments.length ? draft.attachments : null;
                                    const retryRes = await api.sendEmailDirect(toVal, subjectVal, bodyVal, toneVal, attachmentContent, attachmentFilename, adminKey, attachmentsForRetry);
                                    if (retryRes.success) {
                                        const successHtml = `
                                            <div class="email-success-alert">
                                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                                                    <polyline points="20 6 9 17 4 12"></polyline>
                                                </svg>
                                                <span>${retryRes.result || 'Email sent successfully!'}</span>
                                            </div>
                                        `;
                                        card.outerHTML = successHtml;
                                        if (window.updateSavedBotMessage) {
                                            window.updateSavedBotMessage(idx, retryRes.result || 'Email sent successfully!', { immediate: true });
                                        }
                                        const promptIn = document.getElementById('prompt');
                                        if (promptIn) {
                                            promptIn.placeholder = "Message The All Time Helper...";
                                            promptIn.classList.remove('auth-waiting');
                                        }
                                        checkAuthMode();
                                    } else {
                                        alert("Failed to send email: " + (retryRes.error || retryRes.detail || "Unknown error"));
                                        sendBtn.disabled = false;
                                        sendBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg><span>Send Email</span>';
                                        card.querySelectorAll('.email-input, .email-textarea').forEach(el => el.disabled = false);
                                    }
                                } catch (retryErr) {
                                    console.error("Error on email retry send:", retryErr);
                                    alert("Connection error during retry.");
                                    sendBtn.disabled = false;
                                    sendBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg><span>Send Email</span>';
                                    card.querySelectorAll('.email-input, .email-textarea').forEach(el => el.disabled = false);
                                }
                            }
                        } else {
                            alert("Failed to send email: " + (res.error || res.detail || "Unknown error"));
                        }
                      }
                } catch (err) {
                    console.error("Error sending email:", err);
                    sendBtn.disabled = false;
                    sendBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg><span>Send Email</span>';
                    card.querySelectorAll('.email-input, .email-textarea').forEach(el => el.disabled = false);
                    alert("Connection error sending email.");
                }
            });
        } else {
            const cleanedText = cleanTextBefore(textBefore);
            if (window.resetUpscaleImagePolling) {
                window.resetUpscaleImagePolling(el);
            }
            el.innerHTML = cleanedText ? window.renderMarkdown(cleanedText) : '';
            if (window.initUpscaleImagePolling) {
                window.initUpscaleImagePolling(el);
            }
            const draftLoader = document.createElement('div');
            draftLoader.className = 'status-msg';
            draftLoader.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><circle cx="12" cy="12" r="10"></circle><path d="M12 6v6l4 2"></path></svg> <span>Drafting email...</span>`;
            el.appendChild(draftLoader);
        }
    } else {
        if (window.resetUpscaleImagePolling) {
            window.resetUpscaleImagePolling(el);
        }
        el.innerHTML = window.renderMarkdown(content);
        if (window.initUpscaleImagePolling) {
            window.initUpscaleImagePolling(el);
        }
    }
    
    if (typeof hljs !== 'undefined') {
        el.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
    }
}

function collectEmailDraftForDrag(card) {
    if (!card) return null;
    const draft = card.__emailDraft && typeof card.__emailDraft === 'object' ? { ...card.__emailDraft } : {};
    const recipient = card.querySelector('.email-to-input')?.value.trim();
    const subject = card.querySelector('.email-subject-input')?.value.trim();
    const body = card.querySelector('.email-body-input')?.value;
    const tone = card.querySelector('.email-tone-select')?.value;

    if (recipient !== undefined) draft.recipient = recipient || '';
    if (subject !== undefined) draft.subject = subject || '';
    if (body !== undefined) draft.body = body || '';
    if (tone !== undefined) draft.tone = tone || 'modern';

    if (Array.isArray(draft.attachments) && draft.attachments.length) {
        draft.attachments = draft.attachments.map((attachment, index) => ({
            id: attachment?.id || '',
            content: attachment?.content || attachment?.attachment_content || attachment?.data || '',
            name: attachment?.name || attachment?.filename || `attachment_${index + 1}.png`,
            filename: attachment?.filename || attachment?.name || `attachment_${index + 1}.png`,
            type: attachment?.type || attachment?.content_type || '',
            size: attachment?.size || null,
            sha256: attachment?.sha256 || ''
        })).filter(attachment => attachment.id || String(attachment.content || '').trim() || attachment.filename);
    }

    return draft;
}

const ui = {
    smartFocus, switchAuth, updUI, signOut, toggleDropdown, selModel,
    toggleSidebar, openSettings, closeSettings,    toggleSet,
    addMsg, renderHist, startRename, saveRename, filterHist,
    checkAuthMode, startEditPrompt, cancelEdit,
    previewImg, clearImgPreview,
    showDeleteConfirm, closeDeleteConfirm,
    openImageModal, closeImageModal,
    showNeuralContext, openAttachedContextSheet, closeNeuralContext,
    setThemeUI, handleDragStart, handleDragEnd,
    renderBotMessage,
    collectEmailDraftForDrag
};

export { ui };
