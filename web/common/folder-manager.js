/**
 * Manage Folders, from the gear of TagPup and of TagTuner (docs/plans/manage-folders.md): add folders to the library
 * -- browse to a parent, tick its subfolders, queue them to be indexed (/api/folder/index-start) -- or remove a folder
 * the library holds (/api/folder/remove). Both are the server's (tagpup.web.folder_routes); this is the one dialog both
 * pages open, each passing what it does after: `queued(reply)` once folders are queued, `removed(folder, reply)` once
 * one is removed. It never touches the library's roots (TagTuner's Roots dialog does).
 *
 * Removal lists the folders the library holds, not the disk's: the usual reason to remove one is that it is gone from
 * disk, and a dialog of the disk's folders could not pick it (#47). Whether each is on disk is asked of the rows shown
 * (/api/folder/on-disk): asking it of all 2,660 of a library on a share took 22 s. A removal keeps the photo files and
 * drops the face work on them; a folder under one added with its subfolders goes into the library's ignored folders.
 * The server refuses to remove a folder being indexed, and the dialog shows its sentence.
 *
 * A reply that comes after the dialog closed, or after it opened again, is let go by its opening (`token`), as
 * web/tuner/roots.js does; the page is still told what was written. A second press while a write is on its way does
 * nothing (`busy`). A library change reloads both pages, so the dialog never outlives its library.
 */
import { api } from './api.js';
import { holdFocus, releaseFocus } from './dialog.js';
import { buildElement, replaceContent } from './dom.js';
import { baseName } from './paths.js';

/** The most rows the removal list shows, and asks about at once (tagpup.services.photos.FOLDER_STATES_MOST). */
export const SHOWN_MOST = 100;

/** What the dialog says before a removal: the files stay, the face work goes, a covered folder is ignored. */
export const REMOVAL_SAYS = 'The photo files stay where they are. The face work on them -- names given and faces '
    + 'excluded -- goes with them, and cannot be undone. A folder under one added with its subfolders is put in the '
    + "library's ignored folders, so an index of the folder above leaves it out.";

const manager = {
    modal: null, status: null, open: false, token: 0, busy: false, queued: null, removed: null, lookTimer: null, asking: new Set(),
    add: { browse: null, parent: null, list: null, selection: null, queue: null, hideIndexed: null,
        folders: [], ticked: new Set() },
    remove: { filter: null, list: null, selection: null, button: null, folders: [], chosen: null, states: new Map() },
};

function say(text) {
    manager.status.textContent = text || '';
}

/** True when a reply of opening `token` has nothing left to show. */
function stale(token) {
    return token !== manager.token || !manager.open;
}

function post(path, body, fallback) {
    return api.fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    }).then(res => api.answered(res, fallback));
}

function setBusy(busy) {
    manager.busy = busy;
    manager.add.queue.disabled = busy || !manager.add.ticked.size;
    manager.remove.button.disabled = busy || !manager.remove.chosen;
}

function note(container, text) {
    replaceContent(container, buildElement('p', { className: 'folder-manager-empty', text }));
}

// ---- Add: a parent's subfolders, ticked and queued ---------------------------------------------------------------

function loadSubfolders(parent) {
    const token = manager.token;
    manager.add.parent.textContent = parent;
    note(manager.add.list, 'Reading the folder...');
    return api.fetch(`/api/folder/subfolders?path=${encodeURIComponent(parent)}`)
        .then(res => api.answered(res, 'The folder could not be read.'))
        .then(data => {
            if (stale(token)) return;
            const folders = [];
            // The parent itself, when it holds images directly or has no subfolders: a leaf is the whole answer.
            if (data.own_images > 0 || !data.folders.length) {
                folders.push({ path: data.parent, name: `${baseName(data.parent) || data.parent}  (this folder itself)`,
                    images: data.own_images, indexed: data.own_indexed || 0 });
            }
            folders.push(...data.folders);
            manager.add.folders = folders;
            // Preselected: what has images and is not wholly in the library already.
            manager.add.ticked = new Set(folders.filter(f => f.images > 0 && f.indexed < f.images).map(f => f.path));
            renderAddList();
        })
        .catch(err => {
            if (!stale(token)) note(manager.add.list, 'Could not read that folder: ' + err.message);
        });
}

function browse() {
    const token = manager.token;
    return api.json('/api/browse-folder').then(data => {
        if (!stale(token) && data && data.path) return loadSubfolders(data.path);
        return null;
    });
}

