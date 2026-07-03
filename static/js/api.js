import { state } from './state.js';

const HEADERS_BASE = { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '69420' };

function getAuthHeaders() {
    const token = localStorage.getItem('helper_token_v2') || '';
    return { ...HEADERS_BASE, 'Authorization': `Bearer ${token}` };
}

async function handleAuth(type) {
    let params = {};
    if (type === 'login') {
        params = { email: document.getElementById('l-email').value, pwd: document.getElementById('l-pwd').value };
    } else if (type === 'signup') {
        params = { email: document.getElementById('s-email').value, pwd: document.getElementById('s-pwd').value, name: document.getElementById('s-name').value };
    } else if (type === 'verify') {
        params = { email: document.getElementById('s-email').value || document.getElementById('l-email').value, otp: document.getElementById('v-otp').value };
    }

    const res = await fetch('/' + type, { method: 'POST', headers: HEADERS_BASE, body: JSON.stringify(params) });
    return await res.json();
}

async function streamChat(payload, signal) {
    return await fetch('/chat', {
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
    const res = await fetch('/attachments', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}`, 'ngrok-skip-browser-warning': '69420' },
        body: form
    });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || 'Attachment upload failed');
    return data.attachments || [];
}

async function cancelInferenceJob(jobId) {
    if (!jobId) return null;
    const res = await fetch(`/chat/jobs/${encodeURIComponent(jobId)}/cancel`, {
        method: 'POST',
        headers: getAuthHeaders()
    });
    return await res.json();
}

async function fetchChats() {
    const token = localStorage.getItem('helper_token_v2');
    if (!token) return null;
    const res = await fetch('/get_chats', { headers: { 'Authorization': `Bearer ${token}`, 'ngrok-skip-browser-warning': '69420' } });
    return await res.json();
}

async function syncChats(payload) {
    const token = localStorage.getItem('helper_token_v2');
    if (!token) return { success: false, error: 'Missing auth token' };
    try {
        const res = await fetch('/sync_chats', {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify(payload)
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) return { success: false, error: data.error || `Sync failed with status ${res.status}` };
        return data;
    } catch (error) {
        return { success: false, error: error.message };
    }
}

async function retrieveContext(text) {
    const res = await fetch('/retrieve_context', {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({ text, n: 3 })
    });
    return await res.json();
}

async function checkUpscaleStatus(jobId) {
    const res = await fetch(`/api/upscale/status/${jobId}`);
    return await res.json();
}

const api = { handleAuth, streamChat, uploadAttachments, cancelInferenceJob, fetchChats, syncChats, retrieveContext, checkUpscaleStatus };
export { api };
