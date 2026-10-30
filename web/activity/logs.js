// Logs: the logs in data/logs in two lists, as the server groups them (`group`): the programs' own (the web server's,
// the MCP server's, the console's: a short list), and the runs' (each index's, each failed test run's), which keep
// coming and are kept the last few of each library and kind -- those filtered by library and by kind, here, from the
// one listing; each said in words (what ran, the library, when, its size), the file's name only its title. The log
// chosen, its newest records first as time, level, logger and message; filtered by level (INFO and worse unless
// asked), by text and by run; Follow asks for what was written since, every few seconds; Older reads further back;
// Raw and Download are the file itself. The server reads each log from its end and never whole
// (/api/activity/logs/<name>): the page says where it read from and to, by offset. A log that is not there (removed
// with the older runs', or not written yet), found so by a read, Older or Follow, is said in the server's sentence
// where its lines would be, nothing more read of it and Raw and Download not offered. A test run belongs to no library:
// the Library filter is set aside while the kind chosen is one of no library's.
import { api } from './common/api.js';
import { buildElement, replaceContent } from './common/dom.js';
import { state } from './state.js';
import { asked } from './poll.js';
import { bytes, clock } from './format.js';
import { foldLine, unfold, unfolded } from './fold.js';

/** How many records a read asks for. */
const LOG_PAGE = 200;

/** The most records the page keeps shown: following a busy log keeps the newest. */
const LOG_KEEP = 2000;

const LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];

/** A run's kind (tagpup.logs.group_of) in words, for the Kind filter; a kind not here is shown as the server sends it. */
const KIND_WORDS = { indexer: 'Indexer runs', run_tests: 'Test runs' };

const RUN = 'run';

function logAddress(name, what = '') {
    return `/api/activity/logs/${encodeURIComponent(name)}${what}`;
}

/** The query a read of the chosen log sends. */
function logQuery(extra = {}) {
    const params = new URLSearchParams();
    params.set('limit', String(LOG_PAGE));
    if (state.logs.level) params.set('level', state.logs.level);
    if (state.logs.text) params.set('text', state.logs.text);
    if (state.logs.run) params.set('run', state.logs.run);
    for (const [key, value] of Object.entries(extra)) params.set(key, String(value));
    return `?${params.toString()}`;
}

/** Read the list of logs, and draw the two lists. */
export function loadLogFiles() {
    return api.site.json('/api/activity/logs').then(data => {
        state.logs.files = (data && data.logs) || [];
        state.logs.server = (data && data.server) || null;
        state.logs.keepRuns = (data && data.keep_runs) || null;
        if (!state.logs.name || !state.logs.files.some(file => file.name === state.logs.name)) {
            const web = state.logs.files.find(file => file.name === state.logs.server);
            state.logs.name = web ? web.name : (state.logs.files[0] || {}).name || null;
        }
        renderLogFiles();
        return data;
    });
}

/** A run's log is one the server groups so; any other (a program's own) is in the short list. */
const isRun = file => file.group === RUN;

const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;

function logButton(file, label) {
    const chosen = file.name === state.logs.name;
    const button = buildElement('button', {
        className: chosen ? 'log-file chosen' : 'log-file', title: file.name,
        attrs: { type: 'button', 'aria-pressed': chosen ? 'true' : 'false' },
        data: { log: file.name },
    }, [buildElement('span', { text: label }), buildElement('small', { text: `last written ${clock(file.modified)}, ${bytes(file.bytes)}` })]);
    button.addEventListener('click', () => chooseLog(file.name));
    return button;
}

/** The options of a filter: All, then each value the runs' logs hold, in words; the value chosen kept if still there. */
function fillFilter(id, values, words, chosen) {
    const select = document.getElementById(id);
    if (!select) return chosen;
    const kept = values.includes(chosen) ? chosen : '';
    replaceContent(select, buildElement('option', { text: 'All', attrs: { value: '' } }),
                   ...values.map(value => buildElement('option', { text: words(value), attrs: { value } })));
    select.value = kept;
    return kept;
}

/** The log whose button has the keyboard's focus, or null: drawn again, its new button gets it back. */
function focusedLog() {
    const active = document.activeElement;
    return active && active.classList && active.classList.contains('log-file') ? active.dataset.log : null;
}

function refocus(name) {
    if (!name) return;
    const button = [...document.querySelectorAll('button.log-file')].find(each => each.dataset.log === name);
    if (button) button.focus();
}

