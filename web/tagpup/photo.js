// TagPup's page: the open photo -- its details, tags, faces and zoom, carrying tags
// forward, opening, rotating and deleting it.
import { api } from './common/api.js';
import { buildElement, replaceContent } from './common/dom.js';
import { photoFileUrl } from './common/file-addresses.js';
import { openImageZoom, wireImageZoom } from './common/image-zoom.js';
import { attachPersonFaces } from './common/person-faces.js';
import { pressable } from './common/press.js';
import { baseName, isUnc, samePath } from './common/paths.js';
import {
    personLabelOf, personTitleOf, photoAlreadyHas, sortedTags,
} from './common/vocabulary.js';
import { upper } from './hooks.js';
import { clearPhotoFaces, nameFaceAs, showPhotoFaces } from './face-boxes.js';
import { state } from './state.js';
import {
    btnCarryForward,
    detailCamera, detailCameraItem, detailDateTaken, detailPeople, detailsPanel, detailSize, detailTags,
    btnAddAllFaces, emptyState, facesSection, facesStrip, facesSummary, folderViewContent,
    folderViewStats, inputAddPerson, inputAddTag,
    inputPhotoTitle, mainImage, panelContent, photoList, statusDot, statusText
} from './elements.js';
import { setStatus } from './status.js';
import { isJustLooking, libraryName } from './looking.js';
import { pictureCheckSaid, whereWritten } from './write-queue.js';
import { photoDeleted, photosGone, recordsWritten } from './cache.js';
import { fileSizeSaid, takenOf, takenSaid } from './format.js';
import { namesAPerson, normalizeTag } from './tags.js';
import { deleteWaits, pendingOf, readHeld, removePhotoTags, shownTags } from './pending.js';
import {
    leavePhotoThen, photoChangedOnDisk, queueWriteOf, redrawIfShowing, saveDetailEdits,
    updateSaveButton
} from './edits.js';
import { postPhotoMetadata } from './common/photo-write.js';
import {
    countSaid, markOpenRow, nextOf, renderFileList, scanFolder, showFolderView, updateListStats, updatePhotoPosition,
} from './folder.js';
import { removeFromSelection } from './selected.js';
import { applyPhotoStale, holdDeletes } from './stale.js';
import { bigClip, forGoodQuestion } from './bulk-words.js';
import { isVideo, showMedia, stopVideo } from './media.js';
import { fetchLibraryRecord, forgetPhoto, libraryIdOfPath, refetchCards } from './library-source.js';
import { backToView, notePhoto, rememberView } from './view-left.js';
import { showSmartRenameParts } from './rename-one.js';
import { showPhotoPath } from './photo-path.js';
import { bulkBusy, busySentence, lockBulkControls } from './bulk-run.js';

// ---- Detected faces ----------------------------------------------------
// Face recognition already ran for this photo -- the suggester needs it to propose
// people -- but nothing ever showed it. Without the crops you learn that a face went
// unrecognised only later, in TagTuner. This puts that in front of you while tagging.
// facesRequestToken is declared with the other state at the top; see the note there.

export function renderPhotoFaces(photoPath) {
    if (!facesSection || !facesStrip) return;
    const token = ++state.facesRequestToken;

    // The same photo read again (a tag written, a face named or left out) keeps its strip, its boxes and its panel until the
    // new answer replaces them in one step (drawPhotoFaces): no "Loading", no empty strip in between (owner, 2026-11-15).
    // Another photo's are cleared at once, and the section says it is loading: an empty strip looked like a photo with no faces.
    const again = holdsFacesOf(photoPath);
    if (!again) {
        if (btnAddAllFaces) btnAddAllFaces.classList.add('hidden');
        if (facesSummary) facesSummary.textContent = '';
        sayAboutFaces(FACES_LOADING);
        clearPhotoFaces();
    }

    api.fetch(`/api/photo-faces?path=${encodeURIComponent(photoPath)}`)
        .then(res => {
            if (!res.ok) throw new Error(`the server answered ${res.status}`);
            return res.json();
        })
        .then(data => {
            // A slower reply for a previously selected photo, or an earlier read of this one, must not overwrite
            // the strip the later request will draw.
            if (token !== state.facesRequestToken) return;
            drawPhotoFaces(photoPath, data);
            // A save that touched a face while this was read was not followed (the faces were not here yet): it is now,
            // unless a naming is under way, whose end follows it (face-boxes.js notBusy).
            const box = state.faceBoxes;
            if (box.owed && !box.busy) {
                box.owed = false;
                renderPhotoFaces(photoPath);
            }
        })
        .catch(err => {
            console.error('Error loading faces:', err);
            if (token !== state.facesRequestToken) return;
            if (holdsFacesOf(photoPath)) {
                // The faces shown are this photo's still, maybe out of date: they stay, and a line says so.
                sayAboutFaces(FACES_NOT_REFRESHED, { keep: true });
                return;
            }
            clearPhotoFaces();
            sayAboutFaces(FACES_FAILED);
        });
}

/** Are the faces on the page (the boxes, and the strip drawn with them) this photo's? */
function holdsFacesOf(photoPath) {
    const box = state.faceBoxes;
    return Boolean(box.path) && samePath(box.path, photoPath);
}

