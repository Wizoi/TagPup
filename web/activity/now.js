// Now: what runs this moment -- each library's indexing (the folders being indexed and how
// far, those waiting), its Suggest runs and a recurring job running, the folder watcher's
// sync, and the server (an update waiting, the server draining, an update
// refused). Asked every few seconds while the page is in view (/api/activity/now). How long a run has run and
// since it last moved are by the server's clock (`at`). A line read aloud (#now-summary) says what runs and how far,
// in tens of percent, and is changed only when that changes: every poll would be read out otherwise.
import { api } from './common/api.js';
import { buildElement } from './common/dom.js';
import { state } from './state.js';
import { ago, duration, secondsBetween } from './format.js';
import { badge, fill, none, runLinks } from './view.js';

/** Ask what runs now, and show it. */
export function loadNow() {
    return api.site.json('/api/activity/now').then(data => {
        if (!data || data.success === false) return data;
        state.now = data;
        renderNow();
        const stamp = document.getElementById('updated');
        if (stamp) stamp.textContent = data.at ? `Updated ${data.at.slice(11)}` : '';
        return data;
    });
}

/** Stop a batch of photo files of this server at its chunk (or never start it, if it waits). */
function cancelBatch(library, batch) {
    return api.site.json('/api/activity/batches/cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ library: library.name, ticket: batch.ticket }),
    }).then(() => loadNow());
}

/** A batch of photo files, running or waiting: how far, and Cancel when this server runs it. */
function batchLines(library) {
    return (library.batches || []).map(batch => {
        const detail = batch.state === 'queued'
            ? `${batch.photos} photos, ${batch.position} ahead; a restart drops it before anything is written`
            : `${batch.done} of ${batch.photos} photos written${batch.conflict ? `, ${batch.conflict} changed outside` : ''}`
              + `${batch.state === 'stopping' ? ', stopping after this chunk' : ''}${batch.here ? '' : ' (another program runs it)'}`;
        const button = batch.ticket === null ? null : buildElement('button', {
            className: 'btn cancel-batch', text: 'Cancel', attrs: { type: 'button', disabled: batch.state === 'stopping' } });
        if (button) button.addEventListener('click', () => { if (!button.disabled) { button.disabled = true; cancelBatch(library, batch); } });
        return buildElement('div', { className: 'activity' }, [
            badge(batch.state, batch.state === 'queued' ? 'quiet' : 'busy'),
            buildElement('span', { className: 'what', text: batch.operation }),
            buildElement('span', { className: 'detail', text: detail }),
            button,
        ]);
    });
}

/** "running 5 min 3 s, last progress 4 s ago": how long a run has run and since it last moved, by the server's clock. */
function timing(started, progressed) {
    const at = state.now && state.now.at;
    const ran = secondsBetween(started, at);
    const still = secondsBetween(progressed, at);
    const parts = [];
    if (ran !== null) parts.push(`running ${duration(ran)}`);
    if (still !== null) parts.push(`last progress ${duration(still)} ago`);
    return parts.join(', ');
}

/** The phase a message of the indexer names: its words before the first number or colon ("Bake folder", "Metadata"). */
function phaseOf(message) {
    return String(message || '').split(/[:\d]/)[0].replace(/\.+$/, '').trim();
}

