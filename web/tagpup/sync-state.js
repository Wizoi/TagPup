// TagPup's page: when the library was last in step with its folders, in the strip above a library view
// (docs/ARCHITECTURE.md, phase 9c). A view of the library is as good as its rows are current, so the strip says
// when sync (phase 8) last left the library in step -- "Library last in step with its folders: 5 min ago", or "never"
// -- and that a sync is running now. It is read from GET /api/sync, the answer the Activity page's "Last in step" is
// made of, once when a view opens and again with Refresh view: one cheap request, which can fail without the view
// minding (the strip says it could not be read, and nothing else changes).
import { api } from './common/api.js';
import { state } from './state.js';
import { libraryStripSync } from './elements.js';

const TIME = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/;

/** The time a sync record holds ("2026-10-03 07:41:09", the server's local clock) as a Date, or null. */
export function syncTime(text) {
    const found = TIME.exec(String(text || ''));
    if (!found) return null;
    const [, year, month, day, hour, minute, second] = found.map(Number);
    const date = new Date(year, month - 1, day, hour, minute, second);
    return Number.isNaN(date.getTime()) ? null : date;
}

/** "just now", "5 min ago", "3 h ago", "2 days ago", else the date: how long ago a sync record's time is. */
export function sinceSync(text, now = Date.now()) {
    const date = syncTime(text);
    if (!date) return String(text || '');
    const seconds = Math.round((now - date.getTime()) / 1000);
    if (seconds < 0) return 'just now';
    if (seconds < 60) return 'just now';
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return `${hours} h ago`;
    const days = Math.floor(hours / 24);
    return days < 14 ? `${days} days ago` : `on ${String(text).slice(0, 10)}`;
}

/** The sentence the strip shows for what was read (state.syncInfo). */
export function syncSentence(info, now = Date.now()) {
    if (info.status === 'error') return 'Could not read when the library was last in step with its folders.';
    if (info.status === 'idle') return '';
    if (info.status === 'loading' && !info.known) return 'Checking when the library was last in step...';
    const when = info.lastInStep ? sinceSync(info.lastInStep, now) : 'never';
    return `Library last in step with its folders: ${when}${info.syncing ? '. A sync is running now.' : ''}`;
}

export function renderSyncInfo() {
    const info = state.syncInfo;
    const text = syncSentence(info);
    libraryStripSync.textContent = text;
    libraryStripSync.title = info.lastInStep || '';
    libraryStripSync.classList.toggle('hidden', !text);
    libraryStripSync.classList.toggle('library-strip-problem', info.status === 'error');
}

/** Read it: once when a view opens, again with Refresh view. An answer that is not the newest asked for is dropped. */
export function loadSyncInfo() {
    const info = state.syncInfo;
    if (info.controller) info.controller.abort();
    info.controller = new AbortController();
    const controller = info.controller;
    info.asked += 1;
    const token = info.asked;
    info.status = 'loading';
    renderSyncInfo();
    return api.fetch('/api/sync', { signal: controller.signal })
        .then(res => res.json().catch(() => ({})).then(body => ({ ok: res.ok, body })))
        .then(({ ok, body }) => {
            if (token !== info.asked) return;
            if (!ok || !body || typeof body !== 'object') throw new Error((body && body.error) || 'The library could not be asked.');
            info.lastInStep = body.last_in_step || null;
            info.syncing = body.syncing === true;
            info.known = true;
            info.status = 'ready';
            renderSyncInfo();
        })
        .catch(err => {
            if (err.name === 'AbortError' || token !== info.asked) return;
            console.error('Could not read when the library was last in step:', err);
            info.status = 'error';
            renderSyncInfo();
        });
}

/** The view closed: nothing to say, and an answer on its way is not wanted. */
export function clearSyncInfo() {
    const info = state.syncInfo;
    if (info.controller) info.controller.abort();
    info.controller = null;
    info.asked += 1;
    info.status = 'idle';
    info.known = false;
    info.lastInStep = null;
    info.syncing = false;
    renderSyncInfo();
}
