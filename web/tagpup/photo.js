// TagPup's page: the open photo -- its details, tags, faces and zoom, carrying tags
// forward, opening, rotating and deleting it, and editing when it was taken.
import { api } from './common/api.js';
import { buildElement, replaceContent } from './common/dom.js';
import { photoAlreadyHas } from './common/vocabulary.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import {
    btnCancelDateModal, btnCarryForward, btnCloseDateModal, btnEditDateTaken, btnSaveDateModal,
    dateTakenModal, detailDateTaken, detailPath, detailPeople, detailsPanel, detailTags,
    emptyState, facesSection, facesStrip, facesSummary, folderViewContent, folderViewHeader,
    folderViewStats, imageZoom, imageZoomImg, inputAddPerson, inputAddTag, inputDateTaken,
    inputPhotoTitle, mainImage, panelContent, photoList, statusDot, statusText
} from './elements.js';
import { setStatus } from './status.js';
import { saveToLocalStorageCache } from './cache.js';
import {
    baseName, exifDateToIso, formatFriendlyDateSingle, getCurrentDateTimeIso,
    getFolderDateStats, parseExifDateToLocalDate, takenOf
} from './format.js';
import { namesAPerson, updateTagsDatalist } from './tags.js';
import {
    leavePhotoThen, postPhotoMetadata, queuePhotoWrite, redrawIfShowing, saveDetailEdits,
    updateSaveButton
} from './edits.js';
import { renderFileList, showFolderView, updateListStats, updatePhotoPosition } from './folder.js';

// ---- Detected faces ----------------------------------------------------
// Face recognition already ran for this photo -- the suggester needs it to propose
// people -- but nothing ever showed it. Without the crops you learn that a face went
// unrecognised only later, in TagTuner. This puts that in front of you while tagging.
// facesRequestToken is declared with the other state at the top; see the note there.

export function renderPhotoFaces(photoPath) {
    if (!facesSection || !facesStrip) return;
    const token = ++state.facesRequestToken;

    facesStrip.innerHTML = '';
    facesSection.classList.add('hidden');
    if (facesSummary) facesSummary.textContent = '';

    api.fetch(`/api/photo-faces?path=${encodeURIComponent(photoPath)}`)
        .then(res => res.ok ? res.json() : { faces: [] })
        .then(data => {
            // A slower reply for a previously selected photo must not overwrite
            // the strip for the one now on screen.
            if (token !== state.facesRequestToken) return;
            const faces = data.faces || [];
            if (faces.length === 0) return;

            // Who this photo already names, so a face is only offered when acting
            // on it would change something.
            const photoRecord = state.folderPhotos.find(p => p.path === photoPath);

            facesSection.classList.remove('hidden');
            if (facesSummary) {
                const unmatched = data.unmatched || 0;
                facesSummary.textContent = unmatched
                    ? `${faces.length} detected, ${unmatched} unidentified`
                    : `${faces.length} detected, all identified`;
            }

            faces.forEach(face => {
                const card = document.createElement('div');
                card.className = 'face-card';
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
                img.alt = face.name || 'Unidentified face';
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

                const label = document.createElement('span');
                label.className = 'face-card-label';
                if (face.excluded) {
                    label.textContent = face.excluded_reason || 'excluded';
                    card.title = 'Excluded from face matching';
                } else if (face.name) {
                    label.textContent = face.name;
                    card.title = face.name;
                } else if (face.suggestion) {
                    const pct = Math.round((face.similarity || 0) * 100);
                    // The percentage lives on the crop now; repeating it here is
                    // what pushed the name into an ellipsis.
                    label.textContent = `${face.suggestion}?`;
                    card.title = `Closest match: ${face.suggestion} (${pct}%). Not assigned.`;
                    label.classList.add('face-card-suggestion');
                } else {
                    label.textContent = 'Unidentified';
                    card.title = 'No similar face in this database yet';
                }
                card.appendChild(label);

                // Clicking a face adds that person to the photo, which is the
                // small correction TagPup is meant for; deeper work is TagTuner's.
                //
                // A recognised face counts as much as a proposed one. Only
                // unnamed-with-a-suggestion used to be clickable, so a photo whose
                // faces were already identified offered no way to act on them --
                // the strip said who was in the picture while People Tags sat
                // empty, and clicking did nothing.
                const namesSomebody = face.name || face.suggestion;
                const alreadyTagged = namesSomebody
                    && photoAlreadyHas(photoRecord, namesSomebody, namesAPerson);

                if (namesSomebody && !alreadyTagged && !face.excluded) {
                    card.classList.add('face-card-actionable');
                    // Keep what the card already said -- the closest match and how
                    // sure it is -- and add what pressing it does. Replacing it
                    // threw away the reading somebody hovers to check.
                    card.title = `${card.title || namesSomebody}`
                        + `
Click to add ${namesSomebody} to this photo.`;
                    card.addEventListener('click', () => {
                        upper.applySuggestedTagDirect(namesSomebody, true, photoPath);
                    });
                } else if (alreadyTagged) {
                    // Not clickable, and saying so beats a card that looks live
                    // and does nothing when pressed.
                    card.classList.add('face-card-settled');
                    card.title = `${namesSomebody} is already tagged on this photo`;
                }
                facesStrip.appendChild(card);
            });
        })
        .catch(err => console.error('Error loading faces:', err));
}