function indexingLines(library) {
    const lines = [];
    const indexing = library.indexing || {};
    const running = indexing.running;
    if (running) {
        const more = running.folders > 1 ? ` and ${running.folders - 1} more folder(s)` : '';
        const percent = running.percent || 0;
        lines.push(buildElement('div', { className: 'activity' }, [
            badge('indexing', 'busy'),
            buildElement('span', { className: 'what', text: `${running.name}${more}` }),
            buildElement('progress', { attrs: { max: 100, value: percent, 'aria-valuetext':
                                                `${percent}%${running.message ? `, ${phaseOf(running.message)}` : ''}` },
                                       title: `${percent}%` }),
            buildElement('span', { className: 'percent', text: `${percent}%` }),
            buildElement('span', { className: 'detail', text: running.message || '' }),
            buildElement('span', { className: 'when', text: timing(running.started, running.progressed) }),
            ...runLinks(running.run, running.logs),
        ]));
    }
    const queued = indexing.queued || [];
    if (queued.length) {
        lines.push(buildElement('div', { className: 'activity' }, [
            badge(`${queued.length} waiting`, 'quiet'),
            buildElement('span', { className: 'detail',
                                   text: queued.map(job => job.folders > 1 ? `${job.name} (+${job.folders - 1})` : job.name)
                                       .join(', ') }),
        ]));
    }
    for (const run of library.suggesting || []) {
        lines.push(buildElement('div', { className: 'activity' }, [
            badge('Suggest', 'busy'),
            buildElement('span', { className: 'what', text: run.folder }),
            // What it waits for, when it waits: the folder's index, or the graphics card and who has it.
            buildElement('span', { className: 'detail',
                                   text: `${run.status}, ${run.completed} of ${run.total}${run.message ? `: ${run.message}` : ''}` }),
        ]));
    }
    for (const job of library.jobs || []) {
        lines.push(buildElement('div', { className: 'activity' }, [
            badge('job', 'busy'),
            buildElement('span', { className: 'what', text: job.job }),
            buildElement('span', { className: 'detail', text: `started ${ago(job.started)}` }),
            ...runLinks(job.run, job.logs),
        ]));
    }
    lines.push(...batchLines(library));
    return lines;
}

function serverLines(data) {
    const lines = [];
    const server = data.server || {};
    if (server.taking_work === false) {
        lines.push(buildElement('div', { className: 'activity' }, [
            badge('draining', 'busy'),
            buildElement('span', { className: 'detail', text: 'The server takes no new work while it moves onto a new version.' }),
        ]));
    }
    if (data.syncing) {
        const progress = data.syncing.progress || {};
        const walked = progress.phase
            ? `${progress.phase}: ${progress.folders} folder(s), ${progress.files} photo file(s)` : 'starting';
        lines.push(buildElement('div', { className: 'activity' }, [
            badge('syncing', 'busy'),
            buildElement('span', { className: 'what', text: data.syncing.library }),
            buildElement('span', { className: 'detail', text: data.syncing.folder || 'every folder' }),
            buildElement('span', { className: 'phase', text: walked }),
            buildElement('span', { className: 'when', text: timing(data.syncing.started, progress.progressed) }),
        ]));
        if (progress.folder) {
            lines.push(buildElement('div', { className: 'activity at' }, [
                buildElement('span', { className: 'detail', text: `at ${progress.folder}` }),
            ]));
        }
    }
    return lines;
}

/** What runs, said in words that change only when the phase does or the percent passes a ten. */
export function summaryOf(data) {
    const parts = [];
    if (data.syncing) {
        const phase = (data.syncing.progress || {}).phase;
        parts.push(`${data.syncing.library}: syncing${phase ? `, ${phase}` : ''}`);
    }
    for (const library of data.libraries || []) {
        const running = (library.indexing || {}).running;
        if (!running) continue;
        const phase = phaseOf(running.message);
        const tens = Math.floor((running.percent || 0) / 10) * 10;
        parts.push(`${library.name}: indexing ${running.name}${phase ? `, ${phase}` : ''}, ${tens}%`);
    }
    return parts.length ? `${parts.join('. ')}.` : 'Nothing is running.';
}

/** The line read aloud, changed only when its words change. */
function say(data) {
    const line = document.getElementById('now-summary');
    const text = summaryOf(data);
    if (line && line.textContent !== text) line.textContent = text;
}

/** Show what state.now holds. */
export function renderNow() {
    const data = state.now;
    if (!data) return;
    const blocks = [];
    const general = serverLines(data);
    if (general.length) blocks.push(buildElement('div', { className: 'card' }, general));
    for (const library of data.libraries || []) {
        const lines = indexingLines(library);
        if (!lines.length) continue;
        blocks.push(buildElement('div', { className: 'card' }, [
            buildElement('h3', { text: library.name }), ...lines]));
    }
    fill(document.getElementById('now-body'), ...(blocks.length ? blocks : [none('Nothing is running.')]));
    say(data);
}