function renderLogFiles() {
    const focused = focusedLog();
    const files = state.logs.files;
    const system = files.filter(file => !isRun(file));
    const ran = files.filter(isRun);
    const own = document.getElementById('log-system');
    if (own) replaceContent(own, ...(system.length ? system.map(file => logButton(file, file.source))
        : [buildElement('p', { className: 'none', text: 'No program has written a log yet.' })]));
    const libraries = [...new Set(ran.map(file => file.library).filter(Boolean))].sort();
    const kinds = [...new Set(ran.map(file => file.kind).filter(Boolean))].sort();
    state.logs.library = fillFilter('log-library', libraries, value => value, state.logs.library);
    state.logs.kind = fillFilter('log-kind', kinds, value => KIND_WORDS[value] || value, state.logs.kind);
    renderRunFiles();
    const kept = document.getElementById('log-kept');
    if (kept) kept.textContent = state.logs.keepRuns
        ? `The last ${state.logs.keepRuns} runs' logs of each library and kind are kept.` : '';
    foldLine('logs', files.length
        ? `${plural(system.length, 'system log', 'system logs')}; ${plural(ran.length, "run's log", "runs' logs")} kept`
        : 'No logs yet.');
    renderFileLinks();
    refocus(focused);
}

/** Raw and Download: the chosen log's file, or not offered while there is none (no log chosen, or it is not there). */
function renderFileLinks() {
    for (const [id, what] of [['log-raw', '/raw'], ['log-download', '/download']]) {
        const link = document.getElementById(id);
        if (!link) continue;
        if (state.logs.name && !state.logs.why) {
            link.href = api.site.url(logAddress(state.logs.name, what));
            link.removeAttribute('aria-disabled');
        } else {
            link.removeAttribute('href');
            link.setAttribute('aria-disabled', 'true');
        }
    }
}

/** The runs' logs of the library and kind chosen, or why there are none. */
function renderRunFiles() {
    const list = document.getElementById('log-runs');
    if (!list) return;
    const { kind } = state.logs;
    const ran = state.logs.files.filter(isRun);
    // A kind whose runs belong to no library (a test run's): the Library filter is set aside, not matched by nothing.
    const libraryless = !!kind && ran.filter(file => file.kind === kind).every(file => !file.library);
    const filter = document.getElementById('log-library');
    if (filter) filter.disabled = libraryless;
    const library = libraryless ? '' : state.logs.library;
    const shown = ran.filter(file => (!library || file.library === library) && (!kind || file.kind === kind));
    const none = ran.length
        ? `No ${kind ? (KIND_WORDS[kind] || kind).toLowerCase() : 'runs'}' logs of ${library || 'any library'} are kept.`
        : 'No run has written a log yet.';
    replaceContent(list, ...(shown.length ? shown.map(file => logButton(file, file.source))
        : [buildElement('p', { className: 'none', text: none })]));
}

/** Show the log `name` from its end, with the filters as they are. */
export function chooseLog(name) {
    state.logs.name = name;
    state.logs.why = '';
    renderLogFiles();
    return readLog();
}

/** The chosen log is not there (removed with the older runs', or not written yet), or was refused: said where its
 *  lines would be, nothing followed or read further back, Raw and Download not offered. */
function notThere(data) {
    Object.assign(state.logs, { records: [], start: null, end: null, complete: true,
                                why: (data && data.error) || 'Could not read the log.' });
    renderLogRecords();
}

/** Read the chosen log from its end again: the filters changed, or another log was chosen. */
export function readLog() {
    if (!state.logs.name) {
        renderLogRecords();
        return Promise.resolve(null);
    }
    const read = ++state.logs.asked;
    return api.site.json(logAddress(state.logs.name) + logQuery()).then(data => {
        if (read !== state.logs.asked) return null;   // a newer read was asked meanwhile
        if (!data || data.success === false) {
            notThere(data);
            return data;
        }
        state.logs.records = data.records || [];
        state.logs.start = data.start;
        state.logs.end = data.end;
        state.logs.complete = !!data.complete;
        state.logs.why = '';
        renderLogRecords();
        return data;
    });
}

/** Older: the records before the oldest shown. */
export function readOlder() {
    if (!state.logs.name || state.logs.complete || state.logs.start === null) return Promise.resolve(null);
    const read = ++state.logs.asked;
    return api.site.json(logAddress(state.logs.name) + logQuery({ before: state.logs.start })).then(data => {
        if (read !== state.logs.asked) return null;
        if (!data || data.success === false) {
            notThere(data);
            return null;
        }
        state.logs.records = state.logs.records.concat(data.records || []);
        state.logs.start = data.start;
        state.logs.complete = !!data.complete;
        renderLogRecords();
        return data;
    });
}

