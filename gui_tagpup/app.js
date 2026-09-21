// app.js - Standalone TagPup GUI Logic

// Database Subfolder Routing Interceptor
(function() {
    const pathSegments = window.location.pathname.split('/');
    let activeDbName = "";
    const RESERVED = ["api", "gui", "gui_tagpup", "index.html", "style.css", "app.js", "favicon.ico"];
    for (const segment of pathSegments) {
        if (segment && !RESERVED.includes(segment) && !segment.includes('.')) {
            activeDbName = segment;
            break;
        }
    }
    if (activeDbName) {
        // Intercept fetch calls
        const originalFetch = window.fetch;
        window.fetch = function(input, init) {
            if (typeof input === 'string' && input.startsWith('/api/')) {
                input = '/' + activeDbName + input;
            }
            return originalFetch(input, init);
        };

        // Intercept image src assignments
        const propDesc = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
        if (propDesc && propDesc.set) {
            const originalSrcSetter = propDesc.set;
            Object.defineProperty(HTMLImageElement.prototype, 'src', {
                set: function(val) {
                    if (typeof val === 'string') {
                        const idx = val.indexOf('/api/');
                        if (idx !== -1) {
                            const prefix = '/' + activeDbName + '/api/';
                            if (!val.includes(prefix)) {
                                val = val.substring(0, idx) + '/' + activeDbName + val.substring(idx);
                            }
                        }
                    }
                    originalSrcSetter.call(this, val);
                },
                get: propDesc.get,
                configurable: true,
                enumerable: true
            });
        }
    }
})();

