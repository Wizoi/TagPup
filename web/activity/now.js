// Now: what runs this moment, a card a library -- its runs as a tree (each run under the run its row names as `parent`,
// this process's own index -- its queue's -- by its row's `parent` too, the folder watcher's whole sync one line with the library's
// running sync row, at the folder its walk is at), each with its phase in the server's words (`said`), how far, how
// long and since it last moved; its Suggest runs, the folders waiting and its batches of photo files -- and the server
// draining for an update. Asked every few seconds while the page is in view (/api/activity/now). Every time is by the
// server's clock (`at`). The folders are shown as the server names them: the page is served to this PC only (owner). A run whose last progress is older than STUCK_AFTER says so; one that has said nothing yet is
// not called stuck. A library whose jobs could not be read (/api/activity/jobs) says why on its card, and the others are
// drawn; one with nothing running says what finished in it last (its `last_finished`, /api/activity/jobs). A line read
// aloud (#now-summary) says what runs, in tens of percent, changed only when its words change; the cards are not read.
import { api } from './common/api.js';
import { buildElement } from './common/dom.js';
import { state } from './state.js';
import { asked } from './poll.js';
import { ago, duration, parseTime, secondsBetween } from './format.js';
import { badge, fill, none, runLinks, runTree } from './view.js';

/** The seconds without progress after which a running run is said to be stuck (docs/PLAN_ACTIVITY.md, "Design"). */
const STUCK_AFTER = 300;

