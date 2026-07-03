// app.js

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
    const modeSelect = document.getElementById('tuner-mode');
    const photoSearch = document.getElementById('photo-search');
    const photoList = document.getElementById('photo-list');
    const listStats = document.getElementById('list-stats');
    const emptyState = document.getElementById('empty-state');
    const panelContent = document.getElementById('panel-content');
    
    const mainImage = document.getElementById('main-image');
    const detailPath = document.getElementById('detail-path');
    const detailFilename = document.getElementById('detail-filename');
    const detailTitle = document.getElementById('detail-title');
    const detailTags = document.getElementById('detail-tags');
    const detailYear = document.getElementById('detail-year');
    const facesGrid = document.getElementById('faces-grid');
    const btnRefreshList = document.getElementById('btn-refresh-list');
    const sidebar = document.querySelector('.sidebar');
    const sidebarResizer = document.getElementById('sidebar-resizer');
    const btnAutomatchAll = document.getElementById('btn-automatch-all');
    const btnUnmatchAll = document.getElementById('btn-unmatch-all');
    const showMatchedToggle = document.getElementById('show-matched-toggle');
    const showMatchedContainer = document.getElementById('show-matched-container');

    // Face Matching Mode DOM Elements
    const faceMatchingContent = document.getElementById('face-matching-content');
    const matchingPersonName = document.getElementById('matching-person-name');
    const matchingPersonCount = document.getElementById('matching-person-count');
    const btnUnmatchSelected = document.getElementById('btn-unmatch-selected');
    const matchingFacesGrid = document.getElementById('matching-faces-grid');
    const inputReassignName = document.getElementById('input-reassign-name');
    const btnReassignSelected = document.getElementById('btn-reassign-selected');
    const btnNewPerson = document.getElementById('btn-new-person');
    const btnRenamePerson = document.getElementById('btn-rename-person');

    // New Person Modal DOM Elements
    const newPersonModal = document.getElementById('new-person-modal');
    const btnCloseModal = document.getElementById('btn-close-modal');
    const newPersonName = document.getElementById('new-person-name');
    const modalNameError = document.getElementById('modal-name-error');
    const btnMatchSelectAll = document.getElementById('btn-match-select-all');
    const btnMatchSelectNone = document.getElementById('btn-match-select-none');
    const modalMatchesLoading = document.getElementById('modal-matches-loading');
    const modalMatchesList = document.getElementById('modal-matches-list');
    const btnModalCancel = document.getElementById('btn-modal-cancel');
    const btnModalSave = document.getElementById('btn-modal-save');

    // Face Matching Details Sidebar DOM Elements
    const matchingDetailsPlaceholder = document.getElementById('matching-details-placeholder');
    const matchingDetailsContent = document.getElementById('matching-details-content');
    const matchingDetailImg = document.getElementById('matching-detail-img');
    const matchingDetailBoundingBoxOverlay = document.getElementById('matching-detail-bounding-box-overlay');
    const matchingDetailId = document.getElementById('matching-detail-id');
    const matchingDetailConfidence = document.getElementById('matching-detail-confidence');
    const matchingDetailBox = document.getElementById('matching-detail-box');
    const matchingDetailPath = document.getElementById('matching-detail-path');
    const matchingDetailTags = document.getElementById('matching-detail-tags');
    const matchingDetailPeople = document.getElementById('matching-detail-people');
    const matchingDetailDiagnostics = document.getElementById('matching-detail-diagnostics');

    let allPhotos = [];
    let activePhotoPath = null;
    let allKnownPeople = [];
    let currentPhotoDetails = null;

    // Face Matching Mode State
    let allPeopleWithCounts = [];
    let activePersonName = null;
    let lastLoadedPersonName = null;
    let selectedFaceIds = [];
    let activePersonFaces = [];
    let activeTab = 'matches'; // 'matches' or 'outliers'
    let modalSelectedFaceIds = [];

    // Abort controllers for ongoing fetch requests
    let sidebarAbortController = null;
    let detailsAbortController = null;
    let sidebarDetailAbortController = null;

    // Load initial data
    const urlParams = new URLSearchParams(window.location.search);
    const urlMode = urlParams.get('mode');
    if (urlMode && (urlMode === 'folder-match' || urlMode === 'face-matching' || urlMode === 'unmatched-faces')) {
        modeSelect.value = urlMode;
    }

    const urlPhoto = urlParams.get('photo');
    if (urlPhoto) {
        activePhotoPath = urlPhoto;
    }

    const urlPerson = urlParams.get('person');
    if (urlPerson) {
        activePersonName = urlPerson;
    }

    const urlShowMatched = urlParams.get('show_matched');
    if (urlShowMatched === 'true' && showMatchedToggle) {
        showMatchedToggle.checked = true;
    }
    updateMatchedToggleVisibility();

    fetchPhotos();
    fetchKnownPeople();

    // Event Listeners
    modeSelect.addEventListener('change', () => {
        updateMatchedToggleVisibility();
        updateURLParams();
        fetchPhotos();
    });

    if (showMatchedToggle) {
        showMatchedToggle.addEventListener('change', () => {
            updateURLParams();
            fetchPhotos();
        });
    }

    function updateMatchedToggleVisibility() {
        const mode = modeSelect.value;
        if (mode === 'folder-match') {
            if (showMatchedContainer) showMatchedContainer.classList.remove('hidden');
        } else {
            if (showMatchedContainer) showMatchedContainer.classList.add('hidden');
        }
    }

    photoSearch.addEventListener('input', filterPhotos);
    if (btnRefreshList) {
        btnRefreshList.addEventListener('click', fetchPhotos);
    }
    if (btnAutomatchAll) {
        btnAutomatchAll.addEventListener('click', () => {
            if (!activePhotoPath) return;
            postAutoMatchAll(activePhotoPath);
        });
    }
    if (btnUnmatchAll) {
        btnUnmatchAll.addEventListener('click', () => {
            if (!activePhotoPath) return;
            if (confirm('Are you sure you want to unmatch all faces in this photo?')) {
                postUnmatchAll(activePhotoPath);
            }
        });
    }
    if (btnUnmatchSelected) {
        btnUnmatchSelected.addEventListener('click', () => {
            if (selectedFaceIds.length === 0) return;
            if (confirm(`Are you sure you want to unmatch the ${selectedFaceIds.length} selected face(s)?`)) {
                postUnmatchBulk(selectedFaceIds);
            }
        });
    }
    if (inputReassignName) {
        inputReassignName.addEventListener('input', updateMatchingSelectionUI);
    }
    if (btnReassignSelected) {
        btnReassignSelected.addEventListener('click', () => {
            const name = inputReassignName.value.trim();
            if (!name || selectedFaceIds.length === 0) return;
            if (!allKnownPeople.includes(name)) {
                if (!confirm(`"${name}" is not currently in the database. Do you want to create a new person tag and assign it to the selected face(s)?`)) {
                    return;
                }
            } else {
                if (!confirm(`Are you sure you want to assign the ${selectedFaceIds.length} selected face(s) to "${name}"?`)) {
                    return;
                }
            }
            postMatchBulk(selectedFaceIds, name);
        });
    }

    if (btnNewPerson) {
        btnNewPerson.addEventListener('click', () => {
            if (selectedFaceIds.length !== 1) return;
            const seedFaceId = selectedFaceIds[0];
            openNewPersonModal(seedFaceId);
        });
    }

    if (btnRenamePerson) {
        btnRenamePerson.addEventListener('click', () => {
            if (!activePersonName || activePersonName === 'Unmatched') return;
            const newName = prompt(`Rename person "${activePersonName}" to:`, activePersonName);
            if (newName === null) return;
            const trimmed = newName.trim();
            if (!trimmed) {
                alert('Name cannot be empty.');
                return;
            }
            if (trimmed === activePersonName) return;
            
            if (!confirm(`Are you sure you want to rename "${activePersonName}" to "${trimmed}"? This will update all of their matched face tags and photo metadata.`)) {
                return;
            }
            
            postRenamePerson(activePersonName, trimmed);
        });
    }

    const btnMatchingSelectAll = document.getElementById('btn-matching-select-all');
    if (btnMatchingSelectAll) {
        btnMatchingSelectAll.addEventListener('click', () => {
            const faceItems = matchingFacesGrid.querySelectorAll('.face-match-item');
            const faceIds = Array.from(faceItems).map(item => parseInt(item.getAttribute('data-face-id')));
            
            const allSelected = faceIds.every(id => selectedFaceIds.includes(id));
            
            if (allSelected) {
                selectedFaceIds = selectedFaceIds.filter(id => !faceIds.includes(id));
                faceItems.forEach(item => item.classList.remove('selected'));
            } else {
                faceIds.forEach(id => {
                    if (!selectedFaceIds.includes(id)) {
                        selectedFaceIds.push(id);
                    }
                });
                faceItems.forEach(item => item.classList.add('selected'));
            }
            
            updateMatchingSelectionUI();
            
            if (selectedFaceIds.length > 0) {
                showFaceDetails(selectedFaceIds[selectedFaceIds.length - 1]);
            } else {
                clearFaceDetails();
            }
        });
    }

    function openNewPersonModal(seedFaceId) {
        if (newPersonName) {
            newPersonName.value = '';
        }
        if (modalNameError) {
            modalNameError.classList.add('hidden');
        }
        if (btnModalSave) {
            btnModalSave.disabled = true;
        }
        if (newPersonModal) {
            newPersonModal.classList.remove('hidden');
        }
        modalSelectedFaceIds = [];
        if (modalMatchesLoading) {
            modalMatchesLoading.classList.remove('hidden');
            modalMatchesLoading.textContent = 'Finding similar faces...';
        }
        if (modalMatchesList) {
            modalMatchesList.innerHTML = '';
        }

        fetch(`/api/face-matches-unmatched?id=${seedFaceId}`)
            .then(res => {
                if (!res.ok) throw new Error('Failed to fetch similar faces');
                return res.json();
            })
            .then(data => {
                if (modalMatchesLoading) {
                    modalMatchesLoading.classList.add('hidden');
                }
                renderModalMatches(data.matches);
            })
            .catch(err => {
                console.error(err);
                if (modalMatchesLoading) {
                    modalMatchesLoading.textContent = 'Error finding similar faces.';
                }
            });
    }

    function renderModalMatches(matches) {
        if (!modalMatchesList) return;
        modalMatchesList.innerHTML = '';
        if (!matches || matches.length === 0) {
            const noMatches = document.createElement('div');
            noMatches.style.gridColumn = '1 / -1';
            noMatches.style.textAlign = 'center';
            noMatches.style.padding = '20px';
            noMatches.style.color = 'var(--text-muted)';
            noMatches.textContent = 'No similar unmatched faces found (similarity >= 0.8).';
            modalMatchesList.appendChild(noMatches);
            return;
        }

        matches.forEach(match => {
            const card = document.createElement('div');
            card.className = 'modal-face-card';
            card.setAttribute('data-modal-face-id', match.id);

            const imgWrapper = document.createElement('div');
            imgWrapper.className = 'modal-face-card-img-wrapper';
            const img = document.createElement('img');
            img.src = `/api/face-crop?id=${match.id}`;
            img.alt = 'Similar Face';
            img.loading = 'lazy';
            imgWrapper.appendChild(img);
            card.appendChild(imgWrapper);

            const details = document.createElement('div');
            details.className = 'modal-face-card-details';

            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.className = 'modal-face-card-checkbox';
            checkbox.addEventListener('click', (e) => {
                e.stopPropagation();
                toggleModalFaceSelection(match.id, checkbox.checked, card);
            });
            details.appendChild(checkbox);

            const score = document.createElement('span');
            score.className = 'modal-face-card-score';
            score.textContent = (match.similarity * 100).toFixed(1) + '%';
            details.appendChild(score);
            card.appendChild(details);

            const filename = document.createElement('span');
            filename.className = 'modal-face-card-filename';
            filename.textContent = match.filename;
            filename.title = match.filename;
            card.appendChild(filename);

            card.addEventListener('click', () => {
                checkbox.checked = !checkbox.checked;
                toggleModalFaceSelection(match.id, checkbox.checked, card);
            });

            modalMatchesList.appendChild(card);
        });
    }

    function toggleModalFaceSelection(faceId, isSelected, cardElement) {
        const idx = modalSelectedFaceIds.indexOf(faceId);
        if (isSelected) {
            if (idx === -1) modalSelectedFaceIds.push(faceId);
            cardElement.classList.add('selected');
        } else {
            if (idx > -1) modalSelectedFaceIds.splice(idx, 1);
            cardElement.classList.remove('selected');
        }
    }

    if (btnMatchSelectAll) {
        btnMatchSelectAll.addEventListener('click', () => {
            if (!modalMatchesList) return;
            const cards = modalMatchesList.getElementsByClassName('modal-face-card');
            Array.from(cards).forEach(card => {
                const faceIdStr = card.getAttribute('data-modal-face-id');
                if (!faceIdStr) return;
                const faceId = parseInt(faceIdStr);
                const checkbox = card.querySelector('.modal-face-card-checkbox');
                if (checkbox) checkbox.checked = true;
                if (!modalSelectedFaceIds.includes(faceId)) {
                    modalSelectedFaceIds.push(faceId);
                }
                card.classList.add('selected');
            });
        });
    }

    if (btnMatchSelectNone) {
        btnMatchSelectNone.addEventListener('click', () => {
            if (!modalMatchesList) return;
            const cards = modalMatchesList.getElementsByClassName('modal-face-card');
            Array.from(cards).forEach(card => {
                const checkbox = card.querySelector('.modal-face-card-checkbox');
                if (checkbox) checkbox.checked = false;
                card.classList.remove('selected');
            });
            modalSelectedFaceIds = [];
        });
    }

    if (newPersonName) {
        newPersonName.addEventListener('input', () => {
            validateNewPersonName();
        });
    }

    function validateNewPersonName() {
        if (!newPersonName || !modalNameError || !btnModalSave) return false;
        const val = newPersonName.value.trim();
        if (!val) {
            modalNameError.textContent = 'Name cannot be empty.';
            modalNameError.classList.remove('hidden');
            btnModalSave.disabled = true;
            return false;
        }

        const isDup = allKnownPeople.some(p => p.toLowerCase() === val.toLowerCase());
        if (isDup) {
            modalNameError.textContent = 'This name already exists in the database. Please enter a unique name.';
            modalNameError.classList.remove('hidden');
            btnModalSave.disabled = true;
            return false;
        }

        modalNameError.classList.add('hidden');
        btnModalSave.disabled = false;
        return true;
    }

    if (btnModalSave) {
        btnModalSave.addEventListener('click', () => {
            const name = newPersonName.value.trim();
            if (!validateNewPersonName()) return;

            const seedFaceId = selectedFaceIds[0];
            const allFaceIdsToMatch = [seedFaceId, ...modalSelectedFaceIds];

            btnModalSave.disabled = true;
            btnModalSave.textContent = 'Saving...';

            fetch('/api/faces/match-bulk', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ face_ids: allFaceIdsToMatch, person_name: name })
            })
            .then(async res => {
                if (!res.ok) {
                    let errMsg = 'Matching failed';
                    try {
                        const errData = await res.json();
                        if (errData && errData.error) errMsg = errData.error;
                    } catch(e) {}
                    throw new Error(errMsg);
                }
                return res.json();
            })
            .then(data => {
                if (data.success) {
                    if (newPersonModal) {
                        newPersonModal.classList.add('hidden');
                    }
                    
                    selectedFaceIds = [];
                    updateMatchingSelectionUI();
                    clearFaceDetails();
                    
                    fetchPeopleWithCounts();
                    fetchKnownPeople();
                    
                    if (activePersonName) {
                        selectPerson(activePersonName);
                    }
                    if (activePhotoPath) {
                        selectPhoto(activePhotoPath);
                    }
                } else {
                    alert('Failed to save matches.');
                }
            })
            .catch(err => {
                console.error(err);
                alert('Error saving matches: ' + err.message);
            })
            .finally(() => {
                btnModalSave.disabled = false;
                btnModalSave.textContent = 'Create & Tag Matches';
            });
        });
    }

    const hideNewPersonModal = () => {
        if (newPersonModal) {
            newPersonModal.classList.add('hidden');
        }
    };

    if (btnCloseModal) {
        btnCloseModal.addEventListener('click', hideNewPersonModal);
    }
    if (btnModalCancel) {
        btnModalCancel.addEventListener('click', hideNewPersonModal);
    }

    const tabMatches = document.getElementById('tab-matches');
    const tabOutliers = document.getElementById('tab-outliers');
    const tabLowConf = document.getElementById('tab-low-conf');
    if (tabMatches && tabOutliers && tabLowConf) {
        tabMatches.addEventListener('click', () => {
            const targetMode = modeSelect.value === 'unmatched-faces' ? 'high' : 'matches';
            if (activeTab === targetMode) return;
            activeTab = targetMode;
            tabMatches.classList.add('active');
            tabOutliers.classList.remove('active');
            tabLowConf.classList.remove('active');
            selectedFaceIds = [];
            renderPersonFaces(activePersonFaces);
            updateMatchingSelectionUI();
            clearFaceDetails();
        });
        tabOutliers.addEventListener('click', () => {
            const targetMode = modeSelect.value === 'unmatched-faces' ? 'lower' : 'outliers';
            if (activeTab === targetMode) return;
            activeTab = targetMode;
            tabOutliers.classList.add('active');
            tabMatches.classList.remove('active');
            tabLowConf.classList.remove('active');
            selectedFaceIds = [];
            renderPersonFaces(activePersonFaces);
            updateMatchingSelectionUI();
            clearFaceDetails();
        });
        tabLowConf.addEventListener('click', () => {
            if (modeSelect.value !== 'unmatched-faces') return;
            if (activeTab === 'low') return;
            activeTab = 'low';
            tabLowConf.classList.add('active');
            tabMatches.classList.remove('active');
            tabOutliers.classList.remove('active');
            selectedFaceIds = [];
            renderPersonFaces(activePersonFaces);
            updateMatchingSelectionUI();
            clearFaceDetails();
        });
    }

    // Sidebar Resizing Logic
    if (sidebar && sidebarResizer) {
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
            
            if (newWidth < 200) newWidth = 200;
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

    // Helper to compare paths robustly on Windows (ignore slash direction and casing)
    function pathsEqual(p1, p2) {
        if (!p1 || !p2) return false;
        return p1.replace(/\\/g, '/').toLowerCase() === p2.replace(/\\/g, '/').toLowerCase();
    }

    function updateURLParams() {
        const params = new URLSearchParams(window.location.search);
        params.set('mode', modeSelect.value);
        const mode = modeSelect.value;
        if (mode === 'face-matching' || mode === 'unmatched-faces') {
            if (activePersonName) {
                params.set('person', activePersonName);
            } else {
                params.delete('person');
            }
            params.delete('photo');
            params.delete('show_matched');
        } else {
            if (activePhotoPath) {
                params.set('photo', activePhotoPath);
            } else {
                params.delete('photo');
            }
            params.delete('person');
            if (showMatchedToggle && showMatchedToggle.checked) {
                params.set('show_matched', 'true');
            } else {
                params.delete('show_matched');
            }
        }
        window.history.replaceState({}, '', `${window.location.pathname}?${params.toString()}`);
    }

    // Fetch photos list from API
    function fetchPhotos() {
        const mode = modeSelect.value;
        updateEmptyState();

        // Abort any ongoing sidebar fetches
        if (sidebarAbortController) {
            sidebarAbortController.abort();
        }
        sidebarAbortController = new AbortController();

        // Abort any ongoing details fetches
        if (detailsAbortController) {
            detailsAbortController.abort();
        }

        if (mode === 'face-matching' || mode === 'unmatched-faces') {
            fetchPeopleWithCounts();
            return;
        }

        // Hide face matching panel if returning to standard modes
        faceMatchingContent.classList.add('hidden');
        if (!activePhotoPath) {
            emptyState.classList.remove('hidden');
            panelContent.classList.add('hidden');
        } else {
            emptyState.classList.add('hidden');
            panelContent.classList.remove('hidden');
        }

        listStats.textContent = 'Loading photos...';
        photoList.innerHTML = '';
        
        const showMatched = showMatchedToggle && showMatchedToggle.checked;
        fetch(`/api/photos?mode=${mode}&show_matched=${showMatched}`, { signal: sidebarAbortController.signal })
            .then(res => {
                if (!res.ok) throw new Error('Network response was not ok');
                return res.json();
            })
            .then(data => {
                allPhotos = data;
                renderPhotoList();
            })
            .catch(err => {
                if (err.name === 'AbortError') return;
                console.error('Error fetching photos:', err);
                listStats.textContent = 'Error loading photos';
            });
    }

    // Render photo list in sidebar grouped by year and folder, sorted descending
    function renderPhotoList() {
        photoList.innerHTML = '';
        
        if (allPhotos.length === 0) {
            listStats.textContent = 'No photos found';
            return;
        }

        listStats.textContent = `Found ${allPhotos.length} photo(s)`;

        // Group photos by year, then by folder path
        const yearGroups = {};
        allPhotos.forEach(photo => {
            const year = photo.year || 'Unknown';
            const folder = photo.folder || 'Root';
            
            if (!yearGroups[year]) {
                yearGroups[year] = {
                    name: year,
                    folders: {},
                    maxMtime: 0
                };
            }
            if (!yearGroups[year].folders[folder]) {
                yearGroups[year].folders[folder] = {
                    name: folder,
                    photos: [],
                    maxMtime: 0
                };
            }
            yearGroups[year].folders[folder].photos.push(photo);
            if (photo.mtime > yearGroups[year].folders[folder].maxMtime) {
                yearGroups[year].folders[folder].maxMtime = photo.mtime;
            }
            if (photo.mtime > yearGroups[year].maxMtime) {
                yearGroups[year].maxMtime = photo.mtime;
            }
        });

        // Sort years descending
        const sortedYears = Object.values(yearGroups).sort((a, b) => {
            if (a.name === 'Unknown') return 1;
            if (b.name === 'Unknown') return -1;
            return b.name - a.name;
        });

        sortedYears.forEach((yearGroup, yearIdx) => {
            // Create Year Container
            const yearLi = document.createElement('li');
            yearLi.className = 'year-group';
            
            // Year Header
            const yearHeader = document.createElement('div');
            yearHeader.className = 'year-header';
            
            const yearChevron = document.createElement('span');
            yearChevron.className = 'chevron-icon';
            // Default expand the first entry (first year)
            const isYearExpanded = (yearIdx === 0);
            yearChevron.textContent = isYearExpanded ? '▼' : '▶';
            yearHeader.appendChild(yearChevron);
            
            const yearTitle = document.createElement('span');
            yearTitle.className = 'year-title';
            yearTitle.textContent = `📅 ${yearGroup.name}`;
            yearHeader.appendChild(yearTitle);
            
            yearLi.appendChild(yearHeader);
            
            // Year Content (Folders list)
            const yearContent = document.createElement('ul');
            yearContent.className = 'year-content';
            yearContent.style.listStyle = 'none';
            if (!isYearExpanded) {
                yearContent.style.display = 'none';
            }
            
            // Toggle logic for Year
            yearHeader.addEventListener('click', () => {
                const collapsed = (yearContent.style.display === 'none');
                yearContent.style.display = collapsed ? 'block' : 'none';
                yearChevron.textContent = collapsed ? '▼' : '▶';
            });
            
            // Sort folders in this year descending
            const sortedFolders = Object.values(yearGroup.folders).sort((a, b) => b.maxMtime - a.maxMtime);
            
            sortedFolders.forEach((folderGroup, folderIdx) => {
                // Create Folder Container
                const folderLi = document.createElement('li');
                folderLi.className = 'folder-group';
                
                // Folder Header
                const folderHeader = document.createElement('div');
                folderHeader.className = 'folder-header';
                
                const folderChevron = document.createElement('span');
                folderChevron.className = 'chevron-icon';
                // Default expand the first entry: first folder of the first year (disabled)
                const isFolderExpanded = false;
                folderChevron.textContent = isFolderExpanded ? '▼' : '▶';
                folderHeader.appendChild(folderChevron);
                
                const folderIcon = document.createElement('span');
                folderIcon.className = 'folder-icon';
                folderIcon.textContent = '📁';
                folderHeader.appendChild(folderIcon);
                
                const folderTitle = document.createElement('span');
                folderTitle.className = 'folder-title';
                const cleanPath = folderGroup.name.replace(/\\/g, '/');
                const parts = cleanPath.split('/');
                const baseName = parts[parts.length - 1] || cleanPath;
                folderTitle.textContent = baseName;
                folderTitle.title = folderGroup.name;
                folderHeader.appendChild(folderTitle);
                
                const totalUnmatched = folderGroup.photos.reduce((sum, p) => sum + p.unmatched_count, 0);
                const folderCount = document.createElement('span');
                folderCount.className = 'folder-count';
                folderCount.textContent = ` (${totalUnmatched})`;
                folderHeader.appendChild(folderCount);
                folderGroup.countEl = folderCount;

                if (totalUnmatched > 0) {
                    const btnFolderAutomatch = document.createElement('button');
                    btnFolderAutomatch.className = 'btn-folder-automatch';
                    btnFolderAutomatch.innerHTML = '🤖';
                    btnFolderAutomatch.title = 'AutoMatch all photos in this folder';
                    btnFolderAutomatch.addEventListener('click', (e) => {
                        e.stopPropagation();
                        postFolderAutoMatch(folderGroup, btnFolderAutomatch);
                    });
                    folderHeader.appendChild(btnFolderAutomatch);
                    folderGroup.btnEl = btnFolderAutomatch;
                }
                
                folderLi.appendChild(folderHeader);
                
                // Folder Content (Photos list)
                const folderContent = document.createElement('ul');
                folderContent.className = 'folder-content';
                folderContent.style.listStyle = 'none';
                if (!isFolderExpanded) {
                    folderContent.style.display = 'none';
                }
                
                // Toggle logic for Folder
                folderHeader.addEventListener('click', () => {
                    const collapsed = (folderContent.style.display === 'none');
                    folderContent.style.display = collapsed ? 'block' : 'none';
                    folderChevron.textContent = collapsed ? '▼' : '▶';
                });
                
                // Render photos inside folder
                folderGroup.photos.sort((a, b) => a.filename.localeCompare(b.filename, undefined, { numeric: true, sensitivity: 'base' }));
                
                folderGroup.photos.forEach(photo => {
                    const li = document.createElement('li');
                    li.className = 'photo-item folder-photo-item';
                    li.photo = photo;
                    
                    if (pathsEqual(photo.path, activePhotoPath)) {
                        li.classList.add('active');
                        // Ensure parent folders / years are expanded if photo is active
                        yearContent.style.display = 'block';
                        yearChevron.textContent = '▼';
                        folderContent.style.display = 'block';
                        folderChevron.textContent = '▼';
                    }

                    const title = document.createElement('div');
                    title.className = 'photo-title';
                    title.textContent = photo.filename;

                    const badgeContainer = document.createElement('div');
                    badgeContainer.className = 'photo-badges-container';
                    badgeContainer.style.display = 'flex';
                    badgeContainer.style.gap = '6px';
                    badgeContainer.style.marginTop = '6px';

                    const badgeUnmatched = document.createElement('span');
                    badgeUnmatched.className = 'photo-badge unmatched';
                    badgeUnmatched.textContent = `${photo.unmatched_count} unmatched`;
                    photo.badgeEl = badgeUnmatched;
                    badgeContainer.appendChild(badgeUnmatched);

                    const badgeMatched = document.createElement('span');
                    badgeMatched.className = 'photo-badge matched';
                    badgeMatched.textContent = `${photo.matched_count || 0} matched`;
                    photo.badgeMatchedEl = badgeMatched;
                    badgeContainer.appendChild(badgeMatched);

                    photo.liEl = li;

                    li.appendChild(title);
                    li.appendChild(badgeContainer);

                    li.addEventListener('click', () => selectPhoto(photo.path, li));
                    folderContent.appendChild(li);
                });
                
                folderLi.appendChild(folderContent);
                yearContent.appendChild(folderLi);
            });
            
            yearLi.appendChild(yearContent);
            photoList.appendChild(yearLi);
        });

        if (activePhotoPath) {
            const exists = allPhotos.some(p => pathsEqual(p.path, activePhotoPath));
            if (exists) {
                selectPhoto(activePhotoPath);
            } else {
                activePhotoPath = null;
                updateURLParams();
            }
        }
    }

    // Filter photo list based on search bar input
    function filterPhotos() {
        const query = photoSearch.value.toLowerCase();
        const mode = modeSelect.value;

        if (mode === 'face-matching' || mode === 'unmatched-faces') {
            const items = Array.from(photoList.children);
            items.forEach(item => {
                if (item.personName) {
                    const match = item.personName.toLowerCase().includes(query);
                    item.style.display = match ? 'block' : 'none';
                }
            });
            return;
        }

        const yearGroups = Array.from(photoList.querySelectorAll('.year-group'));
        
        yearGroups.forEach(yearGroup => {
            const folderGroups = Array.from(yearGroup.querySelectorAll('.folder-group'));
            let yearHasVisiblePhotos = false;

            folderGroups.forEach(folderGroup => {
                const photos = Array.from(folderGroup.querySelectorAll('.photo-item'));
                let folderHasVisiblePhotos = false;

                photos.forEach(photoItem => {
                    const photo = photoItem.photo;
                    if (photo) {
                        const match = photo.filename.toLowerCase().includes(query) || 
                                      photo.path.toLowerCase().includes(query);
                        photoItem.style.display = match ? 'block' : 'none';
                        if (match) {
                            folderHasVisiblePhotos = true;
                        }
                    }
                });

                // Display or hide folder group
                folderGroup.style.display = folderHasVisiblePhotos ? 'block' : 'none';
                
                // If query is not empty, automatically expand folder content to show matching photos
                const folderContent = folderGroup.querySelector('.folder-content');
                const folderChevron = folderGroup.querySelector('.folder-header .chevron-icon');
                if (query.length > 0 && folderHasVisiblePhotos) {
                    if (folderContent) folderContent.style.display = 'block';
                    if (folderChevron) folderChevron.textContent = '▼';
                }

                if (folderHasVisiblePhotos) {
                    yearHasVisiblePhotos = true;
                }
            });

            // Display or hide year group
            yearGroup.style.display = yearHasVisiblePhotos ? 'block' : 'none';

            // If query is not empty, automatically expand year content to show matching folders
            const yearContent = yearGroup.querySelector('.year-content');
            const yearChevron = yearGroup.querySelector('.year-header .chevron-icon');
            if (query.length > 0 && yearHasVisiblePhotos) {
                if (yearContent) yearContent.style.display = 'block';
                if (yearChevron) yearChevron.textContent = '▼';
            }
        });
    }

    // Fetch all unique known people for autocomplete
    function fetchKnownPeople() {
        fetch('/api/people')
            .then(res => {
                if (!res.ok) throw new Error('Failed to fetch people list');
                return res.json();
            })
            .then(data => {
                allKnownPeople = data;
                updatePeopleDatalist();
            })
            .catch(err => console.error('Error fetching people list:', err));
    }

    // Update global datalist elements with known people names
    function updatePeopleDatalist() {
        const datalist = document.getElementById('people-datalist');
        if (!datalist) return;
        datalist.innerHTML = '';
        allKnownPeople.forEach(person => {
            const option = document.createElement('option');
            option.value = person;
            datalist.appendChild(option);
        });
    }

    // Select a photo and load details
    function selectPhoto(path, element) {
        // Remove active class from all items and add to clicked
        const items = photoList.getElementsByClassName('photo-item');
        Array.from(items).forEach(item => item.classList.remove('active'));
        
        let activeEl = element;
        if (!activeEl) {
            // Find the item matching path in list
            activeEl = Array.from(items).find(item => item.photo && pathsEqual(item.photo.path, path));
        }
        if (activeEl) {
            activeEl.classList.add('active');
            
            // Traverse up to expand year/folder content containers if collapsed
            let parent = activeEl.parentElement;
            while (parent && parent !== photoList) {
                if (parent.classList.contains('folder-content') || parent.classList.contains('year-content')) {
                    parent.style.display = 'block';
                    // Update chevron of corresponding header
                    const header = parent.previousElementSibling;
                    if (header) {
                        const chevron = header.querySelector('.chevron-icon');
                        if (chevron) chevron.textContent = '▼';
                    }
                }
                parent = parent.parentElement;
            }
        }

        activePhotoPath = path;
        updateURLParams();
        
        // Abort any ongoing details fetches
        if (detailsAbortController) {
            detailsAbortController.abort();
        }
        detailsAbortController = new AbortController();

        // Show loading state or request
        fetch(`/api/photo-details?path=${encodeURIComponent(path)}`, { signal: detailsAbortController.signal })
            .then(res => {
                if (!res.ok) throw new Error('Failed to load details');
                return res.json();
            })
            .then(details => {
                renderPhotoDetails(details);
            })
            .catch(err => {
                if (err.name === 'AbortError') return;
                console.error('Error loading photo details:', err);
                alert('Error loading photo details');
            });
    }

    // Render photo details inside main panel
    function renderPhotoDetails(details) {
        currentPhotoDetails = details;
        // Update inline badge count on the sidebar list item (if present)
        const items = photoList.getElementsByClassName('photo-item');
        const activeEl = Array.from(items).find(item => item.photo && pathsEqual(item.photo.path, details.path));
        if (activeEl && activeEl.photo) {
            // Count unmatched and matched faces in details.faces
            const unmatchedCount = (details.faces || []).filter(f => !f.name).length;
            const matchedCount = (details.faces || []).filter(f => f.name).length;
            activeEl.photo.unmatched_count = unmatchedCount;
            activeEl.photo.matched_count = matchedCount;
            if (activeEl.photo.badgeEl) {
                activeEl.photo.badgeEl.textContent = `${unmatchedCount} unmatched`;
            }
            if (activeEl.photo.badgeMatchedEl) {
                activeEl.photo.badgeMatchedEl.textContent = `${matchedCount} matched`;
            }
        }

        // Switch views
        emptyState.classList.add('hidden');
        panelContent.classList.remove('hidden');

        // Main original image (loaded as optimized preview size to speed up UI)
        mainImage.src = `/api/photo-file?path=${encodeURIComponent(details.path)}&size=1024`;
        mainImage.alt = details.filename;

        // Metadata details
        detailPath.textContent = details.path;
        detailFilename.textContent = details.filename;
        if (detailYear) {
            detailYear.textContent = details.year || 'Unknown';
        }
        detailTitle.textContent = details.caption || 'No description available';

        // Tag pills
        detailTags.innerHTML = '';
        
        // People pills
        const peopleList = details.people || [];
        peopleList.forEach(person => {
            const pill = document.createElement('span');
            pill.className = 'tag-pill people-tag';
            pill.textContent = `👤 ${person}`;
            detailTags.appendChild(pill);
        });

        // Category / Keyword tags
        const tagsList = details.tags || [];
        tagsList.forEach(tag => {
            const cleanTag = tag.replace(/\\/g, '/');
            const leaf = cleanTag.includes('/') ? cleanTag.split('/').pop().trim() : cleanTag.trim();
            if (peopleList.some(p => p.toLowerCase() === leaf.toLowerCase())) {
                return;
            }
            const pill = document.createElement('span');
            pill.className = 'tag-pill';
            pill.textContent = tag;
            detailTags.appendChild(pill);
        });

        if (peopleList.length === 0 && tagsList.length === 0) {
            const emptyTags = document.createElement('span');
            emptyTags.style.color = 'var(--text-muted)';
            emptyTags.style.fontSize = '13px';
            emptyTags.textContent = 'No tags present';
            detailTags.appendChild(emptyTags);
        }

        // Render detected faces
        facesGrid.innerHTML = '';
        const faces = details.faces || [];

        // Sort faces: matched faces first, then sorted by max correlation (similarity) to known faces descending
        faces.sort((a, b) => {
            const aMatched = a.name ? 1 : 0;
            const bMatched = b.name ? 1 : 0;
            if (aMatched !== bMatched) {
                return bMatched - aMatched;
            }
            return (b.max_similarity || 0) - (a.max_similarity || 0);
        });

        faces.forEach(face => {
            const card = document.createElement('div');
            card.className = 'face-card';

            const header = document.createElement('div');
            header.className = 'face-card-header';

            const cropContainer = document.createElement('div');
            cropContainer.className = 'face-crop-container';

            const cropImg = document.createElement('img');
            cropImg.src = `/api/face-crop?id=${face.id}`;
            cropImg.alt = face.name ? face.name : 'Unmatched Face';
            cropContainer.appendChild(cropImg);

            const info = document.createElement('div');
            info.className = 'face-info';

            const label = document.createElement('span');
            label.className = 'face-label';
            label.textContent = `Face ID: ${face.id}`;

            const name = document.createElement('span');
            name.className = 'face-name';
            name.textContent = face.name ? face.name : 'Unmatched';

            const status = document.createElement('span');
            if (face.name) {
                status.className = 'face-status resolved';
                status.textContent = 'Resolved';
            } else {
                status.className = 'face-status unknown';
                status.textContent = 'Unmatched';
            }

            const coords = document.createElement('span');
            coords.className = 'face-coords';
            coords.textContent = `Coords: [${face.box.join(', ')}]`;

            info.appendChild(label);
            info.appendChild(name);
            info.appendChild(status);
            info.appendChild(coords);

            header.appendChild(cropContainer);
            header.appendChild(info);
            card.appendChild(header);

            // Edit Panel (initially hidden)
            const editPanel = document.createElement('div');
            editPanel.className = 'face-edit-panel hidden';
            
            // Stop click events inside the edit panel from bubbling to card
            editPanel.addEventListener('click', (e) => {
                e.stopPropagation();
            });

            // Group 1: Suggestions list (top 5 matches)
            const suggestionsGroup = document.createElement('div');
            suggestionsGroup.className = 'face-edit-group';
            
            const suggestionsTitle = document.createElement('span');
            suggestionsTitle.className = 'face-edit-title';
            suggestionsTitle.textContent = 'Suggestions';
            suggestionsGroup.appendChild(suggestionsTitle);

            const suggestionsList = document.createElement('div');
            suggestionsList.className = 'suggestions-list';
            suggestionsGroup.appendChild(suggestionsList);
            editPanel.appendChild(suggestionsGroup);

            // Group 2: Match input selector
            const matchGroup = document.createElement('div');
            matchGroup.className = 'face-edit-group';

            const matchTitle = document.createElement('span');
            matchTitle.className = 'face-edit-title';
            matchTitle.textContent = 'Match to Person';
            matchGroup.appendChild(matchTitle);

            const customGroup = document.createElement('div');
            customGroup.className = 'custom-match-group';

            const input = document.createElement('input');
            input.type = 'text';
            input.className = 'custom-match-input';
            input.placeholder = 'Search or enter name...';
            input.setAttribute('list', 'people-datalist');
            customGroup.appendChild(input);

            const btnMatch = document.createElement('button');
            btnMatch.className = 'btn btn-primary';
            btnMatch.textContent = 'Match';
            customGroup.appendChild(btnMatch);

            const btnNewPersonUnmatched = document.createElement('button');
            btnNewPersonUnmatched.className = 'btn btn-secondary';
            btnNewPersonUnmatched.textContent = '👤+';
            btnNewPersonUnmatched.title = 'Create new person profile';
            btnNewPersonUnmatched.style.padding = '6px 10px';
            btnNewPersonUnmatched.addEventListener('click', (e) => {
                e.stopPropagation();
                selectedFaceIds = [face.id];
                openNewPersonModal(face.id);
            });
            customGroup.appendChild(btnNewPersonUnmatched);

            matchGroup.appendChild(customGroup);

            const valError = document.createElement('div');
            valError.className = 'validation-error hidden';
            matchGroup.appendChild(valError);
            editPanel.appendChild(matchGroup);

            // Group 3: Action buttons (Unmatch / Cancel)
            const actionsBar = document.createElement('div');
            actionsBar.className = 'edit-actions-bar';

            if (face.name) {
                const btnUnmatch = document.createElement('button');
                btnUnmatch.className = 'btn btn-danger';
                btnUnmatch.textContent = 'Unmatch Face';
                btnUnmatch.addEventListener('click', (e) => {
                    e.stopPropagation();
                    postUnmatch(face.id);
                });
                actionsBar.appendChild(btnUnmatch);
            }

            const btnCancel = document.createElement('button');
            btnCancel.className = 'btn btn-secondary';
            btnCancel.textContent = 'Cancel';
            btnCancel.addEventListener('click', (e) => {
                e.stopPropagation();
                deselectAllFaceCards();
            });
            actionsBar.appendChild(btnCancel);
            editPanel.appendChild(actionsBar);

            card.appendChild(editPanel);

            // Click card selection handler
            card.addEventListener('click', (e) => {
                if (card.classList.contains('selected')) {
                    return;
                }
                
                deselectAllFaceCards();
                card.classList.add('selected');
                editPanel.classList.remove('hidden');
                
                input.value = '';
                valError.classList.add('hidden');
                valError.textContent = '';

                // Fetch matches
                suggestionsList.innerHTML = '<span style="font-size:11px;color:var(--text-muted);font-style:italic;">Loading matches...</span>';
                fetch(`/api/face-matches?id=${face.id}`)
                    .then(res => {
                        if (!res.ok) throw new Error('Failed to load matches');
                        return res.json();
                    })
                    .then(matches => {
                        suggestionsList.innerHTML = '';
                        if (!matches || matches.length === 0) {
                            const noSugg = document.createElement('span');
                            noSugg.className = 'no-suggestions';
                            noSugg.textContent = 'No visual matches found';
                            suggestionsList.appendChild(noSugg);
                        } else {
                            matches.forEach(item => {
                                const name = typeof item === 'string' ? item : item.name;
                                const pill = document.createElement('button');
                                pill.className = 'suggestion-pill';
                                pill.textContent = name;
                                pill.title = name;
                                pill.addEventListener('click', (e) => {
                                    e.stopPropagation();
                                    postMatch(face.id, name);
                                });
                                suggestionsList.appendChild(pill);
                            });
                        }
                    })
                    .catch(err => {
                        console.error('Error fetching face matches:', err);
                        suggestionsList.innerHTML = '<span style="font-size:11px;color:#f87171;">Failed to load</span>';
                    });
            });

            // Match button handler
            btnMatch.addEventListener('click', (e) => {
                e.stopPropagation();
                const nameVal = input.value.trim();
                if (!nameVal) {
                    valError.textContent = 'Please enter a name.';
                    valError.classList.remove('hidden');
                    return;
                }
                // Validate against known people
                if (!allKnownPeople.includes(nameVal)) {
                    if (!confirm(`"${nameVal}" is not currently in the database. Do you want to create a new person tag with this name?`)) {
                        return;
                    }
                }
                valError.classList.add('hidden');
                postMatch(face.id, nameVal);
            });

            // Input enter key handler
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    btnMatch.click();
                }
            });

            facesGrid.appendChild(card);
        });

        if (faces.length === 0) {
            const noFaces = document.createElement('div');
            noFaces.style.gridColumn = '1 / -1';
            noFaces.style.textAlign = 'center';
            noFaces.style.padding = '20px';
            noFaces.style.color = 'var(--text-muted)';
            noFaces.textContent = 'No faces detected for this photo in the index.';
            facesGrid.appendChild(noFaces);
        }
    }

    // Deselect all face cards and hide their edit panels
    function deselectAllFaceCards() {
        const cards = facesGrid.getElementsByClassName('face-card');
        Array.from(cards).forEach(card => {
            card.classList.remove('selected');
            const panel = card.querySelector('.face-edit-panel');
            if (panel) {
                panel.classList.add('hidden');
            }
        });
    }

    // POST face match update
    function postMatch(faceId, personName) {
        // Client-side conflict check to prevent second match in same photo
        if (currentPhotoDetails && currentPhotoDetails.faces) {
            const alreadyMatched = currentPhotoDetails.faces.some(f => f.id !== faceId && f.name === personName);
            if (alreadyMatched) {
                alert(`Cannot match: "${personName}" is already tagged on another face in this photo.`);
                return;
            }
        }

        fetch('/api/face/match', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ face_id: faceId, person_name: personName })
        })
        .then(async res => {
            if (!res.ok) {
                let errMsg = 'Match operation failed';
                try {
                    const errData = await res.json();
                    if (errData && errData.error) errMsg = errData.error;
                } catch(e) {}
                throw new Error(errMsg);
            }
            return res.json();
        })
        .then(data => {
            if (data.success) {
                // Refresh photo details
                if (activePhotoPath) {
                    selectPhoto(activePhotoPath);
                }
            } else {
                alert('Failed to match face');
            }
        })
        .catch(err => {
            console.error('Error matching face:', err);
            alert('Error matching face: ' + err.message);
        });
    }

    // POST face unmatch update
    function postUnmatch(faceId) {
        fetch('/api/face/unmatch', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ face_id: faceId })
        })
        .then(res => {
            if (!res.ok) throw new Error('Unmatch operation failed');
            return res.json();
        })
        .then(data => {
            if (data.success) {
                // Refresh photo details
                if (activePhotoPath) {
                    selectPhoto(activePhotoPath);
                }
            } else {
                alert('Failed to unmatch face');
            }
        })
        .catch(err => {
            console.error('Error unmatching face:', err);
            alert('Error unmatching face: ' + err.message);
        });
    }

    // POST automatch all faces for active photo
    function postAutoMatchAll(photoPath) {
        btnAutomatchAll.disabled = true;
        const originalText = btnAutomatchAll.textContent;
        btnAutomatchAll.textContent = 'AutoMatching...';

        fetch('/api/photo/automatch', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ photo_path: photoPath })
        })
        .then(res => {
            if (!res.ok) throw new Error('AutoMatch operation failed');
            return res.json();
        })
        .then(data => {
            if (data.success) {
                selectPhoto(photoPath);
            } else {
                alert('Failed to AutoMatch faces.');
            }
        })
        .catch(err => {
            console.error('Error during AutoMatch:', err);
            alert('Error during AutoMatch: ' + err.message);
        })
        .finally(() => {
            btnAutomatchAll.disabled = false;
            btnAutomatchAll.textContent = originalText;
        });
    }

    // POST automatch all photos in a folder
    function postFolderAutoMatch(folderGroup, btn) {
        btn.disabled = true;
        const originalContent = btn.innerHTML;
        btn.innerHTML = '⏳';
        btn.title = 'AutoMatching...';

        fetch('/api/folder/automatch', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ folder_path: folderGroup.name })
        })
        .then(res => {
            if (!res.ok) throw new Error('Folder AutoMatch operation failed');
            return res.json();
        })
        .then(data => {
            if (data.success) {
                const remaining = data.remaining_counts || {};
                folderGroup.photos.forEach(p => {
                    const matchedDiff = p.unmatched_count - (remaining[p.path] || 0);
                    p.unmatched_count = remaining[p.path] || 0;
                    p.matched_count = (p.matched_count || 0) + matchedDiff;

                    if (p.badgeEl) {
                        p.badgeEl.textContent = `${p.unmatched_count} unmatched`;
                    }
                    if (p.badgeMatchedEl) {
                        p.badgeMatchedEl.textContent = `${p.matched_count} matched`;
                    }
                    if (p.unmatched_count === 0 && p.liEl) {
                        if (modeSelect.value === 'folder-match') {
                            p.liEl.style.display = 'none';
                        }
                    }
                });

                const totalUnmatched = folderGroup.photos.reduce((sum, p) => sum + p.unmatched_count, 0);
                if (folderGroup.countEl) {
                    folderGroup.countEl.textContent = ` (${totalUnmatched})`;
                }

                if (totalUnmatched === 0 && folderGroup.btnEl) {
                    folderGroup.btnEl.style.display = 'none';
                }

                if (activePhotoPath) {
                    selectPhoto(activePhotoPath);
                }
            } else {
                alert('Failed to AutoMatch faces in this folder.');
            }
        })
        .catch(err => {
            console.error('Error during Folder AutoMatch:', err);
            alert('Error during Folder AutoMatch: ' + err.message);
        })
        .finally(() => {
            btn.disabled = false;
            btn.innerHTML = originalContent;
            btn.title = 'AutoMatch all photos in this folder';
        });
    }

    // POST unmatch all faces for active photo
    function postUnmatchAll(photoPath) {
        btnUnmatchAll.disabled = true;
        const originalText = btnUnmatchAll.textContent;
        btnUnmatchAll.textContent = 'Unmatching...';

        fetch('/api/photo/unmatch-all', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ photo_path: photoPath })
        })
        .then(res => {
            if (!res.ok) throw new Error('Unmatch-all operation failed');
            return res.json();
        })
        .then(data => {
            if (data.success) {
                selectPhoto(photoPath);
            } else {
                alert('Failed to unmatch all faces.');
            }
        })
        .catch(err => {
            console.error('Error unmatching all faces:', err);
            alert('Error unmatching all faces: ' + err.message);
        })
        .finally(() => {
            btnUnmatchAll.disabled = false;
            btnUnmatchAll.textContent = originalText;
        });
    }

    // Update empty state icon/text depending on mode
    function updateEmptyState() {
        if (!emptyState) return;
        const icon = emptyState.querySelector('.empty-state-icon');
        const title = emptyState.querySelector('h2');
        const text = emptyState.querySelector('p');
        if (modeSelect.value === 'face-matching' || modeSelect.value === 'unmatched-faces') {
            if (icon) icon.textContent = '👤';
            if (title) title.textContent = 'No Person Selected';
            if (text) text.textContent = 'Select a person from the left sidebar to view and manage potential unmatched face matches.';
        } else {
            if (icon) icon.textContent = '🖼️';
            if (title) title.textContent = 'No Photo Selected';
            if (text) text.textContent = 'Select a photo from the left sidebar to view details and begin tuning face tags.';
        }
    }

    // Fetch people with face counts from backend
    function fetchPeopleWithCounts(isSilent = false, keepTab = false) {
        if (!isSilent) {
            listStats.textContent = 'Loading people...';
            photoList.innerHTML = '';
        }
        
        const mode = modeSelect.value;
        const apiPath = (mode === 'unmatched-faces')
            ? '/api/unmatched-faces/people'
            : '/api/people-with-counts';

        fetch(apiPath, { signal: sidebarAbortController.signal })
            .then(res => {
                if (!res.ok) throw new Error('Network response was not ok');
                return res.json();
            })
            .then(data => {
                allPeopleWithCounts = data;
                renderPeopleList(keepTab);
            })
            .catch(err => {
                if (err.name === 'AbortError') return;
                console.error('Error fetching people with counts:', err);
                listStats.textContent = 'Error loading people';
            });
    }

    // Render unique people with counts in the sidebar
    function renderPeopleList(keepTab = false) {
        const savedScrollTop = photoList.scrollTop;
        photoList.innerHTML = '';
        
        if (allPeopleWithCounts.length === 0) {
            listStats.textContent = 'No people found';
            return;
        }

        listStats.textContent = `Found ${allPeopleWithCounts.length} person(s)`;

        // Apply filter directly in case search has value
        const query = photoSearch.value.toLowerCase();

        allPeopleWithCounts.forEach(person => {
            const li = document.createElement('li');
            li.className = 'photo-item';
            li.personName = person.name;
            
            if (person.name === activePersonName) {
                li.classList.add('active');
            }

            const match = person.name.toLowerCase().includes(query);
            li.style.display = match ? 'block' : 'none';

            const title = document.createElement('div');
            title.className = 'photo-title';
            title.textContent = person.name;

            const badge = document.createElement('span');
            badge.className = 'photo-badge';
            badge.style.color = 'var(--text-secondary)';
            badge.style.backgroundColor = 'var(--bg-tertiary)';
            badge.style.border = '1px solid var(--border-color)';
            if (modeSelect.value === 'unmatched-faces') {
                badge.textContent = `${person.count} photo${person.count !== 1 ? 's' : ''}`;
            } else {
                badge.textContent = `${person.count} face${person.count !== 1 ? 's' : ''}`;
            }

            li.appendChild(title);
            li.appendChild(badge);

            li.addEventListener('click', () => selectPerson(person.name, li));
            photoList.appendChild(li);
        });

        // Restore scroll position
        photoList.scrollTop = savedScrollTop;

        if (activePersonName) {
            const exists = allPeopleWithCounts.some(p => p.name === activePersonName);
            if (exists) {
                if (activePersonName === lastLoadedPersonName) {
                    selectPerson(activePersonName, null, true);
                } else {
                    selectPerson(activePersonName, null, false, keepTab);
                }
            } else {
                activePersonName = null;
                updateURLParams();
            }
        }
    }

    // Select a person to display their face matches
    function selectPerson(name, element, skipFetch = false, keepTab = false) {
        const items = photoList.getElementsByClassName('photo-item');
        Array.from(items).forEach(item => item.classList.remove('active'));
        
        let activeEl = element;
        if (!activeEl) {
            activeEl = Array.from(items).find(item => item.personName === name);
        }
        if (activeEl) {
            activeEl.classList.add('active');
            activeEl.scrollIntoView({ block: 'nearest' });
        }

        activePersonName = name;
        updateURLParams();

        if (skipFetch) return;

        selectedFaceIds = [];
        activePersonFaces = [];
        
        const mode = modeSelect.value;
        const tabMatches = document.getElementById('tab-matches');
        const tabOutliers = document.getElementById('tab-outliers');
        const tabLowConf = document.getElementById('tab-low-conf');
        const matchingTabs = document.getElementById('matching-tabs');
        const matchingStaticTitle = document.getElementById('matching-static-title');

        if (!keepTab) {
            if (mode === 'unmatched-faces') {
                activeTab = 'high';
                if (tabMatches && tabOutliers && tabLowConf) {
                    tabMatches.classList.add('active');
                    tabMatches.textContent = 'High Confidence';
                    tabOutliers.classList.remove('active');
                    tabOutliers.textContent = 'Lower Confidence';
                    tabLowConf.classList.remove('active');
                    tabLowConf.classList.add('hidden');
                }
                if (inputReassignName) {
                    if (name !== 'Unknown Faces') {
                        inputReassignName.value = name;
                    } else {
                        inputReassignName.value = '';
                    }
                }
            } else {
                activeTab = 'matches';
                if (tabMatches && tabOutliers) {
                    tabMatches.classList.add('active');
                    tabMatches.textContent = 'Matches';
                    tabOutliers.classList.remove('active');
                    tabOutliers.textContent = 'Outliers';
                }
                if (tabLowConf) {
                    tabLowConf.classList.add('hidden');
                }
                if (inputReassignName) {
                    inputReassignName.value = '';
                }
            }
        } else {
            // Even if keeping the tab, make sure tab text/visibility is correct for the mode
            if (mode === 'unmatched-faces') {
                if (tabMatches && tabOutliers && tabLowConf) {
                    tabMatches.textContent = 'High Confidence';
                    tabOutliers.textContent = 'Lower Confidence';
                    tabLowConf.classList.add('hidden');
                }
            } else {
                if (tabMatches && tabOutliers) {
                    tabMatches.textContent = 'Matches';
                    tabOutliers.textContent = 'Outliers';
                }
                if (tabLowConf) {
                    tabLowConf.classList.add('hidden');
                }
            }
        }
        
        if (name === 'Unmatched') {
            if (matchingTabs) matchingTabs.classList.add('hidden');
            if (matchingStaticTitle) {
                matchingStaticTitle.classList.remove('hidden');
                matchingStaticTitle.textContent = 'Unmatched Faces';
            }
            if (btnRenamePerson) btnRenamePerson.classList.add('hidden');
        } else if (name === 'Unknown Faces') {
            if (matchingTabs) matchingTabs.classList.remove('hidden');
            if (matchingStaticTitle) matchingStaticTitle.classList.add('hidden');
            if (btnRenamePerson) btnRenamePerson.classList.add('hidden');
        } else {
            if (matchingTabs) matchingTabs.classList.remove('hidden');
            if (matchingStaticTitle) matchingStaticTitle.classList.add('hidden');
            if (btnRenamePerson) btnRenamePerson.classList.remove('hidden');
        }

        const matchingActions = document.querySelector('.matching-actions');
        if (matchingActions) {
            if (name === 'Unknown Faces' && mode === 'unmatched-faces') {
                matchingActions.classList.add('hidden');
            } else {
                matchingActions.classList.remove('hidden');
            }
        }

        updateMatchingSelectionUI();
        clearFaceDetails();

        const toggleGroup = document.getElementById('notperson-toggle-group');
        if (name === 'Unmatched') {
            if (toggleGroup) toggleGroup.classList.remove('hidden');
        } else {
            if (toggleGroup) toggleGroup.classList.add('hidden');
        }

        // Switch views
        emptyState.classList.add('hidden');
        panelContent.classList.add('hidden');
        faceMatchingContent.classList.remove('hidden');

        matchingPersonName.textContent = name;
        matchingPersonCount.textContent = 'Loading faces...';
        
        // Cancel any pending face-crop image requests in the grid
        const activeImgs = matchingFacesGrid.querySelectorAll('img');
        activeImgs.forEach(img => {
            img.src = '';
        });
        matchingFacesGrid.innerHTML = '';

        // Abort any ongoing details fetches
        if (detailsAbortController) {
            detailsAbortController.abort();
        }
        detailsAbortController = new AbortController();

        const apiPath = (mode === 'unmatched-faces')
            ? `/api/unmatched-faces/person-matches?name=${encodeURIComponent(name)}`
            : `/api/person-faces?name=${encodeURIComponent(name)}&limit=-1`;

        fetch(apiPath, { signal: detailsAbortController.signal })
            .then(res => {
                if (!res.ok) throw new Error('Failed to load faces');
                return res.json();
            })
            .then(data => {
                activePersonFaces = data.faces;
                lastLoadedPersonName = name;
                
                // Update tab counts
                updateTabLabels();
                
                renderPersonFaces(activePersonFaces);
            })
            .catch(err => {
                if (err.name === 'AbortError') return;
                console.error('Error loading person faces:', err);
                matchingPersonCount.textContent = 'Error loading faces';
            });
    }

    function updateTabLabels() {
        const tabMatches = document.getElementById('tab-matches');
        const tabOutliers = document.getElementById('tab-outliers');
        const tabLowConf = document.getElementById('tab-low-conf');
        if (!tabMatches || !tabOutliers) return;
        
        let filteredFaces = activePersonFaces;
        
        if (modeSelect.value === 'unmatched-faces') {
            const high = filteredFaces.filter(f => f.similarity !== undefined && f.similarity >= 0.9);
            const lower = filteredFaces.filter(f => f.similarity !== undefined && f.similarity >= 0.8 && f.similarity < 0.9);
            
            tabMatches.textContent = `High Confidence (${high.length})`;
            tabOutliers.textContent = `Lower Confidence (${lower.length})`;
            if (tabLowConf) {
                tabLowConf.classList.add('hidden');
            }
        } else {
            const standards = filteredFaces.filter(f => activePersonName === 'Unmatched' || f.similarity === undefined || f.similarity >= 0.85);
            const outliers = filteredFaces.filter(f => activePersonName !== 'Unmatched' && f.similarity !== undefined && f.similarity < 0.85);
            
            tabMatches.textContent = `Matches (${standards.length})`;
            tabOutliers.textContent = `Outliers (${outliers.length})`;
        }
    }

    // Render face match items in grid
    function renderPersonFaces(faces) {
        // Cancel any pending face-crop image requests in the grid
        const activeImgs = matchingFacesGrid.querySelectorAll('img');
        activeImgs.forEach(img => {
            img.src = '';
        });
        matchingFacesGrid.innerHTML = '';
        selectedFaceIds = [];
        updateMatchingSelectionUI();
        clearFaceDetails();

        let renderedFaces = faces;

        const high = [];
        const lower = [];
        const standards = [];
        const outliers = [];

        renderedFaces.forEach(face => {
            if (modeSelect.value === 'unmatched-faces') {
                if (face.similarity !== undefined && face.similarity >= 0.9) {
                    high.push(face);
                } else if (face.similarity !== undefined && face.similarity >= 0.8) {
                    lower.push(face);
                }
            } else {
                if (activePersonName === 'Unmatched') {
                    standards.push(face);
                } else {
                    if (face.similarity !== undefined && face.similarity < 0.85) {
                        outliers.push(face);
                    } else {
                        standards.push(face);
                    }
                }
            }
        });

        // Set count header and determine faces to render
        let targetFaces = [];
        let displayedCount = 0;
        let unmatchedText = '';
        
        if (modeSelect.value === 'unmatched-faces') {
            if (activeTab === 'high') {
                targetFaces = high;
                unmatchedText = 'high confidence potential';
            } else {
                targetFaces = lower;
                unmatchedText = 'lower confidence potential';
            }
            displayedCount = targetFaces.length;
        } else {
            if (activeTab === 'matches' || activePersonName === 'Unmatched') {
                targetFaces = standards;
                unmatchedText = activePersonName === 'Unmatched' ? 'unmatched' : 'matched';
            } else {
                targetFaces = outliers;
                unmatchedText = 'outlier';
            }
            displayedCount = targetFaces.length;
        }

        matchingPersonCount.textContent = `${displayedCount} ${unmatchedText} face${displayedCount !== 1 ? 's' : ''}`;

        if (displayedCount === 0) {
            const emptyGrid = document.createElement('div');
            emptyGrid.style.textAlign = 'center';
            emptyGrid.style.padding = '40px';
            emptyGrid.style.color = 'var(--text-muted)';
            if (modeSelect.value === 'unmatched-faces') {
                emptyGrid.textContent = `No potential ${activeTab} confidence matches found.`;
            } else {
                if (activePersonName === 'Unmatched') {
                    emptyGrid.textContent = 'No unmatched faces found.';
                } else if (activeTab === 'matches') {
                    emptyGrid.textContent = 'No matching faces found for this person.';
                } else {
                    emptyGrid.textContent = 'No outliers found for this person.';
                }
            }
            matchingFacesGrid.appendChild(emptyGrid);
            return;
        }

        function renderGroupSection(title, groupFaces, returnElement = false) {
            const section = document.createElement('div');
            section.className = 'matching-group-section';

            const header = document.createElement('div');
            header.className = 'matching-group-header';
            header.style.display = 'flex';
            header.style.justifyContent = 'space-between';
            header.style.alignItems = 'center';

            const titleSpan = document.createElement('span');
            if (modeSelect.value === 'unmatched-faces') {
                const numFaces = groupFaces.length;
                const numPhotos = new Set(groupFaces.map(f => f.photo_path)).size;
                titleSpan.textContent = `${title} (${numFaces} face${numFaces !== 1 ? 's' : ''} in ${numPhotos} photo${numPhotos !== 1 ? 's' : ''})`;
            } else {
                titleSpan.textContent = title;
            }
            header.appendChild(titleSpan);

            if (modeSelect.value === 'unmatched-faces') {
                const assignBtn = document.createElement('button');
                assignBtn.className = 'btn btn-primary btn-sm';
                assignBtn.style.padding = '2px 8px';
                assignBtn.style.fontSize = '11px';
                assignBtn.textContent = '👤 Assign Cluster';
                assignBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const executeAssignment = (name) => {
                        if (!allKnownPeople.includes(name)) {
                            if (!confirm(`"${name}" is not currently in the database. Do you want to create a new person tag and assign this cluster to it?`)) {
                                return;
                            }
                        } else {
                            if (!confirm(`Are you sure you want to assign all ${groupFaces.length} faces in this cluster to "${name}"?`)) {
                                return;
                            }
                        }
                        const faceIds = groupFaces.map(f => f.id);
                        postMatchBulk(faceIds, name);
                    };

                    if (activePersonName === 'Unknown Faces') {
                        showAutocompletePopup(groupFaces.length, executeAssignment);
                    } else {
                        const name = inputReassignName.value.trim();
                        if (!name) {
                            alert('Please enter or select a name in the "Assign to name..." input field first.');
                            inputReassignName.focus();
                            return;
                        }
                        executeAssignment(name);
                    }
                });
                header.appendChild(assignBtn);
            }

            section.appendChild(header);

            const grid = document.createElement('div');
            grid.className = 'matching-group-grid';

            groupFaces.forEach(face => {
                const item = document.createElement('div');
                item.className = 'face-match-item';
                item.title = face.photo_path;
                item.setAttribute('data-face-id', face.id);

                if (selectedFaceIds.includes(face.id)) {
                    item.classList.add('selected');
                }

                const img = document.createElement('img');
                img.src = `/api/face-crop?id=${face.id}`;
                img.alt = `Face crop ${face.id}`;
                img.loading = 'lazy';
                item.appendChild(img);

                item.addEventListener('click', () => {
                    const idx = selectedFaceIds.indexOf(face.id);
                    if (idx > -1) {
                        selectedFaceIds.splice(idx, 1);
                        item.classList.remove('selected');
                    } else {
                        selectedFaceIds.push(face.id);
                        item.classList.add('selected');
                    }
                    updateMatchingSelectionUI();

                    if (selectedFaceIds.length > 0) {
                        const lastFaceId = selectedFaceIds[selectedFaceIds.length - 1];
                        showFaceDetails(lastFaceId);
                    } else {
                        clearFaceDetails();
                    }
                });

                grid.appendChild(item);
            });

            section.appendChild(grid);
            if (returnElement) {
                return section;
            } else {
                matchingFacesGrid.appendChild(section);
            }
        }

        if (modeSelect.value === 'unmatched-faces') {
            // 1. Group target faces by cluster name
            const clusterMap = {};
            targetFaces.forEach(face => {
                const clusterName = face.cluster_name || 'Unclustered';
                if (!clusterMap[clusterName]) {
                    clusterMap[clusterName] = [];
                }
                clusterMap[clusterName].push(face);
            });

            // Sort faces within each cluster descending by similarity
            Object.keys(clusterMap).forEach(cName => {
                clusterMap[cName].sort((a, b) => (b.similarity || 0) - (a.similarity || 0));
            });

            // 2. Map each cluster to its most recent year
            const yearClusters = {};
            Object.keys(clusterMap).forEach(cName => {
                const faces = clusterMap[cName];
                let maxYear = 0;
                faces.forEach(f => {
                    const y = parseInt(f.year);
                    if (y && y > maxYear) maxYear = y;
                });
                const yearStr = maxYear > 0 ? maxYear.toString() : 'Unknown';
                if (!yearClusters[yearStr]) {
                    yearClusters[yearStr] = [];
                }
                yearClusters[yearStr].push({ name: cName, faces: faces });
            });

            // 3. Sort years descending
            const sortedYears = Object.keys(yearClusters).sort((a, b) => {
                if (a === 'Unknown') return 1;
                if (b === 'Unknown') return -1;
                return parseInt(b) - parseInt(a);
            });

            // 4. Render year headers and clusters
            sortedYears.forEach(year => {
                const yearContainer = document.createElement('div');
                yearContainer.className = 'year-section-container';
                yearContainer.style.marginBottom = '30px';

                const yearHeader = document.createElement('div');
                yearHeader.className = 'year-section-header';
                yearHeader.style.padding = '8px 4px';
                yearHeader.style.fontSize = '18px';
                yearHeader.style.fontWeight = '700';
                yearHeader.style.borderBottom = '2px solid var(--border-color)';
                yearHeader.style.color = 'var(--text-primary)';
                yearHeader.style.marginBottom = '16px';
                yearHeader.textContent = `Year: ${year}`;
                yearContainer.appendChild(yearHeader);

                // Sort clusters inside this year by size descending
                const clusters = yearClusters[year];
                clusters.sort((a, b) => b.faces.length - a.faces.length);

                clusters.forEach(cluster => {
                    const section = renderGroupSection(cluster.name, cluster.faces, true);
                    if (section) {
                        yearContainer.appendChild(section);
                    }
                });

                matchingFacesGrid.appendChild(yearContainer);
            });
        } else {
            // Group by year
            const groups = {};
            targetFaces.forEach(face => {
                const year = face.year || 'Unknown';
                if (!groups[year]) {
                    groups[year] = [];
                }
                groups[year].push(face);
            });

            // Sort years descending
            const sortedGroupNames = Object.keys(groups).sort((a, b) => {
                if (a === 'Unknown') return 1;
                if (b === 'Unknown') return -1;
                return b - a;
            });

            // Sort faces within each year descending by mtime
            sortedGroupNames.forEach(year => {
                groups[year].sort((a, b) => {
                    const timeA = a.mtime || 0;
                    const timeB = b.mtime || 0;
                    return timeB - timeA;
                });
            });

            // Render sections
            sortedGroupNames.forEach(groupName => {
                renderGroupSection(groupName, groups[groupName]);
            });
        }

        updateMatchingSelectionUI();
    }

    // Populate matching details sidebar
    function showFaceDetails(faceId) {
        if (sidebarDetailAbortController) {
            sidebarDetailAbortController.abort();
        }
        sidebarDetailAbortController = new AbortController();
        const signal = sidebarDetailAbortController.signal;

        const face = activePersonFaces.find(f => f.id === faceId);
        if (!face) return;

        matchingDetailsPlaceholder.classList.add('hidden');
        matchingDetailsContent.classList.remove('hidden');

        matchingDetailId.textContent = face.id;
        const confidence = face.prob !== null && face.prob !== undefined ? (face.prob * 100).toFixed(1) + '%' : 'N/A';
        matchingDetailConfidence.textContent = confidence;
        matchingDetailBox.textContent = `[${face.box.join(', ')}]`;
        matchingDetailPath.textContent = face.photo_path;

        // Reset overlay
        matchingDetailBoundingBoxOverlay.style.left = '0';
        matchingDetailBoundingBoxOverlay.style.top = '0';
        matchingDetailBoundingBoxOverlay.style.width = '0';
        matchingDetailBoundingBoxOverlay.style.height = '0';

        // Prepare image onload
        matchingDetailImg.onload = () => {
            const naturalWidth = matchingDetailImg.naturalWidth;
            const naturalHeight = matchingDetailImg.naturalHeight;
            const renderedWidth = matchingDetailImg.clientWidth;
            const renderedHeight = matchingDetailImg.clientHeight;
            const offsetLeft = matchingDetailImg.offsetLeft;
            const offsetTop = matchingDetailImg.offsetTop;

            if (naturalWidth > 0 && naturalHeight > 0 && face.box && face.box.length === 4) {
                const x1_pct = face.box[0] / naturalWidth;
                const y1_pct = face.box[1] / naturalHeight;
                const x2_pct = face.box[2] / naturalWidth;
                const y2_pct = face.box[3] / naturalHeight;

                const left = offsetLeft + x1_pct * renderedWidth;
                const top = offsetTop + y1_pct * renderedHeight;
                const width = (x2_pct - x1_pct) * renderedWidth;
                const height = (y2_pct - y1_pct) * renderedHeight;

                matchingDetailBoundingBoxOverlay.style.left = `${left}px`;
                matchingDetailBoundingBoxOverlay.style.top = `${top}px`;
                matchingDetailBoundingBoxOverlay.style.width = `${width}px`;
                matchingDetailBoundingBoxOverlay.style.height = `${height}px`;
            }
        };

        // Load original preview
        matchingDetailImg.src = `/api/photo-file?path=${encodeURIComponent(face.photo_path)}&size=512`;

        // Loading placeholders
        matchingDetailTags.innerHTML = '<span style="font-size:11px;color:var(--text-muted);font-style:italic;">Loading tags...</span>';
        matchingDetailPeople.innerHTML = '<span style="font-size:11px;color:var(--text-muted);font-style:italic;">Loading people...</span>';
        matchingDetailDiagnostics.innerHTML = '<span style="font-size:11px;color:var(--text-muted);font-style:italic;">Loading diagnostics...</span>';

        fetch(`/api/photo-details?path=${encodeURIComponent(face.photo_path)}`, { signal })
            .then(res => {
                if (!res.ok) throw new Error('Failed to load photo details');
                return res.json();
            })
            .then(details => {
                matchingDetailTags.innerHTML = '';
                const tagsList = details.tags || [];
                tagsList.forEach(tag => {
                    const pill = document.createElement('span');
                    pill.className = 'tag-pill';
                    pill.textContent = tag;
                    matchingDetailTags.appendChild(pill);
                });
                if (tagsList.length === 0) {
                    matchingDetailTags.innerHTML = '<span style="font-size:11px;color:var(--text-muted);font-style:italic;">No tags</span>';
                }

                matchingDetailPeople.innerHTML = '';
                const peopleList = details.people || [];
                peopleList.forEach(person => {
                    const pill = document.createElement('span');
                    pill.className = 'tag-pill people-tag';
                    pill.textContent = `👤 ${person}`;
                    matchingDetailPeople.appendChild(pill);
                });
                if (peopleList.length === 0) {
                    matchingDetailPeople.innerHTML = '<span style="font-size:11px;color:var(--text-muted);font-style:italic;">No people resolved</span>';
                }
            })
            .catch(err => {
                if (err.name === 'AbortError') return;
                console.error('Error loading photo details:', err);
                matchingDetailTags.innerHTML = '<span style="font-size:11px;color:#f87171;">Failed to load</span>';
                matchingDetailPeople.innerHTML = '<span style="font-size:11px;color:#f87171;">Failed to load</span>';
            });

        fetch(`/api/face-matches?id=${face.id}`, { signal })
            .then(res => {
                if (!res.ok) throw new Error('Failed to load matches');
                return res.json();
            })
            .then(diagnostics => {
                matchingDetailDiagnostics.innerHTML = '';
                if (!diagnostics || diagnostics.length === 0) {
                    matchingDetailDiagnostics.innerHTML = '<div style="font-size:12px;color:var(--text-muted);font-style:italic;padding:4px;">No diagnostic matches found</div>';
                } else {
                    diagnostics.forEach(item => {
                        const itemDiv = document.createElement('div');
                        itemDiv.className = 'diagnostics-item';

                        const nameSpan = document.createElement('span');
                        nameSpan.className = 'diagnostics-name';
                        nameSpan.textContent = item.name;

                        const simSpan = document.createElement('span');
                        simSpan.className = 'diagnostics-similarity';
                        const sim = item.similarity;
                        simSpan.textContent = sim.toFixed(3);
                        
                        if (sim >= 0.8) {
                            simSpan.style.color = '#10b981';
                        } else if (sim >= 0.65) {
                            simSpan.style.color = '#f59e0b';
                        } else {
                            simSpan.style.color = '#ef4444';
                        }

                        itemDiv.appendChild(nameSpan);
                        itemDiv.appendChild(simSpan);
                        matchingDetailDiagnostics.appendChild(itemDiv);
                    });
                }
            })
            .catch(err => {
                if (err.name === 'AbortError') return;
                console.error('Error loading diagnostics:', err);
                matchingDetailDiagnostics.innerHTML = '<div style="font-size:11px;color:#f87171;padding:4px;">Failed to load diagnostics</div>';
            });
    }

    // Clear matching details sidebar
    function clearFaceDetails() {
        if (sidebarDetailAbortController) {
            sidebarDetailAbortController.abort();
        }
        matchingDetailsPlaceholder.classList.remove('hidden');
        matchingDetailsContent.classList.add('hidden');
        matchingDetailImg.onload = null;
        matchingDetailImg.src = '';
    }

    // Update reassign and unmatch buttons text and disabled status
    function updateMatchingSelectionUI() {
        if (!btnUnmatchSelected) return;
        const count = selectedFaceIds.length;
        btnUnmatchSelected.textContent = `⚠️ Unmatch Selected (${count})`;
        btnUnmatchSelected.disabled = count === 0;

        if (btnReassignSelected) {
            btnReassignSelected.disabled = count === 0 || !inputReassignName.value.trim();
        }

        if (btnNewPerson) {
            btnNewPerson.disabled = count !== 1;
        }

        const btnMatchingSelectAll = document.getElementById('btn-matching-select-all');
        if (btnMatchingSelectAll) {
            const faceItems = matchingFacesGrid.querySelectorAll('.face-match-item');
            if (faceItems.length === 0) {
                btnMatchingSelectAll.disabled = true;
                btnMatchingSelectAll.textContent = 'Select All';
            } else {
                btnMatchingSelectAll.disabled = false;
                const faceIds = Array.from(faceItems).map(item => parseInt(item.getAttribute('data-face-id')));
                const allSelected = faceIds.every(id => selectedFaceIds.includes(id));
                btnMatchingSelectAll.textContent = allSelected ? 'Select None' : 'Select All';
            }
        }
    }

    // POST bulk unmatch to backend API
    function postUnmatchBulk(faceIds) {
        btnUnmatchSelected.disabled = true;
        const originalText = btnUnmatchSelected.textContent;
        btnUnmatchSelected.textContent = 'Unmatching...';

        fetch('/api/faces/unmatch-bulk', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ face_ids: faceIds })
        })
        .then(res => {
            if (!res.ok) throw new Error('Bulk unmatch operation failed');
            return res.json();
        })
        .then(data => {
            if (data.success) {
                // In-place DOM removal of selected cards
                faceIds.forEach(id => {
                    const card = matchingFacesGrid.querySelector(`[data-face-id="${id}"]`);
                    if (card) {
                        const grid = card.parentElement;
                        card.remove();
                        if (grid && grid.children.length === 0) {
                            const section = grid.parentElement;
                            if (section && section.classList.contains('matching-group-section')) {
                                section.remove();
                            }
                        }
                    }
                });

                // Update activePersonFaces
                activePersonFaces = activePersonFaces.filter(f => !faceIds.includes(f.id));

                // Update tab counts
                updateTabLabels();

                // Render remaining faces inline immediately
                renderPersonFaces(activePersonFaces);

                // Clear selection state and details
                selectedFaceIds = [];
                updateMatchingSelectionUI();
                clearFaceDetails();

                // Check if the person is going to be removed after unmatching
                const willBeRemoved = (activePersonName !== 'Unmatched' && activePersonFaces.length === 0);
                if (willBeRemoved) {
                    let priorName = null;
                    if (allPeopleWithCounts && allPeopleWithCounts.length > 0) {
                        const idx = allPeopleWithCounts.findIndex(p => p.name === activePersonName);
                        if (idx !== -1) {
                            if (idx > 0) {
                                priorName = allPeopleWithCounts[idx - 1].name;
                            } else if (allPeopleWithCounts.length > 1) {
                                priorName = allPeopleWithCounts[idx + 1].name;
                            }
                        }
                    }

                    if (priorName) {
                        selectPerson(priorName);
                    } else {
                        activePersonName = null;
                        updateURLParams();
                        emptyState.classList.remove('hidden');
                        panelContent.classList.add('hidden');
                        faceMatchingContent.classList.add('hidden');
                    }
                }

                // Fetch updated sidebar counts in background silently
                fetchPeopleWithCounts(true, true);
            } else {
                alert('Failed to unmatch faces');
                updateMatchingSelectionUI();
            }
        })
        .catch(err => {
            console.error('Error during bulk unmatch:', err);
            alert('Error during bulk unmatch: ' + err.message);
            updateMatchingSelectionUI();
        });
    }

    // POST bulk match to backend API
    function postMatchBulk(faceIds, name) {
        btnReassignSelected.disabled = true;
        const originalText = btnReassignSelected.textContent;
        btnReassignSelected.textContent = 'Assigning...';

        fetch('/api/faces/match-bulk', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ face_ids: faceIds, person_name: name })
        })
        .then(async res => {
            if (!res.ok) {
                let errMsg = 'Bulk matching failed';
                try {
                    const errData = await res.json();
                    if (errData && errData.error) errMsg = errData.error;
                } catch(e) {}
                throw new Error(errMsg);
            }
            return res.json();
        })
        .then(data => {
            if (data.success) {
                // In-place DOM removal of selected cards
                faceIds.forEach(id => {
                    const card = matchingFacesGrid.querySelector(`[data-face-id="${id}"]`);
                    if (card) {
                        const grid = card.parentElement;
                        card.remove();
                        if (grid && grid.children.length === 0) {
                            const section = grid.parentElement;
                            if (section && section.classList.contains('matching-group-section')) {
                                section.remove();
                            }
                        }
                    }
                });

                // Update activePersonFaces
                activePersonFaces = activePersonFaces.filter(f => !faceIds.includes(f.id));

                // Update tab counts
                updateTabLabels();

                // Render remaining faces inline immediately
                renderPersonFaces(activePersonFaces);

                // Clear selection and reassign name field
                selectedFaceIds = [];
                if (inputReassignName) {
                    if (modeSelect.value !== 'unmatched-faces') {
                        inputReassignName.value = '';
                    }
                }
                updateMatchingSelectionUI();
                clearFaceDetails();

                // Check if the person is going to be removed after matching
                const willBeRemoved = (activePersonName !== 'Unmatched' && activePersonFaces.length === 0);
                if (willBeRemoved) {
                    let priorName = null;
                    if (allPeopleWithCounts && allPeopleWithCounts.length > 0) {
                        const idx = allPeopleWithCounts.findIndex(p => p.name === activePersonName);
                        if (idx !== -1) {
                            if (idx > 0) {
                                priorName = allPeopleWithCounts[idx - 1].name;
                            } else if (allPeopleWithCounts.length > 1) {
                                priorName = allPeopleWithCounts[idx + 1].name;
                            }
                        }
                    }

                    if (priorName) {
                        selectPerson(priorName);
                    } else {
                        activePersonName = null;
                        updateURLParams();
                        emptyState.classList.remove('hidden');
                        panelContent.classList.add('hidden');
                        faceMatchingContent.classList.add('hidden');
                    }
                }

                // Fetch updated sidebar counts and known people in background silently
                fetchPeopleWithCounts(true, true);
                fetchKnownPeople();
            } else {
                alert('Failed to reassign faces.');
            }
        })
        .catch(err => {
            console.error('Error in bulk reassign:', err);
            alert('Error reassigning faces: ' + err.message);
        })
        .finally(() => {
            btnReassignSelected.disabled = false;
            btnReassignSelected.textContent = originalText;
            updateMatchingSelectionUI();
        });
    }

    // POST rename person to backend API
    function postRenamePerson(oldName, newName) {
        if (btnRenamePerson) btnRenamePerson.disabled = true;
        const originalText = btnRenamePerson.textContent;
        btnRenamePerson.textContent = '✏️ Renaming...';

        fetch('/api/person/rename', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ old_name: oldName, new_name: newName })
        })
        .then(res => {
            if (!res.ok) throw new Error('Rename operation failed');
            return res.json();
        })
        .then(data => {
            if (data.success) {
                // Update selection state to new name
                activePersonName = newName;
                lastLoadedPersonName = null;
                updateURLParams();
                
                // Refresh sidebar and known people lists
                fetchPeopleWithCounts();
                fetchKnownPeople();
            } else {
                alert('Failed to rename person');
            }
        })
        .catch(err => {
            console.error('Error renaming person:', err);
            alert('Error renaming person: ' + err.message);
        })
        .finally(() => {
            if (btnRenamePerson) {
                btnRenamePerson.disabled = false;
                btnRenamePerson.textContent = originalText;
            }
        });
    }

    function showAutocompletePopup(facesCount, onConfirm) {
        // Create modal overlay
        const overlay = document.createElement('div');
        overlay.style.position = 'fixed';
        overlay.style.top = '0';
        overlay.style.left = '0';
        overlay.style.width = '100vw';
        overlay.style.height = '100vh';
        overlay.style.backgroundColor = 'rgba(0, 0, 0, 0.6)';
        overlay.style.backdropFilter = 'blur(4px)';
        overlay.style.display = 'flex';
        overlay.style.justifyContent = 'center';
        overlay.style.alignItems = 'center';
        overlay.style.zIndex = '10000';

        // Create modal card
        const card = document.createElement('div');
        card.style.backgroundColor = 'var(--bg-secondary)';
        card.style.border = '1px solid var(--border-color)';
        card.style.borderRadius = '8px';
        card.style.padding = '24px';
        card.style.width = '350px';
        card.style.boxShadow = '0 10px 25px rgba(0, 0, 0, 0.5)';

        // Title
        const title = document.createElement('h3');
        title.style.margin = '0 0 12px 0';
        title.style.fontSize = '16px';
        title.style.color = 'var(--text-primary)';
        title.textContent = `Assign ${facesCount} faces in cluster to:`;
        card.appendChild(title);

        // Autocomplete Input
        const input = document.createElement('input');
        input.type = 'text';
        input.placeholder = 'Search or enter name...';
        input.setAttribute('list', 'people-datalist');
        input.style.width = '100%';
        input.style.boxSizing = 'border-box';
        input.style.padding = '8px 12px';
        input.style.border = '1px solid var(--border-color)';
        input.style.borderRadius = '4px';
        input.style.backgroundColor = 'var(--bg-tertiary)';
        input.style.color = 'var(--text-primary)';
        input.style.outline = 'none';
        input.style.marginBottom = '20px';
        card.appendChild(input);

        // Button group
        const btnGroup = document.createElement('div');
        btnGroup.style.display = 'flex';
        btnGroup.style.justifyContent = 'flex-end';
        btnGroup.style.gap = '10px';

        const cancelBtn = document.createElement('button');
        cancelBtn.className = 'btn btn-secondary';
        cancelBtn.style.padding = '6px 12px';
        cancelBtn.textContent = 'Cancel';
        cancelBtn.addEventListener('click', () => {
            document.body.removeChild(overlay);
        });

        const confirmBtn = document.createElement('button');
        confirmBtn.className = 'btn btn-primary';
        confirmBtn.style.padding = '6px 12px';
        confirmBtn.textContent = 'Assign';
        
        const submitAction = () => {
            const val = input.value.trim();
            if (!val) {
                alert('Name cannot be empty.');
                input.focus();
                return;
            }
            document.body.removeChild(overlay);
            onConfirm(val);
        };

        confirmBtn.addEventListener('click', submitAction);
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                submitAction();
            }
        });

        btnGroup.appendChild(cancelBtn);
        btnGroup.appendChild(confirmBtn);
        card.appendChild(btnGroup);
        overlay.appendChild(card);
        document.body.appendChild(overlay);

        // Focus input
        setTimeout(() => input.focus(), 50);
    }

    // Keyboard navigation (Up/Down arrow keys) through visible sidebar entries
    document.addEventListener('keydown', (e) => {
        // Only trigger arrow navigation if we are not focused on input fields
        if (document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'TEXTAREA' || document.activeElement.tagName === 'SELECT') {
            return;
        }

        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            const mode = modeSelect.value;
            // Find all photo/person items in the sidebar
            const items = Array.from(photoList.getElementsByClassName('photo-item'));
            // Filter to only those that are currently visible
            const visibleItems = items.filter(item => {
                if (window.getComputedStyle(item).display === 'none') {
                    return false;
                }
                // Check parent folders/years
                let parent = item.parentElement;
                while (parent && parent !== photoList) {
                    if (window.getComputedStyle(parent).display === 'none') {
                        return false;
                    }
                    parent = parent.parentElement;
                }
                return true;
            });

            if (visibleItems.length === 0) return;

            // Find current active index
            const activeIndex = visibleItems.findIndex(item => item.classList.contains('active'));
            
            let nextIndex = -1;
            if (e.key === 'ArrowDown') {
                if (activeIndex === -1) {
                    nextIndex = 0;
                } else if (activeIndex < visibleItems.length - 1) {
                    nextIndex = activeIndex + 1;
                }
            } else if (e.key === 'ArrowUp') {
                if (activeIndex === -1) {
                    nextIndex = visibleItems.length - 1;
                } else if (activeIndex > 0) {
                    nextIndex = activeIndex - 1;
                }
            }

            if (nextIndex !== -1) {
                e.preventDefault(); // Prevent page scrolling
                const nextItem = visibleItems[nextIndex];
                if (nextItem) {
                    if (mode === 'folder-match' && nextItem.photo) {
                        selectPhoto(nextItem.photo.path, nextItem);
                    } else if ((mode === 'face-matching' || mode === 'unmatched-faces') && nextItem.personName) {
                        selectPerson(nextItem.personName, nextItem);
                    }
                    nextItem.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                }
            }
        }
    });
});
