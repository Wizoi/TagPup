// TagPup's page: the folder list -- choosing, scanning and closing a folder, the list of
// its photos and the folder view's header, and the sidebar's width.
import { api } from './common/api.js';
import { baseName, isUnder, pathKey, samePath } from './common/paths.js';
import { pressable } from './common/press.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import {
    btnChangeDb, btnCreateDb, btnFolderAutoApply, btnNameFaces, btnShowInLibrary, btnToggleRename, btnToggleTimeshift,
    currentFolderName, dbSelect, emptyState, facesSection, folderPathInput, folderViewContent,
    folderViewHeader, folderViewStats, listStats, panelContent, photoList, photoPosition,
    photoSearch, sidebar, sidebarResizer, statusDot, statusText
} from './elements.js';
import { flagField, setStatus } from './status.js';
import { clearSelection, keepOnly } from './selected.js';
import { forgetSelectionFaces } from './selection-faces.js';
import { CACHE_TTL_MS, CACHE_VERSION, folderCacheKey, saveToLocalStorageCache } from './cache.js';
import { updatePeopleDatalist, updateTagsDatalist } from './tags.js';
import { discardDetailEdits, hasUnsavedEdits, leavePhotoThen } from './edits.js';
import { leaveThen, shownTags } from './pending.js';
import { clearPhotoFaces } from './face-boxes.js';
import { kindsSaid, libraryPosition, viewCountSaid } from './library-source.js';
import { replaceAddress } from './history-entries.js';
import { isVideo } from './media.js';

export function wireChangeDogPark() {
    if (btnChangeDb) {
        btnChangeDb.addEventListener('click', () => {
            const leaf = baseName(state.scannedFolder) || 'the open folder';
            if (!confirm(
                `Switch to a different dog park?

` +
                `${leaf} will be closed. Its photos belong to this dog park's index, ` +
                `and another one has its own people, tags and suggestions.`
            )) return;
            // Closing the folder clears it in place -- no unload for the browser
            // to catch -- so unsaved edits are asked about first.
            leavePhotoThen(closeFolderForDogPark);
        });
    }
}

/**
 * The selection belongs to the folder it was made in: another folder starts with none, and the
 * same folder scanned again keeps what is still in it. Called with the folder that was open
 * before, as the new one is put in place.
 */
function carrySelection(previous, path, photos) {
    if (previous && samePath(previous, path)) keepOnly(photos.map(photo => photo.path));
    else {
        clearSelection();
        state.lastSelectedPath = null;
    }
    upper.updateSelectedThumbnailsCount();
}

export function closeFolderForDogPark() {
    clearSelection();
    state.lastSelectedPath = null;
    state.scannedFolder = null;
    state.folderPhotos = [];
    upper.forgetFolderSuggestions();
    forgetSelectionFaces();
    upper.checkFolderMembership(null);
    folderPathInput.value = '';
    const url = new URL(window.location);
    url.searchParams.delete('path');
    replaceAddress(url);
    updateCurrentFolderLabel();
    // Close the photo too, and the list: both still showed the folder just
    // closed, editable and clickable. Edits were settled before this ran.
    openFolderView();
    renderFileList();
    updateListStats();
    if (dbSelect) dbSelect.focus();
}

export function wireFolderPathInput() {
    folderPathInput.addEventListener('input', () => {
        const val = folderPathInput.value;
        if (!val) return;
        api.json(`/api/autocomplete-folder?path=${encodeURIComponent(val)}`)
            .then(data => {
                const folderDatalist = document.getElementById('folder-datalist');
                if (folderDatalist) {
                    folderDatalist.innerHTML = '';
                    data.forEach(item => {
                        const opt = document.createElement('option');
                        opt.value = item;
                        folderDatalist.appendChild(opt);
                    });
                }
            })
            .catch(err => console.error("Error autocompleting folder:", err));
    });
    // Choosing a folder opens it. Picking from the autocomplete list or leaving the
    // box both fire `change`; Enter commits without waiting to lose focus. A button
    // that only repeats what choosing already means is one step too many.
    folderPathInput.addEventListener('change', () => openChosenFolder());
    folderPathInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); openChosenFolder(); }
    });
}