export const FACES_LOADING = 'Loading faces...';
export const FACES_FAILED = 'The faces of this photo could not be loaded. Select the photo again to try once more.';
export const FACES_NOT_REFRESHED = 'The faces of this photo could not be refreshed. Select the photo again to try once more.';

/**
 * The faces section holding only a line of words: it is loading, or it failed (never an empty strip). With `keep`, the cards
 * shown stay and the line goes under them, in place of any line before it (a read again that failed).
 */
function sayAboutFaces(words, { keep = false } = {}) {
    if (keep) facesStrip.querySelectorAll('.faces-note').forEach(old => old.remove());
    else facesStrip.innerHTML = '';
    const note = document.createElement('div');
    note.className = 'faces-note';
    note.setAttribute('role', 'status');
    note.textContent = words;
    facesStrip.appendChild(note);
    facesSection.classList.remove('hidden');
}

/**
 * The faces of the photo drawn: the boxes over it and the strip below. `data` is what /api/photo-faces answered, or the
 * page's own copy of it (renderPhotoFacesHere).
 */
function drawPhotoFaces(photoPath, data) {
    const faces = data.faces || [];
    showPhotoFaces(photoPath, data);

    // Who this photo already names, so a face is only offered when acting
    // on it would change something.
    const photoRecord = state.folderPhotos.find(p => p.path === photoPath);
    // The cards are made aside and put in place in one step, with the section, the summary and Add all: the strip on screen
    // stays as it was until then, so a photo read again does not blink empty.
    const cards = [];

    // The faces a click would name, in the strip's order: what Add all goes through.
    const actionable = [];
    faces.forEach(face => {
        // A face nobody is like has no card: it offers nothing to click. The face icon's boxes on the
        // photo are where a missing person's face is found and named (owner, 2026-11-02).
        if (!face.excluded && !face.name && !face.suggestion) return;
        const card = document.createElement('div');
        card.className = 'face-card';
        card.dataset.faceId = String(face.id);
        if (face.excluded) card.classList.add('excluded');
        else if (!face.name) card.classList.add('unmatched');

        // The crop and anything drawn over it share a box, so the
        // confidence can sit on the picture instead of competing with the
        // name for a 72px label that ellipsises either way.
        const frame = document.createElement('div');
        frame.className = 'face-card-frame';

        const img = document.createElement('img');
        img.className = 'face-card-img';
        img.src = api.image(`/api/face-crop?id=${face.id}`);
        img.alt = face.name ? personLabelOf(face) : 'Unidentified face';
        frame.appendChild(img);

        // How sure the match is, in the corner of the crop. It used to be
        // appended to the name inside the label, where "Saskia Wren? 72%"
        // truncated to "Saskia Wren? ..." and the number -- the part that
        // decides whether to trust it -- was the first thing lost.
        if (!face.excluded && face.suggestion && face.similarity) {
            const badge = document.createElement('span');
            badge.className = 'face-card-confidence';
            badge.textContent = `${Math.round(face.similarity * 100)}%`;
            frame.appendChild(badge);
        }
        card.appendChild(frame);

        // Who the face is, or who it looks like, as a person (their id) when the server named one: two people called
        // alike are two cards, each under its label.
        const suggestionPerson = face.suggestion_person && face.suggestion_person.id !== null
            && face.suggestion_person.id !== undefined ? face.suggestion_person : null;
        const suggestedWho = suggestionPerson || face.suggestion;
        const suggestedLabel = suggestionPerson ? suggestionPerson.name : face.suggestion;
        const facePerson = face.person && face.person.id !== null && face.person.id !== undefined ? face.person : null;
        const label = document.createElement('span');
        label.className = 'face-card-label';
        if (face.excluded) {
            label.textContent = face.excluded_reason || 'excluded';
            card.title = 'Excluded from face matching';
        } else if (face.name) {
            label.textContent = personLabelOf(face);
            card.title = personTitleOf(face);
        } else if (face.suggestion) {
            const pct = Math.round((face.similarity || 0) * 100);
            // The percentage lives on the crop now; repeating it here is
            // what pushed the name into an ellipsis.
            label.textContent = `${suggestedLabel}?`;
            card.title = `Closest match: ${suggestedLabel} (${pct}%). Not assigned.`;
            label.classList.add('face-card-suggestion');
            card.tabIndex = 0;
            attachPersonFaces(card, suggestedWho);
        }
        card.appendChild(label);

        // Clicking a face names it that person AND puts the person on the
        // photo, as a box's panel does (nameFaceAs: the tag first, then the
        // face, one function for both, #860/#861). It used to add the tag
        // only, so a face "named" from the strip stayed a red box that the
        // owner had named, in a copy of the answer nothing refreshed.
        //
        // A recognised face counts as much as a proposed one: its click adds
        // the person to the photo when the photo lacks them. And a proposed
        // one whose person the photo has already is still clickable, to name
        // the face. Only a card that would change nothing is settled.
        const namesSomebody = face.name ? (facePerson || face.name) : suggestedWho;
        const shownSomebody = face.name ? personLabelOf(face) : suggestedLabel;
        // As the photo shows its tags (pending.js shownTags): a person whose pill was just taken off is offered again.
        const alreadyTagged = namesSomebody
            && photoAlreadyHas(photoRecord && { tags: shownTags(photoRecord) },
                typeof namesSomebody === 'object' ? namesSomebody.tag : namesSomebody, namesAPerson, state.people);

        if (namesSomebody && !face.excluded && (!alreadyTagged || !face.name)) {
            card.classList.add('face-card-actionable');
            // Keep what the card already said -- the closest match and how
            // sure it is -- and add what pressing it does. Replacing it
            // threw away the reading somebody hovers to check.
            const does = face.name ? `add ${shownSomebody} to this photo`
                : alreadyTagged ? `name this face ${shownSomebody}`
                    : `name this face ${shownSomebody} and add them to this photo`;
            card.title = `${card.title || namesSomebody}`
                + `
Click to ${does}.`;
            card.addEventListener('click', () => {
                nameFaceAs(face, namesSomebody);
            });
            pressable(card);
            actionable.push({ face, who: namesSomebody });
        } else if (alreadyTagged) {
            // Not clickable, and saying so beats a card that looks live
            // and does nothing when pressed.
            card.classList.add('face-card-settled');
            card.title = `${shownSomebody} is already tagged on this photo`;
        }
        cards.push(card);
    });
    const unmatched = data.unmatched || 0;
    const focused = focusedFace();
    facesStrip.replaceChildren(...cards);
    facesSection.classList.toggle('hidden', faces.length === 0);
    if (facesSummary) {
        facesSummary.textContent = !faces.length ? ''
            : unmatched ? `${faces.length} detected, ${unmatched} unidentified` : `${faces.length} detected, all identified`;
    }
    if (focused !== null) focusFaceAgain(focused);
    if (btnAddAllFaces) btnAddAllFaces.classList.toggle('hidden', actionable.length === 0);
    if (btnAddAllFaces && actionable.length > 0) {
        btnAddAllFaces.onclick = async () => {
            // The people on the photo in one write of the file; then each face is named (the database only, no file),
            // one at a time, and not on into another photo.
            await upper.applySuggestedTagsDirect(actionable.map(({ who }) => (typeof who === 'object' ? who.tag : who)),
                true, photoPath);
            for (const { face, who } of actionable) {
                if (!state.activePhotoPath || !samePath(state.activePhotoPath, photoPath)) break;
                if (!(await nameFaceAs(face, who))) break;
            }
        };
    }
}

