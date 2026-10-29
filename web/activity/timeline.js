// Recent activity (What happened): one timeline of what was done -- each library's changes of the journal, recurring
// job runs and syncs, its runs of the indexer, and the server's updates -- newest first, as trees in Now's look (a
// `ul.run-tree`): a run under the entry whose `id` is its row's `parent` (any entry of its library but a journal
// change, whose ids are the journal's), or whose `run` is among its `syncs` (a folder sync's index); a sync the `sync`
// job ran is one entry already (the server pairs them, tagpup.services.activity). Each line says what ran, where, how
// it ended in a word and why it failed, how long it took, its counts and its logs; a library whose timeline could not
// be read says so. More reads further back (/api/activity/timeline). A reply without `unreadable` (a server before it)
// says none.
import { api } from './common/api.js';
import { buildElement } from './common/dom.js';
import { state } from './state.js';
import { asked } from './poll.js';
import { ago, counts, duration } from './format.js';
import { badge, fill, none, outcomeKind, runLinks, runTree } from './view.js';

/** How many more entries More asks for. */
const MORE = 50;

/** Read the timeline, as far back as it has been asked, and show it. */
export function loadTimeline() {
    return asked(api.site.json(`/api/activity/timeline?limit=${state.timeline.limit}`)).then(data => {
        if (!data || data.success === false) return data;
        state.timeline.entries = data.entries || [];
        state.timeline.more = !!data.more;
        state.timeline.unreadable = data.unreadable || [];
        renderTimeline();
        return data;
    });
}

/** More: read further back. */
export function readMore() {
    state.timeline.limit += MORE;
    return loadTimeline();
}

const known = value => value !== null && value !== undefined;

/** The entries as trees: [{ entry, children }], newest first at each level. */
function treeOf(entries) {
    const nodes = entries.map(entry => ({ entry, children: [] }));
    const byId = new Map();
    const byRun = new Map();
    for (const node of nodes) {
        const { entry } = node;
        if (entry.kind !== 'change' && known(entry.id)) byId.set(`${entry.library}|${entry.id}`, node);
        if (entry.run) byRun.set(entry.run, node);
    }
    const roots = [];
    for (const node of nodes) {
        const { entry } = node;
        const parent = (known(entry.parent) && byId.get(`${entry.library}|${entry.parent}`))
            || (entry.syncs || []).map(tag => byRun.get(tag)).find(Boolean) || null;
        (parent && parent !== node ? parent.children : roots).push(node);
    }
    return roots;
}

/** One entry's line: when, what ran, the library, how it ended, why it failed, how long, its counts and its logs. */
function entryLine({ entry }) {
    const said = counts(entry.counts);
    return [buildElement('div', { className: 'activity run-line' }, [
        buildElement('span', { className: 'when detail', text: entry.time || '', title: ago(entry.time) }),
        buildElement('span', { className: 'what', text: entry.what || entry.kind }),
        buildElement('span', { className: 'detail library', text: entry.library || '' }),
        entry.outcome ? badge(entry.outcome, outcomeKind(entry.outcome)) : null,
        entry.error ? buildElement('span', { className: 'error', text: entry.error }) : null,
        known(entry.seconds) ? buildElement('span', { className: 'detail took', text: `took ${duration(entry.seconds)}` }) : null,
        said ? buildElement('span', { className: 'detail counts', text: said }) : null,
        ...runLinks(entry.run, entry.logs),
    ])];
}

/** A library whose timeline could not be read: a line saying so, first. */
function unreadableLine({ library, error }) {
    return buildElement('li', { className: 'entry unreadable', data: { library } }, [
        buildElement('div', { className: 'activity unreadable' }, [
            badge('unreadable', 'bad'),
            buildElement('span', { className: 'what', text: library }),
            buildElement('span', { className: 'error', text: `Could not read what was done in this library: ${error}` }),
        ])]);
}

/** Show what state.timeline holds. */
export function renderTimeline() {
    const { entries, unreadable } = state.timeline;
    const tree = runTree(treeOf(entries), entryLine,
                         ({ entry }) => ({ className: `entry kind-${entry.kind}`, data: { kind: entry.kind, library: entry.library } }));
    tree.prepend(...(unreadable || []).map(unreadableLine));
    fill(document.getElementById('timeline-body'), entries.length || (unreadable || []).length ? tree : none('Nothing done yet.'));
    const more = document.getElementById('timeline-more');
    if (more) more.disabled = !state.timeline.more;
}

/** More's listener. */
export function wireTimeline() {
    const more = document.getElementById('timeline-more');
    if (more) more.addEventListener('click', () => readMore());
}