export function wireSidebarResizer() {
    // Sidebar resize logic
    let isResizing = false;
    sidebarResizer.addEventListener('mousedown', (e) => {
        isResizing = true;
        sidebarResizer.classList.add('resizing');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
        e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
        if (!isResizing) return;
        const sidebarRect = sidebar.getBoundingClientRect();
        let newWidth = e.clientX - sidebarRect.left;
        if (newWidth < 240) newWidth = 240;
        if (newWidth > 600) newWidth = 600;
        sidebar.style.width = `${newWidth}px`;
    });

    document.addEventListener('mouseup', () => {
        if (isResizing) {
            isResizing = false;
            sidebarResizer.classList.remove('resizing');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        }
    });
}

// Native Folder Browser Dialog Trigger
export function browseFolder() {
    statusDot.className = 'status-indicator-dot busy';
    statusText.textContent = 'Browsing...';
    
    api.json('/api/browse-folder')
        .then(data => {
            if (data.path) {
                folderPathInput.value = data.path;
                scanFolder(false);
            } else {
                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Ready';
            }
        })
        .catch(err => {
            console.error(err);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
        });
}


/**
 * Lock the dog-park picker once a folder is open.
 *
 * Switching reloads the page carrying the same ?path=, so the same folder comes
 * back attached to a different database -- different index, different suggestions,
 * different people -- with nothing on screen marking the change. That is a quiet
 * way to tag a folder into the wrong library. The picker is free until a folder is
 * open and behind a Change button afterwards, and Change closes the folder so the
 * new dog park starts from a deliberate choice rather than an inherited one.
 */
export function updateDogParkLock() {
    const open = Boolean(state.scannedFolder);
    if (dbSelect) dbSelect.disabled = open;
    if (btnCreateDb) btnCreateDb.disabled = open;
    if (btnChangeDb) btnChangeDb.classList.toggle('hidden', !open);
}

/**
 * Open whatever folder is currently in the box, unless it is already open.
 *
 * `change` fires both when a suggestion is picked and when the box loses focus,
 * so without the guard, clicking away from a folder already on screen would
 * re-scan it for nothing.
 */
export function openChosenFolder() {
    const path = folderPathInput.value.trim();
    if (!path) return;
    // Typed, so any spelling of the open folder counts as the open folder.
    if (state.scannedFolder && samePath(path, state.scannedFolder)) return;
    scanFolder(false);
}

/** Show which folder is open, by name, and put it in the window title. */
export function updateCurrentFolderLabel() {
    const leaf = baseName(state.scannedFolder);
    updateDogParkLock();
    if (currentFolderName) {
        currentFolderName.textContent = leaf;
        currentFolderName.title = state.scannedFolder || '';
        currentFolderName.classList.toggle('hidden', !leaf);
    }
    document.title = leaf ? `${leaf} — TagPup` : 'TagPup GUI';
    // A folder is open on disk, and no view of the library is: Show in library, and Name faces from tags with its "Only this
    // folder" (name-faces.js; the gear's item is the whole library's).
    const onDisk = Boolean(state.scannedFolder) && !state.library;
    btnShowInLibrary.classList.toggle('hidden', !onDisk);
    btnNameFaces.classList.toggle('hidden', !onDisk);
    if (folderViewHeader) {
        const label = leaf ? `Folder View — ${leaf}` : 'Folder View (Thumbnails)';
        folderViewHeader.innerHTML = '';
        const icon = document.createElement('span');
        icon.className = 'folder-header-icon';
        icon.textContent = '\u{1F4C2}';
        folderViewHeader.appendChild(icon);
        folderViewHeader.appendChild(document.createTextNode(' ' + label));
    }
}

/**
 * Does the scan of `open` hold photos `indexed`'s index wrote: the same folder, one above it or one below it (a folder's
 * scan is recursive, web/common/paths.js isUnder)? What an index's end reads again (library-view.js indexEnded).
 */
