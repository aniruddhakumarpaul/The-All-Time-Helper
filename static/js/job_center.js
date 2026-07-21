// job_center.js
// User-owned inference task visibility and cancellation.
(function () {
    const MODAL_ID = 'job-center-modal';
    const BUTTON_ID = 'open-job-center-btn';
    const apiUrl = path => window.helperApiUrl ? window.helperApiUrl(path) : path;
    let refreshTimer = null;
    let isLoading = false;

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
            .job-btn{border:1px solid var(--glass-border);background:rgba(255,255,255,.06);color:var(--text-main);padding:9px 13px;border-radius:999px;cursor:pointer;font-weight:700;font-size:.82rem;}
            .job-btn:disabled{opacity:.55;cursor:progress;}
            .job-btn.danger{color:#ffb0b0;border-color:rgba(255,93,93,.45);}
            .job-stats{display:grid;grid-template-columns:repeat(3,minmax(120px,1fr));gap:12px;margin-bottom:16px;}
            .job-stat,.job-item{border:1px solid var(--glass-border);background:rgba(255,255,255,.045);border-radius:16px;padding:12px;}
            .job-stat-label{font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;color:var(--text-sub);font-weight:900;}
            .job-stat-value{font-size:1.15rem;font-weight:850;margin-top:4px;}
            .job-list{display:grid;gap:10px;}
            .job-item-head{display:flex;align-items:center;justify-content:space-between;gap:12px;}
            .job-id{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.76rem;word-break:break-all;}
            .job-meta{color:var(--text-sub);font-size:.76rem;margin-top:8px;line-height:1.45;}
            .job-empty{color:var(--text-sub);border:1px dashed var(--glass-border);border-radius:16px;padding:18px;text-align:center;}
            .job-error{padding:14px;margin-bottom:12px;border:1px solid rgba(255,93,93,.45);border-radius:14px;color:#ffb0b0;background:rgba(255,93,93,.08);}
            @media(max-width:620px){.job-head{align-items:flex-start;flex-direction:column}.job-actions{width:100%}.job-actions .job-btn{flex:1}.job-stats{grid-template-columns:1fr}.job-card{padding:20px}.job-item-head{align-items:flex-start;flex-direction:column}.job-item-head .job-btn{width:100%}}
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
        modal.setAttribute('aria-labelledby', 'active-tasks-title');
        modal.setAttribute('aria-hidden', 'true');
        modal.innerHTML = `
            <div class="job-card">
                <div class="job-head">
                    <div>
                        <div class="job-title" id="active-tasks-title">Active Tasks</div>
                        <div class="job-sub" id="job-center-subtitle">Requests currently running for your account.</div>
                    </div>
                    <div class="job-actions">
                        <button type="button" class="job-btn" id="job-center-refresh">Refresh</button>
                        <button type="button" class="job-btn" id="job-center-close">Close</button>
                    </div>
                </div>
                <div id="job-center-content" aria-live="polite"><div class="job-empty">Loading...</div></div>
            </div>
        `;
        document.body.appendChild(modal);
        modal.addEventListener('click', event => { if (event.target === modal) closeJobCenter(); });
        modal.querySelector('#job-center-close')?.addEventListener('click', closeJobCenter);
        modal.querySelector('#job-center-refresh')?.addEventListener('click', () => loadJobs({ showLoading: true }));
        modal.addEventListener('click', event => {
            const button = event.target.closest('[data-cancel-job-id]');
            if (button) cancelJob(button);
        });
        window.HelperDialogs?.sync();
        return modal;
    }

    function renderJobs(data) {
        const modal = ensureModal();
        const content = modal.querySelector('#job-center-content');
        const subtitle = modal.querySelector('#job-center-subtitle');
        const queue = data?.queue || {};
        const jobs = Array.isArray(data?.jobs) ? data.jobs : [];
        if (subtitle) subtitle.textContent = jobs.length
            ? `${jobs.length} task(s) running for your account.`
            : 'No requests are running for your account.';
        content.innerHTML = `
            <div class="job-stats">
                <div class="job-stat"><div class="job-stat-label">Your tasks</div><div class="job-stat-value">${escapeHtml(queue.user_active_jobs || 0)}</div></div>
                <div class="job-stat"><div class="job-stat-label">Waiting</div><div class="job-stat-value">${escapeHtml(queue.queue_depth || 0)}</div></div>
                <div class="job-stat"><div class="job-stat-label">Capacity</div><div class="job-stat-value">${escapeHtml(queue.max_workers || 0)}</div></div>
            </div>
            <div class="job-list">
                ${jobs.length ? jobs.map(job => `
                    <section class="job-item">
                        <div class="job-item-head">
                            <div class="job-id">${escapeHtml(job.id)}</div>
                            <button type="button" class="job-btn danger" data-cancel-job-id="${escapeHtml(job.id)}" aria-label="Stop task ${escapeHtml(job.id)}">Stop task</button>
                        </div>
                        <div class="job-meta">Status: ${escapeHtml(job.status)} | Elapsed: ${escapeHtml(job.elapsed_seconds)}s | Limit: ${escapeHtml(job.timeout_seconds)}s</div>
                    </section>
                `).join('') : '<div class="job-empty">No active tasks. New requests will appear here while they run.</div>'}
            </div>
        `;
    }

    function showJobError(message) {
        const content = ensureModal().querySelector('#job-center-content');
        if (!content) return;
        content.querySelector('.job-error')?.remove();
        const error = document.createElement('div');
        error.className = 'job-error';
        error.setAttribute('role', 'alert');
        error.textContent = message || 'The task request failed.';
        content.prepend(error);
    }

    async function loadJobs({ showLoading = false } = {}) {
        if (isLoading) return;
        isLoading = true;
        const modal = ensureModal();
        const content = modal.querySelector('#job-center-content');
        const refresh = modal.querySelector('#job-center-refresh');
        if (showLoading && content) content.innerHTML = '<div class="job-empty">Loading active tasks...</div>';
        if (refresh) {
            refresh.disabled = true;
            refresh.setAttribute('aria-busy', 'true');
        }
        try {
            const response = await fetch(apiUrl('/jobs/status'), { headers: authHeaders(), cache: 'no-store' });
            window.handleHelperUnauthorized?.(response);
            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.success === false) throw new Error(data.error || data.detail || 'Active tasks could not be loaded.');
            renderJobs(data);
        } catch (error) {
            showJobError(error.message || 'Active tasks could not be loaded.');
        } finally {
            isLoading = false;
            if (refresh) {
                refresh.disabled = false;
                refresh.removeAttribute('aria-busy');
            }
        }
    }

    async function cancelJob(button) {
        const jobId = button?.dataset.cancelJobId;
        if (!jobId || button.disabled) return;
        const original = button.textContent;
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        button.textContent = 'Stopping...';
        try {
            const response = await fetch(apiUrl(`/jobs/${encodeURIComponent(jobId)}/cancel`), {
                method: 'POST',
                headers: authHeaders()
            });
            window.handleHelperUnauthorized?.(response);
            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.success === false) throw new Error(data.error || data.detail || 'This task could not be stopped.');
            await loadJobs({ showLoading: false });
        } catch (error) {
            showJobError(error.message || 'This task could not be stopped.');
        } finally {
            if (button.isConnected) {
                button.disabled = false;
                button.removeAttribute('aria-busy');
                button.textContent = original;
            }
        }
    }

    function openJobCenter() {
        const modal = ensureModal();
        modal.classList.add('active');
        window.HelperDialogs?.sync();
        loadJobs({ showLoading: true });
        clearInterval(refreshTimer);
        refreshTimer = setInterval(() => {
            if (document.getElementById(MODAL_ID)?.classList.contains('active')) {
                loadJobs({ showLoading: false });
            }
        }, 4000);
    }

    function closeJobCenter() {
        document.getElementById(MODAL_ID)?.classList.remove('active');
        clearInterval(refreshTimer);
        refreshTimer = null;
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
        button.innerHTML = '<span class="nav-glyph" aria-hidden="true">T</span><span>Active Tasks</span>';
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
            if (event.key !== 'Escape') return;
            const modal = document.getElementById(MODAL_ID);
            if (!modal?.classList.contains('active')) return;
            if (window.HelperDialogs && !window.HelperDialogs.isTop(modal)) return;
            event.preventDefault();
            event.stopPropagation();
            closeJobCenter();
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