// Select Single Photo View
export function selectPhoto(path) {
    // Every way to another photo comes through here -- rows, the grid, the
    // context menu, the arrow keys and swipes -- so this is where it asks.
    if (path === state.activePhotoPath) showPhoto(path);
    else leavePhotoThen(() => showPhoto(path));
}

export function showPhoto(path) {
    // Typed text belongs to the photo it was typed for. By now it has been
    // saved or discarded, but a field is never carried to another photo.
    if (path !== state.activePhotoPath) {
        inputAddTag.value = '';
        inputAddPerson.value = '';
    }
    state.activePhotoPath = path;

    // Highlight sidebar element
    photoList.querySelectorAll('.photo-item').forEach(el => el.classList.remove('active'));
    folderViewHeader.classList.remove('active');
    const activeLi = photoList.querySelector(`li[data-path="${CSS.escape(path)}"]`);
    if (activeLi) {
        activeLi.classList.add('active');
    }

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
    if (!photo) return;

    // Render values
    mainImage.src = photoFileUrl(photo, 800);
    detailPath.textContent = photo.path;
    let dateVal = "Unknown";
    const shownDate = takenOf(photo) && parseExifDateToLocalDate(takenOf(photo));
    if (shownDate) dateVal = formatFriendlyDateSingle(shownDate, getFolderDateStats());
    detailDateTaken.textContent = dateVal;
    // The same photo shown again -- a refresh -- keeps a title being typed.
    const typing = state.titleShown.path === path && inputPhotoTitle.value !== state.titleShown.title;
    if (!typing) inputPhotoTitle.value = photo.title || '';
    state.titleShown = { path, title: photo.title || '' };
    updateSaveButton();

    renderTags(photo.tags);
    upper.renderSuggestionsPanel(photo.path);
}

/**
 * The tags on whichever photo sits before this one in the list.
 *
 * "Before" is list order rather than a history of what you visited, because that
 * is what matches the way a shoot is worked: down the folder, in order. Reaching
 * backwards from the first photo yields nothing rather than wrapping around.
 */
export function previousPhotoTags() {
    const items = Array.from(photoList.querySelectorAll('.photo-item-file'));
    const index = items.findIndex(
        el => el.getAttribute('data-path') === state.activePhotoPath
    );
    if (index <= 0) return { tags: [], from: null };
    const prevPath = items[index - 1].getAttribute('data-path');
    const prev = state.folderPhotos.find(p => p.path === prevPath);
    if (!prev) return { tags: [], from: null };
    return {
        tags: (prev.tags || []).slice(),
        from: prev.filename || baseName(prevPath),
    };
}