export function scanHolds(open, indexed) {
    return samePath(open, indexed) || isUnder(open, indexed) || isUnder(indexed, open);
}

/**
 * Is this browser's kept scan of `path`, kept at `taken` (Date.now()), out of date: an index holding its photos is under
 * way or waits, or ended after it was kept, as this page has heard (state.indexFollow, suggestions.js checkIndexingStatus)?
 * Shown, it was the list from before the index: no faces, no new photos (#3408).
 */
function keptScanOutOfDate(path, taken) {
    const follow = state.indexFollow;
    if ([...follow.known.values()].some(folder => scanHolds(path, folder))) return true;
    return [...follow.ends.values()].some(end => end.at > (Number(taken) || 0) && scanHolds(path, end.folder));
}

// Scans a folder
export function scanFolder(forceRefresh = false, { keepTyped = false, asked = false } = {}) {
    const path = folderPathInput.value.trim();
    if (!path) {
        flagField(folderPathInput, 'Choose or type a folder to scan');
        return;
    }
    // Opening a folder, or refreshing this one, repopulates the panel from what was scanned -- so it asks first, like
    // any other way off the photo, and about the tag changes waiting for another folder (pending.js leaveThen). Staying
    // puts the open folder back in the box rather than leave it naming another. `keepTyped`: the open photo's file
    // changed and the folder is read again to show it (edits.js photoChangedOnDisk); what was typed stays in its fields,
    // so there is nothing to ask about. `asked`: the caller asked already.
    if (!keepTyped && !asked) {
        leaveThen(() => {
            folderPathInput.value = path;   // the folder asked about, whatever the box says by now
            scanFolder(forceRefresh, { asked: true });
        }, () => {
            if (state.scannedFolder) folderPathInput.value = state.scannedFolder;
        }, path);
        return;
    }
    // A folder opened leaves the library view that was open (library-view.js).
    if (state.library) upper.leaveLibraryView();

    // Reset folder-specific suggestions state to prevent leaks
    upper.forgetFolderSuggestions();
    forgetSelectionFaces();

    // Cache lookup if not forcing refresh
    if (!forceRefresh) {
        const rawCache = localStorage.getItem(folderCacheKey(path));
        if (rawCache) {
            try {
                const cacheEntry = JSON.parse(rawCache);
                const age = Date.now() - cacheEntry.timestamp;
                if (age < CACHE_TTL_MS && cacheEntry.version === CACHE_VERSION
                    && !keptScanOutOfDate(path, cacheEntry.timestamp)) {
                    const previous = state.scannedFolder;
                    state.scannedFolder = path;
                    updateCurrentFolderLabel();
                    state.folderPhotos = cacheEntry.photos;
                    carrySelection(previous, path, state.folderPhotos);
                    state.folderSuggestions = cacheEntry.suggestions || {};
                    state.suggestionsInMemory = cacheEntry.inMemory && Object.keys(state.folderSuggestions).length > 0
                        ? path : null;
                    updateListStats('(cached)');
                    folderViewHeader.classList.remove('hidden');
                    upper.updateSuggestButtonState();
                    btnToggleRename.disabled = false;
                    btnToggleTimeshift.disabled = false;
                    if (Object.keys(state.folderSuggestions).length > 0) {
                        btnFolderAutoApply.disabled = false;
                    } else {
                        btnFolderAutoApply.disabled = true;
                    }
                    
                    renderFileList();
                    updateTagsDatalist();
                    updatePeopleDatalist();
                    upper.populateCameraModelsDropdown();
                    
                    // Update URL
                    const url = new URL(window.location);
                    url.searchParams.set('path', path);
                    replaceAddress(url);
                    
                    upper.checkSuggestionsStatus(path);
                    upper.checkFolderMembership(path);
                    upper.checkIndexingStatus();

                    // If active photo path is set, reload its data
                    if (state.activePhotoPath) {
                        const matched = state.folderPhotos.find(p => p.path === state.activePhotoPath);
                        if (matched) {
                            upper.selectPhoto(state.activePhotoPath);
                        } else {
                            showFolderView();
                        }
                    } else {
                        showFolderView();
                    }
                    
                    statusDot.className = 'status-indicator-dot';
                    statusText.textContent = 'Ready';
                    return;
                }
            } catch (e) {
                console.error("Error reading folder cache:", e);
            }
        }
    }

    if (state.scanAbortController) {
        state.scanAbortController.abort();
    }
    state.scanAbortController = new AbortController();
    const scanning = state.scanning = { path, stale: false };

    statusDot.className = 'status-indicator-dot busy';
    statusText.textContent = 'Scanning...';
    listStats.textContent = 'Scanning...';
    photoList.querySelectorAll('.photo-item-file').forEach(el => el.remove());

    api.fetch(`/api/folder/scan?path=${encodeURIComponent(path)}&force=${forceRefresh}`, { signal: state.scanAbortController.signal })
        .then(res => {
            if (!res.ok) return res.json().then(e => { throw new Error(e.error || 'Scan failed') });
            return res.json();
        })
        .then(data => {
            if (state.scanning === scanning) state.scanning = null;
            // An index holding its photos ended while it was out (library-view.js indexEnded): what it read may be from
            // before; read again, once for each such end.
            if (scanning.stale) {
                folderPathInput.value = path;
                scanFolder(true, { asked: true });
                return;
            }
            // The scan asked about unsaved edits before it began; anything typed
            // while it ran has not been asked about. The photo being edited is
            // still here after a refresh, and showPhoto keeps what is being typed.
            // If it is not -- another folder -- ask now, before it is replaced.
            const stillHere = state.activePhotoPath && data.some(p => samePath(p.path, state.activePhotoPath));
            if (!stillHere && hasUnsavedEdits()) {
                leavePhotoThen(() => showScannedFolder(path, data), {
                    onStay: () => {
                        // Keep editing: the folder still open goes back on screen, and what was to be said of the scan is not.
                        state.afterScan = null;
                        folderPathInput.value = state.scannedFolder || '';
                        renderFileList();
                        updateListStats();
                        statusDot.className = 'status-indicator-dot';
                        statusText.textContent = 'Ready';
                    },
                });
                return;
            }
            showScannedFolder(path, data);
        })
        .catch(err => {
            if (err.name === 'AbortError') return;
            if (state.scanning === scanning) state.scanning = null;
            console.error(err);
            state.afterScan = null;
            listStats.textContent = 'Scan failed';
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
            alert("Error scanning folder: " + err.message);
        });
}