function addRow(folder) {
    const whole = folder.images > 0 && folder.indexed >= folder.images;
    const box = buildElement('input', { attrs: { type: 'checkbox' } });
    box.checked = manager.add.ticked.has(folder.path);
    box.addEventListener('change', () => {
        if (box.checked) manager.add.ticked.add(folder.path);
        else manager.add.ticked.delete(folder.path);
        showAddSelection();
    });
    let meta = `${folder.images} image(s)`;
    if (!folder.images) meta = 'no images';
    else if (whole) meta += ' — already in the library';
    else if (folder.indexed) meta += `, ${folder.indexed} already in the library`;
    return buildElement('label', { className: 'folder-manager-row' + (whole ? ' already-indexed' : '') }, [
        box,
        buildElement('span', { className: 'folder-manager-name', text: folder.name, title: folder.path }),
        buildElement('span', { className: 'folder-manager-meta', text: meta }),
    ]);
}

function renderAddList() {
    const hide = manager.add.hideIndexed.checked;
    const rows = manager.add.folders
        .filter(f => !(hide && f.images > 0 && f.indexed >= f.images))
        .map(addRow);
    if (!manager.add.folders.length) note(manager.add.list, 'No subfolders and no images directly in that folder.');
    else replaceContent(manager.add.list, ...rows);
    showAddSelection();
}

function showAddSelection() {
    const ticked = manager.add.folders.filter(f => manager.add.ticked.has(f.path));
    const images = ticked.reduce((sum, f) => sum + (f.images || 0), 0);
    manager.add.selection.textContent = ticked.length ? `${ticked.length} folder(s), ${images} image(s)` : 'Nothing ticked';
    manager.add.queue.textContent = ticked.length > 1 ? `Add ${ticked.length} folders` : 'Add';
    manager.add.queue.disabled = manager.busy || !ticked.length;
}

function tickAll(on) {
    manager.add.ticked = new Set(on ? manager.add.folders.filter(f => f.images > 0).map(f => f.path) : []);
    renderAddList();
}

function queueTicked() {
    const folders = manager.add.folders.filter(f => manager.add.ticked.has(f.path)).map(f => f.path);
    if (!folders.length || manager.busy) return null;
    const token = manager.token;
    setBusy(true);
    say('Adding...');
    return post('/api/folder/index-start', { folder_paths: folders }, 'The folders could not be added.')
        .then(data => {
            // The page follows the queue whether or not the dialog is still open: the folders were added.
            if (manager.queued) manager.queued(data);
            if (stale(token)) return;
            const queued = (data.queued || []).length;
            const already = (data.already_queued || []).length;
            let text = `${queued} folder(s) added and queued to be indexed.`;
            if (already) text += ` ${already} already queued or being indexed.`;
            // A folder on the ignored list was taken off it to be added (tagpup.services.libraries.add).
            if (data.note) text += ' ' + data.note;
            say(text);
            manager.add.ticked = new Set();
            renderAddList();
        })
        .catch(err => {
            if (!stale(token)) say('Not added: ' + err.message);
        })
        .finally(() => setBusy(false));
}

// ---- Remove: the library's folders, one chosen -------------------------------------------------------------------

function loadIndexed() {
    const token = manager.token;
    return api.fetch('/api/folder/indexed')
        .then(res => api.answered(res, "The library's folders could not be read."))
        .then(data => {
            if (stale(token)) return;
            manager.remove.folders = data.folders || [];
            renderRemoveList();
        })
        .catch(err => {
            if (!stale(token)) note(manager.remove.list, "Could not read the library's folders: " + err.message);
        });
}

function shownFolders() {
    const wanted = manager.remove.filter.value.trim().toLowerCase();
    return manager.remove.folders.filter(f => !wanted || f.path.toLowerCase().includes(wanted));
}

function removeRow(folder) {
    const state = manager.remove.states.get(folder.path);
    const choice = buildElement('input', { attrs: { type: 'radio', name: 'folder-manager-remove' } });
    choice.checked = folder.path === manager.remove.chosen;
    choice.addEventListener('change', () => {
        manager.remove.chosen = folder.path;
        showRemoveSelection();
    });
    // A folder above those holding photos has none of its own; removing it takes every folder under it.
    let meta = `${folder.photos} photo(s)` + (folder.own_photos === 0 ? ' in folders under it' : '');
    if (state === 'gone') meta += ' — not on disk';
    else if (state === 'unanswered') meta += ' — its share is not answering';
    return buildElement('label', { className: 'folder-manager-row' + (state === 'gone' ? ' not-on-disk' : '') }, [
        choice,
        buildElement('span', { className: 'folder-manager-name', text: folder.path, title: folder.path }),
        buildElement('span', { className: 'folder-manager-meta', text: meta }),
    ]);
}