/** Is there anything to copy forward onto the current photo? */
export function carryForwardCandidates() {
    const { tags, from } = previousPhotoTags();
    const photo = state.folderPhotos.find(p => p.path === state.activePhotoPath);
    if (!photo) return { missing: [], from };
    const have = new Set(photo.tags || []);
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
    if (!photo) return;

    const { tags: carried, from } = previousPhotoTags();
    const nothingToCarry = () => setStatus('ready', from
        ? `Nothing to carry over from ${from}`
        : 'No previous photo to carry tags from');
    if (!carryForwardCandidates().missing.length) {
        nothingToCarry();
        return;
    }

    return queuePhotoWrite(async () => {
        // What is missing now, not when the key was pressed: a write queued
        // ahead of this one may have added some of it, or removed others.
        const before = (photo.tags || []).slice();
        const have = new Set(before);
        const missing = carried.filter(t => !have.has(t));
        if (!missing.length) {
            nothingToCarry();
            return true;
        }
        const updatedTags = Array.from(new Set([...before, ...missing]));
        setStatus('busy', `Copying ${missing.length} tag(s) from ${from}...`);
        try {
            await postPhotoMetadata(photo, { tags: updatedTags });
        } catch (err) {
            console.error(err);
            setStatus('error', 'Could not copy tags: ' + err.message);
            return false;
        }
        photo.tags = updatedTags;
        upper.recordUndo({
            label: `carry ${missing.length} tag(s) from ${from}`,
            photos: [{ path: photo.path, tags: before, title: photo.title }],
        });
        redrawIfShowing(photo);
        renderFileList();
        upper.renderThumbnails();
        saveToLocalStorageCache();
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
// The 800px preview is already loaded, so it is shown at once, scaled up, as the
// image's background; the original is the image itself and paints over it when
// it arrives. The preview stays if the original is a format the browser cannot
// draw (TIFF, HEIC).
//
// Opening it is not leaving the photo: nothing in the panel changes, so it neither
// asks about unsaved edits nor makes any. While it is open the arrow keys do
// nothing, rather than change the photo underneath it.
export function isZoomOpen() {
    return Boolean(imageZoom) && !imageZoom.classList.contains('hidden');
}

export function openZoom() {
    if (!imageZoom || !state.activePhotoPath || !mainImage.getAttribute('src')) return;
    const preview = mainImage.src;               // absolute, database prefix included
    const original = new URL(preview, window.location.href);
    original.searchParams.delete('size');      // no size: the file as it is on disk
    imageZoomImg.style.backgroundImage = `url("${preview}")`;
    imageZoomImg.src = original.href;
    imageZoom.classList.remove('hidden');
}

export function closeZoom() {
    if (!isZoomOpen()) return;
    imageZoom.classList.add('hidden');
    imageZoomImg.removeAttribute('src');       // stop a large download nobody wants now
    imageZoomImg.style.backgroundImage = '';
}

export function wireZoom() {
    if (imageZoom) {
        mainImage.addEventListener('click', () => {
            if (mainImage.dataset.dragged === 'true') {
                delete mainImage.dataset.dragged;  // that was a swipe
                return;
            }
            openZoom();
        });
        imageZoom.addEventListener('click', closeZoom);
        imageZoomImg.addEventListener('load', () => {
            // The original has arrived; drop the preview so a transparent PNG does
            // not show it through.
            if (imageZoomImg.getAttribute('src')) imageZoomImg.style.backgroundImage = '';
        });
        imageZoomImg.addEventListener('error', () => {
            // Not drawable here: fall back to the preview, scaled up. Once only --
            // the preview failing too must not loop.
            const preview = mainImage.src;
            if (isZoomOpen() && preview && imageZoomImg.src !== preview) {
                imageZoomImg.src = preview;
            }
        });

        // Captured, so it is decided before the arrow-key navigation hears it.
        // Ctrl+S is left alone: its own listener is registered first and saves.
        document.addEventListener('keydown', (e) => {
            if (!isZoomOpen()) return;
            if (e.key === 'Escape') {
                e.preventDefault();
                e.stopPropagation();
                closeZoom();
                return;
            }
            if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Enter', ' ',
                 'PageUp', 'PageDown', 'Home', 'End'].includes(e.key)) {
                e.preventDefault();
                e.stopPropagation();
            }
        }, true);
    }
}

export function renderTags(tags) {
    detailPeople.innerHTML = '';
    detailTags.innerHTML = '';
    
    const photo = state.folderPhotos.find(p => p.path === state.activePhotoPath);
    const photoPeople = (photo && photo.people) ? photo.people : [];
    
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
        peopleTags.forEach(tag => {
            const pill = document.createElement('span');
            pill.className = 'tag-pill';
            pill.style.cursor = 'pointer';
            pill.title = 'Click to remove person';
            pill.textContent = tag;
            pill.addEventListener('click', () => {
                deletePhotoTag(tag);
            });
            detailPeople.appendChild(pill);
        });
    }

    if (nonPeopleTags.length === 0) {
        replaceContent(detailTags, buildElement('span', {
            style: 'color: var(--text-muted); font-size: 13px;', text: 'No keywords set.',
        }));
    } else {
        nonPeopleTags.forEach(tag => {
            const pill = document.createElement('span');
            pill.className = 'tag-pill';
            pill.style.cursor = 'pointer';
            pill.title = 'Click to remove tag';
            pill.textContent = tag;
            pill.addEventListener('click', () => {
                deletePhotoTag(tag);
            });
            detailTags.appendChild(pill);
        });
    }
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
    const path = state.activePhotoPath;
    if (!path) return;
    
    const photo = state.folderPhotos.find(p => p.path === path);
    if (!photo) return;

    return queuePhotoWrite(async () => {
        // From the tags as they are when this runs: two pills clicked in quick
        // succession each used to write "all but mine", and the later one put
        // the other back.
        const current = photo.tags || [];
        const updatedTags = current.filter(t => t !== tagToRemove);
        if (updatedTags.length === current.length) return true;

        setStatus('busy', 'Deleting tag...');
        try {
            await postPhotoMetadata(photo, { tags: updatedTags });
        } catch (err) {
            console.error(err);
            setStatus('error', 'Error');
            alert("Error deleting tag: " + err.message);
            return false;
        }
        photo.tags = updatedTags;
        redrawIfShowing(photo);
        updateTagsDatalist();
        setStatus('ready', 'Ready');
        saveToLocalStorageCache();
        return true;
    });
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
/**
 * A photo's image URL, versioned by its mtime.
 *
 * The server caches these for a day. A rotation changes the file but not the URL,
 * so the grid went on showing the old turn -- and so did the photo itself, when
 * returned to later. Carrying the mtime makes a changed file a new URL.
 */
export function photoFileUrl(photo, size) {
    return api.image(`/api/photo-file?path=${encodeURIComponent(photo.path)}&size=${size}`
        + `&v=${encodeURIComponent(photo.mtime || 0)}`);
}

export function rotatePhoto(direction) {
    const path = state.activePhotoPath;
    if (!path) return;

    statusDot.className = 'status-indicator-dot busy';
    statusText.textContent = 'Rotating...';

    api.json('/api/photo/rotate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, direction })
    })
    .then(data => {
        if (data.success) {
            // The photo's new mtime is its images' new URL, here and in the grid.
            const photo = state.folderPhotos.find(p => p.path === path) || { path };
            photo.mtime = data.mtime || Date.now() / 1000;
            mainImage.src = photoFileUrl(photo, 800);
            const thumb = document.querySelector(
                `#thumbnails-grid [data-path="${CSS.escape(path)}"] img`);
            if (thumb) thumb.src = photoFileUrl(photo, 300);
            saveToLocalStorageCache();
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Ready';
        } else {
            throw new Error(data.error || 'Failed to rotate');
        }
    })
    .catch(err => {
        console.error(err);
        statusDot.className = 'status-indicator-dot';
        statusText.textContent = 'Error';
        alert("Error rotating image: " + err.message);
    });
}