/**
 * A Delete of the folder's selection has ended (bulk-run.js): the folder is read again, the one truth of a folder -- the deleted
 * leave its list, its kept scan and the selection (showScannedFolder, carrySelection keeps what is still listed) -- and the photo
 * open in the panel, when its file went, closes onto the next the list still holds, as the panel's Delete does. `folder` is the
 * folder the delete was asked in: once another folder or a view is open nothing is touched (#532). Resolves once the folder is
 * on screen again, so a clip stopped for the delete plays again only if the scan still lists it; a scan that fails says so
 * (scanFolder) and the promise stays unresolved: the clip stays stopped. `said` ({kind, sentence}, bulk-run.js) is how the delete
 * ended: the scan says Ready as it lands, so it is said again then, of this folder only (as the sidebar's Refresh does).
 */
export function folderPhotosDeleted(folder, said) {
    if (state.library || !state.scannedFolder || !samePath(state.scannedFolder, folder)) return Promise.resolve(false);
    const listed = state.folderPhotos.map(photo => photo.path);
    const open = state.activePhotoPath;
    return new Promise(resolve => {
        state.afterScan = () => {
            const here = !state.library && samePath(state.scannedFolder, folder);
            if (here && open && !state.activePhotoPath) openNextStillListed(listed, open);
            if (here && said) setStatus(said.kind, said.sentence, { transient: false });
            resolve(here);
        };
        folderPathInput.value = folder;
        scanFolder(true, { asked: true });
    });
}

