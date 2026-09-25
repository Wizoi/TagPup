// TagPup's page: the folder list -- choosing, scanning and closing a folder, the list of
// its photos and the folder view's header, and the sidebar's width.
import { api } from './common/api.js';
import { baseName, samePath } from './common/paths.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import {
    btnChangeDb, btnCreateDb, btnFolderAutoApply, btnToggleRename, btnToggleTimeshift,
    currentFolderName, dbSelect, emptyState, facesSection, folderPathInput, folderViewContent,
    folderViewHeader, folderViewStats, listStats, panelContent, photoList, photoPosition,
    photoSearch, sidebar, sidebarResizer, statusDot, statusText
} from './elements.js';
import { flagField } from './status.js';
import { CACHE_TTL_MS, folderCacheKey, saveToLocalStorageCache } from './cache.js';
import { updatePeopleDatalist, updateTagsDatalist } from './tags.js';
import { discardDetailEdits, hasUnsavedEdits, leavePhotoThen } from './edits.js';

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

export function closeFolderForDogPark() {
    state.scannedFolder = null;
    state.folderPhotos = [];
    state.folderSuggestions = {};
    folderPathInput.value = '';
    const url = new URL(window.location);
    url.searchParams.delete('path');
    window.history.replaceState({}, '', url);
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

// Scans a folder
export function scanFolder(forceRefresh = false) {
    // Opening a folder, or refreshing this one, repopulates the panel from what
    // was scanned -- so it asks first, like any other way off the photo. Staying
    // puts the open folder back in the box rather than leave it naming another.
    if (hasUnsavedEdits() || state.detailSaveInFlight) {
        leavePhotoThen(() => scanFolder(forceRefresh), {
            onStay: () => { if (state.scannedFolder) folderPathInput.value = state.scannedFolder; },
        });
        return;
    }
    const path = folderPathInput.value.trim();
    if (!path) {
        flagField(folderPathInput, 'Choose or type a folder to scan');
        return;
    }

    // Reset folder-specific suggestions state to prevent leaks
    state.folderSuggestions = {};

    // Cache lookup if not forcing refresh
    if (!forceRefresh) {
        // Falls back once to the key as typed, which is how entries were stored
        // before pathKey; the next save moves it to the shared key.
        const rawCache = localStorage.getItem(folderCacheKey(path))
            || localStorage.getItem(`tagpup_cache_${path}`);
        if (rawCache) {
            try {
                const cacheEntry = JSON.parse(rawCache);
                const age = Date.now() - cacheEntry.timestamp;
                if (age < CACHE_TTL_MS) {
                    state.scannedFolder = path;
                    updateCurrentFolderLabel();
                    state.folderPhotos = cacheEntry.photos;
                    state.folderSuggestions = cacheEntry.suggestions || {};
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
                    window.history.replaceState({}, '', url);
                    
                    upper.checkSuggestionsStatus(path);
                    
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
            // The scan asked about unsaved edits before it began; anything typed
            // while it ran has not been asked about. The photo being edited is
            // still here after a refresh, and showPhoto keeps what is being typed.
            // If it is not -- another folder -- ask now, before it is replaced.
            const stillHere = state.activePhotoPath && data.some(p => samePath(p.path, state.activePhotoPath));
            if (!stillHere && hasUnsavedEdits()) {
                leavePhotoThen(() => showScannedFolder(path, data), {
                    onStay: () => {
                        // Keep editing: the folder still open goes back on screen.
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
            console.error(err);
            listStats.textContent = 'Scan failed';
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
            alert("Error scanning folder: " + err.message);
        });
}

export function showScannedFolder(path, data) {
    state.scannedFolder = path;
    updateCurrentFolderLabel();
    state.folderPhotos = data;
    updateListStats();

    // Update URL search path parameter
    const url = new URL(window.location);
    url.searchParams.set('path', path);
    window.history.replaceState({}, '', url);

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
    return (photo.tags || []).length > 0
        || (photo.people || []).length > 0
        || (photo.title || '').trim().length > 0;
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
            || (photo.tags || []).some(t => t.toLowerCase().includes(query));
    });
}

/**
 * The count line under the search box.
 *
 * "N files loaded" answered a question nobody was asking. What a person wants to
 * know on returning to a folder is how much of it is left.
 */
export function updateListStats(suffix) {
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

    const filtered = visiblePhotos();
    updateListStats();

    const fragment = document.createDocumentFragment();
    filtered.forEach(photo => {
        const li = document.createElement('li');
        li.className = 'photo-item photo-item-file';
        // Reachable by Tab, and announced as a choice rather than decoration.
        // Nothing in either list used to be focusable, which is why the arrow
        // keys only worked while focus happened to be sitting on <body>.
        li.tabIndex = 0;
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

        const tagCount = (photo.tags || []).length;
        if (tagCount) {
            const count = document.createElement('span');
            count.className = 'photo-item-tagcount';
            count.textContent = String(tagCount);
            count.title = `${tagCount} tag(s)`;
            li.appendChild(count);
        }

        // Display a badge if suggestion is ready for this file
        if (state.folderSuggestions[photo.path]) {
            const badge = document.createElement('span');
            badge.className = 'photo-item-has-sugg';
            badge.textContent = 'AI';
            li.appendChild(badge);
        }

        li.addEventListener('click', () => upper.selectPhoto(photo.path));
        li.addEventListener('keydown', (ev) => {
            if (ev.key === 'Enter' || ev.key === ' ') {
                ev.preventDefault();
                upper.selectPhoto(photo.path);
            }
        });
        fragment.appendChild(li);
    });
    photoList.appendChild(fragment);
    updatePhotoPosition();
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
    const items = Array.from(photoList.querySelectorAll('.photo-item-file'));
    const index = state.activePhotoPath
        ? items.findIndex(el => el.getAttribute('data-path') === state.activePhotoPath)
        : -1;
    photoPosition.classList.toggle('hidden', index === -1);
    photoPosition.textContent = index === -1 ? '' : `${index + 1} of ${items.length}`;
}

export function filterFileList() {
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
    if (facesSection) facesSection.classList.add('hidden');
    
    // Highlight folder view item in list
    photoList.querySelectorAll('.photo-item').forEach(el => el.classList.remove('active'));
    folderViewHeader.classList.add('active');

    // Hide single views, show Folder Grid
    panelContent.classList.add('hidden');
    emptyState.classList.add('hidden');
    folderViewContent.classList.remove('hidden');

    // Populate Folder View details
    folderViewStats.textContent = `${state.folderPhotos.length} photos`;
    
    upper.renderThumbnails();
    upper.updateSelectedThumbnailsCount();
    updatePhotoPosition();
}
