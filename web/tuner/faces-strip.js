// A photo's details and the strip of its faces.
import { api } from './common/api.js';
import { buildElement, replaceContent } from './common/dom.js';
import { samePath } from './common/paths.js';
import { nameProblem, samePerson } from './common/vocabulary.js';
import { state } from './state.js';
import { emptyState, modeSelect, panelContent, photoList } from './elements.js';
import { UNKNOWN_YEAR } from './rules.js';
import { personExists, updateURLParams } from './shared.js';
import { openNewPersonModal } from './new-person.js';

const mainImage = document.getElementById('main-image');
const detailPath = document.getElementById('detail-path');
const detailFilename = document.getElementById('detail-filename');
const detailTitle = document.getElementById('detail-title');
const detailTags = document.getElementById('detail-tags');
const detailYear = document.getElementById('detail-year');
const facesGrid = document.getElementById('faces-grid');
const btnAutomatchAll = document.getElementById('btn-automatch-all');
const btnUnmatchAll = document.getElementById('btn-unmatch-all');

// Select a photo and load details
export function selectPhoto(path, element) {
    // Remove active class from all items and add to clicked
    const items = photoList.getElementsByClassName('photo-item');
    Array.from(items).forEach(item => item.classList.remove('active'));
    
    let activeEl = element;
    if (!activeEl) {
        // Find the item matching path in list
        activeEl = Array.from(items).find(item => item.photo && samePath(item.photo.path, path));
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

    state.activePhotoPath = path;
    updateURLParams();
    
    // Abort any ongoing details fetches
    if (state.detailsAbortController) {
        state.detailsAbortController.abort();
        // A grid load cancelled here is no longer on its way, so the sidebar
        // must be free to ask for it again.
        state.loadingPersonName = null;
    }
    state.detailsAbortController = new AbortController();

    // Show loading state or request
    api.fetch(`/api/photo-details?path=${encodeURIComponent(path)}`, { signal: state.detailsAbortController.signal })
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
    state.currentPhotoDetails = details;
    // Update inline badge count on the sidebar list item (if present)
    const items = photoList.getElementsByClassName('photo-item');
    const activeEl = Array.from(items).find(item => item.photo && samePath(item.photo.path, details.path));
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
    mainImage.src = api.image(`/api/photo-file?path=${encodeURIComponent(details.path)}&size=1024`);
    mainImage.alt = details.filename;

    // Metadata details
    detailPath.textContent = details.path;
    detailFilename.textContent = details.filename;
    if (detailYear) {
        detailYear.textContent = details.year || UNKNOWN_YEAR;
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
        // A keyword that names somebody already shown as a person is not repeated.
        if (peopleList.some(p => samePerson(p, tag))) {
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
        cropImg.src = api.image(`/api/face-crop?id=${face.id}`);
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
            state.selectedFaceIds = [face.id];
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
            replaceContent(suggestionsList, buildElement('span', {
                style: 'font-size:11px;color:var(--text-muted);font-style:italic;', text: 'Loading matches...',
            }));
            api.fetch(`/api/face-matches?id=${face.id}`)
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
                    replaceContent(suggestionsList, buildElement('span', {
                        style: 'font-size:11px;color:#f87171;', text: 'Failed to load',
                    }));
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
            if (!personExists(nameVal)) {
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
    const problem = nameProblem(personName);
    if (problem) {
        alert(problem);
        return;
    }
    // Client-side conflict check to prevent second match in same photo
    if (state.currentPhotoDetails && state.currentPhotoDetails.faces) {
        const alreadyMatched = state.currentPhotoDetails.faces.some(f => f.id !== faceId && f.name === personName);
        if (alreadyMatched) {
            alert(`Cannot match: "${personName}" is already tagged on another face in this photo.`);
            return;
        }
    }

    api.fetch('/api/face/match', {
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
            if (state.activePhotoPath) {
                selectPhoto(state.activePhotoPath);
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
    api.fetch('/api/face/unmatch', {
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
            if (state.activePhotoPath) {
                selectPhoto(state.activePhotoPath);
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

    api.fetch('/api/photo/automatch', {
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
export function postFolderAutoMatch(folderGroup, btn) {
    btn.disabled = true;
    // What the button showed, put back as it was: its own nodes, not a copy as markup.
    const originalContent = [...btn.childNodes];
    btn.textContent = '⏳';
    btn.title = 'AutoMatching...';

    api.fetch('/api/folder/automatch', {
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

            if (state.activePhotoPath) {
                selectPhoto(state.activePhotoPath);
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
        replaceContent(btn, ...originalContent);
        btn.title = 'AutoMatch all photos in this folder';
    });
}

// POST unmatch all faces for active photo
function postUnmatchAll(photoPath) {
    btnUnmatchAll.disabled = true;
    const originalText = btnUnmatchAll.textContent;
    btnUnmatchAll.textContent = 'Unmatching...';

    api.fetch('/api/photo/unmatch-all', {
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

// Its listeners, which main.js adds once the page has loaded.
export function wireFacesStrip() {
    if (btnAutomatchAll) {
        btnAutomatchAll.addEventListener('click', () => {
            if (!state.activePhotoPath) return;
            postAutoMatchAll(state.activePhotoPath);
        });
    }
    if (btnUnmatchAll) {
        btnUnmatchAll.addEventListener('click', () => {
            if (!state.activePhotoPath) return;
            if (confirm('Are you sure you want to unmatch all faces in this photo?')) {
                postUnmatchAll(state.activePhotoPath);
            }
        });
    }
}
