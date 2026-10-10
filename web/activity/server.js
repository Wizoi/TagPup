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

/** Unload models now: the server lets every model go and gives up the graphics card, unless
 *  Suggest is using them, and says which in a sentence. Shown beside the button. */
export function unloadModels() {
    if (state.unloading) return Promise.resolve(null);
    state.unloading = true;
    state.unloaded = { text: 'Unloading...', ok: true };
    renderServer();
    return api.site.json('/api/activity/models/unload', { method: 'POST' }).then(answer => {
        const text = (answer && (answer.message || answer.error)) || 'Could not unload them.';
        state.unloaded = { text, ok: !!(answer && answer.success) };
    }).catch(err => {
        state.unloaded = { text: `Could not unload them: ${err.message || err}`, ok: false };
    }).then(() => {
        state.unloading = false;
        return loadServer();
    }).then(() => { renderServer(); });
}

function modelsFact(models) {
    const button = buildElement('button', { className: 'btn', attrs: { type: 'button' }, id: 'unload-models',
                                            text: 'Unload models now' });
    button.disabled = state.unloading || !models.loaded.length;
    button.addEventListener('click', unloadModels);
    const said = state.unloaded;
    return fact('Models', buildElement('span', { text: models.text }), button,
                said ? buildElement('span', { className: said.ok ? 'detail' : 'error', id: 'unload-models-said',
                                              text: said.text }) : null);
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
    if (data.models) facts.push(modelsFact(data.models));
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
