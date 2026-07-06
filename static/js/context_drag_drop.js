// context_drag_drop.js
// Enables direct drag/drop neural context retrieval from chat messages into the chat surface or composer.
(function () {
    const EXTENSION_MARKER = 'context-drag-drop-installed';
    const DROP_ACTIVE_CLASS = 'context-drop-active';
    const MAX_ATTACHED_CONTEXTS = 6;
    const MAX_CONTEXT_CHARS = 6000;
    const MAX_TOTAL_CONTEXT_CHARS = 18000;
    let tempDragUnlock = false;

    if (window[EXTENSION_MARKER]) return;
    window[EXTENSION_MARKER] = true;

    function authHeaders() {
        const token = localStorage.getItem('helper_token_v2') || '';
        return {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + token,
            'ngrok-skip-browser-warning': '69420'
        };
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');
    }

    function enableMessageDragTargets(root) {
        (root || document).querySelectorAll?.('.msg .txt')?.forEach(node => {
            node.setAttribute('draggable', 'true');
            node.classList.add('context-draggable');
        });
    }

    function draggedText(event) {
        const plain = event.dataTransfer?.getData('text/plain') || '';
        if (plain.trim()) return plain.trim();
        const source = event.target?.closest?.('.msg .txt');
        return (source?.innerText || '').trim();
    }

    function attachedState() {
        const state = window.__helperState;
        if (!state) return null;
        if (!Array.isArray(state.attachedContexts)) state.attachedContexts = [];
        return state;
    }

    function attachRetrievedContext(sourceText, results) {
        const state = attachedState();
        if (!state || state.attachedContexts.length >= MAX_ATTACHED_CONTEXTS) return false;
        const snippets = Array.isArray(results) && results.length
            ? results.map((result, index) => `[Retrieved Context ${index + 1}]\n${result.content || ''}`).join('\n\n')
            : sourceText;
        const clean = String(snippets || '').trim();
        if (!clean) return false;
        const currentTotal = state.attachedContexts.reduce((total, item) => total + String(item.text || '').length, 0);
        const allowed = Math.max(0, Math.min(MAX_CONTEXT_CHARS, MAX_TOTAL_CONTEXT_CHARS - currentTotal));
        if (!allowed) return false;
        state.attachedContexts.push({ kind: 'retrieved-drag-drop', text: clean.slice(0, allowed) });
        return true;
    }

    function renderContextPanel(data, sourceText) {
        const card = document.getElementById('neural-context-card');
        const cont = document.getElementById('context-results');
        const scrim = document.getElementById('neural-scrim');
        if (!card || !cont || !scrim) return;
        cont.textContent = '';

        const header = document.createElement('div');
        header.className = 'neural-insight-box';
        const headerTitle = document.createElement('div');
        headerTitle.className = 'insight-header';
        headerTitle.textContent = 'Drag/Drop Context Retrieved';
        const headerBody = document.createElement('div');
        headerBody.className = 'insight-text';
        headerBody.textContent = data?.explanation || 'Retrieved relevant memory/context for the dropped chat text.';
        header.append(headerTitle, headerBody);
        cont.appendChild(header);

        const source = document.createElement('div');
        source.className = 'context-snippet context-source-snippet';
        const sourceMeta = document.createElement('span');
        sourceMeta.className = 'context-meta';
        sourceMeta.textContent = 'DROPPED CHAT TEXT';
        const sourceContent = document.createElement('div');
        sourceContent.style.maxHeight = '90px';
        sourceContent.style.overflowY = 'auto';
        sourceContent.style.fontSize = '0.82rem';
        sourceContent.textContent = sourceText;
        source.append(sourceMeta, sourceContent);
        cont.appendChild(source);

        const label = document.createElement('span');
        label.className = 'source-label';
        label.textContent = 'Retrieved Source Snippets';
        cont.appendChild(label);

        const results = Array.isArray(data?.results) ? data.results : [];
        if (!results.length) {
            const empty = document.createElement('p');
            empty.style.textAlign = 'center';
            empty.style.color = 'var(--text-sub)';
            empty.style.padding = '20px';
            empty.textContent = 'No direct neural links found. The dropped text was still attached to the next prompt.';
            cont.appendChild(empty);
        } else {
            results.forEach(result => {
                const div = document.createElement('div');
                div.className = 'context-snippet';
                const meta = document.createElement('span');
                meta.className = 'context-meta';
                meta.textContent = result.metadata?.type || 'DOCUMENT';
                const content = document.createElement('div');
                content.style.maxHeight = '150px';
                content.style.overflowY = 'auto';
                content.style.fontSize = '0.85rem';
                content.style.color = 'var(--text-main)';
                content.textContent = result.content || '';
                div.append(meta, content);
                cont.appendChild(div);
            });
        }

        card.classList.add('active');
        scrim.classList.add('active');
    }

    function setDropStatus(message) {
        const prompt = document.getElementById('prompt');
        if (!prompt) return;
        prompt.placeholder = message || 'Message The All Time Helper...';
        setTimeout(() => {
            if (prompt.placeholder === message) prompt.placeholder = 'Message The All Time Helper...';
        }, 2800);
    }

    async function retrieveDroppedContext(text) {
        if (!text.trim()) return;
        setDropStatus('Retrieving context from dropped chat text...');
        const mascot = document.getElementById('mascot-container');
        mascot?.classList.add('thinking');
        try {
            const response = await fetch('/retrieve_context', {
                method: 'POST',
                headers: authHeaders(),
                body: JSON.stringify({ text, n: 3 })
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.success === false) throw new Error(data.error || `Context retrieval failed (${response.status})`);
            attachRetrievedContext(text, data.results);
            renderContextPanel(data, text);
            setDropStatus('Context attached to the next prompt.');
        } catch (error) {
            attachRetrievedContext(text, []);
            renderContextPanel({ results: [], explanation: error.message || 'Context retrieval failed.' }, text);
            setDropStatus('Dropped text attached; retrieval failed.');
        } finally {
            mascot?.classList.remove('thinking');
        }
    }

    function installStyles() {
        if (document.getElementById('context-drag-drop-styles')) return;
        const style = document.createElement('style');
        style.id = 'context-drag-drop-styles';
        style.textContent = `
            .context-draggable{cursor:grab;}
            .context-draggable:active{cursor:grabbing;}
            #chat-area.${DROP_ACTIVE_CLASS}, #input-wrap.${DROP_ACTIVE_CLASS}, #prompt.${DROP_ACTIVE_CLASS}{outline:1px solid rgba(125,156,255,.55);outline-offset:4px;}
            #chat-area.${DROP_ACTIVE_CLASS}{background:rgba(125,156,255,.045);}
            #prompt.${DROP_ACTIVE_CLASS}{box-shadow:0 0 0 3px rgba(125,156,255,.14);border-radius:14px;}
            .context-source-snippet{border-style:dashed;}
        `;
        document.head.appendChild(style);
    }

    function setDropActive(on) {
        ['chat-area', 'input-wrap', 'prompt'].forEach(id => {
            document.getElementById(id)?.classList.toggle(DROP_ACTIVE_CLASS, on);
        });
    }

    function installDropZones() {
        const zones = [document.getElementById('chat-area'), document.getElementById('input-wrap'), document.getElementById('prompt')].filter(Boolean);
        zones.forEach(zone => {
            zone.addEventListener('dragover', event => {
                if (!draggedText(event)) return;
                event.preventDefault();
                if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
                setDropActive(true);
            });
            zone.addEventListener('dragleave', event => {
                if (!event.relatedTarget || !zone.contains(event.relatedTarget)) setDropActive(false);
            });
            zone.addEventListener('drop', event => {
                const text = draggedText(event);
                if (!text) return;
                event.preventDefault();
                event.stopPropagation();
                setDropActive(false);
                retrieveDroppedContext(text);
            });
        });
    }

    function installDragSourceBridge() {
        document.addEventListener('pointerdown', event => {
            const target = event.target?.closest?.('.msg .txt');
            if (target) target.setAttribute('draggable', 'true');
        }, true);
        document.addEventListener('dragstart', event => {
            const target = event.target?.closest?.('.msg .txt');
            if (!target) return;
            const text = (target.innerText || '').trim();
            if (!text) return;
            if (!window.isGDown) {
                tempDragUnlock = true;
                window.isGDown = true;
            }
            event.dataTransfer?.setData('text/plain', text);
            event.dataTransfer?.setData('application/x-helper-context', text);
            event.dataTransfer.effectAllowed = 'copy';
        }, true);
        document.addEventListener('dragend', () => {
            if (tempDragUnlock) {
                window.isGDown = false;
                tempDragUnlock = false;
                document.body.classList.remove('neural-grab-active');
            }
            setDropActive(false);
        }, true);
    }

    function observeMessages() {
        enableMessageDragTargets(document);
        const area = document.getElementById('chat-area');
        if (!area) return;
        new MutationObserver(mutations => {
            mutations.forEach(mutation => mutation.addedNodes.forEach(node => {
                if (node.nodeType === 1) enableMessageDragTargets(node);
            }));
        }).observe(area, { childList: true, subtree: true });
    }

    function init() {
        installStyles();
        observeMessages();
        installDropZones();
        installDragSourceBridge();
        window.retrieveDroppedContext = retrieveDroppedContext;
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
