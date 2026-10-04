// TagPup's page: when the library was last in step with its folders, in the strip above a library view
// (docs/ARCHITECTURE.md, phase 9c). A view of the library is as good as its rows are current. It is read from GET
// /api/sync, the answer the Activity page's "Last in step" is made of, once when a view opens and again with Refresh
// view: one cheap request, which can fail without the view minding.
//
// Since the owner's review (#670) it is SAID ONLY WHEN SOMETHING IS WRONG, as a few words on the strip's one line: the
// read failed; a sync is running now; the library was never in step; the newest sync left it out of step (new photos,
// moved or unreadable ones, not yet settled); the last time it was in step is more than QUIET_FOR_HOURS ago (the daily
// catch-up has not run); or the folder of the view holds photos on disk the library does not (library-banner.js's
// offer). Otherwise nothing: an in-step library is the normal case. A card whose file changed says so on the card.
import { api } from './common/api.js';
import { state } from './state.js';
import { libraryStripSync } from './elements.js';
import { forgetWhatTheDiskHeld } from './library-banner.js';

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

/** Longer than this since the library was last in step is worth saying: the daily catch-up sync has not left it in step. */
export const QUIET_FOR_HOURS = 48;

/**
 * What the strip says of the library's step with its folders (state.syncInfo), or '' when nothing is wrong. `notHeld`: the
 * view's folder holds photos on disk the library does not (the banner says how many; this says when it was last in step).
 */
export function syncSentence(info, now = Date.now(), { notHeld = false } = {}) {
    if (info.status === 'error') return 'Could not read when the library was last in step with its folders.';
    if (info.status === 'idle' || !info.known) return '';
    const last = info.lastInStep ? `Last in step with its folders: ${sinceSync(info.lastInStep, now)}.` : '';
    if (info.syncing) return last ? `A sync is running now. ${last}` : 'A sync is running now.';
    if (!info.lastInStep) return 'Never in step with its folders: no sync of the whole library has finished.';
    if (info.lastRunInStep === false) return `The last sync found photos not in step with the library yet. ${last}`;
    const date = syncTime(info.lastInStep);
    if (date && now - date.getTime() > QUIET_FOR_HOURS * 3600 * 1000) return last;
    return notHeld ? last : '';
}

export function renderSyncInfo() {
    const info = state.syncInfo;
    const banner = state.moves.banner;
    const text = syncSentence(info, Date.now(), { notHeld: Boolean(banner && banner.kind === 'offer') });
    libraryStripSync.textContent = text;
    libraryStripSync.title = info.lastInStep || '';
    libraryStripSync.classList.toggle('hidden', !text);
    libraryStripSync.classList.toggle('library-strip-problem', Boolean(text));
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
            const lastInStep = body.last_in_step || null;
            const syncing = body.syncing === true;
            const run = body.last_run && typeof body.last_run === 'object' ? body.last_run : null;
            // A sync came or went: the disk and the library may have moved apart, so what the disk was said to hold is asked again.
            if (info.known && (info.lastInStep !== lastInStep || info.syncing !== syncing)) forgetWhatTheDiskHeld();
            info.lastInStep = lastInStep;
            info.lastRunInStep = run ? run.in_step !== false : null;
            info.syncing = syncing;
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
    info.lastRunInStep = null;
    info.syncing = false;
    renderSyncInfo();
}
