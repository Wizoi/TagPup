// Logs: a tab for each log in data/logs (the web server's, the always-on process's, each
// library's indexer, the console's...), its newest records first as time, level, logger and
// message; filtered by level (warnings and worse unless asked), by text and by run; Follow
// asks for what was written since, every few seconds; Older reads further back; Raw and
// Download are the file itself. The server reads each log from its end and never whole
// (/api/activity/logs/<name>): the page says where it read from and to, by offset.
import { api } from './common/api.js';
import { buildElement, replaceContent } from './common/dom.js';
import { state } from './state.js';
import { bytes } from './format.js';

/** How many records a read asks for. */
const LOG_PAGE = 200;

/** The most records the page keeps shown: following a busy log keeps the newest. */
const LOG_KEEP = 2000;

const LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];

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

/** Read the list of logs, and make a tab of each. */
export function loadLogFiles() {
    return api.site.json('/api/activity/logs').then(data => {
        state.logs.files = (data && data.logs) || [];
        if (!state.logs.name || !state.logs.files.some(file => file.name === state.logs.name)) {
            const web = state.logs.files.find(file => file.name === 'tagpup_web.log');
            state.logs.name = web ? web.name : (state.logs.files[0] || {}).name || null;
        }
        renderLogTabs();
        return data;
    });
}

function renderLogTabs() {
    const tabs = document.getElementById('log-tabs');
    if (!tabs) return;
    const buttons = state.logs.files.map(file => {
        const chosen = file.name === state.logs.name;
        const tab = buildElement('button', {
            className: chosen ? 'tab chosen' : 'tab', title: file.name,
            attrs: { type: 'button', role: 'tab', 'aria-selected': chosen ? 'true' : 'false' },
            data: { log: file.name },
        }, [buildElement('span', { text: file.source }), buildElement('small', { text: bytes(file.bytes) })]);
        tab.addEventListener('click', () => chooseLog(file.name));
        return tab;
    });
    replaceContent(tabs, ...(buttons.length ? buttons : [buildElement('p', { className: 'none', text: 'No logs yet.' })]));
    const raw = document.getElementById('log-raw');
    const download = document.getElementById('log-download');
    if (raw) raw.href = state.logs.name ? api.site.url(logAddress(state.logs.name, '/raw')) : '#';
    if (download) download.href = state.logs.name ? api.site.url(logAddress(state.logs.name, '/download')) : '#';
}

/** Show the log `name` from its end, with the filters as they are. */
export function chooseLog(name) {
    state.logs.name = name;
    renderLogTabs();
    return readLog();
}

/** Read the chosen log from its end again: the filters changed, or another log was chosen. */
export function readLog() {
    if (!state.logs.name) {
        renderLogRecords();
        return Promise.resolve(null);
    }
    const asked = ++state.logs.asked;
    return api.site.json(logAddress(state.logs.name) + logQuery()).then(data => {
        if (asked !== state.logs.asked) return null;   // a newer read was asked meanwhile
        if (!data || data.success === false) {
            say((data && data.error) || 'Could not read the log.');
            return data;
        }
        state.logs.records = data.records || [];
        state.logs.start = data.start;
        state.logs.end = data.end;
        state.logs.complete = !!data.complete;
        say('');
        renderLogRecords();
        return data;
    });
}

/** Older: the records before the oldest shown. */
export function readOlder() {
    if (!state.logs.name || state.logs.complete || state.logs.start === null) return Promise.resolve(null);
    const asked = ++state.logs.asked;
    return api.site.json(logAddress(state.logs.name) + logQuery({ before: state.logs.start })).then(data => {
        if (asked !== state.logs.asked || !data || data.success === false) return null;
        state.logs.records = state.logs.records.concat(data.records || []);
        state.logs.start = data.start;
        state.logs.complete = !!data.complete;
        renderLogRecords();
        return data;
    });
}

/** Follow: what was written since the last read, newest first above what is shown. */
export function followLog() {
    if (!state.logs.follow || !state.logs.name || state.logs.end === null) return Promise.resolve(null);
    const asked = state.logs.asked;
    return api.site.json(logAddress(state.logs.name) + logQuery({ after: state.logs.end })).then(data => {
        if (asked !== state.logs.asked || !data || data.success === false) return null;
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

/** "Logs for this run": the log `name`, only the lines of the run `run`, at every level. */
export function showRunLogs(run, name) {
    state.logs.run = run;
    state.logs.level = 'DEBUG';
    const level = document.getElementById('log-level');
    if (level) level.value = 'DEBUG';
    const section = document.getElementById('logs');
    if (section && typeof section.scrollIntoView === 'function') section.scrollIntoView();
    return chooseLog(name || 'tagpup_web.log');
}

function say(text) {
    const line = document.getElementById('log-status');
    if (line) line.textContent = text || '';
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
        replaceContent(table, ...(rows.length ? rows : [buildElement('tr', {}, [
            buildElement('td', { className: 'none', attrs: { colspan: 4 },
                                 text: state.logs.name ? 'No lines match.' : 'Choose a log.' })])]));
    }
    const older = document.getElementById('log-older');
    if (older) older.disabled = !state.logs.name || state.logs.complete;
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