/** Follow: what was written since the last read, newest first above what is shown; nothing read while Logs is folded. */
export function followLog() {
    if (!state.logs.follow || !unfolded('logs') || !state.logs.name || state.logs.end === null) return Promise.resolve(null);
    const reading = state.logs.asked;
    return asked(api.site.json(logAddress(state.logs.name) + logQuery({ after: state.logs.end }))).then(data => {
        if (reading !== state.logs.asked) return null;
        if (!data || data.success === false) {
            // Removed meanwhile (a server's start keeps the last runs' logs): said, and not followed on; the server
            // answered, so the poll has not failed.
            notThere(data);
            return null;
        }
        if (data.rotated) {
            // The file was rotated: what it holds now is all new.
            state.logs.records = data.records || [];
            state.logs.start = data.start;
        } else if ((data.records || []).length) {
            state.logs.records = (data.records || []).concat(state.logs.records).slice(0, LOG_KEEP);
        }
        state.logs.end = data.end;
        if ((data.records || []).length || data.rotated) renderLogRecords();
        return data;
    });
}

/** "Logs for this run": the log `name` (as the server named it), only the lines of the run `run`, INFO and worse; Logs unfolded. */
export function showRunLogs(run, name) {
    state.logs.run = run;
    state.logs.level = 'INFO';
    const level = document.getElementById('log-level');
    if (level) level.value = 'INFO';
    unfold('logs');
    const section = document.getElementById('logs');
    if (section && typeof section.scrollIntoView === 'function') section.scrollIntoView();
    // The listing again first: a run newer than the page has a log the first listing did not hold. A click (or any
    // read) asked meanwhile is newer than this one, and is the one shown.
    const asking = ++state.logs.asked;
    return loadLogFiles().then(() => (asking === state.logs.asked ? chooseLog(name || state.logs.server) : null));
}

function renderRecord(record) {
    const level = record.level || '';
    return buildElement('tr', { className: `record level-${level.toLowerCase() || 'none'}` }, [
        buildElement('td', { className: 'when', text: record.time || '' }),
        buildElement('td', { className: 'level', text: level }),
        buildElement('td', { className: 'logger', text: record.logger || '', title: record.thread || '' }),
        buildElement('td', { className: 'message' }, [buildElement('pre', { text: record.message })]),
    ]);
}

/** Show the records read, and what filters them. */
export function renderLogRecords() {
    const table = document.getElementById('log-records');
    if (table) {
        const rows = state.logs.records.map(renderRecord);
        const none = state.logs.why || (!state.logs.name ? 'Choose a log.'
            : state.logs.run ? "No line of this run's matches." : 'No lines match.');
        replaceContent(table, ...(rows.length ? rows : [buildElement('tr', {}, [
            buildElement('td', { className: state.logs.why ? 'none why' : 'none', attrs: { colspan: 4 }, text: none })])]));
    }
    const older = document.getElementById('log-older');
    if (older) older.disabled = !state.logs.name || state.logs.complete;
    renderFileLinks();
    const chip = document.getElementById('log-run');
    if (chip) {
        chip.classList.toggle('hidden', !state.logs.run);
        const label = document.getElementById('log-run-tag');
        if (label) label.textContent = state.logs.run || '';
    }
}

/** The filters' and buttons' listeners, and the levels offered. */
export function wireLogs() {
    const level = document.getElementById('log-level');
    if (level) {
        replaceContent(level, ...LOG_LEVELS.map(name => buildElement('option', {
            text: name === 'WARNING' ? 'WARNING and worse' : `${name}${name === 'CRITICAL' ? '' : ' and worse'}`,
            attrs: { value: name } })));
        level.value = state.logs.level;
        level.addEventListener('change', () => {
            state.logs.level = level.value;
            readLog();
        });
    }
    const text = document.getElementById('log-text');
    if (text) {
        let waiting = null;
        text.addEventListener('input', () => {
            clearTimeout(waiting);
            waiting = setTimeout(() => {
                state.logs.text = text.value.trim();
                readLog();
            }, 300);
        });
    }
    for (const [id, key] of [['log-library', 'library'], ['log-kind', 'kind']]) {
        const filter = document.getElementById(id);
        if (filter) filter.addEventListener('change', () => {
            state.logs[key] = filter.value;
            renderRunFiles();
        });
    }
    const follow = document.getElementById('log-follow');
    if (follow) follow.addEventListener('change', () => { state.logs.follow = follow.checked; });
    const older = document.getElementById('log-older');
    if (older) older.addEventListener('click', () => readOlder());
    const clear = document.getElementById('log-run-clear');
    if (clear) clear.addEventListener('click', () => {
        state.logs.run = null;
        readLog();
    });
}
