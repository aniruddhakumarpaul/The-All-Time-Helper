/**
 * api.js — API Client Module
 * 
 * All fetch/networking logic extracted from main_v3.js.
 * Handles auth, chat streaming, cloud sync, and context retrieval.
 */

import { state } from './state.js';

const HEADERS_BASE = { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '69420' };

function getAuthHeaders() {
    const token = localStorage.getItem('helper_token_v2') || '';
    return { ...HEADERS_BASE, 'Authorization': `Bearer ${token}` };
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

    const res = await fetch('/' + type, { method: 'POST', headers: HEADERS_BASE, body: JSON.stringify(params) });
    return await res.json();
}

/**
 * Stream a chat response from the backend.
 * @param {Object} payload - Chat request body
 * @param {AbortSignal} signal - Abort signal for cancellation
 * @returns {Response} Raw fetch response for streaming
 */
async function streamChat(payload, signal) {
    const res = await fetch('/chat', {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify(payload),
        signal
    });
    return res;
}

/**
 * Load chats from cloud.
 */
async function fetchChats() {
    const token = localStorage.getItem('helper_token_v2');
    if (!token) return null;
    const res = await fetch('/get_chats', { headers: { 'Authorization': `Bearer ${token}`, 'ngrok-skip-browser-warning': '69420' } });
    return await res.json();
}

/**
 * Sync chats to cloud.
 */
async function syncChats(chats) {
    const token = localStorage.getItem('helper_token_v2');
    if (!token) return;
    try {
        await fetch('/sync_chats', {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify(chats)
        });
    } catch (e) { /* Silent fail for sync */ }
}

/**
 * Retrieve neural context via drag-drop.
 */
async function retrieveContext(text) {
    const res = await fetch('/retrieve_context', {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({ text, n: 3 })
    });
    return await res.json();
}

/**
 * Check upscale job status.
 */
async function checkUpscaleStatus(jobId) {
    const res = await fetch(`/api/upscale/status/${jobId}`);
    return await res.json();
}

const api = { handleAuth, streamChat, fetchChats, syncChats, retrieveContext, checkUpscaleStatus };
export { api };
