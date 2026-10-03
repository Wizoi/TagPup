// TagPup's page: which library the page works in, whether it holds the folder open, and
// asking before a folder it does not hold is added to it.
//
// With kr-track selected, the owner opened a folder another library held, on a network
// share outside kr-track's root folders; Suggest made rows for its photos in kr-track
// without asking, and the folder was kr-track's from then on (2026-09-28). Now opening a
// folder the library holds photos in none of the folders of asks: "Add this folder to
// kr-track?", with how many photos it holds and whether it is outside the library's root
// folders. It names only this library: no other library is opened or mentioned. Add adds
// it (POST /api/folder/add); Just look shows its photos and lets the owner edit their
// files (below) without adding it.
//
// Just look is not read-only (2026-10-02): "that should still let me change captions on
// photos, do smart renames, and add/update tags if it does not go into an index". Captions,
// tags, Smart Rename, rotating, deleting and the date changes write the photo FILES and
// nothing of the library's (tagpup.services.file_only); the server decides, photo by photo,
// by whether the library holds the photo's folder -- never by this page.
//
// Suggest works there too (2026-10-03: "do the photo analysis and get the suggestions from the
// index -- but we just do not want to add it to the index"). The server analyses the photos
// against what the library holds, keeps what it finds in memory, and adds nothing to the
// library; applying what it offered (Apply All, a suggestion's chip, carry-forward) writes the
// files only. What stays held back here is what needs a row of the library's: naming faces.
import { api, libraryIn } from './common/api.js';
import { samePath } from './common/paths.js';
import { state } from './state.js';
import { isJustLooking, libraryName } from './looking.js';
import {
    addFolderFacts, addFolderLibrary, addFolderModal, addFolderPath, addFolderTitle,
    btnAddFolder, btnAddFolderFromNote, btnJustLook, justLookingNote,
    justLookingText, libraryNameBadge
} from './elements.js';
import { setStatus } from './status.js';
import { checkIndexingStatus, updateSuggestButtonState } from './suggestions.js';

/**
 * What needs a row of the library's, held back while just looking: naming a face (it makes a
 * person record in the faces table). A click on the faces strip is stopped before any feature
 * hears it. (A caption, a tag, a rename, a turn, a delete, a date, Suggest and applying what it
 * offered are not here: they read the library or write the files, and are allowed.)
 */
const WRITE_CONTROLS = '#faces-strip';

/** Say which library the page works in, in the header, all the time. */
export function showLibraryName() {
    if (!libraryNameBadge) return;
    const name = libraryName();
    libraryNameBadge.textContent = name || 'No library chosen';
    libraryNameBadge.classList.toggle('library-name-none', !name);
    libraryNameBadge.title = name
        ? `Every change on this page goes to ${name}`
        : 'Choose a library to work in';
}

/**
 * Ask what the library holds of the folder just opened, and ask the person to add it
 * when it holds photos in none of its folders -- or in only some. A folder being just
 * looked at is not asked about again. `path` null forgets the folder.
 */
export function checkFolderMembership(path) {
    state.folderMembership = null;
    if (!path) {
        state.justLooking = null;
        applyJustLooking();
        return Promise.resolve(null);
    }
    if (state.justLooking && !samePath(state.justLooking, path)) state.justLooking = null;
    applyJustLooking();
    return api.fetch(`/api/folder/membership?path=${encodeURIComponent(path)}`)
        .then(res => (res && res.ok ? res.json() : null))
        .then(found => {
            if (!state.scannedFolder || !samePath(path, state.scannedFolder)) return null;   // another folder since
            if (!found || typeof found !== 'object' || Array.isArray(found)) return null;
            state.folderMembership = found;
            if (!(found.photos_not_held > 0)) {
                state.justLooking = null;
                applyJustLooking();
            } else if (!isJustLooking()) {
                askToAdd(found);
            }
            return found;
        })
        .catch(err => {
            console.error('Error asking what the library holds of the folder:', err);
            return null;
        });
}

function fact(text, className) {
    const item = document.createElement('li');
    item.textContent = text;
    if (className) item.className = className;
    addFolderFacts.appendChild(item);
}

function plural(count, one, many) {
    return `${count} ${count === 1 ? one : many}`;
}

