// Recent activity: one timeline of what was done -- each library's changes of the journal,
// recurring job runs and syncs, this server's runs of the indexer, and the always-on
// process's updates -- newest first, with their counts; More reads further back
// (/api/activity/timeline).
import { api } from './common/api.js';
import { buildElement } from './common/dom.js';
import { state } from './state.js';
import { ago, counts, duration } from './format.js';
import { badge, fill, none, outcomeKind, runLinks } from './view.js';

/** How many more entries More asks for. */
const MORE = 50;

/** Read the timeline, as far back as it has been asked, and show it. */
export function loadTimeline() {
    return api.site.json(`/api/activity/timeline?limit=${state.timeline.limit}`).then(data => {
        if (!data || data.success === false) return data;
        state.timeline.entries = data.entries || [];
        state.timeline.more = !!data.more;
        renderTimeline();
        return data;
    });
}

/** More: read further back. */
export function readMore() {
    state.timeline.limit += MORE;
    return loadTimeline();
}

function entryRow(entry) {
    return buildElement('tr', { className: `entry kind-${entry.kind}`, data: { kind: entry.kind } }, [
        buildElement('td', { text: entry.time || '', title: ago(entry.time) }),
        buildElement('td', { text: entry.library || '' }),
        buildElement('td', {}, [badge(entry.kind, 'quiet')]),
        buildElement('td', { className: 'what', text: entry.what || '' }),
        buildElement('td', {}, [badge(entry.outcome || '', outcomeKind(entry.outcome))]),
        buildElement('td', { text: counts(entry.counts) }),
        buildElement('td', { text: duration(entry.seconds) }),
        buildElement('td', { className: 'error', text: entry.error || '' }),
        buildElement('td', {}, runLinks(entry.run, entry.logs)),
    ]);
}

/** Show what state.timeline holds. */
export function renderTimeline() {
    const rows = state.timeline.entries.map(entryRow);
    fill(document.getElementById('timeline-body'), rows.length ? buildElement('table', { className: 'timeline-table' }, [
        buildElement('thead', {}, [buildElement('tr', {}, ['When', 'Library', 'Kind', 'What', 'Outcome', 'Counts', 'Took',
                                                            'Error', ''].map(text => buildElement('th', { text })))]),
        buildElement('tbody', {}, rows),
    ]) : none('Nothing done yet.'));
    const more = document.getElementById('timeline-more');
    if (more) more.disabled = !state.timeline.more;
}

/** More's listener. */
export function wireTimeline() {
    const more = document.getElementById('timeline-more');
    if (more) more.addEventListener('click', () => readMore());
}