function renderRemoveList() {
    const matching = shownFolders();
    const shown = matching.slice(0, SHOWN_MOST);
    if (!shown.length) {
        note(manager.remove.list, manager.remove.folders.length ? 'No folder matches that.' : 'This library holds no photos.');
    } else {
        const more = matching.length - shown.length;
        replaceContent(manager.remove.list, ...shown.map(removeRow), more > 0
            ? buildElement('p', { className: 'folder-manager-empty', text: `${more} more: type in the filter to find one.` })
            : null);
    }
    showRemoveSelection();
    lookSoon(shown);
}

/** Ask whether the rows shown are on disk, a moment after the filter stops changing. */
function lookSoon(shown) {
    clearTimeout(manager.lookTimer);
    // Not those already answered, nor those a look is still on its way for.
    const unknown = shown.map(f => f.path)
        .filter(path => !manager.remove.states.has(path) && !manager.asking.has(path));
    if (!unknown.length) return;
    const token = manager.token;
    const asking = manager.asking;
    manager.lookTimer = setTimeout(() => {
        unknown.forEach(path => asking.add(path));
        const query = unknown.map(path => 'path=' + encodeURIComponent(path)).join('&');
        api.fetch('/api/folder/on-disk?' + query)
            .then(res => api.answered(res, 'Could not look at the folders.'))
            .then(data => {
                unknown.forEach(path => asking.delete(path));
                if (stale(token)) return;
                (data.folders || []).forEach(f => manager.remove.states.set(f.path, f.state));
                renderRemoveList();
            })
            .catch(err => {
                unknown.forEach(path => asking.delete(path));
                if (!stale(token)) say('Could not look at the folders: ' + err.message);
            });
    }, 250);
}

function showRemoveSelection() {
    const chosen = manager.remove.folders.find(f => f.path === manager.remove.chosen);
    manager.remove.selection.textContent = chosen ? `${chosen.photos} photo(s) under it` : 'Nothing chosen';
    manager.remove.button.disabled = manager.busy || !chosen;
}

function removedSaid(data) {
    let text = `Removed ${data.photos_removed} photo(s) and ${data.faces_removed} face(s).`;
    if (data.manual_lost || data.excluded_lost) {
        text += ` That included ${data.manual_lost} name(s) given and ${data.excluded_lost} face(s) excluded.`;
    }
    if (data.ignored) {
        text += " A folder above it was added with its subfolders, so it is now in the library's ignored folders "
            + '(Settings; History can undo that).';
    }
    return text;
}

function removeChosen() {
    const chosen = manager.remove.folders.find(f => f.path === manager.remove.chosen);
    if (!chosen || manager.busy) return null;
    if (!window.confirm(`Remove the ${chosen.photos} photo(s) under\n\n${chosen.path}\n\nfrom this library?\n\n`
        + REMOVAL_SAYS)) return null;
    const token = manager.token;
    setBusy(true);
    say('Removing...');
    return post('/api/folder/remove', { folder_path: chosen.path }, 'The folder could not be removed.')
        .then(data => {
            if (manager.removed) manager.removed(chosen.path, data);
            // Closed and opened again meanwhile: that opening's list was read before the removal.
            if (stale(token)) return manager.open ? loadIndexed() : null;
            say(removedSaid(data));
            manager.remove.chosen = null;
            return loadIndexed();
        })
        .catch(err => {
            // The server's own sentence: "Not removed: ... is being indexed ..." among them.
            if (!stale(token)) say(err.said ? err.message : 'Not removed: ' + err.message);
        })
        .finally(() => setBusy(false));
}

// ---- The dialog ---------------------------------------------------------------------------------------------------

function button(text, className, onClick) {
    const made = buildElement('button', { className, text, attrs: { type: 'button' } });
    made.addEventListener('click', onClick);
    return made;
}

