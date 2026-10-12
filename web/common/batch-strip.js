/**
 * The one strip for the batches of photo files, on both pages (docs/ARCHITECTURE.md, "One batch model"). A batch is more than 25 photos
 * written as one journaled change, one at a time in a process; the server lists those running or waiting, in any process
 * (GET /api/activity/batches), and stops one of its own at its next chunk (POST /api/activity/batches/cancel).
 *
 * The strip asks once when the page opens, so a reload shows the batch that is still going, and every POLL_MS while the page has a write
 * in flight (api.js raises `tagpup:writes`) or the server lists a batch; it stops when neither. It draws what the server says: the
 * operation, the photos handled of the total, the time left from when it started and what it has done, Cancel for a batch of this
 * server, a waiting one with how many are ahead. A page's own request still awaits its reply and hands the result sentence to `showEnded`;
 * a batch that leaves the list with no such sentence (another tab's, a reload's) ends in one the strip makes from the last row it saw.
 * When the server cannot be asked the strip says it cannot tell: it never claims a write finished because the list was not read.
 */
import { api, libraryIn } from './api.js';
import { buildElement, replaceContent } from './dom.js';
import { openHistory } from './history-dialog.js';

export const POLL_MS = 1500;
/** Errors listed with their file's name; the rest are counted. The server names 50 as well. */
export const MOST_ERRORS_SHOWN = 50;
export const CANNOT_TELL = 'TagPup cannot tell how far the write has got: the server did not answer. It may still be going on; '
    + "the page's own request says when it is done.";

/** `1 photo`, `3,412 photos`. */
export function photosOf(count) {
    return `${count.toLocaleString()} ${count === 1 ? 'photo' : 'photos'}`;
}

/** How long `seconds` is, roughly: `about 3 minutes`, `less than a minute`, `about 1 hour 5 minutes`. */
export function howLong(seconds) {
    const minutes = Math.round(seconds / 60);
    if (minutes < 1) return 'less than a minute';
    if (minutes < 60) return `about ${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`;
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    const hourText = `${hours} ${hours === 1 ? 'hour' : 'hours'}`;
    return rest ? `about ${hourText} ${rest} ${rest === 1 ? 'minute' : 'minutes'}` : `about ${hourText}`;
}

/** The sentence an edit ends in: `what` was done, what was left out, and the errors, counted. */
export function endedSentence(what, result) {
    const parts = [what];
    const note = (key, words) => { if (result[key]) parts.push(`${result[key].toLocaleString()} ${words}`); };
    const skipped = (key, why) => note(key, `${why} ${result[key] === 1 ? 'was' : 'were'} skipped`);
    skipped('skipped_missing', 'missing on disk');
    skipped('skipped_damaged', 'damaged');
    note('unchanged', `already ${result.unchanged === 1 ? 'was' : 'were'} as asked`);
    const errors = result.error_count || 0;
    parts.push(`${errors.toLocaleString()} ${errors === 1 ? 'error' : 'errors'}`);
    return `${parts.join('; ')}.`;
}

/**
 * What a batch that left the list ended in, from the last row seen of it. The row is as old as the last poll, so the counts are
 * said as seen then; the change (History) says what was written.
 */
function batchLeftSentence(row) {
    const seenAt = `${(row.done + row.conflict).toLocaleString()} of ${photosOf(row.photos)} handled when last seen`;
    const where = row.id ? ` Change ${row.id} in History says what it wrote and can undo it.` : '';
    if (row.state !== 'stopping') return `${row.operation} has ended (${seenAt}).${where}`;
    return row.started === null || row.started === undefined
        ? `${row.operation} was cancelled before it began: nothing was written.`
        : `${row.operation} was stopped as asked (${seenAt}).${where}`;
}

const batchKey = row => (row.ticket === null || row.ticket === undefined ? `c${row.id}` : `t${row.ticket}`);
const sameBatchLibrary = (a, b) => String(a).toLowerCase() === String(b).toLowerCase();

/**
 * The strip's one state. `seen` is the rows listed at the last poll by batchKey; `ended` the one sentence a write ends in
 * ({ text, problem, errors, errorCount, byPage }); `note` what a Cancel answered; `unknown` that the list could not be read;
 * `writes` how many write requests of the page are out; `mine` the library the rows are of.
 */
const batchStrip = {
    section: null, live: null, options: {}, seen: new Map(), rows: [], ended: null, note: '', unknown: false, timer: null, writes: 0, mine: '',
};

const stripLibrary = () => (batchStrip.options.library ? batchStrip.options.library() : libraryIn(location.pathname));

function sayEnded(words) {
    batchStrip.live.textContent = '';
    batchStrip.live.textContent = words;
}

function batchTimeLeft(row) {
    const handled = row.done + row.conflict;
    if (!row.started || !handled || handled >= row.photos) return '';
    return `, ${howLong((Date.now() / 1000 - row.started) / handled * (row.photos - handled))} left`;
}

function batchRow(row) {
    const queued = row.state === 'queued';
    const handled = row.done + row.conflict;
    const lead = queued ? `Waiting, ${row.position} ahead: ` : row.state === 'stopping' ? 'Stopping after this chunk: ' : '';
    const title = `${lead}writing ${photosOf(row.photos)} (${row.operation})${row.here ? '' : ', run by another program'}`;
    const parts = [buildElement('strong', { text: title })];
    if (!queued) {
        parts.push(buildElement('progress', { attrs: { max: row.photos, value: handled, 'aria-label': row.operation } }));
        parts.push(buildElement('span', { text: `${handled.toLocaleString()} of ${row.photos.toLocaleString()}${batchTimeLeft(row)}` }));
    }
    if (row.ticket !== null && row.ticket !== undefined && row.state !== 'stopping') {
        const button = buildElement('button', { className: 'btn btn-secondary btn-sm', text: 'Cancel', attrs: { type: 'button' } });
        button.addEventListener('click', () => cancelBatch(row, button));
        parts.push(button);
    }
    return buildElement('div', { className: 'batch-strip-row' }, parts);
}