/** Open the photo the folder still lists that comes after `gone` in `listed` (the list before the scan), else the one before it. */
function openNextStillListed(listed, gone) {
    const now = new Map(state.folderPhotos.map(photo => [pathKey(photo.path), photo]));
    const still = path => now.get(pathKey(path));
    const at = listed.findIndex(path => samePath(path, gone));
    const next = nextOf(listed.map(still).filter(Boolean), listed.slice(0, at).filter(still).length);
    if (next) upper.selectPhoto(next.path);
}

/**
 * The photo to open when the open one has left the list: `at` is where it was in `list`, which no longer holds it. The next one,
 * else the one before it; undefined when the list is empty. The one rule of the panel's Delete (a view's order or a folder's list,
 * photo.js deleteActivePhoto) and the selection's (openNextStillListed): here, with the list the panel walks (updatePhotoPosition).
 */
export function nextOf(list, at) {
    return at < list.length ? list[at] : list[at - 1];
}

export function showScannedFolder(path, data) {
    const previous = state.scannedFolder;
    state.scannedFolder = path;
    updateCurrentFolderLabel();
    state.folderPhotos = data;
    carrySelection(previous, path, data);
    updateListStats();

    // Update URL search path parameter
    const url = new URL(window.location);
    url.searchParams.set('path', path);
    replaceAddress(url);

    // Save to cache
    saveToLocalStorageCache();

    // Show folder view header item
    folderViewHeader.classList.remove('hidden');

    // Enable suggest tags button
    upper.updateSuggestButtonState();
    btnToggleRename.disabled = false;
    btnToggleTimeshift.disabled = false;
    btnFolderAutoApply.disabled = true;

    renderFileList();
    updateTagsDatalist();
    updatePeopleDatalist();
    upper.populateCameraModelsDropdown();

    // Start tracking background progress check
    upper.checkSuggestionsStatus(path);
    // Does the library hold it? If not, ask before anything is added (membership.js).
    upper.checkFolderMembership(path);
    // An index under way is followed to its end, whichever folder it is of (suggestions.js).
    upper.checkIndexingStatus();

    // If active photo path is set, reload its data
    if (state.activePhotoPath) {
        const matched = state.folderPhotos.find(p => p.path === state.activePhotoPath);
        if (matched) {
            upper.selectPhoto(state.activePhotoPath);
        } else {
            showFolderView();
        }
    } else {
        showFolderView();
    }

    statusDot.className = 'status-indicator-dot';
    statusText.textContent = 'Ready';
    if (state.afterScan) {
        const after = state.afterScan;
        state.afterScan = null;
        after();
    }
}

/**
 * Has this photo been tagged at all?
 *
 * Deliberately a low bar: one tag, one person, or a title counts. The question a
 * tagging session asks is "have I been here yet", not "is this perfect", and a
 * stricter test would mark honest work as unfinished.
 */
export function isPhotoTagged(photo) {
    if (!photo) return false;
    return shownTags(photo).length > 0
        || (photo.people || []).length > 0
        || (photo.title || '').trim().length > 0;
}

/**
 * A count of a folder's records as the page says one: "9 photos, 3 videos" where both are, "1 photo", "3 videos", and
 * "0 photos" for none. The one spelling of it (#2565): the folder view's line, the grid's menu, and the confirmations of
 * Smart Rename and Apply All, which count what they will do.
 */
export function countSaid(records) {
    const videos = records.filter(isVideo).length;
    return kindsSaid({ photos: records.length - videos, videos });
}

/** How much of the loaded folder has been tagged. */
export function taggedCounts() {
    const total = state.folderPhotos.length;
    const tagged = state.folderPhotos.filter(isPhotoTagged).length;
    return { total, tagged, remaining: total - tagged };
}

/**
 * The photos the sidebar and the grid should show.
 *
 * Both used to filter for themselves, with copies of the same predicate that had
 * already drifted apart -- the grid read `photo.filename` directly where the list
 * fell back to the path. One filter means the two panes cannot disagree about
 * what you are looking at, and a new rule lands in both at once.
 */
