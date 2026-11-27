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
//
// Under the status, one line says what leaving the page now does (leaveSaid): a write sent the server finishes whatever
// the page does; a write queued behind it, and a tag change waiting to be taken into one (pending.js), is still in this
// page, and closing it loses them. The browser's own Leave prompt asks for the same ones (waitingInPage, edits.js).
import { buildElement, replaceContent } from './common/dom.js';
import { pathKey } from './common/paths.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import { libraryName } from './looking.js';
import { writeQueueBox } from './elements.js';

/**
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

/**
 * What a save's or a turn's reply says of the picture check (the server's `pictures_refused`: writes whose new file
 * would have changed the picture's bytes, thrown away and made again, DECISIONS 2026-11-03), as a sentence to follow
 * "Saved." or "Rotated.", or '' when none was.
 */
export function pictureCheckSaid(data) {
    if (!(data && data.pictures_refused)) return '';
    return " The first try would have changed the photo's picture, so it was thrown away and the photo written again; nothing was lost.";
}

/** Done entries the list keeps; a failed one stays until it is dismissed or retried. */
export const KEEP_DONE = 10;

/** "1 photo", "2,500 photos": the count the write line and the tag changes' questions say. */
export const photosWord = (n) => `${n.toLocaleString()} ${n === 1 ? 'photo' : 'photos'}`;

/**
 * A new entry, waiting: `label` says what it does; `photos()` gives the paths of the photos its write sends, asked while
 * it waits (waitingInPage) and while it is out (photosOut): a tag drain's chunk is chosen only when its turn comes.
 */
export function queueEntry(label, photos) {
    const queue = state.writeQueue;
    if (!pendingWrites()) {
        // A new run of writes: "Saving 1 of 1" again, not "Saving 7 of 7".
        queue.batchTotal = 0;
        queue.batchSettled = 0;
    }
    queue.batchTotal += 1;
    const entry = { id: queue.nextId++, label, photos, status: 'waiting', error: '', at: new Date() };
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
    if (status === 'done') {
        upper.navigatorCountsChanged();
        upper.selectionWritten();
    }
    const done = queue.entries.filter(e => e.status === 'done');
    if (done.length > KEEP_DONE) {
        const dropped = new Set(done.slice(0, done.length - KEEP_DONE));
        queue.entries = queue.entries.filter(e => !dropped.has(e));
    }
    renderWriteQueue();
}

/**
 * A write under way refused as changed on disk, nothing written, is read again to be sent once more: a save, until its
 * second send is answered (photo-write.js postOrSendAgain), and a tag chunk naming a photo by id that is no longer at its
 * path, until the read again gives its files back (pending.js sendChunk, readMoved). Meanwhile it is waiting in this page
 * (`on`), its row saying so; then it is under way again until it is marked done or failed.
 */
export function markBackInPage(entry, on) {
    entry.backInPage = on;
    renderWriteQueue();
}

/** How many writes are waiting or under way. */
function pendingWrites() {
    return state.writeQueue.entries.filter(e => e.status === 'waiting' || e.status === 'writing').length;
}

/**
 * The photos of the write under way, as it was queued naming them (queueEntry's `photos`: a single save's one, a tag
 * chunk's, a Time Shift's, an Apply All's): what a page going now leaves unknown (edits.js wireUnsavedEdits).
 */
export function photosOut() {
    return state.writeQueue.entries.filter(e => e.status === 'writing').flatMap(e => e.photos());
}

/**
 * How many photos have a write still in this page, not sent: one queued behind the write out (a save, Apply All, a
 * rename; a tag drain's names nothing until its turn), a save refused and being read again to be sent once more
 * (markBackInPage), and the tag changes waiting to be taken into a write (pending.js, `state.pending.files`, a drain
 * stopped by a failure's too). Closing the page loses them; a write sent is the server's.
 */
export function waitingInPage() {
    const keys = new Set(state.pending.files.keys());
    state.writeQueue.entries.filter(e => e.status === 'waiting' || (e.status === 'writing' && e.backInPage))
        .forEach(e => e.photos().forEach(path => keys.add(pathKey(path))));
    return keys.size;
}

/**
 * The write line's words of what leaving does now, the one place they are said: '' when nothing is sent or waiting. Sent,
 * the server finishes it if the page goes; waiting in this page, it is lost if the page goes (with a failure: until Retry).
 */