function endedBlock(ended) {
    const button = (text, onClick) => {
        const each = buildElement('button', { className: 'btn btn-secondary btn-sm', text, attrs: { type: 'button' } });
        each.addEventListener('click', onClick);
        return each;
    };
    const listed = (ended.errors || []).slice(0, MOST_ERRORS_SHOWN);
    const total = ended.errorCount || listed.length;
    const more = total - listed.length;
    const errors = listed.length ? buildElement('details', {}, [
        buildElement('summary', { text: `${total.toLocaleString()} errors${more > 0 ? `: the first ${listed.length} are listed` : ''}` }),
        buildElement('ul', { className: 'batch-strip-errors' }, [
            ...listed.map(each => buildElement('li', {}, [buildElement('strong', { text: String(each.name || `photo ${each.id}`) }), ` — ${each.why}`])),
            more > 0 ? buildElement('li', { text: `${more.toLocaleString()} more, not listed` }) : null,
        ]),
    ]) : null;
    return buildElement('div', { className: ended.problem ? 'batch-strip-problem' : '' }, [
        buildElement('div', { className: 'batch-strip-row' }, [
            buildElement('span', { className: 'batch-strip-sentence', text: ended.text }),
            button('History', () => openHistory()),
            button('Dismiss', dismissEnded),
        ]),
        errors,
    ]);
}

function drawBatches() {
    const state = batchStrip;
    replaceContent(state.section, ...state.rows.map(batchRow),
        state.note ? buildElement('div', { text: state.note }) : null,
        state.unknown && (state.writes || state.seen.size) ? buildElement('div', { className: 'batch-strip-problem', text: CANNOT_TELL }) : null,
        state.ended ? endedBlock(state.ended) : null);
    state.section.hidden = !state.section.childNodes.length;
}

function pollAgain() {
    const state = batchStrip;
    clearTimeout(state.timer);
    state.timer = state.writes || state.seen.size ? setTimeout(pollBatches, POLL_MS) : null;
}

/** Ask the server which batches run or wait, and draw this page's library's. */
async function pollBatches() {
    const state = batchStrip;
    clearTimeout(state.timer);
    state.timer = null;
    const asked = stripLibrary();
    if (!asked) return;        // the picker page has no library, so nothing to write
    if (!sameBatchLibrary(asked, state.mine)) Object.assign(state, { mine: asked, seen: new Map(), rows: [], ended: null, note: '' });
    let listed;
    try {
        listed = (await api.site.json('/api/activity/batches')).batches;      // a 403 or 404 answers {error}: no list
        if (!Array.isArray(listed)) throw new Error('no list');
    } catch {
        state.unknown = true;      // nothing is claimed: the batches seen stay as they were
        drawBatches();
        pollAgain();
        return;
    }
    state.unknown = false;
    if (!sameBatchLibrary(asked, stripLibrary())) return pollBatches();       // the library changed while asking: those rows are another's
    state.rows = listed.filter(row => sameBatchLibrary(row.library, state.mine));
    const now = new Map(state.rows.map(row => [batchKey(row), row]));
    for (const [key, row] of state.seen) {
        if (now.has(key) || (state.ended && state.ended.byPage)) continue;
        state.ended = { text: batchLeftSentence(row), problem: false };
        sayEnded(state.ended.text);
    }
    state.seen = now;
    if (!state.rows.length) state.note = '';
    drawBatches();
    pollAgain();
}

async function cancelBatch(row, button) {
    button.disabled = true;
    batchStrip.note = '';
    try {
        const answer = await api.site.json('/api/activity/batches/cancel', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ library: row.library, ticket: row.ticket }),
        });
        if (!answer.success) batchStrip.note = answer.error || 'The server did not stop it.';
    } catch (err) {
        batchStrip.note = `Cancel was not sent (${err.message}).`;
    }
    return pollBatches();
}

function dismissEnded() {
    const inside = batchStrip.section.contains(document.activeElement);
    batchStrip.ended = null;
    drawBatches();
    if (inside && batchStrip.options.restoreFocus) batchStrip.options.restoreFocus();
}

/** The page's own request has its reply: `text` is its sentence, `problem` when it is not a success; `errors` the first of them. */
export function showEnded({ text, problem = false, errors = [], errorCount = 0 }) {
    batchStrip.ended = { text, problem, errors, errorCount, byPage: true };
    drawBatches();
    sayEnded(text);
}

/** Start the strip: `library()` names the page's library (the URL's by default), `restoreFocus()` is where Dismiss leaves the focus. */
export function wireBatchStrip(options = {}) {
    const state = batchStrip;
    state.options = options;
    state.mine = stripLibrary();
    state.section = buildElement('section', { className: 'batch-strip', attrs: { 'aria-label': 'Writes to photo files', hidden: true } });
    state.live = buildElement('div', { className: 'batch-strip-live', attrs: { role: 'status', 'aria-live': 'polite' } });
    document.body.append(state.section, state.live);
    document.addEventListener('tagpup:writes', (event) => {
        const was = state.writes;
        state.writes = event.detail.writes;
        if (state.writes && !was) state.unknown = false;
        if (state.writes && !was && !state.timer) state.timer = setTimeout(pollBatches, POLL_MS);
        if (!state.writes && was && state.seen.size) pollBatches();       // it may have ended: ask now rather than in a moment
    });
    return pollBatches();
}
