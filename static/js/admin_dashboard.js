// admin_dashboard.js
// Lightweight authenticated operations dashboard injected without touching index.html.
(function () {
    const MODAL_ID = 'admin-ops-modal';
    const BUTTON_ID = 'open-admin-ops-btn';

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

    function detailsHtml(details) {
        const entries = Object.entries(details || {}).filter(([, value]) => value !== undefined && value !== null && value !== '');
        if (!entries.length) return '';
        return '<dl class="ops-details">' + entries.map(([key, value]) => {
            const rendered = Array.isArray(value) ? value.join(', ') : typeof value === 'object' ? JSON.stringify(value) : String(value);
            return `<div><dt>${escapeHtml(key.replaceAll('_', ' '))}</dt><dd>${escapeHtml(rendered)}</dd></div>`;
        }).join('') + '</dl>';
    }

    function ensureStyles() {
        if (document.getElementById('admin-ops-styles')) return;
        const style = document.createElement('style');
        style.id = 'admin-ops-styles';
        style.textContent = `
            #${MODAL_ID}{position:fixed;inset:0;z-index:12000;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.55);backdrop-filter:blur(10px);padding:22px;}
            #${MODAL_ID}.active{display:flex;}
            .ops-card{width:min(980px,96vw);max-height:86vh;overflow:auto;border:1px solid var(--glass-border);border-radius:24px;background:var(--glass-bg);box-shadow:0 30px 80px rgba(0,0,0,.45);color:var(--text-main);padding:24px;}
            .ops-head{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:18px;}
            .ops-title{font-size:1.25rem;font-weight:800;letter-spacing:.2px;}
            .ops-sub{color:var(--text-sub);font-size:.82rem;margin-top:4px;}
            .ops-actions{display:flex;gap:10px;align-items:center;}
            .ops-btn{border:1px solid var(--glass-border);background:rgba(255,255,255,.06);color:var(--text-main);padding:9px 13px;border-radius:12px;cursor:pointer;font-weight:700;font-size:.82rem;}
            .ops-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;}
            .ops-item{border:1px solid var(--glass-border);background:rgba(255,255,255,.045);border-radius:18px;padding:14px;min-height:130px;}
            .ops-row{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:8px;}
            .ops-name{font-weight:800;font-size:.96rem;}
            .ops-pill{font-size:.68rem;text-transform:uppercase;letter-spacing:.08em;font-weight:900;padding:4px 8px;border-radius:999px;border:1px solid currentColor;}
            .ops-ok{color:#3ddc97}.ops-warn{color:#ffca4b}.ops-fail{color:#ff5d5d}.ops-off{color:var(--text-sub)}
            .ops-summary{font-size:.82rem;color:var(--text-sub);line-height:1.45;margin-bottom:10px;}
            .ops-details{font-size:.72rem;color:var(--text-sub);display:grid;gap:6px;margin:0;}
            .ops-details div{display:grid;grid-template-columns:96px 1fr;gap:8px;border-top:1px solid rgba(255,255,255,.07);padding-top:6px;}
            .ops-details dt{text-transform:capitalize;opacity:.75;white-space:nowrap;}
            .ops-details dd{margin:0;word-break:break-word;color:var(--text-main);opacity:.82;}
            .ops-error{padding:14px;border:1px solid rgba(255,93,93,.45);border-radius:14px;color:#ff8c8c;background:rgba(255,93,93,.08);}
            #${BUTTON_ID}{cursor:pointer;}
        `;
        document.head.appendChild(style);
    }

    function ensureModal() {
        ensureStyles();
        let modal = document.getElementById(MODAL_ID);
        if (modal) return modal;
        modal = document.createElement('div');
        modal.id = MODAL_ID;
        modal.innerHTML = `
            <div class="ops-card" role="dialog" aria-modal="true" aria-label="Admin Operations Dashboard">
                <div class="ops-head">
                    <div>
                        <div class="ops-title">Admin Operations</div>
                        <div class="ops-sub" id="admin-ops-subtitle">Runtime health and configuration snapshot.</div>
                    </div>
                    <div class="ops-actions">
                        <button class="ops-btn" id="admin-ops-refresh">Refresh</button>
                        <button class="ops-btn" id="admin-ops-close">Close</button>
                    </div>
                </div>
                <div id="admin-ops-content"><div class="ops-summary">Loading...</div></div>
            </div>
        `;
        document.body.appendChild(modal);
        modal.addEventListener('click', event => { if (event.target === modal) closeDashboard(); });
        modal.querySelector('#admin-ops-close')?.addEventListener('click', closeDashboard);
        modal.querySelector('#admin-ops-refresh')?.addEventListener('click', loadDashboard);
        return modal;
    }

    function renderStatus(data) {
        const modal = ensureModal();
        const content = modal.querySelector('#admin-ops-content');
        const subtitle = modal.querySelector('#admin-ops-subtitle');
        const overall = data?.overall || 'warn';
        if (subtitle) subtitle.innerHTML = `Overall: <span class="${statusClass(overall)}">${escapeHtml(overall.toUpperCase())}</span> · ${escapeHtml(data?.user || '')}`;
        const components = Array.isArray(data?.components) ? data.components : [];
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
        if (content) content.innerHTML = '<div class="ops-summary">Loading runtime status...</div>';
        try {
            const response = await fetch('/admin/status', { headers: authHeaders(), cache: 'no-store' });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.success === false) throw new Error(data.error || `HTTP ${response.status}`);
            renderStatus(data);
        } catch (error) {
            if (content) content.innerHTML = `<div class="ops-error">${escapeHtml(error.message || error)}</div>`;
        }
    }

    function openDashboard() {
        const modal = ensureModal();
        modal.classList.add('active');
        loadDashboard();
    }

    function closeDashboard() {
        document.getElementById(MODAL_ID)?.classList.remove('active');
    }

    function installButton() {
        if (document.getElementById(BUTTON_ID)) return true;
        const nav = document.querySelector('#sidebar .bottom-nav');
        if (!nav) return false;
        const button = document.createElement('div');
        button.className = 'set-btn';
        button.id = BUTTON_ID;
        button.innerHTML = '<span style="font-size:1rem;line-height:1;">⌁</span> Ops Dashboard';
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
            if (event.key === 'Escape') closeDashboard();
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
