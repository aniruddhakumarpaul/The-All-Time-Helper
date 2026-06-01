        // Exported globally for index.html onclick handlers
        const activeUpscalePollers = new Map();

        function fallbackCopy(text, callback) {
            const textArea = document.createElement("textarea");
            textArea.value = text;
            textArea.style.top = "0";
            textArea.style.left = "0";
            textArea.style.position = "fixed";
            textArea.style.opacity = "0";
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            try {
                const successful = document.execCommand('copy');
                if (successful) {
                    callback();
                } else {
                    console.error('Fallback copy failed');
                }
            } catch (err) {
                console.error('Fallback copy error:', err);
            }
            document.body.removeChild(textArea);
        }

        function extractPollinationsJobId(url) {
            if (!url || typeof url !== 'string') return '';
            try {
                const normalizedUrl = url.replace(/&amp;/g, '&');
                const parsed = new URL(normalizedUrl, window.location.origin);
                if (!parsed.hostname || !parsed.hostname.toLowerCase().includes('pollinations.ai')) {
                    return '';
                }
                const uid = parsed.searchParams.get('uid');
                return uid ? uid.trim() : '';
            } catch (err) {
                return '';
            }
        }

        function isPollinationsGeneratedUrl(url) {
            if (!url || typeof url !== 'string') return false;
            const normalizedUrl = url.replace(/&amp;/g, '&');
            return /image\.pollinations\.ai/i.test(normalizedUrl) && /[?&]uid=/i.test(normalizedUrl);
        }

        function clearUpscalePoller(jobId) {
            if (!jobId) return;
            const entry = activeUpscalePollers.get(jobId);
            if (entry && entry.timer) {
                clearTimeout(entry.timer);
            }
            activeUpscalePollers.delete(jobId);
        }

        function getCurrentUpscaleWrappers(jobId) {
            if (!jobId) return [];
            return Array.from(document.querySelectorAll(`.pollinations-upscale-card[data-upscale-job-id="${jobId}"]`));
        }

        function notifyUpscaleReady(jobId, localUrl, originalUrl) {
            if (!jobId || !localUrl) return;
            const detail = { jobId, localUrl, originalUrl: originalUrl || '' };
            window.dispatchEvent(new CustomEvent('upscale-image-ready', { detail }));
            if (typeof window.replaceGeneratedImageUrlInChats === 'function') {
                window.replaceGeneratedImageUrlInChats(jobId, localUrl, originalUrl || '');
            }
        }

        function updateLastMessageImageState(img) {
            const chatArea = document.getElementById('chat-area');
            const lastMsg = chatArea ? chatArea.lastElementChild : null;
            if (!lastMsg || !lastMsg.contains(img) || !lastMsg.classList.contains('b-msg')) return;

            const totalImgs = lastMsg.querySelectorAll('.chat-rendered-img').length;
            const loadedImgs = lastMsg.querySelectorAll('.chat-rendered-img[data-loaded="true"]').length;
            if (totalImgs === loadedImgs) {
                const mascot = document.getElementById('mascot-container');
                if (mascot) mascot.classList.remove('thinking');
                const stopBtn = document.getElementById('stop-btn');
                if (stopBtn) stopBtn.style.display = 'none';
            }
        }

        window.resetUpscaleImagePolling = function(rootEl) {
            if (!rootEl || typeof rootEl.querySelectorAll !== 'function') return;
            const wrappers = rootEl.querySelectorAll('.pollinations-upscale-card[data-upscale-job-id]');
            wrappers.forEach(wrapper => {
                const jobId = wrapper.getAttribute('data-upscale-job-id');
                clearUpscalePoller(jobId);
            });
        }

        window.initUpscaleImagePolling = function(rootEl) {
            if (!rootEl || typeof rootEl.querySelectorAll !== 'function') return;

            const wrappers = rootEl.querySelectorAll('.pollinations-upscale-card[data-upscale-job-id]');
            wrappers.forEach(wrapper => {
                const jobId = wrapper.getAttribute('data-upscale-job-id');
                if (!jobId) return;
                if (activeUpscalePollers.has(jobId)) return;
                activeUpscalePollers.set(jobId, { timer: null, errors: 0 });

                const setFailure = (message) => {
                    const currentWrappers = getCurrentUpscaleWrappers(jobId);
                    const targets = currentWrappers.length ? currentWrappers : [wrapper];
                    targets.forEach(currentWrapper => {
                        const img = currentWrapper ? currentWrapper.querySelector('.chat-rendered-img') : null;
                        const loadingEl = currentWrapper ? currentWrapper.querySelector('.ai-img-loading') : null;
                        const errorEl = currentWrapper ? currentWrapper.querySelector('.ai-img-error') : null;
                        if (loadingEl) loadingEl.style.display = 'none';
                        if (errorEl) {
                            errorEl.textContent = message;
                            errorEl.style.display = 'block';
                        } else if (loadingEl) {
                            loadingEl.innerHTML = `<div style="padding:16px;color:var(--text-sub)">${message}</div>`;
                            loadingEl.style.display = 'block';
                        }
                        if (img) {
                            img.dataset.loaded = 'true';
                            updateLastMessageImageState(img);
                        }
                    });
                    clearUpscalePoller(jobId);
                };

                const markReady = (localUrl) => {
                    if (!localUrl) {
                        setFailure('Image preview is unavailable.');
                        return;
                    }
                    const finalizeReady = () => {
                        const currentWrappers = getCurrentUpscaleWrappers(jobId);
                        const targets = currentWrappers.length ? currentWrappers : [wrapper];
                        let updated = false;
                        let originalUrl = '';

                        targets.forEach(currentWrapper => {
                            const img = currentWrapper ? currentWrapper.querySelector('.chat-rendered-img') : null;
                            const loadingEl = currentWrapper ? currentWrapper.querySelector('.ai-img-loading') : null;
                            const errorEl = currentWrapper ? currentWrapper.querySelector('.ai-img-error') : null;
                            if (!img) return;

                            originalUrl = originalUrl || currentWrapper.getAttribute('data-original-url') || '';
                            img.dataset.loaded = 'true';
                            img.src = localUrl;
                            img.style.display = 'block';
                            if (loadingEl) loadingEl.remove();
                            if (errorEl) errorEl.remove();
                            updateLastMessageImageState(img);
                            updated = true;
                        });

                        clearUpscalePoller(jobId);
                        if (updated) {
                            notifyUpscaleReady(jobId, localUrl, originalUrl);
                        } else {
                            setFailure('Image preview is unavailable.');
                        }
                    };

                    const probe = new Image();
                    probe.onload = finalizeReady;
                    probe.onerror = () => {
                        setFailure('Generated image preview failed to load.');
                    };
                    probe.src = localUrl;
                };

                const poll = async () => {
                    const entry = activeUpscalePollers.get(jobId);
                    if (!entry) return;
                    try {
                        const res = await fetch(`/api/upscale/status/${encodeURIComponent(jobId)}`);
                        const data = await res.json();
                        entry.errors = 0;
                        if (data && data.success && data.status === 'ready' && data.url) {
                            markReady(data.url);
                            return;
                        }
                        if (data && (data.status === 'failed' || data.status === 'missing' || data.success === false)) {
                            setFailure('Generated image could not be enhanced.');
                            return;
                        }
                    } catch (err) {
                        entry.errors = (entry.errors || 0) + 1;
                        if (entry.errors >= 8) {
                            setFailure('Image status check failed. Try regenerating the image.');
                            return;
                        }
                    }
                    const nextEntry = activeUpscalePollers.get(jobId);
                    if (!nextEntry) return;
                    nextEntry.timer = setTimeout(poll, 2500);
                };

                getCurrentUpscaleWrappers(jobId).forEach(currentWrapper => {
                    const loadingEl = currentWrapper ? currentWrapper.querySelector('.ai-img-loading') : null;
                    if (loadingEl) loadingEl.style.display = 'flex';
                });
                const entry = activeUpscalePollers.get(jobId);
                if (entry) entry.timer = setTimeout(poll, 0);
            });
        };

        window.copyCode = function(btn, encodedCode) {
            const code = decodeURIComponent(encodedCode);
            const doSuccessFeedback = () => {
                const span = btn.querySelector('span');
                if (!span) return;
                const originalSVG = btn.innerHTML;
                
                btn.classList.add('success');
                span.innerText = 'Copied!';
                const svgEl = btn.querySelector('svg');
                if (svgEl) {
                    svgEl.outerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#4ade80" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg>';
                }
                
                setTimeout(() => {
                    btn.classList.remove('success');
                    btn.innerHTML = originalSVG;
                }, 2000);
            };

            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(code)
                    .then(doSuccessFeedback)
                    .catch(err => {
                        console.error("Clipboard API failed, trying fallback:", err);
                        fallbackCopy(code, doSuccessFeedback);
                    });
            } else {
                fallbackCopy(code, doSuccessFeedback);
            }
        };

        window.downloadCode = function(encodedCode, lang) {
            try {
                const code = decodeURIComponent(encodedCode);
                const blob = new Blob([code], { type: 'text/plain' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                
                let ext = 'txt';
                if (lang && typeof lang === 'string') {
                    const l = lang.trim().toLowerCase();
                    if (l === 'python') ext = 'py';
                    else if (l === 'javascript' || l === 'js') ext = 'js';
                    else if (l === 'typescript' || l === 'ts') ext = 'ts';
                    else if (l === 'html') ext = 'html';
                    else if (l === 'css') ext = 'css';
                    else if (l === 'json') ext = 'json';
                    else if (l === 'markdown' || l === 'md') ext = 'md';
                    else if (l) ext = l;
                }
                
                a.href = url;
                a.download = `snippet_${Math.floor(Math.random()*10000)}.${ext}`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
            } catch (err) {
                console.error("Error downloading code:", err);
            }
        };

        // --- GLOBAL IMAGE ERROR HANDLER (DE-COUPLED) ---
        window.handleImgError = function(img, safeHref, uniqueId) {
            if (!img) return;
            img.retryCount = (img.retryCount || 0) + 1;
            const loadEl = document.getElementById('load-' + uniqueId);
            
            console.log(`DEBUG: Image Error (Try ${img.retryCount}/25) for ${uniqueId}`);

            if (img.retryCount <= 25) {
                const delay = img.retryCount <= 5 ? 6000 : 8000;
                
                if(loadEl) {
                    const statusSpan = loadEl.querySelector('span');
                    if(statusSpan) {
                        if (img.retryCount > 15) statusSpan.textContent = '🎨 Still baking pixels... (' + img.retryCount + '/25)';
                        else if (img.retryCount > 5) statusSpan.textContent = '🎨 Rendering details... (' + img.retryCount + '/25)';
                        else statusSpan.textContent = '🎨 Generating... (' + img.retryCount + '/25)';
                    }
                }
                
                setTimeout(() => { 
                    if (img && !img.dataset.loaded) {
                        const retryUrl = safeHref + (safeHref.includes('?') ? '&' : '?') + '_r=' + img.retryCount;
                        img.src = `/api/image_proxy?url=${encodeURIComponent(retryUrl)}`; 
                    }
                }, delay);
            } else {
                if(loadEl) {
                    loadEl.innerHTML = `<div style="padding:16px;color:var(--text-sub)">⌛ Generation is taking a while. <a href="${safeHref}" target="_blank" style="color:var(--accent-blue); font-weight: 600;">View directly →</a></div>`;
                    
                    const chatArea = document.getElementById('chat-area');
                    const lastMsg = chatArea ? chatArea.lastElementChild : null;
                    if (lastMsg && lastMsg.contains(img)) {
                        const mascot = document.getElementById('mascot-container');
                        if (mascot) mascot.classList.remove('thinking');
                        const stopBtn = document.getElementById('stop-btn');
                        if (stopBtn) stopBtn.style.display = 'none';
                    }
                }
            }
        };

        window.renderMarkdown = function(text) {
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
                    const safeCode = encodeURIComponent(code).replace(/'/g, '%27');
                    return `
                        <div class="code-wrapper">
                            <div class="code-actions">
                                <button class="code-btn copy-btn" onclick="window.copyCode(this, '${safeCode}')">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                                    <span>Copy</span>
                                </button>
                                <button class="code-btn download-btn" onclick="window.downloadCode('${safeCode}', '${language || 'txt'}')">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
                                    <span>Save</span>
                                </button>
                            </div>
                            <pre><code class="${langClass}">${escapedCode}</code></pre>
                        </div>`;
                };

                renderer.image = function(href, title, text) {
                    let imgHref = href;
                    let imgText = text;
                    if (typeof href === 'object' && href !== null) {
                        imgHref = href.href || '';
                        imgText = href.text || '';
                    }

                    const normalizedHref = imgHref.replace(/&amp;/g, '&');
                    const baseUrl = normalizedHref.replace(/&_r=\d+/, '').replace(/\?_r=\d+/, '');
                    const safeHref = baseUrl.replace(/'/g, "\\'");
                    const safeAlt = (imgText || 'AI Generated Image').replace(/'/g, "\\'");
                    const uniqueId = 'img-' + Math.random().toString(36).substr(2, 9);
                    const jobId = extractPollinationsJobId(baseUrl);
                    const isDeferredPollinations = Boolean(jobId && isPollinationsGeneratedUrl(baseUrl));
                    const localStaticUrl = baseUrl.startsWith('static/') ? `/${baseUrl}` : baseUrl;
                    const isLocalStaticImage = localStaticUrl.startsWith('/static/');

                    if (isDeferredPollinations) {
                        return `
                        <div class="ai-img-wrapper pollinations-upscale-card" id="wrap-${uniqueId}" data-upscale-job-id="${jobId}" data-original-url="${baseUrl.replace(/"/g, '&quot;')}" data-alt="${safeAlt}">
                            <div class="ai-img-loading" id="load-${uniqueId}">
                                <div class="img-shimmer"></div>
                                <span>🎨 Enhancing image...</span>
                            </div>
                            <div class="ai-img-error" id="error-${uniqueId}" style="display:none; padding:16px; color:var(--text-sub);"></div>
                            <img
                                id="${uniqueId}"
                                alt="${safeAlt}"
                                title="${title || ''}"
                                class="chat-rendered-img"
                                style="display:none;"
                                crossorigin="anonymous"
                                referrerpolicy="no-referrer"
                                onclick="window.openImageModal(this.src)"
                            >
                        </div>`;
                    }

                    if (isLocalStaticImage) {
                        const safeLocalHref = localStaticUrl.replace(/'/g, "\\'");
                        return `
                        <div class="ai-img-wrapper" id="wrap-${uniqueId}">
                            <img
                                id="${uniqueId}"
                                src="${localStaticUrl}"
                                alt="${safeAlt}"
                                title="${title || ''}"
                                class="chat-rendered-img"
                                data-loaded="true"
                                style="display:block;"
                                onclick="window.openImageModal('${safeLocalHref}')"
                            >
                        </div>`;
                    }

                    // Use proxy to bypass blocks for non-Pollinations external images.
                    const proxyUrl = `/api/image_proxy?url=${encodeURIComponent(baseUrl)}`;

                    return `
                        <div class="ai-img-wrapper" id="wrap-${uniqueId}">
                            <div class="ai-img-loading" id="load-${uniqueId}">
                                <div class="img-shimmer"></div>
                                <span>🎨 Generating image...</span>
                            </div>
                            <img 
                                id="${uniqueId}"
                                src="${proxyUrl}" 
                                alt="${safeAlt}" 
                                title="${title || ''}" 
                                class="chat-rendered-img"
                                style="display:none;"
                                crossorigin="anonymous"
                                referrerpolicy="no-referrer"
                                onclick="window.openImageModal('${safeHref}')"
                                onload="
                                    this.dataset.loaded = 'true';
                                    const lEl = document.getElementById('load-${uniqueId}');
                                    if(lEl) lEl.style.display='none';
                                    this.style.display='block';
                                    const chatArea = document.getElementById('chat-area');
                                    const lastMsg = chatArea ? chatArea.lastElementChild : null;
                                    if (lastMsg && lastMsg.contains(this) && lastMsg.classList.contains('b-msg')) {
                                        const totalImgs = lastMsg.querySelectorAll('.chat-rendered-img').length;
                                        const loadedImgs = lastMsg.querySelectorAll('.chat-rendered-img[style*=\\'display: block\\']').length;
                                        if (totalImgs === loadedImgs) {
                                            const mascot = document.getElementById('mascot-container');
                                            if (mascot) mascot.classList.remove('thinking');
                                            const stopBtn = document.getElementById('stop-btn');
                                            if (stopBtn) stopBtn.style.display = 'none';
                                        }
                                    }
                                "
                                onerror="window.handleImgError(this, '${safeHref}', '${uniqueId}')"
                            >
                        </div>`;
                };

                return marked.parse(text, { renderer: renderer });
            } catch (e) {
                console.error("Markdown Error:", e);
                return text;
            }
        };

        // --- ATTACHED CONTEXTS PARSER & RENDERER ---
        window.parseAttachedContexts = function(c) {
            const contexts = [];
            if (!c) return { contexts, cleanText: '' };
            const regex = /\[Attached Context (\d+)\]\s*"""\s*([\s\S]*?)\s*"""/g;
            let match;
            let cleanText = c;
            while ((match = regex.exec(c)) !== null) {
                contexts.push({
                    index: parseInt(match[1]),
                    text: match[2].trim()
                });
            }
            cleanText = cleanText.replace(regex, '').trim();
            return { contexts, cleanText };
        };

        window.toggleContextExpand = function(id) {
            const el = document.getElementById(id);
            if (!el) return;
            const isHidden = el.style.display === 'none';
            el.style.display = isHidden ? 'block' : 'none';
            
            const card = el.closest('.msg-context-card');
            if (card) {
                const chevron = card.querySelector('.chevron-icon');
                if (chevron) {
                    chevron.style.transform = isHidden ? 'rotate(180deg)' : 'rotate(0deg)';
                }
            }
        };
