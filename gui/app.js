// app.js

document.addEventListener('DOMContentLoaded', () => {
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
    const facesGrid = document.getElementById('faces-grid');
    const btnRecluster = document.getElementById('btn-recluster');
    const btnRefreshList = document.getElementById('btn-refresh-list');
    const sidebar = document.querySelector('.sidebar');
    const sidebarResizer = document.getElementById('sidebar-resizer');
    const btnAutomatchAll = document.getElementById('btn-automatch-all');
    const btnUnmatchAll = document.getElementById('btn-unmatch-all');

    // Face Matching Mode DOM Elements
    const faceMatchingContent = document.getElementById('face-matching-content');
    const matchingPersonName = document.getElementById('matching-person-name');
    const matchingPersonCount = document.getElementById('matching-person-count');
    const btnUnmatchSelected = document.getElementById('btn-unmatch-selected');
    const matchingFacesGrid = document.getElementById('matching-faces-grid');
    const inputReassignName = document.getElementById('input-reassign-name');
    const btnReassignSelected = document.getElementById('btn-reassign-selected');

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

    // Face Matching Mode State
    let allPeopleWithCounts = [];
    let activePersonName = null;
    let selectedFaceIds = [];
    let activePersonFaces = [];
    let activeTab = 'matches'; // 'matches' or 'outliers'

    // Abort controllers for ongoing fetch requests
    let sidebarAbortController = null;
    let detailsAbortController = null;
    let sidebarDetailAbortController = null;

    // Load initial data
    const urlParams = new URLSearchParams(window.location.search);
    const urlMode = urlParams.get('mode');
    if (urlMode && (urlMode === 'unmatched' || urlMode === 'face-matching')) {
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

    fetchPhotos();
    fetchKnownPeople();

    // Event Listeners
    modeSelect.addEventListener('change', () => {
        updateURLParams();
        fetchPhotos();
    });
    const hideNotPersonToggle = document.getElementById('hide-notperson');
    if (hideNotPersonToggle) {
        hideNotPersonToggle.addEventListener('change', () => {
            fetchPhotos();
            if (activePhotoPath && modeSelect.value === 'unmatched') {
                selectPhoto(activePhotoPath);
            } else if (activePersonName === 'Unmatched' && modeSelect.value === 'face-matching') {
                selectPerson('Unmatched');
            }
        });
    }
    photoSearch.addEventListener('input', filterPhotos);
    if (btnRecluster) {
        btnRecluster.addEventListener('click', triggerReclustering);
    }
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

    const tabMatches = document.getElementById('tab-matches');
    const tabOutliers = document.getElementById('tab-outliers');
    if (tabMatches && tabOutliers) {
        tabMatches.addEventListener('click', () => {
            if (activeTab === 'matches') return;
            activeTab = 'matches';
            tabMatches.classList.add('active');
            tabOutliers.classList.remove('active');
            renderPersonFaces(activePersonFaces);
        });
        tabOutliers.addEventListener('click', () => {
            if (activeTab === 'outliers') return;
            activeTab = 'outliers';
            tabOutliers.classList.add('active');
            tabMatches.classList.remove('active');
            renderPersonFaces(activePersonFaces);
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
        if (modeSelect.value === 'face-matching') {
            if (activePersonName) {
                params.set('person', activePersonName);
            } else {
                params.delete('person');
            }
            params.delete('photo');
        } else {
            if (activePhotoPath) {
                params.set('photo', activePhotoPath);
            } else {
                params.delete('photo');
            }
            params.delete('person');
        }
        window.history.replaceState({}, '', `${window.location.pathname}?${params.toString()}`);
    }

    // Fetch photos list from API
    function fetchPhotos() {
        const mode = modeSelect.value;
        const toggleGroup = document.getElementById('notperson-toggle-group');
        const hideNotPersonCheckbox = document.getElementById('hide-notperson');
        const hideNotPerson = hideNotPersonCheckbox ? hideNotPersonCheckbox.checked : false;
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

        if (mode === 'face-matching') {
            if (activePersonName === 'Unmatched') {
                if (toggleGroup) toggleGroup.classList.remove('hidden');
            } else {
                if (toggleGroup) toggleGroup.classList.add('hidden');
            }
            fetchPeopleWithCounts();
            return;
        }

        if (toggleGroup) toggleGroup.classList.remove('hidden');

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
        
        fetch(`/api/photos?mode=${mode}&hide_notperson=${hideNotPerson}`, { signal: sidebarAbortController.signal })
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
                // Default expand the first entry: first folder of the first year
                const isFolderExpanded = (yearIdx === 0 && folderIdx === 0);
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
                folderGroup.photos.sort((a, b) => b.mtime - a.mtime);
                
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

                    const subtitle = document.createElement('div');
                    subtitle.className = 'photo-subtitle';
                    subtitle.textContent = photo.path;

                    const badge = document.createElement('span');
                    badge.className = 'photo-badge unmatched';
                    badge.textContent = `${photo.unmatched_count} unmatched`;

                    li.appendChild(title);
                    li.appendChild(subtitle);
                    li.appendChild(badge);

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

        if (mode === 'face-matching') {
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

    // Trigger face re-clustering API and refresh data
    function triggerReclustering() {
        if (!btnRecluster) return;
        btnRecluster.disabled = true;
        const originalText = btnRecluster.textContent;
        btnRecluster.textContent = 'Clustering...';

        fetch('/api/faces/recluster', {
            method: 'POST'
        })
        .then(res => {
            if (!res.ok) throw new Error('Re-clustering request failed');
            return res.json();
        })
        .then(data => {
            if (data.success) {
                alert('Face re-clustering has been successfully started in the background. Depending on the size of your library, it will take a moment to complete. You can continue using TagTuner and refresh or select items to view updated identities.');
            } else {
                alert('Failed to start re-clustering.');
            }
        })
        .catch(err => {
            console.error('Error during re-clustering:', err);
            alert('Error during re-clustering: ' + err.message);
        })
        .finally(() => {
            btnRecluster.disabled = false;
            btnRecluster.textContent = originalText;
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
        const hideNotPersonCheckbox = document.getElementById('hide-notperson');
        const hideNotPerson = hideNotPersonCheckbox ? hideNotPersonCheckbox.checked : false;

        // Update inline badge count on the sidebar list item (if present)
        const items = photoList.getElementsByClassName('photo-item');
        const activeEl = Array.from(items).find(item => item.photo && pathsEqual(item.photo.path, details.path));
        if (activeEl) {
            // Count unmatched faces in details.faces (respecting hideNotPerson toggle)
            const unmatchedCount = (details.faces || []).filter(f => {
                if (hideNotPerson) {
                    return !f.name;
                } else {
                    return !f.name || f.name === 'Non Person';
                }
            }).length;
            activeEl.photo.unmatched_count = unmatchedCount;
            const badge = activeEl.querySelector('.photo-badge');
            if (badge) {
                badge.textContent = `${unmatchedCount} unmatched`;
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

        faces.forEach(face => {
            const isNotPerson = face.name === 'Non Person';
            const hideNotPersonCheckbox = document.getElementById('hide-notperson');
            const hideNotPerson = hideNotPersonCheckbox ? hideNotPersonCheckbox.checked : false;

            const card = document.createElement('div');
            card.className = 'face-card';
            if (isNotPerson) {
                card.classList.add('notperson-face');
                if (hideNotPerson) {
                    card.classList.add('hidden');
                }
            }

            const header = document.createElement('div');
            header.className = 'face-card-header';

            const cropContainer = document.createElement('div');
            cropContainer.className = 'face-crop-container';

            const cropImg = document.createElement('img');
            cropImg.src = `/api/face-crop?id=${face.id}`;
            cropImg.alt = (face.name && face.name !== 'Non Person') ? face.name : 'Unmatched Face';
            cropContainer.appendChild(cropImg);

            const info = document.createElement('div');
            info.className = 'face-info';

            const label = document.createElement('span');
            label.className = 'face-label';
            label.textContent = `Face ID: ${face.id}`;

            const name = document.createElement('span');
            name.className = 'face-name';
            name.textContent = (face.name && face.name !== 'Non Person') ? face.name : 'Unmatched';

            const status = document.createElement('span');
            if (face.name && face.name !== 'Non Person') {
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
            matchGroup.appendChild(customGroup);

            const valError = document.createElement('div');
            valError.className = 'validation-error hidden';
            matchGroup.appendChild(valError);
            editPanel.appendChild(matchGroup);

            // Group 3: Action buttons (Unmatch / Cancel)
            const actionsBar = document.createElement('div');
            actionsBar.className = 'edit-actions-bar';

            if (face.name && face.name !== 'Non Person') {
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
        fetch('/api/face/match', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ face_id: faceId, person_name: personName })
        })
        .then(res => {
            if (!res.ok) throw new Error('Match operation failed');
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
                alert(`AutoMatch completed. Matched ${data.matched_count} face(s).`);
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
        if (modeSelect.value === 'face-matching') {
            if (icon) icon.textContent = '👤';
            if (title) title.textContent = 'No Person Selected';
            if (text) text.textContent = 'Select a person from the left sidebar to view and manage their matched faces.';
        } else {
            if (icon) icon.textContent = '🖼️';
            if (title) title.textContent = 'No Photo Selected';
            if (text) text.textContent = 'Select a photo from the left sidebar to view details and begin tuning face tags.';
        }
    }

    // Fetch people with face counts from backend
    function fetchPeopleWithCounts() {
        listStats.textContent = 'Loading people...';
        photoList.innerHTML = '';
        
        fetch('/api/people-with-counts', { signal: sidebarAbortController.signal })
            .then(res => {
                if (!res.ok) throw new Error('Network response was not ok');
                return res.json();
            })
            .then(data => {
                allPeopleWithCounts = data;
                renderPeopleList();
            })
            .catch(err => {
                if (err.name === 'AbortError') return;
                console.error('Error fetching people with counts:', err);
                listStats.textContent = 'Error loading people';
            });
    }

    // Render unique people with counts in the sidebar
    function renderPeopleList() {
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
            badge.textContent = `${person.count} face${person.count !== 1 ? 's' : ''}`;

            li.appendChild(title);
            li.appendChild(badge);

            li.addEventListener('click', () => selectPerson(person.name, li));
            photoList.appendChild(li);
        });

        if (activePersonName) {
            const exists = allPeopleWithCounts.some(p => p.name === activePersonName);
            if (exists) {
                selectPerson(activePersonName);
            } else {
                activePersonName = null;
                updateURLParams();
            }
        }
    }

    // Select a person to display their face matches
    function selectPerson(name, element) {
        const items = photoList.getElementsByClassName('photo-item');
        Array.from(items).forEach(item => item.classList.remove('active'));
        
        let activeEl = element;
        if (!activeEl) {
            activeEl = Array.from(items).find(item => item.personName === name);
        }
        if (activeEl) {
            activeEl.classList.add('active');
        }

        activePersonName = name;
        updateURLParams();
        selectedFaceIds = [];
        activePersonFaces = [];
        activeTab = 'matches';
        
        // Reset tabs UI
        const tabMatches = document.getElementById('tab-matches');
        const tabOutliers = document.getElementById('tab-outliers');
        const matchingTabs = document.getElementById('matching-tabs');
        const matchingStaticTitle = document.getElementById('matching-static-title');
        
        if (tabMatches && tabOutliers) {
            tabMatches.classList.add('active');
            tabMatches.textContent = 'Matches';
            tabOutliers.classList.remove('active');
            tabOutliers.textContent = 'Outliers';
        }
        
        if (name === 'Unmatched') {
            if (matchingTabs) matchingTabs.classList.add('hidden');
            if (matchingStaticTitle) matchingStaticTitle.classList.remove('hidden');
        } else {
            if (matchingTabs) matchingTabs.classList.remove('hidden');
            if (matchingStaticTitle) matchingStaticTitle.classList.add('hidden');
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
        matchingFacesGrid.innerHTML = '';

        // Abort any ongoing details fetches
        if (detailsAbortController) {
            detailsAbortController.abort();
        }
        detailsAbortController = new AbortController();

        fetch(`/api/person-faces?name=${encodeURIComponent(name)}&limit=-1`, { signal: detailsAbortController.signal })
            .then(res => {
                if (!res.ok) throw new Error('Failed to load faces');
                return res.json();
            })
            .then(data => {
                activePersonFaces = data.faces;
                
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
        if (!tabMatches || !tabOutliers) return;
        
        let filteredFaces = activePersonFaces;
        if (activePersonName === 'Unmatched') {
            const hideNotPersonToggle = document.getElementById('hide-notperson');
            const hideNotPerson = hideNotPersonToggle ? hideNotPersonToggle.checked : false;
            if (hideNotPerson) {
                filteredFaces = activePersonFaces.filter(f => f.name !== 'Non Person');
            }
        }
        
        const standards = filteredFaces.filter(f => activePersonName === 'Unmatched' || f.similarity === undefined || f.similarity >= 0.85);
        const outliers = filteredFaces.filter(f => activePersonName !== 'Unmatched' && f.similarity !== undefined && f.similarity < 0.85);
        
        tabMatches.textContent = `Matches (${standards.length})`;
        tabOutliers.textContent = `Outliers (${outliers.length})`;
    }

    // Render face match items in grid
    function renderPersonFaces(faces) {
        matchingFacesGrid.innerHTML = '';
        selectedFaceIds = [];
        updateMatchingSelectionUI();
        clearFaceDetails();

        let renderedFaces = faces;
        if (activePersonName === 'Unmatched') {
            const hideNotPersonCheckbox = document.getElementById('hide-notperson');
            const hideNotPerson = hideNotPersonCheckbox ? hideNotPersonCheckbox.checked : false;
            if (hideNotPerson) {
                renderedFaces = faces.filter(f => f.name !== 'Non Person');
            }
        }

        const standards = [];
        const outliers = [];

        renderedFaces.forEach(face => {
            if (activePersonName === 'Unmatched') {
                standards.push(face);
            } else {
                if (face.similarity !== undefined && face.similarity < 0.85) {
                    outliers.push(face);
                } else {
                    standards.push(face);
                }
            }
        });

        // Set count header
        const displayedCount = activeTab === 'matches' || activePersonName === 'Unmatched' 
            ? standards.length 
            : outliers.length;
        
        const unmatchedText = activePersonName === 'Unmatched' ? 'unmatched' : (activeTab === 'matches' ? 'matched' : 'outlier');
        matchingPersonCount.textContent = `${displayedCount} ${unmatchedText} face${displayedCount !== 1 ? 's' : ''}`;

        if (displayedCount === 0) {
            const emptyGrid = document.createElement('div');
            emptyGrid.style.textAlign = 'center';
            emptyGrid.style.padding = '40px';
            emptyGrid.style.color = 'var(--text-muted)';
            if (activePersonName === 'Unmatched') {
                emptyGrid.textContent = 'No unmatched faces found.';
            } else if (activeTab === 'matches') {
                emptyGrid.textContent = 'No matching faces found for this person.';
            } else {
                emptyGrid.textContent = 'No outliers found for this person.';
            }
            matchingFacesGrid.appendChild(emptyGrid);
            return;
        }

        function renderGroupSection(title, groupFaces) {
            const section = document.createElement('div');
            section.className = 'matching-group-section';

            const header = document.createElement('div');
            header.className = 'matching-group-header';
            header.textContent = title;
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
            matchingFacesGrid.appendChild(section);
        }

        if (activeTab === 'matches' || activePersonName === 'Unmatched') {
            // Group standards by year
            const standardsByYear = {};
            standards.forEach(face => {
                const year = face.year || 'Unknown';
                if (!standardsByYear[year]) {
                    standardsByYear[year] = [];
                }
                standardsByYear[year].push(face);
            });

            // Sort years descending
            const sortedYears = Object.keys(standardsByYear).sort((a, b) => {
                if (a === 'Unknown') return 1;
                if (b === 'Unknown') return -1;
                return b - a;
            });

            // Sort faces within each year descending by mtime
            sortedYears.forEach(year => {
                standardsByYear[year].sort((a, b) => {
                    const timeA = a.mtime || 0;
                    const timeB = b.mtime || 0;
                    return timeB - timeA;
                });
            });

            // Render Year sections
            sortedYears.forEach(year => {
                renderGroupSection(year, standardsByYear[year]);
            });
        } else {
            // Sort outliers descending by mtime
            outliers.sort((a, b) => {
                const timeA = a.mtime || 0;
                const timeB = b.mtime || 0;
                return timeB - timeA;
            });

            // Render Outliers section
            renderGroupSection('Outliers', outliers);
        }
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

                // Get remaining count for the current tab
                let filteredFaces = activePersonFaces;
                if (activePersonName === 'Unmatched') {
                    const hideNotPersonToggle = document.getElementById('hide-notperson');
                    const hideNotPerson = hideNotPersonToggle ? hideNotPersonToggle.checked : false;
                    if (hideNotPerson) {
                        filteredFaces = activePersonFaces.filter(f => f.name !== 'Non Person');
                    }
                }
                const displayedFaces = activeTab === 'matches' || activePersonName === 'Unmatched'
                    ? filteredFaces.filter(f => activePersonName === 'Unmatched' || f.similarity === undefined || f.similarity >= 0.85)
                    : filteredFaces.filter(f => activePersonName !== 'Unmatched' && f.similarity !== undefined && f.similarity < 0.85);

                const finalCount = displayedFaces.length;
                const unmatchedText = activePersonName === 'Unmatched' ? 'unmatched' : (activeTab === 'matches' ? 'matched' : 'outlier');
                matchingPersonCount.textContent = `${finalCount} ${unmatchedText} face${finalCount !== 1 ? 's' : ''}`;

                if (finalCount === 0) {
                    matchingFacesGrid.innerHTML = '';
                    const emptyGrid = document.createElement('div');
                    emptyGrid.style.textAlign = 'center';
                    emptyGrid.style.padding = '40px';
                    emptyGrid.style.color = 'var(--text-muted)';
                    if (activePersonName === 'Unmatched') {
                        emptyGrid.textContent = 'No unmatched faces found.';
                    } else if (activeTab === 'matches') {
                        emptyGrid.textContent = 'No matching faces found for this person.';
                    } else {
                        emptyGrid.textContent = 'No outliers found for this person.';
                    }
                    matchingFacesGrid.appendChild(emptyGrid);
                }

                // Clear selection state and details
                selectedFaceIds = [];
                updateMatchingSelectionUI();
                clearFaceDetails();

                // Fetch updated sidebar counts in background
                fetchPeopleWithCounts();
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
        .then(res => {
            if (!res.ok) throw new Error('Bulk matching failed');
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

                // Get remaining count for the current tab
                let filteredFaces = activePersonFaces;
                if (activePersonName === 'Unmatched') {
                    const hideNotPersonToggle = document.getElementById('hide-notperson');
                    const hideNotPerson = hideNotPersonToggle ? hideNotPersonToggle.checked : false;
                    if (hideNotPerson) {
                        filteredFaces = activePersonFaces.filter(f => f.name !== 'Non Person');
                    }
                }
                const displayedFaces = activeTab === 'matches' || activePersonName === 'Unmatched'
                    ? filteredFaces.filter(f => activePersonName === 'Unmatched' || f.similarity === undefined || f.similarity >= 0.85)
                    : filteredFaces.filter(f => activePersonName !== 'Unmatched' && f.similarity !== undefined && f.similarity < 0.85);

                const finalCount = displayedFaces.length;
                const unmatchedText = activePersonName === 'Unmatched' ? 'unmatched' : (activeTab === 'matches' ? 'matched' : 'outlier');
                matchingPersonCount.textContent = `${finalCount} ${unmatchedText} face${finalCount !== 1 ? 's' : ''}`;

                if (finalCount === 0) {
                    matchingFacesGrid.innerHTML = '';
                    const emptyGrid = document.createElement('div');
                    emptyGrid.style.textAlign = 'center';
                    emptyGrid.style.padding = '40px';
                    emptyGrid.style.color = 'var(--text-muted)';
                    if (activePersonName === 'Unmatched') {
                        emptyGrid.textContent = 'No unmatched faces found.';
                    } else if (activeTab === 'matches') {
                        emptyGrid.textContent = 'No matching faces found for this person.';
                    } else {
                        emptyGrid.textContent = 'No outliers found for this person.';
                    }
                    matchingFacesGrid.appendChild(emptyGrid);
                }

                // Clear selection and reassign name field
                selectedFaceIds = [];
                if (inputReassignName) {
                    inputReassignName.value = '';
                }
                updateMatchingSelectionUI();
                clearFaceDetails();

                // Fetch updated sidebar counts and known people in background
                fetchPeopleWithCounts();
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
});
