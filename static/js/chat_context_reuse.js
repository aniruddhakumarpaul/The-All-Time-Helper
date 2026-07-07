// chat_context_reuse.js
// Preserve raw attached context when dragging the visible context card from a sent chat message.
(function () {
    const MARKER = '__helperChatContextReuseInstalled';
    const CONTEXT_MIME = 'application/x-helper-composer-context';
    const MAX_ITEM_CHARS = 6000;
    if (window[MARKER]) return;
    window[MARKER] = true;

    function state() {
        return window.__helperState || null;
    }

    function activeChat() {
        const st = state();
        if (!st?.activeId || !Array.isArray(st.chats)) return null;
        return st.chats.find(chat => String(chat.id) === String(st.activeId)) || null;
    }

    function clip(value, size) {
        return String(value || '').trim().slice(0, size);
    }

    function normalizeContext(item) {
        if (!item || !item.text) return null;
        return {
            kind: item.kind || 'text',
            title: clip(item.title || 'Context', 80),
            subtitle: clip(item.subtitle || '', 140),
            text: clip(item.text || '', MAX_ITEM_CHARS),
            preview: item.preview || '',
            status: 'ready',
        };
    }

    function contextFromCard(card) {
        if (!card) return null;
        if (card.dataset.contextJson) {
            try {
                const parsed = JSON.parse(card.dataset.contextJson);
                const normalized = normalizeContext(parsed);
                if (normalized) return normalized;
            } catch (_) {}
        }
        const chat = activeChat();
        const chatArea = document.getElementById('chat-area');
        const messageNode = card.closest('.msg');
        if (!chat || !chatArea || !messageNode || !Array.isArray(chat.ms)) return null;
        const messageIndex = Array.from(chatArea.querySelectorAll('.msg')).indexOf(messageNode);
        const contextIndex = Number(card.dataset.contextIndex || Array.from(card.parentElement?.querySelectorAll('.chat-context-card') || []).indexOf(card));
        const item = chat.ms[messageIndex]?.contexts?.[contextIndex];
        return normalizeContext(item);
    }

    function markCards(root = document) {
        root.querySelectorAll?.('.chat-context-card').forEach((card, index) => {
            card.setAttribute('draggable', 'true');
            card.classList.add('composer-draggable-context', 'chat-context-reusable');
            if (!card.dataset.contextIndex) card.dataset.contextIndex = String(index);
            const context = contextFromCard(card);
            if (context) {
                try { card.dataset.contextJson = JSON.stringify(context); } catch (_) {}
                card.setAttribute('title', 'Drag to reuse this exact context');
            }
        });
    }

    function installDragCapture() {
        window.addEventListener('dragstart', event => {
            const card = event.target?.closest?.('.chat-context-card');
            if (!card || !event.dataTransfer) return;
            const context = contextFromCard(card);
            if (!context) return;
            event.stopImmediatePropagation();
            event.dataTransfer.setData(CONTEXT_MIME, JSON.stringify(context));
            event.dataTransfer.setData('text/plain', context.text);
            event.dataTransfer.effectAllowed = 'copy';
            document.body.classList.add('composer-context-dragging');
        }, true);
    }

    function init() {
        markCards(document);
        installDragCapture();
        const chatArea = document.getElementById('chat-area');
        if (chatArea) {
            new MutationObserver(records => {
                for (const record of records) {
                    for (const node of record.addedNodes) {
                        if (node.nodeType === 1) markCards(node);
                    }
                }
            }).observe(chatArea, { childList: true, subtree: true });
        }
        window.markReusableChatContextCards = markCards;
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
