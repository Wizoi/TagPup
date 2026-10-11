// The server: the version answering, since when it runs, its background tasks and its
// models (/api/activity/server).
import { api } from './common/api.js';
import { buildElement } from './common/dom.js';
import { state } from './state.js';
import { ago } from './format.js';
import { badge, fill } from './view.js';

/** Read how the server is, and show it. */
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
    const facts = [
        fact('Version', buildElement('span', { text: data.version || 'run from its code folder' })),
        fact('Server running since', buildElement('span', { text: data.running_since || '', title: ago(data.running_since) })),
        fact('Background', buildElement('span', { text: (data.background || []).join(', ') || 'none' })),
    ];
    if (data.models) facts.push(modelsFact(data.models));
    fill(document.getElementById('server-body'), buildElement('div', { className: 'card' }, facts));
}
