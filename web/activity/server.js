// Always on: the version answering, since when the server and its supervisor run, how
// often the server crashed, the last update and an update refused (/api/activity/server,
// from data/supervisor.json).
import { api } from './common/api.js';
import { buildElement } from './common/dom.js';
import { state } from './state.js';
import { ago } from './format.js';
import { badge, fill } from './view.js';

/** Read how the always-on process is, and show it. */
export function loadServer() {
    return api.site.json('/api/activity/server').then(data => {
        if (!data || data.success === false) return data;
        state.server = data;
        renderServer();
        return data;
    });
}

function fact(label, ...value) {
    return buildElement('div', { className: 'fact' }, [buildElement('span', { className: 'label', text: label }), ...value]);
}

/** Show what state.server holds. */
export function renderServer() {
    const data = state.server;
    if (!data) return;
    const supervisor = data.supervisor;
    const facts = [
        fact('Version', buildElement('span', { text: data.version || 'run from its code folder' })),
        fact('Server running since', buildElement('span', { text: data.running_since || '', title: ago(data.running_since) })),
        fact('Background', buildElement('span', { text: (data.background || []).join(', ') || 'none' })),
    ];
    if (!supervisor) {
        facts.push(fact('Always on', badge('not set up', 'quiet'),
                        buildElement('span', { className: 'detail', text: 'No supervisor has run in this home (scripts/startup.py install).' })));
    } else {
        facts.push(fact('Supervisor', badge(supervisor.alive ? supervisor.state : 'not running',
                                              supervisor.alive && supervisor.state === 'running' ? 'ok' : 'busy'),
                        buildElement('span', { className: 'detail', text: supervisor.why || '' })));
        if (supervisor.running_since) {
            facts.push(fact('Supervisor running since', buildElement('span', { text: supervisor.running_since })));
        }
        if (supervisor.crashes !== undefined) {
            facts.push(fact('Crashes', buildElement('span', {
                text: `${supervisor.crashes} in the last ${supervisor.crash_window_minutes || 10} minutes; `
                      + `the server started ${supervisor.server_starts || 0} time(s)` })));
        }
        facts.push(fact('Last update', buildElement('span', {
            text: supervisor.previous_version ? `from ${supervisor.previous_version} to ${supervisor.version}`
                + (supervisor.running_since ? `, ${supervisor.running_since}` : '') : 'none since it started' })));
        if (supervisor.update_refused) {
            facts.push(fact('Update refused', badge('refused', 'bad'), buildElement('span', {
                className: 'detail', text: `${supervisor.update_refused.said} (since ${supervisor.update_refused.since})` })));
        }
    }
    fill(document.getElementById('server-body'), buildElement('div', { className: 'card' }, facts));
}
