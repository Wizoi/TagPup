// Needs attention: what the owner is to put right, and nothing when there is nothing --
// each library's photos found damaged: a picture that does not decode, never indexed or
// written to, and one that decodes but may be an incomplete copy (tagpup.services.
// damaged_photos). Each with its path, why, when it was found, its size and modified time,
// and TagPup's page on its folder (/api/activity/attention). A file replaced since is not
// listed: it has left the list by itself. And the names to review: how many names no person's
// tag is wait for the owner to settle, with the TagTuner page that opens the list.
import { api } from './common/api.js';
import { buildElement } from './common/dom.js';
import { state } from './state.js';
import { bytes } from './format.js';
import { badge, fill, none } from './view.js';

/** Read what needs attention, and show it. */
export function loadAttention() {
    return api.site.json('/api/activity/attention').then(data => {
        if (!data || data.success === false) return data;
        state.attention = data;
        renderAttention();
        return data;
    });
}

/** A file's modified time, from seconds, as the records spell a time. */
function modified(seconds) {
    const date = new Date(Number(seconds) * 1000);
    if (!seconds || Number.isNaN(date.getTime())) return '';
    const two = n => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())} `
        + `${two(date.getHours())}:${two(date.getMinutes())}:${two(date.getSeconds())}`;
}

function plural(count, one, many) {
    return `${count} ${count === 1 ? one : many}`;
}

/**
 * Check again: read the damaged photos again now, whatever their stamp -- `library`'s, or
 * only `path` -- and read the list again (POST /api/activity/attention/check).
 */
export function checkAgain(library, path) {
    if (state.attentionChecking) return Promise.resolve(null);
    state.attentionChecking = true;
    renderAttention();
    return api.site.json('/api/activity/attention/check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(path ? { library, path } : { library }),
    }).then(done => {
        const found = (done && done.libraries) || [];
        const whole = found.reduce((sum, each) => sum + (each.whole || 0), 0);
        const still = found.reduce((sum, each) => sum + (each.still || 0), 0);
        const parts = [];
        if (whole) parts.push(`${plural(whole, 'photo reads', 'photos read')} whole now, and ${whole === 1 ? 'is' : 'are'}`
            + ' being indexed again');
        if (still) parts.push(`${plural(still, 'is', 'are')} still damaged`);
        state.attentionChecked = { text: parts.length ? `${parts.join('; ')}.` : 'Nothing to check.', ok: true };
        return done;
    }).catch(err => {
        state.attentionChecked = { text: `Check again failed: ${err.message}`, ok: false };
        return null;
    }).then(done => {
        state.attentionChecking = false;
        return loadAttention().then(() => done);
    });
}

function checkButton(text, title, library, path) {
    const button = buildElement('button', {
        className: 'btn check-again', text, title, attrs: { type: 'button', disabled: state.attentionChecking },
    });
    button.addEventListener('click', () => checkAgain(library, path));
    return button;
}

function photoRow(photo, library) {
    return buildElement('tr', { className: 'damaged-photo', data: { kind: photo.kind } }, [
        buildElement('td', { className: 'path', text: photo.path }),
        buildElement('td', {}, [badge(photo.indexed ? 'possibly incomplete' : "can't be read",
                                      photo.indexed ? 'busy' : 'bad')]),
        buildElement('td', { text: photo.indexed ? photo.detail : photo.reason, title: photo.detail || '' }),
        buildElement('td', { text: photo.found || '' }),
        buildElement('td', { text: bytes(photo.size) }),
        buildElement('td', { text: modified(photo.mtime) }),
        buildElement('td', {}, [photo.folder_url ? buildElement('a', {
            className: 'link', text: 'Open the folder in TagPup',
            attrs: { href: photo.folder_url, target: '_blank', rel: 'noopener' } }) : null]),
        buildElement('td', {}, [checkButton('Check again', 'Read this photo again now: replaced by a good copy, '
            + 'it is indexed', library, photo.path)]),
    ]);
}

/** The names the owner settles by hand: how many, and the page that opens them (the library's TagTuner, Review People). */
function namesNote(library) {
    const names = library.names_to_review || 0;
    if (!names) return null;
    return buildElement('p', { className: 'names-to-review' }, [
        `${plural(names, 'name', 'names')} ${names === 1 ? 'waits' : 'wait'} for you to settle: make a person, link `
            + 'the name to one, unname the faces or set it aside. Nothing is changed until you choose. ',
        library.names_url ? buildElement('a', {
            className: 'link', text: 'Open them in TagTuner',
            attrs: { href: library.names_url, target: '_blank', rel: 'noopener' } }) : null,
    ]);
}

function libraryCard(library) {
    const photos = library.photos || [];
    return buildElement('div', { className: 'card', data: { library: library.name } }, [
        buildElement('h3', { text: library.name }),
        namesNote(library),
        photos.length ? buildElement('p', { className: 'detail', text: 'Restore each from a backup; nothing was written to them. '
            + 'A photo replaced by a good copy leaves this list by itself, and is indexed.' }) : null,
        photos.length ? buildElement('table', { className: 'attention-table' }, [
            buildElement('thead', {}, [buildElement('tr', {}, ['Photo', '', 'Why', 'First found', 'Size', 'Modified', '', '']
                .map(text => buildElement('th', { text })))]),
            buildElement('tbody', {}, photos.map(photo => photoRow(photo, library.name))),
        ]) : null,
        photos.length ? checkButton(photos.length === 1 ? 'Check again' : 'Check all again',
                    'Restored them? Read them again now, whatever their modified time says', library.name, null) : null,
        library.faces_to_detect ? buildElement('p', { className: 'detail faces-to-detect', text:
            `${plural(library.faces_to_detect, 'photo waits', 'photos wait')} for ${library.faces_to_detect === 1 ? 'its'
                : 'their'} faces to be detected again: indexed from a damaged copy, whole now. `
            + 'The next index of their folders detects them.' }) : null,
        library.error ? buildElement('p', { className: 'error', text: library.error }) : null,
    ]);
}

/** Show what state.attention holds: a card for each library with something to show. */
export function renderAttention() {
    const data = state.attention;
    if (!data) return;
    const cards = (data.libraries || [])
        .filter(library => (library.photos || []).length || library.faces_to_detect || library.names_to_review || library.error)
        .map(libraryCard);
    const count = (data.unreadable || 0) + (data.incomplete || 0) + (data.names_to_review || 0);
    const link = document.querySelector('a[href="#attention"]');
    if (link) link.textContent = count ? `Needs attention (${count})` : 'Needs attention';
    const checked = state.attentionChecked
        ? buildElement('p', { className: state.attentionChecked.ok ? 'detail' : 'error', text: state.attentionChecked.text })
        : null;
    fill(document.getElementById('attention-body'), checked,
         ...(cards.length ? cards : [none('Nothing needs attention.')]));
}
