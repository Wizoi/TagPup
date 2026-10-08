/**
 * A bulk assignment of faces that stopped part-way is not lost (docs/ARCHITECTURE.md, "Faces and the photo's person tag", #907).
 *
 * Assigning, unmatching and ignoring faces of many photos, and Re-examine, run as a job on the server that writes the people's tags
 * a few photos at a time (tagpup.jobs.face_assignments). A restart or a closed window leaves it half done; opening the library
 * shows "N of M faces of an assignment remain" with Resume and Let go, and a job running in another window is followed. The
 * page's own side (what to read again once faces were written) is `options.changed`.
 */
import { api } from './api.js';

const ID = 'face-job-banner';
const POLL_MS = 2000;

function remaining(job) {
    return Math.max(0, (job.total || 0) - (job.done || 0));
}

function sentence(job) {
    const left = remaining(job);
    if (job.state === 'running') return `An assignment of faces is running: ${job.done} of ${job.total} faces done.`;
    return `${left} of ${job.total} faces of an assignment remain: it stopped (${job.state}) before it was finished.`;
}

function bannerElement() {
    let banner = document.getElementById(ID);
    if (banner) return banner;
    banner = document.createElement('div');
    banner.id = ID;
    banner.setAttribute('role', 'status');
    banner.style.cssText = 'padding:8px 12px;background:var(--bg-tertiary,#333);color:var(--text-primary,#eee);'
        + 'border-bottom:1px solid var(--border-color,#555);display:flex;gap:12px;align-items:center;';
    document.body.prepend(banner);
    return banner;
}

function button(label, onClick) {
    const each = document.createElement('button');
    each.textContent = label;
    each.addEventListener('click', onClick);
    return each;
}

function hide() {
    const banner = document.getElementById(ID);
    if (banner) banner.remove();
}

function show(job, options) {
    const banner = bannerElement();
    banner.textContent = '';
    const text = document.createElement('span');
    text.className = 'face-job-text';
    text.textContent = sentence(job);
    banner.appendChild(text);
    if (job.state === 'running') return;
    banner.appendChild(button('Resume', () => resume(job, options)));
    banner.appendChild(button('Let go', () => letGo(job)));
}

async function post(route, body) {
    const res = await api.fetch(route, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.success) throw new Error(data.error || data.description || `the server answered ${res.status}`);
    return data;
}

async function letGo(job) {
    try {
        await post('/api/faces/job/cancel', { job: job.job, let_go: true });
        hide();
    } catch (err) {
        alert('Could not let it go: ' + err.message);
    }
}

async function resume(job, options) {
    try {
        const data = await post('/api/faces/job/resume', { job: job.job });
        show(data.job, options);
        follow(data.job.job, options);
    } catch (err) {
        alert('Could not resume: ' + err.message);
    }
}

/** Follow job `handle` until it is not running, then hide the banner and tell the page. */
async function follow(handle, options) {
    for (;;) {
        await new Promise(resolve => window.setTimeout(resolve, options.pollMs || POLL_MS));
        let found;
        try {
            const res = await api.fetch(`/api/faces/job/status?job=${encodeURIComponent(handle)}`);
            found = (await res.json()).job;
        } catch (err) {
            return;
        }
        if (!found) return hide();
        show(found, options);
        if (found.state !== 'running') {
            if (found.state === 'done') hide();
            if (options.changed) options.changed(found);
            return;
        }
    }
}

/** On opening the library: show a job that is running or stopped part-way. Resolves to the job shown, or null. */
export async function wireFaceJobBanner(options = {}) {
    let job = null;
    try {
        const res = await api.fetch('/api/faces/job/current');
        job = (await res.json()).job;
    } catch (err) {
        return null;
    }
    if (!job) return null;
    show(job, options);
    if (job.state === 'running') follow(job.job, options);
    return job;
}
