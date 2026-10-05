// Now: what runs this moment -- each library's indexing (the folders being indexed and how
// far, those waiting), its Suggest runs and a recurring job running, the folder watcher's
// sync, and the always-on process (an update waiting, the server draining, an update
// refused). Asked every few seconds while the page is in view (/api/activity/now).
import { api } from './common/api.js';
import { buildElement } from './common/dom.js';
import { state } from './state.js';
import { ago } from './format.js';
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

function indexingLines(library) {
    const lines = [];
    const indexing = library.indexing || {};
    const running = indexing.running;
    if (running) {
        const more = running.folders > 1 ? ` and ${running.folders - 1} more folder(s)` : '';
        lines.push(buildElement('div', { className: 'activity' }, [
            badge('indexing', 'busy'),
            buildElement('span', { className: 'what', text: `${running.name}${more}` }),
            buildElement('progress', { attrs: { max: 100, value: running.percent || 0 },
                                       title: `${running.percent || 0}%` }),
            buildElement('span', { className: 'detail', text: running.message || '' }),
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
    return lines;
}

function serverLines(data) {
    const lines = [];
    const server = data.server || {};
    const supervisor = data.supervisor;
    if (server.taking_work === false) {
        lines.push(buildElement('div', { className: 'activity' }, [
            badge('draining', 'busy'),
            buildElement('span', { className: 'detail', text: 'The server takes no new work while it moves onto a new version.' }),
        ]));
    }
    if (supervisor && supervisor.state === 'moving') {
        lines.push(buildElement('div', { className: 'activity' }, [
            badge('update waiting', 'busy'),
            buildElement('span', { className: 'detail',
                                   text: `Moving ${supervisor.why || 'to a new version'} at the next quiet moment` }),
        ]));
    }
    if (supervisor && supervisor.update_refused) {
        lines.push(buildElement('div', { className: 'activity' }, [
            badge('update refused', 'bad'),
            buildElement('span', { className: 'detail', text: supervisor.update_refused.said || '' }),
        ]));
    }
    if (supervisor && ['gave up', 'restarting', 'waiting for the ports'].includes(supervisor.state)) {
        lines.push(buildElement('div', { className: 'activity' }, [
            badge(supervisor.state, 'bad'),
            buildElement('span', { className: 'detail', text: supervisor.why || '' }),
        ]));
    }
    if (data.syncing) {
        lines.push(buildElement('div', { className: 'activity' }, [
            badge('syncing', 'busy'),
            buildElement('span', { className: 'what', text: data.syncing.library }),
            buildElement('span', { className: 'detail', text: data.syncing.folder || 'every folder' }),
        ]));
    }
    return lines;
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
}