export function leaveSaid() {
    const sent = new Set(state.writeQueue.entries.filter(e => e.status === 'writing' && !e.backInPage)
        .flatMap(e => e.photos()).map(pathKey)).size;
    const waiting = waitingInPage();
    const them = (n) => (n === 1 ? 'it' : 'them');
    if (waiting && !pendingWrites()) return `${photosWord(waiting)} waiting in this page for Retry: lost if you leave`;
    const until = `stay until ${waiting === 1 ? 'it is' : 'they are'} sent`;
    if (waiting && sent) return `${photosWord(sent)} sent, ${waiting.toLocaleString()} waiting in this page: ${until}`;
    if (waiting) return `${photosWord(waiting)} waiting in this page: ${until}`;
    if (sent) return `${photosWord(sent)} sent: the server finishes ${them(sent)} even if you leave`;
    return '';
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
    return 'All changes saved';
}

const STATE_WORDS = { waiting: 'waiting', writing: 'writing', done: 'done', failed: 'failed' };

function entryRow(entry) {
    const said = entry.status === 'failed' && entry.error
        ? `failed: ${entry.error}`
        : entry.status === 'writing' && entry.backInPage ? 'writing, its file read again' : STATE_WORDS[entry.status];
    const row = buildElement('li', { className: 'write-queue-entry', data: { status: entry.status } }, [
        buildElement('span', { className: 'write-queue-label', text: entry.label }),
        buildElement('span', { className: 'write-queue-state', text: said }),
        buildElement('span', { className: 'write-queue-time', text: entry.at.toLocaleTimeString() }),
    ]);
    const buttons = [];
    if (entry.status === 'failed' && entry.retry) {
        const retry = buildElement('button', { className: 'write-queue-retry', text: 'Retry', attrs: { type: 'button' } });
        retry.addEventListener('click', (e) => { e.stopPropagation(); retryEntry(entry); });
        buttons.push(retry);
    }
    // A drain of tag changes (pending.js) is cancelled, not dismissed: dismissing would leave its files waiting.
    if (entry.cancel && (entry.status === 'failed' || entry.status === 'writing')) {
        const cancel = buildElement('button', { className: 'write-queue-cancel', text: 'Cancel', attrs: { type: 'button' } });
        cancel.addEventListener('click', (e) => {
            e.stopPropagation();
            entry.cancel();
            if (entry.status === 'failed') dismissEntry(entry);
        });
        buttons.push(cancel);
    } else if (entry.status === 'failed') {
        const dismiss = buildElement('button', { className: 'write-queue-dismiss', text: 'Dismiss', attrs: { type: 'button' } });
        dismiss.addEventListener('click', (e) => { e.stopPropagation(); dismissEntry(entry); });
        buttons.push(dismiss);
    }
    if (buttons.length) row.appendChild(buildElement('span', { className: 'write-queue-actions' }, buttons));
    return row;
}

/** The status, the line under it of what leaving does, and the list, built once; a click opens it on a touch screen. */
function parts() {
    let status = writeQueueBox.querySelector('.write-queue-status');
    let list = writeQueueBox.querySelector('.write-queue-list');
    let leave = writeQueueBox.querySelector('.write-queue-leave');
    if (!status) {
        status = buildElement('button', {
            className: 'write-queue-status', attrs: { type: 'button', 'aria-expanded': 'false' },
        });
        list = buildElement('ul', { className: 'write-queue-list', attrs: { 'aria-label': 'Writes to photos' } });
        status.addEventListener('click', () => {
            state.writeQueue.open = !state.writeQueue.open;
            if (!state.writeQueue.open) status.blur();
            renderWriteQueue();
        });
        // A click elsewhere or Escape closes the list: it must not stay up over the page.
        const close = () => {
            if (!state.writeQueue.open) return;
            state.writeQueue.open = false;
            renderWriteQueue();
        };
        document.addEventListener('pointerdown', (e) => { if (!writeQueueBox.contains(e.target)) close(); });
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
        leave = buildElement('div', { className: 'write-queue-leave', attrs: { role: 'status' } });
        replaceContent(writeQueueBox, status, leave, buildElement('div', { className: 'write-queue-panel' }, [list]));
    }
    return { status, list, leave };
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
    const { status, list, leave } = parts();
    const pending = pendingWrites();
    const mood = pending ? 'busy' : failedEntries().length ? 'failed' : 'idle';
    writeQueueBox.className = `write-queue write-queue-${mood}${queue.open ? ' open' : ''}`;
    status.textContent = writeQueueText();
    status.setAttribute('aria-expanded', queue.open ? 'true' : 'false');
    leave.textContent = leaveSaid();
    leave.hidden = !leave.textContent;
    replaceContent(list, ...queue.entries.slice().reverse().map(entryRow));
}
