        // Exported globally for index.html onclick handlers
        window.copyCode = function(btn, encodedCode) {
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
        };

        window.downloadCode = function(encodedCode, lang) {
            const code = decodeURIComponent(encodedCode);
            const blob = new Blob([code], { type: 'text/plain' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            const ext = lang.toLowerCase() === 'python' ? 'py' : (lang || 'txt');
            a.href = url;
            a.download = `snippet_${Math.floor(Math.random()*10000)}.${ext}`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
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
                    const safeCode = encodeURIComponent(code);
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
                    if (typeof href === 'object') {
                        const obj = href;
                        href = obj.href || '';
                        text = obj.text || '';
                        title = obj.title || '';
                    }
                    // Strip any previous retry param so we start clean
                    const baseUrl = href.replace(/&_r=\d+/, '').replace(/\?_r=\d+/, '');
                    const safeHref = baseUrl.replace(/'/g, "\\'");
                    const safeAlt = (text || 'AI Generated Image').replace(/'/g, "\\'");
                    const uniqueId = 'img-' + Math.random().toString(36).substr(2, 9);

                    return `
                        <div class="ai-img-wrapper" id="wrap-${uniqueId}">
                            <div class="ai-img-loading" id="load-${uniqueId}">
                                <div class="img-shimmer"></div>
                                <span>🎨 Generating image...</span>
                            </div>
                            <img 
                                id="${uniqueId}"
                                src="${baseUrl}" 
                                alt="${safeAlt}" 
                                title="${title || ''}" 
                                class="chat-rendered-img"
                                style="display:none;"
                                onclick="window.openImageModal('${safeHref}')"
                                onload="
                                    const lEl = document.getElementById('load-${uniqueId}');
                                    if(lEl) lEl.style.display='none';
                                    this.style.display='block';
                                "
                                onerror="
                                    this.retryCount = (this.retryCount || 0) + 1;
                                    const loadEl = document.getElementById('load-${uniqueId}');
                                    if (this.retryCount <= 12) {
                                        const s = this;
                                        // Adaptive backoff: give the GPU more time initially (6s), then standard waits
                                        const delay = this.retryCount <= 3 ? 6000 : 8000;
                                        
                                        if(loadEl) {
                                            const statusSpan = loadEl.querySelector('span');
                                            if(statusSpan) statusSpan.textContent = '🎨 Generating... (' + this.retryCount + '/12)';
                                        }
                                        
                                        setTimeout(() => { 
                                            // Append retry param to force refresh, but keep seed consistent in base URL
                                            s.src = '${safeHref}' + (('${safeHref}'.includes('?')) ? '&' : '?') + '_r=' + s.retryCount; 
                                        }, delay);
                                    } else {
                                        if(loadEl) loadEl.innerHTML = '<div style=\\'padding:16px;color:var(--text-sub)\\'>⚠️ Generation took too long. <a href=\\'${safeHref}\\' target=\\'_blank\\' style=\\'color:var(--accent-blue)\\'>View directly →</a></div>';
                                    }
                                "

                            >
                        </div>`;
                };

                return marked.parse(text, { renderer: renderer });
            } catch (e) {
                console.error("Markdown Error:", e);
                return text;
            }
        };
