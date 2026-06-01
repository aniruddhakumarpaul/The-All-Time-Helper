/**
 * api.js — API Client Module
 * 
 * All fetch/networking logic extracted from main_v3.js.
 * Handles auth, chat streaming, cloud sync, and context retrieval.
 */

import { state } from './state.js';

const HEADERS_BASE = { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '69420' };
const DEFAULT_LOCAL_API_BASE = 'http://127.0.0.1:9000';

function resolveApiBase() {
    const explicit = typeof window !== 'undefined' ? window.__HELPER_API_BASE__ : '';
    const stored = typeof window !== 'undefined' ? localStorage.getItem('helper_api_base_url') : '';
    const candidate = (stored || explicit || '').toString().trim().replace(/\/+$/, '');
    if (candidate) return candidate;
    if (typeof window !== 'undefined' && window.location && window.location.protocol !== 'file:') {
        return window.location.origin;
    }
    return DEFAULT_LOCAL_API_BASE;
}

function apiUrl(path) {
    return new URL(path, resolveApiBase()).toString();
}

function getAuthHeaders() {
    const token = localStorage.getItem('helper_token_v2') || '';
    return { ...HEADERS_BASE, 'Authorization': `Bearer ${token}` };
}

function checkAuthStatus(res) {
    if (res && res.status === 401) {
        if (typeof window !== 'undefined' && typeof window.signOut === 'function') {
            window.signOut();
        } else {
            localStorage.removeItem('helper_user_v2');
            localStorage.removeItem('helper_token_v2');
            localStorage.removeItem('helper_active_chat_v2');
            localStorage.removeItem('helper_active_modal_v2');
            location.reload();
        }
        throw new Error('Unauthorized');
    }
    return res;
}

/**
 * Authenticate user (login/signup/verify).
 */
async function handleAuth(type) {
    let params = {};
    if (type === 'login') {
        params = { email: document.getElementById('l-email').value, pwd: document.getElementById('l-pwd').value };
    } else if (type === 'signup') {
        params = { email: document.getElementById('s-email').value, pwd: document.getElementById('s-pwd').value, name: document.getElementById('s-name').value };
    } else if (type === 'verify') {
        params = { email: document.getElementById('s-email').value || document.getElementById('l-email').value, otp: document.getElementById('v-otp').value };
    }

    const res = await fetch(apiUrl('/' + type), { method: 'POST', headers: HEADERS_BASE, body: JSON.stringify(params) });
    return await res.json();
}

/**
 * Stream a chat response from the backend.
 * @param {Object} payload - Chat request body
 * @param {AbortSignal} signal - Abort signal for cancellation
 * @returns {Response} Raw fetch response for streaming
 */
async function streamChat(payload, signal) {
    const res = await fetch(apiUrl('/chat'), {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify(payload),
        signal
    });
    return checkAuthStatus(res);
}

/**
 * Load chats from cloud.
 */
async function fetchChats() {
    const token = localStorage.getItem('helper_token_v2');
    if (!token) return null;
    const res = await fetch(apiUrl('/get_chats'), { headers: { 'Authorization': `Bearer ${token}`, 'ngrok-skip-browser-warning': '69420' } });
    checkAuthStatus(res);
    return await res.json();
}

/**
 * Sync chats to cloud.
 */
async function syncChats(chats) {
    const token = localStorage.getItem('helper_token_v2');
    if (!token) return;
    let res;
    try {
        res = await fetch(apiUrl('/sync_chats'), {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify(chats)
        });
    } catch (e) {
        return; // Network error or similar
    }
    checkAuthStatus(res);
}

/**
 * Retrieve neural context via drag-drop.
 */
async function retrieveContext(text) {
    const res = await fetch(apiUrl('/retrieve_context'), {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({ text, n: 3 })
    });
    checkAuthStatus(res);
    return await res.json();
}

/**
 * Check upscale job status.
 */
async function checkUpscaleStatus(jobId) {
    const res = await fetch(apiUrl(`/api/upscale/status/${jobId}`));
    checkAuthStatus(res);
    return await res.json();
}

/**
 * Send an email directly via the backend API.
 */
async function sendEmailDirect(recipient, subject, body, tone, attachmentContent = null, attachmentFilename = 'report.txt', adminKey = null) {
    const res = await fetch(apiUrl('/api/send_email_direct'), {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({
            recipient,
            subject,
            body,
            tone,
            attachment_content: attachmentContent,
            attachment_filename: attachmentFilename,
            admin_key: adminKey
        })
    });
    checkAuthStatus(res);
    return await res.json();
}

/**
 * Cancel a running chat job on the backend.
 */
async function cancelChatJob(jobId) {
    if (!jobId) return null;
    const res = await fetch(apiUrl('/cancel_chat_job'), {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({ job_id: jobId })
    });
    checkAuthStatus(res);
    return await res.json();
}

/**
 * Render email preview HTML from the backend API.
 */
async function renderEmailPreview(body, tone) {
    const res = await fetch(apiUrl('/api/render_email_preview'), {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({ body, tone })
    });
    checkAuthStatus(res);
    return await res.text();
}

const api = { handleAuth, streamChat, fetchChats, syncChats, retrieveContext, checkUpscaleStatus, sendEmailDirect, cancelChatJob, renderEmailPreview };
export { api };
