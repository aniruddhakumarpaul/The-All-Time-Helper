/**
 * uiController.js - DOM and User Interface
 */

export class UIController {
    constructor() {
        this.cache = {
            logo: document.getElementById('main-logo-img'),
            mascot: document.getElementById('mascot-container'),
            prompt: document.getElementById('prompt'),
            chatArea: document.getElementById('chat-area'),
            sidebar: document.getElementById('sidebar'),
            sidebarScrim: document.getElementById('sidebar-scrim'),
            themeModal: document.getElementById('theme-modal'),
            neuralCard: document.getElementById('neural-context-card'),
            neuralScrim: document.getElementById('neural-scrim'),
            contextResults: document.getElementById('context-results'),
            imgInput: document.getElementById('img-in'),
            imgPreviewArea: document.getElementById('img-preview-area'),
            welcome: document.getElementById('welcome')
        };
        this.tiltSettleTimer = null;
        this.activePollers = new Set();
        this.SB_OFFSET = 300;
        this.isDraggingSidebar = false;
        this.isHorizontalSwipe = false;
        this.startX = 0;
        this.startY = 0;
        this.currentSwipeX = 0;
    }

    // --- Theming ---

    applyTheme(choice) {
        localStorage.setItem('helper_theme_pref', choice);
        const theme = (choice === 'system') ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : choice;
        document.body.setAttribute('data-theme', theme);
        if (this.cache.logo) {
            this.cache.logo.src = theme === 'dark' ? "/static/img/logo.png" : "/static/img/logo(2).jpg";
        }
    }

    initTheme() {
        this.applyTheme(localStorage.getItem('helper_theme_pref') || 'system');
    }

    // --- Message Rendering ---

    addMsg({ role, content, img, idx, mName, user, isMasked = false }) {
        const div = document.createElement('div');
        div.className = `msg ${role === 'u' ? 'u' : 'b'}-msg entering`;
        setTimeout(() => div.classList.remove('entering'), 600);

        const name = user ? user.name : 'Human';
        const initial = name.charAt(0).toUpperCase();

        const avatarHtml = role === 'u'
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

        let msgContent = role === 'b' ? this.renderMarkdown(content) : content.replace(/</g, '&lt;').replace(/>/g, '&gt;');
        if (role === 'u' && isMasked) msgContent = '•'.repeat(Math.max(8, content.length));

        let tools = '';
        if (role === 'u' && idx !== undefined && !isMasked) {
            tools = `<div class="msg-tools">
                        <div class="tool-icon" onclick="window.startEditPrompt(${idx}, this)" title="Edit Prompt">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
                        </div>
                     </div>`;
        }

        let watermark = '';
        if (role === 'b' && mName) {
            watermark = `<div class="model-watermark" style="font-size: 0.7rem; color: var(--accent-blue); opacity: 0.8; margin-top: 12px; display: flex; align-items: center; gap: 6px; font-weight: 600; font-family: 'Outfit', sans-serif; letter-spacing: 0.3px; border-top: 1px solid rgba(255,255,255,0.05); padding-top: 8px;">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="opacity: 0.7;"><circle cx="12" cy="12" r="10"></circle><path d="M12 8v8M8 12h8"></path></svg>
                <span style="text-transform: uppercase; font-size: 0.65rem;">${mName}</span>
            </div>`;
        }

        div.innerHTML = `
            <div class="av-wrap">
                ${avatarHtml}
                <div class="av-label" style="font-size: 0.8rem; color: var(--text-sub); font-weight: 600; letter-spacing: 0.5px;">
                    ${role === 'u' ? name : 'THE ALL TIME HELPER'}
                </div>
            </div>
            <div class="txt" draggable="false" ondragstart="if(!window.isGDown) { event.preventDefault(); return false; } window.handleDragStart(event)" ondragend="window.handleDragEnd(event)">
                <div id="msg-text-${idx}">${msgContent}</div>
                ${img ? `
                    <div class="chat-img-preview-container" onclick="window.openImageModal('data:image/png;base64,${img}')">
                        <img src="data:image/png;base64,${img}" class="chat-img-preview">
                    </div>` : ''}
                ${watermark}
                ${tools}
            </div>
        `;

        this.cache.chatArea.appendChild(div);
        div.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
        this.scrollToBottom();
        return div.querySelector(`#msg-text-${idx}`);
    }