/** The face whose card in the strip has the keyboard's focus, by its id; null when the focus is elsewhere. */
function focusedFace() {
    const card = facesStrip.querySelector('.face-card:focus');
    return card && card.dataset.faceId ? card.dataset.faceId : null;
}

/**
 * The focus given back to the same face's card in the strip drawn again; to the strip's first card that takes it when that
 * card takes it no more (a recognised face's "add them to this photo" pressed is settled, with no tab stop).
 */
function focusFaceAgain(faceId) {
    const same = Array.from(facesStrip.children).find(card => card.dataset.faceId === faceId);
    const card = same && same.hasAttribute('tabindex') ? same : facesStrip.querySelector('.face-card[tabindex]');
    if (card) card.focus({ preventScroll: true });
}

/**
 * The open photo's faces drawn again from the page's own copy (the boxes' `faces`), with no request: a face named here is
 * shown named at once, before the server has written it (face-boxes.js nameFaceAs), and shown as it was if the write fails.
 * `readStands`: only the tags it shows changed (pending.js pageFollows), and a read of its faces under way still draws.
 */
export function renderPhotoFacesHere(photoPath, { readStands = false } = {}) {
    const box = state.faceBoxes;
    if (!facesSection || !facesStrip || !box.path || !samePath(box.path, photoPath)) return;
    // A reply to an earlier request is no longer the picture.
    if (!readStands) state.facesRequestToken += 1;
    drawPhotoFaces(photoPath, {
        faces: box.faces, shown_size: box.size, unmatched: box.faces.filter(each => !each.name && !each.excluded).length,
    });
}

// Select Single Photo View
export function selectPhoto(path) {
    // Every way to another photo comes through here -- rows, the grid, the
    // context menu, the arrow keys and swipes -- so this is where it asks.
    if (path === state.activePhotoPath) showPhoto(path);
    else leavePhotoThen(() => openPhoto(path));
}

/** Show a photo: a folder's is in the page; a library view's is read from the library first. */
function openPhoto(path) {
    if (!state.library) {
        showPhoto(path);
        return;
    }
    const held = state.folderPhotos.find(p => p.path === path);
    if (held) {
        state.library.activeId = held.id;
        showPhoto(path);
        notePhoto(held.id);   // the same photo opened again after Back: its place says which, for Forward (#867)
        return;
    }
    const id = libraryIdOfPath(path);
    if (id !== null) openLibraryPhoto(id);
}

/**
 * Put a library photo's record in the panel: the page's only full record.
 */
/** A library photo's record, read held against the tag writes out meanwhile (pending.js readHeld). */
const readLibraryRecord = (id) => readHeld(() => fetchLibraryRecord(id));

function showLibraryRecord(lib, record) {
    state.folderPhotos = [record];
    lib.activeId = record.id;
    lib.previous = null;
    showPhoto(record.path);
    notePhoto(record.id);
    readPreviousOf(lib, record.id);
}

/**
 * The view holds only the open photo, so the one before it in the view's order (a photo or a clip, whatever it is) is read
 * from the library when the open one is shown, for "Same as previous" (finding #2832: it was never offered in a view). Said
 * only while that photo is still the open one; a photo the library no longer has is dropped by fetchLibraryRecord.
 */
