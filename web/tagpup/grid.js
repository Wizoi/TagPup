// TagPup's page: the folder view's thumbnails, their size, selecting them, and the
// right-click menu.
import { api } from './common/api.js';
import { buildElement, replaceContent } from './common/dom.js';
import { baseName } from './common/paths.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import {
    btnSizeLarge, btnSizeMedium, btnSizeSmall, gridContextMenu, inputPhotoTitle, statusDot,
    statusText, thumbnailsGrid
} from './elements.js';
import { saveToLocalStorageCache } from './cache.js';
import {
    formatFriendlyDateSingle, getFolderDateStats, parseExifDateToLocalDate, takenOf
} from './format.js';
import { queuePhotoWrite } from './edits.js';
import { renderFileList, visiblePhotos } from './folder.js';
import { photoFileUrl, selectPhoto } from './photo.js';

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
    const total = state.folderPhotos.length;

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

export function invertThumbnailSelection() {
    const all = state.folderPhotos.map(p => p.path);
    const next = all.filter(p => !state.selectedThumbnails.includes(p));
    state.selectedThumbnails.length = 0;
    next.forEach(p => state.selectedThumbnails.push(p));
    renderThumbnails();
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

// Render Grid Thumbnails
export function renderThumbnails() {
    thumbnailsGrid.innerHTML = '';

    const filtered = visiblePhotos();

    if (filtered.length === 0) {
        replaceContent(thumbnailsGrid, buildElement('div', {
            style: 'grid-column: 1/-1; text-align: center; color: var(--text-muted); padding: 40px;',
            text: 'No photos found matching filter.',
        }));
        return;
    }

    const fragment = document.createDocumentFragment();
    filtered.forEach(photo => {
        const card = document.createElement('div');
        card.className = 'thumbnail-card';
        if (state.selectedThumbnails.includes(photo.path)) {
            card.classList.add('selected');
        }
        card.setAttribute('data-path', photo.path);

        const chkContainer = document.createElement('div');
        chkContainer.className = 'thumbnail-checkbox-container';
        const chk = document.createElement('input');
        chk.type = 'checkbox';
        chk.className = 'thumbnail-checkbox';
        chk.checked = state.selectedThumbnails.includes(photo.path);
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

        const img = document.createElement('img');
        img.src = photoFileUrl(photo, 300);
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
                
                // Queued with every other write to a photo, so the tags it sends
                // are the photo's tags when it runs, not a copy from before.
                queuePhotoWrite(() => api.json('/api/photo/save-metadata', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path: photo.path, title: newTitle, tags: photo.tags })
                })
                .then(data => {
                    if (data.success) {
                        const oldPath = photo.path;
                        photo.title = newTitle;
                        photo.captions = newTitle ? [newTitle] : [];
                        
                        if (data.new_path && data.new_path !== oldPath) {
                            photo.path = data.new_path;
                            photo.filename = baseName(data.new_path);
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
            const isSelected = state.selectedThumbnails.includes(photo.path);
            const nextChecked = !isSelected;
            chk.checked = nextChecked;
            handleCardSelectionClick(photo.path, nextChecked, card, e.shiftKey);
        });

        fragment.appendChild(card);
    });
    thumbnailsGrid.appendChild(fragment);
    upper.updateCameraHighlights();
}

export function handleCardSelectionClick(path, isChecked, cardElement, isShiftKey) {
    if (isShiftKey && state.lastSelectedPath) {
        const cardElements = Array.from(thumbnailsGrid.querySelectorAll('.thumbnail-card'));
        const paths = cardElements.map(el => el.getAttribute('data-path'));
        
        const startIdx = paths.indexOf(state.lastSelectedPath);
        const endIdx = paths.indexOf(path);
        
        if (startIdx !== -1 && endIdx !== -1) {
            const minIdx = Math.min(startIdx, endIdx);
            const maxIdx = Math.max(startIdx, endIdx);
            
            for (let i = minIdx; i <= maxIdx; i++) {
                const currentPath = paths[i];
                const currentCard = cardElements[i];
                const currentChk = currentCard.querySelector('.thumbnail-checkbox');
                
                if (currentChk) currentChk.checked = isChecked;
                
                const idx = state.selectedThumbnails.indexOf(currentPath);
                if (isChecked) {
                    if (idx === -1) state.selectedThumbnails.push(currentPath);
                    currentCard.classList.add('selected');
                } else {
                    if (idx > -1) state.selectedThumbnails.splice(idx, 1);
                    currentCard.classList.remove('selected');
                }
            }
            upper.updateSelectedThumbnailsCount();
            state.lastSelectedPath = path;
            return;
        }
    }
    
    toggleThumbnailSelection(path, isChecked, cardElement);
    state.lastSelectedPath = path;
}

export function toggleThumbnailSelection(path, isChecked, cardElement) {
    const idx = state.selectedThumbnails.indexOf(path);
    if (isChecked) {
        if (idx === -1) state.selectedThumbnails.push(path);
        cardElement.classList.add('selected');
    } else {
        if (idx > -1) state.selectedThumbnails.splice(idx, 1);
        cardElement.classList.remove('selected');
    }
    upper.updateSelectedThumbnailsCount();
}

export function selectAllThumbnails() {
    state.selectedThumbnails = state.folderPhotos.map(p => p.path);
    renderThumbnails();
    upper.updateSelectedThumbnailsCount();
}

export function selectNoneThumbnails() {
    state.selectedThumbnails = [];
    renderThumbnails();
    upper.updateSelectedThumbnailsCount();
}
