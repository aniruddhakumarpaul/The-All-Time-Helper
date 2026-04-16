document.addEventListener('DOMContentLoaded', () => {
    console.log("DEBUG: All Time Helper Script Initializing...");
    // Global Bot State
        window.botState = 'idle';
        const LOGO_DATA = "/static/img/logo.png";
        const LOGO_LIGHT_DATA = "/static/img/logo(2).jpg";
        const BOT_DATA = "/static/img/bot.png";
        let user = null; 
        let chats = [];
        let activeId = null; window.activeId = null; // Exposed globally for inline handlers
        let abortC = null; let currentImg = null;
        let selectedModel = 'agentic-pro';
        let currentBlobUrl = null;
        let chatToDelete = null;
        let isRenaming = false;
        let currentSearch = '';
        
        // --- Core Helpers (Hoisted to Top) ---
        function smartFocus(id) {
            if (window.innerWidth > 850) {
                const el = document.getElementById(id);
                if (el) el.focus();
            }
        }

        // Auth
        function switchAuth(t) { 
            ['login', 'signup', 'otp'].forEach(f => { document.getElementById(f+'-form').style.display = (f === t ? 'block' : 'none'); }); 
            if(t==='login') document.getElementById('l-email').focus();
            if(t==='signup') document.getElementById('s-name').focus();
            if(t==='otp') document.getElementById('v-otp').focus();
        }

        function updUI() {
            if(user) {
                const nameStr = user.name || 'Friend';
                
                const sbGreet = document.getElementById('sidebar-greet');
                if(sbGreet) sbGreet.innerText = 'Hello, ' + nameStr;
                
                const cGreet = document.getElementById('center-greet');
                if(cGreet) cGreet.innerHTML = `Hello, <span style="background: var(--greet-grad); background-clip: text; -webkit-background-clip: text; -webkit-text-fill-color: transparent;">${nameStr}</span>`;
                
                const uInfo = document.getElementById('user-info');
                if(uInfo) uInfo.innerText = 'Signed in as ' + user.email;
            }
        }

        async function handleAuth(t) {
            const btn = document.getElementById(t + '-btn');
            const originalText = btn.innerHTML;
            
            let p = {};
            if(t==='login') p = {email: document.getElementById('l-email').value, pwd: document.getElementById('l-pwd').value};
            else if(t==='signup') p = {email: document.getElementById('s-email').value, pwd: document.getElementById('s-pwd').value, name: document.getElementById('s-name').value };
            else if(t==='verify') p = {email: document.getElementById('s-email').value || document.getElementById('l-email').value, otp: document.getElementById('v-otp').value};

            // Loading state
            btn.disabled = true;
            btn.innerHTML = '<div class="spinner"></div>';

            try {
                const res = await fetch('/'+t, { 
                    method: 'POST', 
                    headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '69420' },
                    body: JSON.stringify(p) 
                });
                const data = await res.json();
                if(data.success) {
                    if(t === 'signup' || (t === 'login' && data.unverified)) switchAuth('otp');
                    else {
                        user = data.user; localStorage.setItem('helper_user_v2', JSON.stringify(user));
                        if(data.token) localStorage.setItem('helper_token_v2', data.token);
                        document.getElementById('auth-overlay').style.display = 'none';
                        loadUserChats();
                        updUI();
                        
                        // Theme Onboarding 
                        if(!localStorage.getItem('helper_theme_pref')) {
                            document.getElementById('theme-modal').style.display = 'flex';
                        }
                        
                        smartFocus('prompt');
                    }
                } else alert(data.error || 'Check credentials');
            } catch(e) { 
                console.error("Auth Fail:", e);
                alert('Connection Error: ' + e.message + '\n(Check if server is running at ' + location.origin + ')'); 
            }
            finally {
                btn.disabled = false;
                btn.innerHTML = originalText;
            }
        }

        function signOut() { localStorage.removeItem('helper_user_v2'); localStorage.removeItem('helper_token_v2'); location.reload(); }

        // Dropdown
        function toggleDropdown() { document.getElementById('model-menu').style.display = (document.getElementById('model-menu').style.display === 'flex' ? 'none' : 'flex'); }
        function selModel(id, name) { 
            selectedModel = id; 
            document.getElementById('active-model-name').innerText = name; 
            document.getElementById('model-menu').style.display = 'none'; 
        }

        // Sidebar Toggle
        function toggleSidebar() { 
            const sb = document.getElementById('sidebar');
            const isOpen = sb.classList.toggle('open');
            document.body.classList.toggle('sidebar-open', isOpen);
            
            if (isOpen) {
                history.pushState({ view: 'sidebar' }, "");
            }
        }
        
        // Settings
        function openSettings() { 
            document.getElementById('settings-modal').style.display = 'flex'; 
            history.pushState({ view: 'settings' }, "");
        }
        function closeSettings() { document.getElementById('settings-modal').style.display = 'none'; document.getElementById('prompt').focus(); }

        function handleChatKey(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                send();
            } else if (e.key === 'Escape') {
                startNewChat();
            }
        }

        window.toggleThemeMenu = function(e, menuId) {
            if(e) e.stopPropagation();
            const target = menuId || 'theme-menu';
            const menu = document.getElementById(target);
            if(!menu) return;
            
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
        window.applyThemeChoice = function(choice) {
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
                if(text === choice || text.includes(choice)) o.classList.add('active');
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
            
            if(document.getElementById('theme-modal').style.display === 'flex') {
                setTimeout(() => document.getElementById('theme-modal').style.display = 'none', 400);
            }
        }

        function setThemeUI(theme) {
            document.body.setAttribute('data-theme', theme);
            const isDark = theme === 'dark';
            // Swapped: Logo is original for Dark, and (2) for Light
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
        document.getElementById('active-model-name').innerText = 'Agentic Swarm (Pro)';
        
        // -----------------------
        function toggleSet(id) { document.getElementById(id).classList.toggle('on'); }

        function startNewChat() {
            activeId = Date.now().toString();
            document.getElementById('chat-area').innerHTML = '';
            document.getElementById('chat-area').style.display = 'none';
            document.getElementById('welcome').style.display = 'flex';
            clearImgPreview();
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
            const list = document.getElementById('history-list'); if(!list) return;
            list.innerHTML = '';
            
            // Sorting: Pinned first, then by recency
            const sorted = chats.slice().reverse().sort((a,b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0));
            
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
                div.onclick = (e) => { if(!e.target.closest('.del-chat-btn')) loadChat(c.id); };
                list.appendChild(div);
            });
        }

        window.togglePin = (id) => {
            const chat = chats.find(c => c.id === id);
            if(chat) {
                chat.pinned = !chat.pinned;
                saveUserChats();
                renderHist();
                
                // Gentle Animation: Scroll list to top to follow the pinned chat
                const histList = document.getElementById('history-list');
                if (histList) {
                    histList.scrollTo({ top: 0, behavior: 'smooth' });
                }
            }
        };

        window.exportChat = () => {
            const chat = chats.find(c => c.id === activeId);
            if(!chat || !chat.ms.length) return;
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
                if(ev.key === 'Enter') { ev.stopPropagation(); saveRename(id, input.value); } 
                if(ev.key === 'Escape') { ev.stopPropagation(); isRenaming = false; renderHist(); }
            };
        }

        function saveRename(id, val) {
            if (!isRenaming) return;
            const chat = chats.find(c => c.id === id);
            if(chat && val.trim()) { chat.title = val.trim(); saveUserChats(); }
            isRenaming = false;
            renderHist();
        }

        function loadChat(id) {
            activeId = id; const chat = chats.find(c => c.id === id);
            document.getElementById('chat-area').innerHTML = '';
            document.getElementById('chat-area').style.display = 'block';
            document.getElementById('welcome').style.display = 'none';
            clearImgPreview();
            chat.ms.forEach((m, idx) => addMsg(m.r, m.c, m.i, idx));
            renderHist();
            
            // Auto-close sidebar on mobile after selection
            if (window.innerWidth <= 850 && document.getElementById('sidebar').classList.contains('open')) {
                toggleSidebar();
            }
            
            smartFocus('prompt');
        }





        function addMsg(r, c, i, idx) {
            const div = document.createElement('div');
            div.className = `msg ${r}-msg`;
            
            const name = user ? user.name : 'User';
            const initial = name.charAt(0).toUpperCase();
            
            const avatarHtml = r === 'u' 
                ? `<div class="av u-av"><span class="initial-letter">${initial}</span><span class="full-name">${name}</span></div>`
                : `<div class="av b-av" id="bot-av-${idx}">
                    <svg class="orb-svg" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
                        <defs>
                            <radialGradient id="orbGrad-${idx}" cx="50%" cy="50%" r="50%">
                                <stop offset="0%" style="stop-color:#00ffff;stop-opacity:1" />
                                <stop offset="40%" style="stop-color:#00ffff;stop-opacity:0.6" />
                                <stop offset="100%" style="stop-color:#00ffff;stop-opacity:0" />
                            </radialGradient>
                            <filter id="orbGlow-${idx}" x="-50%" y="-50%" width="200%" height="200%">
                                <feGaussianBlur in="SourceGraphic" stdDeviation="5" />
                            </filter>
                        </defs>
                        <circle cx="50%" cy="50%" r="40" fill="url(#orbGrad-${idx})" filter="url(#orbGlow-${idx})" />
                        <circle cx="50%" cy="50%" r="25" fill="url(#orbGrad-${idx})" />
                    </svg>
                    <div class="bot-bubble" id="bot-bubble-${idx}">I am great!</div>
                   </div>`;

            const content = r === 'b' ? renderMarkdown(c) : c.replace(/</g,'&lt;').replace(/>/g,'&gt;');
            
            let tools = '';
            if(r === 'u' && idx !== undefined) {
                tools = `<div class="msg-tools">
                            <div class="tool-icon" onclick="startEditPrompt(${idx}, this)" title="Edit Prompt">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
                            </div>
                         </div>`;
            }

            div.innerHTML = `
                <div class="av-wrap">
                    ${avatarHtml}
                    <div style="font-size: 0.8rem; color: var(--text-sub); font-weight: 600; letter-spacing: 0.5px;">
                        ${r === 'u' ? (user ? user.name : 'You') : 'THE ALL TIME HELPER'}
                    </div>
                </div>
                <div class="txt">
                    <div id="msg-text-${idx}">${content}</div>
                    ${i ? `
                        <div class="chat-img-preview-container" onclick="openImageModal('data:image/png;base64,${i}')">
                            <img src="data:image/png;base64,${i}" class="chat-img-preview">
                        </div>` : ''}
                    ${tools}
                </div>
            `;
            
            document.getElementById('chat-area').appendChild(div);
            div.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
            document.getElementById('chat-area').scrollTop = document.getElementById('chat-area').scrollHeight;
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
                <textarea class="edit-area" style="width:100%; min-height:80px; background:rgba(255,255,255,0.05); color:white; border:1px solid var(--accent-blue); border-radius:12px; padding:10px; outline:none; margin-top:10px;">${oldText}</textarea>
                <div style="display:flex; gap:10px; margin-top:10px;">
                    <button class="auth-btn" style="padding:8px 15px; margin:0; font-size:0.8rem;" onclick="submitEdit(${idx}, this.parentElement.parentElement)">Save & Submit</button>
                    <button class="auth-btn" style="padding:8px 15px; margin:0; font-size:0.8rem; background:rgba(255,255,255,0.1); color:white;" onclick="loadChat(activeId)">Cancel</button>
                </div>
            `;
        }

        async function submitEdit(idx, container) {
            const newText = container.querySelector('textarea').value.trim();
            if(!newText) return;
            triggerBotReaction(newText);
            let chat = chats.find(c => c.id === activeId);
            // Slice history to remove everything AFTER this prompt
            chat.ms = chat.ms.slice(0, idx);
            // Set prompt and send
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
            const bubbles = document.querySelectorAll('.bot-bubble');
            bubbles.forEach(b => {
                if (window.botState !== 'idle') b.style.display = 'block';
                else b.style.display = 'none';
            });
        }

        function popBot(idx) {}
        function hitBot(idx) {}

        function trackCursor(e) {}

        function previewImg(i) {
            if (i.files && i.files[0]) {
                const file = i.files[0];
                const reader = new FileReader();
                reader.onload = (e) => { 
                    currentImg = e.target.result.split(',')[1];
                    if (currentBlobUrl) URL.revokeObjectURL(currentBlobUrl);
                    currentBlobUrl = URL.createObjectURL(file);
                    
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
                reader.readAsDataURL(file);
            }
        }

        function clearImgPreview() {
            if (currentBlobUrl) URL.revokeObjectURL(currentBlobUrl);
            currentBlobUrl = null;
            currentImg = null;
            document.getElementById('img-in').value = '';
            const area = document.getElementById('img-preview-area');
            area.style.display = 'none';
            area.innerHTML = '';
        }

        function showDeleteConfirm(id, e) {
            if(e) e.stopPropagation();
            chatToDelete = id;
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
            if(!p && !currentImg) return;
            if(!activeId) activeId = Date.now().toString();
            let chat = chats.find(c => c.id === activeId);
            if(!chat) { chat = {id: activeId, title: p.substring(0,35), ms: []}; chats.push(chat); }
            window.activeId = activeId;

            document.getElementById('welcome').style.display = 'none';
            document.getElementById('chat-area').style.display = 'block';
            
            addMsg('u', p, currentImg, chat.ms.length);
            chat.ms.push({r: 'u', c: p, i: currentImg});
            triggerBotReaction(p);
            clearImgPreview();
            document.getElementById('prompt').value = '';
            document.getElementById('stop-btn').style.display = 'flex';
            
            let initialContent = '...';
            const isLocal = selectedModel !== 'agentic-pro' && !selectedModel.includes('gemini');
            if (isLocal) {
                initialContent = 'Thinking... (Local Agent initializing tools, may take 10-20s)';
            }
            const bTxt = addMsg('b', initialContent, null, chat.ms.length); 
            if (initialContent === '...') bTxt.innerText = '';
            updateBotVisuals();
            abortC = new AbortController();
            
            try {
                const token = localStorage.getItem('helper_token_v2') || '';
                const res = await fetch('/chat', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${token}`,
                        'ngrok-skip-browser-warning': '69420'
                    },
                    body: JSON.stringify({
                        prompt: p, 
                        history: chat.ms, 
                        model: selectedModel,
                        img: currentImg,
                        name: user.name,
                        sys: {
                            english: document.getElementById('t-eng').classList.contains('on'),
                            oneword: document.getElementById('t-word').classList.contains('on'),
                            pers: document.getElementById('t-pers').classList.contains('on')
                        }
                    }),
                    signal: abortC.signal
                });
                
                if (res.status === 401) {
                    signOut();
                    return;
                }

                if (!res.ok) {
                    const errorText = `System Error ${res.status}: The backend is currently overloaded or experiencing rate limits. Please try again in a few seconds.`;
                    bTxt.innerText = errorText;
                    chat.ms.push({r: 'b', c: errorText});
                    saveChats();
                    return;
                }

                const reader = res.body.getReader(); 
                let fullTxt = '';
                let buffer = '';
                const decoder = new TextDecoder("utf-8");

                while(true) {
                    const {done, value} = await reader.read(); 
                    if(done) break;
                    
                    buffer += decoder.decode(value, {stream: true});
                    const lines = buffer.split('\n');
                    buffer = lines.pop(); // Retain the final incomplete chunk in the buffer
                    
                    lines.forEach(line => {
                        const trimmedLine = line.trim();
                        if(trimmedLine && !trimmedLine.startsWith('<')) { 
                            try {
                                const j = JSON.parse(trimmedLine);
                                if(j.message && j.message.content) { 
                                    fullTxt += j.message.content;
                                    bTxt.innerHTML = renderMarkdown(fullTxt);
                                }
                            } catch(e) {
                                console.warn("Dropped malformed line:", e);
                            } 
                        }
                    });
                }
                
                // Process any remaining buffered payload
                if (buffer.trim()) {
                    try {
                        const j = JSON.parse(buffer);
                        if(j.message && j.message.content) { fullTxt += j.message.content; }
                    } catch(e) {}
                }
                
                // Smart Title Generation: Update title if it's currently a short placeholder
                if (chat.title && chat.title.trim().length <= 5 && fullTxt.trim().length > 10) {
                    const firstLine = fullTxt.split('\n')[0];
                    const newTitle = firstLine.substring(0, 35).trim() + (firstLine.length > 35 ? '...' : '');
                    if (newTitle) chat.title = newTitle;
                }

                chat.ms.push({r: 'b', c: fullTxt});
                // Final render: apply Markdown + syntax highlighting after streaming completes
                bTxt.innerHTML = renderMarkdown(fullTxt);
                bTxt.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
                saveUserChats();
            } catch(e) { bTxt.innerText += " [Stopped]"; }
            finally { 
                document.getElementById('stop-btn').style.display = 'none'; 
                abortC = null; currentImg = null; window.activeId = activeId; renderHist(); 
            }
        }

        function stopAI() { if(abortC) abortC.abort(); }

        async function loadUserChats() {
            if(!user || !user.email) return;
            const key = 'helper_chats_v2_' + user.email;
            
            // 1. Load initial state from LocalStorage immediately
            let localStr = localStorage.getItem(key);
            if (!localStr && localStorage.getItem('helper_chats_v2')) {
                // Migration from global key
                localStr = localStorage.getItem('helper_chats_v2');
                localStorage.setItem(key, localStr);
                localStorage.removeItem('helper_chats_v2');
            }
            
            if (localStr) {
                chats = JSON.parse(localStr);
                renderHist();
                console.log("DEBUG: Loaded chats from local storage:", chats.length);
            }

            // 2. Refresh from Cloud
            const token = localStorage.getItem('helper_token_v2');
            if(token) {
                try {
                    console.log("DEBUG: Fetching chats from cloud...");
                    const res = await fetch('/get_chats', { headers: { 'Authorization': `Bearer ${token}`, 'ngrok-skip-browser-warning': '69420' } });
                    const data = await res.json();
                    if(data.success && data.chats) {
                        console.log("DEBUG: Cloud sync returned chats:", data.chats.length);
                        // Significant safeguard: Only overwrite if cloud has data OR local is empty
                        if(data.chats.length > 0 || chats.length === 0) {
                            chats = data.chats;
                            localStorage.setItem(key, JSON.stringify(chats));
                            renderHist();
                        }
                    }
                } catch(e) { console.error("Cloud fetch failed:", e); }
            } else {
                renderHist();
            }
        }

        async function saveUserChats() {
            if(!user || !user.email) return;
            const key = 'helper_chats_v2_' + user.email;
            localStorage.setItem(key, JSON.stringify(chats));
            
            const token = localStorage.getItem('helper_token_v2');
            if(token) {
                try {
                    console.log("DEBUG: Syncing chats to cloud...", chats.length);
                    const res = await fetch('/sync_chats', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}`, 'ngrok-skip-browser-warning': '69420' },
                        body: JSON.stringify(chats)
                    });
                    const data = await res.json();
                    if(!data.success) console.error("Cloud sync failed server-side:", data.error);
                } catch(e) { console.error("Cloud sync failed:", e); }
            }
        }

        const savedUser = localStorage.getItem('helper_user_v2');
        if(savedUser) { 
            user = JSON.parse(savedUser); document.getElementById('auth-overlay').style.display = 'none';
            loadUserChats();
            updUI();
            
            // Show Onboarding for existing users who haven't picked yet
            if(!localStorage.getItem('helper_theme_pref')) {
                document.getElementById('theme-modal').style.display = 'flex';
            }
            
            smartFocus('prompt');
        } else {
            document.getElementById('l-email').focus();
            renderHist();
        }

        // Liquid-Glass Interactions
        function jiggleLogo() {
            const logo = document.getElementById('main-logo-img');
            if (logo) {
                logo.classList.remove('logo-jiggle'); // reset if already jiggling
                void logo.offsetWidth; // trigger reflow
                logo.classList.add('logo-jiggle');
            }
        }

        const promptIn = document.getElementById('prompt');
        const sendBtn = document.getElementById('main-send-btn');
        if (promptIn) {
            // Auto Resize
            promptIn.addEventListener('input', () => {
                autoRes(promptIn);
                if (promptIn.value.trim().length > 0) {
                    if (sendBtn) sendBtn.classList.add('pulsing');
                } else {
                    if (sendBtn) sendBtn.classList.remove('pulsing');
                }
            });
            // Key Handling
            promptIn.addEventListener('keydown', handleChatKey);
        }

        // Mobile Sidebar & Dropdown Dismissal
        document.addEventListener('click', (e) => {
            const sidebar = document.getElementById('sidebar');
            const menuBtn = document.getElementById('mobile-menu-btn');
            const modelToggle = document.getElementById('model-toggle');
            const modelMenu = document.getElementById('model-menu');
            const themeBtn = document.getElementById('theme-btn');
            const themeSettingsBtn = document.getElementById('theme-btn-settings');
            const themeMenu = document.getElementById('theme-menu');

            // 1. Close Sidebar if clicking outside (Mobile)
            if (window.innerWidth <= 850 && sidebar && sidebar.classList.contains('open')) {
                if (!sidebar.contains(e.target) && (!menuBtn || !menuBtn.contains(e.target))) {
                    toggleSidebar();
                }
            }

            // 2. Close Theme Menus if clicking outside
            const isClickInsideTheme = (themeBtn && themeBtn.contains(e.target)) || 
                                     (themeSettingsBtn && themeSettingsBtn.contains(e.target)) ||
                                     (themeMenu && themeMenu.contains(e.target));
            if (!isClickInsideTheme && themeMenu && themeMenu.style.display === 'flex') {
                themeMenu.style.display = 'none';
            }

            // 3. Close Model Menu if clicking outside
            const isClickInsideModel = (modelToggle && modelToggle.contains(e.target)) || 
                                     (modelMenu && modelMenu.contains(e.target));
            if (!isClickInsideModel && modelMenu && modelMenu.style.display === 'flex') {
                modelMenu.style.display = 'none';
            }
        });
        // Image Modal Logic
        function openImageModal(src) {
            const modal = document.getElementById('image-modal');
            const img = document.getElementById('modal-img');
            if (modal && img) {
                img.src = src;
                modal.style.display = 'flex';
                setTimeout(() => modal.classList.add('active'), 10);
                history.pushState({ view: 'image' }, "");
            }
        }
        function closeImageModal() {
            const modal = document.getElementById('image-modal');
            if (modal) {
                modal.classList.remove('active');
                setTimeout(() => modal.style.display = 'none', 300);
            }
        }

// NOTE: setAtmosphere, openImageModal, closeImageModal are defined
// inside the DOMContentLoaded block above and exported to window there.
// Duplicate outer definitions removed to prevent initialization conflicts.

// --- Mobile hardware back button & Global Shortcuts ---
window.addEventListener('popstate', (e) => {
    // 1. Close Lightbox
    const lb = document.getElementById('image-modal');
    if (lb && lb.classList.contains('active')) {
        closeImageModal();
        return;
    }
    // 2. Close Settings
    const set = document.getElementById('settings-modal');
    if (set && set.style.display === 'flex') {
        closeSettings();
        return;
    }
    // 3. Close Sidebar
    const sb = document.getElementById('sidebar');
    if (sb && sb.classList.contains('open')) {
        toggleSidebar();
        return;
    }
    // 4. Close confirmation
    const conf = document.getElementById('delete-confirm-modal');
    if (conf && conf.style.display === 'flex') {
        conf.style.display = 'none';
        return;
    }
});

window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        // Priority close on ESC
        const lb = document.getElementById('image-modal');
        if (lb && lb.classList.contains('active')) { closeImageModal(); return; }
        
        const set = document.getElementById('settings-modal');
        if (set && set.style.display === 'flex') { closeSettings(); return; }
        
        const conf = document.getElementById('delete-confirm-modal');
        if (conf && conf.style.display === 'flex') { conf.style.display = 'none'; return; }
        
        const sb = document.getElementById('sidebar');
        if (sb && sb.classList.contains('open')) { toggleSidebar(); return; }
    }
});

// --- Pull to Refresh Gesture ---
let touchStartY = 0;
let touchDiffY = 0;
const REFRESH_THRESHOLD = 120;
const NOTCH_ZONE = 60; // Pull must start in the top 60px

window.addEventListener('touchstart', (e) => {
    const startY = e.touches[0].pageY;
    if ((window.scrollY === 0 || document.getElementById('chat-area').scrollTop === 0) && startY < NOTCH_ZONE) {
        touchStartY = startY;
    } else {
        touchStartY = 999999; // Disarm if outside zone
    }
}, { passive: true });

window.addEventListener('touchmove', (e) => {
    const currentY = e.touches[0].pageY;
    touchDiffY = currentY - touchStartY;
    
    if (touchDiffY > 0 && (window.scrollY === 0 || document.getElementById('chat-area').scrollTop === 0)) {
        const indicator = document.getElementById('pull-indicator');
        if (indicator) {
            const pullProgress = Math.min(touchDiffY, REFRESH_THRESHOLD * 1.5);
            indicator.style.top = (pullProgress - 60) + 'px';
            indicator.style.opacity = Math.min(pullProgress / REFRESH_THRESHOLD, 1);
        }
    }
}, { passive: true });

window.addEventListener('touchend', () => {
    if (touchDiffY > REFRESH_THRESHOLD) {
        // Trigger Refresh
        location.reload();
    } else {
        // Reset Indicator
        const indicator = document.getElementById('pull-indicator');
        if (indicator) {
            indicator.style.top = '-60px';
            indicator.style.opacity = '0';
        }
    }
    touchDiffY = 0;
});

    const logoImg = document.getElementById('main-logo-img');
    if(logoImg) logoImg.addEventListener('click', () => {
        jiggleLogo();
        // Removed defunct setAtmosphere ghost call to prevent console errors.
    });

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
    window.copyCode = copyCode;
    window.downloadCode = downloadCode;
    window.startEditPrompt = startEditPrompt;
    window.submitEdit = submitEdit;
    window.openSettings = openSettings;
    // BUG FIX: Restored correct closeSettings.
    // The previous version only closed the modal when clicking the backdrop itself,
    // which meant the X button (which has class 'close-settings') worked but
    // calling closeSettings() directly from openSettings() button logic did not.
    window.closeSettings = closeSettings;
    window.handleChatKey = handleChatKey;
    window.autoRes = function(el) {
        if (!el) return;
        el.style.height = 'auto';
        el.style.height = (el.scrollHeight) + 'px';
    };
    window.stopAI = stopAI;
    window.openImageModal = openImageModal;
    window.closeImageModal = closeImageModal;
    // exportChat is already exported at line 286. 

});