document.addEventListener('DOMContentLoaded', () => {
    // Database selection logic
    const dbSelect = document.getElementById('db-select');
    const btnCreateDb = document.getElementById('btn-create-db');

    function initDatabaseSelector() {
        if (!dbSelect) return;
        
        const pathSegments = window.location.pathname.split('/');
        let activeDb = "";
        const RESERVED = ["api", "gui", "gui_tagpup", "index.html", "style.css", "app.js", "favicon.ico"];
        for (const segment of pathSegments) {
            if (segment && !RESERVED.includes(segment) && !segment.includes('.')) {
                activeDb = segment;
                break;
            }
        }
        
        fetch('api/databases')
            .then(res => res.json())
            .then(data => {
                dbSelect.innerHTML = '';
                const selectedDb = activeDb || data.selected || 'photo_index';
                
                data.databases.forEach(db => {
                    const option = document.createElement('option');
                    option.value = db;
                    option.textContent = db;
                    if (db === selectedDb) {
                        option.selected = true;
                    }
                    dbSelect.appendChild(option);
                });
                
                if (!activeDb && data.selected) {
                    window.location.pathname = '/' + data.selected + '/';
                }
            })
            .catch(err => console.error('Error fetching databases:', err));

        dbSelect.addEventListener('change', () => {
            const selectedDb = dbSelect.value;
            fetch('api/databases/select', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ db_name: selectedDb + '.db' })
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    const queryStr = window.location.search;
                    window.location.href = '/' + selectedDb + '/' + queryStr;
                } else {
                    alert('Error selecting database: ' + (data.error || 'Unknown error'));
                }
            })
            .catch(err => alert('Error selecting database: ' + err));
        });

        if (btnCreateDb) {
            btnCreateDb.addEventListener('click', () => {
                const dbName = prompt('Enter a name for the new database (alphanumeric characters, e.g. "vacation_2026"):');
                if (!dbName) return;
                
                let cleanName = dbName.trim();
                if (!cleanName) return;
                if (cleanName.endsWith('.db')) {
                    cleanName = cleanName.substring(0, cleanName.length - 3);
                }
                
                if (!/^[a-zA-Z0-9_\-]+$/.test(cleanName)) {
                    alert('Invalid name. Only letters, numbers, underscores, and hyphens are allowed.');
                    return;
                }
                
                fetch('api/databases/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ db_name: cleanName })
                })
                .then(res => res.json())
                .then(data => {
                    if (data.success) {
                        window.location.href = '/' + cleanName + '/';
                    } else {
                        alert('Error creating database: ' + (data.error || 'Unknown error'));
                    }
                })
                .catch(err => alert('Error creating database: ' + err));
            });
        }
    }
    initDatabaseSelector();

    // DOM Elements
    const folderPathInput = document.getElementById('folder-path-input');
    const btnBrowseFolder = document.getElementById('btn-browse-folder');
    const btnScanFolder = document.getElementById('btn-scan-folder');
    const btnIndexFolder = document.getElementById('btn-index-folder');
    const btnSuggestTags = document.getElementById('btn-suggest-tags');
    const suggestProgressContainer = document.getElementById('suggest-progress-container');
    const suggestProgressBar = document.getElementById('suggest-progress-bar');
    const suggestProgressText = document.getElementById('suggest-progress-text');
    const indexProgressContainer = document.getElementById('index-progress-container');
    const indexProgressBar = document.getElementById('index-progress-bar');
    const indexProgressText = document.getElementById('index-progress-text');
    
    const photoSearch = document.getElementById('photo-search');
    const photoList = document.getElementById('photo-list');
    const listStats = document.getElementById('list-stats');
    const filterUntaggedOnly = document.getElementById('filter-untagged-only');
    const btnRefreshList = document.getElementById('btn-refresh-list');
    
    const sidebar = document.querySelector('.sidebar');
    const sidebarResizer = document.getElementById('sidebar-resizer');
    
    const detailsPanel = document.getElementById('details-panel');
    const emptyState = document.getElementById('empty-state');
    const panelContent = document.getElementById('panel-content');
    
    const folderViewHeader = document.getElementById('folder-view-header');
    const folderViewContent = document.getElementById('folder-view-content');
    const folderViewTitle = document.getElementById('folder-view-title');
    const folderViewStats = document.getElementById('folder-view-stats');
    const btnSelectAllThumbnails = document.getElementById('btn-select-all-thumbnails');
    const btnSelectNoneThumbnails = document.getElementById('btn-select-none-thumbnails');
    const selectedThumbnailsCount = document.getElementById('selected-thumbnails-count');
    const btnFolderAutoApply = document.getElementById('btn-folder-auto-apply');
    const facesStrip = document.getElementById('faces-strip');
    const facesSummary = document.getElementById('faces-summary');
    const folderSelectionSidebar = document.getElementById('folder-selection-sidebar');
    const selectionEmptyHint = document.getElementById('selection-empty-hint');
    const selectionSummaryScroll = document.querySelector('.selection-summary-scroll');
    const selectionSummaryCount = document.getElementById('selection-summary-count');
    const selectionPeopleList = document.getElementById('selection-people-list');
    const selectionTagsList = document.getElementById('selection-tags-list');
    const selectionSuggestedPeopleList = document.getElementById('selection-suggested-people-list');
    const selectionSuggestedTagsList = document.getElementById('selection-suggested-tags-list');
    const selectionDateLabel = document.getElementById('selection-date-label');
    const selectionDateValue = document.getElementById('selection-date-value');
    const bulkAddPeopleInput = document.getElementById('bulk-add-people-input');
    const btnBulkAddPeople = document.getElementById('btn-bulk-add-people');
    const bulkAddTagsInput = document.getElementById('bulk-add-tags-input');
    const btnBulkAddTags = document.getElementById('btn-bulk-add-tags');
    const thumbnailsGrid = document.getElementById('thumbnails-grid');
    
    const btnSizeSmall = document.getElementById('btn-size-small');
    const btnSizeMedium = document.getElementById('btn-size-medium');
    const btnSizeLarge = document.getElementById('btn-size-large');
    
    const timeshiftPanel = document.getElementById('timeshift-panel');
    const timeshiftCameraSelect = document.getElementById('timeshift-camera-select');
    const timeshiftMinutesInput = document.getElementById('timeshift-minutes-input');
    const btnApplyTimeshift = document.getElementById('btn-apply-timeshift');
    const btnToggleTimeshift = document.getElementById('btn-toggle-timeshift');
    
    const btnToggleRename = document.getElementById('btn-toggle-rename');
    const renamePanel = document.getElementById('rename-panel');
    const renameGroupingInput = document.getElementById('rename-grouping-input');
    const btnApplyRename = document.getElementById('btn-apply-rename');
    
    const mainImage = document.getElementById('main-image');
    const btnRotateLeft = document.getElementById('btn-rotate-left');
    const btnRotateRight = document.getElementById('btn-rotate-right');
    const btnDeletePhoto = document.getElementById('btn-delete-photo');
    const facesSection = document.getElementById('faces-section');
    
    const detailPath = document.getElementById('detail-path');
    const detailDateTaken = document.getElementById('detail-date-taken');
    const inputPhotoTitle = document.getElementById('input-photo-title');
    const btnSaveTitle = document.getElementById('btn-save-title');
    const detailPeople = document.getElementById('detail-people');
    const inputAddPerson = document.getElementById('input-add-person');
    const btnAddPerson = document.getElementById('btn-add-person');
    const detailTags = document.getElementById('detail-tags');
    const inputAddTag = document.getElementById('input-add-tag');
    const btnAddTag = document.getElementById('btn-add-tag');
    
    const suggestionsSection = document.getElementById('suggestions-section');
    const btnApplyAllSingleSugg = document.getElementById('btn-apply-all-single-sugg');
    const btnSuggestTitleWand = document.getElementById('btn-suggest-title-wand');
    const suggestedPeopleContainer = document.getElementById('suggested-people-container');
    const suggestedTagsContainer = document.getElementById('suggested-tags-container');
    
    const statusDot = document.getElementById('status-dot');
    const statusText = document.getElementById('status-text');
    const btnUndo = document.getElementById('btn-undo');
    const btnCarryForward = document.getElementById('btn-carry-forward');
    
    const tagsDatalist = document.getElementById('tags-datalist');
    const peopleDatalist = document.getElementById('people-datalist');
    const DATE_KEYS = [
        "EXIF:DateTimeOriginal", "DateTimeOriginal",
        "XMP:DateTimeOriginal",
        "EXIF:CreateDate", "CreateDate",
        "XMP:CreateDate",
        "EXIF:ModifyDate", "OnlyDate",
        "XMP:ModifyDate"
    ];

    // App State
    let scannedFolder = '';
    let folderPhotos = [];
    let activePhotoPath = null;
    let selectedThumbnails = [];
    let lastSelectedPath = null;
    let folderSuggestions = {};
    let progressTimer = null;
    let knownTags = [];
    let knownPeople = [];
    let taxonomyNodes = [];
    
    // Abort controller for scan fetches
    let scanAbortController = null;

    // Browser local storage cache configuration (30 minutes timeout)
    const CACHE_TTL_MS = 30 * 60 * 1000;

    /**
     * Say what just happened, without stopping the work to say it.
     *
     * The status line was already carrying this; the modals were stacked on top of
     * it, so finishing a bulk rename meant dismissing a box to report that the thing
     * you watched happen had happened. Modals are kept for the two cases that earn
     * them: a question that must be answered before acting, and a failure that would
     * otherwise pass unnoticed.
     *
     * `kind` is 'ready', 'busy' or 'error'. A 'ready' message with `transient` set
     * falls back to Ready on its own, so the line does not keep claiming the result
     * of something you did five minutes ago.
     */
    let statusResetTimer = null;
    function setStatus(kind, message, { transient = true } = {}) {
        if (statusResetTimer) {
            clearTimeout(statusResetTimer);
            statusResetTimer = null;
        }
        statusDot.className = kind === 'ready'
            ? 'status-indicator-dot'
            : `status-indicator-dot ${kind}`;
        statusText.textContent = message;

        if (kind === 'ready' && transient && message !== 'Ready') {
            statusResetTimer = setTimeout(() => {
                statusText.textContent = 'Ready';
                statusResetTimer = null;
            }, 6000);
        }
    }

    /**
     * Put a validation message beside the field it is about.
     *
     * "Please enter a grouping name" in a modal hides the form you need to correct.
     * Said next to the field, it can be read and fixed in one motion.
     */
    function flagField(input, message) {
        if (!input) {
            setStatus('error', message, { transient: false });
            return;
        }
        input.classList.add('field-invalid');
        input.setAttribute('title', message);
        setStatus('error', message, { transient: false });
        input.focus();
        const clear = () => {
            input.classList.remove('field-invalid');
            input.removeAttribute('title');
            input.removeEventListener('input', clear);
        };
        input.addEventListener('input', clear);
    }

    /**
     * The last undoable write, and how to put it back.
     *
     * One step deep on purpose. The mistake this catches is the one that actually
     * happens -- a bulk write against the wrong selection, noticed immediately -- and
     * a deeper stack would need the photo files to be the source of truth rather than
     * this snapshot, which they are, since anything can edit them behind our back.
     * Holding one step keeps that window short enough to be honest about.
     */
    let lastUndoable = null;

    function recordUndo(entry) {
        lastUndoable = entry;
        updateUndoButton();
    }

    function updateUndoButton() {
        if (!btnUndo) return;
        btnUndo.disabled = !lastUndoable;
        btnUndo.title = lastUndoable
            ? `Undo: ${lastUndoable.label}  (Ctrl+Z)`
            : 'Nothing to undo';
    }

    /**
     * Put back the values captured before the last bulk write.
     *
     * Each photo is restored to the tags and title it had, rather than the change
     * being reversed field by field: a snapshot cannot be confused about what an
     * addition or a removal was, and the write path is the one already trusted.
     */
    function undoLastOperation() {
        if (!lastUndoable) {
            setStatus('ready', 'Nothing to undo');
            return;
        }
        const entry = lastUndoable;
        lastUndoable = null;
        updateUndoButton();

        setStatus('busy', `Undoing: ${entry.label}...`);
        const writes = entry.photos.map(snapshot =>
            fetch('/api/photo/save-metadata', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    path: snapshot.path,
                    title: snapshot.title,
                    tags: snapshot.tags,
                })
            })
            .then(res => res.json())
            .then(data => {
                if (!data.success) throw new Error(data.error || 'failed');
                const photo = folderPhotos.find(p => p.path === snapshot.path);
                if (photo) {
                    photo.tags = snapshot.tags.slice();
                    photo.title = snapshot.title;
                }
            })
        );

        Promise.allSettled(writes).then(results => {
            const failed = results.filter(r => r.status === 'rejected').length;
            renderFileList();
            renderThumbnails();
            if (activePhotoPath) {
                const photo = folderPhotos.find(p => p.path === activePhotoPath);
                if (photo) renderTags(photo.tags || []);
            }
            saveToLocalStorageCache();

            if (failed) {
                // Partly restored is worth saying plainly: the rest is as it was.
                setStatus('error',
                    `Undo restored ${results.length - failed} of ${results.length} photo(s)`,
                    { transient: false });
            } else {
                setStatus('ready', `Undone: ${entry.label}`);
            }
        });
    }

    /** Snapshot the photos a bulk write is about to change. */
    function snapshotPhotos(paths) {
        return paths
            .map(path => folderPhotos.find(p => p.path === path))
            .filter(Boolean)
            .map(photo => ({
                path: photo.path,
                tags: (photo.tags || []).slice(),
                title: photo.title || '',
            }));
    }

    function saveToLocalStorageCache() {
        if (!scannedFolder) return;
        const cacheEntry = {
            timestamp: Date.now(),
            photos: folderPhotos,
            suggestions: folderSuggestions
        };
        try {
            localStorage.setItem(`tagpup_cache_${scannedFolder}`, JSON.stringify(cacheEntry));
        } catch (e) {
            console.warn("Storage quota exceeded, could not cache folder data.");
        }
    }

    function updateSuggestButtonState(status = null) {
        if (!scannedFolder || folderPhotos.length === 0) {
            btnSuggestTags.disabled = true;
            return;
        }
        if (status === 'preparing' || status === 'running') {
            btnSuggestTags.disabled = true;
            return;
        }
        const hasUnprocessed = folderPhotos.some(photo => !folderSuggestions[photo.path]);
        btnSuggestTags.disabled = !hasUnprocessed;
    }

    // Load Datalists on Startup
    fetchKnownTagsAndPeople();

    // Check for folder path in URL on startup
    const params = new URLSearchParams(window.location.search);
    const initialPath = params.get('path');
    if (initialPath) {
        folderPathInput.value = initialPath;
        scanFolder(false);
        checkIndexingStatus(initialPath);
    }

    // Event Listeners setup
    btnBrowseFolder.addEventListener('click', browseFolder);
    folderPathInput.addEventListener('input', () => {
        const val = folderPathInput.value;
        if (!val) return;
        fetch(`/api/autocomplete-folder?path=${encodeURIComponent(val)}`)
            .then(res => res.json())
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
    btnScanFolder.addEventListener('click', () => scanFolder(true));
    btnIndexFolder.addEventListener('click', startIndexing);
    btnSuggestTags.addEventListener('click', startSuggestions);
    btnRefreshList.addEventListener('click', () => scanFolder(true));
    
    photoSearch.addEventListener('input', filterFileList);
    folderViewHeader.addEventListener('click', showFolderView);
    
    btnSelectAllThumbnails.addEventListener('click', selectAllThumbnails);
    btnSelectNoneThumbnails.addEventListener('click', selectNoneThumbnails);
    btnBulkAddPeople.addEventListener('click', bulkAddPeopleToSelection);
    btnBulkAddTags.addEventListener('click', bulkAddTagsToSelection);
    bulkAddPeopleInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') bulkAddPeopleToSelection(); });
    bulkAddTagsInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') bulkAddTagsToSelection(); });
    btnFolderAutoApply.addEventListener('click', applyFolderSuggestionsLevel);
    
    btnRotateLeft.addEventListener('click', () => rotatePhoto('left'));
    btnRotateRight.addEventListener('click', () => rotatePhoto('right'));
    btnDeletePhoto.addEventListener('click', deleteActivePhoto);
    
    btnSaveTitle.addEventListener('click', saveSingleTitle);
    btnAddPerson.addEventListener('click', saveSingleAddPerson);
    btnAddTag.addEventListener('click', saveSingleAddTag);
    inputPhotoTitle.addEventListener('keydown', (e) => { if (e.key === 'Enter') saveSingleTitle(); });
    inputAddPerson.addEventListener('keydown', (e) => { if (e.key === 'Enter') saveSingleAddPerson(); });
    inputAddTag.addEventListener('keydown', (e) => { if (e.key === 'Enter') saveSingleAddTag(); });

    /**
     * Commit what was typed when a field is left, not only when Enter is pressed.
     *
     * These fields used to commit on Enter alone, and nothing tracked unsaved text.
     * Typing a tag and then clicking the next photo discarded it silently:
     * selectPhoto simply repopulated the panel. Each loss is small, which is what
     * made it easy to miss and tiresome to keep hitting.
     *
     * Escape is the way out for text you have decided against -- it clears the field
     * so that leaving it commits nothing.
     */
    function commitFieldOnBlur(input, save, isPersonField) {
        input.addEventListener('blur', async () => {
            if (input.dataset.abandoned === 'true') {
                input.dataset.abandoned = '';
                return;
            }
            const typed = input.value.trim();
            if (!typed) return;

            // Would saving this have to ask where the tag belongs? If so, leave it
            // for Enter or the Add button: a modal about the photo you just left,
            // raised while you are looking at the next one, is its own kind of lost
            // work.
            const parts = typed.split(',').map(t => t.trim()).filter(Boolean);
            for (const part of parts) {
                const resolved = await resolveTagOrPerson(part, isPersonField, { prompt: false });
                if (!resolved) {
                    setStatus('ready',
                        `"${part}" is new \u2014 press Enter to say where it belongs`);
                    return;
                }
            }
            save();
        });
        input.addEventListener('keydown', (e) => {
            if (e.key !== 'Escape') return;
            // Mark before clearing: clearing moves focus in some browsers, and the
            // blur handler must know this text was abandoned on purpose.
            input.dataset.abandoned = 'true';
            input.value = '';
            input.blur();
        });
    }

    commitFieldOnBlur(inputAddPerson, saveSingleAddPerson, true);
    commitFieldOnBlur(inputAddTag, saveSingleAddTag, false);

    /**
     * Uncommitted text belongs to the photo it was typed for, and to no other.
     *
     * These fields were never cleared on changing photo, so text typed for one
     * survived into the next and Enter there applied it to the wrong photo. Clearing
     * it says what was dropped rather than doing either silently.
     */
    function clearPendingEntry() {
        const dropped = [inputAddTag, inputAddPerson]
            .filter(el => el && el.value.trim())
            .map(el => {
                const text = el.value.trim();
                el.value = '';
                return text;
            });
        if (dropped.length) {
            setStatus('ready', `Not saved: ${dropped.join(', ')}`);
        }
    }
    // The title field is not in this list on purpose: it is pre-filled with the
    // photo's current title, so blurring it unchanged would re-save the same value
    // on every pass through the panel. It commits on Enter and on its Save button.

    btnSuggestTitleWand.addEventListener('click', applySuggestedTitle);
    btnApplyAllSingleSugg.addEventListener('click', applyAllSingleSuggestions);
    detailPath.addEventListener('click', openPhotoInExplorer);
    btnApplyTimeshift.addEventListener('click', applyTimeShift);
    
    function setThumbnailSize(size) {
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
    }
    
    btnSizeSmall.addEventListener('click', () => setThumbnailSize('small'));
    btnSizeMedium.addEventListener('click', () => setThumbnailSize('medium'));
    btnSizeLarge.addEventListener('click', () => setThumbnailSize('large'));
    
    // Restore saved size preference
    const savedSize = localStorage.getItem('tagpup_thumbnail_size') || 'medium';
    setThumbnailSize(savedSize);
    btnToggleTimeshift.addEventListener('click', () => {
        timeshiftPanel.classList.toggle('hidden');
        btnToggleTimeshift.classList.toggle('active');
        updateCameraHighlights();
    });
    timeshiftCameraSelect.addEventListener('change', updateCameraHighlights);
    
    btnToggleRename.addEventListener('click', () => {
        renamePanel.classList.toggle('hidden');
        btnToggleRename.classList.toggle('active');
        if (!renamePanel.classList.contains('hidden')) {
            renameGroupingInput.focus();
        }
    });

    btnApplyRename.addEventListener('click', () => {
        const grouping = renameGroupingInput.value.trim();
        if (!grouping) {
            flagField(renameGroupingInput, 'Enter a grouping name to rename by');
            return;
        }

        if (selectedThumbnails.length === 0) {
            setStatus('error', 'Select some photos first \u2014 nothing is selected',
                      { transient: false });
            return;
        }

        if (!confirm(`Are you sure you want to smart-rename the ${selectedThumbnails.length} selected photos?`)) {
            return;
        }

        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Smart renaming...';
        btnApplyRename.disabled = true;

        fetch('/api/folder/rename-photos', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                folder_path: scannedFolder,
                photo_paths: selectedThumbnails,
                grouping: grouping
            })
        })
        .then(res => {
            if (!res.ok) return res.json().then(e => { throw new Error(e.error || 'Rename failed') });
            return res.json();
        })
        .then(data => {
            folderPhotos = data.updated_photos;
            selectedThumbnails = [];
            lastSelectedPath = null;
            
            renameGroupingInput.value = '';
            renamePanel.classList.add('hidden');
            btnToggleRename.classList.remove('active');
            
            renderFileList();
            renderThumbnails();
            updateSelectedThumbnailsCount();
            saveToLocalStorageCache();
            
            setStatus('ready', `Renamed ${selectedThumbnails.length} photo(s)`);
        })
        .catch(err => {
            console.error(err);
            setStatus('error', 'Smart rename failed', { transient: false });
            btnApplyRename.disabled = false;
            alert("Error during smart rename: " + err.message);
        });
    });

    /**
     * Move the selection through the list by a number of steps.
     *
     * The list order is the photo order, so this is what both the up/down keys and
     * the left/right keys use -- "the row below" and "the next photo" are the same
     * move, and someone flipping through a shoot should not have to know that.
     */
    function stepPhoto(delta) {
        const items = Array.from(photoList.querySelectorAll('.photo-item-file'));
        if (items.length === 0) return false;

        const currentIndex = items.findIndex(
            item => item.getAttribute('data-path') === activePhotoPath
        );
        let nextIndex;
        if (currentIndex === -1) {
            // Nothing selected yet: forwards starts at the top, backwards at the end.
            nextIndex = delta > 0 ? 0 : items.length - 1;
        } else {
            nextIndex = Math.min(Math.max(currentIndex + delta, 0), items.length - 1);
        }
        if (nextIndex === currentIndex) return false;

        selectPhoto(items[nextIndex].getAttribute('data-path'));
        items[nextIndex].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        return true;
    }

    /**
     * Should a keystroke be left to the field the caret is in?
     *
     * Only where the keystroke means something to the text being typed. The filter
     * box is not such a field: it is a search control, and the natural move is to
     * narrow the list and then walk the results with the arrow keys. A blanket INPUT
     * check used to catch it and return before preventDefault, so the browser
     * scrolled the page instead -- the keys looked broken in the one place you most
     * wanted them.
     */
    function keystrokeBelongsToField(el) {
        if (!el) return false;
        if (el === photoSearch) return false;
        return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable;
    }

    document.addEventListener('keydown', (e) => {
        if (keystrokeBelongsToField(document.activeElement)) return;

        // Ctrl+D copies the previous photo's tags onto this one, so that tagging a
        // shoot is: tag the first, then arrow across and repeat. It is checked
        // before the modifier guard below, which exists to leave browser shortcuts
        // alone.
        if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === 'd' || e.key === 'D')) {
            e.preventDefault();
            carryTagsForward();
            return;
        }
        if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === 'z' || e.key === 'Z')) {
            e.preventDefault();
            undoLastOperation();
            return;
        }
        if (e.ctrlKey || e.metaKey || e.altKey) return;

        // Left/right are the same move as up/down. Up/down reads as "the row below"
        // in the list; left/right reads as "the next photo" over the image. Both are
        // offered because which one a person reaches for depends on where they are
        // looking, and there is no reason to make them guess right.
        const steps = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 };
        if (!(e.key in steps)) return;

        e.preventDefault();
        stepPhoto(steps[e.key]);
    });

    // Dynamic Autocomplete loaders
    function fetchKnownTagsAndPeople() {
        loadTaxonomy().then(() => {
            fetch('/api/tags')
                .then(res => res.json())
                .then(data => {
                    knownTags = data;
                    updateTagsDatalist();
                    updatePeopleDatalist();
                })
                .catch(err => console.error("Error loading tags taxonomy:", err));

            fetch('/api/people')
                .then(res => res.json())
                .then(data => {
                    knownPeople = data;
                    updatePeopleDatalist();
                })
                .catch(err => console.error("Error loading database people:", err));
        }).catch(err => console.error("Error loading taxonomy tree:", err));
    }

    function isPersonTag(tag) {
        if (!tag) return false;
        const tagLower = tag.toLowerCase().trim();
        if (tagLower === 'family' || tagLower === 'friends' || tagLower === 'people' || tagLower === 'pets') {
            return true;
        }
        if (tag.startsWith('Family/') || tag.startsWith('Friends/') || tag.startsWith('People/') || tag.startsWith('Pets/')) {
            return true;
        }
        const leaf = tag.includes('/') ? tag.split('/').pop().trim() : tag;
        if (knownPeople.includes(leaf)) {
            return true;
        }
        // Fallback: search taxonomyNodes dynamically for has_face flag
        if (taxonomyNodes && Array.isArray(taxonomyNodes)) {
            const foundNode = taxonomyNodes.find(n => n.tag === tag);
            if (foundNode && foundNode.has_face === 1) {
                return true;
            }
            if (tag.includes('/')) {
                const parts = tag.split('/');
                for (let i = 1; i <= parts.length; i++) {
                    const ancestorTag = parts.slice(0, i).join('/');
                    const ancestorNode = taxonomyNodes.find(n => n.tag === ancestorTag);
                    if (ancestorNode && ancestorNode.has_face === 1) {
                        return true;
                    }
                }
            }
        }
        return false;
    }

    /** The last segment of a tag path, which is the name a person is known by. */
    function leafOf(tag) {
        if (!tag) return '';
        return tag.includes('/') ? tag.split('/').pop().trim() : tag.trim();
    }

    /**
     * Does this photo already carry this tag, or this same person under another name?
     *
     * A person belongs in the keywords as a path, "People/Hazel Brookmire". But face
     * recognition and the tag suggester both speak in leaf names, so a plain
     * `tags.includes()` compared "Hazel Brookmire" against "People/Hazel Brookmire",
     * found no match, and wrote the person in a second time as a bare leaf. Identity is
     * the leaf; the path is only where they are filed.
     */
    function photoAlreadyHas(photo, tag) {
        const tags = (photo && photo.tags) || [];
        if (tags.includes(tag)) return true;
        if (!isPersonTag(tag)) return false;
        const leaf = leafOf(tag).toLowerCase();
        return tags.some(t => isPersonTag(t) && leafOf(t).toLowerCase() === leaf);
    }

    function updateTagsDatalist() {
        tagsDatalist.innerHTML = '';
        
        const uniqueFolderTags = new Set();
        folderPhotos.forEach(p => {
            if (p.tags) p.tags.forEach(t => {
                if (!isPersonTag(t)) {
                    uniqueFolderTags.add(t);
                }
            });
        });

        const filteredKnownTags = knownTags.filter(t => {
            return !isPersonTag(t);
        });

        const combined = Array.from(new Set([...filteredKnownTags, ...uniqueFolderTags])).sort();
        combined.forEach(t => {
            const opt = document.createElement('option');
            opt.value = t;
            tagsDatalist.appendChild(opt);
        });
        updateFolderAutoApplyState();
    }

    function updatePeopleDatalist() {
        peopleDatalist.innerHTML = '';
        
        const peopleSet = new Set();
        
        // Add all person tags from knownTags
        knownTags.forEach(t => {
            if (isPersonTag(t)) {
                peopleSet.add(t);
            }
        });
        
        // Helper to resolve a flat name to a full person tag path
        function resolveToPersonPath(name) {
            // Find in knownTags first
            const matchedTag = knownTags.find(t => {
                if (!isPersonTag(t)) return false;
                const leaf = t.split('/').pop().trim();
                return leaf.toLowerCase() === name.toLowerCase();
            });
            if (matchedTag) return matchedTag;
            
            // Default fallback
            return `People/${name}`;
        }
        
        // Add from knownPeople
        knownPeople.forEach(name => {
            peopleSet.add(resolveToPersonPath(name));
        });
        
        // Add from folderPhotos
        folderPhotos.forEach(p => {
            if (p.tags) {
                p.tags.forEach(t => {
                    if (isPersonTag(t)) {
                        peopleSet.add(t);
                    }
                });
            }
            if (p.people) {
                p.people.forEach(name => {
                    peopleSet.add(resolveToPersonPath(name));
                });
            }
        });

        // One entry per person, whatever shape their tag arrived in. The list was
        // built from three sources -- person tags, known people, and the tags on the
        // photos in view -- and a person recorded both as "Josephine Sandoval" and as
        // "People/Josephine Sandoval" appeared twice, which is a choice nobody can make
        // correctly because both do the same thing.
        //
        // The pathed form wins: it says where the person belongs, and a bare name is
        // what you get when that was lost.
        const byPerson = new Map();
        Array.from(peopleSet).forEach(tag => {
            const leaf = tag.split('/').pop().trim().toLowerCase();
            if (!leaf) return;
            const existing = byPerson.get(leaf);
            if (!existing || (!existing.includes('/') && tag.includes('/'))) {
                byPerson.set(leaf, tag);
            }
        });

        Array.from(byPerson.values()).sort().forEach(p => {
            const opt = document.createElement('option');
            opt.value = p;
            peopleDatalist.appendChild(opt);
        });
    }

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

    // Native Folder Browser Dialog Trigger
    function browseFolder() {
        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Browsing...';
        
        fetch('/api/browse-folder')
            .then(res => res.json())
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

    // Scans a folder
    function scanFolder(forceRefresh = false) {
        const path = folderPathInput.value.trim();
        if (!path) {
            flagField(folderPathInput, 'Choose or type a folder to scan');
            return;
        }

        // Reset folder-specific suggestions state to prevent leaks
        folderSuggestions = {};

        // Cache lookup if not forcing refresh
        if (!forceRefresh) {
            const rawCache = localStorage.getItem(`tagpup_cache_${path}`);
            if (rawCache) {
                try {
                    const cacheEntry = JSON.parse(rawCache);
                    const age = Date.now() - cacheEntry.timestamp;
                    if (age < CACHE_TTL_MS) {
                        scannedFolder = path;
                        folderPhotos = cacheEntry.photos;
                        folderSuggestions = cacheEntry.suggestions || {};
                        updateListStats('(cached)');
                        folderViewHeader.classList.remove('hidden');
                        updateSuggestButtonState();
                        btnToggleRename.disabled = false;
                        btnToggleTimeshift.disabled = false;
                        if (Object.keys(folderSuggestions).length > 0) {
                            btnFolderAutoApply.disabled = false;
                        } else {
                            btnFolderAutoApply.disabled = true;
                        }
                        
                        renderFileList();
                        updateTagsDatalist();
                        updatePeopleDatalist();
                        populateCameraModelsDropdown();
                        
                        // Update URL
                        const url = new URL(window.location);
                        url.searchParams.set('path', path);
                        window.history.replaceState({}, '', url);
                        
                        checkSuggestionsStatus(path);
                        
                        // If active photo path is set, reload its data
                        if (activePhotoPath) {
                            const matched = folderPhotos.find(p => p.path === activePhotoPath);
                            if (matched) {
                                selectPhoto(activePhotoPath);
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

        if (scanAbortController) {
            scanAbortController.abort();
        }
        scanAbortController = new AbortController();

        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Scanning...';
        listStats.textContent = 'Scanning...';
        photoList.querySelectorAll('.photo-item-file').forEach(el => el.remove());

        fetch(`/api/folder/scan?path=${encodeURIComponent(path)}&force=${forceRefresh}`, { signal: scanAbortController.signal })
            .then(res => {
                if (!res.ok) return res.json().then(e => { throw new Error(e.error || 'Scan failed') });
                return res.json();
            })
            .then(data => {
                scannedFolder = path;
                folderPhotos = data;
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
                updateSuggestButtonState();
                btnToggleRename.disabled = false;
                btnToggleTimeshift.disabled = false;
                btnFolderAutoApply.disabled = true;
                
                renderFileList();
                updateTagsDatalist();
                updatePeopleDatalist();
                populateCameraModelsDropdown();
                
                // Start tracking background progress check
                checkSuggestionsStatus(path);
                
                // If active photo path is set, reload its data
                if (activePhotoPath) {
                    const matched = folderPhotos.find(p => p.path === activePhotoPath);
                    if (matched) {
                        selectPhoto(activePhotoPath);
                    } else {
                        showFolderView();
                    }
                } else {
                    showFolderView();
                }

                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Ready';
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

    /**
     * Has this photo been tagged at all?
     *
     * Deliberately a low bar: one tag, one person, or a title counts. The question a
     * tagging session asks is "have I been here yet", not "is this perfect", and a
     * stricter test would mark honest work as unfinished.
     */
    function isPhotoTagged(photo) {
        if (!photo) return false;
        return (photo.tags || []).length > 0
            || (photo.people || []).length > 0
            || (photo.title || '').trim().length > 0;
    }

    /** How much of the loaded folder has been tagged. */
    function taggedCounts() {
        const total = folderPhotos.length;
        const tagged = folderPhotos.filter(isPhotoTagged).length;
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
    function visiblePhotos() {
        const query = photoSearch.value.toLowerCase().trim();
        const untaggedOnly = filterUntaggedOnly && filterUntaggedOnly.checked;
        return folderPhotos.filter(photo => {
            // Narrowing to what is left turns a folder into a work queue.
            if (untaggedOnly && isPhotoTagged(photo)) return false;
            if (!query) return true;
            const fname = photo.filename || photo.path.split(/[/\\]/).pop() || '';
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
    function updateListStats(suffix) {
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
    function renderFileList() {
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
            if (activePhotoPath === photo.path) {
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
            const fullName = photo.filename || photo.path.split(/[/\\]/).pop() || "";
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
            if (folderSuggestions[photo.path]) {
                const badge = document.createElement('span');
                badge.className = 'photo-item-has-sugg';
                badge.textContent = 'AI';
                li.appendChild(badge);
            }

            li.addEventListener('click', () => selectPhoto(photo.path));
            li.addEventListener('keydown', (ev) => {
                if (ev.key === 'Enter' || ev.key === ' ') {
                    ev.preventDefault();
                    selectPhoto(photo.path);
                }
            });
            fragment.appendChild(li);
        });
        photoList.appendChild(fragment);
    }

    let searchTimeout = null;
    if (filterUntaggedOnly) {
        filterUntaggedOnly.addEventListener('change', () => {
            renderFileList();
            renderThumbnails();
        });
    }

    function filterFileList() {
        if (searchTimeout) clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            renderFileList();
            if (!folderViewContent.classList.contains('hidden')) {
                renderThumbnails();
            }
        }, 150);
    }

    // Select Folder Thumbnail View
    function showFolderView() {
        activePhotoPath = null;
        facesRequestToken++;               // abandon any in-flight face lookup
        if (facesSection) facesSection.classList.add('hidden');
        
        // Highlight folder view item in list
        photoList.querySelectorAll('.photo-item').forEach(el => el.classList.remove('active'));
        folderViewHeader.classList.add('active');

        // Hide single views, show Folder Grid
        panelContent.classList.add('hidden');
        emptyState.classList.add('hidden');
        folderViewContent.classList.remove('hidden');

        // Populate Folder View details
        folderViewStats.textContent = `${folderPhotos.length} photos`;
        
        renderThumbnails();
        updateSelectedThumbnailsCount();
    }


    // ---- Grid context menu -------------------------------------------------
    // The selection toolbar lives in a header that scrolls out of view in a long
    // folder, which made "select none" hard to reach at exactly the moment it is
    // wanted. A right-click menu puts the selection actions where the cursor is.
    const gridContextMenu = document.getElementById('grid-context-menu');

    function hideGridContextMenu() {
        if (gridContextMenu) gridContextMenu.classList.add('hidden');
    }

    function showGridContextMenu(x, y, pathUnderCursor) {
        if (!gridContextMenu) return;
        const count = selectedThumbnails.length;
        const total = folderPhotos.length;

        gridContextMenu.querySelectorAll('[data-requires-selection]').forEach(el => {
            el.classList.toggle('disabled', count === 0);
        });
        const countLabel = gridContextMenu.querySelector('#context-menu-count');
        if (countLabel) {
            countLabel.textContent = count === 0
                ? `No photos selected (${total} in folder)`
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

    function invertThumbnailSelection() {
        const all = folderPhotos.map(p => p.path);
        const next = all.filter(p => !selectedThumbnails.includes(p));
        selectedThumbnails.length = 0;
        next.forEach(p => selectedThumbnails.push(p));
        renderThumbnails();
        updateSelectedThumbnailsCount();
    }

    function selectRangeToCursor(path) {
        if (!path) return;
        handleCardSelectionClick(path, true, null, true);
    }

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
                    fetch('/api/photo/open-explorer', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ path: pathUnderCursor })
                    }).catch(err => console.error('open-explorer failed', err));
                }
            }
        });
    }


    // ---- Detected faces ----------------------------------------------------
    // Face recognition already ran for this photo -- the suggester needs it to propose
    // people -- but nothing ever showed it. Without the crops you learn that a face went
    // unrecognised only later, in TagTuner. This puts that in front of you while tagging.
    let facesRequestToken = 0;

    function renderPhotoFaces(photoPath) {
        if (!facesSection || !facesStrip) return;
        const token = ++facesRequestToken;

        facesStrip.innerHTML = '';
        facesSection.classList.add('hidden');
        if (facesSummary) facesSummary.textContent = '';

        fetch(`/api/photo-faces?path=${encodeURIComponent(photoPath)}`)
            .then(res => res.ok ? res.json() : { faces: [] })
            .then(data => {
                // A slower reply for a previously selected photo must not overwrite
                // the strip for the one now on screen.
                if (token !== facesRequestToken) return;
                const faces = data.faces || [];
                if (faces.length === 0) return;

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

                    const img = document.createElement('img');
                    img.className = 'face-card-img';
                    img.src = `/api/face-crop?id=${face.id}`;
                    img.alt = face.name || 'Unidentified face';
                    card.appendChild(img);

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
                        label.textContent = `${face.suggestion}? ${pct}%`;
                        card.title = `Closest match: ${face.suggestion} (${pct}%). Not assigned.`;
                        label.classList.add('face-card-suggestion');
                    } else {
                        label.textContent = 'Unidentified';
                        card.title = 'No similar face in this database yet';
                    }
                    card.appendChild(label);

                    // Clicking a suggestion adds that person to the photo, which is the
                    // small correction TagPup is meant for; deeper work is TagTuner's.
                    if (!face.name && face.suggestion) {
                        card.classList.add('face-card-actionable');
                        card.addEventListener('click', () => {
                            applySuggestedTagDirect(face.suggestion, true);
                        });
                    }
                    facesStrip.appendChild(card);
                });
            })
            .catch(err => console.error('Error loading faces:', err));
    }

    // Render Grid Thumbnails
    function renderThumbnails() {
        thumbnailsGrid.innerHTML = '';

        const filtered = visiblePhotos();

        if (filtered.length === 0) {
            thumbnailsGrid.innerHTML = '<div style="grid-column: 1/-1; text-align: center; color: var(--text-muted); padding: 40px;">No photos found matching filter.</div>';
            return;
        }

        const fragment = document.createDocumentFragment();
        filtered.forEach(photo => {
            const card = document.createElement('div');
            card.className = 'thumbnail-card';
            if (selectedThumbnails.includes(photo.path)) {
                card.classList.add('selected');
            }
            card.setAttribute('data-path', photo.path);

            const chkContainer = document.createElement('div');
            chkContainer.className = 'thumbnail-checkbox-container';
            const chk = document.createElement('input');
            chk.type = 'checkbox';
            chk.className = 'thumbnail-checkbox';
            chk.checked = selectedThumbnails.includes(photo.path);
            chk.addEventListener('click', (e) => {
                e.stopPropagation();
                handleCardSelectionClick(photo.path, chk.checked, card, e.shiftKey);
            });
            chkContainer.appendChild(chk);
            card.appendChild(chkContainer);

            const imgWrapper = document.createElement('div');
            imgWrapper.className = 'thumbnail-img-wrapper';

            // Add AI suggestion badge if suggestions exist
            if (folderSuggestions[photo.path]) {
                const aiBadge = document.createElement('span');
                aiBadge.className = 'thumbnail-has-sugg';
                aiBadge.textContent = 'AI';
                imgWrapper.appendChild(aiBadge);
            }

            const img = document.createElement('img');
            img.src = `/api/photo-file?path=${encodeURIComponent(photo.path)}&size=300`;
            img.loading = 'lazy';
            img.alt = photo.filename;
            imgWrapper.appendChild(img);
            card.appendChild(imgWrapper);

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
                name.title = `Title: ${photo.title}\nFile: ${fullName}\n(Click to edit title)`;
            } else {
                name.textContent = displayName;
                name.title = `File: ${fullName}\n(Click to add title)`;
            }
            
            name.addEventListener('click', (e) => {
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
                    
                    statusDot.className = 'status-indicator-dot busy';
                    statusText.textContent = 'Saving title...';
                    
                    fetch('/api/photo/save-metadata', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ path: photo.path, title: newTitle, tags: photo.tags })
                    })
                    .then(res => res.json())
                    .then(data => {
                        if (data.success) {
                            const oldPath = photo.path;
                            photo.title = newTitle;
                            photo.captions = newTitle ? [newTitle] : [];
                            
                            if (data.new_path && data.new_path !== oldPath) {
                                photo.path = data.new_path;
                                photo.filename = data.new_path.split(/[/\\]/).pop();
                                if (activePhotoPath === oldPath) {
                                    activePhotoPath = data.new_path;
                                }
                            }
                            
                            renderFileList();
                            renderThumbnails();
                            
                            if (activePhotoPath === photo.path) {
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
                    });
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
            const rawMeta = photo.raw_metadata || {};
            for (let k of DATE_KEYS) {
                if (rawMeta[k]) {
                    const localD = parseExifDateToLocalDate(rawMeta[k]);
                    if (localD) {
                        const stats = getFolderDateStats();
                        dateVal = formatFriendlyDateSingle(localD, stats);
                    }
                    break;
                }
            }
            dateSpan.textContent = dateVal;
            textInfo.appendChild(dateSpan);
            infoRow.appendChild(textInfo);

            const btnDetail = document.createElement('button');
            btnDetail.className = 'btn-thumbnail-detail';
            btnDetail.title = 'View details and edit metadata';
            btnDetail.innerHTML = '🔍';
            btnDetail.addEventListener('click', (e) => {
                e.stopPropagation();
                selectPhoto(photo.path);
            });
            infoRow.appendChild(btnDetail);
            
            card.appendChild(infoRow);

            // Click toggles card selection
            card.addEventListener('click', (e) => {
                if (e.target.tagName === 'INPUT') return;
                const isSelected = selectedThumbnails.includes(photo.path);
                const nextChecked = !isSelected;
                chk.checked = nextChecked;
                handleCardSelectionClick(photo.path, nextChecked, card, e.shiftKey);
            });

            fragment.appendChild(card);
        });
        thumbnailsGrid.appendChild(fragment);
        updateCameraHighlights();
    }

    function handleCardSelectionClick(path, isChecked, cardElement, isShiftKey) {
        if (isShiftKey && lastSelectedPath) {
            const cardElements = Array.from(thumbnailsGrid.querySelectorAll('.thumbnail-card'));
            const paths = cardElements.map(el => el.getAttribute('data-path'));
            
            const startIdx = paths.indexOf(lastSelectedPath);
            const endIdx = paths.indexOf(path);
            
            if (startIdx !== -1 && endIdx !== -1) {
                const minIdx = Math.min(startIdx, endIdx);
                const maxIdx = Math.max(startIdx, endIdx);
                
                for (let i = minIdx; i <= maxIdx; i++) {
                    const currentPath = paths[i];
                    const currentCard = cardElements[i];
                    const currentChk = currentCard.querySelector('.thumbnail-checkbox');
                    
                    if (currentChk) currentChk.checked = isChecked;
                    
                    const idx = selectedThumbnails.indexOf(currentPath);
                    if (isChecked) {
                        if (idx === -1) selectedThumbnails.push(currentPath);
                        currentCard.classList.add('selected');
                    } else {
                        if (idx > -1) selectedThumbnails.splice(idx, 1);
                        currentCard.classList.remove('selected');
                    }
                }
                updateSelectedThumbnailsCount();
                lastSelectedPath = path;
                return;
            }
        }
        
        toggleThumbnailSelection(path, isChecked, cardElement);
        lastSelectedPath = path;
    }

    function toggleThumbnailSelection(path, isChecked, cardElement) {
        const idx = selectedThumbnails.indexOf(path);
        if (isChecked) {
            if (idx === -1) selectedThumbnails.push(path);
            cardElement.classList.add('selected');
        } else {
            if (idx > -1) selectedThumbnails.splice(idx, 1);
            cardElement.classList.remove('selected');
        }
        updateSelectedThumbnailsCount();
    }

    function updateSelectedThumbnailsCount() {
        selectedThumbnailsCount.textContent = `Selected: ${selectedThumbnails.length}`;
        selectionSummaryCount.textContent = `Selected: ${selectedThumbnails.length}`;
        
        // The panel stays mounted whether or not anything is selected. It used to be
        // hidden on an empty selection, so clearing and re-selecting made the whole
        // right-hand column collapse and reflow on every click.
        folderSelectionSidebar.classList.remove('hidden');
        if (selectionEmptyHint) {
            selectionEmptyHint.classList.toggle('hidden', selectedThumbnails.length > 0);
        }
        if (selectionSummaryScroll) {
            selectionSummaryScroll.classList.toggle('hidden', selectedThumbnails.length === 0);
        }

        if (selectedThumbnails.length > 0) {
            
            // Gather statistics
            const selectedPhotos = folderPhotos.filter(p => selectedThumbnails.includes(p.path));
            
            // Calculate Date Taken Range
            const dateObjs = [];
            selectedPhotos.forEach(photo => {
                let photoDate = null;
                const rawMeta = photo.raw_metadata || {};
                for (let k of DATE_KEYS) {
                    if (rawMeta[k]) {
                        const parsed = parseExifDateToLocalDate(rawMeta[k]);
                        if (parsed) {
                            photoDate = parsed;
                            break;
                        }
                    }
                }
                if (photoDate) {
                    dateObjs.push(photoDate);
                }
            });

            if (dateObjs.length === 0) {
                selectionDateLabel.textContent = "Date Taken";
                selectionDateValue.textContent = "Unknown";
            } else if (dateObjs.length === 1) {
                selectionDateLabel.textContent = "Date Taken";
                const stats = getFolderDateStats();
                selectionDateValue.textContent = formatFriendlyDateSingle(dateObjs[0], stats);
            } else {
                dateObjs.sort((a, b) => a - b);
                selectionDateLabel.textContent = "Date Taken Range";
                selectionDateValue.textContent = formatFriendlyDateRange(dateObjs[0], dateObjs[dateObjs.length - 1]);
            }

            const peopleCounts = {};
            const tagCounts = {};
            
            selectedPhotos.forEach(photo => {
                const photoPeople = photo.people || [];
                const tags = photo.tags || [];
                
                tags.forEach(tag => {
                    const isPerson = isPersonTag(tag) || photoPeople.includes(tag);
                    if (isPerson) {
                        const leaf = tag.includes('/') ? tag.split('/').pop().trim() : tag;
                        peopleCounts[leaf] = (peopleCounts[leaf] || 0) + 1;
                    } else {
                        tagCounts[tag] = (tagCounts[tag] || 0) + 1;
                    }
                });
            });
            
            // Render People List
            selectionPeopleList.innerHTML = '';
            const peopleKeys = Object.keys(peopleCounts).sort();
            if (peopleKeys.length === 0) {
                selectionPeopleList.innerHTML = '<span style="color: var(--text-muted); font-size: 12px; padding: 4px 0;">None</span>';
            } else {
                peopleKeys.forEach(p => {
                    const count = peopleCounts[p];
                    const chip = document.createElement('span');
                    chip.className = 'selection-summary-chip';
                    chip.textContent = `${p} (${count})`;
                    
                    // Show apply arrow only if it's not present on ALL selected photos
                    if (count < selectedThumbnails.length) {
                        const applyIcon = document.createElement('span');
                        applyIcon.className = 'selection-summary-chip-apply';
                        applyIcon.textContent = ' ➡️';
                        applyIcon.title = `Apply "${p}" to all selected photos`;
                        applyIcon.addEventListener('click', (e) => {
                            e.stopPropagation();
                            applyTagToAllSelected(p, true);
                        });
                        chip.appendChild(applyIcon);
                    }
                    
                    // Remove icon
                    const removeIcon = document.createElement('span');
                    removeIcon.className = 'selection-summary-chip-remove';
                    removeIcon.textContent = ' ×';
                    removeIcon.title = `Remove "${p}" from all selected photos`;
                    removeIcon.addEventListener('click', (e) => {
                        e.stopPropagation();
                        removeTagFromAllSelected(p, true);
                    });
                    chip.appendChild(removeIcon);
                    
                    selectionPeopleList.appendChild(chip);
                });
            }
            
            // Render Tags List
            selectionTagsList.innerHTML = '';
            const tagKeys = Object.keys(tagCounts).sort();
            if (tagKeys.length === 0) {
                selectionTagsList.innerHTML = '<span style="color: var(--text-muted); font-size: 12px; padding: 4px 0;">None</span>';
            } else {
                tagKeys.forEach(t => {
                    const count = tagCounts[t];
                    const chip = document.createElement('span');
                    chip.className = 'selection-summary-chip';
                    chip.textContent = `${t} (${count})`;
                    
                    // Show apply arrow only if it's not present on ALL selected photos
                    if (count < selectedThumbnails.length) {
                        const applyIcon = document.createElement('span');
                        applyIcon.className = 'selection-summary-chip-apply';
                        applyIcon.textContent = ' ➡️';
                        applyIcon.title = `Apply "${t}" to all selected photos`;
                        applyIcon.addEventListener('click', (e) => {
                            e.stopPropagation();
                            applyTagToAllSelected(t, false);
                        });
                        chip.appendChild(applyIcon);
                    }
                    
                    // Remove icon
                    const removeIcon = document.createElement('span');
                    removeIcon.className = 'selection-summary-chip-remove';
                    removeIcon.textContent = ' ×';
                    removeIcon.title = `Remove "${t}" from all selected photos`;
                    removeIcon.addEventListener('click', (e) => {
                        e.stopPropagation();
                        removeTagFromAllSelected(t, false);
                    });
                    chip.appendChild(removeIcon);
                    selectionTagsList.appendChild(chip);
                });
            }

            // Tally suggested tags & people from folderSuggestions
            const suggPeopleCounts = {};
            const suggTagCounts = {};
            
            selectedPhotos.forEach(photo => {
                const sugg = folderSuggestions[photo.path];
                if (sugg) {
                    const photoPeople = photo.people || [];
                    const tags = photo.tags || [];
                    
                    // Suggestions might contain people
                    if (sugg.people) {
                        sugg.people.forEach(p => {
                            const leaf = p.name;
                            // Only suggest if not already added to this photo
                            const alreadyAdded = tags.includes(leaf) || photoPeople.includes(leaf);
                            if (!alreadyAdded) {
                                suggPeopleCounts[leaf] = (suggPeopleCounts[leaf] || 0) + 1;
                            }
                        });
                    }
                    
                    // Suggestions might contain general tags
                    if (sugg.tags) {
                        sugg.tags.forEach(t => {
                            const leaf = t.tag;
                            const isPerson = isPersonTag(leaf);
                            const alreadyAdded = tags.includes(leaf);
                            if (!alreadyAdded) {
                                if (isPerson) {
                                    const cleanLeaf = leaf.includes('/') ? leaf.split('/').pop().trim() : leaf;
                                    suggPeopleCounts[cleanLeaf] = (suggPeopleCounts[cleanLeaf] || 0) + 1;
                                } else {
                                    suggTagCounts[leaf] = (suggTagCounts[leaf] || 0) + 1;
                                }
                            }
                        });
                    }
                }
            });
            
            // Render Suggested People List
            selectionSuggestedPeopleList.innerHTML = '';
            const suggPeopleKeys = Object.keys(suggPeopleCounts).sort();
            if (suggPeopleKeys.length === 0) {
                selectionSuggestedPeopleList.innerHTML = '<span style="color: var(--text-muted); font-size: 12px; padding: 4px 0;">None</span>';
            } else {
                suggPeopleKeys.forEach(p => {
                    const count = suggPeopleCounts[p];
                    const chip = document.createElement('span');
                    chip.className = 'suggestion-chip';
                    chip.style.cursor = 'pointer';
                    chip.textContent = `${p} (${count})`;
                    chip.title = `Click to apply "${p}" to all selected photos`;
                    chip.addEventListener('click', (e) => {
                        e.stopPropagation();
                        applyTagToAllSelected(p, true);
                    });
                    selectionSuggestedPeopleList.appendChild(chip);
                });
            }
            
            // Render Suggested Tags List
            selectionSuggestedTagsList.innerHTML = '';
            const suggTagKeys = Object.keys(suggTagCounts).sort();
            if (suggTagKeys.length === 0) {
                selectionSuggestedTagsList.innerHTML = '<span style="color: var(--text-muted); font-size: 12px; padding: 4px 0;">None</span>';
            } else {
                suggTagKeys.forEach(t => {
                    const count = suggTagCounts[t];
                    const chip = document.createElement('span');
                    chip.className = 'suggestion-chip';
                    chip.style.cursor = 'pointer';
                    chip.textContent = `${t} (${count})`;
                    chip.title = `Click to apply "${t}" to all selected photos`;
                    chip.addEventListener('click', (e) => {
                        e.stopPropagation();
                        applyTagToAllSelected(t, false);
                    });
                    selectionSuggestedTagsList.appendChild(chip);
                });
            }
            btnApplyRename.disabled = false;
            updateFolderAutoApplyState();
        } else {
            // Empty selection: keep the panel, clear what it was showing.
            selectionDateLabel.textContent = 'Date Taken';
            selectionDateValue.textContent = '--';
            if (selectionPeopleList) selectionPeopleList.innerHTML = '';
            if (selectionTagsList) selectionTagsList.innerHTML = '';
            if (selectionSuggestedPeopleList) selectionSuggestedPeopleList.innerHTML = '';
            if (selectionSuggestedTagsList) selectionSuggestedTagsList.innerHTML = '';
            btnApplyRename.disabled = true;
            updateFolderAutoApplyState();
        }
    }

    function applyTagToAllSelected(tag, isPerson) {
        if (selectedThumbnails.length === 0) return;
        
        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Applying tag...';
        
        fetch('/api/photos/bulk-tags', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paths: selectedThumbnails, add_tags: [tag], remove_tags: [] })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                // Update tags in cache
                selectedThumbnails.forEach(path => {
                    const photo = folderPhotos.find(p => p.path === path);
                    if (photo) {
                        if (!photo.tags.includes(tag)) {
                            photo.tags.push(tag);
                        }
                        if (isPerson) {
                            if (!photo.people) photo.people = [];
                            if (!photo.people.includes(tag)) photo.people.push(tag);
                        }
                    }
                });
                
                updateSelectedThumbnailsCount();
                renderFileList();
                renderThumbnails();
                updateTagsDatalist();
                saveToLocalStorageCache();
                
                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Ready';
            } else {
                throw new Error(data.error);
            }
        })
        .catch(err => {
            console.error(err);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
            alert("Error applying tag selection: " + err.message);
        });
    }

    function removeTagFromAllSelected(tag, isPerson) {
        if (selectedThumbnails.length === 0) return;

        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Removing tag...';
        
        fetch('/api/photos/bulk-tags', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paths: selectedThumbnails, add_tags: [], remove_tags: [tag] })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                // Update tags in cache
                selectedThumbnails.forEach(path => {
                    const photo = folderPhotos.find(p => p.path === path);
                    if (photo) {
                        photo.tags = photo.tags.filter(t => t !== tag);
                        if (isPerson && photo.people) {
                            photo.people = photo.people.filter(p => p !== tag);
                        }
                    }
                });
                
                updateSelectedThumbnailsCount();
                renderFileList();
                renderThumbnails();
                updateTagsDatalist();
                saveToLocalStorageCache();
                
                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Ready';
            } else {
                throw new Error(data.error);
            }
        })
        .catch(err => {
            console.error(err);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
            alert("Error removing tag selection: " + err.message);
        });
    }

    function selectAllThumbnails() {
        selectedThumbnails = folderPhotos.map(p => p.path);
        renderThumbnails();
        updateSelectedThumbnailsCount();
    }

    function selectNoneThumbnails() {
        selectedThumbnails = [];
        renderThumbnails();
        updateSelectedThumbnailsCount();
    }



    // Select Single Photo View
    function selectPhoto(path) {
        if (path !== activePhotoPath) clearPendingEntry();
        activePhotoPath = path;

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

        renderPhotoFaces(path);

        // Fetch photo data from local array
        const photo = folderPhotos.find(p => p.path === path);
        if (!photo) return;

        // Render values
        mainImage.src = `/api/photo-file?path=${encodeURIComponent(photo.path)}&size=800`;
        detailPath.textContent = photo.path;
        let dateVal = "Unknown";
        const rawMeta = photo.raw_metadata || {};
        for (let k of DATE_KEYS) {
            if (rawMeta[k]) {
                const localD = parseExifDateToLocalDate(rawMeta[k]);
                if (localD) {
                    const stats = getFolderDateStats();
                    dateVal = formatFriendlyDateSingle(localD, stats);
                }
                break;
            }
        }
        detailDateTaken.textContent = dateVal;
        inputPhotoTitle.value = photo.title || '';
        
        renderTags(photo.tags);
        renderSuggestionsPanel(photo.path);
    }

    /**
     * The tags on whichever photo sits before this one in the list.
     *
     * "Before" is list order rather than a history of what you visited, because that
     * is what matches the way a shoot is worked: down the folder, in order. Reaching
     * backwards from the first photo yields nothing rather than wrapping around.
     */
    function previousPhotoTags() {
        const items = Array.from(photoList.querySelectorAll('.photo-item-file'));
        const index = items.findIndex(
            el => el.getAttribute('data-path') === activePhotoPath
        );
        if (index <= 0) return { tags: [], from: null };
        const prevPath = items[index - 1].getAttribute('data-path');
        const prev = folderPhotos.find(p => p.path === prevPath);
        if (!prev) return { tags: [], from: null };
        return {
            tags: (prev.tags || []).slice(),
            from: prev.filename || prevPath.split(/[/\\]/).pop(),
        };
    }

    /** Is there anything to copy forward onto the current photo? */
    function carryForwardCandidates() {
        const { tags, from } = previousPhotoTags();
        const photo = folderPhotos.find(p => p.path === activePhotoPath);
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
    function carryTagsForward() {
        const path = activePhotoPath;
        if (!path) return;
        const photo = folderPhotos.find(p => p.path === path);
        if (!photo) return;

        const { missing, from } = carryForwardCandidates();
        if (!missing.length) {
            setStatus('ready', from
                ? `Nothing to carry over from ${from}`
                : 'No previous photo to carry tags from');
            return;
        }

        const updatedTags = Array.from(new Set([...(photo.tags || []), ...missing]));
        const before = (photo.tags || []).slice();
        setStatus('busy', `Copying ${missing.length} tag(s) from ${from}...`);

        fetch('/api/photo/save-metadata', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path, title: photo.title, tags: updatedTags })
        })
        .then(res => res.json())
        .then(data => {
            if (!data.success) throw new Error(data.error || 'Failed to save');
            photo.tags = updatedTags;
            recordUndo({
                label: `carry ${missing.length} tag(s) from ${from}`,
                photos: [{ path, tags: before, title: photo.title }],
            });
            renderTags(updatedTags);
            renderFileList();
            renderThumbnails();
            saveToLocalStorageCache();
            setStatus('ready', `Copied ${missing.length} tag(s) from ${from}`);
        })
        .catch(err => {
            console.error(err);
            setStatus('error', 'Could not copy tags: ' + err.message);
        });
    }

    /** Keep the carry-forward button honest about what it would do. */
    function updateCarryForwardState() {
        if (!btnCarryForward) return;
        const { missing, from } = carryForwardCandidates();
        btnCarryForward.disabled = missing.length === 0;
        btnCarryForward.title = !from
            ? 'No previous photo in the list to copy from'
            : missing.length
                ? `Copy ${missing.length} tag(s) from ${from}  (Ctrl+D)`
                : `${from} has no tags this photo is missing`;
    }

    enableSwipeNavigation(mainImage);

    if (btnCarryForward) btnCarryForward.addEventListener('click', carryTagsForward);
    if (btnUndo) btnUndo.addEventListener('click', undoLastOperation);

    /**
     * Swipe or drag horizontally across the image to move between photos.
     *
     * Thresholds do the work: SWIPE_MIN_PX keeps a tap or a jitter from counting,
     * and requiring the horizontal distance to exceed the vertical keeps a scroll
     * that drifts sideways from flipping the photo. Right-to-left goes forwards,
     * matching every photo viewer people already use.
     */
    const SWIPE_MIN_PX = 60;
    function enableSwipeNavigation(surface) {
        if (!surface) return;
        let startX = null;
        let startY = null;
        let pointerId = null;

        surface.addEventListener('pointerdown', (e) => {
            // Primary button or touch only; ignore right-click and middle-click.
            if (e.pointerType === 'mouse' && e.button !== 0) return;
            pointerId = e.pointerId;
            startX = e.clientX;
            startY = e.clientY;
        });

        const finish = (e) => {
            if (pointerId === null || e.pointerId !== pointerId) return;
            const dx = e.clientX - startX;
            const dy = e.clientY - startY;
            pointerId = null;
            startX = null;
            startY = null;

            if (Math.abs(dx) < SWIPE_MIN_PX) return;      // a tap, or a twitch
            if (Math.abs(dx) <= Math.abs(dy)) return;      // a scroll that drifted
            stepPhoto(dx < 0 ? 1 : -1);
        };

        surface.addEventListener('pointerup', finish);
        surface.addEventListener('pointercancel', () => { pointerId = null; });
    }

    function renderTags(tags) {
        detailPeople.innerHTML = '';
        detailTags.innerHTML = '';
        
        const photo = folderPhotos.find(p => p.path === activePhotoPath);
        const photoPeople = (photo && photo.people) ? photo.people : [];
        
        const peopleTags = [];
        const nonPeopleTags = [];
        
        if (tags) {
            tags.forEach(tag => {
                const isPerson = isPersonTag(tag);
                if (isPerson) {
                    peopleTags.push(tag);
                } else {
                    nonPeopleTags.push(tag);
                }
            });
        }

        if (peopleTags.length === 0) {
            detailPeople.innerHTML = '<span style="color: var(--text-muted); font-size: 13px;">No people tags.</span>';
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
            detailTags.innerHTML = '<span style="color: var(--text-muted); font-size: 13px;">No keywords set.</span>';
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
    function saveSingleTitle() {
        const path = activePhotoPath;
        if (!path) return;
        
        const newTitle = inputPhotoTitle.value.trim();
        const photo = folderPhotos.find(p => p.path === path);
        if (!photo) return;

        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Saving...';

        fetch('/api/photo/save-metadata', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path, title: newTitle, tags: photo.tags })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                photo.title = newTitle;
                photo.captions = newTitle ? [newTitle] : [];
                
                if (data.new_path && data.new_path !== path) {
                    photo.path = data.new_path;
                    photo.filename = data.new_path.split(/[/\\]/).pop();
                    activePhotoPath = data.new_path;
                    selectPhoto(data.new_path);
                }
                
                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Ready';
                saveToLocalStorageCache();
                renderFileList();
                renderThumbnails();
            } else {
                throw new Error(data.error || 'Failed to save');
            }
        })
        .catch(err => {
            console.error(err);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
            alert("Error saving title: " + err.message);
        });
    }

    async function saveSingleAddPerson() {
        const path = activePhotoPath;
        if (!path) return;
        
        const newPersonVal = inputAddPerson.value.trim();
        if (!newPersonVal) return;

        const photo = folderPhotos.find(p => p.path === path);
        if (!photo) return;

        const inputPeople = newPersonVal.split(',').map(t => t.trim()).filter(t => t);
        const resolvedPeople = [];
        
        for (const p of inputPeople) {
            const resolved = await resolveTagOrPerson(p, true);
            if (resolved) {
                resolvedPeople.push(resolved);
            }
        }
        
        if (resolvedPeople.length === 0) return;

        const updatedTags = Array.from(new Set([...photo.tags, ...resolvedPeople]));

        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Adding person...';

        fetch('/api/photo/save-metadata', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path, title: photo.title, tags: updatedTags })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                photo.tags = updatedTags;
                
                if (!photo.people) photo.people = [];
                resolvedPeople.forEach(p => {
                    const leaf = p.includes('/') ? p.split('/').pop().trim() : p;
                    if (!photo.people.includes(leaf)) photo.people.push(leaf);
                });
                
                renderTags(updatedTags);
                inputAddPerson.value = '';
                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Ready';
                saveToLocalStorageCache();
                fetchKnownTagsAndPeople();
            } else {
                throw new Error(data.error || 'Failed to save');
            }
        })
        .catch(err => {
            console.error(err);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
            alert("Error adding person: " + err.message);
        });
    }

    async function saveSingleAddTag() {
        const path = activePhotoPath;
        if (!path) return;
        
        const newTagVal = inputAddTag.value.trim();
        if (!newTagVal) return;

        const photo = folderPhotos.find(p => p.path === path);
        if (!photo) return;

        const inputTags = newTagVal.split(',').map(t => t.trim()).filter(t => t);
        const resolvedTags = [];
        for (const t of inputTags) {
            const resolved = await resolveTagOrPerson(t, false);
            if (resolved) {
                resolvedTags.push(resolved);
            }
        }
        
        if (resolvedTags.length === 0) return;

        const updatedTags = Array.from(new Set([...photo.tags, ...resolvedTags]));

        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Adding tag...';

        fetch('/api/photo/save-metadata', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path, title: photo.title, tags: updatedTags })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                photo.tags = updatedTags;
                renderTags(updatedTags);
                inputAddTag.value = '';
                updateTagsDatalist();
                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Ready';
                saveToLocalStorageCache();
                fetchKnownTagsAndPeople();
            } else {
                throw new Error(data.error || 'Failed to save');
            }
        })
        .catch(err => {
            console.error(err);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
            alert("Error adding tag: " + err.message);
        });
    }

    function deletePhotoTag(tagToRemove) {
        const path = activePhotoPath;
        if (!path) return;
        
        const photo = folderPhotos.find(p => p.path === path);
        if (!photo) return;

        const updatedTags = photo.tags.filter(t => t !== tagToRemove);

        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Deleting tag...';

        fetch('/api/photo/save-metadata', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path, title: photo.title, tags: updatedTags })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                photo.tags = updatedTags;
                renderTags(updatedTags);
                updateTagsDatalist();
                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Ready';
                saveToLocalStorageCache();
            } else {
                throw new Error(data.error || 'Failed to delete');
            }
        })
        .catch(err => {
            console.error(err);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
            alert("Error deleting tag: " + err.message);
        });
    }

    function openPhotoInExplorer() {
        const path = activePhotoPath;
        if (!path) return;
        
        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Opening Explorer...';
        
        fetch('/api/photo/open-explorer', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Ready';
            } else {
                throw new Error(data.error);
            }
        })
        .catch(err => {
            console.error(err);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Ready';
        });
    }

    // PIL rotation trigger
    function rotatePhoto(direction) {
        const path = activePhotoPath;
        if (!path) return;

        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Rotating...';

        fetch('/api/photo/rotate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path, direction })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                // Reload image with cache buster
                mainImage.src = `/api/photo-file?path=${encodeURIComponent(path)}&size=800&t=${Date.now()}`;
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
    function deleteActivePhoto() {
        const path = activePhotoPath;
        if (!path) return;

        const index = folderPhotos.findIndex(p => p.path === path);
        if (index === -1) return;

        const filename = folderPhotos[index].filename || 'this photo';
        if (!confirm(`Are you sure you want to delete "${filename}" and move it to the Windows Recycle Bin?`)) {
            return;
        }

        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Deleting...';

        fetch('/api/photo/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                // Remove photo from client folderPhotos array
                folderPhotos.splice(index, 1);

                // Remove from selection array if selected
                const selIndex = selectedThumbnails.indexOf(path);
                if (selIndex !== -1) {
                    selectedThumbnails.splice(selIndex, 1);
                }

                // Update cache
                saveToLocalStorageCache();

                // Update UI statistics
                updateListStats();
                folderViewStats.textContent = `${folderPhotos.length} photos`;

                // Re-render components
                renderFileList();
                renderThumbnails();

                // Select the next photo or fall back to grid/folder view
                if (folderPhotos.length === 0) {
                    showFolderView();
                } else {
                    const nextPhoto = folderPhotos[index] || folderPhotos[index - 1];
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

    // Suggest Tags operations
    function startSuggestions() {
        const path = scannedFolder;
        if (!path) return;

        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Suggesting...';
        btnSuggestTags.disabled = true;
        btnFolderAutoApply.disabled = true;

        fetch('/api/folder/suggest-start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ folder_path: path })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                suggestProgressContainer.classList.remove('hidden');
                checkSuggestionsStatus(path);
            } else {
                updateSuggestButtonState();
                throw new Error(data.error);
            }
        })
        .catch(err => {
            updateSuggestButtonState();
            console.error(err);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
            alert("Error starting suggestions: " + err.message);
        });
    }

    let indexProgressTimer = null;

    function startIndexing() {
        const path = folderPathInput.value.trim();
        if (!path) {
            flagField(folderPathInput, 'Choose or type a folder to index');
            return;
        }

        btnScanFolder.disabled = true;
        btnIndexFolder.disabled = true;
        btnSuggestTags.disabled = true;

        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Indexing...';

        fetch('/api/folder/index-start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ folder_path: path })
        })
        .then(res => {
            if (!res.ok) return res.json().then(e => { throw new Error(e.error || 'Indexing start failed') });
            return res.json();
        })
        .then(data => {
            if (data.success) {
                checkIndexingStatus(path);
            }
        })
        .catch(err => {
            console.error(err);
            btnScanFolder.disabled = false;
            btnIndexFolder.disabled = false;
            btnSuggestTags.disabled = false;
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Ready';
            alert("Error starting indexing: " + err.message);
        });
    }

    function checkIndexingStatus(folderPath) {
        if (indexProgressTimer) clearInterval(indexProgressTimer);

        function queryProgress() {
            fetch(`/api/folder/index-status?path=${encodeURIComponent(folderPath)}`)
                .then(res => res.json())
                .then(data => {
                    if (data.status === 'running') {
                        indexProgressContainer.classList.remove('hidden');
                        const pct = data.percent || 0;
                        indexProgressBar.style.width = `${pct}%`;
                        indexProgressText.textContent = `${data.message || 'Indexing...'}`;
                    }
                    else if (data.status === 'completed') {
                        clearInterval(indexProgressTimer);
                        const wasVisible = !indexProgressContainer.classList.contains('hidden');
                        indexProgressContainer.classList.add('hidden');
                        
                        btnScanFolder.disabled = false;
                        btnIndexFolder.disabled = false;
                        
                        if (wasVisible) {
                            fetchKnownTagsAndPeople();
                            scanFolder(true);
                            alert(data.message || "Folder successfully added to the database and indexed!");
                        }
                        
                        statusDot.className = 'status-indicator-dot';
                        statusText.textContent = 'Ready';
                    }
                    else if (data.status === 'failed') {
                        clearInterval(indexProgressTimer);
                        const wasVisible = !indexProgressContainer.classList.contains('hidden');
                        indexProgressContainer.classList.add('hidden');
                        
                        btnScanFolder.disabled = false;
                        btnIndexFolder.disabled = false;
                        updateSuggestButtonState();
                        
                        if (wasVisible) {
                            alert("Indexing failed: " + (data.message || 'Unknown error'));
                        }
                        
                        statusDot.className = 'status-indicator-dot';
                        statusText.textContent = 'Error';
                    }
                })
                .catch(err => {
                    console.error("Error polling indexing progress:", err);
                });
        }

        queryProgress();
        indexProgressTimer = setInterval(queryProgress, 1000);
    }

    function checkSuggestionsStatus(folderPath) {
        if (progressTimer) clearInterval(progressTimer);

        function queryProgress() {
            fetch(`/api/folder/suggest-status?path=${encodeURIComponent(folderPath)}`)
                .then(res => res.json())
                .then(data => {
                    if (data.status === 'preparing' || data.status === 'running') {
                        suggestProgressContainer.classList.remove('hidden');
                        const total = data.total || 0;
                        const completed = data.completed || 0;
                        
                        if (data.status === 'preparing' || total === 0) {
                            suggestProgressBar.style.width = `0%`;
                            suggestProgressText.textContent = `Preparing AI models & scanning folder...`;
                        } else {
                            const pct = Math.round((completed / total) * 100);
                            suggestProgressBar.style.width = `${pct}%`;
                            suggestProgressText.textContent = `Processing: ${completed} / ${total} (${pct}%)`;
                        }
                        
                        // Merge progressive suggestions
                        folderSuggestions = data.suggestions || {};
                        renderFileList(); // updates tags badge dynamically
                        saveToLocalStorageCache();
                    } 
                    else if (data.status === 'completed') {
                        clearInterval(progressTimer);
                        suggestProgressContainer.classList.add('hidden');
                        
                        folderSuggestions = data.suggestions || {};
                        btnFolderAutoApply.disabled = false;
                        
                        renderFileList();
                        saveToLocalStorageCache();
                        
                        updateSuggestButtonState('completed');
                        
                        // If active photo selected, refresh suggestions pane
                        if (activePhotoPath) {
                            renderSuggestionsPanel(activePhotoPath);
                        }

                        statusDot.className = 'status-indicator-dot';
                        statusText.textContent = 'Ready';
                    }
                    else {
                        // 'idle' (never run), 'not_started', 'error', or anything unexpected:
                        // stop polling rather than spinning on a status we cannot advance.
                        clearInterval(progressTimer);
                        suggestProgressContainer.classList.add('hidden');
                        updateSuggestButtonState(data.status);
                        if (data.status === 'error') {
                            statusDot.className = 'status-indicator-dot';
                            statusText.textContent = 'Error';
                            if (data.message) {
                                console.error("Suggestions failed:", data.message);
                            }
                        }
                    }
                })
                .catch(err => {
                    console.error("Error polling suggestions progress:", err);
                });
        }

        // Query once immediately, then poll
        queryProgress();
        progressTimer = setInterval(queryProgress, 1500);
    }

    // Render suggestions box in right pane for active photo
    function renderSuggestionsPanel(photoPath) {
        const sugg = folderSuggestions[photoPath];
        if (!sugg) {
            suggestionsSection.classList.add('hidden');
            return;
        }

        suggestionsSection.classList.remove('hidden');

        // Configure Title Wand
        if (sugg.title) {
            btnSuggestTitleWand.disabled = false;
            btnSuggestTitleWand.title = `Suggested Title: "${sugg.title}"`;
            btnSuggestTitleWand.setAttribute('data-suggested-title', sugg.title);
        } else {
            btnSuggestTitleWand.disabled = true;
            btnSuggestTitleWand.title = "No AI title suggested";
            btnSuggestTitleWand.removeAttribute('data-suggested-title');
        }

        // People
        suggestedPeopleContainer.innerHTML = '';
        if (!sugg.people || sugg.people.length === 0) {
            suggestedPeopleContainer.innerHTML = '<span style="color: var(--text-muted); font-size: 12px;">No people detected.</span>';
        } else {
            sugg.people.forEach(item => {
                const chip = document.createElement('span');
                chip.className = 'suggestion-chip';
                chip.style.cursor = 'pointer';
                chip.title = 'Click to add person';
                chip.textContent = item.name;
                chip.addEventListener('click', () => applySuggestedTagDirect(item.name, true));
                suggestedPeopleContainer.appendChild(chip);
            });
        }

        // Tags
        suggestedTagsContainer.innerHTML = '';
        if (!sugg.tags || sugg.tags.length === 0) {
            suggestedTagsContainer.innerHTML = '<span style="color: var(--text-muted); font-size: 12px;">No tag recommendations.</span>';
        } else {
            sugg.tags.forEach(item => {
                const chip = document.createElement('span');
                chip.className = 'suggestion-chip';
                chip.style.cursor = 'pointer';
                chip.title = 'Click to add tag';
                chip.textContent = item.tag;
                chip.addEventListener('click', () => applySuggestedTagDirect(item.tag, false));
                suggestedTagsContainer.appendChild(chip);
            });
        }
    }

    /**
     * Add a suggested tag or person to the open photo, as the tag they are filed under.
     *
     * This used to reduce whatever it was given to its leaf and write that. For a
     * keyword it threw away the level the taxonomy had just resolved; for a person it
     * wrote the bare name that the keyword convention does not allow, beside the
     * "People/<name>" the photo was already carrying. Clicking a face TagPup had
     * matched therefore added that person a second time, in the wrong form.
     *
     * Suggestions arrive as leaf names, so the name is resolved to its taxonomy path
     * first. The modal only appears where the name is genuinely ambiguous, which for a
     * recognised face means never: they are in the taxonomy already.
     */
    async function applySuggestedTagDirect(tagName, isPerson) {
        const path = activePhotoPath;
        if (!path) return;
        const photo = folderPhotos.find(p => p.path === path);
        if (!photo) return;

        const resolved = await resolveTagOrPerson(tagName, isPerson);
        if (!resolved) return;

        if (photoAlreadyHas(photo, resolved)) return;
        const updatedTags = [...photo.tags, resolved];
        const leaf = leafOf(resolved);

        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Saving...';

        fetch('/api/photo/save-metadata', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path, title: photo.title, tags: updatedTags })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                photo.tags = updatedTags;
                if (isPerson) {
                    if (!photo.people) photo.people = [];
                    if (!photo.people.includes(leaf)) photo.people.push(leaf);
                }
                renderTags(updatedTags);
                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Ready';
                saveToLocalStorageCache();
            } else {
                throw new Error(data.error);
            }
        })
        .catch(err => {
            console.error(err);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
            alert("Error adding suggested tag: " + err.message);
        });
    }

    function applySuggestedTitle() {
        const path = activePhotoPath;
        if (!path) return;
        const photo = folderPhotos.find(p => p.path === path);
        const sugg = folderSuggestions[path];
        if (!photo || !sugg || !sugg.title) return;

        inputPhotoTitle.value = sugg.title;
        saveSingleTitle();
    }

    async function applyAllSingleSuggestions() {
        const path = activePhotoPath;
        if (!path) return;
        const photo = folderPhotos.find(p => p.path === path);
        const sugg = folderSuggestions[path];
        if (!photo || !sugg) return;

        // Resolve each suggestion to the tag it is filed under before writing it, and
        // skip anyone the photo already names. Applying the list raw wrote bare leaves.
        const wanted = [
            ...(sugg.tags || []).map(t => ({ name: t.tag, isPerson: false })),
            ...(sugg.people || []).map(p => ({ name: p.name, isPerson: true })),
        ];
        const resolvedSuggestions = [];
        for (const item of wanted) {
            const resolved = await resolveTagOrPerson(item.name, item.isPerson);
            if (!resolved) continue;
            if (photoAlreadyHas(photo, resolved)) continue;
            if (resolvedSuggestions.includes(resolved)) continue;
            resolvedSuggestions.push(resolved);
        }
        if (resolvedSuggestions.length === 0) return;

        const updatedTags = Array.from(new Set([...photo.tags, ...resolvedSuggestions]));
        const updatedTitle = photo.title; // Do not apply suggested title automatically

        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Saving...';

        fetch('/api/photo/save-metadata', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path, title: updatedTitle, tags: updatedTags })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                photo.tags = updatedTags;
                if (!photo.people) photo.people = [];
                resolvedSuggestions.filter(isPersonTag).forEach(t => {
                    const leaf = leafOf(t);
                    if (!photo.people.includes(leaf)) photo.people.push(leaf);
                });
                renderTags(updatedTags);
                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Ready';
                saveToLocalStorageCache();
            } else {
                throw new Error(data.error);
            }
        })
        .catch(err => {
            console.error(err);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
            alert("Error applying all suggestions: " + err.message);
        });
    }

    // Bulk Editing operations
    async function bulkAddPeopleToSelection() {
        if (selectedThumbnails.length === 0) return;
        const val = bulkAddPeopleInput.value.trim();
        if (!val) return;
        
        const peopleList = val.split(',').map(p => p.trim()).filter(p => p);
        if (peopleList.length === 0) return;

        const resolvedPeople = [];
        for (const p of peopleList) {
            const resolved = await resolveTagOrPerson(p, true);
            if (resolved) {
                resolvedPeople.push(resolved);
            }
        }
        if (resolvedPeople.length === 0) return;

        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Adding people...';

        fetch('/api/photos/bulk-tags', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paths: selectedThumbnails, add_tags: resolvedPeople, remove_tags: [] })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                selectedThumbnails.forEach(path => {
                    const photo = folderPhotos.find(p => p.path === path);
                    if (photo) {
                        if (!photo.people) photo.people = [];
                        resolvedPeople.forEach(p => {
                            if (!photo.tags.includes(p)) photo.tags.push(p);
                            const leaf = p.includes('/') ? p.split('/').pop().trim() : p;
                            if (!photo.people.includes(leaf)) photo.people.push(leaf);
                        });
                    }
                });

                bulkAddPeopleInput.value = '';
                updateSelectedThumbnailsCount();
                renderFileList();
                renderThumbnails();
                updatePeopleDatalist();
                saveToLocalStorageCache();

                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Ready';
            } else {
                throw new Error(data.error);
            }
        })
        .catch(err => {
            console.error(err);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
            alert("Error bulk adding people: " + err.message);
        });
    }

    async function bulkAddTagsToSelection() {
        if (selectedThumbnails.length === 0) return;
        const val = bulkAddTagsInput.value.trim();
        if (!val) return;
        
        const tagsList = val.split(',').map(t => t.trim()).filter(t => t);
        if (tagsList.length === 0) return;

        const resolvedTags = [];
        for (const t of tagsList) {
            const resolved = await resolveTagOrPerson(t, false);
            if (resolved) {
                resolvedTags.push(resolved);
            }
        }
        if (resolvedTags.length === 0) return;

        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Adding tags...';

        fetch('/api/photos/bulk-tags', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paths: selectedThumbnails, add_tags: resolvedTags, remove_tags: [] })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                selectedThumbnails.forEach(path => {
                    const photo = folderPhotos.find(p => p.path === path);
                    if (photo) {
                        resolvedTags.forEach(t => {
                            if (!photo.tags.includes(t)) photo.tags.push(t);
                        });
                    }
                });

                bulkAddTagsInput.value = '';
                updateSelectedThumbnailsCount();
                renderFileList();
                renderThumbnails();
                updateTagsDatalist();
                saveToLocalStorageCache();

                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Ready';
            } else {
                throw new Error(data.error);
            }
        })
        .catch(err => {
            console.error(err);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
            alert("Error bulk adding tags: " + err.message);
        });
    }

    function applyFolderSuggestionsLevel() {
        const folder = scannedFolder;
        if (!folder || selectedThumbnails.length === 0) return;

        // Say what will be written, and to how many photos that already carry work,
        // before writing it. The common mistake is not misreading the button -- it is
        // having the wrong selection, and a count of photos alone does not surface
        // that. This is the last point at which it costs nothing.
        const alreadyTagged = selectedThumbnails.filter(p => {
            const photo = folderPhotos.find(x => x.path === p);
            return photo && isPhotoTagged(photo);
        }).length;
        const scope = [
            `Auto-apply AI suggestions to ${selectedThumbnails.length} selected photo(s)?`,
            '',
            alreadyTagged
                ? `${alreadyTagged} of them already have tags. Suggestions are added to what is there; nothing is removed.`
                : 'None of them are tagged yet.',
            '',
            'This writes keywords into the photo files. Undo restores the previous tags for this session only.',
        ].join('\n');
        if (!confirm(scope)) return;

        const before = snapshotPhotos(selectedThumbnails);
        setStatus('busy', `Applying suggestions to ${selectedThumbnails.length} photo(s)...`);

        fetch('/api/folder/auto-apply', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                folder_path: folder, 
                photo_paths: selectedThumbnails,
                threshold: 0.75 
            })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                recordUndo({
                    label: `auto-apply to ${before.length} photo(s)`,
                    photos: before,
                });
                setStatus('ready',
                    `Suggestions applied to ${before.length} photo(s) \u2014 Ctrl+Z to undo`);
                scanFolder(true); // Rescan folder to load updated tags
            } else {
                throw new Error(data.error);
            }
        })
        .catch(err => {
            console.error(err);
            // A failure that would otherwise pass unnoticed still earns a modal.
            setStatus('error', 'Applying suggestions failed', { transient: false });
            alert("Error applying suggestions: " + err.message);
        });
    }

    function updateFolderAutoApplyState() {
        if (!scannedFolder || selectedThumbnails.length === 0) {
            btnFolderAutoApply.disabled = true;
            return;
        }
        
        let hasSomethingToApply = false;
        
        for (let path of selectedThumbnails) {
            const sugg = folderSuggestions[path];
            if (!sugg) continue;
            
            const photo = folderPhotos.find(p => p.path === path);
            if (!photo) continue;
            
            const currentTags = photo.tags || [];
            const photoPeople = photo.people || [];

            // Check general tags suggestions
            if (sugg.tags) {
                for (let t of sugg.tags) {
                    if (t.score >= 0.75) {
                        if (!currentTags.includes(t.tag)) {
                            hasSomethingToApply = true;
                            break;
                        }
                    }
                }
            }
            if (hasSomethingToApply) break;

            // Check people suggestions. A person the photo already names under their
            // path is nothing to apply, so compare by leaf rather than by spelling --
            // otherwise the button offers to add someone who is already there.
            if (sugg.people) {
                for (let p of sugg.people) {
                    if (p.score >= 0.75) {
                        const leaf = leafOf(p.name);
                        if (!photoAlreadyHas(photo, p.name)
                            && !photoPeople.some(n => leafOf(n).toLowerCase() === leaf.toLowerCase())) {
                            hasSomethingToApply = true;
                            break;
                        }
                    }
                }
            }
            if (hasSomethingToApply) break;
        }
        
        btnFolderAutoApply.disabled = !hasSomethingToApply;
    }

    function populateCameraModelsDropdown() {
        if (!folderPhotos || folderPhotos.length === 0) {
            timeshiftPanel.classList.add('hidden');
            btnToggleTimeshift.disabled = true;
            return;
        }

        btnToggleTimeshift.disabled = false;
        timeshiftCameraSelect.innerHTML = '';

        // Group photos by camera model
        const modelCounts = {};
        folderPhotos.forEach(photo => {
            const model = cameraModelOf(photo);
            modelCounts[model] = (modelCounts[model] || 0) + 1;
        });

        // Add "All Cameras" option
        const optAll = document.createElement('option');
        optAll.value = "All Cameras";
        optAll.textContent = `All Cameras (${folderPhotos.length} photos)`;
        timeshiftCameraSelect.appendChild(optAll);

        // Sort camera models alphabetically
        const sortedModels = Object.keys(modelCounts).sort();
        sortedModels.forEach(model => {
            const opt = document.createElement('option');
            opt.value = model;
            opt.textContent = `${model} (${modelCounts[model]} photos)`;
            timeshiftCameraSelect.appendChild(opt);
        });
        
        timeshiftMinutesInput.value = 0; // reset
        updateCameraHighlights();
    }

    /** The camera a photo came from, as the time-shift dropdown labels it. */
    function cameraModelOf(photo) {
        const raw = (photo && photo.raw_metadata) || {};
        return raw["EXIF:Model"] || raw["Model"]
            || raw["EXIF:Make"] || raw["Make"]
            || "Unknown Camera";
    }

    function applyTimeShift() {
        const folder = scannedFolder;
        if (!folder) return;
        
        const cameraModel = timeshiftCameraSelect.value;
        const minutes = parseInt(timeshiftMinutesInput.value, 10);
        
        if (isNaN(minutes) || minutes === 0) {
            // Beside the field, where it can be read and fixed in one motion.
            flagField(timeshiftMinutesInput, 'Enter a shift in minutes (not zero)');
            return;
        }

        // How many photos this is actually about. "All photos for camera X" does not
        // say whether that is four or four hundred, and the two deserve different
        // amounts of hesitation.
        const affected = cameraModel === 'All Cameras'
            ? folderPhotos.length
            : folderPhotos.filter(p => cameraModelOf(p) === cameraModel).length;
        const direction = minutes > 0 ? 'later' : 'earlier';
        const promptMsg = [
            `Shift Date Taken by ${Math.abs(minutes)} minute(s) ${direction}?`,
            '',
            `Camera: ${cameraModel}`,
            `Photos affected: ${affected}`,
            '',
            'This rewrites the timestamp inside each photo file and CANNOT be undone from here.',
        ].join('\n');
        if (!confirm(promptMsg)) return;

        setStatus('busy', `Shifting ${affected} photo(s) by ${minutes} minute(s)...`);
        
        fetch('/api/folder/time-shift', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                folder_path: folder,
                camera_model: cameraModel,
                shift_minutes: minutes
            })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                if (data.updated_photos) {
                    folderPhotos = data.updated_photos;
                }
                
                setStatus('ready', `Time shift applied to ${affected} photo(s)`);

                renderFileList();
                renderThumbnails();
                updateTagsDatalist();
                updatePeopleDatalist();
                populateCameraModelsDropdown();
                saveToLocalStorageCache();
            } else {
                throw new Error(data.error || "Time shift failed");
            }
        })
        .catch(err => {
            console.error(err);
            setStatus('error', 'Time shift failed', { transient: false });
            alert("Error shifting time: " + err.message);
        });
        timeshiftMinutesInput.value = 0; // reset
        updateCameraHighlights();
    }

    function parseExifDateToLocalDate(rawStr) {
        const regex = /^(\d{4})[: -](\d{2})[: -](\d{2})\s+(\d{2}):(\d{2}):(\d{2})/;
        const match = String(rawStr).trim().match(regex);
        if (!match) return null;
        
        return new Date(
            parseInt(match[1], 10),
            parseInt(match[2], 10) - 1,
            parseInt(match[3], 10),
            parseInt(match[4], 10),
            parseInt(match[5], 10),
            parseInt(match[6], 10)
        );
    }

    function getFolderDateStats() {
        const dates = [];
        folderPhotos.forEach(photo => {
            const raw = photo.raw_metadata || {};
            for (let k of DATE_KEYS) {
                if (raw[k]) {
                    const localD = parseExifDateToLocalDate(raw[k]);
                    if (localD) {
                        dates.push(localD);
                        break;
                    }
                }
            }
        });

        if (dates.length === 0) {
            return {
                allWithin7Days: false,
                sameYearAcrossFolder: false
            };
        }

        let minT = dates[0].getTime();
        let maxT = dates[0].getTime();
        const years = new Set();
        dates.forEach(d => {
            const t = d.getTime();
            if (t < minT) minT = t;
            if (t > maxT) maxT = t;
            years.add(d.getFullYear());
        });

        const spanDays = (maxT - minT) / (1000 * 60 * 60 * 24);
        return {
            allWithin7Days: spanDays <= 7.0,
            sameYearAcrossFolder: years.size <= 1
        };
    }

    function getFriendlyDatePart(d, stats) {
        const pad = (n) => String(n).padStart(2, '0');
        if (stats.allWithin7Days) {
            const weekdays = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
            return weekdays[d.getDay()];
        } else if (stats.sameYearAcrossFolder) {
            const month = pad(d.getMonth() + 1);
            const day = pad(d.getDate());
            return `${month}/${day}`;
        } else {
            const month = pad(d.getMonth() + 1);
            const day = pad(d.getDate());
            const year = String(d.getFullYear()).slice(-2);
            return `${month}/${day}/${year}`;
        }
    }

    function format12HourTime(d) {
        const hours24 = d.getHours();
        const mins = String(d.getMinutes()).padStart(2, '0');
        const secs = String(d.getSeconds()).padStart(2, '0');
        const ampm = hours24 >= 12 ? 'PM' : 'AM';
        const hours12 = hours24 % 12 || 12;
        return `${hours12}:${mins}:${secs} ${ampm}`;
    }

    function formatFriendlyDateSingle(d, stats) {
        const datePart = getFriendlyDatePart(d, stats);
        const timePart = format12HourTime(d);
        return `${datePart} ${timePart}`;
    }

    function formatFriendlyDateRange(minDate, maxDate) {
        const stats = getFolderDateStats();
        const d1Str = formatFriendlyDateSingle(minDate, stats);
        const d2Str = formatFriendlyDateSingle(maxDate, stats);
        
        if (minDate.getTime() === maxDate.getTime()) {
            return d1Str;
        }
        
        const sameDay = minDate.getFullYear() === maxDate.getFullYear() &&
                        minDate.getMonth() === maxDate.getMonth() &&
                        minDate.getDate() === maxDate.getDate();
                        
        if (sameDay) {
            const datePart = getFriendlyDatePart(minDate, stats);
            return `${datePart} ${format12HourTime(minDate)} - ${format12HourTime(maxDate)}`;
        } else {
            return `${d1Str} - ${d2Str}`;
        }
    }

    function updateCameraHighlights() {
        if (timeshiftPanel.classList.contains('hidden')) {
            thumbnailsGrid.querySelectorAll('.thumbnail-card').forEach(card => {
                card.classList.remove('timeshift-highlighted');
            });
            return;
        }

        const cameraModel = timeshiftCameraSelect.value;
        
        thumbnailsGrid.querySelectorAll('.thumbnail-card').forEach(card => {
            const path = card.getAttribute('data-path');
            const photo = folderPhotos.find(p => p.path === path);
            if (photo) {
                const raw = photo.raw_metadata || {};
                const model = raw["EXIF:Model"] || raw["Model"] || raw["EXIF:Make"] || raw["Make"] || "Unknown Camera";
                
                if (cameraModel === "All Cameras" || model === cameraModel) {
                    card.classList.add('timeshift-highlighted');
                } else {
                    card.classList.remove('timeshift-highlighted');
                }
            } else {
                card.classList.remove('timeshift-highlighted');
            }
        });
    }

    // Date Taken Editor Dialog Controls
    const btnEditDateTaken = document.getElementById('btn-edit-date-taken');
    const dateTakenModal = document.getElementById('date-taken-modal');
    const btnCloseDateModal = document.getElementById('btn-close-date-modal');
    const btnCancelDateModal = document.getElementById('btn-cancel-date-modal');
    const btnSaveDateModal = document.getElementById('btn-save-date-modal');
    const inputDateTaken = document.getElementById('input-date-taken');

    function exifDateToIso(exifStr) {
        if (!exifStr) return "";
        // Match standard formats like YYYY:MM:DD HH:MM:SS or similar
        const regex = /^(\d{4})[: -](\d{2})[: -](\d{2})\s+(\d{2}):(\d{2}):(\d{2})/;
        const match = String(exifStr).trim().match(regex);
        if (!match) return "";
        
        const year = match[1];
        const month = match[2];
        const day = match[3];
        const hour = match[4];
        const min = match[5];
        const sec = match[6];
        return `${year}-${month}-${day}T${hour}:${min}:${sec}`;
    }

    function getCurrentDateTimeIso() {
        const now = new Date();
        const pad = (n) => String(n).padStart(2, '0');
        const y = now.getFullYear();
        const m = pad(now.getMonth() + 1);
        const d = pad(now.getDate());
        const h = pad(now.getHours());
        const min = pad(now.getMinutes());
        const s = pad(now.getSeconds());
        return `${y}-${m}-${d}T${h}:${min}:${s}`;
    }

    if (btnEditDateTaken && dateTakenModal) {
        btnEditDateTaken.addEventListener('click', () => {
            const photo = folderPhotos.find(p => p.path === activePhotoPath);
            if (!photo) return;
            
            let rawDate = null;
            const rawMeta = photo.raw_metadata || {};
            for (let k of DATE_KEYS) {
                if (rawMeta[k]) {
                    rawDate = rawMeta[k];
                    break;
                }
            }
            
            const isoDate = exifDateToIso(rawDate) || getCurrentDateTimeIso();
            inputDateTaken.value = isoDate;
            dateTakenModal.classList.add('active');
        });

        const closeDateModal = () => {
            dateTakenModal.classList.remove('active');
        };
        
        btnCloseDateModal.addEventListener('click', closeDateModal);
        btnCancelDateModal.addEventListener('click', closeDateModal);

        btnSaveDateModal.addEventListener('click', () => {
            const path = activePhotoPath;
            if (!path) return;
            
            const photo = folderPhotos.find(p => p.path === path);
            if (!photo) return;
            
            const newDateVal = inputDateTaken.value; // format: "YYYY-MM-DDTHH:MM:SS.sss"
            if (!newDateVal) {
                alert("Please enter a valid date and time.");
                return;
            }
            
            statusDot.className = 'status-indicator-dot busy';
            statusText.textContent = 'Saving date taken...';
            closeDateModal();
            
            fetch('/api/photo/save-metadata', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    path: path,
                    title: photo.title,
                    tags: photo.tags,
                    date_taken: newDateVal
                })
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    const formattedDate = newDateVal.replace("T", " ").replace(/-/g, ":");
                    
                    // Update in-memory record
                    photo.raw_metadata = photo.raw_metadata || {};
                    photo.raw_metadata["EXIF:DateTimeOriginal"] = formattedDate;
                    photo.raw_metadata["XMP:DateTimeOriginal"] = formattedDate;
                    photo.raw_metadata["EXIF:CreateDate"] = formattedDate;
                    
                    // Format and display in UI
                    const localD = parseExifDateToLocalDate(formattedDate);
                    if (localD) {
                        const stats = getFolderDateStats();
                        detailDateTaken.textContent = formatFriendlyDateSingle(localD, stats);
                    } else {
                        detailDateTaken.textContent = newDateVal;
                    }
                    
                    statusDot.className = 'status-indicator-dot';
                    statusText.textContent = 'Ready';
                    saveToLocalStorageCache();
                    renderFileList();
                    renderThumbnails();
                } else {
                    throw new Error(data.error || 'Failed to save');
                }
            })
            .catch(err => {
                console.error(err);
                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Error';
                alert("Error saving date taken: " + err.message);
            });
        });
    }

    // Taxonomy Tree Manager Controls & Resolution Dialogs
    const btnManageTaxonomy = document.getElementById('btn-manage-taxonomy');
    const taxonomyModal = document.getElementById('taxonomy-modal');
    const btnCloseTaxonomy = document.getElementById('btn-close-taxonomy');
    const btnCloseTaxonomyFooter = document.getElementById('btn-close-taxonomy-footer');
    const btnTaxonomyAddRoot = document.getElementById('btn-taxonomy-add-root');
    const taxonomySearchInput = document.getElementById('taxonomy-search-input');
    
    if (btnManageTaxonomy && taxonomyModal) {
        btnManageTaxonomy.addEventListener('click', () => {
            taxonomyModal.classList.add('active');
            renderTaxonomyTree();
        });
        
        const closeTaxonomy = () => {
            taxonomyModal.classList.remove('active');
        };
        
        btnCloseTaxonomy.addEventListener('click', closeTaxonomy);
        btnCloseTaxonomyFooter.addEventListener('click', closeTaxonomy);
        
        btnTaxonomyAddRoot.addEventListener('click', () => {
            const name = prompt("Enter name of new root category:");
            if (name && name.trim()) {
                const hasFace = confirm(`Enable Face Matching for "${name}" (e.g. for people or pets)?`);
                createTaxonomyNode(name.trim(), null, hasFace ? 1 : 0);
            }
        });
        
        taxonomySearchInput.addEventListener('input', () => {
            renderTaxonomyTree();
        });
    }

    function loadTaxonomy() {
        return fetch('/api/taxonomy/tree')
            .then(res => res.json())
            .then(data => {
                // An error reply is an object, not the list of nodes. Storing it
                // raw made the next tag you typed throw a TypeError out of
                // resolveTagOrPerson, so the add just did nothing. Callers already
                // guard with Array.isArray in places, which is the same bug noticed
                // once and patched at the wrong end.
                taxonomyNodes = Array.isArray(data) ? data : [];
            })
            .catch(err => {
                console.error('Could not load the taxonomy:', err);
                taxonomyNodes = [];
            });
    }

    function renderTaxonomyTree() {
        const container = document.getElementById('taxonomy-tree-container');
        if (!container) return;
        const searchVal = taxonomySearchInput.value.toLowerCase().trim();
        container.innerHTML = '';
        
        const nodesById = {};
        taxonomyNodes.forEach(node => {
            nodesById[node.id] = { ...node, children: [] };
        });
        
        const roots = [];
        Object.values(nodesById).forEach(node => {
            if (node.parent_id === null) {
                roots.push(node);
            } else {
                const parent = nodesById[node.parent_id];
                if (parent) {
                    parent.children.push(node);
                } else {
                    roots.push(node);
                }
            }
        });
        
        function matchesSearch(node) {
            if (!searchVal) return true;
            if (node.tag.toLowerCase().includes(searchVal)) return true;
            return node.children.some(child => matchesSearch(child));
        }
        
        const filteredRoots = roots.filter(matchesSearch);
        
        if (filteredRoots.length === 0) {
            container.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 20px;">No tags match your search or taxonomy is empty.</div>';
            return;
        }
        
        const ul = document.createElement('ul');
        ul.className = 'taxonomy-tree-list';
        
        filteredRoots.forEach(root => {
            ul.appendChild(createNodeElement(root, nodesById));
        });
        
        container.appendChild(ul);
    }

    function createNodeElement(node, nodesById) {
        const li = document.createElement('li');
        li.className = 'taxonomy-node';
        li.setAttribute('data-id', node.id);
        
        const content = document.createElement('div');
        content.className = 'taxonomy-node-content';
        
        const expander = document.createElement('span');
        expander.className = 'taxonomy-node-expander';
        if (node.children && node.children.length > 0) {
            expander.textContent = '▶';
            expander.onclick = (e) => {
                e.stopPropagation();
                const sublist = li.querySelector('.taxonomy-sublist');
                if (sublist) {
                    if (sublist.classList.contains('hidden')) {
                        sublist.classList.remove('hidden');
                        expander.textContent = '▼';
                    } else {
                        sublist.classList.add('hidden');
                        expander.textContent = '▶';
                    }
                }
            };
        } else {
            expander.textContent = '•';
            expander.style.cursor = 'default';
        }
        content.appendChild(expander);
        
        const nameSpan = document.createElement('span');
        nameSpan.className = 'taxonomy-node-name';
        nameSpan.textContent = node.name;
        content.appendChild(nameSpan);
        
        const metaSpan = document.createElement('span');
        metaSpan.className = 'taxonomy-node-meta';
        metaSpan.textContent = `${node.usage_count} ${node.usage_count === 1 ? 'img' : 'imgs'}`;
        content.appendChild(metaSpan);
        
        const actions = document.createElement('div');
        actions.className = 'taxonomy-node-actions';
        
        if (node.parent_id === null) {
            const faceMatchLabel = document.createElement('label');
            faceMatchLabel.style.display = 'inline-flex';
            faceMatchLabel.style.alignItems = 'center';
            faceMatchLabel.style.gap = '6px';
            faceMatchLabel.style.fontSize = '12px';
            faceMatchLabel.style.marginRight = '12px';
            faceMatchLabel.title = "Enable face matching for this category (People/Pets)";
            
            const labelText = document.createElement('span');
            labelText.textContent = 'Face Matching:';
            faceMatchLabel.appendChild(labelText);
            
            const switchLabel = document.createElement('label');
            switchLabel.className = 'switch';
            
            const faceCheckbox = document.createElement('input');
            faceCheckbox.type = 'checkbox';
            faceCheckbox.checked = node.has_face === 1;
            faceCheckbox.onchange = (e) => {
                updateTaxonomyNode(node.id, { has_face: e.target.checked ? 1 : 0 });
            };
            switchLabel.appendChild(faceCheckbox);
            
            const sliderSpan = document.createElement('span');
            sliderSpan.className = 'slider';
            switchLabel.appendChild(sliderSpan);
            
            faceMatchLabel.appendChild(switchLabel);
            actions.appendChild(faceMatchLabel);
        } else {
            if (node.has_face === 1) {
                const badge = document.createElement('span');
                badge.className = 'taxonomy-node-badge badge-people';
                badge.textContent = 'Face Match';
                actions.appendChild(badge);
            }
        }
        
        const hideLabel = document.createElement('label');
        hideLabel.style.display = 'inline-flex';
        hideLabel.style.alignItems = 'center';
        hideLabel.style.gap = '4px';
        hideLabel.style.fontSize = '12px';
        hideLabel.style.marginRight = '8px';
        hideLabel.title = "Hide this tag and its sub-tags from autocomplete popups for new images";
        
        const hideCheckbox = document.createElement('input');
        hideCheckbox.type = 'checkbox';
        hideCheckbox.checked = node.hidden_from_autocomplete === 1;
        hideCheckbox.onchange = (e) => {
            updateTaxonomyNode(node.id, { hidden_from_autocomplete: e.target.checked ? 1 : 0 });
        };
        hideLabel.appendChild(hideCheckbox);
        
        const hideText = document.createElement('span');
        hideText.textContent = 'Hide';
        hideLabel.appendChild(hideText);
        actions.appendChild(hideLabel);
        
        const btnAdd = document.createElement('button');
        btnAdd.className = 'btn btn-secondary btn-sm';
        btnAdd.style.padding = '2px 6px';
        btnAdd.style.fontSize = '11px';
        btnAdd.textContent = '➕ Add';
        btnAdd.onclick = (e) => {
            e.stopPropagation();
            const childName = prompt(`Enter name of new subtag under "${node.tag}":`);
            if (childName && childName.trim()) {
                createTaxonomyNode(childName.trim(), node.id);
            }
        };
        actions.appendChild(btnAdd);

        const btnRename = document.createElement('button');
        btnRename.className = 'btn btn-secondary btn-sm';
        btnRename.style.padding = '2px 6px';
        btnRename.style.fontSize = '11px';
        btnRename.textContent = '✏️ Rename';
        btnRename.onclick = (e) => {
            e.stopPropagation();
            const newName = prompt(`Enter new name for tag "${node.name}":`, node.name);
            if (newName && newName.trim() && newName.trim() !== node.name) {
                renameTaxonomyNode(node.id, newName.trim());
            }
        };
        actions.appendChild(btnRename);
        
        const btnDel = document.createElement('button');
        btnDel.className = 'btn btn-secondary btn-sm';
        btnDel.style.padding = '2px 6px';
        btnDel.style.fontSize = '11px';
        btnDel.style.backgroundColor = 'rgba(239, 68, 68, 0.1)';
        btnDel.style.color = '#f87171';
        btnDel.style.borderColor = 'rgba(239, 68, 68, 0.2)';
        btnDel.textContent = '🗑️';
        btnDel.onclick = (e) => {
            e.stopPropagation();
            deleteTaxonomyNode(node.id, node.tag);
        };
        actions.appendChild(btnDel);
        
        content.appendChild(actions);
        li.appendChild(content);
        
        if (node.children && node.children.length > 0) {
            const sublist = document.createElement('ul');
            sublist.className = 'taxonomy-sublist hidden';
            
            node.children.forEach(child => {
                sublist.appendChild(createNodeElement(child, nodesById));
            });
            
            li.appendChild(sublist);
        }
        
        return li;
    }

    function updateTaxonomyNode(id, fields) {
        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Updating taxonomy...';
        
        fetch('/api/taxonomy/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id, ...fields })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                loadTaxonomy().then(() => {
                    renderTaxonomyTree();
                    fetchKnownTagsAndPeople();
                    statusDot.className = 'status-indicator-dot';
                    statusText.textContent = 'Ready';
                });
            } else {
                alert("Error updating tag: " + data.error);
            }
        })
        .catch(err => {
            console.error(err);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
        });
    }

    function createTaxonomyNode(name, parentId = null, hasFace = 0) {
        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Creating tag...';
        
        fetch('/api/taxonomy/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, parent_id: parentId, has_face: hasFace })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                loadTaxonomy().then(() => {
                    renderTaxonomyTree();
                    fetchKnownTagsAndPeople();
                    statusDot.className = 'status-indicator-dot';
                    statusText.textContent = 'Ready';
                });
            } else {
                alert("Error creating tag: " + data.error);
            }
        })
        .catch(err => {
            console.error(err);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
        });
    }

    function deleteTaxonomyNode(id, tagPath) {
        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Checking usage...';
        
        fetch('/api/taxonomy/delete-check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tag_id: id })
        })
        .then(res => res.json())
        .then(async (data) => {
            if (!data.success) {
                alert("Error checking tag usage: " + data.error);
                return;
            }
            
            let confirmResult = { action: 'remove' };
            
            if (data.used) {
                const possibleTargets = taxonomyNodes
                    .filter(n => n.id !== id && !n.tag.startsWith(tagPath + "/"))
                    .map(n => n.tag);
                    
                confirmResult = await showDeleteConflictModal(tagPath, data.count, possibleTargets);
                if (!confirmResult) {
                    statusDot.className = 'status-indicator-dot';
                    statusText.textContent = 'Ready';
                    return;
                }
            } else {
                const confirmed = confirm(`Are you sure you want to remove tag "${tagPath}"?`);
                if (!confirmed) {
                    statusDot.className = 'status-indicator-dot';
                    statusText.textContent = 'Ready';
                    return;
                }
            }
            
            statusDot.className = 'status-indicator-dot busy';
            statusText.textContent = 'Deleting tag...';
            
            fetch('/api/taxonomy/delete-confirm', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    tag_id: id,
                    action: confirmResult.action,
                    target_tag: confirmResult.target_tag
                })
            })
            .then(res => res.json())
            .then(resData => {
                if (resData.success) {
                    loadTaxonomy().then(() => {
                        renderTaxonomyTree();
                        fetchKnownTagsAndPeople();
                        if (data.used && scannedFolder) {
                            scanFolder(true);
                        }
                        statusDot.className = 'status-indicator-dot';
                        statusText.textContent = 'Ready';
                    });
                } else {
                    alert("Error deleting tag: " + resData.error);
                }
            });
        })
        .catch(err => {
            console.error(err);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
        });
    }

    function renameTaxonomyNode(tagId, newName) {
        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Renaming tag...';
        
        fetch('/api/taxonomy/rename', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tag_id: tagId, new_name: newName })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                loadTaxonomy().then(() => {
                    renderTaxonomyTree();
                    fetchKnownTagsAndPeople();
                    if (scannedFolder) {
                        scanFolder(true);
                    }
                    statusDot.className = 'status-indicator-dot';
                    statusText.textContent = 'Ready';
                });
            } else {
                alert("Error renaming tag: " + data.error);
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

    function showPlacementModal(title, message, options, allowNewRoot = false) {
        return new Promise((resolve) => {
            const overlay = document.createElement('div');
            overlay.className = 'modal-overlay active';
            
            let optionsHtml = options.map((opt, idx) => `
                <label class="placement-option-label">
                    <input type="radio" name="placement-opt" value="${opt}" ${idx === 0 ? 'checked' : ''}>
                    <span>${opt}</span>
                </label>
            `).join('');
            
            if (allowNewRoot) {
                optionsHtml += `
                    <label class="placement-option-label">
                        <input type="radio" name="placement-opt" value="__new_root__">
                        <span>Create a new root category...</span>
                    </label>
                    <div id="new-root-input-container" style="display: none; padding-left: 24px; margin-top: 8px; flex-direction: column; gap: 8px;">
                        <input type="text" id="new-root-name-input" placeholder="New root category name..." class="taxonomy-search-input">
                        <label style="display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text-secondary);">
                            <input type="checkbox" id="new-root-has-face-checkbox">
                            <span>Enable face matching (People/Pets)</span>
                        </label>
                    </div>
                `;
            }
            
            overlay.innerHTML = `
                <div class="modal-container" style="max-width: 450px;">
                    <div class="modal-header">
                        <h2>${title}</h2>
                        <button class="modal-close-btn">&times;</button>
                    </div>
                    <div class="modal-body">
                        <p style="margin-bottom: 16px; color: var(--text-secondary); line-height: 1.5; font-size: 14px;">${message}</p>
                        <div class="placement-options">
                            ${optionsHtml}
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button class="btn btn-secondary btn-cancel">Cancel</button>
                        <button class="btn btn-primary btn-confirm">Confirm</button>
                    </div>
                </div>
            `;
            
            document.body.appendChild(overlay);
            
            const radioNewRoot = overlay.querySelector('input[value="__new_root__"]');
            const newRootContainer = overlay.querySelector('#new-root-input-container');
            
            overlay.querySelectorAll('input[name="placement-opt"]').forEach(rad => {
                rad.addEventListener('change', (e) => {
                    if (newRootContainer) {
                        newRootContainer.style.display = e.target.value === '__new_root__' ? 'flex' : 'none';
                    }
                });
            });
            
            const close = (value) => {
                overlay.className = 'modal-overlay';
                setTimeout(() => overlay.remove(), 300);
                resolve(value);
            };
            
            overlay.querySelector('.modal-close-btn').onclick = () => close(null);
            overlay.querySelector('.btn-cancel').onclick = () => close(null);
            
            overlay.querySelector('.btn-confirm').onclick = () => {
                const selected = overlay.querySelector('input[name="placement-opt"]:checked').value;
                if (selected === '__new_root__') {
                    const name = overlay.querySelector('#new-root-name-input').value.trim();
                    const hasFace = overlay.querySelector('#new-root-has-face-checkbox').checked;
                    if (!name) {
                        alert("Please enter a root category name.");
                        return;
                    }
                    close({ action: 'create_root', name, hasFace });
                } else {
                    close({ action: 'select', root: selected });
                }
            };
        });
    }

    function showDeleteConflictModal(tagName, count, targetTagsOptions) {
        return new Promise((resolve) => {
            const overlay = document.createElement('div');
            overlay.className = 'modal-overlay active';
            
            const dropdownHtml = targetTagsOptions.map(t => `<option value="${t}">${t}</option>`).join('');
            
            overlay.innerHTML = `
                <div class="modal-container" style="max-width: 500px;">
                    <div class="modal-header">
                        <h2>Tag Removal Check</h2>
                        <button class="modal-close-btn">&times;</button>
                    </div>
                    <div class="modal-body">
                        <p style="margin-bottom: 16px; line-height: 1.5; font-size: 14px;">
                            The tag <strong style="color: var(--accent);">${tagName}</strong> is used by <strong>${count}</strong> photos. 
                            Removing it requires clean up. Please choose how you want to handle these photos:
                        </p>
                        <div class="placement-options">
                            <label class="placement-option-label">
                                <input type="radio" name="delete-opt" value="remove" checked>
                                <span>Remove this tag from all affected photos</span>
                            </label>
                            <label class="placement-option-label">
                                <input type="radio" name="delete-opt" value="move">
                                <span>Move affected photos to another tag</span>
                            </label>
                        </div>
                        <div id="move-tag-dropdown-container" style="display: none; padding-left: 24px; margin-top: 8px;">
                            <select id="move-target-select" class="taxonomy-search-input" style="width: 100%;">
                                <option value="">-- Select Target Tag --</option>
                                ${dropdownHtml}
                            </select>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button class="btn btn-secondary btn-cancel">Cancel</button>
                        <button class="btn btn-primary btn-confirm">Confirm</button>
                    </div>
                </div>
            `;
            
            document.body.appendChild(overlay);
            
            const radioMove = overlay.querySelector('input[value="move"]');
            const dropdownContainer = overlay.querySelector('#move-tag-dropdown-container');
            
            overlay.querySelectorAll('input[name="delete-opt"]').forEach(rad => {
                rad.addEventListener('change', (e) => {
                    dropdownContainer.style.display = e.target.value === 'move' ? 'block' : 'none';
                });
            });
            
            const close = (value) => {
                overlay.className = 'modal-overlay';
                setTimeout(() => overlay.remove(), 300);
                resolve(value);
            };
            
            overlay.querySelector('.modal-close-btn').onclick = () => close(null);
            overlay.querySelector('.btn-cancel').onclick = () => close(null);
            
            overlay.querySelector('.btn-confirm').onclick = () => {
                const selected = overlay.querySelector('input[name="delete-opt"]:checked').value;
                if (selected === 'move') {
                    const target = overlay.querySelector('#move-target-select').value;
                    if (!target) {
                        alert("Please select a target tag.");
                        return;
                    }
                    close({ action: 'move', target_tag: target });
                } else {
                    close({ action: 'remove' });
                }
            };
        });
    }

    /**
     * Turn typed text into a taxonomy path, asking where it belongs if need be.
     *
     * With `prompt: false` it will not ask: anything that needs a decision returns
     * null instead of opening the placement modal. That is what lets a blur commit
     * the unambiguous cases and leave the rest alone, rather than deciding when to
     * ask by predicting this function's behaviour -- a copy that would drift.
     */
    async function resolveTagOrPerson(inputName, isPersonField = false, { prompt = true } = {}) {
        inputName = inputName.trim();
        if (!inputName) return null;

        const askWhereItGoes = (...args) => (prompt ? showPlacementModal(...args) : null);
        
        if (inputName.includes('/')) {
            await fetch('/api/taxonomy/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: inputName })
            });
            await loadTaxonomy();
            return inputName;
        }
        
        const allRoots = taxonomyNodes.filter(n => n.parent_id === null);
        const peopleRoots = allRoots.filter(r => r.has_face === 1);
        const keywordRoots = allRoots.filter(r => r.has_face === 0);
        
        const matches = taxonomyNodes.filter(n => n.name.toLowerCase() === inputName.toLowerCase());
        
        if (isPersonField) {
            if (matches.length > 0) {
                const peopleMatches = matches.filter(m => {
                    const parts = m.tag.split('/');
                    const rootName = parts[0];
                    const rootNode = allRoots.find(r => r.name.toLowerCase() === rootName.toLowerCase());
                    return rootNode && rootNode.has_face === 1;
                });
                
                if (peopleMatches.length === 1) {
                    return peopleMatches[0].tag;
                } else if (peopleMatches.length > 1) {
                    const options = peopleMatches.map(m => m.tag);
                    const res = await askWhereItGoes(
                        "Resolve Ambiguous Person",
                        `Multiple folders exist for "${inputName}". Please select which one you mean:`,
                        options,
                        false
                    );
                    return res ? res.root : null;
                }
            }
            
            const peopleRootNames = peopleRoots.map(r => r.name);
            if (peopleRootNames.length === 0) {
                await fetch('/api/taxonomy/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: "People", has_face: 1 })
                });
                await loadTaxonomy();
                return `People/${inputName}`;
            } else if (peopleRootNames.length === 1) {
                const targetPath = `${peopleRootNames[0]}/${inputName}`;
                await fetch('/api/taxonomy/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: targetPath })
                });
                await loadTaxonomy();
                return targetPath;
            } else {
                const res = await askWhereItGoes(
                    "Resolve New Person",
                    `The person "${inputName}" is new. Please select which people folder to add them under:`,
                    peopleRootNames,
                    false
                );
                if (!res) return null;
                const targetPath = `${res.root}/${inputName}`;
                await fetch('/api/taxonomy/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: targetPath })
                });
                await loadTaxonomy();
                return targetPath;
            }
        } else {
            if (matches.length === 1) {
                return matches[0].tag;
            } else if (matches.length > 1) {
                const options = matches.map(m => m.tag);
                const res = await askWhereItGoes(
                    "Resolve Ambiguous Tag",
                    `Multiple tag paths exist for "${inputName}". Please select which one you mean:`,
                    options,
                    false
                );
                return res ? res.root : null;
            }
            
            const rootNames = keywordRoots.map(r => r.name);
            const res = await askWhereItGoes(
                "Resolve New Tag",
                `The tag "${inputName}" is new. Please specify which category it should be placed under, or create a new one:`,
                rootNames,
                true
            );
            if (!res) return null;
            
            let targetPath;
            if (res.action === 'create_root') {
                const rootRes = await fetch('/api/taxonomy/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: res.name, has_face: res.hasFace ? 1 : 0 })
                }).then(r => r.json());
                
                if (!rootRes.success) {
                    alert("Error creating root category: " + rootRes.error);
                    return null;
                }
                targetPath = `${res.name}/${inputName}`;
            } else {
                targetPath = `${res.root}/${inputName}`;
            }
            
            await fetch('/api/taxonomy/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: targetPath })
            });
            await loadTaxonomy();
            return targetPath;
        }
    }
});
