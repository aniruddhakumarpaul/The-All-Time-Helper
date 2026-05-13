document.addEventListener('DOMContentLoaded', () => {
    try {
        console.log("DEBUG: All Time Helper Script Initializing...");
        // Global Bot State
        window.botState = 'idle';
        const LOGO_DATA = "/static/img/logo.png";
        const LOGO_LIGHT_DATA = "/static/img/logo(2).jpg";
        const BOT_DATA = "/static/img/bot.png";
        let user = null;
        let chats = []; window.chats = chats;
        let activeId = null; window.activeId = null; // Exposed globally for inline handlers
        let abortC = null; let currentImg = null;
        let selectedModel = 'gemma4:e2b';
        let currentBlobUrl = null;
        let chatToDelete = null;
        let isRenaming = false;
        let currentSearch = '';
        let tiltSettleTimer = null;

        // --- Core Helpers (Hoisted to Top) ---
        function smartFocus(id) {
            if (window.innerWidth > 850) {
                const el = document.getElementById(id);
                if (el) el.focus();
            }
        }

        // --- Upscaling Engine (Frontend) ---
        const activePollers = new Set();
        function startUpscalePoller(jobId, container) {
            if (activePollers.has(jobId)) return;
            activePollers.add(jobId);

            const img = container.querySelector('.chat-rendered-img');
            if (!img) {
                activePollers.delete(jobId);
                return;
            }

            // Wrap image in a container if not already
            if (!img.parentElement.classList.contains('upscale-container')) {
                const wrapper = document.createElement('div');
                wrapper.className = 'upscale-container';
                img.parentNode.insertBefore(wrapper, img);
                wrapper.appendChild(img);
            }

            img.classList.add('upscaling');
            const badge = document.createElement('div');
            badge.className = 'upscale-badge';
            badge.innerHTML = '<div class="spinner" style="width:12px; height:12px; margin-right:5px; border-width:2px;"></div> Enhancing...';
            img.parentElement.appendChild(badge);

            const poll = async () => {
                try {
                    const res = await fetch(`/api/upscale/status/${jobId}`);
                    const data = await res.json();
                    if (data.success && data.status === 'ready') {
                        const highRes = new Image();
                        highRes.src = data.url;
                        highRes.onload = () => {
                            img.src = data.url;
                            img.classList.remove('upscaling');
                            badge.innerHTML = '✨ 4K Enhanced';
                            badge.classList.add('ready');
                            setTimeout(() => {
                                badge.style.opacity = '0';
                                setTimeout(() => badge.remove(), 500);
                            }, 4000);
                            activePollers.delete(jobId);
                        };
                    } else if (data.status === 'failed') {
                        img.classList.remove('upscaling');
                        badge.remove();
                        activePollers.delete(jobId);
                    } else {
                        setTimeout(poll, 2500);
                    }
                } catch (e) {
                    activePollers.delete(jobId);
                }
            };
            poll();
        }

        // Auth
        function switchAuth(t) {
            ['login', 'signup', 'otp'].forEach(f => { document.getElementById(f + '-form').style.display = (f === t ? 'block' : 'none'); });
            if (t === 'login') document.getElementById('l-email').focus();
            if (t === 'signup') document.getElementById('s-name').focus();
            if (t === 'otp') document.getElementById('v-otp').focus();
        }

        function updUI() {
            if (user) {
                const nameStr = user.name || 'Human';
                const initial = nameStr.charAt(0).toUpperCase();

                const sbGreet = document.getElementById('sidebar-greet');
                if (sbGreet) sbGreet.innerText = 'Hello, ' + nameStr;

                const cGreet = document.getElementById('center-greet');
                if (cGreet) cGreet.innerHTML = `Hello, <span style="background: var(--greet-grad); background-clip: text; -webkit-background-clip: text; -webkit-text-fill-color: transparent;">${nameStr}</span>`;

                const uInfo = document.getElementById('user-info');
                if (uInfo) uInfo.innerText = user.email;

                const avCont = document.getElementById('sidebar-av-container');
                if (avCont) {
                    avCont.innerHTML = `<div class="av u-av" style="width: 32px; height: 32px; font-size: 0.8rem;"><span class="initial-letter">${initial}</span><span class="full-name">${nameStr}</span></div>`;
                }
            }
        }

        async function handleAuth(t) {
            const btn = document.getElementById(t + '-btn');
            const originalText = btn.innerHTML;

            let p = {};
            if (t === 'login') p = { email: document.getElementById('l-email').value, pwd: document.getElementById('l-pwd').value };
            else if (t === 'signup') p = { email: document.getElementById('s-email').value, pwd: document.getElementById('s-pwd').value, name: document.getElementById('s-name').value };
            else if (t === 'verify') p = { email: document.getElementById('s-email').value || document.getElementById('l-email').value, otp: document.getElementById('v-otp').value };

            // Loading state
            btn.disabled = true;
            btn.innerHTML = '<div class="spinner"></div>';

            try {
                const res = await fetch('/' + t, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '69420' },
                    body: JSON.stringify(p)
                });
                const data = await res.json();
                if (data.success) {
                    if (t === 'signup' || (t === 'login' && data.unverified)) switchAuth('otp');
                    else {
                        user = data.user; localStorage.setItem('helper_user_v2', JSON.stringify(user));
                        if (data.token) localStorage.setItem('helper_token_v2', data.token);
                        document.getElementById('auth-overlay').style.display = 'none';
                        loadUserChats();
                        updUI();

                        // Theme Onboarding 
                        if (!localStorage.getItem('helper_theme_pref')) {
                            document.getElementById('theme-modal').style.display = 'flex';
                        }

                        smartFocus('prompt');
                    }
                } else alert(data.error || 'Check credentials');
            } catch (e) {
                console.error("Auth Fail:", e);
                alert('Connection Error: ' + e.message + '\n(Check if server is running at ' + location.origin + ')');
            }
            finally {
                btn.disabled = false;
                btn.innerHTML = originalText;
            }
        }

        function signOut() { 
            localStorage.removeItem('helper_user_v2'); 
            localStorage.removeItem('helper_token_v2'); 
            localStorage.removeItem('helper_active_chat_v2');
            localStorage.removeItem('helper_active_modal_v2');
            location.reload(); 
        }

        // Dropdown
        function toggleDropdown() {
            const menu = document.getElementById('model-menu');
            if (menu) menu.classList.toggle('active');
        }
        function selModel(id, name) {
            selectedModel = id;
            document.getElementById('active-model-name').innerText = name;
            const menu = document.getElementById('model-menu');
            if (menu) menu.classList.remove('active');
        }

        // Sidebar Toggle
        function toggleSidebar() {
            const sb = document.getElementById('sidebar');
            const scrim = document.getElementById('sidebar-scrim');
            const isOpen = sb.classList.toggle('open');
            document.body.classList.toggle('sidebar-open', isOpen);

            // GHOST FIX: Clear inline styles left by the swipe gesture
            if (sb) sb.style.transform = '';
            if (scrim) {
                scrim.style.opacity = '';
                scrim.style.display = '';
            }

            if (isOpen) {
                history.pushState({ view: 'sidebar' }, "");
            }
        }

        // Settings
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

        function handleChatKey(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                send();
            } else if (e.key === 'Escape') {
                startNewChat();
            }
        }

        window.toggleThemeMenu = function (e, menuId) {
            if (e) e.stopPropagation();
            const target = menuId || 'theme-menu';
            const menu = document.getElementById(target);
            if (!menu) return;

            const isVisible = menu.style.display === 'flex';

            // Close all other menus and clear row elevations
            const allMenus = document.querySelectorAll('.dropdown-menu');
            allMenus.forEach(m => m.style.display = 'none');
            document.querySelectorAll('.set-row').forEach(r => r.classList.remove('row-elevated'));

            if (!isVisible) {
                menu.style.display = 'flex';
                const parentRow = menu.closest('.set-row');
                if (parentRow) parentRow.classList.add('row-elevated');
            }
        }

        // --- Theme Engine v2 ---
        window.applyThemeChoice = function (choice) {
            localStorage.setItem('helper_theme_pref', choice);

            // Sync Icons across multiple layers (Header & Settings)
            const iconMap = { 'light': '☀️', 'dark': '🌙', 'system': '🌓' };
            const labels = { 'light': 'Light', 'dark': 'Dark', 'system': 'System' };

            const themeBtnIcon = document.getElementById('current-theme-icon');
            if (themeBtnIcon) themeBtnIcon.innerText = iconMap[choice] || '🌓';

            const themeSettingsIcon = document.getElementById('current-theme-icon-settings');
            if (themeSettingsIcon) themeSettingsIcon.innerText = (iconMap[choice] || '🌓') + ' ' + (labels[choice] || 'System');

            // Highlight active option in modal & dropdown
            const opts = document.querySelectorAll('.theme-opt, .menu-item');
            opts.forEach(o => o.classList.remove('active'));
            opts.forEach(o => {
                const text = o.innerText.toLowerCase();
                if (text === choice || text.includes(choice)) o.classList.add('active');
            });

            if (choice === 'system') {
                const isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
                setThemeUI(isDark ? 'dark' : 'light');
            } else {
                setThemeUI(choice);
            }

            // Close UI elements and reset row elevation
            const menus = document.querySelectorAll('.dropdown-menu');
            menus.forEach(m => m.style.display = 'none');
            document.querySelectorAll('.set-row').forEach(r => r.classList.remove('row-elevated'));

            if (document.getElementById('theme-modal').style.display === 'flex') {
                setTimeout(() => document.getElementById('theme-modal').style.display = 'none', 400);
            }
        }

        function setThemeUI(theme) {
            document.body.setAttribute('data-theme', theme);
            const isDark = theme === 'dark';
            document.getElementById('main-logo-img').src = isDark ? LOGO_DATA : LOGO_LIGHT_DATA;
        }

        // Listen for System Changes
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {
            if (localStorage.getItem('helper_theme_pref') === 'system') {
                setThemeUI(e.matches ? 'dark' : 'light');
            }
        });

        function initTheme() {
            const pref = localStorage.getItem('helper_theme_pref') || 'system';
            applyThemeChoice(pref);
        }
        initTheme();

        // Final UI tweak
        document.getElementById('active-model-name').innerText = 'Gemma 4';

        // -----------------------
        function toggleSet(id) { document.getElementById(id).classList.toggle('on'); }

        function startNewChat() {
            activeId = Date.now().toString();
            document.getElementById('chat-area').innerHTML = '';
            document.getElementById('chat-area').style.display = 'none';
            document.getElementById('welcome').style.display = 'flex';
            clearImgPreview();
            
            const promptEl = document.getElementById('prompt');
            if (promptEl) {
                promptEl.value = '';
                promptEl.style.height = 'auto';
            }
            
            renderHist();

            // Mobile Sidebar Fix
            if (window.innerWidth <= 850 && document.getElementById('sidebar').classList.contains('open')) {
                toggleSidebar();
            }

            smartFocus('prompt');
        }

        window.renderHist = renderHist;
        function renderHist() {
            if (isRenaming) return;
            const list = document.getElementById('history-list'); if (!list) return;
            list.innerHTML = '';

            // Sorting: Pinned first, then by recency
            const sorted = chats.slice().reverse().sort((a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0));

            sorted.forEach(c => {
                const title = (c.title || 'New Chat').toLowerCase();
                if (currentSearch && !title.includes(currentSearch.toLowerCase())) return;

                const div = document.createElement('div');
                div.className = `history-item ${c.id === activeId ? 'active-chat' : ''} ${c.pinned ? 'pinned' : ''}`;

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
                div.onclick = (e) => { if (!e.target.closest('.del-chat-btn')) loadChat(c.id); };
                list.appendChild(div);
            });
        }

        window.togglePin = (id) => {
            const chat = chats.find(c => c.id === id);
            if (chat) {
                chat.pinned = !chat.pinned;
                saveUserChats();
                renderHist();
                const histList = document.getElementById('history-list');
                if (histList) {
                    histList.scrollTo({ top: 0, behavior: 'smooth' });
                }
            }
        };

        window.exportChat = () => {
            const chat = chats.find(c => c.id === activeId);
            if (!chat || !chat.ms.length) return;
            let md = `# ${chat.title || 'Conversation'}\n\n`;
            chat.ms.forEach(m => {
                const role = m.r === 'u' ? 'User' : 'Assistant';
                md += `### ${role}\n${m.c}\n\n---\n\n`;
            });
            const blob = new Blob([md], { type: 'text/markdown' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `chat_${activeId}.md`;
            a.click();
            URL.revokeObjectURL(url);
        };

        window.filterHist = filterHist;
        function filterHist(q) {
            currentSearch = q;
            renderHist();
        }

        function startRename(id, e) {
            e.stopPropagation();
            isRenaming = true;
            const span = document.getElementById(`t-${id}`);
            if (span.querySelector('input')) return;
            const old = span.innerText;
            span.innerHTML = `<input type="text" class="rename-in" value="${old}" id="edit-${id}" onclick="event.stopPropagation()">`;
            const input = document.getElementById(`edit-${id}`);
            input.focus();
            input.onblur = () => saveRename(id, input.value);
            input.onkeydown = (ev) => {
                if (ev.key === 'Enter') { ev.stopPropagation(); saveRename(id, input.value); }
                if (ev.key === 'Escape') { ev.stopPropagation(); isRenaming = false; renderHist(); }
            };
        }

        function saveRename(id, val) {
            if (!isRenaming) return;
            const chat = chats.find(c => c.id === id);
            if (chat && val.trim()) { chat.title = val.trim(); saveUserChats(); }
            isRenaming = false;
            renderHist();
        }

        window.loadChat = loadChat;
        function loadChat(id) {
            activeId = id; 
            localStorage.setItem('helper_active_chat_v2', id);
            const chat = chats.find(c => c.id === id);
            document.getElementById('chat-area').innerHTML = '';
            document.getElementById('chat-area').style.display = 'block';
            document.getElementById('welcome').style.display = 'none';
            clearImgPreview();
            chat.ms.forEach((m, idx) => addMsg(m.r, m.c, m.i, idx, m.m || 'AI Assistant', m.masked));
            renderHist();

            if (window.innerWidth <= 850 && document.getElementById('sidebar').classList.contains('open')) {
                toggleSidebar();
            }

            smartFocus('prompt');
            checkAuthMode();
        }

        function checkAuthMode() {
            console.log("DEBUG: checkAuthMode running for activeId:", window.activeId);
            const chat = chats.find(c => c.id === window.activeId);
            const promptIn = document.getElementById('prompt');
            if (!chat || !promptIn) {
                console.warn("DEBUG: checkAuthMode failed - no chat or promptEl");
                return;
            }
            
            const lastMsg = chat.ms.length > 0 ? chat.ms[chat.ms.length - 1] : null;
            if (lastMsg) console.log("DEBUG: Last message for auth check:", lastMsg.r, lastMsg.c.substring(0, 50));
            const authKeywords = ["please provide your admin key", "enter your admin_key", "provide the password", "authorize with your key", "auth_required", "admin key is missing", "incorrect admin key"];
            const needsAuth = lastMsg && lastMsg.r === 'b' && authKeywords.some(kw => lastMsg.c.toLowerCase().includes(kw));

            if (needsAuth) {
                console.log("DEBUG: Auth required detected! Applying UI...");
                promptIn.placeholder = "🔒 ENTER ADMIN KEY TO AUTHORIZE EMAIL DISPATCH...";
                promptIn.classList.add('auth-waiting');
                jiggleLogo();
                smartFocus('prompt');
            } else {
                promptIn.placeholder = "Message The All Time Helper...";
                promptIn.classList.remove('auth-waiting');
            }
        }

        function addMsg(r, c, i, idx, mName, isMasked = false) {
            const div = document.createElement('div');
            div.className = `msg ${r}-msg entering`; 
            setTimeout(() => div.classList.remove('entering'), 600);

            const name = user ? user.name : 'Human';
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

            let content = r === 'b' ? window.renderMarkdown(c) : c.replace(/</g, '&lt;').replace(/>/g, '&gt;');
            if (r === 'u' && isMasked) content = '•'.repeat(Math.max(8, c.length));

            let tools = '';
            if (r === 'u' && idx !== undefined && !isMasked) {
                tools = `<div class="msg-tools">
                            <div class="tool-icon" onclick="startEditPrompt(${idx}, this)" title="Edit Prompt">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
                            </div>
                         </div>`;
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
                        ${r === 'u' ? (user ? user.name : 'Human') : 'THE ALL TIME HELPER'}
                    </div>
                </div>
                <div class="txt" draggable="false" ondragstart="if(!window.isGDown) { event.preventDefault(); return false; } handleDragStart(event, this.innerText)" ondragend="handleDragEnd(event)">
                    <div id="msg-text-${idx}">${content}</div>
                    ${i ? `
                        <div class="chat-img-preview-container" onclick="openImageModal('data:image/png;base64,${i}')">
                            <img src="data:image/png;base64,${i}" class="chat-img-preview">
                        </div>` : ''}
                    ${watermark}
                    ${tools}
                </div>
            `;

            document.getElementById('chat-area').appendChild(div);
            div.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
            document.getElementById('chat-area').scrollTop = document.getElementById('chat-area').scrollHeight;
            if (mName) console.log(`DEBUG: Rendered watermark for ${mName}`);
            return div.querySelector(`#msg-text-${idx}`);
        }

        function startEditPrompt(idx, btn) {
            console.log("DEBUG: Editing prompt", idx);
            const chat = chats.find(c => c.id === activeId);
            const msg = chat.ms[idx];
            const txtDiv = document.getElementById(`msg-text-${idx}`);
            if (!txtDiv) { console.error("DEBUG: txtDiv not found"); return; }
            const oldText = msg.c;
            txtDiv.innerHTML = `
                <textarea class="edit-area">${oldText}</textarea>
                <div class="edit-controls">
                    <button class="auth-btn edit-btn" onclick="submitEdit(${idx}, this.parentElement.parentElement)">Save & Submit</button>
                    <button class="auth-btn edit-btn edit-btn-cancel" onclick="cancelEdit(${idx})">Cancel</button>
                </div>
            `;
        }

        function cancelEdit(idx) {
            const chat = chats.find(c => c.id === activeId);
            if (!chat || !chat.ms[idx]) return;
            const msg = chat.ms[idx];
            const txtDiv = document.getElementById(`msg-text-${idx}`);
            if (txtDiv) {
                txtDiv.innerHTML = msg.r === 'b' ? window.renderMarkdown(msg.c) : msg.c.replace(/</g, '&lt;').replace(/>/g, '&gt;');
                if (msg.r === 'b') txtDiv.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
            }
        }

        async function submitEdit(idx, container) {
            const newText = container.querySelector('textarea').value.trim();
            if (!newText) return;
            let chat = chats.find(c => c.id === activeId);
            if (!chat) return;
            chat.ms = chat.ms.slice(0, idx);
            loadChat(activeId);
            triggerBotReaction(newText);
            document.getElementById('prompt').value = newText;
            send();
        }

        function triggerBotReaction(txt) {
            const low = txt.toLowerCase();
            if (low.match(/\b(hi|hello|hey)\b/)) {
                window.botState = 'wave';
                setTimeout(() => { window.botState = 'idle'; updateBotVisuals(); }, 3000);
            } else if (low.includes("how are you")) {
                window.botState = 'thumbsUp';
                setTimeout(() => { window.botState = 'idle'; updateBotVisuals(); }, 3000);
            }
            updateBotVisuals();
        }

        function updateBotVisuals() {
            document.querySelectorAll('.bot-bubble').forEach(b => {
                b.style.display = window.botState !== 'idle' ? 'block' : 'none';
            });
        }

        function popBot() {
            const logo = document.getElementById('main-logo-img');
            if (logo) {
                logo.classList.add('logo-pop');
                setTimeout(() => logo.classList.remove('logo-pop'), 600);
            }
        }
        function hitBot() {
            const logo = document.getElementById('main-logo-img');
            if (logo) {
                logo.classList.add('logo-jiggle');
                setTimeout(() => logo.classList.remove('logo-jiggle'), 500);
            }
        }

        // --- Extreme Watchful Teacher Tracking ---
        function trackCursor(e) {
            const logo = document.getElementById('main-logo-img');
            if (!logo || window.innerWidth <= 850) return;
            if (tiltSettleTimer) clearTimeout(tiltSettleTimer);
            const rect = logo.getBoundingClientRect();
            const centerX = rect.left + rect.width / 2;
            const centerY = rect.top + rect.height / 2;
            const dx = e.clientX - centerX;
            const dy = e.clientY - centerY;
            const maxRotation = 35;
            const rotX = Math.max(-maxRotation, Math.min(maxRotation, -dy / 12));
            const rotY = Math.max(-maxRotation, Math.min(maxRotation, dx / 12));
            const moveX = Math.max(-10, Math.min(10, dx / 50));
            const moveY = Math.max(-10, Math.min(10, dy / 50));
            logo.style.transform = `perspective(600px) rotateX(${rotX}deg) rotateY(${rotY}deg) translate3d(${moveX}px, ${moveY}px, 0)`;
            tiltSettleTimer = setTimeout(() => {
                logo.style.transform = `perspective(600px) rotateX(0) rotateY(0) translate3d(0, 0, 0)`;
            }, 2000);
        }

        function previewImg(i) {
            if (i.files && i.files[0]) {
                const reader = new FileReader();
                reader.onload = (e) => {
                    currentImg = e.target.result.split(',')[1];
                    if (currentBlobUrl) URL.revokeObjectURL(currentBlobUrl);
                    currentBlobUrl = URL.createObjectURL(i.files[0]);
                    const area = document.getElementById('img-preview-area');
                    area.style.display = 'flex';
                    area.innerHTML = `
                        <div class="img-thumb-wrap">
                            <img src="${currentBlobUrl}" class="img-thumb">
                            <button class="img-remove-btn" onclick="clearImgPreview()">✕</button>
                        </div>
                    `;
                    selModel('moondream', 'Moondream (Vision)');
                };
                reader.readAsDataURL(i.files[0]);
            }
        }

        function clearImgPreview() {
            if (currentBlobUrl) URL.revokeObjectURL(currentBlobUrl);
            currentBlobUrl = null; currentImg = null;
            document.getElementById('img-in').value = '';
            const area = document.getElementById('img-preview-area');
            area.style.display = 'none'; area.innerHTML = '';
        }

        function showDeleteConfirm(id, e) {
            if (e) e.stopPropagation(); chatToDelete = id;
            document.getElementById('delete-confirm-modal').style.display = 'flex';
            document.getElementById('confirm-del-btn').onclick = () => {
                chats = chats.filter(c => c.id !== chatToDelete);
                saveUserChats();
                if (activeId === chatToDelete) startNewChat();
                else renderHist();
                closeDeleteConfirm();
            };
        }

        function closeDeleteConfirm() {
            document.getElementById('delete-confirm-modal').style.display = 'none';
            chatToDelete = null;
        }

        async function send() {
            const p = document.getElementById('prompt').value.trim();
            if (!p && !currentImg) return;
            if (!activeId) activeId = Date.now().toString();
            let chat = chats.find(c => c.id === activeId);
            if (!chat) { chat = { id: activeId, title: p.substring(0, 35), ms: [] }; chats.push(chat); }
            window.activeId = activeId;
            document.getElementById('welcome').style.display = 'none';
            document.getElementById('chat-area').style.display = 'block';

            let isMasked = false;
            const promptEl = document.getElementById('prompt');
            const isAuthWaiting = promptEl && promptEl.classList.contains('auth-waiting');

            if (isAuthWaiting) {
                isMasked = true;
            } else if (chat.ms.length > 0) {
                const lastMsg = chat.ms[chat.ms.length - 1].c.toLowerCase();
                const authKW = ["please provide your admin key", "enter your admin_key", "provide the password", "authorize with your key"];
                if (authKW.some(kw => lastMsg.includes(kw))) {
                    isMasked = true;
                }
            }

            addMsg('u', p, currentImg, chat.ms.length, null, isMasked);
            chat.ms.push({ r: 'u', c: p, i: currentImg, masked: isMasked });
            triggerBotReaction(p);
            clearImgPreview();
            promptEl.value = ''; promptEl.style.height = 'auto';
            document.getElementById('stop-btn').style.display = 'flex';
            document.getElementById('main-send-btn').style.display = 'none';
            promptEl.placeholder = "Message The All Time Helper...";
            promptEl.classList.remove('auth-waiting');

            let initialContent = '...';
            const isLocal = selectedModel !== 'agentic-pro' && !selectedModel.includes('gemini');
            if (isLocal) initialContent = 'Thinking... (Local Agent initializing tools, may take 10-20s)';
            
            const mName = document.getElementById('active-model-name').innerText;
            const bTxt = addMsg('b', initialContent, null, chat.ms.length, mName);
            const parentMsg = bTxt.closest('.msg');
            if (parentMsg) parentMsg.classList.add('thinking-state');
            
            bTxt.innerHTML = `
                <div class="status-msg"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><circle cx="12" cy="12" r="10"></circle><path d="M12 6v6l4 2"></path></svg> <span id="status-text">${initialContent}</span></div>
                <div class="typing-indicator"><span></span><span></span><span></span></div>
            `;
            updateBotVisuals();
            const mascot = document.getElementById('mascot-container');
            if (mascot) mascot.classList.add('thinking');
            abortC = new AbortController();

            try {
                const token = localStorage.getItem('helper_token_v2') || '';
                const res = await fetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}`, 'ngrok-skip-browser-warning': '69420' },
                    body: JSON.stringify({
                        prompt: p, history: chat.ms, model: selectedModel, img: currentImg, name: user.name,
                        persona: document.getElementById('persona-toggle').checked, isMasked: isMasked,
                        sys: { english: document.getElementById('t-eng').classList.contains('on'), oneword: document.getElementById('t-word').classList.contains('on'), pers: document.getElementById('t-pers').classList.contains('on') }
                    }),
                    signal: abortC.signal
                });
                if (res.status === 401) { signOut(); return; }

                if (!res.ok) {
                    const errorText = `System Error ${res.status}: The backend is currently overloaded or experiencing rate limits. Please try again in a few seconds.`;
                    bTxt.innerText = errorText;
                    chat.ms.push({ r: 'b', c: errorText });
                    saveUserChats();
                    return;
                }

                const reader = res.body.getReader(); let fullTxt = ''; let buffer = ''; const decoder = new TextDecoder("utf-8");
                while (true) {
                    const { done, value } = await reader.read(); if (done) break;
                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n'); buffer = lines.pop();
                    lines.forEach(line => {
                        const trimmedLine = line.trim();
                        if (!trimmedLine || trimmedLine.startsWith('<')) return;
                        try {
                            const j = JSON.parse(trimmedLine);
                            if (j.status) {
                                let statusEl = bTxt.querySelector('#status-text');
                                if (!statusEl) {
                                    const statusDiv = document.createElement('div');
                                    statusDiv.className = 'status-msg';
                                    statusDiv.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><circle cx="12" cy="12" r="10"></circle><path d="M12 6v6l4 2"></path></svg> <span id="status-text">${j.status}</span>`;
                                    bTxt.prepend(statusDiv);
                                } else { statusEl.innerText = j.status; }
                                return;
                            }
                            if (j.message && j.message.content) {
                                if (fullTxt === '') {
                                    bTxt.querySelector('.typing-indicator')?.remove();
                                    bTxt.querySelector('.status-msg')?.remove();
                                    bTxt.closest('.msg').classList.remove('thinking-state');
                                }
                                fullTxt += j.message.content; bTxt.innerHTML = window.renderMarkdown(fullTxt);
                            }
                        } catch (e) {
                            if (trimmedLine.length > 5) console.warn("Dropped malformed line:", trimmedLine);
                        }
                    });
                }
                // Process remaining buffer
                if (buffer.trim()) {
                    try { const j = JSON.parse(buffer); if (j.message && j.message.content) fullTxt += j.message.content; } catch (e) { }
                }
                if (chat.title && chat.title.trim().length <= 5 && fullTxt.trim().length > 10) {
                    const firstLine = fullTxt.split('\n')[0];
                    chat.title = firstLine.substring(0, 35).trim() + (firstLine.length > 35 ? '...' : '');
                }
                chat.ms.push({ r: 'b', c: fullTxt, m: document.getElementById('active-model-name').innerText });
                bTxt.innerHTML = window.renderMarkdown(fullTxt);
                bTxt.querySelectorAll('img').forEach(img => { if (img.src.includes('uid=')) { const jId = new URLSearchParams(img.src.split('?')[1]).get('uid'); if (jId) startUpscalePoller(jId, bTxt); } });
                bTxt.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
                saveUserChats();
            } catch (e) { bTxt.innerText += " [Stopped]"; }
            finally {
                document.getElementById('stop-btn').style.display = 'none';
                document.getElementById('main-send-btn').style.display = 'flex';
                checkAuthMode();
                if (mascot) mascot.classList.remove('thinking');
                document.querySelectorAll('.thinking-state').forEach(m => m.classList.remove('thinking-state'));
                document.querySelectorAll('.typing-indicator').forEach(ti => ti.remove());
                abortC = null; currentImg = null;
                window.activeId = activeId;
                if (window.activeId === activeId && chats.find(c => c.id === activeId)?.ms.length <= 2) renderHist();
                checkAuthMode();
            }
        }

        function stopAI() { if (abortC) abortC.abort(); }

        async function loadUserChats() {
            if (!user || !user.email) return;
            const key = 'helper_chats_v2_' + user.email;
            let localStr = localStorage.getItem(key);
            if (!localStr && localStorage.getItem('helper_chats_v2')) {
                // Migration from global key
                localStr = localStorage.getItem('helper_chats_v2');
                localStorage.setItem(key, localStr);
                localStorage.removeItem('helper_chats_v2');
            }
            if (localStr) {
                chats = JSON.parse(localStr); window.chats = chats; renderHist();
                const sId = localStorage.getItem('helper_active_chat_v2');
                if (sId && chats.find(c => c.id === sId)) loadChat(sId);
                console.log("DEBUG: Loaded chats from local storage:", chats.length);
            }
            const token = localStorage.getItem('helper_token_v2');
            if (token) {
                try {
                    console.log("DEBUG: Fetching chats from cloud...");
                    const res = await fetch('/get_chats', { headers: { 'Authorization': `Bearer ${token}`, 'ngrok-skip-browser-warning': '69420' } });
                    const data = await res.json();
                    if (data.success && data.chats) {
                        console.log("DEBUG: Cloud sync returned chats:", data.chats.length);
                        if (data.chats.length > 0 || chats.length === 0) {
                            chats = data.chats; window.chats = chats;
                            localStorage.setItem(key, JSON.stringify(chats)); renderHist();
                        }
                    }
                } catch (e) { console.error("Cloud fetch failed:", e); }
            } else {
                window.chats = chats;
                renderHist();
            }
        }

        async function saveUserChats() {
            if (!user) return;
            localStorage.setItem('helper_chats_v2_' + user.email, JSON.stringify(chats));
            const token = localStorage.getItem('helper_token_v2');
            if (token) {
                try { await fetch('/sync_chats', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}`, 'ngrok-skip-browser-warning': '69420' }, body: JSON.stringify(chats) }); } catch (e) { }
            }
        }

        const savedUser = localStorage.getItem('helper_user_v2');
        if (savedUser) {
            user = JSON.parse(savedUser); document.getElementById('auth-overlay').style.display = 'none';
            loadUserChats();
            if (localStorage.getItem('helper_active_modal_v2') === 'settings') openSettings();
            updUI();
            if (!localStorage.getItem('helper_theme_pref')) document.getElementById('theme-modal').style.display = 'flex';
            smartFocus('prompt');
        } else {
            document.getElementById('l-email').focus();
            renderHist();
        }

        document.addEventListener('mousemove', trackCursor);
        document.addEventListener('mouseleave', () => {
            const logo = document.getElementById('main-logo-img');
            if (logo) logo.style.transform = 'perspective(600px) rotateX(0) rotateY(0) translate3d(0, 0, 0)';
        });

        const promptIn = document.getElementById('prompt');
        const sendBtn = document.getElementById('main-send-btn');
        if (promptIn) {
            promptIn.addEventListener('input', () => {
                window.autoRes(promptIn);
                sendBtn?.classList.toggle('pulsing', promptIn.value.trim().length > 0);
            });
            promptIn.addEventListener('keydown', handleChatKey);
        }

        const personaToggle = document.getElementById('persona-toggle');
        const personaItem = document.querySelector('.persona-switch-item');
        function syncPersonaUI() {
            if (personaToggle && personaItem) personaItem.classList.toggle('persona-active', personaToggle.checked);
        }
        if (personaToggle) { personaToggle.addEventListener('change', syncPersonaUI); syncPersonaUI(); }

        document.addEventListener('click', (e) => {
            const sb = document.getElementById('sidebar');
            if (window.innerWidth <= 850 && sb?.classList.contains('open') && !sb.contains(e.target) && !document.getElementById('mobile-menu-btn')?.contains(e.target)) toggleSidebar();
            const tm = document.getElementById('theme-menu');
            if (tm && tm.style.display === 'flex' && !tm.contains(e.target) && !document.getElementById('theme-btn')?.contains(e.target) && !document.getElementById('theme-btn-settings')?.contains(e.target)) tm.style.display = 'none';
            const mm = document.getElementById('model-menu');
            if (mm && mm.classList.contains('active') && !mm.contains(e.target) && !document.getElementById('model-toggle')?.contains(e.target)) mm.classList.remove('active');
        });

        function openImageModal(src) {
            const m = document.getElementById('image-modal'); const img = document.getElementById('modal-img');
            if (m && img) { img.src = src; img.classList.remove('is-zoomed'); m.style.display = 'flex'; setTimeout(() => m.classList.add('active'), 10); history.pushState({ view: 'image' }, ""); }
        }
        function closeImageModal() {
            const m = document.getElementById('image-modal'); const img = document.getElementById('modal-img');
            if (m) { m.classList.remove('active'); img?.classList.remove('is-zoomed'); setTimeout(() => m.style.display = 'none', 300); }
        }

        (function initImageZoom() {
            const img = document.getElementById('modal-img'); const cont = document.getElementById('image-modal');
            if (!img || !cont) return;
            img.onclick = (e) => { e.stopPropagation(); img.classList.toggle('is-zoomed'); };
            cont.onclick = (e) => { if (e.target === cont) { img.classList.remove('is-zoomed'); closeImageModal(); } };
        })();

        window.addEventListener('popstate', (e) => {
            if (document.getElementById('image-modal')?.classList.contains('active')) { closeImageModal(); return; }
            if (document.getElementById('settings-modal')?.style.display === 'flex') { closeSettings(); return; }
            if (document.getElementById('sidebar')?.classList.contains('open')) { toggleSidebar(); return; }
            const conf = document.getElementById('delete-confirm-modal');
            if (conf && conf.style.display === 'flex') { conf.style.display = 'none'; return; }
        });

        window.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                if (document.getElementById('image-modal')?.classList.contains('active')) { closeImageModal(); return; }
                if (document.getElementById('settings-modal')?.style.display === 'flex') { closeSettings(); return; }
                if (document.getElementById('delete-confirm-modal')?.style.display === 'flex') { document.getElementById('delete-confirm-modal').style.display = 'none'; return; }
                if (document.getElementById('sidebar')?.classList.contains('open')) { toggleSidebar(); return; }
            }
        });

        function handleDragStart(e) {
            e.dataTransfer.setData('text/plain', e.currentTarget.innerText);
            e.currentTarget.parentElement.classList.add('dragging');
            document.getElementById('mascot-container')?.classList.add('mascot-drop-active');
        }
        function handleDragEnd(e) {
            e.currentTarget.parentElement.classList.remove('dragging');
            document.getElementById('mascot-container')?.classList.remove('mascot-drop-active');
        }

        function initMascotDrop() {
            const m = document.getElementById('mascot-container'); if (!m) return;
            m.ondragover = (e) => { e.preventDefault(); m.classList.add('mascot-drop-active'); };
            m.ondragleave = () => m.classList.remove('mascot-drop-active');
            m.ondrop = async (e) => {
                e.preventDefault(); m.classList.remove('mascot-drop-active');
                const txt = e.dataTransfer.getData('text/plain');
                if (txt) retrieveContext(txt);
            };
        }

        async function retrieveContext(text) {
            const m = document.getElementById('mascot-container'); if (m) m.classList.add('thinking');
            try {
                const res = await fetch('/retrieve_context', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${localStorage.getItem('helper_token_v2')}`, 'ngrok-skip-browser-warning': '69420' }, body: JSON.stringify({ text, n: 3 }) });
                const data = await res.json();
                if (data.success) showNeuralContext(data.results, data.explanation);
            } finally { if (m) m.classList.remove('thinking'); }
        }

        function showNeuralContext(results, explanation) {
            const card = document.getElementById('neural-context-card');
            const cont = document.getElementById('context-results');
            const scrim = document.getElementById('neural-scrim');
            if (!card || !cont || !scrim) return;
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
                div.innerHTML = `<span class="context-meta">${res.metadata?.type || 'DOCUMENT'}</span><div style="max-height: 150px; overflow-y: auto; font-size: 0.85rem; color: var(--text-main);">${res.content}</div>`;
                cont.appendChild(div);
            });
            card.classList.add('active'); scrim.classList.add('active');
        }

        let touchStartY = 0, touchDiffY = 0;
        window.addEventListener('touchstart', (e) => {
            const y = e.touches[0].pageY;
            if ((window.scrollY === 0 || document.getElementById('chat-area').scrollTop === 0) && y < 60) touchStartY = y;
            else touchStartY = 999999;
        }, { passive: true });
        window.addEventListener('touchmove', (e) => {
            const y = e.touches[0].pageY; touchDiffY = y - touchStartY;
            if (touchDiffY > 0 && (window.scrollY === 0 || document.getElementById('chat-area').scrollTop === 0)) {
                const ind = document.getElementById('pull-indicator');
                if (ind) { const p = Math.min(touchDiffY, 180); ind.style.top = (p - 60) + 'px'; ind.style.opacity = Math.min(p / 120, 1); }
            }
        }, { passive: true });
        window.addEventListener('touchend', () => {
            if (touchDiffY > 120) location.reload();
            else { const ind = document.getElementById('pull-indicator'); if (ind) { ind.style.top = '-60px'; ind.style.opacity = '0'; } }
            touchDiffY = 0;
        });

        // Liquid-Glass Interactions
        function jiggleLogo() { hitBot(); }

        const logoImg = document.getElementById('main-logo-img');
        if (logoImg) logoImg.addEventListener('click', () => { jiggleLogo(); });

        // --- Global Function Mapping for HTML onclick ---
        window.handleAuth = handleAuth;
        window.switchAuth = switchAuth;
        window.signOut = signOut;
        window.toggleDropdown = toggleDropdown;
        window.selModel = selModel;
        window.send = send;
        window.startNewChat = startNewChat;
        window.loadChat = loadChat;
        window.showDeleteConfirm = showDeleteConfirm;
        window.closeDeleteConfirm = closeDeleteConfirm;
        window.clearImgPreview = clearImgPreview;
        window.previewImg = previewImg;
        window.toggleSidebar = toggleSidebar;
        window.triggerBotReaction = triggerBotReaction;
        // copyCode & downloadCode are defined in utils.js (loaded first)
        window.startEditPrompt = startEditPrompt;
        window.cancelEdit = cancelEdit;
        window.submitEdit = submitEdit;
        window.openSettings = openSettings;
        window.closeSettings = closeSettings;
        window.handleChatKey = handleChatKey;
        window.autoRes = function (el) {
            if (!el) return;
            el.style.height = 'auto';
            el.style.height = (el.scrollHeight) + 'px';
        };
        window.stopAI = stopAI;
        window.openImageModal = openImageModal;
        window.closeImageModal = closeImageModal;
        window.toggleSet = toggleSet;
        window.filterHist = filterHist;
        window.startRename = startRename;
        window.closeNeuralContext = function() {
            const card = document.getElementById('neural-context-card');
            const scrim = document.getElementById('neural-scrim');
            if (card) card.classList.remove('active');
            if (scrim) scrim.classList.remove('active');
        };
        window.handleDragStart = handleDragStart;
        window.handleDragEnd = handleDragEnd;
        window.jiggleLogo = jiggleLogo;

        initMascotDrop();

        // --- Neural Grab Logic (Hold 'G' to Enable Dragging) ---
        window.isGDown = false;
        function updateDraggableState(enabled) {
            window.isGDown = enabled;
            const msgs = document.querySelectorAll('.msg .txt');
            msgs.forEach(m => {
                m.setAttribute('draggable', enabled ? 'true' : 'false');
                if (enabled) m.classList.add('grab-mode');
                else m.classList.remove('grab-mode');
            });
            if (enabled) document.body.classList.add('neural-grab-active');
            else document.body.classList.remove('neural-grab-active');
        }

        document.addEventListener('keydown', (e) => {
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
            if (e.key.toLowerCase() === 'g' && !window.isGDown) {
                updateDraggableState(true);
            }
        });
        document.addEventListener('keyup', (e) => {
            if (e.key.toLowerCase() === 'g') {
                updateDraggableState(false);
            }
        });
        window.addEventListener('blur', () => {
            updateDraggableState(false);
        });

        initSidebarSwipe();
    } catch (e) { console.error("Critical Runtime Error in Dashboard:", e); }
});

