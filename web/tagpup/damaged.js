// TagPup's page: the photos of the open folder found damaged -- a notice at the top of the
// folder naming them, a mark on each one's card in place of a silent gap, and why on the
// photo when it is opened -- and the library's count in the header (common/damaged-count.js).
//
// The indexer finds them (tagpup.services.damaged_photos): a photo whose picture does not
// decode -- cut short, zero bytes -- which it neither indexes nor writes to, and one that
// decodes but ends in zero bytes, possibly an incomplete copy, which it indexes and leaves
// as it is. The folder's list is asked as the folder opens (GET /api/folder/damaged); a
// file replaced since is not on it.
import { api } from './common/api.js';
import { showDamagedCount } from './common/damaged-count.js';
import { buildElement, replaceContent } from './common/dom.js';
import { pathKey, samePath } from './common/paths.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import { damagedBadge, damagedNotice, photoDamagedNote, thumbnailsGrid } from './elements.js';
import { setStatus } from './status.js';

function plural(count, one, many) {
    return `${count} ${count === 1 ? one : many}`;
}

/** What is known of a photo found damaged -- { path, name, kind, reason, detail, indexed,
 *  zero_tail, found, ... } -- or null. */
export function damageOf(path) {
    return (path && state.damagedPhotos[pathKey(path)]) || null;
}

/** The library's count in the header. */
export function showLibraryDamage() {
    return showDamagedCount(damagedBadge);
}

/**
 * Ask which photos under the folder just opened were found damaged, and show them: the
 * notice, the cards' marks, the open photo's note. `path` null forgets the folder.
 */
export function checkDamagedPhotos(path) {
    state.damagedPhotos = {};
    state.damagedAsked += 1;
    const asked = state.damagedAsked;
    showDamage();
    if (!path) return Promise.resolve(null);
    return api.json(`/api/folder/damaged?path=${encodeURIComponent(path)}`)
        .then(found => {
            // Another folder, or the same asked again, since.
            if (asked !== state.damagedAsked || !state.scannedFolder || !samePath(path, state.scannedFolder)) return null;
            const photos = found && Array.isArray(found.photos) ? found.photos : [];
            state.damagedPhotos = Object.fromEntries(photos.map(photo => [pathKey(photo.path), photo]));
            showDamage();
            // Found since the page opened, or replaced: the header says so too.
            showLibraryDamage();
            return photos;
        })
        .catch(err => {
            console.error('Could not ask which photos in the folder were found damaged:', err);
            return null;
        });
}

function showDamage() {
    renderDamagedNotice();
    markDamagedCards();
    showPhotoDamage(state.activePhotoPath);
}

function checkButton(text, title, photoPaths) {
    const button = buildElement('button', {
        className: 'btn btn-secondary btn-sm damaged-check', text, title,
        attrs: { type: 'button', disabled: state.damagedChecking },
    });
    button.addEventListener('click', () => checkAgain(photoPaths));
    return button;
}

/**
 * Check again: read these photos (every one listed, without) again now, whatever their
 * stamp says -- a good copy laid over a damaged one can keep its time and size. One that
 * reads whole leaves the list and is indexed again (POST /api/damaged-photos/check).
 */
export function checkAgain(photoPaths) {
    if (state.damagedChecking) return Promise.resolve(null);
    const folder = state.scannedFolder;
    const asked = photoPaths || Object.values(state.damagedPhotos).map(photo => photo.path);
    state.damagedChecking = true;
    renderDamagedNotice();
    setStatus('busy', 'Checking again...', { transient: false });
    return api.json('/api/damaged-photos/check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths: asked }),
    }).then(done => {
        if (!done || done.success === false) throw new Error((done && done.error) || 'Check again failed');
        const parts = [];
        if (done.whole) parts.push(`${plural(done.whole, 'photo reads', 'photos read')} whole now, and ${done.whole === 1
            ? 'is' : 'are'} being indexed again`);
        if (done.still) parts.push(`${plural(done.still, 'is', 'are')} still damaged`);
        if (done.unreachable) parts.push(`${plural(done.unreachable, 'could', 'could')} not be reached`);
        setStatus('ready', parts.length ? `${parts.join('; ')}.` : 'Nothing to check.');
        return done;
    }).catch(err => {
        console.error('Could not check the damaged photos again:', err);
        setStatus('error', `Check again failed: ${err.message}`, { transient: false });
        return null;
    }).then(done => {
        state.damagedChecking = false;
        if (folder && state.scannedFolder && samePath(folder, state.scannedFolder)) return checkDamagedPhotos(folder);
        renderDamagedNotice();
        return done;
    });
}

