// job_center.js
// Lightweight job center for live inference queue visibility and cancellation.
(function () {
    const MODAL_ID = 'job-center-modal';
    const BUTTON_ID = 'open-job-center-btn';
    let refreshTimer = null;

    function authHeaders() {
        const token = localStorage.getItem('helper_token_v2') || '';
        return { 'Authorization': 'Bearer ' + token, 'ngrok-skip-browser-warning': '69420' };
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');
    }

    function ensureStyles() {
        if (document.getElementById('job-center-styles')) return;
        const style = document.createElement('style');
        style.id = 'job-center-styles';
        style.textContent = `
            #${MODAL_ID}{position:fixed;inset:0;z-index:12100;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.55);backdrop-filter:blur(10px);padding:22px;}
            #${MODAL_ID}.active{display:flex;}
            .job-card{width:min(760px,96vw);max-height:84vh;overflow:auto;border:1px solid var(--glass-border);border-radius:24px;background:var(--glass-bg);box-shadow:0 30px 80px rgba(0,0,0,.45);color:var(--text-main);padding:24px;}
            .job-head{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:18px;}
            .job-title{font-size:1.22rem;font-weight:850;}
            .job-sub{font-size:.82rem;color:var(--text-sub);margin-top:4px;}
            .job-actions{display:flex;gap:10px;align-items:center;}
            .job-btn{border:1px solid var(--glass-border);background:rgba(255,255,255,.06);color:var(--text-main);padding:9px 13px;border-radius:12px;cursor:pointer;font-weight:700;font-size:.82rem;}
            .job-btn.danger{color:#ff8c8c;border-color:rgba(255,93,93,.4);}
            .job-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:16px;}
            .job-stat,.job-item{border:1px solid var(--glass-border);background:rgba(255,255,255,.045);border-radius:16px;padding:12px;}
            .job-stat-label{font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;color:var(--text-sub);font-weight:900;}
            .job-stat-value{font-size:1.15rem;font-weight:850;margin-top:4px;}
            .job-list{display:grid;gap:10px;}
            .job-item-head{display:flex;align-items:center;justify-content:space-between;gap:12px;}
            .job-id{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.76rem;word-break:break-all;}
            .job-meta{color:var(--text-sub);font-size:.76rem;margin-top:8px;line-height:1.45;}
            .job-empty{color:var(--text-sub);border:1px dashed var(--glass-border);border-radius:16px;padding:18px;text-align:center;}
            .job-error{padding:14px;border:1px solid rgba(255,93,93,.45);border-radius:14px;color:#ff8c8c;background:rgba(255,93,93,.08);}
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
            <div class="job-card" role="dialog" aria-modal="true" aria-label="Job Center">
                <div class="job-head">
                    <div>
                        <div class="job-title">Job Center</div>
                        <div class="job-sub" id="job-center-subtitle">Live inference queue and active requests.</div>
                    </div>
                    <div class="job-actions">
                        <button class="job-btn" id="job-center-refresh">Refresh</button>
                        <button class="job-btn" id="job-center-close">Close</button>
                    </div>
                </div>
                <div id="job-center-content"><div class="job-empty">Loading...</div></div>
            </div>
        `;
        document.body.appendChild(modal);
        modal.addEventListener('click', event => { if (event.target === modal) closeJobCenter(); });
        modal.querySelector('#job-center-close')?.addEventListener('click', closeJobCenter);
        modal.querySelector('#job-center-refresh')?.addEventListener('click', loadJobs);
        modal.addEventListener('click', event => {
            const button = event.target.closest('[data-cancel-job-id]');
            if (button) cancelJob(button.dataset.cancelJobId);
        });
        return modal;
    }

    function renderJobs(data) {
        const modal = ensureModal();
        const content = modal.querySelector('#job-center-content');
        const subtitle = modal.querySelector('#job-center-subtitle');
        const queue = data?.queue || {};
        const jobs = Array.isArray(data?.jobs) ? data.jobs : [];
        if (subtitle) subtitle.textContent = `${jobs.length} active job(s), ${queue.queue_depth || 0} queued globally.`;
        content.innerHTML = `
            <div class="job-stats">
                <div class="job-stat"><div class="job-stat-label">Active</div><div class="job-stat-value">${escapeHtml(queue.user_active_jobs || 0)}</div></div>
                <div class="job-stat"><div class="job-stat-label">Queued</div><div class="job-stat-value">${escapeHtml(queue.queue_depth || 0)}</div></div>
                <div class="job-stat"><div class="job-stat-label">Workers</div><div class="job-stat-value">${escapeHtml(queue.max_workers || 0)}</div></div>
                <div class="job-stat"><div class="job-stat-label">Depth Limit</div><div class="job-stat-value">${escapeHtml(queue.max_queue_depth || 0)}</div></div>
            </div>
            <div class="job-list">
                ${jobs.length ? jobs.map(job => `
                    <section class="job-item">
                        <div class="job-item-head">
                            <div class="job-id">${escapeHtml(job.id)}</div>
                            <button class="job-btn danger" data-cancel-job-id="${escapeHtml(job.id)}">Cancel</button>
                        </div>
                        <div class="job-meta">Status: ${escapeHtml(job.status)} · Elapsed: ${escapeHtml(job.elapsed_seconds)}s · Timeout: ${escapeHtml(job.timeout_seconds)}s</div>
                    </section>
                `).join('') : '<div class="job-empty">No active jobs for your account.</div>'}
            </div>
        `;
    }

    async function loadJobs() {
        const modal = ensureModal();
        const content = modal.querySelector('#job-center-content');
        if (content) content.innerHTML = '<div class="job-empty">Loading jobs...</div>';
        try {
            const response = await fetch('/jobs/status', { headers: authHeaders(), cache: 'no-store' });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.success === false) throw new Error(data.error || `HTTP ${response.status}`);
            renderJobs(data);
        } catch (error) {
            if (content) content.innerHTML = `<div class="job-error">${escapeHtml(error.message || error)}</div>`;
        }
    }

    async function cancelJob(jobId) {
        if (!jobId) return;
        try {
            await fetch(`/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST', headers: authHeaders() });
        } finally {
            loadJobs();
        }
    }

    function openJobCenter() {
        ensureModal().classList.add('active');
        loadJobs();
        clearInterval(refreshTimer);
        refreshTimer = setInterval(() => {
            if (document.getElementById(MODAL_ID)?.classList.contains('active')) loadJobs();
        }, 4000);
    }

    function closeJobCenter() {
        document.getElementById(MODAL_ID)?.classList.remove('active');
        clearInterval(refreshTimer);
        refreshTimer = null;
    }

    function installButton() {
        if (document.getElementById(BUTTON_ID)) return true;
        const nav = document.querySelector('#sidebar .bottom-nav');
        if (!nav) return false;
        const button = document.createElement('div');
        button.className = 'set-btn';
        button.id = BUTTON_ID;
        button.innerHTML = '<span style="font-size:1rem;line-height:1;">↯</span> Job Center';
        button.addEventListener('click', openJobCenter);
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
        window.openJobCenter = openJobCenter;
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape') closeJobCenter();
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