function initSidebarSwipe() {
    const sb = document.getElementById('sidebar'); const scr = document.getElementById('sidebar-scrim');
    let sX = 0, sY = 0, cX = 300, isD = false, isH = false;
    if (!sb || !scr) return;
    sb.addEventListener('touchstart', (e) => {
        if (!sb.classList.contains('open') || window.innerWidth > 992) return;
        sX = e.touches[0].clientX; sY = e.touches[0].clientY; cX = 300; isD = true; isH = false;
        sb.style.transition = 'none'; scr.style.transition = 'none';
    }, { passive: true });
    sb.addEventListener('touchmove', (e) => {
        if (!isD) return;
        const tX = e.touches[0].clientX, tY = e.touches[0].clientY, dX = tX - sX, dY = tY - sY;
        if (!isH) {
            if (Math.abs(dX) > Math.abs(dY) * 1.5) isH = true;
            else if (Math.abs(dY) > 5) { isD = false; return; }
            else return;
        }
        cX = Math.min(300, Math.max(0, 300 + dX));
        sb.style.transform = `translateX(${cX}px)`; scr.style.opacity = cX / 300;
    }, { passive: true });
    sb.addEventListener('touchend', () => {
        if (!isD) return; isD = false;
        sb.style.transition = 'transform 0.4s cubic-bezier(0.25, 0.8, 0.25, 1)'; scr.style.transition = 'opacity 0.4s ease';
        if (cX < 200) {
            sb.style.transform = 'translateX(0px)'; scr.style.opacity = '0';
            setTimeout(() => { sb.classList.remove('open'); document.body.classList.remove('sidebar-open'); sb.style.transform = ''; sb.style.transition = ''; scr.style.opacity = ''; scr.style.transition = ''; }, 400);
        } else {
            sb.style.transform = 'translateX(300px)'; scr.style.opacity = '1';
            setTimeout(() => { sb.style.transition = ''; scr.style.transition = ''; }, 400);
        }
    });
    sb.onclick = (e) => e.stopPropagation();
}

// renderMarkdown, copyCode, downloadCode → canonical versions live in utils.js
