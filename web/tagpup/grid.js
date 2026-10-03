// TagPup's page: the folder view's thumbnails, their size, selecting them, and the
// right-click menu. The cards are drawn by the windowed grid (vgrid.js): only the rows on
// screen, and a couple either side, are in the DOM; this module is what a card is, and what
// selecting does across all of them.
import { api } from './common/api.js';
import { buildElement } from './common/dom.js';
import { baseName, pathKey } from './common/paths.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import {
    btnSizeLarge, btnSizeMedium, btnSizeSmall, folderViewMain, gridContextMenu, inputPhotoTitle,
    photoSearch, statusDot, statusText, thumbnailsGrid
} from './elements.js';
import { saveToLocalStorageCache } from './cache.js';
import {
    formatFriendlyDateSingle, getFolderDateStats, parseExifDateToLocalDate, takenOf
} from './format.js';
import { photoChangedOnDisk, queueWriteOf, stampOf, takeStamp } from './edits.js';
import { damageOf, markCard } from './damaged.js';
import { renderFileList, visiblePhotos } from './folder.js';
import { photoFileUrl, selectPhoto } from './photo.js';
import { createVGrid } from './vgrid.js';
import {
    applyEditedRecords, ASK_SELECT_ABOVE, cancelLibrarySelection, cardDamage, idsBetween, libraryCardKey,
    libraryCount, libraryIdOfPath, libraryIndexOfKey, libraryRecordAt, selectInLibrary
} from './library-source.js';
import {
    addToSelection, isSelected, removeFromSelection, renameInSelection, setSelection
} from './selected.js';

export function setThumbnailSize(size) {
    btnSizeSmall.classList.remove('active');
    btnSizeMedium.classList.remove('active');
    btnSizeLarge.classList.remove('active');
    thumbnailsGrid.classList.remove('size-smaller', 'size-larger');

    if (size === 'small') {
        btnSizeSmall.classList.add('active');
        thumbnailsGrid.classList.add('size-smaller');
    } else if (size === 'medium') {
        btnSizeMedium.classList.add('active');
    } else if (size === 'large') {
        btnSizeLarge.classList.add('active');
        thumbnailsGrid.classList.add('size-larger');
    }
    localStorage.setItem('tagpup_thumbnail_size', size);
    // A card's width is a new one, so its height is: read again, the same photos kept in view.
    if (state.grid && state.shownPhotos.length) state.grid.relayout();
}

export function wireThumbnailSize() {
    btnSizeSmall.addEventListener('click', () => setThumbnailSize('small'));
    btnSizeMedium.addEventListener('click', () => setThumbnailSize('medium'));
    btnSizeLarge.addEventListener('click', () => setThumbnailSize('large'));

    // Restore saved size preference
    const savedSize = localStorage.getItem('tagpup_thumbnail_size') || 'medium';
    setThumbnailSize(savedSize);
}

// ---- Grid context menu -------------------------------------------------
// The selection toolbar lives in a header that scrolls out of view in a long
// folder, which made "select none" hard to reach at exactly the moment it is
// wanted. A right-click menu puts the selection actions where the cursor is.
export function hideGridContextMenu() {
    if (gridContextMenu) gridContextMenu.classList.add('hidden');
}

export function showGridContextMenu(x, y, pathUnderCursor) {
    if (!gridContextMenu) return;
    const count = state.selectedThumbnails.length;
    const total = state.library ? state.library.ids.length : state.folderPhotos.length;

    gridContextMenu.querySelectorAll('[data-requires-selection]').forEach(el => {
        el.classList.toggle('disabled', count === 0);
    });
    const countLabel = gridContextMenu.querySelector('#context-menu-count');
    if (countLabel) {
        countLabel.textContent = count === 0
            ? `No photos selected (${total} in ${state.library ? 'view' : 'folder'})`
            : `${count} of ${total} selected`;
    }

    gridContextMenu.dataset.path = pathUnderCursor || '';
    gridContextMenu.classList.remove('hidden');

    // Keep the menu inside the viewport rather than letting it run off the edge.
    gridContextMenu.style.left = '0px';
    gridContextMenu.style.top = '0px';
    const rect = gridContextMenu.getBoundingClientRect();
    const left = Math.min(x, window.innerWidth - rect.width - 8);
    const top = Math.min(y, window.innerHeight - rect.height - 8);
    gridContextMenu.style.left = `${Math.max(8, left)}px`;
    gridContextMenu.style.top = `${Math.max(8, top)}px`;
}