export function visiblePhotos() {
    const query = photoSearch.value.toLowerCase().trim();
    return state.folderPhotos.filter(photo => {
        if (!query) return true;
        const fname = photo.filename || baseName(photo.path) || '';
        return fname.toLowerCase().includes(query)
            || (photo.title && photo.title.toLowerCase().includes(query))
            || shownTags(photo).some(t => t.toLowerCase().includes(query));
    });
}

/**
 * The count line under the search box.
 *
 * "N files loaded" answered a question nobody was asking. What a person wants to
 * know on returning to a folder is how much of it is left.
 */
export function updateListStats(suffix) {
    if (state.library) {
        listStats.textContent = `${viewCountSaid(state.library)} in this view`;
        return;
    }
    const { total, tagged, remaining } = taggedCounts();
    if (!total) {
        listStats.textContent = suffix || '0 files loaded';
        return;
    }
    const extra = suffix ? ` ${suffix}` : '';
    listStats.textContent = remaining === 0
        ? `${total} files, all tagged${extra}`
        : `${tagged} of ${total} tagged, ${remaining} to go${extra}`;
}

// Render list in Sidebar
export function renderFileList() {
    // Remove old files
    photoList.querySelectorAll('.photo-item-file').forEach(el => el.remove());
    // A library view has no list: tens of thousands of rows are what the grid's window is for.
    if (state.library) {
        updateListStats();
        updatePhotoPosition();
        return;
    }

    const filtered = visiblePhotos();
    updateListStats();

    const fragment = document.createDocumentFragment();
    filtered.forEach(photo => {
        const li = document.createElement('li');
        li.className = 'photo-item photo-item-file';
        // Announced as a choice rather than decoration, and focusable: the arrow keys used to work only while the
        // focus sat on <body>. One row of the list takes the Tab (markOpenRow), not each.
        li.tabIndex = -1;
        li.setAttribute('role', 'option');
        if (state.activePhotoPath === photo.path) {
            li.classList.add('active');
        }
        li.setAttribute('data-path', photo.path);
        if (isPhotoTagged(photo)) li.classList.add('is-tagged');

        // A dot rather than a word: it has to read at a glance down a long list,
        // and it is the first thing scanned when picking up where you left off.
        const doneDot = document.createElement('span');
        doneDot.className = 'photo-item-done';
        doneDot.textContent = isPhotoTagged(photo) ? '\u25CF' : '\u25CB';
        doneDot.title = isPhotoTagged(photo) ? 'Tagged' : 'Not tagged yet';
        li.appendChild(doneDot);

        const nameSpan = document.createElement('span');
        nameSpan.className = 'photo-item-name';
        const fullName = photo.filename || baseName(photo.path) || "";
        const lastDotIndex = fullName.lastIndexOf('.');
        const displayName = lastDotIndex !== -1 ? fullName.substring(0, lastDotIndex) : fullName;
        
        if (photo.title) {
            nameSpan.textContent = photo.title;
            nameSpan.style.fontStyle = 'italic';
            nameSpan.style.color = '#a5b4fc';
            nameSpan.title = `Title: ${photo.title}\nFile: ${fullName}`;
        } else {
            nameSpan.textContent = displayName;
            nameSpan.title = fullName;
        }
        li.appendChild(nameSpan);

        const tagCount = shownTags(photo).length;   // as the photo shows them (pending.js)
        if (tagCount) {
            const count = document.createElement('span');
            count.className = 'photo-item-tagcount';
            count.textContent = String(tagCount);
            count.title = `${tagCount} tag(s)`;
            li.appendChild(count);
        }

        // Display a badge if suggestion is ready for this file
        if (upper.suggested(state.folderSuggestions[photo.path])) {
            const badge = document.createElement('span');
            badge.className = 'photo-item-has-sugg';
            badge.textContent = 'AI';
            li.appendChild(badge);
        }

        li.addEventListener('click', () => upper.selectPhoto(photo.path));
        fragment.appendChild(pressable(li));       // Enter or Space opens it, as a click does
    });
    photoList.appendChild(fragment);
    markOpenRow(photoList.querySelector('.photo-item.active'));
    updatePhotoPosition();
}

