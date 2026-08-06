import { state } from './state.js?v=210';

const HEADERS_BASE = { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '69420' };
const apiUrl = path => window.helperApiUrl ? window.helperApiUrl(path) : path;

function getAuthHeaders() {
    const token = localStorage.getItem('helper_token_v2') || '';
    return { ...HEADERS_BASE, 'Authorization': `Bearer ${token}` };
}

function payloadError(payload, fallback) {
    if (typeof payload?.error === 'string' && payload.error.trim()) return payload.error.trim();
    if (typeof payload?.detail === 'string' && payload.detail.trim()) return payload.detail.trim();
    if (Array.isArray(payload?.detail) && payload.detail[0]?.msg) return String(payload.detail[0].msg);
    return fallback;
}

async function parseJsonResponse(response, fallback) {
    window.handleHelperUnauthorized?.(response);
    const raw = await response.text();
    let payload = {};
    if (raw) {
        try {
            payload = JSON.parse(raw);
        } catch (_) {
            payload = {};
        }
    }
    if (!response.ok || payload?.success === false) {
        return {
            ...(payload && typeof payload === 'object' ? payload : {}),
            success: false,
            status: response.status,
            error: payloadError(payload, fallback + ' (status ' + response.status + ')')
        };
    }
    return payload;
}

async function requestJson(path, options, fallback) {
    try {
        const response = await fetch(apiUrl(path), options);
        return await parseJsonResponse(response, fallback);
    } catch (error) {
        if (error?.name === 'AbortError') throw error;
        throw new Error(fallback + '. Check your connection and try again.');
    }
}

async function handleAuth(type) {
    let params = {};
    if (type === 'login') {
        params = {
            email: document.getElementById('l-email').value.trim(),
            pwd: document.getElementById('l-pwd').value
        };
    } else if (type === 'signup') {
        params = {
            email: document.getElementById('s-email').value.trim(),
            pwd: document.getElementById('s-pwd').value,
            name: document.getElementById('s-name').value.trim()
        };
    } else if (type === 'verify') {
        params = {
            email: (document.getElementById('s-email').value || document.getElementById('l-email').value).trim(),
            otp: document.getElementById('v-otp').value.trim()
        };
    }

    return await requestJson('/' + type, {
        method: 'POST',
        headers: HEADERS_BASE,
        body: JSON.stringify(params)
    }, 'Account request failed');
}

async function createChatJob(payload, signal) {
    return await requestJson('/chat/jobs', {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify(payload),
        signal
    }, 'The response task could not be created');
}

async function streamChatJob(jobId, after = 0, signal) {
    return await fetch(apiUrl(`/chat/jobs/${encodeURIComponent(jobId)}/events?after=${Math.max(0, Number(after) || 0)}`), {
        method: 'GET',
        headers: getAuthHeaders(),
        signal
    });
}
async function streamChat(payload, signal) {
    return await fetch(apiUrl('/chat'), {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify(payload),
        signal
    });
}

async function uploadAttachments(files) {
    const selected = Array.from(files || []);
    if (!selected.length) return [];
    const form = new FormData();
    selected.forEach(file => form.append('files', file));
    const token = localStorage.getItem('helper_token_v2') || '';
    const data = await requestJson('/attachments', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}`, 'ngrok-skip-browser-warning': '69420' },
        body: form
    }, 'Attachment upload failed');
    if (!data.success) throw new Error(data.error || 'Attachment upload failed');
    return data.attachments || [];
}

async function getChatJob(jobId, after = 0) {
    if (!jobId) return { success: false, status: 404, error: 'Task not found' };
    return await requestJson(
        '/chat/jobs/' + encodeURIComponent(jobId) + '?after=' + Math.max(0, Number(after) || 0),
        { headers: getAuthHeaders() },
        'Response task could not be recovered'
    );
}
async function cancelInferenceJob(jobId) {
    if (!jobId) return null;
    return await requestJson(`/chat/jobs/${encodeURIComponent(jobId)}/cancel`, {
        method: 'POST',
        headers: getAuthHeaders()
    }, 'Generation could not be stopped');
}

async function fetchChats() {
    const token = localStorage.getItem('helper_token_v2');
    if (!token) return null;
    return await requestJson('/get_chats', {
        headers: { 'Authorization': `Bearer ${token}`, 'ngrok-skip-browser-warning': '69420' }
    }, 'Conversations could not be loaded');
}

async function syncChats(payload) {
    const token = localStorage.getItem('helper_token_v2');
    if (!token) return { success: false, error: 'Missing auth token' };
    try {
        return await requestJson('/sync_chats', {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify(payload)
        }, 'Conversation sync failed');
    } catch (error) {
        return { success: false, error: error.message };
    }
}

async function retrieveContext(text) {
    return await requestJson('/retrieve_context', {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({ text, n: 3 })
    }, 'Related context could not be retrieved');
}

async function checkUpscaleStatus(jobId) {
    return await requestJson(`/api/upscale/status/${encodeURIComponent(jobId)}`, {}, 'Image status could not be loaded');
}

const api = {
    handleAuth,
    streamChat,
    createChatJob,
    streamChatJob,
    uploadAttachments,
    getChatJob,
    cancelInferenceJob,
    fetchChats,
    syncChats,
    retrieveContext,
    checkUpscaleStatus
};
export { api };