/** Ask what runs now, and show it. */
export function loadNow() {
    return asked(api.site.json('/api/activity/now')).then(data => {
        if (!data || data.success === false) return data;
        state.now = data;
        const stamp = document.getElementById('updated');
        if (stamp) stamp.textContent = data.at ? `Updated ${data.at.slice(11)}` : '';
        renderNow();
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

/** A count as the owner reads one: 20,417. */
function count(value) {
    return Number(value || 0).toLocaleString('en-US');
}

/** How long a run has run and since it last moved, by the server's time `at`: { text, stuck }. Stuck only with a
 *  progress older than STUCK_AFTER; `saysNone` for a run that says no progress (a sync of one folder). */
function timing(at, started, progressed, saysNone = false) {
    const ran = secondsBetween(started, at);
    const still = secondsBetween(progressed, at);
    const stuck = !!progressed && still !== null && still > STUCK_AFTER;
    const parts = [];
    if (ran !== null) parts.push(`running ${duration(ran)}`);
    if (stuck) parts.push(`no progress for ${Math.floor(still / 60)} min; the share may be away`);
    else if (still !== null) parts.push(`last progress ${duration(still)} ago`);
    else if (!saysNone) parts.push('no progress said yet');
    return { text: parts.join(', '), stuck };
}

/** How far a run's counts say it got: "812 folders, 20,417 photo files", "120 of 800 photos", "2 of 9 folders baked". */
function howFar(counts) {
    const c = counts || {};
    const parts = [];
    if (c.folders_done !== undefined && c.folders !== undefined) {
        parts.push(`${count(c.folders_done)} of ${count(c.folders)} folders done`);
        if (c.written) parts.push(`${count(c.written)} written`);
        if (c.current) parts.push(`${count(c.current)} already current`);
        if (c.conflicts) parts.push(`${count(c.conflicts)} changed outside`);
    } else if (c.folders !== undefined && c.files !== undefined) {
        parts.push(`${count(c.folders)} folders, ${count(c.files)} photo files`);
    }
    if (c.done !== undefined && c.of !== undefined) parts.push(`${count(c.done)} of ${count(c.of)} photos`);
    return parts.join(', ');
}

/** A running row of the library (its `jobs`), as a line says it. */
function fromRow(row) {
    const progress = row.progress || {};
    const counts = progress.counts || {};
    return { label: row.job, said: progress.said || '', far: howFar(counts), percent: counts.percent,
             started: row.started, progressed: progress.progressed || null, run: row.run, logs: row.logs };
}

/** This process's own index (its queue's `indexing.running`), as a line says it. */
function fromQueue(running) {
    const more = running.folders > 1 ? ` and ${running.folders - 1} more folder(s)` : '';
    return { label: `index of ${running.name}${more}`, said: running.said || '', far: running.message || '', percent: running.percent || 0,
             started: running.started, progressed: running.progressed || null, run: running.run, logs: running.logs };
}

/** The folder watcher's sync with no row of its own to join: a sync of one folder (no run while it runs), or a whole
 *  one before its row is read. */
function fromWatcher(syncing) {
    const progress = syncing.progress || {};
    return { label: syncing.folder ? 'sync of one folder' : 'sync (folder watcher)', said: progress.phase || '',
             far: progress.phase ? howFar(progress) : '', detail: syncing.folder || '', started: syncing.started,
             progressed: progress.progressed || null, at: progress.folder || '', quiet: !!syncing.folder };
}

/** The library's runs as a tree: [{ item, children }], each run under the run its row names as `parent`. */
function treeOf(library, data) {
    const rows = library.jobs || [];
    const nodes = new Map(rows.map(row => [row.run_id, { row, item: fromRow(row), children: [] }]));
    const roots = [];
    for (const node of nodes.values()) {
        const parent = node.row.parent !== null && node.row.parent !== undefined ? nodes.get(node.row.parent) : null;
        (parent ? parent.children : roots).push(node);
    }
    const syncing = data.syncing;
    if (syncing && syncing.library === library.name) {
        // The watcher's whole sync is a run of the sync job: the same walk as the library's running sync row.
        const row = syncing.folder === null ? [...nodes.values()].find(node => node.row.job === 'sync') : null;
        if (row) Object.assign(row.item, { label: 'sync (folder watcher)', at: (syncing.progress || {}).folder || '' });
        else roots.unshift({ item: fromWatcher(syncing), children: [] });
    }
    const running = (library.indexing || {}).running;
    if (running) {
        const parent = running.parent !== null && running.parent !== undefined ? nodes.get(running.parent) : null;
        (parent ? parent.children : roots).push({ item: fromQueue(running), children: [] });
    }
    return roots;
}

/** One run's line, and under it the folder its walk is at. */
function runLine(item, at) {
    const times = timing(at, item.started, item.progressed, item.quiet);
    const hasPercent = item.percent !== undefined && item.percent !== null;
    const line = buildElement('div', { className: `activity run-line${times.stuck ? ' stuck' : ''}` }, [
        badge(times.stuck ? 'stuck' : 'running', times.stuck ? 'bad' : 'busy'),
        buildElement('span', { className: 'what', text: item.label }),
        item.said ? buildElement('span', { className: 'phase', text: item.said }) : null,
        item.detail ? buildElement('span', { className: 'detail path', text: item.detail }) : null,
        item.far ? buildElement('span', { className: 'detail', text: item.far }) : null,
        hasPercent ? buildElement('progress', { attrs: { max: 100, value: item.percent,
                                                         'aria-valuetext': `${item.percent}%${item.said ? `, ${item.said}` : ''}` },
                                                title: `${item.percent}%` }) : null,
        hasPercent ? buildElement('span', { className: 'percent', text: `${item.percent}%` }) : null,
        buildElement('span', { className: times.stuck ? 'when error' : 'when', text: times.text }),
        ...runLinks(item.run, item.logs),
    ]);
    const where = item.at ? buildElement('div', { className: 'activity at' }, [
        buildElement('span', { className: 'detail path', text: `at ${item.at}` })]) : null;
    return [line, where];
}

function treeList(nodes, at) {
    return runTree(nodes, node => runLine(node.item, at), node => ({ className: 'run', data: { label: node.item.label } }));
}

/** The folders waiting for this process's index, and the library's Suggest runs. */
function waitingLines(library) {
    const lines = [];
    const queued = (library.indexing || {}).queued || [];
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
    return lines;
}

/** The library's block of /api/activity/jobs, or null before it is read. */
function listed(name) {
    return ((state.jobs && state.jobs.libraries) || []).find(library => library.name === name) || null;
}

/** "Nothing is running.", and what finished in the library last, as the server read it of that library alone. */
function nothingRunning(name) {
    const last = (listed(name) || {}).last_finished;
    const at = parseTime(state.now && state.now.at);
    if (!last || !at) return 'Nothing is running.';
    return `Nothing is running. The last, ${last.what}, finished ${ago(last.finished, at.getTime())}.`;
}

/** Why the library's jobs could not be read (/api/activity/jobs), or ''. */
function unreadable(name) {
    return (listed(name) || {}).error || '';
}

function libraryCard(library, roots, at) {
    const error = unreadable(library.name);
    const others = [...waitingLines(library), ...batchLines(library)];
    return buildElement('article', { className: 'card now-library', data: { library: library.name } }, [
        buildElement('h3', { text: library.name }),
        error ? buildElement('p', { className: 'activity unreadable' }, [
            badge('unreadable', 'bad'),
            buildElement('span', { className: 'error', text: `Could not read this library: ${error}` })]) : null,
        roots.length ? treeList(roots, at) : null,
        ...others,
        roots.length || others.length || error ? null : none(nothingRunning(library.name)),
    ]);
}

function serverLines(data) {
    const server = data.server || {};
    if (server.taking_work !== false) return [];
    return [buildElement('div', { className: 'activity' }, [
        badge('draining', 'busy'),
        buildElement('span', { className: 'detail', text: 'The server takes no new work while it moves onto a new version.' }),
    ])];
}

/** Each run of the trees, depth first. */
function flat(nodes) {
    return nodes.flatMap(node => [node.item, ...flat(node.children)]);
}

/** What runs, said in words that change only when a phase does, a run becomes stuck or a percent passes a ten. */
export function summaryOf(data) {
    const parts = [];
    for (const library of data.libraries || []) {
        for (const item of flat(treeOf(library, data))) {
            const tens = item.percent !== undefined && item.percent !== null ? `, ${Math.floor(item.percent / 10) * 10}%` : '';
            const stuck = timing(data.at, item.started, item.progressed).stuck ? ', stuck' : '';
            parts.push(`${library.name}: ${item.label}${item.said ? `, ${item.said}` : ''}${tens}${stuck}`);
        }
    }
    return parts.length ? `${parts.join('. ')}.` : 'Nothing is running.';
}

/** The line read aloud, changed only when its words change. */
function say(data) {
    const line = document.getElementById('now-summary');
    const text = summaryOf(data);
    if (line && line.textContent !== text) line.textContent = text;
}

/** Show what state.now holds: a card a library, the server's line first when it drains. */
export function renderNow() {
    const data = state.now;
    if (!data) return;
    const general = serverLines(data);
    const blocks = general.length ? [buildElement('div', { className: 'card' }, general)] : [];
    for (const library of data.libraries || []) blocks.push(libraryCard(library, treeOf(library, data), data.at));
    fill(document.getElementById('now-body'), ...blocks);
    say(data);
}
