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
                const baseOrigin = window.location.origin && window.location.origin !== 'null' ? window.location.origin : 'http://localhost';
                const parsed = new URL(normalizedUrl, baseOrigin);
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

                    const presentFailure = (currentWrapper, img, loadingEl, errorEl, originalUrl) => {
                        if (loadingEl) loadingEl.style.display = 'none';
                        if (errorEl) {
                            errorEl.textContent = '';
                            const text = document.createElement('span');
                            text.textContent = message;
                            errorEl.appendChild(text);
                            if (originalUrl) {
                                const link = document.createElement('a');
                                link.href = originalUrl;
                                link.target = '_blank';
                                link.rel = 'noopener noreferrer';
                                link.textContent = ' View original';
                                link.style.color = 'var(--accent-blue)';
                                errorEl.appendChild(link);
                            }
                            errorEl.style.display = 'block';
                        }
                        if (img) {
                            img.dataset.loaded = 'true';
                            updateLastMessageImageState(img);
                            window.refreshChatImageActions?.(img.parentElement);
                        }
                    };

                    targets.forEach(currentWrapper => {
                        const img = currentWrapper ? currentWrapper.querySelector('.chat-rendered-img') : null;
                        const loadingEl = currentWrapper ? currentWrapper.querySelector('.ai-img-loading') : null;
                        const errorEl = currentWrapper ? currentWrapper.querySelector('.ai-img-error') : null;
                        const originalUrl = currentWrapper ? currentWrapper.getAttribute('data-original-url') || '' : '';

                        if (img && originalUrl && img.dataset.originalFallbackAttempted !== 'true') {
                            img.dataset.originalFallbackAttempted = 'true';
                            img.dataset.retryUrl = '';
                            img.dataset.modalUrl = originalUrl;
                            img.removeAttribute('crossorigin');
                            if (errorEl) errorEl.style.display = 'none';
                            if (loadingEl) {
                                loadingEl.style.display = 'flex';
                                const status = loadingEl.querySelector('span');
                                if (status) status.textContent = 'Loading original image...';
                            }
                            img.addEventListener(
                                'error',
                                () => presentFailure(currentWrapper, img, loadingEl, errorEl, originalUrl),
                                { once: true }
                            );
                            img.src = originalUrl;
                            return;
                        }

                        presentFailure(currentWrapper, img, loadingEl, errorEl, originalUrl);
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
                            // Replace action metadata with the enhanced asset while keeping the wrapper fallback URL.
                            img.dataset.originalUrl = localUrl;
                            img.dataset.modalUrl = localUrl;
                            img.dataset.downloadUrl = localUrl;
                            img.dataset.retryUrl = '';
                            img.src = localUrl;
                            img.style.display = 'block';
                            if (loadingEl) loadingEl.remove();
                            if (errorEl) errorEl.remove();
                            updateLastMessageImageState(img);
                            window.refreshChatImageActions?.(img.parentElement);
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
                        const path = `/api/upscale/status/${encodeURIComponent(jobId)}`;
                        const res = await fetch(window.helperApiUrl ? window.helperApiUrl(path) : path);
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

        const ALLOWED_MARKDOWN_PROTOCOLS = new Set(['http:', 'https:', 'mailto:']);
        const FORBIDDEN_MARKDOWN_TAGS = ['script', 'iframe', 'object', 'embed', 'form', 'input', 'textarea', 'select'];
        const DANGEROUS_URL_PATTERN = /^(?:javascript|data|vbscript|file):/i;
        const PERMANENT_IMAGE_ERROR_STATUSES = new Set([401, 402, 403, 404]);

        function escapeMarkdownHtml(value) {
            return String(value || '')
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        function safeClassToken(value) {
            return String(value || '').replace(/[^\w-]/g, '').slice(0, 40);
        }

        function normalizeMarkdownUrl(rawUrl) {
            return String(rawUrl || '').replace(/&amp;/g, '&').trim();
        }

        function normalizePollinationsImageUrl(rawUrl) {
            const normalized = normalizeMarkdownUrl(rawUrl);
            if (!normalized || DANGEROUS_URL_PATTERN.test(normalized)) return normalized;

            try {
                const parsed = new URL(normalized, window.location.origin);
                if ((parsed.hostname || '').toLowerCase() === 'image.pollinations.ai') {
                    const model = parsed.searchParams.get('model');
                    if (model && model.toLowerCase() === 'turbo') {
                        parsed.searchParams.set('model', 'flux');
                    }
                    if ((parsed.searchParams.get('nologo') || '').toLowerCase() === 'true') {
                        parsed.searchParams.delete('nologo');
                    }
                    return parsed.href;
                }

                if (parsed.origin === window.location.origin && parsed.pathname === '/api/image_proxy') {
                    const innerUrl = parsed.searchParams.get('url');
                    if (innerUrl) {
                        const normalizedInner = normalizePollinationsImageUrl(innerUrl);
                        if (normalizedInner !== innerUrl) {
                            parsed.searchParams.set('url', normalizedInner);
                            return parsed.href;
                        }
                    }
                    return parsed.href;
                }
            } catch (err) {
                return normalized;
            }

            return normalized;
        }

        function getSafeMarkdownLink(rawUrl) {
            const normalized = normalizeMarkdownUrl(rawUrl);
            if (!normalized || DANGEROUS_URL_PATTERN.test(normalized)) return '';
            try {
                const parsed = new URL(normalized, window.location.origin);
                if (!ALLOWED_MARKDOWN_PROTOCOLS.has(parsed.protocol.toLowerCase())) return '';
                return parsed.href;
            } catch (err) {
                return '';
            }
        }

        function getSafeImageSource(rawUrl) {
            const normalized = normalizePollinationsImageUrl(normalizeMarkdownUrl(rawUrl))
                .replace(/&_r=\d+/, '')
                .replace(/\?_r=\d+/, '');
            if (!normalized || DANGEROUS_URL_PATTERN.test(normalized)) return null;

            if (normalized.startsWith('static/')) {
                return { kind: 'local', url: `/${normalized}` };
            }
            if (normalized.startsWith('/static/')) {
                return { kind: 'local', url: normalized };
            }
            if (normalized.startsWith('/api/image_proxy?')) {
                return { kind: 'local', url: normalized };
            }

            try {
                // about:blank/file previews have a null origin; absolute image URLs still need a valid URL base.
                const baseOrigin = window.location.origin && window.location.origin !== 'null' ? window.location.origin : 'http://localhost';
                const parsed = new URL(normalized, baseOrigin);
                if (parsed.origin === window.location.origin && parsed.pathname.startsWith('/static/')) {
                    return { kind: 'local', url: parsed.pathname + parsed.search + parsed.hash };
                }
                if (parsed.origin === window.location.origin && parsed.pathname === '/api/image_proxy') {
                    return { kind: 'local', url: parsed.pathname + parsed.search };
                }
                if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
                    return { kind: 'external', url: parsed.href };
                }
            } catch (err) {
                return null;
            }
            return null;
        }

        async function probeImageProxyStatus(url) {
            try {
                const path = `/api/image_proxy?url=${encodeURIComponent(url)}`;
                const res = await fetch(window.helperApiUrl ? window.helperApiUrl(path) : path, { cache: 'no-store' });
                const message = res && !res.ok ? (await res.text()).trim() : '';
                return { status: res ? res.status : 0, message };
            } catch (err) {
                return { status: 0, message: '' };
            }
        }

        function appendSpinner(parent, message) {
            const shimmer = document.createElement('div');
            shimmer.className = 'img-shimmer';
            const label = document.createElement('span');
            label.textContent = message;
            parent.appendChild(shimmer);
            parent.appendChild(label);
        }

        function buildRenderedImageHtml(source, title, altText) {
            const safeSource = getSafeImageSource(source);
            if (!safeSource) return '';

            const uniqueId = 'img-' + Math.random().toString(36).slice(2, 11);
            const wrapper = document.createElement('div');
            wrapper.className = 'ai-img-wrapper';
            wrapper.id = `wrap-${uniqueId}`;

            const img = document.createElement('img');
            img.id = uniqueId;
            img.className = 'chat-rendered-img';
            img.alt = String(altText || 'AI Generated Image');
            if (title) img.title = String(title);

            img.dataset.originalUrl = safeSource.url;
            img.dataset.imageSource = isPollinationsGeneratedUrl(safeSource.url) ? 'generated' : safeSource.kind;
            img.dataset.generated = String(isPollinationsGeneratedUrl(safeSource.url));
            img.dataset.filename = String(title || altText || 'helper-image').slice(0, 160);

            const jobId = extractPollinationsJobId(safeSource.url);
            const isDeferredPollinations = Boolean(jobId && isPollinationsGeneratedUrl(safeSource.url));

            if (isDeferredPollinations) {
                wrapper.classList.add('pollinations-upscale-card');
                wrapper.dataset.upscaleJobId = jobId;
                wrapper.dataset.originalUrl = safeSource.url;
                wrapper.dataset.alt = img.alt;

                const loading = document.createElement('div');
                loading.className = 'ai-img-loading';
                loading.id = `load-${uniqueId}`;
                appendSpinner(loading, 'Enhancing image...');

                const error = document.createElement('div');
                error.className = 'ai-img-error';
                error.id = `error-${uniqueId}`;
                error.style.display = 'none';
                error.style.padding = '16px';
                error.style.color = 'var(--text-sub)';

                img.style.display = 'none';
                img.crossOrigin = 'anonymous';
                img.referrerPolicy = 'no-referrer';
                img.dataset.loadId = uniqueId;

                wrapper.appendChild(loading);
                wrapper.appendChild(error);
                wrapper.appendChild(img);
                return wrapper.outerHTML;
            }

            img.dataset.modalUrl = safeSource.url;

            if (safeSource.kind === 'local') {
                img.src = safeSource.url;
                img.dataset.loaded = 'true';
                img.style.display = 'block';
                wrapper.appendChild(img);
                return wrapper.outerHTML;
            }

            const loading = document.createElement('div');
            loading.className = 'ai-img-loading';
            loading.id = `load-${uniqueId}`;
            appendSpinner(loading, 'Generating image...');

            img.src = `/api/image_proxy?url=${encodeURIComponent(safeSource.url)}`;
            img.style.display = 'none';
            img.crossOrigin = 'anonymous';
            img.referrerPolicy = 'no-referrer';
            img.dataset.loadId = uniqueId;
            img.dataset.retryUrl = safeSource.url;

            wrapper.appendChild(loading);
            wrapper.appendChild(img);
            return wrapper.outerHTML;
        }

        function updateRenderedImageLoaded(img) {
            if (!img) return;
            img.dataset.loaded = 'true';
            const loadId = img.dataset.loadId;
            const loadEl = loadId ? document.getElementById('load-' + loadId) : null;
            if (loadEl) loadEl.style.display = 'none';
            img.style.display = 'block';
            updateLastMessageImageState(img);
            window.refreshChatImageActions?.(img.parentElement);
        }

        function sanitizeMarkdownHtml(html) {
            const dirty = String(html || '');
            const config = {
                FORBID_TAGS: FORBIDDEN_MARKDOWN_TAGS,
                FORBID_ATTR: ['onclick', 'onerror', 'onload', 'onmouseover', 'onfocus', 'onblur'],
                ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto):|[^a-z]|[a-z+.\-]+(?:[^a-z+.\-:]|$))/i,
                ADD_ATTR: ['target', 'rel', 'crossorigin', 'referrerpolicy']
            };

            if (window.DOMPurify && typeof window.DOMPurify.sanitize === 'function') {
                return window.DOMPurify.sanitize(dirty, config);
            }

            const template = document.createElement('template');
            template.innerHTML = dirty;
            template.content.querySelectorAll(FORBIDDEN_MARKDOWN_TAGS.join(',')).forEach(el => el.remove());
            template.content.querySelectorAll('*').forEach(el => {
                Array.from(el.attributes).forEach(attr => {
                    const name = attr.name.toLowerCase();
                    const value = String(attr.value || '').trim();
                    if (name.startsWith('on') || DANGEROUS_URL_PATTERN.test(value)) {
                        el.removeAttribute(attr.name);
                    }
                    if ((name === 'href' || name === 'src') && DANGEROUS_URL_PATTERN.test(value)) {
                        el.removeAttribute(attr.name);
                    }
                });
            });
            return template.innerHTML;
        }

        window.hydrateRenderedMarkdown = function(rootEl) {
            if (!rootEl || typeof rootEl.querySelectorAll !== 'function') return;

            rootEl.querySelectorAll('.copy-btn[data-code]').forEach(btn => {
                if (btn.dataset.hydrated === 'true') return;
                btn.dataset.hydrated = 'true';
                btn.addEventListener('click', () => window.copyCode(btn, btn.dataset.code || ''));
            });

            rootEl.querySelectorAll('.download-btn[data-code]').forEach(btn => {
                if (btn.dataset.hydrated === 'true') return;
                btn.dataset.hydrated = 'true';
                btn.addEventListener('click', () => window.downloadCode(btn.dataset.code || '', btn.dataset.lang || 'txt'));
            });

            rootEl.querySelectorAll('.chat-rendered-img').forEach(img => {
                if (img.dataset.hydrated === 'true') return;
                img.dataset.hydrated = 'true';
                window.installChatImageActions?.(img);
                img.addEventListener('click', () => {
                    const modalUrl = img.dataset.modalUrl || img.src;
                    if (modalUrl && typeof window.openImageModal === 'function') {
                        const metadata = window.__helperImageMetadata?.(img) || {};
                        window.openImageModal(modalUrl, {
                            filename: metadata.filename,
                            mimeType: metadata.mimeType,
                            sourceUrl: metadata.sourceUrl,
                            downloadUrl: metadata.downloadUrl,
                            downloadable: metadata.downloadable,
                            copyable: metadata.copyable,
                            imageElement: img,
                            context: typeof window.__helperImageContextFromElement === 'function' ? window.__helperImageContextFromElement(img) : undefined,
                        });
                    }
                });
                img.addEventListener('load', () => {
                    updateRenderedImageLoaded(img);
                    window.refreshChatImageActions?.(img.parentElement);
                });
                img.addEventListener('error', () => {
                    if (img.dataset.retryUrl && img.dataset.loadId) {
                        window.handleImgError(img, img.dataset.retryUrl, img.dataset.loadId);
                    }
                });
            });
        };

        // --- GLOBAL IMAGE ERROR HANDLER (DE-COUPLED) ---
        window.handleImgError = function(img, safeHref, uniqueId) {
            if (!img) return;
            const safeSource = getSafeImageSource(safeHref);
            if (!safeSource || safeSource.kind !== 'external') return;
            const loadEl = document.getElementById('load-' + uniqueId);

            if (img.dataset.proxyStatusChecked !== 'true') {
                img.dataset.proxyStatusChecked = 'true';
                probeImageProxyStatus(safeSource.url).then(({ status, message }) => {
                    if (PERMANENT_IMAGE_ERROR_STATUSES.has(Number(status))) {
                        if (loadEl) {
                            const fallbackMessage = /image\.pollinations\.ai/i.test(safeSource.url)
                                ? 'Pollinations rejected this model or account/budget.'
                                : 'Image could not be loaded.';
                            loadEl.textContent = '';
                            const messageEl = document.createElement('div');
                            messageEl.style.padding = '16px';
                            messageEl.style.color = 'var(--text-sub)';
                            messageEl.textContent = message || fallbackMessage;
                            loadEl.appendChild(messageEl);
                        }
                        img.dataset.loaded = 'true';
                        updateLastMessageImageState(img);
                        window.refreshChatImageActions?.(img.parentElement);
                        return;
                    }

                    window.handleImgError(img, safeSource.url, uniqueId);
                }).catch(() => {
                    window.handleImgError(img, safeSource.url, uniqueId);
                });
                return;
            }

            img.retryCount = (img.retryCount || 0) + 1;
            
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
                        const retryUrl = safeSource.url + (safeSource.url.includes('?') ? '&' : '?') + '_r=' + img.retryCount;
                        img.src = `/api/image_proxy?url=${encodeURIComponent(retryUrl)}`; 
                    }
                }, delay);
            } else {
                if(loadEl) {
                    loadEl.textContent = '';
                    const message = document.createElement('div');
                    message.style.padding = '16px';
                    message.style.color = 'var(--text-sub)';
                    message.appendChild(document.createTextNode('Generation is taking a while. '));
                    const link = document.createElement('a');
                    link.href = safeSource.url;
                    link.target = '_blank';
                    link.rel = 'noopener noreferrer';
                    link.style.color = 'var(--accent-blue)';
                    link.style.fontWeight = '600';
                    link.textContent = 'View directly';
                    message.appendChild(link);
                    loadEl.appendChild(message);
                    
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
                renderer.html = function() {
                    return '';
                };

                renderer.link = function(arg1, arg2, arg3) {
                    let href = arg1;
                    let title = arg2;
                    let linkText = arg3;
                    if (typeof arg1 === 'object' && arg1 !== null) {
                        href = arg1.href || '';
                        title = arg1.title || '';
                        linkText = arg1.text || '';
                    }
                    const safeHref = getSafeMarkdownLink(href);
                    const safeText = linkText || href || '';
                    if (!safeHref) return escapeMarkdownHtml(safeText);
                    const a = document.createElement('a');
                    a.href = safeHref;
                    if (title) a.title = String(title);
                    if (!safeHref.toLowerCase().startsWith('mailto:')) {
                        a.target = '_blank';
                        a.rel = 'noopener noreferrer';
                    }
                    a.textContent = safeText;
                    return a.outerHTML;
                };
                
                renderer.code = function(arg1, arg2) {
                    let code = arg1;
                    let language = arg2;
                    if (typeof arg1 === 'object') {
                        code = arg1.text || '';
                        language = arg1.lang || '';
                    }
                    const safeLang = safeClassToken(language || 'txt') || 'txt';
                    const langClass = `language-${safeLang}`;
                    const escapedCode = escapeMarkdownHtml(code);
                    const safeCode = encodeURIComponent(code).replace(/'/g, '%27');
                    return `
                        <div class="code-wrapper">
                            <div class="code-actions">
                                <button type="button" class="code-btn copy-btn" data-code="${safeCode}">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                                    <span>Copy</span>
                                </button>
                                <button type="button" class="code-btn download-btn" data-code="${safeCode}" data-lang="${safeLang}">
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
                    return buildRenderedImageHtml(imgHref, title, imgText);
                };

                const rendered = marked.parse(text, { renderer: renderer });
                return sanitizeMarkdownHtml(rendered);
            } catch (e) {
                console.error("Markdown Error:", e);
                return escapeMarkdownHtml(text);
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
