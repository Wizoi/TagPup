// Sync & watcher: each library's root folders (on disk or missing, watched), when a change
// in its folders was last noticed, its last sync of one folder and of the whole library
// ("last in step"), and the folders to review, opening TagTuner's dialog
// (/api/activity/sync).
import { api } from './common/api.js';
import { buildElement } from './common/dom.js';
import { state } from './state.js';
import { ago, counts } from './format.js';
import { badge, fill, none, runLinks } from './view.js';

/** Read each library's syncs, and show them. */
export function loadSync() {
    return api.site.json('/api/activity/sync').then(data => {
        if (!data || data.success === false) return data;
        state.sync = data;
        renderSync();
        return data;
    });
}

function syncLine(label, record) {
    if (!record) return buildElement('div', { className: 'fact' }, [
        buildElement('span', { className: 'label', text: label }), buildElement('span', { text: 'never' })]);
    return buildElement('div', { className: 'fact' }, [
        buildElement('span', { className: 'label', text: label }),
        buildElement('span', { text: ago(record.finished), title: record.finished }),
        badge(record.in_step ? 'in step' : 'not in step', record.in_step ? 'ok' : 'busy'),
        buildElement('span', { className: 'detail', text: counts(record.changed) || 'nothing changed' }),
        ...runLinks(record.run, record.logs),
    ]);
}

function librarySync(library) {
    const roots = (library.roots || []).map(root => buildElement('li', {}, [
        buildElement('span', { className: 'path', text: root.path }),
        badge(root.there ? 'on disk' : 'missing', root.there ? 'ok' : 'bad'),
        badge(root.watched ? 'watched' : 'not watched', root.watched ? 'ok' : 'quiet'),
    ]));
    const watcher = library.watcher;
    const review = library.review_folders;
    const reviewLine = buildElement('div', { className: 'fact' }, [
        buildElement('span', { className: 'label', text: 'Folders to review' }),
        buildElement('span', { text: review === null || review === undefined ? 'not counted yet' : String(review) }),
        review && library.review_url ? buildElement('a', {
            className: 'link', text: 'Review in TagTuner',
            attrs: { href: library.review_url, target: '_blank', rel: 'noopener' } }) : null,
    ]);
    return buildElement('div', { className: 'card', data: { library: library.name } }, [
        buildElement('h3', { text: library.name }),
        roots.length ? buildElement('ul', { className: 'roots' }, roots)
            : none('No root folders: sync keeps the folders it holds in step, and offers none to review.'),
        buildElement('div', { className: 'fact' }, [
            buildElement('span', { className: 'label', text: 'Last change noticed' }),
            buildElement('span', { text: watcher && watcher.last_event ? ago(watcher.last_event) : 'none since the server started',
                                   title: (watcher && watcher.last_event) || '' }),
            watcher && watcher.not_watched ? badge('too many folders to watch', 'bad') : null,
        ]),
        syncLine('Last folder sync', library.last_folder),
        syncLine('Last whole sync', library.last_whole),
        buildElement('div', { className: 'fact' }, [
            buildElement('span', { className: 'label', text: 'Last in step' }),
            buildElement('span', { text: ago(library.last_in_step), title: library.last_in_step || '' }),
        ]),
        reviewLine,
        library.error ? buildElement('p', { className: 'error', text: library.error }) : null,
    ]);
}

/** Show what state.sync holds. */
export function renderSync() {
    const data = state.sync;
    if (!data) return;
    const cards = (data.libraries || []).map(librarySync);
    fill(document.getElementById('sync-body'),
        data.watching ? null : none('The folder watcher is not running in this server; the daily sync keeps each library in step.'),
        ...(cards.length ? cards : [none('No libraries.')]));
}