/** The cards on screen show what is selected: the mark and the checkbox. */
export function syncSelectionMarks() {
    if (!state.grid) return;
    state.grid.eachCard((card, photo) => {
        const on = isSelected(photo.path);
        card.classList.toggle('selected', on);
        const box = card.querySelector('.thumbnail-checkbox');
        if (box) box.checked = on;
    });
}

export function invertThumbnailSelection() {
    if (state.library) {
        selectEverything('invert');
        return;
    }
    // The photos the filter shows are inverted; those it hides keep whatever they had.
    const shown = visiblePhotos();
    const showing = new Set(shown.map(photo => pathKey(photo.path)));
    const next = state.selectedThumbnails.filter(path => !showing.has(pathKey(path)));
    for (const photo of shown) {
        if (!isSelected(photo.path)) next.push(photo.path);
    }
    setSelection(next);
    syncSelectionMarks();
    upper.updateSelectedThumbnailsCount();
}

export function selectRangeToCursor(path) {
    if (!path) return;
    handleCardSelectionClick(path, true, null, true);
}

export function wireGridContextMenu() {
    if (thumbnailsGrid) {
        thumbnailsGrid.addEventListener('contextmenu', (e) => {
            const card = e.target.closest('.thumbnail-card');
            e.preventDefault();
            showGridContextMenu(e.clientX, e.clientY, card ? card.getAttribute('data-path') : '');
        });
    }

    document.addEventListener('click', (e) => {
        if (gridContextMenu && !gridContextMenu.contains(e.target)) hideGridContextMenu();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') hideGridContextMenu();
    });
    window.addEventListener('scroll', hideGridContextMenu, true);

    if (gridContextMenu) {
        gridContextMenu.addEventListener('click', (e) => {
            const item = e.target.closest('[data-action]');
            if (!item || item.classList.contains('disabled')) return;
            const action = item.getAttribute('data-action');
            const pathUnderCursor = gridContextMenu.dataset.path;
            hideGridContextMenu();

            if (action === 'select-all') selectAllThumbnails();
            else if (action === 'select-none') selectNoneThumbnails();
            else if (action === 'invert') invertThumbnailSelection();
            else if (action === 'select-range') selectRangeToCursor(pathUnderCursor);
            else if (action === 'open') { if (pathUnderCursor) selectPhoto(pathUnderCursor); }
            else if (action === 'explorer') {
                if (pathUnderCursor) {
                    api.fetch('/api/photo/open-explorer', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ path: pathUnderCursor })
                    }).catch(err => console.error('open-explorer failed', err));
                }
            }
        });
    }
}

// ---- The windowed grid --------------------------------------------------

/** A title being typed in a card: the card stays as it is, wherever it scrolls to. */
function cardIsBeingEdited(card) {
    const input = card.querySelector('.thumbnail-filename-input');
    return Boolean(input) && !input.dataset.saving;
}

function noPhotosFound() {
    const lib = state.library;
    return buildElement('div', {
        style: 'grid-column: 1/-1; text-align: center; color: var(--text-muted); padding: 40px;',
        text: !lib ? 'No photos found matching filter.'
            : lib.status === 'loading' ? 'Opening the view...' : (lib.message || 'This view holds no photos.'),
    });
}

/**
 * Build the grid over the folder's photos, once, at start-up. The source is what
 * renderThumbnails last put in state.shownPhotos; a card is keyed by its photo's path.
 */
export function wireThumbnailGrid() {
    state.grid = createVGrid({
        container: thumbnailsGrid,
        scroller: folderViewMain,
        // A folder's photos, or a library view's order and the cards of it that are held
        // (library-source.js): the grid asks the same three questions of either.
        source: {
            count: () => state.library ? libraryCount() : state.shownPhotos.length,
            recordAt: (index) => state.library ? libraryRecordAt(index) : state.shownPhotos[index],
            indexOfKey: (key) => {
                if (state.library) return libraryIndexOfKey(key);
                return state.shownIndex.has(key) ? state.shownIndex.get(key) : -1;
            },
        },
        buildCard: buildThumbnailCard,
        cardKey: (photo) => state.library ? libraryCardKey(photo) : pathKey(photo.path),
        isBusy: cardIsBeingEdited,
        afterBuild: () => upper.updateCameraHighlights(),
        empty: noPhotosFound,
    });
}