function readPreviousOf(lib, id) {
    const index = lib.ids.indexOf(id);
    if (index <= 0) return;
    readLibraryRecord(lib.ids[index - 1]).then(record => {
        if (!record || lib !== state.library || lib.activeId !== id) return;
        lib.previous = { of: id, record, from: record.filename || baseName(record.path) };
        updateCarryForwardState();
    }).catch(err => console.error('Could not read the photo before this one:', err));
}

/**
 * The photo's file changed since the page read it and a write was refused (edits.js photoChangedOnDisk): read it
 * again, and show what the file holds now. A library view's photo is read from the library by id; a folder's, by
 * reading the folder again.
 */
export function reloadChangedPhoto(photo, after = () => {}) {
    const lib = state.library;
    if (lib && photo.id !== undefined) {
        return readLibraryRecord(photo.id).then(record => {
            if (!record || lib !== state.library) return;
            if (state.activePhotoPath === photo.path) showLibraryRecord(lib, record);
            else state.folderPhotos = [record];
            upper.renderThumbnails();
            after();
        }).catch(err => console.error('Could not read the photo again:', err));
    }
    state.afterScan = after;
    return scanFolder(true, { keepTyped: true });
}

/**
 * Open the photo `id` of the library view in the details panel. The panel and every edit work on a record
 * by path (as a folder's photos are); a library view's photo is read whole from the library when it is
 * opened, and the page holds it alone. A photo the library no longer has is dropped from the view.
 */
export function openLibraryPhoto(id) {
    const lib = state.library;
    if (!lib) return Promise.resolve(false);
    lib.openToken += 1;
    lib.wantedId = id;
    const token = lib.openToken;
    const done = () => { if (token === lib.openToken) lib.wantedId = null; };
    return readLibraryRecord(id).then(record => {
        if (!record || lib !== state.library || token !== lib.openToken) {
            done();
            return false;
        }
        leavePhotoThen(() => {
            done();
            if (lib !== state.library || token !== lib.openToken) return;
            showLibraryRecord(lib, record);
        }, { onStay: done });
        return true;
    }).catch(err => {
        done();
        console.error(err);
        setStatus('error', `Could not open the photo: ${err.message}`, { transient: false });
        return false;
    });
}

export function showPhoto(path) {
    // Typed text belongs to the photo it was typed for. By now it has been
    // saved or discarded, but a field is never carried to another photo.
    if (path !== state.activePhotoPath) {
        inputAddTag.value = '';
        inputAddPerson.value = '';
    }
    state.activePhotoPath = path;

    // Highlight sidebar element: it takes the list's Tab
    markOpenRow(photoList.querySelector(`li[data-path="${CSS.escape(path)}"]`));

    // A library view's grid is about to be hidden: keep where it is, and make the photo a place in the history (view-left.js).
    rememberView();

    // Hide folder view, show single details view
    folderViewContent.classList.add('hidden');
    emptyState.classList.add('hidden');
    panelContent.classList.remove('hidden');

    // Start at the top. The panel scrolls, and it used to keep its position
    // across a change of photo, so moving on while reading the tag fields left
    // you looking at another photo's fields with its image off-screen above.
    if (detailsPanel) detailsPanel.scrollTop = 0;
    updateCarryForwardState();
    updatePhotoPosition();

    renderPhotoFaces(path);

    // Fetch photo data from local array
    const photo = state.folderPhotos.find(p => p.path === path);
    // A photo whose file is gone, or with tag changes waiting, cannot be edited (stale.js); any other is editable.
    applyPhotoStale(photo, pendingOf(photo));
    if (!photo) return;

    // Render values. A photo whose file is gone has no picture to ask for; a video is played in the image's place (media.js).
    showMedia(photo, photo.missing ? '' : photoFileUrl(photo, 800));
    showPhotoPath(photo);
    detailSize.textContent = fileSizeSaid(photo.size);
    detailDateTaken.textContent = takenSaid(takenOf(photo));
    showCamera(photo);
    // The same photo shown again -- a refresh -- keeps a title being typed.
    const typing = state.titleShown.path === path && inputPhotoTitle.value !== state.titleShown.title;
    if (!typing) inputPhotoTitle.value = photo.title || '';
    state.titleShown = { path, title: photo.title || '' };
    showSmartRenameParts(path);
    updateSaveButton();

    renderTags();
    upper.renderSuggestionsPanel(photo.path);
}

/**
 * The camera and the lens a photo was taken with, on one line ("Tidewater TX R6m2 · TX24-70mm f/2.8L"), as the server names
 * them (photo_meta.gear); the line is not shown for a photo whose metadata names neither (a scan, a screenshot).
 */
export function showCamera(photo) {
    const line = [photo.camera, photo.lens].filter(Boolean).join(' · ');
    detailCamera.textContent = line;
    detailCamera.title = line;
    detailCameraItem.classList.toggle('hidden', !line);
}

/**
 * Whichever photo sits before this one in the list: `{ record, from }`, or null.
 *
 * "Before" is list order rather than a history of what you visited, because that
 * is what matches the way a shoot is worked: down the folder, in order. Reaching
 * backwards from the first photo yields nothing rather than wrapping around.
 */