    setThinking(active, bTxt, initialText = '') {
        if (!this.cache.mascot) return;
        if (active) {
            this.cache.mascot.classList.add('thinking');
            if (bTxt) {
                const parentMsg = bTxt.closest('.msg');
                if (parentMsg) parentMsg.classList.add('thinking-state');
                bTxt.innerHTML = `
                    <div class="status-msg"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><circle cx="12" cy="12" r="10"></circle><path d="M12 6v6l4 2"></path></svg> <span id="status-text">${initialText || '...'}</span></div>
                    <div class="typing-indicator"><span></span><span></span><span></span></div>
                `;
            }
        } else {
            this.cache.mascot.classList.remove('thinking');
            document.querySelectorAll('.thinking-state').forEach(m => m.classList.remove('thinking-state'));
            document.querySelectorAll('.typing-indicator').forEach(ti => ti.remove());
        }
    }

    updateStatus(bTxt, status) {
        if (!bTxt) return;
        let statusEl = bTxt.querySelector('#status-text');
        if (!statusEl) {
            const statusDiv = document.createElement('div');
            statusDiv.className = 'status-msg';
            statusDiv.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><circle cx="12" cy="12" r="10"></circle><path d="M12 6v6l4 2"></path></svg> <span id="status-text">${status}</span>`;
            bTxt.prepend(statusDiv);
        } else {
            statusEl.innerText = status;
        }
    }

    renderMarkdown(text) {
        if (!text || typeof marked === 'undefined') return text || '';
        const renderer = new marked.Renderer();
        const self = this;
        renderer.image = function(href, title, text) {
            const actualHref = (typeof href === 'object') ? href.href : href;
            const actualText = (typeof href === 'object') ? href.text : text;
            return `<img src="${actualHref}" alt="${actualText}" class="chat-rendered-img" loading="lazy" onclick="window.openImageModal(this.src)">`;
        };
        return marked.parse(text, { renderer: renderer });
    }

    finalizeResponse(bTxt, fullTxt) {
        if (!bTxt) return;
        bTxt.innerHTML = this.renderMarkdown(fullTxt);
        
        // Upscale Poller Detection
        const imgs = bTxt.querySelectorAll('img');
        imgs.forEach(img => {
            if (img.src && img.src.includes('uid=')) {
                try {
                    const urlParams = new URLSearchParams(img.src.split('?')[1]);
                    const jobId = urlParams.get('uid');
                    if (jobId) this.startUpscalePoller(jobId, bTxt);
                } catch (e) { }
            }
        });

        bTxt.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
    }

    startUpscalePoller(jobId, container) {
        if (this.activePollers.has(jobId)) return;
        this.activePollers.add(jobId);

        const img = container.querySelector('.chat-rendered-img');
        if (!img) { this.activePollers.delete(jobId); return; }

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
                        this.activePollers.delete(jobId);
                    };
                } else if (data.status === 'failed') {
                    img.classList.remove('upscaling');
                    badge.remove();
                    this.activePollers.delete(jobId);
                } else {
                    setTimeout(poll, 2500);
                }
            } catch (e) { this.activePollers.delete(jobId); }
        };
        poll();
    }

    // --- Sidebar & History ---

    renderHistory(chats, activeId, currentSearch) {
        if (!this.cache.historyList) return;
        this.cache.historyList.innerHTML = '';

        const sorted = chats.slice().reverse().sort((a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0));

        sorted.forEach(c => {
            const title = (c.title || 'New Chat').toLowerCase();
            if (currentSearch && !title.includes(currentSearch.toLowerCase())) return;

            const div = document.createElement('div');
            div.className = `history-item ${c.id === activeId ? 'active-chat' : ''} ${c.pinned ? 'pinned' : ''}`;
            div.innerHTML = `
                <span class="chat-title-text" id="t-${c.id}">${c.title || 'New Chat'}</span>
                <div class="history-actions">
                    <button class="del-chat-btn pin-btn ${c.pinned ? 'active' : ''}" onclick="event.stopPropagation(); window.togglePin('${c.id}')">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v2a2 2 0 0 0 1.27 1.87L11 15.3V21l2-2 2 2v-5.7l6.73-3.43A2 2 0 0 0 21 10z"></path></svg>
                    </button>
                    <button class="del-chat-btn" onclick="event.stopPropagation(); window.startRename('${c.id}', event)">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
                    </button>
                    <button class="del-chat-btn" onclick="event.stopPropagation(); window.showDeleteConfirm('${c.id}', event)">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"></path><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
                    </button>
                </div>`;
            div.onclick = (e) => { if (!e.target.closest('.del-chat-btn')) window.loadChat(c.id); };
            this.cache.historyList.appendChild(div);
        });
    }

    // --- State & UI Updates ---

    updateUserUI(user) {
        if (!user) return;
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

    // --- Image & Auth Helpers ---

    clearImgPreview() {
        if (this.cache.imgInput) this.cache.imgInput.value = '';
        if (this.cache.imgPreviewArea) {
            this.cache.imgPreviewArea.style.display = 'none';
            this.cache.imgPreviewArea.innerHTML = '';
        }
    }

    showImgPreview(file) {
        if (!this.cache.imgPreviewArea) return;
        this.cache.imgPreviewArea.style.display = 'flex';
        this.cache.imgPreviewArea.innerHTML = `
            <div class="img-thumb-wrap">
                <img src="${URL.createObjectURL(file)}" class="img-thumb">
                <button class="img-remove-btn" onclick="window.clearImgPreview()">✕</button>
            </div>`;
    }

    isAuthRequest(text) {
        const authKeywords = ["please provide your admin key", "enter your admin_key", "provide the password", "authorize with your key", "auth_required"];
        return authKeywords.some(kw => text.toLowerCase().includes(kw));
    }

    updateAuthMode(chat) {
        const promptIn = this.cache.prompt;
        if (!chat || !promptIn) return;
        
        const lastMsg = chat.ms[chat.ms.length - 1];
        if (lastMsg?.r === 'b' && this.isAuthRequest(lastMsg.c)) {
            promptIn.placeholder = "🔒 ENTER ADMIN KEY TO AUTHORIZE EMAIL DISPATCH...";
            promptIn.classList.add('auth-waiting');
            this.cache.mascot?.classList.add('logo-jiggle');
            setTimeout(() => this.cache.mascot?.classList.remove('logo-jiggle'), 500);
        } else {
            promptIn.placeholder = "Message The All Time Helper...";
            promptIn.classList.remove('auth-waiting');
        }
    }

    autoRes(el) {
        el.style.height = 'auto';
        el.style.height = (el.scrollHeight) + 'px';
    }

    trackCursor(e) {
        if (!this.cache.logo || window.innerWidth <= 850) return;
        if (this.tiltSettleTimer) clearTimeout(this.tiltSettleTimer);

        const rect = this.cache.logo.getBoundingClientRect();
        const logoCenterX = rect.left + rect.width / 2;
        const logoCenterY = rect.top + rect.height / 2;

        const dx = e.clientX - logoCenterX;
        const dy = e.clientY - logoCenterY;

        const maxRotation = 35; 
        const rotX = Math.max(-maxRotation, Math.min(maxRotation, -dy / 12));
        const rotY = Math.max(-maxRotation, Math.min(maxRotation, dx / 12));

        const moveX = Math.max(-10, Math.min(10, dx / 50));
        const moveY = Math.max(-10, Math.min(10, dy / 50));

        this.cache.logo.style.transform = `perspective(600px) rotateX(${rotX}deg) rotateY(${rotY}deg) translate3d(${moveX}px, ${moveY}px, 0)`;
        
        if (this.cache.mascot?.classList.contains('thinking')) {
            const scale = 1 + Math.sin(Date.now() / 200) * 0.02;
            this.cache.logo.style.transform += ` scale(${scale})`;
        }

        this.tiltSettleTimer = setTimeout(() => {
            if (this.cache.logo) this.cache.logo.style.transform = 'perspective(600px) rotateX(0) rotateY(0) translate3d(0,0,0)';
        }, 2000);
    }

    initSidebarSwipe() {
        const sidebar = this.cache.sidebar;
        const scrim = this.cache.sidebarScrim;
        if (!sidebar || !scrim) return;

        sidebar.addEventListener('touchstart', (e) => {
            if (!sidebar.classList.contains('open') || window.innerWidth > 992) return;
            this.startX = e.touches[0].clientX;
            this.startY = e.touches[0].clientY;
            this.currentSwipeX = this.SB_OFFSET;
            this.isDraggingSidebar = true;
            this.isHorizontalSwipe = false;
            sidebar.style.transition = 'none';
            scrim.style.transition = 'none';
        }, { passive: true });

        sidebar.addEventListener('touchmove', (e) => {
            if (!this.isDraggingSidebar) return;
            const touchX = e.touches[0].clientX;
            const touchY = e.touches[0].clientY;
            const deltaX = touchX - this.startX;
            const deltaY = touchY - this.startY;

            if (!this.isHorizontalSwipe) {
                if (Math.abs(deltaX) > Math.abs(deltaY) * 1.5) this.isHorizontalSwipe = true;
                else if (Math.abs(deltaY) > 5) { this.isDraggingSidebar = false; return; }
                else return;
            }
            
            this.currentSwipeX = Math.min(this.SB_OFFSET, Math.max(0, this.SB_OFFSET + deltaX));
            sidebar.style.transform = `translateX(${this.currentSwipeX}px)`;
            scrim.style.opacity = this.currentSwipeX / this.SB_OFFSET;
        }, { passive: true });

        sidebar.addEventListener('touchend', () => {
            if (!this.isDraggingSidebar) return;
            this.isDraggingSidebar = false;
            sidebar.style.transition = 'transform 0.4s cubic-bezier(0.25, 0.8, 0.25, 1)';
            scrim.style.transition = 'opacity 0.4s ease';
            
            if (this.currentSwipeX < 200) {
                sidebar.style.transform = 'translateX(0px)';
                scrim.style.opacity = '0';
                setTimeout(() => {
                    sidebar.classList.remove('open');
                    document.body.classList.remove('sidebar-open');
                    sidebar.style.transform = ''; sidebar.style.transition = '';
                    scrim.style.opacity = ''; scrim.style.transition = '';
                }, 400);
            } else {
                sidebar.style.transform = `translateX(${this.SB_OFFSET}px)`;
                scrim.style.opacity = '1';
                setTimeout(() => {
                    sidebar.style.transition = '';
                    scrim.style.transition = '';
                }, 400);
            }
        });

        sidebar.addEventListener('click', (e) => e.stopPropagation());
    }

    // --- Neural Context UI ---

    showNeuralContext(data) {
        if (!this.cache.neuralCard) return;
        this.cache.contextResults.innerHTML = `<div class="neural-insight-box">${data.explanation}</div>`;
        data.results.forEach(r => {
            this.cache.contextResults.innerHTML += `
                <div class="context-snippet">
                    <span class="context-meta">${r.metadata.type || 'Context'}</span>
                    <div>${r.content}</div>
                </div>`;
        });
        this.cache.neuralCard.classList.add('active');
        this.cache.neuralScrim.classList.add('active');
    }

    closeNeuralContext() {
        this.cache.neuralCard?.classList.remove('active');
        this.cache.neuralScrim?.classList.remove('active');
    }

    scrollToBottom() {
        this.cache.chatArea.scrollTop = this.cache.chatArea.scrollHeight;
    }

    renderMarkdown(text) {
        if (!text) return '';
        if (typeof marked === 'undefined') return text.replace(/</g, '&lt;').replace(/>/g, '&gt;');
        const renderer = new marked.Renderer();
        renderer.image = (href, title, text) => {
            const actualHref = (typeof href === 'object') ? href.href : href;
            const actualText = (typeof href === 'object') ? href.text : text;
            return `<img src="${actualHref}" alt="${actualText}" class="chat-rendered-img" loading="lazy" onclick="window.openImageModal(this.src)">`;
        };
        return marked.parse(text, { renderer: renderer });
    }
}

export const uiController = new UIController();
