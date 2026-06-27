// app.js - Standalone TagPup GUI Logic
document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const folderPathInput = document.getElementById('folder-path-input');
    const btnBrowseFolder = document.getElementById('btn-browse-folder');
    const btnScanFolder = document.getElementById('btn-scan-folder');
    const btnSuggestTags = document.getElementById('btn-suggest-tags');
    const suggestProgressContainer = document.getElementById('suggest-progress-container');
    const suggestProgressBar = document.getElementById('suggest-progress-bar');
    const suggestProgressText = document.getElementById('suggest-progress-text');
    
    const photoSearch = document.getElementById('photo-search');
    const photoList = document.getElementById('photo-list');
    const listStats = document.getElementById('list-stats');
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
    const folderSelectionSidebar = document.getElementById('folder-selection-sidebar');
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

    // Load Datalists on Startup
    fetchKnownTagsAndPeople();

    // Check for folder path in URL on startup
    const params = new URLSearchParams(window.location.search);
    const initialPath = params.get('path');
    if (initialPath) {
        folderPathInput.value = initialPath;
        scanFolder(false);
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
    btnScanFolder.addEventListener('click', () => scanFolder(false));
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
    
    btnSaveTitle.addEventListener('click', saveSingleTitle);
    btnAddPerson.addEventListener('click', saveSingleAddPerson);
    btnAddTag.addEventListener('click', saveSingleAddTag);
    inputPhotoTitle.addEventListener('keydown', (e) => { if (e.key === 'Enter') saveSingleTitle(); });
    inputAddPerson.addEventListener('keydown', (e) => { if (e.key === 'Enter') saveSingleAddPerson(); });
    inputAddTag.addEventListener('keydown', (e) => { if (e.key === 'Enter') saveSingleAddTag(); });

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
            alert("Please enter a grouping name.");
            renameGroupingInput.focus();
            return;
        }

        if (selectedThumbnails.length === 0) {
            alert("No photos selected for renaming.");
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
            
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Ready';
            alert("Smart rename completed successfully!");
        })
        .catch(err => {
            console.error(err);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
            btnApplyRename.disabled = false;
            alert("Error during smart rename: " + err.message);
        });
    });

    document.addEventListener('keydown', (e) => {
        if (document.activeElement && (
            document.activeElement.tagName === 'INPUT' || 
            document.activeElement.tagName === 'TEXTAREA' || 
            document.activeElement.isContentEditable
        )) {
            return;
        }

        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            
            const items = Array.from(photoList.querySelectorAll('.photo-item-file'));
            if (items.length === 0) return;
            
            let currentIndex = items.findIndex(item => item.getAttribute('data-path') === activePhotoPath);
            let nextIndex = -1;
            
            if (e.key === 'ArrowDown') {
                if (currentIndex === -1) {
                    nextIndex = 0;
                } else {
                    nextIndex = Math.min(currentIndex + 1, items.length - 1);
                }
            } else if (e.key === 'ArrowUp') {
                if (currentIndex === -1) {
                    nextIndex = items.length - 1;
                } else {
                    nextIndex = Math.max(currentIndex - 1, 0);
                }
            }
            
            if (nextIndex !== -1 && nextIndex !== currentIndex) {
                const targetPath = items[nextIndex].getAttribute('data-path');
                selectPhoto(targetPath);
                
                // Scroll the selected item into view inside the sidebar
                items[nextIndex].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            }
        }
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
        if (tagLower === 'family' || tagLower === 'friends' || tagLower === 'people') {
            return true;
        }
        if (tag.startsWith('Family/') || tag.startsWith('Friends/') || tag.startsWith('People/')) {
            return true;
        }
        const leaf = tag.includes('/') ? tag.split('/').pop().trim() : tag;
        if (knownPeople.includes(leaf)) {
            return true;
        }
        return false;
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
        
        const peopleSet = new Set(knownPeople);
        
        knownTags.forEach(t => {
            if (t.startsWith('People/') || t.startsWith('Family/') || t.startsWith('Friends/')) {
                const leaf = t.split('/').pop().trim();
                peopleSet.add(leaf);
            }
        });
        
        folderPhotos.forEach(p => {
            if (p.people) p.people.forEach(name => peopleSet.add(name));
        });

        const sortedPeople = Array.from(peopleSet).sort();
        sortedPeople.forEach(p => {
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
            alert("Please select or enter a valid folder path.");
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
                        listStats.textContent = `${folderPhotos.length} files loaded (Cached)`;
                        folderViewHeader.classList.remove('hidden');
                        btnSuggestTags.disabled = false;
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
                listStats.textContent = `${folderPhotos.length} files loaded`;
                
                // Update URL search path parameter
                const url = new URL(window.location);
                url.searchParams.set('path', path);
                window.history.replaceState({}, '', url);
                
                // Save to cache
                saveToLocalStorageCache();
                
                // Show folder view header item
                folderViewHeader.classList.remove('hidden');
                
                // Enable suggest tags button
                btnSuggestTags.disabled = false;
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

    // Render list in Sidebar
    function renderFileList() {
        // Remove old files
        photoList.querySelectorAll('.photo-item-file').forEach(el => el.remove());

        const query = photoSearch.value.toLowerCase().trim();
        const filtered = folderPhotos.filter(photo => {
            if (!query) return true;
            const fname = photo.filename || photo.path.split(/[/\\]/).pop() || "";
            return fname.toLowerCase().includes(query) || 
                   (photo.title && photo.title.toLowerCase().includes(query)) ||
                   photo.tags.some(t => t.toLowerCase().includes(query));
        });

        const fragment = document.createDocumentFragment();
        filtered.forEach(photo => {
            const li = document.createElement('li');
            li.className = 'photo-item photo-item-file';
            if (activePhotoPath === photo.path) {
                li.classList.add('active');
            }
            li.setAttribute('data-path', photo.path);

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

            // Display a badge if suggestion is ready for this file
            if (folderSuggestions[photo.path]) {
                const badge = document.createElement('span');
                badge.className = 'photo-item-has-sugg';
                badge.textContent = 'AI';
                li.appendChild(badge);
            }

            li.addEventListener('click', () => selectPhoto(photo.path));
            fragment.appendChild(li);
        });
        photoList.appendChild(fragment);
    }

    let searchTimeout = null;
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

    // Render Grid Thumbnails
    function renderThumbnails() {
        thumbnailsGrid.innerHTML = '';

        const query = photoSearch.value.toLowerCase().trim();
        const filtered = folderPhotos.filter(photo => {
            if (!query) return true;
            return photo.filename.toLowerCase().includes(query) || 
                   (photo.title && photo.title.toLowerCase().includes(query)) ||
                   photo.tags.some(t => t.toLowerCase().includes(query));
        });

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
        
        if (selectedThumbnails.length > 0) {
            folderSelectionSidebar.classList.remove('hidden');
            
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
                    const isPerson = knownPeople.includes(tag) || photoPeople.includes(tag) || tag.startsWith('People/');
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
                            const isPerson = knownPeople.includes(leaf) || leaf.startsWith('People/');
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
            folderSelectionSidebar.classList.add('hidden');
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
                btnSuggestTags.disabled = false;
                throw new Error(data.error);
            }
        })
        .catch(err => {
            btnSuggestTags.disabled = false;
            console.error(err);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
            alert("Error starting suggestions: " + err.message);
        });
    }

    function checkSuggestionsStatus(folderPath) {
        if (progressTimer) clearInterval(progressTimer);

        function queryProgress() {
            fetch(`/api/folder/suggest-status?path=${encodeURIComponent(folderPath)}`)
                .then(res => res.json())
                .then(data => {
                    if (data.status === 'running') {
                        suggestProgressContainer.classList.remove('hidden');
                        const total = data.total || 0;
                        const completed = data.completed || 0;
                        const pct = total > 0 ? Math.round((completed / total) * 100) : 0;
                        
                        suggestProgressBar.style.width = `${pct}%`;
                        suggestProgressText.textContent = `Processing: ${completed} / ${total} (${pct}%)`;
                        
                        // Merge progressive suggestions
                        folderSuggestions = data.suggestions || {};
                        renderFileList(); // updates tags badge dynamically
                        saveToLocalStorageCache();
                    } 
                    else if (data.status === 'completed') {
                        clearInterval(progressTimer);
                        suggestProgressContainer.classList.add('hidden');
                        btnSuggestTags.disabled = false;
                        
                        folderSuggestions = data.suggestions || {};
                        btnFolderAutoApply.disabled = false;
                        
                        renderFileList();
                        saveToLocalStorageCache();
                        
                        // If active photo selected, refresh suggestions pane
                        if (activePhotoPath) {
                            renderSuggestionsPanel(activePhotoPath);
                        }

                        statusDot.className = 'status-indicator-dot';
                        statusText.textContent = 'Ready';
                    }
                    else if (data.status === 'error' || data.status === 'not_started') {
                        clearInterval(progressTimer);
                        suggestProgressContainer.classList.add('hidden');
                        btnSuggestTags.disabled = false;
                        if (data.status === 'error') {
                            statusDot.className = 'status-indicator-dot';
                            statusText.textContent = 'Error';
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

    function applySuggestedTagDirect(tagName, isPerson) {
        const path = activePhotoPath;
        if (!path) return;
        const photo = folderPhotos.find(p => p.path === path);
        if (!photo) return;

        const leaf = tagName.includes('/') ? tagName.split('/').pop().trim() : tagName;

        if (photo.tags.includes(leaf)) return;
        const updatedTags = [...photo.tags, leaf];

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

    function applyAllSingleSuggestions() {
        const path = activePhotoPath;
        if (!path) return;
        const photo = folderPhotos.find(p => p.path === path);
        const sugg = folderSuggestions[path];
        if (!photo || !sugg) return;

        const suggTags = (sugg.tags || []).map(t => t.tag);
        const suggPeople = (sugg.people || []).map(p => p.name);
        const allSuggestions = Array.from(new Set([...suggTags, ...suggPeople]));
        
        const updatedTags = Array.from(new Set([...photo.tags, ...allSuggestions]));
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

        if (!confirm(`This will auto-apply all suggested tags and people across the ${selectedThumbnails.length} selected photos. Continue?`)) {
            return;
        }

        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Applying Suggestions...';

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
                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Ready';
                alert("AI suggestions auto-applied to selected photos!");
                scanFolder(true); // Rescan folder to load updated tags
            } else {
                throw new Error(data.error);
            }
        })
        .catch(err => {
            console.error(err);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
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
            
            // Check people suggestions
            if (sugg.people) {
                for (let p of sugg.people) {
                    if (p.score >= 0.75) {
                        const leaf = p.name;
                        if (!currentTags.includes(leaf) && !photoPeople.includes(leaf)) {
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
            const raw = photo.raw_metadata || {};
            const model = raw["EXIF:Model"] || raw["Model"] || raw["EXIF:Make"] || raw["Make"] || "Unknown Camera";
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

    function applyTimeShift() {
        const folder = scannedFolder;
        if (!folder) return;
        
        const cameraModel = timeshiftCameraSelect.value;
        const minutes = parseInt(timeshiftMinutesInput.value, 10);
        
        if (isNaN(minutes) || minutes === 0) {
            alert("Please enter a non-zero shift in minutes.");
            return;
        }
        
        const promptMsg = `This will shift the Date Taken of all photos for camera "${cameraModel}" by ${minutes} minutes. Continue?`;
        if (!confirm(promptMsg)) {
            return;
        }
        
        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Applying time shift...';
        
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
                
                statusDot.className = 'status-indicator-dot';
                statusText.textContent = 'Ready';
                alert("Time shift applied successfully!");
                
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
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
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
                const isPeople = confirm(`Is "${name}" a category for People?`);
                createTaxonomyNode(name.trim(), null, isPeople ? 1 : 0);
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
                taxonomyNodes = data;
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
            const peopleLabel = document.createElement('label');
            peopleLabel.style.display = 'inline-flex';
            peopleLabel.style.alignItems = 'center';
            peopleLabel.style.gap = '4px';
            peopleLabel.style.fontSize = '12px';
            peopleLabel.style.marginRight = '8px';
            peopleLabel.title = "Designate this category as a People list";
            
            const peopleCheckbox = document.createElement('input');
            peopleCheckbox.type = 'checkbox';
            peopleCheckbox.checked = node.is_people === 1;
            peopleCheckbox.onchange = (e) => {
                updateTaxonomyNode(node.id, { is_people: e.target.checked ? 1 : 0 });
            };
            peopleLabel.appendChild(peopleCheckbox);
            
            const labelText = document.createElement('span');
            labelText.textContent = 'People';
            peopleLabel.appendChild(labelText);
            
            actions.appendChild(peopleLabel);
        } else {
            if (node.is_people === 1) {
                const badge = document.createElement('span');
                badge.className = 'taxonomy-node-badge badge-people';
                badge.textContent = 'Person';
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

    function createTaxonomyNode(name, parentId = null, isPeople = 0) {
        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Creating tag...';
        
        fetch('/api/taxonomy/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, parent_id: parentId, is_people: isPeople })
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
                            <input type="checkbox" id="new-root-is-people-checkbox">
                            <span>This is a People category</span>
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
                    const isPeople = overlay.querySelector('#new-root-is-people-checkbox').checked;
                    if (!name) {
                        alert("Please enter a root category name.");
                        return;
                    }
                    close({ action: 'create_root', name, isPeople });
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

    async function resolveTagOrPerson(inputName, isPersonField = false) {
        inputName = inputName.trim();
        if (!inputName) return null;
        
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
        const peopleRoots = allRoots.filter(r => r.is_people === 1);
        const keywordRoots = allRoots.filter(r => r.is_people === 0);
        
        const matches = taxonomyNodes.filter(n => n.name.toLowerCase() === inputName.toLowerCase());
        
        if (isPersonField) {
            if (matches.length > 0) {
                const peopleMatches = matches.filter(m => {
                    const parts = m.tag.split('/');
                    const rootName = parts[0];
                    const rootNode = allRoots.find(r => r.name.toLowerCase() === rootName.toLowerCase());
                    return rootNode && rootNode.is_people === 1;
                });
                
                if (peopleMatches.length === 1) {
                    return peopleMatches[0].tag;
                } else if (peopleMatches.length > 1) {
                    const options = peopleMatches.map(m => m.tag);
                    const res = await showPlacementModal(
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
                    body: JSON.stringify({ name: "People", is_people: 1 })
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
                const res = await showPlacementModal(
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
                const res = await showPlacementModal(
                    "Resolve Ambiguous Tag",
                    `Multiple tag paths exist for "${inputName}". Please select which one you mean:`,
                    options,
                    false
                );
                return res ? res.root : null;
            }
            
            const rootNames = allRoots.map(r => r.name);
            const res = await showPlacementModal(
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
                    body: JSON.stringify({ name: res.name, is_people: res.isPeople ? 1 : 0 })
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