/** "Add this folder to kr-track?", with what it holds. */
export function askToAdd(found) {
    const name = libraryName();
    const held = found.photos_held || 0;
    addFolderTitle.textContent = '';
    addFolderTitle.appendChild(document.createTextNode(held ? 'Add the rest of this folder to ' : 'Add this folder to '));
    addFolderLibrary.textContent = name;
    addFolderTitle.appendChild(addFolderLibrary);
    addFolderTitle.appendChild(document.createTextNode('?'));
    addFolderPath.textContent = found.folder || state.scannedFolder || '';
    addFolderPath.title = addFolderPath.textContent;

    addFolderFacts.textContent = '';
    if (held) {
        fact(`${name} holds ${held} of its ${plural(found.photos || 0, 'photo', 'photos')}; `
            + `${plural(found.photos_not_held, 'photo', 'photos')} in `
            + `${plural(found.folders_not_held || 1, 'folder', 'folders')} it does not hold would be added.`);
    } else {
        fact(`${plural(found.photos || 0, 'photo', 'photos')}, none of them in ${name}.`);
    }
    if (found.has_roots && !found.under_roots) {
        fact(`Outside ${name}'s root folders.`, 'add-folder-warning');
    }
    if (found.photos_ignored > 0) {
        fact(`${plural(found.photos_ignored, 'photo', 'photos')} in folders ${name} ignores stay out.`,
            'add-folder-note');
    }
    if (found.ignored) {
        fact(`${name}'s settings ignore this folder; adding it keeps it in step all the same.`, 'add-folder-warning');
    }
    fact('Adding it gives each photo a place in the library, indexes it, and keeps it in step from then on. '
        + 'Just look does not add it: tags, captions, renames, rotating, deleting and date changes would be made '
        + 'to the photo files only; Suggest analyses the photos against ' + name + ' without adding them, and face '
        + 'naming stays off.', 'add-folder-note');

    btnAddFolder.textContent = `Add to ${name}`;
    btnAddFolderFromNote.textContent = `Add to ${name}`;
    addFolderModal.classList.add('active');
    btnJustLook.focus();
}

function closeDialog() {
    addFolderModal.classList.remove('active');
}

/** Just look: the photos, edited in their files only, and Suggest analysing them in memory; face naming is held back until added. */
export function justLook() {
    closeDialog();
    state.justLooking = state.scannedFolder || null;
    applyJustLooking();
}

/** Show, or stop showing, that the folder is only being looked at. */
export function applyJustLooking() {
    const looking = isJustLooking();
    document.body.classList.toggle('just-looking', looking);
    if (justLookingNote) {
        justLookingNote.classList.toggle('hidden', !looking);
        const name = libraryName();
        justLookingText.textContent = looking
            ? `Just looking: ${name} does not hold this folder. Tags, captions, renames, rotating, deleting and date `
                + `changes are made to the photo files only, not to ${name}. Suggest analyses the photos against ${name} `
                + `without adding them; nothing is saved to ${name}. Face naming is off until you add the folder.`
            : '';
        btnAddFolderFromNote.textContent = `Add to ${name}`;
    }
    updateSuggestButtonState();
}

/** Add the open folder to the library, as the person asked: POST /api/folder/add. */
export function addFolderToLibrary() {
    const folder = state.scannedFolder;
    if (!folder) return Promise.resolve(null);
    const name = libraryName();
    closeDialog();
    setStatus('busy', `Adding the folder to ${name}...`);
    return api.fetch('/api/folder/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_path: folder }),
    })
        .then(res => res.json().then(data => ({ ok: res.ok, data })))
        .then(({ ok, data }) => {
            if (!ok || !data || !data.success) throw new Error((data && data.error) || 'Adding the folder failed');
            state.justLooking = null;
            if (state.folderMembership) {
                state.folderMembership = {
                    ...state.folderMembership,
                    photos_not_held: 0, folders_not_held: 0, first_not_held: null,
                };
            }
            applyJustLooking();
            setStatus('ready', `Added to ${name}; indexing it now.`);
            checkIndexingStatus(folder);
            return data;
        })
        .catch(err => {
            console.error(err);
            setStatus('error', 'Error', { transient: false });
            alert(`Could not add the folder to ${name}: ${err.message}`);
            return null;
        });
}

function stop(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
    if (justLookingNote) {
        justLookingNote.classList.remove('just-looking-flash');
        void justLookingNote.offsetWidth;
        justLookingNote.classList.add('just-looking-flash');
    }
}

/**
 * The dialog's buttons, the header's name, and the guard that holds back what needs a row of
 * the library's while just looking: on the window, capturing, so it hears a click before any
 * feature's own listener on the document does.
 */
export function wireMembership() {
    showLibraryName();
    btnAddFolder.addEventListener('click', addFolderToLibrary);
    btnAddFolderFromNote.addEventListener('click', addFolderToLibrary);
    btnJustLook.addEventListener('click', justLook);
    addFolderModal.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            e.preventDefault();
            justLook();
        }
    });
    window.addEventListener('click', (e) => {
        if (!isJustLooking()) return;
        const target = e.target && e.target.closest ? e.target.closest(WRITE_CONTROLS) : null;
        if (target) stop(e);
    }, true);
}