function previousPhoto() {
    // The photo before this one in a library view is not in the page: readPreviousOf read it when this one was shown.
    const lib = state.library;
    if (lib) {
        const held = lib.previous;
        return held && held.of === lib.activeId ? held : null;
    }
    const items = Array.from(photoList.querySelectorAll('.photo-item-file'));
    const index = items.findIndex(
        el => el.getAttribute('data-path') === state.activePhotoPath
    );
    if (index <= 0) return null;
    const prevPath = items[index - 1].getAttribute('data-path');
    const prev = state.folderPhotos.find(p => p.path === prevPath);
    return prev ? { record: prev, from: prev.filename || baseName(prevPath) } : null;
}

/** What `photo` (`{ record, from }`, previousPhoto) would carry over: the tags it shows (pending.js shownTags). */
function carriedFrom(before) {
    return before ? { tags: shownTags(before.record), from: before.from } : { tags: [], from: null };
}

/** The tags the photo before this one shows. */
export function previousPhotoTags() {
    return carriedFrom(previousPhoto());
}

/** Is there anything to copy forward onto the current photo? Both as shown: a tag taken off and not written yet is gone. */
export function carryForwardCandidates() {
    const { tags, from } = previousPhotoTags();
    const photo = state.folderPhotos.find(p => p.path === state.activePhotoPath);
    if (!photo) return { missing: [], from };
    const have = new Set(shownTags(photo));
    return { missing: tags.filter(t => !have.has(t)), from };
}

/**
 * Copy the previous photo's tags onto this one.
 *
 * Additive on purpose: tags already here are kept, and only what is missing is
 * added. Copying as a replacement would quietly undo work on a photo that had
 * been partly tagged already, which is precisely the photo you are most likely
 * to be standing on.
 */
export function carryTagsForward() {
    const path = state.activePhotoPath;
    if (!path) return;
    const photo = state.folderPhotos.find(p => p.path === path);
    // Read-only while other tag changes wait for it (stale.js offerWrites makes the button inert; the key is answered here).
    if (!photo || pendingOf(photo)) return;

    // The photo before is taken at the press; what it carries, at the write's turn, as it shows then (a tag its pill took
    // off is not copied, whether that removal is written by then or not).
    const before = previousPhoto();
    const from = before && before.from;
    const nothingToCarry = () => setStatus('ready', from
        ? `Nothing to carry over from ${from}`
        : 'No previous photo to carry tags from');
    if (!carryForwardCandidates().missing.length) {
        nothingToCarry();
        return;
    }

    return queueWriteOf(path, async () => {
        // What is missing now, not when the key was pressed: a write queued
        // ahead of this one may have added some of it, or removed others.
        const held = (photo.tags || []).slice();
        const have = new Set(shownTags(photo));
        const missing = carriedFrom(before).tags.filter(t => !have.has(t));
        if (!missing.length) {
            nothingToCarry();
            return true;
        }
        const updatedTags = Array.from(new Set([...held, ...missing]));
        setStatus('busy', `Copying ${missing.length} tag(s) from ${from}...`);
        let saved;
        try {
            saved = await postPhotoMetadata(photo, { tags: updatedTags }, photoChangedOnDisk);
        } catch (err) {
            console.error(err);
            setStatus('error', 'Could not copy tags: ' + err.message);
            return false;
        }
        photo.tags = updatedTags;
        upper.recordUndo({
            label: `carry ${missing.length} tag(s) from ${from}`,
            photos: [{ path: photo.path, before: held, after: updatedTags }],
        });
        recordsWritten([photo]);
        redrawIfShowing(photo, saved);
        renderFileList();
        upper.renderThumbnails();
        setStatus('ready', `Copied ${missing.length} tag(s) from ${from}`);
        return true;
    });
}

/** Keep the carry-forward button honest about what it would do. */
export function updateCarryForwardState() {
    if (!btnCarryForward) return;
    const { missing, from } = carryForwardCandidates();
    btnCarryForward.disabled = missing.length === 0;
    btnCarryForward.title = !from
        ? 'No previous photo in the list to copy from'
        : missing.length
            ? `Copy ${missing.length} tag(s) from ${from}  (Ctrl+D)`
            : `${from} has no tags this photo is missing`;
}

// ---- Zoom ---------------------------------------------------------------
//
// Click the photo to see it as large as the window allows -- for reading the
// writing on a sign or a name tag -- and click again, or Escape, to put it back.
// The zoom is web/common/image-zoom.js, the one both pages use; what is TagPup's is
// which file it shows: the 800px preview the panel holds is the zoom's first frame,
// and the original (no size: the file as it is on disk) paints over it.
//
// Opening it is not leaving the photo: nothing in the panel changes, so it neither
// asks about unsaved edits nor makes any. While it is open the arrow keys do
// nothing, rather than change the photo underneath it.
export function openZoom() {
    if (!state.activePhotoPath || !mainImage.getAttribute('src')) return;
    const preview = mainImage.src;               // absolute, database prefix included
    const original = new URL(preview, window.location.href);
    original.searchParams.delete('size');      // no size: the file as it is on disk
    openImageZoom(original.href, { preview, opener: mainImage });
}

