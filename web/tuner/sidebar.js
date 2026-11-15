// The mode, the sidebar's lists and the photo list (Folder Matches).
import { api } from './common/api.js';
import { dialogOpen } from './common/dialog.js';
import { baseName, samePath } from './common/paths.js';
import { state } from './state.js';
import {
    emptyState, faceMatchingContent, listStats, modeSelect, panelContent, photoList,
    photoSearch, showMatchedToggle,
} from './elements.js';
import { UNKNOWN_YEAR } from './rules.js';
import { showGroup, updateURLParams } from './shared.js';
import { loadTags, renderTagList } from './tags.js';
import { fetchPeopleWithCounts, selectPerson, showPeopleViewChoice } from './people.js';
import { selectPhoto } from './faces-strip.js';
import { REEXAMINE_TITLE, reexamineFolder } from './reexamine.js';

const btnRefreshList = document.getElementById('btn-refresh-list');
const tagViewContent = document.getElementById('tag-view-content');
const sidebar = document.querySelector('.sidebar');
const sidebarResizer = document.getElementById('sidebar-resizer');
const showMatchedContainer = document.getElementById('show-matched-container');

/**
 * A year's or a folder's header: a button holding its chevron and title (`parts`), so Tab
 * reaches it and Enter or Space opens it; the header's own click opens it too, from its
 * padding. Re-examine is a sibling of the button, never inside it.
 */
function groupHeader(className, parts) {
    const header = document.createElement('div');
    header.className = className;
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'tree-toggle';
    parts.forEach(part => toggle.appendChild(part));
    header.appendChild(toggle);
    return header;
}

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
    showPeopleViewChoice();

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
        const yearChevron = document.createElement('span');
        yearChevron.className = 'chevron-icon';
        const yearTitle = document.createElement('span');
        yearTitle.className = 'year-title';
        yearTitle.textContent = `📅 ${yearGroup.name}`;
        const yearHeader = groupHeader('year-header', [yearChevron, yearTitle]);
        yearLi.appendChild(yearHeader);

        // Year Content (Folders list); the first year opens of itself
        const yearContent = document.createElement('ul');
        yearContent.className = 'year-content';
        yearContent.style.listStyle = 'none';
        showGroup(yearHeader, yearContent, yearIdx === 0);

        // Toggle logic for Year
        yearHeader.addEventListener('click', () => {
            showGroup(yearHeader, yearContent, yearContent.style.display === 'none');
        });
        
        // Sort folders in this year descending
        const sortedFolders = Object.values(yearGroup.folders).sort((a, b) => b.maxMtime - a.maxMtime);
        
        sortedFolders.forEach((folderGroup, folderIdx) => {
            // Create Folder Container
            const folderLi = document.createElement('li');
            folderLi.className = 'folder-group';
            
            // Folder Header
            const folderChevron = document.createElement('span');
            folderChevron.className = 'chevron-icon';

            const folderIcon = document.createElement('span');
            folderIcon.className = 'folder-icon';
            folderIcon.textContent = '📁';

            const folderTitle = document.createElement('span');
            folderTitle.className = 'folder-title';
            folderTitle.textContent = baseName(folderGroup.name) || folderGroup.name;
            folderTitle.title = folderGroup.name;

            const totalUnmatched = folderGroup.photos.reduce((sum, p) => sum + p.unmatched_count, 0);
            const folderCount = document.createElement('span');
            folderCount.className = 'folder-count';
            folderCount.textContent = ` (${totalUnmatched})`;
            folderGroup.countEl = folderCount;
            const folderHeader = groupHeader('folder-header', [folderChevron, folderIcon, folderTitle, folderCount]);

            if (totalUnmatched > 0) {
                const btnFolderAutomatch = document.createElement('button');
                btnFolderAutomatch.className = 'btn-folder-automatch';
                btnFolderAutomatch.textContent = '🤖 Re-examine';
                btnFolderAutomatch.title = REEXAMINE_TITLE;
                btnFolderAutomatch.addEventListener('click', (e) => {
                    e.stopPropagation();
                    reexamineFolder(folderGroup, btnFolderAutomatch);
                });
                folderHeader.appendChild(btnFolderAutomatch);
                folderGroup.btnEl = btnFolderAutomatch;
            }
            
            folderLi.appendChild(folderHeader);
            
            // Folder Content (Photos list), closed until opened
            const folderContent = document.createElement('ul');
            folderContent.className = 'folder-content';
            folderContent.style.listStyle = 'none';
            showGroup(folderHeader, folderContent, false);

            // Toggle logic for Folder
            folderHeader.addEventListener('click', () => {
                showGroup(folderHeader, folderContent, folderContent.style.display === 'none');
            });
            
            // Render photos inside folder
            folderGroup.photos.sort((a, b) => a.filename.localeCompare(b.filename, undefined, { numeric: true, sensitivity: 'base' }));
            
            folderGroup.photos.forEach(photo => {
                const li = document.createElement('li');
                li.className = 'photo-item folder-photo-item';
                if (photo.unmatched_count === 0) li.classList.add('all-matched');
                li.photo = photo;
                // Re-examine updates the photos it named faces in, and their group's count.
                photo.folderGroup = folderGroup;
                
                if (samePath(photo.path, state.activePhotoPath)) {
                    li.classList.add('active');
                    // Ensure parent folders / years are expanded if photo is active
                    showGroup(yearHeader, yearContent, true);
                    showGroup(folderHeader, folderContent, true);
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
                // By the name and by the label shown (`Sam · Pets`).
                const match = item.personName.toLowerCase().includes(query)
                    || String(item.personText || '').toLowerCase().includes(query);
                item.style.display = match ? '' : 'none';
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
            if (query.length > 0 && folderHasVisiblePhotos) {
                showGroup(folderGroup.querySelector('.folder-header'), folderGroup.querySelector('.folder-content'), true);
            }

            if (folderHasVisiblePhotos) {
                yearHasVisiblePhotos = true;
            }
        });

        // Display or hide year group
        yearGroup.style.display = yearHasVisiblePhotos ? 'block' : 'none';

        // If query is not empty, automatically expand year content to show matching folders
        if (query.length > 0 && yearHasVisiblePhotos) {
            showGroup(yearGroup.querySelector('.year-header'), yearGroup.querySelector('.year-content'), true);
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
