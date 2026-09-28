// Needs attention: what the owner is to put right, and nothing when there is nothing --
// each library's photos found damaged: a picture that does not decode, never indexed or
// written to, and one that decodes but may be an incomplete copy (tagpup.services.
// damaged_photos). Each with its path, why, when it was found, its size and modified time,
// and TagPup's page on its folder (/api/activity/attention). A file replaced since is not
// listed: it has left the list by itself.
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

function photoRow(photo) {
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
    ]);
}

function libraryCard(library) {
    const photos = library.photos || [];
    return buildElement('div', { className: 'card', data: { library: library.name } }, [
        buildElement('h3', { text: library.name }),
        buildElement('p', { className: 'detail', text: 'Restore each from a backup; nothing was written to them. '
            + 'A photo replaced by a good copy leaves this list by itself, and is indexed.' }),
        buildElement('table', { className: 'attention-table' }, [
            buildElement('thead', {}, [buildElement('tr', {}, ['Photo', '', 'Why', 'First found', 'Size', 'Modified', '']
                .map(text => buildElement('th', { text })))]),
            buildElement('tbody', {}, photos.map(photoRow)),
        ]),
        library.error ? buildElement('p', { className: 'error', text: library.error }) : null,
    ]);
}

/** Show what state.attention holds: a card for each library with something to show. */
export function renderAttention() {
    const data = state.attention;
    if (!data) return;
    const cards = (data.libraries || []).filter(library => (library.photos || []).length || library.error)
        .map(libraryCard);
    const count = (data.unreadable || 0) + (data.incomplete || 0);
    const link = document.querySelector('a[href="#attention"]');
    if (link) link.textContent = count ? `Needs attention (${count})` : 'Needs attention';
    fill(document.getElementById('attention-body'), ...(cards.length ? cards : [none('Nothing needs attention.')]));
}
