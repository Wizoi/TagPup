// The New Person dialog.
import { api } from './common/api.js';
import { nameProblem } from './common/vocabulary.js';
import { state } from './state.js';
import { btnNewPerson } from './elements.js';
import { upper } from './hooks.js';
import { fetchKnownPeople } from './shared.js';
import { clearFaceDetails, updateMatchingSelectionUI } from './selection.js';

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

export function openNewPersonModal(seedFaceId) {
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
    state.modalSelectedFaceIds = [];
    if (modalMatchesLoading) {
        modalMatchesLoading.classList.remove('hidden');
        modalMatchesLoading.textContent = 'Finding similar faces...';
    }
    if (modalMatchesList) {
        modalMatchesList.innerHTML = '';
    }

    api.fetch(`/api/face-matches-unmatched?id=${seedFaceId}`)
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
        noMatches.textContent = 'No unmatched faces alike enough to name without asking.';
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
        img.src = api.image(`/api/face-crop?id=${match.id}`);
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
    const idx = state.modalSelectedFaceIds.indexOf(faceId);
    if (isSelected) {
        if (idx === -1) state.modalSelectedFaceIds.push(faceId);
        cardElement.classList.add('selected');
    } else {
        if (idx > -1) state.modalSelectedFaceIds.splice(idx, 1);
        cardElement.classList.remove('selected');
    }
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

    const problem = nameProblem(val);
    if (problem) {
        modalNameError.textContent = problem;
        modalNameError.classList.remove('hidden');
        btnModalSave.disabled = true;
        return false;
    }

    const isDup = [...state.everyKnownPerson, ...state.allKnownPeople].some(p => p.toLowerCase() === val.toLowerCase());
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

const hideNewPersonModal = () => {
    if (newPersonModal) {
        newPersonModal.classList.add('hidden');
    }
};

// Its listeners, which main.js adds once the page has loaded.
export function wireNewPerson() {
    if (btnNewPerson) {
        btnNewPerson.addEventListener('click', () => {
            if (state.selectedFaceIds.length !== 1) return;
            const seedFaceId = state.selectedFaceIds[0];
            openNewPersonModal(seedFaceId);
        });
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
                if (!state.modalSelectedFaceIds.includes(faceId)) {
                    state.modalSelectedFaceIds.push(faceId);
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
            state.modalSelectedFaceIds = [];
        });
    }

    if (newPersonName) {
        newPersonName.addEventListener('input', () => {
            validateNewPersonName();
        });
    }

    if (btnModalSave) {
        btnModalSave.addEventListener('click', () => {
            const name = newPersonName.value.trim();
            if (!validateNewPersonName()) return;

            const seedFaceId = state.selectedFaceIds[0];
            const allFaceIdsToMatch = [seedFaceId, ...state.modalSelectedFaceIds];

            btnModalSave.disabled = true;
            btnModalSave.textContent = 'Saving...';

            api.fetch('/api/faces/match-bulk', {
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
                    
                    state.selectedFaceIds = [];
                    updateMatchingSelectionUI();
                    clearFaceDetails();
                    
                    upper.fetchPeopleWithCounts();
                    fetchKnownPeople();
                    
                    if (state.activePersonName) {
                        upper.selectPerson(state.activePersonName);
                    }
                    if (state.activePhotoPath) {
                        upper.selectPhoto(state.activePhotoPath);
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

    if (btnCloseModal) {
        btnCloseModal.addEventListener('click', hideNewPersonModal);
    }
    if (btnModalCancel) {
        btnModalCancel.addEventListener('click', hideNewPersonModal);
    }
}