// Move active photo to Windows Recycle Bin and update state
export function deleteActivePhoto() {
    const path = state.activePhotoPath;
    if (!path) return;

    const index = state.folderPhotos.findIndex(p => p.path === path);
    if (index === -1) return;

    const filename = state.folderPhotos[index].filename || 'this photo';
    if (!confirm(`Are you sure you want to delete "${filename}" and move it to the Windows Recycle Bin?`)) {
        return;
    }

    statusDot.className = 'status-indicator-dot busy';
    statusText.textContent = 'Deleting...';

    api.json('/api/photo/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path })
    })
    .then(data => {
        if (data.success) {
            // Remove photo from client folderPhotos array
            state.folderPhotos.splice(index, 1);

            // Remove from selection array if selected
            const selIndex = state.selectedThumbnails.indexOf(path);
            if (selIndex !== -1) {
                state.selectedThumbnails.splice(selIndex, 1);
            }

            // Update cache
            saveToLocalStorageCache();

            // Update UI statistics
            updateListStats();
            folderViewStats.textContent = `${state.folderPhotos.length} photos`;

            // Re-render components
            renderFileList();
            upper.renderThumbnails();

            // Select the next photo or fall back to grid/folder view. Edits
            // typed for the deleted photo are not asked about: it is no longer
            // in folderPhotos, so hasUnsavedEdits finds nothing to save them to.
            if (state.folderPhotos.length === 0) {
                showFolderView();
            } else {
                const nextPhoto = state.folderPhotos[index] || state.folderPhotos[index - 1];
                selectPhoto(nextPhoto.path);
            }

            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Ready';
        } else {
            throw new Error(data.error || 'Failed to delete photo');
        }
    })
    .catch(err => {
        console.error(err);
        statusDot.className = 'status-indicator-dot';
        statusText.textContent = 'Error';
        alert("Error deleting photo: " + err.message);
    });
}