/**
 * The row open -- the photo's, or the Folder View row while the grid shows -- is marked, and the file rows are one Tab
 * stop (a roving tabindex): the open photo's row takes it, the others a click or the arrows, so a folder of 300 photos
 * is not 300 Tabs between Refresh and the photo. With no photo open the grid has the Tab, unless no row is open at all
 * (the open photo filtered out of the list): then the first row takes it. The Folder View row is a stop of its own
 * whenever it shows: with a photo open it is the way back to the grid, and the arrows step photos, never onto it.
 */
export function markOpenRow(row) {
    for (const el of photoList.querySelectorAll('.photo-item.active, .photo-item-file[tabindex="0"]')) {
        if (el === row) continue;
        el.classList.remove('active');
        if (el !== folderViewHeader) el.tabIndex = -1;
    }
    if (row) row.classList.add('active');
    const stop = row === folderViewHeader ? null : row || photoList.querySelector('.photo-item-file');
    if (!stop) return;
    stop.tabIndex = 0;
    // The focus was on the row the arrows just left: it goes with them, so Tab goes on from the open row, not onto it (#3047).
    const at = document.activeElement;
    if (at !== stop && at && photoList.contains(at) && at.classList.contains('photo-item-file')) stop.focus({ preventScroll: true });
}

/**
 * "12 of 48": where the open photo sits in the list, to see how far along you are.
 *
 * Counted over the rendered rows -- the same list stepPhoto walks -- so Next is
 * always N+1 of M, and a search that narrows the list narrows the count with it.
 * Called when the list is rebuilt (filter, rename, delete, rescan all go through
 * renderFileList) and when the open photo changes. Hidden with no photo open, or
 * when the open photo is not among the rows the search left.
 */
export function updatePhotoPosition() {
    if (!photoPosition) return;
    if (state.library) {
        // Where the open photo is in the view's order, by its ids -- not the rows of a list.
        const at = libraryPosition();
        photoPosition.classList.toggle('hidden', !at);
        photoPosition.textContent = at ? `${at.index.toLocaleString()} of ${at.total.toLocaleString()}` : '';
        return;
    }
    const items = Array.from(photoList.querySelectorAll('.photo-item-file'));
    const index = state.activePhotoPath
        ? items.findIndex(el => el.getAttribute('data-path') === state.activePhotoPath)
        : -1;
    photoPosition.classList.toggle('hidden', index === -1);
    photoPosition.textContent = index === -1 ? '' : `${index + 1} of ${items.length}`;
}

export function filterFileList() {
    if (state.library) return;   // the filter is off in a library view
    if (state.searchTimeout) clearTimeout(state.searchTimeout);
    state.searchTimeout = setTimeout(() => {
        renderFileList();
        if (!folderViewContent.classList.contains('hidden')) {
            upper.renderThumbnails();
        }
    }, 150);
}

// Select Folder Thumbnail View
export function showFolderView() {
    // Leaving the photo for the grid is leaving the photo.
    leavePhotoThen(openFolderView);
}

export function openFolderView() {
    state.activePhotoPath = null;
    discardDetailEdits();              // nothing pending by now; empty the fields
    state.facesRequestToken++;               // abandon any in-flight face lookup
    // Leaving the photo is leaving its faces: the same photo opened again reads them and says so, not hides them as if read.
    clearPhotoFaces();
    if (facesSection) facesSection.classList.add('hidden');
    
    // Highlight folder view item in list
    markOpenRow(folderViewHeader);

    // Hide single views, show Folder Grid
    panelContent.classList.add('hidden');
    emptyState.classList.add('hidden');
    folderViewContent.classList.remove('hidden');

    // Populate Folder View details
    folderViewStats.textContent = state.library ? viewCountSaid(state.library) : countSaid(state.folderPhotos);
    
    upper.renderThumbnails();
    upper.updateSelectedThumbnailsCount();
    updatePhotoPosition();
    // The view of the library as it was left when a photo was opened over it (view-left.js), then a move from a view lands
    // on the photo it was made from (library-moves.js).
    upper.restoreViewAsLeft();
    upper.landOnAnchor();
}
