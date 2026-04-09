console.log("DEBUG: All Time Helper Script Initializing...");
        // Global Bot State
        window.botState = 'idle';
        const LOGO_DATA = "/static/img/logo.png";
        const LOGO_LIGHT_DATA = "/static/img/logo(2).jpg";
        const BOT_DATA = "/static/img/bot.png";
        let user = null; 
        let chats = JSON.parse(localStorage.getItem('helper_chats_v2') || '[]');
        let activeId = null; let abortC = null; let currentImg = null;
        let selectedModel = 'gemma2:2b';
        let currentBlobUrl = null;
        let chatToDelete = null;
        let isRenaming = false;
        let geminiKey = localStorage.getItem('gemini_key') || '';

        if(geminiKey) document.getElementById('gemini-key-in').value = geminiKey;
        function saveGeminiKey(val) { geminiKey = val; localStorage.setItem('gemini_key', val); }

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
                const first = nameStr.split(' ')[0];
                
                const sbGreet = document.getElementById('sidebar-greet');
                if(sbGreet) sbGreet.innerText = 'Hello, ' + nameStr;
                
                const cGreet = document.getElementById('center-greet');
                if(cGreet) cGreet.innerHTML = `Hello, <span style="background: var(--greet-grad); background-clip: text; -webkit-background-clip: text; -webkit-text-fill-color: transparent;">${first}</span>`;
                
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
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(p) 
                });
                const data = await res.json();
                if(data.success) {
                    if(t === 'signup' || (t === 'login' && data.unverified)) switchAuth('otp');
                    else {
                        user = data.user; localStorage.setItem('helper_user_v2', JSON.stringify(user));
                        if(data.token) localStorage.setItem('helper_token_v2', data.token);
                        document.getElementById('auth-overlay').style.display = 'none';
                        updUI();
                        document.getElementById('prompt').focus();
                    }
                } else alert(data.error || 'Check credentials');
            } catch(e) { alert('Server unavailable'); }
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
        function toggleSidebar() { document.getElementById('sidebar').classList.toggle('open'); }
        
        // Settings
        function autoRes(t) { t.style.height = 'auto'; t.style.height = t.scrollHeight + 'px'; }
        function openSettings() { document.getElementById('settings-modal').style.display = 'flex'; }
        function closeSettings() { document.getElementById('settings-modal').style.display = 'none'; document.getElementById('prompt').focus(); }

        function handleChatKey(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                send();
            } else if (e.key === 'Escape') {
                startNewChat();
            }
        }

        function toggleTheme(e) {
            if(e) e.stopPropagation();
            const b = document.body; const isDark = b.getAttribute('data-theme') === 'dark';
            b.setAttribute('data-theme', isDark ? 'light' : 'dark');
            document.getElementById('t-theme').classList.toggle('on');
            document.getElementById('main-logo-img').src = isDark ? LOGO_LIGHT_DATA : LOGO_DATA;
        }
        function toggleSet(id) { document.getElementById(id).classList.toggle('on'); }

        function startNewChat() {
            activeId = Date.now().toString();
            document.getElementById('chat-area').innerHTML = '';
            document.getElementById('chat-area').style.display = 'none';
            document.getElementById('welcome').style.display = 'flex';
            clearImgPreview();
            renderHist();
            document.getElementById('prompt').focus();
        }

        function renderHist() {
            if (isRenaming) return;
            const list = document.getElementById('history-list'); list.innerHTML = '';
            chats.slice().reverse().forEach(c => {
                const div = document.createElement('div');
                div.className = `history-item ${c.id === activeId ? 'active-chat' : ''}`;
                
                let titleContent = `<span class="chat-title-text" id="t-${c.id}">${c.title || 'New Chat'}</span>`;
                
                div.innerHTML = `
                    ${titleContent}
                    <div style="display: flex; gap: 4px;">
                        <button class="del-chat-btn" onclick="event.stopPropagation(); startRename('${c.id}', event)" title="Rename Chat">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
                        </button>
                        <button class="del-chat-btn" onclick="showDeleteConfirm('${c.id}', event)" title="Delete Chat">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"></path><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
                        </button>
                    </div>`;
                div.onclick = (e) => { if(!e.target.closest('.del-chat-btn')) loadChat(c.id); };
                list.appendChild(div);
            });
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
            if(chat && val.trim()) { chat.title = val.trim(); localStorage.setItem('helper_chats_v2', JSON.stringify(chats)); }
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
            document.getElementById('prompt').focus();
        }

        function renderMarkdown(text) {
            try {
                const renderer = new marked.Renderer();
                renderer.code = function(arg1, arg2) {
                    let code = arg1;
                    let language = arg2;
                    if (typeof arg1 === 'object') {
                        code = arg1.text || '';
                        language = arg1.lang || '';
                    }
                    const langClass = language ? `language-${language}` : '';
                    const escapedCode = code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                    const safeCode = encodeURIComponent(code);
                    return `
                        <div class="code-wrapper">
                            <div class="code-actions">
                                <button class="code-btn copy-btn" onclick="copyCode(this, '${safeCode}')">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                                    <span>Copy</span>
                                </button>
                                <button class="code-btn download-btn" onclick="downloadCode('${safeCode}', '${language || 'txt'}')">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
                                    <span>Save</span>
                                </button>
                            </div>
                            <pre><code class="${langClass}">${escapedCode}</code></pre>
                        </div>`;
                };
                return marked.parse(text, { renderer: renderer });
            } catch (e) {
                console.error("Markdown Error:", e);
                return text;
            }
        }

        function copyCode(btn, encodedCode) {
            const code = decodeURIComponent(encodedCode);
            navigator.clipboard.writeText(code).then(() => {
                const span = btn.querySelector('span');
                const originalText = span.innerText;
                const originalSVG = btn.innerHTML;
                
                btn.classList.add('success');
                span.innerText = 'Copied!';
                btn.querySelector('svg').outerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#4ade80" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg>';
                
                setTimeout(() => {
                    btn.classList.remove('success');
                    btn.innerHTML = originalSVG;
                }, 2000);
            });
        }

        function downloadCode(encodedCode, lang) {
            const code = decodeURIComponent(encodedCode);
            const blob = new Blob([code], { type: 'text/plain' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            const ext = lang.toLowerCase() === 'python' ? 'py' : (lang || 'txt');
            a.href = url;
            a.download = `snippet_${Date.now()}.${ext}`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
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
                    <div style="font-size: 0.8rem; color: var(--text-sub); font-weight: 500;">
                        ${r === 'u' ? (user ? user.name : 'You') : 'The Human Assister'}
                    </div>
                </div>
                <div class="txt">
                    <div id="msg-text-${idx}">${content}</div>
                    ${i ? '<br><img src="data:image/png;base64,'+i+'" style="max-width:400px; border-radius:16px; margin-top:15px; box-shadow:0 10px 30px rgba(0,0,0,0.3);">' : ''}
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
                localStorage.setItem('helper_chats_v2', JSON.stringify(chats));
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

            document.getElementById('welcome').style.display = 'none';
            document.getElementById('chat-area').style.display = 'block';
            
            addMsg('u', p, currentImg, chat.ms.length);
            chat.ms.push({r: 'u', c: p, i: currentImg});
            triggerBotReaction(p);
            clearImgPreview();
            document.getElementById('prompt').value = '';
            document.getElementById('stop-btn').style.display = 'block';
            
            const bTxt = addMsg('b', '...', null, chat.ms.length); bTxt.innerText = '';
            updateBotVisuals();
            abortC = new AbortController();
            
            const sysOpts = {
                english: document.getElementById('t-eng').classList.contains('on'),
                oneword: document.getElementById('t-word').classList.contains('on'),
                pers: document.getElementById('t-pers').classList.contains('on')
            };

            try {
                const token = localStorage.getItem('helper_token_v2') || '';
                const res = await fetch('/chat', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${token}`
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
                        },
                        gemini_key: geminiKey
                    }),
                    signal: abortC.signal
                });
                
                if (res.status === 401) {
                    signOut();
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
                        if(line.trim()) { 
                            try {
                                const j = JSON.parse(line);
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
                chat.ms.push({r: 'b', c: fullTxt});
                // Final render: apply Markdown + syntax highlighting after streaming completes
                bTxt.innerHTML = renderMarkdown(fullTxt);
                bTxt.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
                localStorage.setItem('helper_chats_v2', JSON.stringify(chats));
            } catch(e) { bTxt.innerText += " [Stopped]"; }
            finally { 
                document.getElementById('stop-btn').style.display = 'none'; 
                abortC = null; currentImg = null; renderHist(); 
            }
        }

        function stopAI() { if(abortC) abortC.abort(); }

        const savedUser = localStorage.getItem('helper_user_v2');
        if(savedUser) { 
            user = JSON.parse(savedUser); document.getElementById('auth-overlay').style.display = 'none';
            updUI();
            document.getElementById('prompt').focus();
        } else {
            document.getElementById('l-email').focus();
        }
        renderHist();

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
        if (promptIn && sendBtn) {
            promptIn.addEventListener('input', () => {
                if (promptIn.value.trim().length > 0) {
                    sendBtn.classList.add('pulsing');
                } else {
                    sendBtn.classList.remove('pulsing');
                }
            });
        }

        // Mobile Sidebar Dismissal
        document.addEventListener('click', (e) => {
            const sidebar = document.getElementById('sidebar');
            const menuBtn = document.getElementById('mobile-menu-btn');
            if (window.innerWidth <= 800 && sidebar && sidebar.classList.contains('open')) {
                if (!sidebar.contains(e.target) && (!menuBtn || !menuBtn.contains(e.target))) {
                    sidebar.classList.remove('open');
                }
            }
        });