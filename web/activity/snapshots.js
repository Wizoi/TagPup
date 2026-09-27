// Snapshots: each library's, by kind, with when each was taken, its age and size, and the
// disk they take (/api/activity/snapshots). Read-only: restoring one is the CLI's.
import { api } from './common/api.js';
import { buildElement } from './common/dom.js';
import { state } from './state.js';
import { bytes, duration } from './format.js';
import { fill, none } from './view.js';

/** Read each library's snapshots, and show them. */
export function loadSnapshots() {
    return api.site.json('/api/activity/snapshots').then(data => {
        if (!data || data.success === false) return data;
        state.snapshots = data;
        renderSnapshots();
        return data;
    });
}

function age(seconds) {
    return seconds < 86400 ? duration(seconds) : `${Math.floor(seconds / 86400)} d ${Math.round((seconds % 86400) / 3600)} h`;
}

/** Show what state.snapshots holds. */
export function renderSnapshots() {
    const data = state.snapshots;
    if (!data) return;
    const cards = (data.libraries || []).map(library => buildElement('div', { className: 'card', data: { library: library.name } }, [
        buildElement('h3', {}, [buildElement('span', { text: library.name }),
                                buildElement('small', { text: bytes(library.bytes) })]),
        (library.snapshots || []).length ? buildElement('table', { className: 'snapshots-table' }, [
            buildElement('thead', {}, [buildElement('tr', {}, ['Kind', 'Taken', 'Age', 'Size']
                .map(text => buildElement('th', { text })))]),
            buildElement('tbody', {}, library.snapshots.map(snapshot => buildElement('tr', { className: 'snapshot' }, [
                buildElement('td', { text: snapshot.kind }),
                buildElement('td', { text: snapshot.taken }),
                buildElement('td', { text: age(snapshot.age_seconds) }),
                buildElement('td', { text: bytes(snapshot.bytes) }),
            ]))),
        ]) : none('No snapshots yet.'),
        library.error ? buildElement('p', { className: 'error', text: library.error }) : null,
    ]));
    fill(document.getElementById('snapshots-body'), buildElement('p', { className: 'total', text: `All together: ${bytes(data.bytes)}` }),
        ...(cards.length ? cards : [none('No libraries.')]));
}