/**
 * Draw the folder's photos as the filter leaves them. The same folder and filter again keeps
 * the place in the list (an edit, a rename, a rescan); another folder or another filter
 * starts from the top.
 */
export function renderThumbnails() {
    if (state.library) {
        renderLibraryThumbnails();
        return;
    }
    const shown = visiblePhotos();
    const index = new Map();
    shown.forEach((photo, at) => index.set(pathKey(photo.path), at));
    state.shownPhotos = shown;
    state.shownIndex = index;

    const source = `${pathKey(state.scannedFolder)}\n${photoSearch.value.toLowerCase().trim()}`;
    if (state.shownSource === source) {
        state.grid.refresh();
    } else {
        state.shownSource = source;
        state.grid.reset();
    }
}

/**
 * A library view drawn: another view from the top, the same view again with the cards of the photos
 * edited since brought up to date where they are (the order is not recomputed: Refresh view does that).
 */
function renderLibraryThumbnails() {
    const source = `library:${state.library.token}`;
    state.shownPhotos = [];
    state.shownIndex = new Map();
    if (state.shownSource !== source) {
        state.shownSource = source;
        state.grid.reset();
        return;
    }
    applyEditedRecords();
    state.grid.refresh();
}

/**
 * The place of a card not yet fetched: the shape of a card, so the rows are the height they will be.
 * Nothing in it is clickable, has a path, or takes the keyboard; a card that gave up says so.
 */
function buildPlaceholderCard(record) {
    const card = document.createElement('div');
    card.className = 'thumbnail-card placeholder' + (record.failed ? ' failed' : '');
    card.setAttribute('aria-hidden', 'true');
    const wrapper = document.createElement('div');
    wrapper.className = 'thumbnail-img-wrapper';
    if (record.failed) {
        wrapper.appendChild(buildElement('div', { className: 'thumbnail-placeholder-note', text: 'Could not load' }));
    }
    card.appendChild(wrapper);
    const infoRow = document.createElement('div');
    infoRow.className = 'thumbnail-info-row';
    const textInfo = document.createElement('div');
    textInfo.className = 'thumbnail-text-info';
    textInfo.appendChild(buildElement('span', { className: 'thumbnail-filename', text: '\u00A0' }));
    textInfo.appendChild(buildElement('span', { className: 'thumbnail-date', text: '\u00A0' }));
    infoRow.appendChild(textInfo);
    const spacer = document.createElement('button');
    spacer.className = 'btn-thumbnail-detail';
    spacer.style.visibility = 'hidden';
    spacer.tabIndex = -1;
    spacer.textContent = '\u{1F50D}';
    infoRow.appendChild(spacer);
    card.appendChild(infoRow);
    return card;
}