export function wireZoom() {
    wireImageZoom();
    mainImage.addEventListener('click', () => {
        if (mainImage.dataset.dragged === 'true') {
            delete mainImage.dataset.dragged;  // that was a swipe
            return;
        }
        openZoom();
    });
    pressable(mainImage);                      // Enter or Space on the focused picture opens it too
}

/**
 * The open photo's tags and people as pills: the tags it shows (pending.js shownTags: a tag taken off and not written yet
 * is not drawn, whatever a write queued before it answers meanwhile). The pills are alphabetical (sortedTags); the photo's
 * own list is not touched, so a save sends the tags in the order the file holds them.
 */
export function renderTags() {
    // A pill with the focus keeps it through a redraw (a write's reply), by its tag: it was lost to the page.
    const focused = document.activeElement;
    const keep = focused && focused.classList && focused.classList.contains('tag-pill')
        && (detailPeople.contains(focused) || detailTags.contains(focused)) ? focused.textContent : null;
    detailPeople.innerHTML = '';
    detailTags.innerHTML = '';

    const photo = state.folderPhotos.find(p => p.path === state.activePhotoPath);
    const photoPeople = (photo && photo.people) ? photo.people : [];
    const tags = photo ? shownTags(photo) : null;
    
    const peopleTags = [];
    const nonPeopleTags = [];
    
    if (tags) {
        tags.forEach(tag => {
            const isPerson = namesAPerson(tag);
            if (isPerson) {
                peopleTags.push(tag);
            } else {
                nonPeopleTags.push(tag);
            }
        });
    }

    if (peopleTags.length === 0) {
        replaceContent(detailPeople, buildElement('span', {
            style: 'color: var(--text-muted); font-size: 13px;', text: 'No people tags.',
        }));
    } else {
        sortedTags(peopleTags).forEach(tag => {
            const pill = document.createElement('span');
            pill.className = 'tag-pill';
            pill.style.cursor = 'pointer';
            pill.title = 'Click to remove person';
            pill.textContent = tag;
            pill.addEventListener('click', () => takeOffPill(pill, tag, inputAddPerson));
            detailPeople.appendChild(pressable(pill));
        });
    }

    if (nonPeopleTags.length === 0) {
        replaceContent(detailTags, buildElement('span', {
            style: 'color: var(--text-muted); font-size: 13px;', text: 'No keywords set.',
        }));
    } else {
        sortedTags(nonPeopleTags).forEach(tag => {
            const pill = document.createElement('span');
            pill.className = 'tag-pill';
            pill.style.cursor = 'pointer';
            pill.title = 'Click to remove tag';
            pill.textContent = tag;
            pill.addEventListener('click', () => takeOffPill(pill, tag, inputAddTag));
            detailTags.appendChild(pressable(pill));
        });
    }
    if (keep !== null) {
        const again = [...detailPeople.children, ...detailTags.children].find(each => each.textContent === keep
            && each.classList.contains('tag-pill'));
        if (again) again.focus({ preventScroll: true });
    }
}

/**
 * A pill clicked or pressed: its tag taken off. The focus, when it was on the pill, goes to the next pill of its list (the one
 * before when it was the last), or to the list's add box when none is left: not to the page, where Tab starts again.
 */
function takeOffPill(pill, tag, addBox) {
    const list = pill.parentElement;
    const pills = Array.from(list.querySelectorAll('.tag-pill'));
    const at = pills.indexOf(pill);
    const next = document.activeElement === pill ? (pills[at + 1] || pills[at - 1] || null) : undefined;
    deletePhotoTag(tag);
    if (next === undefined) return;
    const again = next && Array.from(list.querySelectorAll('.tag-pill')).find(each => each.textContent === next.textContent);
    (again || addBox).focus({ preventScroll: true });
}

// Single Photo Actions
//
// Enter in a field, its Add button and the title's own Save each write just that
// field, straight away. They go through saveDetailEdits, the one place that
// resolves typed names, merges them into the photo's tags and writes -- these
// were three near-copies of it, and the header's Save would have been a fourth.
export function saveSingleTitle() {
    return saveDetailEdits({ title: true });
}

export function saveSingleAddPerson() {
    return saveDetailEdits({ people: true });
}

export function saveSingleAddTag() {
    return saveDetailEdits({ tags: true });
}

export function deletePhotoTag(tagToRemove) {
    return removePhotoTags(state.activePhotoPath, [tagToRemove]);
}

/**
 * The open photo's faces, read again after its tags were written (the one hook of every write of them: a save, a chip, Apply
 * All, an Undo, a pill taken off, a face's card), and only when the write changed one of them; the one place that decides it.
 * The server names a face when a person put on the photo is its one face to be named (#788), takes a name off when the person
 * goes (#908), and names it again when they come back, and its reply says which: `named_faces`, `unnamed_faces`,
 * `renamed_faces` ([{id, name}] each; tagpup/web/tagpup_routes.py _faces_unnamed). The page's copy of the faces, which the
 * boxes and the strip are drawn from, is read again when a face it holds is listed; an undo's replies, of other photos too,
 * are given together. A reply while a naming of this page's is under way, or while this photo's faces are still being read,
 * is owed: the naming's end (face-boxes.js notBusy) or the read's answer (renderPhotoFaces) reads them again.
 */