function buildDialog() {
    const add = manager.add;
    const remove = manager.remove;
    add.browse = button('Browse...', 'btn btn-primary btn-sm', browse);
    add.parent = buildElement('span', { className: 'folder-manager-parent' });
    add.list = buildElement('div', { className: 'folder-manager-list' });
    add.selection = buildElement('span', { className: 'folder-manager-selection' });
    add.queue = button('Add', 'btn btn-primary btn-sm', queueTicked);
    add.hideIndexed = buildElement('input', { attrs: { type: 'checkbox' } });
    add.hideIndexed.addEventListener('change', renderAddList);
    remove.filter = buildElement('input', { className: 'folder-manager-filter',
        attrs: { type: 'search', placeholder: "Filter the library's folders", 'aria-label': "Filter the library's folders" } });
    remove.filter.addEventListener('input', renderRemoveList);
    remove.list = buildElement('div', { className: 'folder-manager-list' });
    remove.selection = buildElement('span', { className: 'folder-manager-selection' });
    remove.button = button('Remove...', 'btn btn-danger btn-sm', removeChosen);
    manager.status = buildElement('span', { className: 'folder-manager-status', attrs: { role: 'status' } });
    const close = button(String.fromCharCode(0xd7), 'close-btn', closeFolderManager);
    close.setAttribute('aria-label', 'Close');
    const modal = buildElement('div', {
        id: 'folder-manager-modal',
        className: 'modal folder-manager-modal hidden',
        attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'folder-manager-title' },
    }, [
        buildElement('div', { className: 'modal-content folder-manager-content' }, [
            buildElement('div', { className: 'modal-header' }, [
                buildElement('h3', { id: 'folder-manager-title', text: 'Manage folders' }), close]),
            buildElement('div', { className: 'modal-body folder-manager-body' }, [
                buildElement('section', { className: 'folder-manager-part' }, [
                    buildElement('h4', { text: 'Add folders' }),
                    buildElement('p', { className: 'folder-manager-about', text: 'Browse to a folder and tick the ones '
                        + 'under it to add: each is added to the library and queued to be indexed.' }),
                    buildElement('div', { className: 'folder-manager-actions' }, [add.browse, add.parent]),
                    buildElement('div', { className: 'folder-manager-actions' }, [
                        button('All', 'btn btn-secondary btn-sm', () => tickAll(true)),
                        button('None', 'btn btn-secondary btn-sm', () => tickAll(false)),
                        buildElement('label', { className: 'folder-manager-skip' }, [add.hideIndexed,
                            'Hide folders already in the library']),
                    ]),
                    add.list,
                    buildElement('div', { className: 'folder-manager-actions' }, [add.selection, add.queue]),
                ]),
                buildElement('section', { className: 'folder-manager-part' }, [
                    buildElement('h4', { text: 'Remove a folder' }),
                    buildElement('p', { className: 'folder-manager-about', text: 'Takes the photos under the folder out '
                        + 'of the library. ' + REMOVAL_SAYS }),
                    remove.filter,
                    remove.list,
                    buildElement('div', { className: 'folder-manager-actions' }, [remove.selection, remove.button]),
                ]),
            ]),
            buildElement('div', { className: 'modal-footer folder-manager-footer' }, [
                manager.status, button('Close', 'btn btn-secondary', closeFolderManager)]),
        ]),
    ]);
    document.body.append(modal);
    manager.modal = modal;
}

/**
 * Open the dialog on the page's library: its folders read now, nothing browsed yet. `queued(reply)` is called once
 * folders are queued, `removed(folder, reply)` once one is removed -- each after the write, even if the dialog closed.
 */
export function openFolderManager({ queued, removed, welcome } = {}) {
    if (!manager.modal) buildDialog();
    manager.queued = queued || null;
    manager.removed = removed || null;
    manager.open = true;
    manager.token += 1;
    manager.asking = new Set();
    Object.assign(manager.add, { folders: [], ticked: new Set() });
    Object.assign(manager.remove, { folders: [], chosen: null, states: new Map() });
    manager.add.parent.textContent = '';
    manager.remove.filter.value = '';
    note(manager.add.list, (welcome ? welcome + ' ' : '') + 'Browse to the folder that holds the ones to add.');
    note(manager.remove.list, "Reading the library's folders...");
    say('');
    showAddSelection();
    showRemoveSelection();
    manager.modal.classList.remove('hidden');
    holdFocus(manager.modal, { cancel: closeFolderManager, focus: manager.add.browse });
    return loadIndexed();
}

export function closeFolderManager() {
    if (!manager.modal) return;
    manager.modal.classList.add('hidden');
    manager.open = false;
    manager.token += 1;
    clearTimeout(manager.lookTimer);
    releaseFocus(manager.modal);
}