function buildThumbnailCard(photo) {
    if (photo.placeholder) return buildPlaceholderCard(photo);
    const card = document.createElement('div');
    card.className = 'thumbnail-card';
    const selected = isSelected(photo.path);
    if (selected) {
        card.classList.add('selected');
    }
    card.setAttribute('data-path', photo.path);
    // A card of a library view is its photo's `thumb` (the cache's thumbnail by id) and cannot be
    // edited in place: its title is not in the card, and a save from it would write a title over tags it lacks.
    const inLibrary = Boolean(photo.thumb);
    if (inLibrary) card.setAttribute('data-id', String(photo.id));

    const chkContainer = document.createElement('div');
    chkContainer.className = 'thumbnail-checkbox-container';
    const chk = document.createElement('input');
    chk.type = 'checkbox';
    chk.className = 'thumbnail-checkbox';
    chk.checked = selected;
    chk.addEventListener('click', (e) => {
        e.stopPropagation();
        handleCardSelectionClick(photo.path, chk.checked, card, e.shiftKey);
    });
    chkContainer.appendChild(chk);
    card.appendChild(chkContainer);

    const imgWrapper = document.createElement('div');
    imgWrapper.className = 'thumbnail-img-wrapper';

    // Add AI suggestion badge if suggestions exist
    if (state.folderSuggestions[photo.path]) {
        const aiBadge = document.createElement('span');
        aiBadge.className = 'thumbnail-has-sugg';
        aiBadge.textContent = 'AI';
        imgWrapper.appendChild(aiBadge);
    }

    // A photo that cannot be read has no picture to ask for: its card says why (damaged.js).
    // The picture is asked for by the grid, once the card has stayed in view (vgrid.js).
    const damage = inLibrary ? cardDamage(photo) : damageOf(photo.path);
    if (!damage || damage.indexed) {
        const img = document.createElement('img');
        img.dataset.src = inLibrary ? api.image(photo.thumb) : photoFileUrl(photo, 300);
        img.alt = photo.filename;
        imgWrapper.appendChild(img);
    }
    card.appendChild(imgWrapper);
    if (damage) markCard(card, damage);

    const infoRow = document.createElement('div');
    infoRow.className = 'thumbnail-info-row';

    const textInfo = document.createElement('div');
    textInfo.className = 'thumbnail-text-info';

    const name = document.createElement('span');
    name.className = 'thumbnail-filename editable-title';
    const fullName = photo.filename || "";
    const lastDotIndex = fullName.lastIndexOf('.');
    const displayName = lastDotIndex !== -1 ? fullName.substring(0, lastDotIndex) : fullName;

    if (photo.title) {
        name.textContent = photo.title;
        name.classList.add('has-title');
        name.title = `Title: ${photo.title}\nFile: ${fullName}${inLibrary ? '' : '\n(Click to edit title)'}`;
    } else {
        name.textContent = displayName;
        name.title = `File: ${fullName}${inLibrary ? '' : '\n(Click to add title)'}`;
    }

    name.addEventListener('click', (e) => {
        if (inLibrary) return;   // the card selects; the title is edited in the details panel
        e.stopPropagation(); // prevent card selection trigger!

        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'thumbnail-filename-input';
        input.value = photo.title || '';
        input.placeholder = displayName;
        input.title = "Type caption/title and press Enter to save";

        name.replaceWith(input);
        input.focus();
        input.select();

        let isSaving = false;
        function finishEdit() {
            if (isSaving) return;
            isSaving = true;

            const newTitle = input.value.trim();
            if (newTitle === (photo.title || '')) {
                input.replaceWith(name);
                return;
            }

            // Saving: the grid may draw the card again from the photo, editor and all.
            input.dataset.saving = '1';
            statusDot.className = 'status-indicator-dot busy';
            statusText.textContent = 'Saving title...';

            // Queued with every other write to a photo, so the tags it sends
            // are the photo's tags when it runs, not a copy from before.
            queueWriteOf(photo.path, () => api.json('/api/photo/save-metadata', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: photo.path, title: newTitle, tags: photo.tags, stamp: stampOf(photo) })
            })
            .then(data => {
                if (data.changed_on_disk) photoChangedOnDisk(photo);
                if (data.success) {
                    const oldPath = photo.path;
                    takeStamp(photo, data);
                    photo.title = newTitle;
                    photo.captions = newTitle ? [newTitle] : [];

                    if (data.new_path && data.new_path !== oldPath) {
                        photo.path = data.new_path;
                        photo.filename = baseName(data.new_path);
                        renameInSelection(oldPath, data.new_path);
                        if (state.activePhotoPath === oldPath) {
                            state.activePhotoPath = data.new_path;
                        }
                    }

                    renderFileList();
                    renderThumbnails();

                    if (state.activePhotoPath === photo.path) {
                        inputPhotoTitle.value = newTitle;
                    }

                    statusDot.className = 'status-indicator-dot';
                    statusText.textContent = 'Ready';
                    saveToLocalStorageCache();
                } else {
                    throw new Error(data.error || 'Failed to save');
                }
            })
            .catch(err => {
                console.error(err);
                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Error';
                alert("Error saving title: " + err.message);
                input.replaceWith(name);
            }));
        }

        input.addEventListener('keydown', (ev) => {
            if (ev.key === 'Enter') {
                ev.preventDefault();
                finishEdit();
            } else if (ev.key === 'Escape') {
                ev.preventDefault();
                input.replaceWith(name);
            }
        });

        input.addEventListener('blur', () => {
            finishEdit();
        });
    });

    textInfo.appendChild(name);

    // Date taken display
    const dateSpan = document.createElement('span');
    dateSpan.className = 'thumbnail-date';
    let dateVal = "Unknown";
    const thumbDate = takenOf(photo) && parseExifDateToLocalDate(takenOf(photo));
    if (thumbDate) dateVal = formatFriendlyDateSingle(thumbDate, getFolderDateStats());
    dateSpan.textContent = dateVal;
    textInfo.appendChild(dateSpan);
    infoRow.appendChild(textInfo);

    const btnDetail = document.createElement('button');
    btnDetail.className = 'btn-thumbnail-detail';
    btnDetail.title = 'View details and edit metadata';
    btnDetail.textContent = '🔍';
    btnDetail.addEventListener('click', (e) => {
        e.stopPropagation();
        selectPhoto(photo.path);
    });
    infoRow.appendChild(btnDetail);

    card.appendChild(infoRow);

    // Click toggles card selection
    card.addEventListener('click', (e) => {
        if (e.target.tagName === 'INPUT') return;
        const nextChecked = !isSelected(photo.path);
        chk.checked = nextChecked;
        handleCardSelectionClick(photo.path, nextChecked, card, e.shiftKey);
    });

    return card;
}

