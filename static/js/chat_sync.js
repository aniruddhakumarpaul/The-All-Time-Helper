function chatTimestamp(chat) {
    const value = Number(chat?.updated_at ?? chat?.updatedAt ?? 0);
    return Number.isFinite(value) ? value : 0;
}

function normalizeChat(chat) {
    if (!chat || typeof chat !== 'object') return null;
    const id = String(chat.id || '').trim();
    if (!id) return null;
    const updatedAt = chatTimestamp(chat);
    return {
        ...chat,
        id,
        title: String(chat.title || 'New Chat'),
        ms: Array.isArray(chat.ms) ? chat.ms : [],
        updated_at: updatedAt,
        updatedAt,
    };
}

function sortChatsNewestFirst(chats) {
    return (Array.isArray(chats) ? chats : []).slice().sort((a, b) => {
        const pinnedOrder = Number(Boolean(b.pinned)) - Number(Boolean(a.pinned));
        if (pinnedOrder) return pinnedOrder;
        return chatTimestamp(b) - chatTimestamp(a);
    });
}

function mergeChatsByRecency(localChats, remoteChats) {
    const byId = new Map();

    // Load remote first so an equally recent local copy retains browser-only state.
    for (const raw of [...(remoteChats || []), ...(localChats || [])]) {
        const chat = normalizeChat(raw);
        if (!chat) continue;
        const existing = byId.get(chat.id);
        if (!existing || chatTimestamp(chat) >= chatTimestamp(existing)) byId.set(chat.id, chat);
    }

    return sortChatsNewestFirst(Array.from(byId.values()));
}

export { chatTimestamp, mergeChatsByRecency, normalizeChat, sortChatsNewestFirst };
