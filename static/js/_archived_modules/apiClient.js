/**
 * apiClient.js - Networking and Server Communication
 */

export class APIClient {
    constructor() {
        this.baseHeaders = {
            'ngrok-skip-browser-warning': '69420'
        };
    }

    getAuthHeader() {
        const token = localStorage.getItem('helper_token_v2');
        return token ? { 'Authorization': `Bearer ${token}` } : {};
    }

    async post(endpoint, body) {
        const res = await fetch(endpoint, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...this.baseHeaders,
                ...this.getAuthHeader()
            },
            body: JSON.stringify(body)
        });
        return res;
    }

    async get(endpoint) {
        const res = await fetch(endpoint, {
            headers: {
                ...this.baseHeaders,
                ...this.getAuthHeader()
            }
        });
        return res;
    }

    // --- Specific API Calls ---

    async login(email, pwd) {
        const res = await this.post('/login', { email, pwd });
        return await res.json();
    }

    async signup(email, pwd, name) {
        const res = await this.post('/signup', { email, pwd, name });
        return await res.json();
    }

    async verifyOTP(email, otp) {
        const res = await this.post('/verify', { email, otp });
        return await res.json();
    }

    async fetchChats() {
        const res = await this.get('/get_chats');
        return await res.json();
    }

    async syncChats(chats) {
        const res = await this.post('/sync_chats', chats);
        return await res.json();
    }

    async getUpscaleStatus(jobId) {
        const res = await this.get(`/api/upscale/status/${jobId}`);
        return await res.json();
    }

    async retrieveContext(text, n = 3) {
        const res = await this.post('/retrieve_context', { text, n });
        return await res.json();
    }

    // Stream Chat logic
    async streamChat(payload, onChunk, onStatus, onDone, signal) {
        const res = await fetch('/chat', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...this.baseHeaders,
                ...this.getAuthHeader()
            },
            body: JSON.stringify(payload),
            signal
        });

        if (res.status === 401) throw new Error('UNAUTHORIZED');
        if (!res.ok) throw new Error(`HTTP_${res.status}`);

        const reader = res.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();

            lines.forEach(line => {
                const trimmed = line.trim();
                if (!trimmed || trimmed.startsWith('<')) return;
                try {
                    const j = JSON.parse(trimmed);
                    if (j.status) onStatus(j.status);
                    if (j.message && j.message.content) onChunk(j.message.content);
                } catch (e) {
                    if (trimmed.length > 5) console.warn("Parse fail:", trimmed);
                }
            });
        }
        
        if (buffer.trim()) {
            try {
                const j = JSON.parse(buffer);
                if (j.message && j.message.content) onChunk(j.message.content);
            } catch (e) { }
        }
        
        onDone();
    }
}

export const apiClient = new APIClient();