export function facesFollowReply(path, replies) {
    const box = state.faceBoxes;
    if (!path || !samePath(state.activePhotoPath, path)) return;
    const listed = [].concat(replies || []).flatMap(reply => (reply ? [
        ...(reply.named_faces || []), ...(reply.unnamed_faces || []), ...(reply.renamed_faces || []),
    ] : []).map(face => face.id));
    if (!listed.length) return;
    if (!holdsFacesOf(path)) {
        // Not drawn yet, so whose faces were listed cannot be told: the read under way may be older than the write.
        box.owed = true;
        return;
    }
    const held = new Set(box.faces.map(face => face.id));
    if (!listed.some(id => held.has(id))) return;
    if (box.busy) box.owed = true;
    else renderPhotoFaces(path);
}

// The path in Image Details opens the photo in the app Windows uses for it.
// It used to ask Explorer to select the file, with the whole "/select,<path>"
// switch quoted -- which Explorer does not parse as a selection, so what it did
// depended on how it handled the malformed argument. Opening the file is what
// clicking it was for; "Show in File Explorer" is on the right-click menu.
export function openPhotoInDefaultApp() {
    const path = state.activePhotoPath;
    if (!path) return;

    statusDot.className = 'status-indicator-dot busy';
    statusText.textContent = 'Opening photo...';

    api.json('/api/photo/open', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path })
    })
    .then(data => {
        if (!data.success) throw new Error(data.error || 'could not open the photo');
        statusDot.className = 'status-indicator-dot';
        statusText.textContent = 'Ready';
    })
    .catch(err => {
        console.error(err);
        statusDot.className = 'status-indicator-dot';
        statusText.textContent = 'Could not open the photo: ' + err.message;
    });
}

// PIL rotation trigger
export function rotatePhoto(direction) {
    const path = state.activePhotoPath;
    if (!path) return;

    // A rotate is a write of the photo: it waits its turn behind a save of it, which would otherwise name the stamp from
    // before the turn and be refused as changed on disk (#2587); leaving the photo waits for it too.
    return queueWriteOf(path, async () => {
        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Rotating...';
        try {
            const data = await api.json('/api/photo/rotate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path, direction })
            });
            if (!data.success) throw new Error(data.error || 'Failed to rotate');
            // The photo's new mtime is its images' new URL, here and in the grid; the route answers the file's stamp
            // with every rotation it made, so the next save names it.
            const photo = state.folderPhotos.find(p => samePath(p.path, path)) || { path };
            photo.mtime = data.mtime;
            photo.size = data.size;
            // A stamp the route could not look at in time (a share slow to answer) comes as null: the photo is read
            // again, as after a save refused as changed on disk, so the next save names the file's real stamp
            // rather than none (which the save refuses as a malformed request).
            if (data.mtime == null) reloadChangedPhoto(photo);
            mainImage.src = photoFileUrl(photo, 800);
            // The faces' boxes turned with the file; the strip and the boxes ask again.
            renderPhotoFaces(path);
            // A library view's card has the file's old stamp in its thumbnail's address: asked for again.
            if (state.library && photo.id !== undefined) refetchCards([photo.id]);
            const thumb = document.querySelector(
                `#thumbnails-grid [data-path="${CSS.escape(path)}"] img`);
            if (thumb) {
                // The grid asks for a picture from data-src (vgrid.js); a card drawn later reads the new mtime.
                thumb.dataset.src = photoFileUrl(photo, 300);
                thumb.src = thumb.dataset.src;
            }
            // Every record of it the page holds or keeps names the new stamp; a stamp not looked at, none: the scans kept
            // holding it are forgotten, and scanned when opened again.
            if (data.mtime == null) photosGone([path]);
            else recordsWritten([photo]);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = data.file_only || data.pictures_refused
                ? `Rotated.${whereWritten(data)}${pictureCheckSaid(data)}` : 'Ready';
            return true;
        } catch (err) {
            console.error(err);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
            alert("Error rotating image: " + err.message);
            return false;
        }
    }, `Rotate photo ${baseName(path)}`);
}

