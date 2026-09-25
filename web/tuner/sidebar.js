// The mode, the sidebar's lists and the photo list (Folder Matches).
import { api } from './common/api.js';
import { dialogOpen } from './common/dialog.js';
import { baseName, samePath } from './common/paths.js';
import { state } from './state.js';
import {
    emptyState, faceMatchingContent, listStats, modeSelect, panelContent, photoList,
    photoSearch, showMatchedToggle, tagViewContent,
} from './elements.js';
import { UNKNOWN_YEAR } from './rules.js';
import { updateURLParams } from './shared.js';
import { loadTags, renderTagList } from './tags.js';
import { fetchPeopleWithCounts, selectPerson } from './people.js';
import { postFolderAutoMatch, selectPhoto } from './faces-strip.js';

const btnRefreshList = document.getElementById('btn-refresh-list');
const sidebar = document.querySelector('.sidebar');
const sidebarResizer = document.getElementById('sidebar-resizer');
const showMatchedContainer = document.getElementById('show-matched-container');

export function updateMatchedToggleVisibility() {
    const mode = modeSelect.value;
    if (mode === 'folder-match') {
        if (showMatchedContainer) showMatchedContainer.classList.remove('hidden');
    } else {
        if (showMatchedContainer) showMatchedContainer.classList.add('hidden');
    }
}

// Fetch photos list from API
/**
 * Bring the sidebar's counts up to date without touching the panel.
 *
 * fetchPhotos also aborts the panel's request, which is right when the user picks
 * something new and wrong after an exclude, a restore or a finished index: those
 * came back while a grid was loading, cancelled it, and left the panel on
 * "Loading faces..." for good. In the face modes only the counts need refreshing.
 */
export function refreshSidebarQuietly() {
    const mode = modeSelect.value;
    if (mode === 'face-matching' || mode === 'unmatched-faces') {
        fetchPeopleWithCounts(true, true);
        return;
    }
    fetchPhotos();
}

export function fetchPhotos() {
    const mode = modeSelect.value;
    updateEmptyState();

    // Abort any ongoing sidebar fetches
    if (state.sidebarAbortController) {
        state.sidebarAbortController.abort();
    }
    state.sidebarAbortController = new AbortController();

    // Abort any ongoing details fetches
    if (state.detailsAbortController) {
        state.detailsAbortController.abort();
        // A grid load cancelled here is no longer on its way, so the sidebar
        // must be free to ask for it again.
        state.loadingPersonName = null;
    }

    // Review Tags has its own sidebar contents and its own panel; it shares the
    // list element with Review People the way those two share it with each other.
    if (mode === 'tags') {
        faceMatchingContent.classList.add('hidden');
        panelContent.classList.add('hidden');
        emptyState.classList.add('hidden');
        tagViewContent.classList.remove('hidden');
        loadTags();
        return;
    }
    tagViewContent.classList.add('hidden');

    if (mode === 'face-matching' || mode === 'unmatched-faces') {
        fetchPeopleWithCounts();
        return;
    }

    // Hide face matching panel if returning to standard modes
    faceMatchingContent.classList.add('hidden');
    if (!state.activePhotoPath) {
        emptyState.classList.remove('hidden');
        panelContent.classList.add('hidden');
    } else {
        emptyState.classList.add('hidden');
        panelContent.classList.remove('hidden');
    }

    listStats.textContent = 'Loading photos...';
    photoList.innerHTML = '';
    
    const showMatched = showMatchedToggle && showMatchedToggle.checked;
    api.fetch(`/api/photos?mode=${mode}&show_matched=${showMatched}`, { signal: state.sidebarAbortController.signal })
        .then(res => {
            if (!res.ok) throw new Error('Network response was not ok');
            return res.json();
        })
        .then(data => {
            state.allPhotos = data;
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
    
    if (state.allPhotos.length === 0) {
        listStats.textContent = 'No photos found';
        return;
    }

    listStats.textContent = `Found ${state.allPhotos.length} photo(s)`;

    // Group photos by year, then by folder path
    const yearGroups = {};
    state.allPhotos.forEach(photo => {
        const year = photo.year || UNKNOWN_YEAR;
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
        if (a.name === UNKNOWN_YEAR) return 1;
        if (b.name === UNKNOWN_YEAR) return -1;
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
            folderTitle.textContent = baseName(folderGroup.name) || folderGroup.name;
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
                btnFolderAutomatch.textContent = '🤖';
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
                
                if (samePath(photo.path, state.activePhotoPath)) {
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

    if (state.activePhotoPath) {
        const exists = state.allPhotos.some(p => samePath(p.path, state.activePhotoPath));
        if (exists) {
            selectPhoto(state.activePhotoPath);
        } else {
            state.activePhotoPath = null;
            updateURLParams();
        }
    }
}

// Filter photo list based on search bar input
function filterPhotos() {
    const query = photoSearch.value.toLowerCase();
    const mode = modeSelect.value;

    // Tags re-render rather than hide rows: the buckets are computed from the
    // list, so hiding rows in place would leave a bucket counting tags the search
    // has taken off screen.
    if (mode === 'tags') {
        renderTagList();
        return;
    }

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

// Its listeners, which main.js adds once the page has loaded.
export function wireSidebar() {
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

    photoSearch.addEventListener('input', filterPhotos);
    if (btnRefreshList) {
        btnRefreshList.addEventListener('click', fetchPhotos);
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

    // Keyboard navigation (Up/Down arrow keys) through visible sidebar entries
    document.addEventListener('keydown', (e) => {
        // Not the page's while a dialog is open over it (web/common/dialog.js).
        if (dialogOpen()) return;
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
}