export function wireDateTakenModal() {
    if (btnEditDateTaken && dateTakenModal) {
        btnEditDateTaken.addEventListener('click', () => {
            const photo = state.folderPhotos.find(p => p.path === state.activePhotoPath);
            if (!photo) return;
            
            const isoDate = exifDateToIso(takenOf(photo)) || getCurrentDateTimeIso();
            inputDateTaken.value = isoDate;
            dateTakenModal.classList.add('active');
        });

        const closeDateModal = () => {
            dateTakenModal.classList.remove('active');
        };
        
        btnCloseDateModal.addEventListener('click', closeDateModal);
        btnCancelDateModal.addEventListener('click', closeDateModal);

        btnSaveDateModal.addEventListener('click', () => {
            const path = state.activePhotoPath;
            if (!path) return;
            
            const photo = state.folderPhotos.find(p => p.path === path);
            if (!photo) return;
            
            const newDateVal = inputDateTaken.value; // format: "YYYY-MM-DDTHH:MM:SS.sss"
            if (!newDateVal) {
                alert("Please enter a valid date and time.");
                return;
            }
            
            closeDateModal();

            // In the photo write queue with every other write: it sends the tags and
            // title too, and those are read when it runs, not when Save was clicked.
            queuePhotoWrite(async () => {
                setStatus('busy', 'Saving date taken...');
                try {
                    await postPhotoMetadata(photo, { date_taken: newDateVal });
                } catch (err) {
                    console.error(err);
                    setStatus('error', 'Error');
                    alert("Error saving date taken: " + err.message);
                    return false;
                }
                const formattedDate = newDateVal.replace("T", " ").replace(/-/g, ":");

                // What the server now records as when it was taken.
                photo.taken = formattedDate;

                // Format and display in UI -- if this photo is still the one shown.
                if (state.activePhotoPath === photo.path) {
                    const localD = parseExifDateToLocalDate(formattedDate);
                    if (localD) {
                        const stats = getFolderDateStats();
                        detailDateTaken.textContent = formatFriendlyDateSingle(localD, stats);
                    } else {
                        detailDateTaken.textContent = newDateVal;
                    }
                }

                setStatus('ready', 'Ready');
                saveToLocalStorageCache();
                renderFileList();
                upper.renderThumbnails();
                return true;
            });
        });
    }
}