// Move active photo to Windows Recycle Bin and update state
export function deleteActivePhoto() {
    const path = state.activePhotoPath;
    if (!path) return;
    // One delete at a time on a page: not while the selection's delete (or any bulk edit), or this one, is written.
    if (bulkBusy()) {
        alert(busySentence());
        return;
    }

    const index = state.folderPhotos.findIndex(p => p.path === path);
    if (index === -1) return;
    // Not while a tag change for it waits or is out, the selection's Delete's rule (pending.js).
    if (deleteWaits([path])) return;

    // The record asked about, in hand: by the time the reply comes the folder may be another (findings #532).
    const asked = state.folderPhotos[index];
    // And where it was asked: the view or folder open now may be another by then, and its records are not this photo's.
    const askedInView = state.library;
    const filename = asked.filename || 'this photo';
    // A photo of a folder the library does not hold: say that only the file moves. And on a network
    // share there is no Recycle Bin: it goes through this PC's (#694), and where it restores to is said before it goes.
    const alone = isJustLooking()
        ? `\n\n${libraryName()} does not hold this folder: only the file is deleted, and nothing in ${libraryName()} changes.`
        : '';
    const known = state.folderMembership && state.folderMembership.permanent_delete
        ? (state.folderMembership.permanent_reason || 'on a drive without a Recycle Bin') : '';
    const reason = known || (isUnc(path) ? 'on a network share' : '');
    // A clip over 2 GiB is deleted for good on any drive: said first, before the server names it (forGoodQuestion) to be confirmed.
    const question = isVideo(asked) && bigClip(asked.size)
        ? `"${filename}" is a video over 2 GiB: it is deleted for good, not moved to a Recycle Bin and not copied to this PC (too large for TagPup to copy to one). `
            + 'You are asked once more, naming it, before anything is deleted.\n\nDelete it for good?'
        : reason
        ? `This file is ${reason}, where there is no Recycle Bin: it is copied to this PC, the copy goes to this PC's Recycle Bin, `
            + `and then the file is deleted. Restored from the Recycle Bin, the copy goes to your Downloads folder, under `
            + `"TagPup deleted from shares", not back to where it was.\n\nAre you sure you want to delete "${filename}"?`
        : `Are you sure you want to delete "${filename}" and move it to the Windows Recycle Bin?`;
    if (!confirm(question + alone)) {
        return;
    }

    statusDot.className = 'status-indicator-dot busy';
    statusText.textContent = 'Deleting...';
    // A clip playing lets go of its file first: the server reads it a range at a time, and a range being read as the
    // original is deleted would make Windows refuse the delete. Shown again if the delete fails while it is still open.
    const video = isVideo(asked);
    if (video) stopVideo();
    holdDeletes(true);
    lockBulkControls();     // the selection's controls ask the same question (bulk-run.js bulkBusy)
    let stayed = false;     // the delete was cancelled or failed: the clip, if still the one shown, plays again
    const send = (forGood) => api.json('/api/photo/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(forGood ? { path, for_good: true } : { path })
    });
    // A file its drive's Recycle Bin would not keep (or, from a share, this PC's full one: `bin_full`): the server named it and
    // deleted nothing; asked, and only on OK, for good.
    const cancelled = { cancelled: true };
    send(false)
    .then(data => (!data.success && Array.isArray(data.not_kept)
        ? (confirm(forGoodQuestion(data.not_kept, data.not_kept.length, data.bin_full)) ? send(true) : cancelled) : data))
    .then(data => {
        if (data === cancelled) {
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Nothing was deleted.';
            stayed = true;
            return;
        }
        // Deleted: every scan this browser keeps holding it is forgotten, and scanned when opened again; the open folder's
        // records lose it, and its scan is kept again without it (cache.js photoDeleted).
        const at = data.success ? photoDeleted(path) : -1;
        if (data.success && askedInView && state.library !== askedInView) {
            // Another view (or a folder) was opened while the delete was out: nothing of it is this photo's, unless that
            // folder is the photo's own (Back from the view): its record went (photoDeleted), so its card and count do too.
            if (at !== -1) {
                removeFromSelection([path]);
                updateListStats();
                folderViewStats.textContent = countSaid(state.folderPhotos);
                renderFileList();
                upper.renderThumbnails();
            }
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = data.message || 'Ready';
            return;
        }
        if (data.success && askedInView) {
            // The photo leaves the view: its card goes and the total drops. The next one in the order opens.
            const lib = askedInView;
            const gone = asked;
            const at = lib.ids.indexOf(gone.id);
            state.folderPhotos = [];
            lib.activeId = null;
            removeFromSelection([path]);
            forgetPhoto(gone.id);
            const nextId = nextOf(lib.ids, at);
            if (nextId === undefined) backToView();
            else openLibraryPhoto(nextId);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = data.message || 'Ready';
        } else if (data.success) {
            // Found again by its path (photoDeleted): a folder opened meanwhile has no such card, and none of it goes.
            if (at === -1) {
                removeFromSelection([path]);
                statusDot.className = 'status-indicator-dot';
                statusText.textContent = data.message || 'Ready';
                return;
            }
            const wasOpen = samePath(state.activePhotoPath, path);

            // Remove from selection array if selected
            removeFromSelection([path]);

            // Update UI statistics
            updateListStats();
            folderViewStats.textContent = countSaid(state.folderPhotos);

            // Re-render components
            renderFileList();
            upper.renderThumbnails();

            // Select the next photo or fall back to grid/folder view. Edits
            // typed for the deleted photo are not asked about: it is no longer
            // in folderPhotos, so hasUnsavedEdits finds nothing to save them to.
            if (state.folderPhotos.length === 0) {
                showFolderView();
            } else if (wasOpen) {
                // Another photo opened meanwhile stays open.
                const nextPhoto = nextOf(state.folderPhotos, at);
                selectPhoto(nextPhoto.path);
            }

            statusDot.className = 'status-indicator-dot';
            statusText.textContent = data.message || 'Ready';
        } else {
            throw new Error(data.error || 'Failed to delete photo');
        }
    })
    .catch(err => {
        console.error(err);
        statusDot.className = 'status-indicator-dot';
        statusText.textContent = 'Error';
        stayed = true;
        alert("Error deleting photo: " + err.message);
    })
    .finally(() => {
        // Answered: the photo shown now (the next one, or this one if it stayed) offers its Delete again.
        holdDeletes(false, state.folderPhotos.find(p => samePath(p.path, state.activePhotoPath)) || null);
        lockBulkControls();
        if (stayed && video && samePath(state.activePhotoPath, path)) showMedia(asked, '');
    });
}
