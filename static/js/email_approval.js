// email_approval.js
// Add explicit user approval controls to existing email draft cards without changing the renderer.
(function () {
    function generateRequestId() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
        return `email-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    }

    function collectDraft(card) {
        if (!card) return null;
        if (typeof window.collectEmailDraftForDrag === 'function') return window.collectEmailDraftForDrag(card);
        try { return JSON.parse(card.dataset.emailDraft || '{}'); } catch (_) { return null; }
    }

    function setEmailSending(card, button, status, isSending, message) {
        if (typeof window.setHelperBusyState === 'function') window.setHelperBusyState('email', isSending, { card });
        card?.classList.toggle('email-send-busy', Boolean(isSending));
        if (button) {
            button.disabled = Boolean(isSending);
            button.classList.toggle('is-sending-email', Boolean(isSending));
            button.innerHTML = isSending
                ? '<span class="email-send-spinner" aria-hidden="true"></span><span>Sending</span>'
                : 'Approve & Send';
        }
        if (status && message) {
            status.textContent = message;
            status.style.color = 'var(--text-sub)';
        }
    }

    async function approveEmailDraft(card) {
        const draft = collectDraft(card);
        if (!draft) return;
        const status = card.querySelector('.email-draft-send-status');
        const button = card.querySelector('.email-draft-approve-btn');
        const approvalSecret = window.prompt('Enter approval key to send this draft:');
        if (!approvalSecret) return;
        const token = localStorage.getItem('helper_token_v2') || '';
        const requestId = card.dataset.emailDraftRequestId || generateRequestId();
        card.dataset.emailDraftRequestId = requestId;
        setEmailSending(card, button, status, true, 'Validating and sending...');
        try {
            const response = await fetch('/email/send-draft', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`,
                    'ngrok-skip-browser-warning': '69420',
                },
                body: JSON.stringify({ draft, admin_key: approvalSecret, request_id: requestId }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || !data.success) {
                const message = data.detail || data.status || data.error || `Send failed with status ${response.status}`;
                if (status) { status.textContent = message; status.style.color = '#ef4444'; }
                setEmailSending(card, button, null, false);
                return;
            }
            if (status) { status.textContent = data.status || 'Email sent.'; status.style.color = '#22c55e'; }
            if (button) { button.disabled = true; button.classList.remove('is-sending-email'); button.textContent = data.mode === 'simulated' ? 'Simulated' : 'Sent'; }
            if (typeof window.setHelperBusyState === 'function') window.setHelperBusyState('email', false, { card });
            card?.classList.remove('email-send-busy');
        } catch (error) {
            if (status) { status.textContent = `Send failed: ${error.message}`; status.style.color = '#ef4444'; }
            setEmailSending(card, button, null, false);
        }
    }

    function hydrateApprovalButtons(rootEl = document) {
        if (!rootEl || typeof rootEl.querySelectorAll !== 'function') return;
        rootEl.querySelectorAll('.email-draft-card').forEach(card => {
            if (card.dataset.emailApprovalHydrated === 'true') return;
            card.dataset.emailApprovalHydrated = 'true';
            if (!card.dataset.emailDraftRequestId) card.dataset.emailDraftRequestId = generateRequestId();
            let actions = card.querySelector('.email-draft-actions');
            if (!actions) {
                actions = document.createElement('div');
                actions.className = 'email-draft-actions';
                actions.style.cssText = 'display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:12px;';
                card.appendChild(actions);
            }
            let button = actions.querySelector('.email-draft-approve-btn');
            if (!button) {
                button = document.createElement('button');
                button.type = 'button';
                button.className = 'email-draft-approve-btn';
                button.textContent = 'Approve & Send';
                button.style.cssText = 'border:0;border-radius:999px;padding:9px 15px;background:var(--accent-blue);color:#fff;font-weight:800;cursor:pointer;';
                actions.appendChild(button);
            }
            let status = actions.querySelector('.email-draft-send-status');
            if (!status) {
                status = document.createElement('span');
                status.className = 'email-draft-send-status';
                status.style.cssText = 'font-size:.78rem;color:var(--text-sub);';
                actions.appendChild(status);
            }
            button.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                approveEmailDraft(card);
            });
        });
    }

    const originalHydrate = window.hydrateRenderedMarkdown;
    window.hydrateRenderedMarkdown = function hydrateRenderedMarkdownWithApproval(rootEl) {
        if (typeof originalHydrate === 'function') originalHydrate(rootEl);
        hydrateApprovalButtons(rootEl);
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => hydrateApprovalButtons(document));
    } else {
        hydrateApprovalButtons(document);
    }
    window.approveEmailDraft = approveEmailDraft;
    window.hydrateEmailDraftApprovalButtons = hydrateApprovalButtons;
})();
