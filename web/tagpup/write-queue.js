// TagPup's page: what the photo write queue (edits.js queuePhotoWrite) is doing, in a
// small status in the bottom-right corner, as Windows Live Photo Gallery showed it.
//
// Writes to photos go one at a time, and on a network share each can take seconds;
// three clicks queue three writes, and nothing on the page said whether they were
// done, still waiting, or had failed. The status says "Saving 2 of 5..." while the
// queue works, "All changes saved" when it is empty, "1 failed" when a write failed.
// Hovering, focusing or clicking it opens a list of the entries: what each does, its
// state and time, the last few done, and a failed one's reason, with Retry and
// Dismiss. The entries are state.writeQueue's; only edits.js adds and marks them.
import { buildElement, replaceContent } from './common/dom.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import { libraryName } from './looking.js';
import { writeQueueBox } from './elements.js';

/** Done entries the list keeps; a failed one stays until it is dismissed or retried. *//**
 * What a write's reply says of where it wrote (the server's `file_only`, `with_rows`: files
 * written with no row because the library does not hold their folder, and with theirs), as a
 * sentence to follow "Saved.", or '' when every file was written with its rows.
 */
export function whereWritten(data) {
    const only = (data && data.file_only) || 0;
    if (!only) return '';
    const name = libraryName();
    const rows = (data && data.with_rows) || 0;
    return rows
        ? ` ${only} to the files only (${name} does not hold their folder), ${rows} with their rows.`
        : ` Written to the files only: ${name} does not hold this folder.`;
}


export const KEEP_DONE = 10;

/** A new entry, waiting: `label` says what it does. */
export function queueEntry(label) {
    const queue = state.writeQueue;
    if (!pendingWrites()) {
        // A new run of writes: "Saving 1 of 1" again, not "Saving 7 of 7".
        queue.batchTotal = 0;
        queue.batchSettled = 0;
        queue.batchSkipped = 0;
    }
    queue.batchTotal += 1;
    const entry = { id: queue.nextId++, label, status: 'waiting', error: '', at: new Date() };
    queue.entries.push(entry);
    renderWriteQueue();
    return entry;
}

/** Mark `entry` writing, done or failed. */
export function markEntry(entry, status) {
    entry.status = status;
    entry.at = new Date();
    const queue = state.writeQueue;
    if (status === 'done' || status === 'failed') queue.batchSettled += 1;
    // A write that finished may have changed what the library's navigator counts (keywords, people): read again, after a moment.
    if (status === 'done') upper.navigatorCountsChanged();
    const done = queue.entries.filter(e => e.status === 'done');
    if (done.length > KEEP_DONE) {
        const dropped = new Set(done.slice(0, done.length - KEEP_DONE));
        queue.entries = queue.entries.filter(e => !dropped.has(e));
    }
    renderWriteQueue();
}

/**
 * A bulk write skipped `count` photos as damaged: nothing is written into a photo found
 * damaged (the server's libraries.leave_out_damaged). Said on the entry and in the status.
 */
export function noteSkipped(entry, count) {
    if (!entry || !count) return;
    entry.note = `${count} skipped: damaged`;
    state.writeQueue.batchSkipped += count;
    renderWriteQueue();
}

/** How many writes are waiting or under way. */
export function pendingWrites() {
    return state.writeQueue.entries.filter(e => e.status === 'waiting' || e.status === 'writing').length;
}

function failedEntries() {
    return state.writeQueue.entries.filter(e => e.status === 'failed');
}

/** Take a failed entry off the list. */
export function dismissEntry(entry) {
    state.writeQueue.entries = state.writeQueue.entries.filter(e => e !== entry);
    renderWriteQueue();
}

/** Queue a failed entry's write again, as a new entry, and take the failed one off. */
export function retryEntry(entry) {
    dismissEntry(entry);
    if (entry.retry) entry.retry();
}

/** What the status says. */
export function writeQueueText() {
    const queue = state.writeQueue;
    if (pendingWrites()) {
        return `Saving ${Math.min(queue.batchSettled + 1, queue.batchTotal)} of ${queue.batchTotal}...`;
    }
    const failed = failedEntries().length;
    if (failed) return `${failed} failed`;
    const skipped = queue.batchSkipped;
    return skipped ? `All changes saved; ${skipped} skipped: damaged` : 'All changes saved';
}

const STATE_WORDS = { waiting: 'waiting', writing: 'writing', done: 'done', failed: 'failed' };

function entryRow(entry) {
    const said = entry.status === 'failed' && entry.error
        ? `failed: ${entry.error}`
        : entry.note ? `${STATE_WORDS[entry.status]}, ${entry.note}` : STATE_WORDS[entry.status];
    const row = buildElement('li', { className: 'write-queue-entry', data: { status: entry.status } }, [
        buildElement('span', { className: 'write-queue-label', text: entry.label }),
        buildElement('span', { className: 'write-queue-state', text: said }),
        buildElement('span', { className: 'write-queue-time', text: entry.at.toLocaleTimeString() }),
    ]);
    if (entry.status === 'failed') {
        const retry = buildElement('button', { className: 'write-queue-retry', text: 'Retry', attrs: { type: 'button' } });
        const dismiss = buildElement('button', { className: 'write-queue-dismiss', text: 'Dismiss', attrs: { type: 'button' } });
        retry.addEventListener('click', (e) => { e.stopPropagation(); retryEntry(entry); });
        dismiss.addEventListener('click', (e) => { e.stopPropagation(); dismissEntry(entry); });
        row.appendChild(buildElement('span', { className: 'write-queue-actions' }, [retry, dismiss]));
    }
    return row;
}

/** The status and its list, built once; a click opens it on a touch screen. */
function parts() {
    let status = writeQueueBox.querySelector('.write-queue-status');
    let list = writeQueueBox.querySelector('.write-queue-list');
    if (!status) {
        status = buildElement('button', {
            className: 'write-queue-status', attrs: { type: 'button', 'aria-expanded': 'false' },
        });
        list = buildElement('ul', { className: 'write-queue-list', attrs: { 'aria-label': 'Writes to photos' } });
        status.addEventListener('click', () => {
            state.writeQueue.open = !state.writeQueue.open;
            renderWriteQueue();
        });
        replaceContent(writeQueueBox, status, buildElement('div', { className: 'write-queue-panel' }, [list]));
    }
    return { status, list };
}

/** Draw the status and its list from state.writeQueue. */
export function renderWriteQueue() {
    if (!writeQueueBox) return;
    const queue = state.writeQueue;
    if (!queue.entries.length && !queue.batchTotal) {
        writeQueueBox.hidden = true;
        return;
    }
    writeQueueBox.hidden = false;
    const { status, list } = parts();
    const pending = pendingWrites();
    const mood = pending ? 'busy' : failedEntries().length ? 'failed' : 'idle';
    writeQueueBox.className = `write-queue write-queue-${mood}${queue.open ? ' open' : ''}`;
    status.textContent = writeQueueText();
    status.setAttribute('aria-expanded', queue.open ? 'true' : 'false');
    replaceContent(list, ...queue.entries.slice().reverse().map(entryRow));
}