// ---- Selecting ----------------------------------------------------------
// By photo, never by card: most of the photos have no card at the moment. The range of a
// Shift-click is read from the grid's order (state.shownPhotos), the cards on screen follow.

/**
 * A click in a library view. A range is by the view's order, over cards that may not be held: the
 * ones missing are fetched (for their paths) and the selection changes when they are here.
 */
function handleLibraryClick(path, isChecked, cardElement, isShiftKey) {
    const lib = state.library;
    const id = libraryIdOfPath(path);
    if (isShiftKey && lib.lastId !== null && id !== null) {
        const range = idsBetween(lib.lastId, id);
        if (range) {
            selectInLibrary(isChecked ? 'add' : 'remove', range);
            lib.lastId = id;
            state.lastSelectedPath = path;
            return;
        }
    }
    toggleThumbnailSelection(path, isChecked, cardElement);
    state.lastSelectedPath = path;
    lib.lastId = id;
}

export function handleCardSelectionClick(path, isChecked, cardElement, isShiftKey) {
    if (state.library) {
        handleLibraryClick(path, isChecked, cardElement, isShiftKey);
        return;
    }
    if (isShiftKey && state.lastSelectedPath) {
        const startIdx = state.shownIndex.has(pathKey(state.lastSelectedPath))
            ? state.shownIndex.get(pathKey(state.lastSelectedPath)) : -1;
        const endIdx = state.shownIndex.has(pathKey(path)) ? state.shownIndex.get(pathKey(path)) : -1;

        if (startIdx !== -1 && endIdx !== -1) {
            const minIdx = Math.min(startIdx, endIdx);
            const maxIdx = Math.max(startIdx, endIdx);
            const inRange = state.shownPhotos.slice(minIdx, maxIdx + 1).map(photo => photo.path);
            if (isChecked) addToSelection(inRange);
            else removeFromSelection(inRange);
            syncSelectionMarks();
            upper.updateSelectedThumbnailsCount();
            state.lastSelectedPath = path;
            return;
        }
    }

    toggleThumbnailSelection(path, isChecked, cardElement);
    state.lastSelectedPath = path;
}

export function toggleThumbnailSelection(path, isChecked, cardElement) {
    if (isChecked) addToSelection([path]);
    else removeFromSelection([path]);
    if (cardElement) cardElement.classList.toggle('selected', isChecked);
    else syncSelectionMarks();
    upper.updateSelectedThumbnailsCount();
}

/** Select all, or invert, over a whole library view: asked about first when it is large. */
function selectEverything(mode) {
    const lib = state.library;
    if (!lib || lib.ids.length === 0) return;
    const count = lib.ids.length.toLocaleString();
    const question = mode === 'invert' ? `Invert the selection over ${count} photos?` : `Select all ${count} photos?`;
    if (lib.ids.length > ASK_SELECT_ABOVE && !window.confirm(question)) return;
    selectInLibrary(mode, lib.ids);
}

export function selectAllThumbnails() {
    if (state.library) {
        selectEverything('replace');
        return;
    }
    // What the filter shows: the count and a bulk write then match what is on the screen.
    setSelection(visiblePhotos().map(p => p.path));
    syncSelectionMarks();
    upper.updateSelectedThumbnailsCount();
}

export function selectNoneThumbnails() {
    if (state.library) cancelLibrarySelection();
    setSelection([]);
    syncSelectionMarks();
    upper.updateSelectedThumbnailsCount();
}