function listItem(photo) {
    const name = buildElement('button', {
        className: 'link damaged-notice-name', text: photo.name, title: 'Open this photo',
        attrs: { type: 'button' },
    });
    name.addEventListener('click', () => {
        if (state.folderPhotos.some(each => samePath(each.path, photo.path))) upper.selectPhoto(photo.path);
    });
    return buildElement('li', { data: { path: photo.path } }, [
        name, buildElement('span', {
            className: 'damaged-notice-reason', text: ` — ${photo.indexed ? photo.detail : photo.reason} ` }),
        checkButton('Check again', 'Read this photo again now: replaced by a good copy, it is indexed', [photo.path]),
    ]);
}

/** The notice at the top of the folder: which photos cannot be read, which may be incomplete. */
export function renderDamagedNotice() {
    if (!damagedNotice) return;
    const all = Object.values(state.damagedPhotos);
    if (!all.length) {
        replaceContent(damagedNotice);
        damagedNotice.classList.add('hidden');
        return;
    }
    const unreadable = all.filter(photo => !photo.indexed);
    const incomplete = all.filter(photo => photo.indexed);
    const lines = [];
    if (unreadable.length) {
        lines.push(buildElement('p', {
            className: 'damaged-notice-line',
            text: `${plural(unreadable.length, 'photo', 'photos')} in this folder can't be read — the file is damaged. `
                + `Restore ${unreadable.length === 1 ? 'it' : 'them'} from a backup.`,
        }));
        lines.push(buildElement('ul', { className: 'damaged-notice-list' }, unreadable.map(listItem)));
    }
    if (incomplete.length) {
        lines.push(buildElement('p', {
            className: 'damaged-notice-line',
            text: `${plural(incomplete.length, 'photo', 'photos')} in this folder may be ${incomplete.length === 1
                ? 'an incomplete copy' : 'incomplete copies'} — the file ends in zero bytes, and the picture may be `
                + `grey below a line. Compare ${incomplete.length === 1 ? 'it' : 'them'} with a backup.`,
        }));
        lines.push(buildElement('ul', { className: 'damaged-notice-list' }, incomplete.map(listItem)));
    }
    lines.push(buildElement('p', { className: 'damaged-notice-actions' }, [
        checkButton(all.length === 1 ? 'Check again' : 'Check all again',
                    'Restored them? Read them again now, whatever their modified time says', null),
    ]));
    replaceContent(damagedNotice, ...lines);
    damagedNotice.classList.remove('hidden');
}

/**
 * Mark a card: a photo that cannot be read shows why in place of a picture that will not
 * load, and one possibly incomplete carries a warning on its picture. A card of neither
 * loses any mark it had.
 */
export function markCard(card, damage) {
    if (!card) return;
    card.classList.toggle('damaged', !!damage);
    const wrapper = card.querySelector('.thumbnail-img-wrapper');
    if (!wrapper) return;
    wrapper.querySelectorAll('.thumbnail-damaged, .thumbnail-damaged-placeholder').forEach(el => el.remove());
    if (!damage) return;
    if (!damage.indexed) {
        wrapper.querySelectorAll('img').forEach(img => img.remove());
        wrapper.appendChild(buildElement('div', { className: 'thumbnail-damaged-placeholder', title: damage.reason },
            [buildElement('span', { text: '⚠' }), buildElement('span', { text: "Can't be read" })]));
    }
    wrapper.appendChild(buildElement('span', {
        className: 'thumbnail-damaged', text: damage.indexed ? '⚠ Incomplete?' : '⚠ Damaged', title: damage.reason,
    }));
}

/** Mark the grid's cards as the folder's list says. */
export function markDamagedCards() {
    if (!thumbnailsGrid) return;
    thumbnailsGrid.querySelectorAll('.thumbnail-card').forEach(card => {
        markCard(card, damageOf(card.getAttribute('data-path')));
    });
}

/** Say why on the open photo, when it was found damaged. */
export function showPhotoDamage(path) {
    if (!photoDamagedNote) return;
    const damage = damageOf(path);
    if (!damage) {
        photoDamagedNote.textContent = '';
        photoDamagedNote.classList.add('hidden');
        return;
    }
    photoDamagedNote.textContent = damage.indexed
        ? `This photo may be an incomplete copy: ${damage.detail || 'the file ends in zero bytes'}, and the picture `
            + `may be grey below a line. Found ${damage.found}. Compare it with a backup; nothing was written to it.`
        : `This photo can't be read: ${damage.detail ? `${damage.reason} (${damage.detail})` : damage.reason}. `
            + `Found ${damage.found}. Restore it from a backup; nothing was written to it.`;
    photoDamagedNote.classList.remove('hidden');
}
