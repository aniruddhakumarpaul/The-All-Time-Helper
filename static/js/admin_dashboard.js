// admin_dashboard.js
// Authenticated, user-safe runtime readiness view.
(function () {
    const MODAL_ID = 'admin-ops-modal';
    const BUTTON_ID = 'open-admin-ops-btn';
    const apiUrl = path => window.helperApiUrl ? window.helperApiUrl(path) : path;

    function authHeaders() {
        const token = localStorage.getItem('helper_token_v2') || '';
        return { 'Authorization': 'Bearer ' + token, 'ngrok-skip-browser-warning': '69420' };
    }

    function statusClass(status) {
        if (status === 'ok') return 'ops-ok';
        if (status === 'fail') return 'ops-fail';
        if (status === 'off') return 'ops-off';
        return 'ops-warn';
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');
    }

    function detailLabel(key) {
        return String(key || '')
            .replaceAll('_', ' ')
            .replace(/\b\w/g, letter => letter.toUpperCase());
    }

    function detailsHtml(details) {
        const entries = Object.entries(details || {}).filter(([, value]) => value !== undefined && value !== null && value !== '');
        if (!entries.length) return '';
        return '<dl class="ops-details">' + entries.map(([key, value]) => {
            const rendered = Array.isArray(value) ? value.join(', ') : typeof value === 'object' ? JSON.stringify(value) : String(value);
            return `<div><dt>${escapeHtml(detailLabel(key))}</dt><dd>${escapeHtml(rendered)}</dd></div>`;
        }).join('') + '</dl>';
    }

    function ensureStyles() {
        if (document.getElementById('admin-ops-styles')) return;
        const style = document.createElement('style');
        style.id = 'admin-ops-styles';
        style.textContent = `
            #${MODAL_ID}{position:fixed;inset:0;z-index:12000;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.55);backdrop-filter:blur(10px);padding:22px;}
            #${MODAL_ID}.active{display:flex;}
            .ops-card{width:min(920px,96vw);max-height:86vh;overflow:auto;border:1px solid var(--glass-border);border-radius:24px;background:var(--glass-bg);box-shadow:0 30px 80px rgba(0,0,0,.45);color:var(--text-main);padding:24px;}
            .ops-head{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:18px;}
            .ops-title{font-size:1.25rem;font-weight:800;letter-spacing:.2px;}
            .ops-sub{color:var(--text-sub);font-size:.82rem;margin-top:4px;}
            .ops-actions{display:flex;gap:10px;align-items:center;}
            .ops-btn{border:1px solid var(--glass-border);background:rgba(255,255,255,.06);color:var(--text-main);padding:9px 13px;border-radius:999px;cursor:pointer;font-weight:700;font-size:.82rem;}
            .ops-btn:disabled{opacity:.55;cursor:progress;}
            .ops-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;}
            .ops-item{border:1px solid var(--glass-border);background:rgba(255,255,255,.045);border-radius:18px;padding:14px;min-height:130px;}
            .ops-row{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:8px;}
            .ops-name{font-weight:800;font-size:.96rem;}
            .ops-pill{font-size:.68rem;text-transform:uppercase;letter-spacing:.08em;font-weight:900;padding:4px 8px;border-radius:999px;border:1px solid currentColor;}
            .ops-ok{color:#3ddc97}.ops-warn{color:#ffca4b}.ops-fail{color:#ff7474}.ops-off{color:var(--text-sub)}
            .ops-summary{font-size:.82rem;color:var(--text-sub);line-height:1.45;margin-bottom:10px;}
            .ops-details{font-size:.72rem;color:var(--text-sub);display:grid;gap:6px;margin:0;}
            .ops-details div{display:grid;grid-template-columns:118px 1fr;gap:8px;border-top:1px solid rgba(255,255,255,.07);padding-top:6px;}
            .ops-details dt{opacity:.78;white-space:nowrap;}
            .ops-details dd{margin:0;word-break:break-word;color:var(--text-main);opacity:.86;}
            .ops-error{padding:14px;border:1px solid rgba(255,93,93,.45);border-radius:14px;color:#ffb0b0;background:rgba(255,93,93,.08);}
            @media(max-width:620px){.ops-head{align-items:flex-start;flex-direction:column}.ops-actions{width:100%}.ops-btn{flex:1}.ops-card{padding:20px}.ops-details div{grid-template-columns:1fr}}
        `;
        document.head.appendChild(style);
    }

    function ensureModal() {
        ensureStyles();
        let modal = document.getElementById(MODAL_ID);
        if (modal) return modal;
        modal = document.createElement('div');
        modal.id = MODAL_ID;
        modal.dataset.helperDialog = '';
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.setAttribute('aria-labelledby', 'system-status-title');
        modal.setAttribute('aria-hidden', 'true');
        modal.innerHTML = `
            <div class="ops-card">
                <div class="ops-head">
                    <div>
                        <div class="ops-title" id="system-status-title">System Status</div>
                        <div class="ops-sub" id="admin-ops-subtitle">Checking the services available to your account.</div>
                    </div>
                    <div class="ops-actions">
                        <button type="button" class="ops-btn" id="admin-ops-refresh">Refresh</button>
                        <button type="button" class="ops-btn" id="admin-ops-close">Close</button>
                    </div>
                </div>
                <div id="admin-ops-content" aria-live="polite"><div class="ops-summary">Loading...</div></div>
            </div>
        `;
        document.body.appendChild(modal);
        modal.addEventListener('click', event => { if (event.target === modal) closeDashboard(); });
        modal.querySelector('#admin-ops-close')?.addEventListener('click', closeDashboard);
        modal.querySelector('#admin-ops-refresh')?.addEventListener('click', () => loadDashboard());
        window.HelperDialogs?.sync();
        return modal;
    }

    function renderStatus(data) {
        const modal = ensureModal();
        const content = modal.querySelector('#admin-ops-content');
        const subtitle = modal.querySelector('#admin-ops-subtitle');
        const overall = data?.overall || 'warn';
        const updated = data?.generated_at ? new Date(data.generated_at) : null;
        const updatedLabel = updated && !Number.isNaN(updated.getTime()) ? ' | Updated ' + updated.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
        if (subtitle) {
            subtitle.innerHTML = `Overall: <span class="${statusClass(overall)}">${escapeHtml(overall.toUpperCase())}</span>${escapeHtml(updatedLabel)}`;
        }
        const components = Array.isArray(data?.components) ? data.components : [];
        if (!components.length) {
            content.innerHTML = '<div class="ops-error" role="alert">No status information is available.</div>';
            return;
        }
        content.innerHTML = '<div class="ops-grid">' + components.map(component => `
            <section class="ops-item">
                <div class="ops-row">
                    <div class="ops-name">${escapeHtml(component.name)}</div>
                    <div class="ops-pill ${statusClass(component.status)}">${escapeHtml(component.status || 'warn')}</div>
                </div>
                <div class="ops-summary">${escapeHtml(component.summary || '')}</div>
                ${detailsHtml(component.details)}
            </section>
        `).join('') + '</div>';
    }

    async function loadDashboard() {
        const modal = ensureModal();
        const content = modal.querySelector('#admin-ops-content');
        const refresh = modal.querySelector('#admin-ops-refresh');
        if (content) content.innerHTML = '<div class="ops-summary">Checking system status...</div>';
        if (refresh) {
            refresh.disabled = true;
            refresh.setAttribute('aria-busy', 'true');
        }
        try {
            const response = await fetch(apiUrl('/admin/status'), { headers: authHeaders(), cache: 'no-store' });
            window.handleHelperUnauthorized?.(response);
            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.success === false) throw new Error(data.error || data.detail || 'System status could not be loaded.');
            renderStatus(data);
        } catch (error) {
            if (content) content.innerHTML = `<div class="ops-error" role="alert">${escapeHtml(error.message || 'System status could not be loaded.')}</div>`;
        } finally {
            if (refresh) {
                refresh.disabled = false;
                refresh.removeAttribute('aria-busy');
            }
        }
    }

    function openDashboard() {
        const modal = ensureModal();
        modal.classList.add('active');
        window.HelperDialogs?.sync();
        loadDashboard();
    }

    function closeDashboard() {
        document.getElementById(MODAL_ID)?.classList.remove('active');
        window.HelperDialogs?.sync();
    }

    function installButton() {
        if (document.getElementById(BUTTON_ID)) return true;
        const nav = document.querySelector('#sidebar .bottom-nav');
        if (!nav) return false;
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'set-btn';
        button.id = BUTTON_ID;
        button.setAttribute('aria-haspopup', 'dialog');
        button.setAttribute('aria-controls', MODAL_ID);
        button.innerHTML = '<span class="nav-glyph" aria-hidden="true">S</span><span>System Status</span>';
        button.addEventListener('click', openDashboard);
        nav.insertBefore(button, nav.firstChild);
        return true;
    }

    function init() {
        if (!installButton()) {
            const timer = setInterval(() => {
                if (installButton()) clearInterval(timer);
            }, 300);
            setTimeout(() => clearInterval(timer), 10000);
        }
        window.openAdminOpsDashboard = openDashboard;
        document.addEventListener('keydown', event => {
            if (event.key !== 'Escape') return;
            const modal = document.getElementById(MODAL_ID);
            if (!modal?.classList.contains('active')) return;
            if (window.HelperDialogs && !window.HelperDialogs.isTop(modal)) return;
            event.preventDefault();
            event.stopPropagation();
            closeDashboard();
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
