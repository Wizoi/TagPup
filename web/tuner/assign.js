// What is done to a selection of faces: assign, unmatch, exclude, restore, ignore,
// undo, rename.
import { api } from './common/api.js';
import { nameProblem } from './common/vocabulary.js';
import { state } from './state.js';
import {
    btnExcludeSelected, btnReassignSelected, btnRenamePerson, btnRestoreSelected,
    btnUnmatchSelected, emptyState, faceMatchingContent, inputReassignName, modeSelect,
    panelContent,
} from './elements.js';
import { upper } from './hooks.js';
import { fetchKnownPeople, personExists, updateURLParams } from './shared.js';
import { clearFaceDetails, updateMatchingSelectionUI } from './selection.js';
import { putFacesBack, removeFacesFromGrid, updateTabLabels } from './grid-parts.js';

const ignoreConfirmModal = document.getElementById('ignore-confirm-modal');
const ignoreConfirmText = document.getElementById('ignore-confirm-text');
const ignoreConfirmDontAsk = document.getElementById('ignore-confirm-dont-ask');
const btnIgnoreConfirmOk = document.getElementById('btn-ignore-confirm-ok');
const btnIgnoreConfirmCancel = document.getElementById('btn-ignore-confirm-cancel');
const btnIgnoreConfirmClose = document.getElementById('btn-ignore-confirm-close');
const assignUndoBar = document.getElementById('assign-undo-bar');
const assignUndoText = document.getElementById('assign-undo-text');
const btnAssignUndo = document.getElementById('btn-assign-undo');
const btnAssignUndoDismiss = document.getElementById('btn-assign-undo-dismiss');

// Update reassign and unmatch buttons text and disabled status

// ---- Excluding faces ---------------------------------------------------
// A race photograph is mostly strangers, and detection occasionally returns
// something that is not a face. Left in the database they cluster, vote, and drag
// a person's centroid around. Excluding keeps the row and the crop but takes the
// face out of identity work entirely; it is reversible from the Excluded bucket.
//
// The reasons, as tagpup.services.faces.EXCLUSION_REASONS gives them
// (tests/test_rules_have_one_owner.py holds this copy to it): the four offered, the
// first of them the default, and the one given when a cluster is ignored.
const EXCLUDE_REASONS = ['not a person', 'stranger', 'bad crop', 'duplicate'];
export const EXCLUDE_IGNORED_CLUSTER = 'ignored cluster';

/**
 * Ask why, with buttons rather than a text box.
 *
 * The reason takes no part in matching -- it is only ever read back as a label --
 * and these four answers cover every exclusion in this library. Typing one of them
 * hundreds of times is a tax on the fastest action in the app, and free text
 * produced "fuzzy" and "wrong person" beside "bad crop": three ways of recording
 * two things.
 *
 * Resolves to a reason, or null if the question was declined.
 */
function askExcludeReason(count) {
    const modal = document.getElementById('exclude-reason-modal');
    const choices = document.getElementById('exclude-reason-choices');
    const title = document.getElementById('exclude-reason-title');
    if (!modal || !choices) {
        // Nothing to ask with; fall back rather than block the exclusion.
        return Promise.resolve(EXCLUDE_REASONS[0]);
    }

    if (title) {
        title.textContent = count === 1
            ? 'Why exclude this face?'
            : `Why exclude these ${count} faces?`;
    }

    return new Promise(resolve => {
        let settled = false;
        const finish = (value) => {
            if (settled) return;
            settled = true;
            modal.classList.add('hidden');
            resolve(value);
        };

        choices.innerHTML = '';
        EXCLUDE_REASONS.forEach(reason => {
            const button = document.createElement('button');
            button.className = 'exclude-reason-choice';
            button.dataset.reason = reason;
            button.textContent = reason;
            button.addEventListener('click', () => finish(reason));
            choices.appendChild(button);
        });

        const cancel = document.getElementById('btn-exclude-reason-cancel');
        const close = document.getElementById('btn-exclude-reason-close');
        if (cancel) cancel.onclick = () => finish(null);
        if (close) close.onclick = () => finish(null);
        modal.onclick = (e) => { if (e.target === modal) finish(null); };

        modal.classList.remove('hidden');
    });
}

/** Resolves true once the server has excluded the faces, false otherwise. */
export function postExcludeBulk(faceIds, presetReason) {
    if (!faceIds.length) return Promise.resolve(false);
    // A caller that has already asked the question passes the reason in, rather
    // than putting a second modal in front of the same decision.
    if (presetReason === undefined) {
        return askExcludeReason(faceIds.length).then(chosen => {
            if (chosen === null) return false;   // declined
            return postExcludeBulk(faceIds, chosen);
        });
    }
    const reason = presetReason;

    if (btnExcludeSelected) {
        btnExcludeSelected.disabled = true;
        btnExcludeSelected.textContent = 'Excluding...';
    }
    return api.fetch('/api/faces/exclude', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ face_ids: faceIds, reason: (reason || '').trim() || EXCLUDE_REASONS[0] })
    })
    .then(res => {
        if (!res.ok) throw new Error('Exclude failed');
        return res.json();
    })
    .then(() => {
        const excluded = new Set(faceIds);
        state.activePersonFaces = state.activePersonFaces.filter(f => !excluded.has(f.id));
        // In place. Rebuilding the grid to account for a handful of cards leaving
        // meant recreating every card on screen -- ten seconds for Unknown Faces.
        removeFacesFromGrid(faceIds);
        updateTabLabels();
        clearFaceDetails();
        // Go through the mode dispatcher so the sidebar always matches Tune target.
        upper.refreshSidebarQuietly();
        return true;
    })
    .catch(err => {
        console.error(err);
        alert('Error excluding faces: ' + err.message);
        return false;
    })
    .finally(() => updateMatchingSelectionUI());
}

function postRestoreBulk(faceIds, { undo = false } = {}) {
    if (!faceIds.length) return;
    if (btnRestoreSelected) {
        btnRestoreSelected.disabled = true;
        btnRestoreSelected.textContent = 'Restoring...';
    }
    api.fetch('/api/faces/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ face_ids: faceIds })
    })
    .then(res => {
        if (!res.ok) throw new Error('Restore failed');
        return res.json();
    })
    .then(() => {
        if (undo) {
            // Undoing an ignore: the faces come back to the grid they left.
            putFacesBack(faceIds);
        } else {
            // Restoring from Excluded: they leave this grid, in place. This rebuilt
            // every card on screen to account for the few that went.
            const leaving = new Set(faceIds);
            state.activePersonFaces = state.activePersonFaces.filter(f => !leaving.has(f.id));
            removeFacesFromGrid(faceIds);
        }
        updateMatchingSelectionUI();
        clearFaceDetails();
        // Go through the mode dispatcher so the sidebar always matches Tune target.
        upper.refreshSidebarQuietly();
    })
    .catch(err => {
        console.error(err);
        alert('Error restoring faces: ' + err.message);
    })
    .finally(() => updateMatchingSelectionUI());
}

// POST bulk unmatch to backend API
function postUnmatchBulk(faceIds, { undo = false } = {}) {
    btnUnmatchSelected.disabled = true;
    const originalText = btnUnmatchSelected.textContent;
    btnUnmatchSelected.textContent = 'Unmatching...';

    api.fetch('/api/faces/unmatch-bulk', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        // An Undo puts them back unreviewed; an unmatch records "nobody".
        body: JSON.stringify({ face_ids: faceIds, undo })
    })
    .then(res => {
        if (!res.ok) throw new Error('Bulk unmatch operation failed');
        return res.json();
    })
    .then(data => {
        if (data.success && undo) {
            // Undoing an assign: the faces come back to the grid they left.
            putFacesBack(faceIds);
            clearFaceDetails();
            upper.fetchPeopleWithCounts(true, true);
            return;
        }
        if (data.success) {
            // In place: only the cards that left. This removed them and then
            // rebuilt every card on screen as well.
            const leaving = new Set(faceIds);
            state.activePersonFaces = state.activePersonFaces.filter(f => !leaving.has(f.id));
            removeFacesFromGrid(faceIds);

            // Update tab counts
            updateTabLabels();

            updateMatchingSelectionUI();
            clearFaceDetails();

            // Check if the person is going to be removed after unmatching
            const willBeRemoved = (state.activePersonFaces.length === 0);
            if (willBeRemoved) {
                let priorName = null;
                if (state.allPeopleWithCounts && state.allPeopleWithCounts.length > 0) {
                    const idx = state.allPeopleWithCounts.findIndex(p => p.name === state.activePersonName);
                    if (idx !== -1) {
                        if (idx > 0) {
                            priorName = state.allPeopleWithCounts[idx - 1].name;
                        } else if (state.allPeopleWithCounts.length > 1) {
                            priorName = state.allPeopleWithCounts[idx + 1].name;
                        }
                    }
                }

                if (priorName) {
                    upper.selectPerson(priorName);
                } else {
                    state.activePersonName = null;
                    updateURLParams();
                    emptyState.classList.remove('hidden');
                    panelContent.classList.add('hidden');
                    faceMatchingContent.classList.add('hidden');
                }
            }

            // Fetch updated sidebar counts in background silently
            upper.fetchPeopleWithCounts(true, true);
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
/**
 * Report an assignment made without asking first, and offer it back.
 *
 * This is what makes a one-click assign reasonable: the way back is also one
 * click, and it is put in front of you rather than left to be found. It hides
 * itself after a while, because an undo offered forever starts to read as an
 * unfinished job rather than a safety net.
 */
//: Remembered across sessions: this is clicked constantly, and being asked every
//: time is the friction the undo exists to make unnecessary.
const IGNORE_CONFIRM_KEY = 'tagtuner.confirmIgnoreCluster';

function shouldConfirmIgnore() {
    try {
        return localStorage.getItem(IGNORE_CONFIRM_KEY) !== 'never';
    } catch (e) {
        return true;   // no storage: keep asking, which is the safe direction
    }
}

export function askBeforeIgnoring(faceCount, photoCount, proceed) {
    if (!shouldConfirmIgnore() || !ignoreConfirmModal) {
        proceed();
        return;
    }
    state.pendingIgnore = proceed;
    ignoreConfirmText.textContent =
        `${faceCount} face${faceCount !== 1 ? 's' : ''} from `
        + `${photoCount} photo${photoCount !== 1 ? 's' : ''} will stop being offered `
        + `as a match for anyone.`;
    if (ignoreConfirmDontAsk) ignoreConfirmDontAsk.checked = false;
    ignoreConfirmModal.classList.remove('hidden');
}

function closeIgnoreConfirm() {
    if (ignoreConfirmModal) ignoreConfirmModal.classList.add('hidden');
    state.pendingIgnore = null;
}

export function offerAssignUndo(faceIds, name, kind) {
    if (!assignUndoBar) return;
    if (state.assignUndoTimer) clearTimeout(state.assignUndoTimer);

    const n = faceIds.length;
    assignUndoText.textContent = kind === 'ignore'
        ? `Ignored ${n} face${n !== 1 ? 's' : ''}`
        : `Assigned ${n} face${n !== 1 ? 's' : ''} to ${name}`;
    assignUndoBar.classList.remove('hidden');
    assignUndoBar.dataset.faceIds = JSON.stringify(faceIds);
    assignUndoBar.dataset.undoKind = kind || 'assign';

    state.assignUndoTimer = setTimeout(() => {
        assignUndoBar.classList.add('hidden');
        state.assignUndoTimer = null;
    }, 20000);
}

function hideAssignUndo() {
    if (state.assignUndoTimer) {
        clearTimeout(state.assignUndoTimer);
        state.assignUndoTimer = null;
    }
    if (assignUndoBar) assignUndoBar.classList.add('hidden');
}

export function postMatchBulk(faceIds, name) {
    const problem = nameProblem(name);
    if (problem) {
        alert(problem);
        return Promise.resolve(null);
    }
    btnReassignSelected.disabled = true;
    const originalText = btnReassignSelected.textContent;
    btnReassignSelected.textContent = 'Assigning...';

    // Returned so a caller assigning several people can wait for one before
    // starting the next; each success rebuilds the grid underneath them.
    return api.fetch('/api/faces/match-bulk', {
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
            // What the server says it named. It leaves an excluded face alone --
            // naming one is how a face ends up both ruled out and claimed -- so
            // this can be fewer than were sent. A face already under this name
            // is not in it either: it leaves the grid, but Undo must not unname it.
            const assignedIds = Array.isArray(data.matched_ids)
                ? data.matched_ids.map(Number)
                : faceIds;
            const skippedIds = new Set(Array.isArray(data.skipped_excluded)
                ? data.skipped_excluded.map(Number) : []);
            const leavingIds = faceIds.filter(id => !skippedIds.has(id));
            const skipped = skippedIds.size;
            if (skipped) {
                alert(`${skipped} of these face${skipped !== 1 ? 's were' : ' was'} `
                    + `excluded, so ${skipped !== 1 ? 'they were' : 'it was'} not `
                    + `assigned. Restore from the Excluded bucket first to name `
                    + `${skipped !== 1 ? 'them' : 'it'}.`);
            }
            const leaving = new Set(leavingIds);
            state.activePersonFaces = state.activePersonFaces.filter(f => !leaving.has(f.id));

            // Update tab counts
            updateTabLabels();

            // In place: the cards that left are the only ones that changed, and
            // rebuilding the rest cost ten seconds on a grid this size.
            removeFacesFromGrid(leavingIds);

            // Clear the reassign name field
            if (inputReassignName) {
                if (modeSelect.value !== 'unmatched-faces') {
                    inputReassignName.value = '';
                }
            }
            updateMatchingSelectionUI();
            clearFaceDetails();

            // Check if the person is going to be removed after matching
            const willBeRemoved = (state.activePersonFaces.length === 0);
            if (willBeRemoved) {
                let priorName = null;
                if (state.allPeopleWithCounts && state.allPeopleWithCounts.length > 0) {
                    const idx = state.allPeopleWithCounts.findIndex(p => p.name === state.activePersonName);
                    if (idx !== -1) {
                        if (idx > 0) {
                            priorName = state.allPeopleWithCounts[idx - 1].name;
                        } else if (state.allPeopleWithCounts.length > 1) {
                            priorName = state.allPeopleWithCounts[idx + 1].name;
                        }
                    }
                }

                if (priorName) {
                    upper.selectPerson(priorName);
                } else {
                    state.activePersonName = null;
                    updateURLParams();
                    emptyState.classList.remove('hidden');
                    panelContent.classList.add('hidden');
                    faceMatchingContent.classList.add('hidden');
                }
            }

            // Fetch updated sidebar counts and known people in background silently
            upper.fetchPeopleWithCounts(true, true);
            fetchKnownPeople();
            // The ids actually named, so a caller offers Undo for exactly those.
            return assignedIds;
        } else {
            alert('Failed to reassign faces.');
            return null;
        }
    })
    .catch(err => {
        console.error('Error in bulk reassign:', err);
        alert('Error reassigning faces: ' + err.message);
        return null;
    })
    .finally(() => {
        btnReassignSelected.disabled = false;
        btnReassignSelected.textContent = originalText;
        updateMatchingSelectionUI();
    });
}

// POST rename person to backend API
function postRenamePerson(oldName, newName) {
    const problem = nameProblem(newName);
    if (problem) {
        alert(problem);
        return;
    }
    if (btnRenamePerson) btnRenamePerson.disabled = true;
    const originalText = btnRenamePerson.textContent;
    btnRenamePerson.textContent = '✏️ Renaming...';

    api.fetch('/api/person/rename', {
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
            // Some photo files may not have been rewritten; they still name the old spelling.
            if (data.warning) alert('Renamed, but ' + data.warning);
            // Update selection state to new name
            state.activePersonName = newName;
            state.lastLoadedPersonName = null;
            updateURLParams();
            
            // Refresh sidebar and known people lists
            upper.fetchPeopleWithCounts();
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

export function showAutocompletePopup(facesCount, onConfirm) {
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

// Its listeners, which main.js adds once the page has loaded.
export function wireAssign() {
    if (btnUnmatchSelected) {
        btnUnmatchSelected.addEventListener('click', () => {
            if (state.selectedFaceIds.length === 0) return;
            if (confirm(`Are you sure you want to unmatch the ${state.selectedFaceIds.length} selected face(s)?`)) {
                postUnmatchBulk(state.selectedFaceIds);
            }
        });
    }
    if (inputReassignName) {
        inputReassignName.addEventListener('input', () => {
            // Typed over: the name is the user's now, and stops being tied to
            // whichever face's badge put it there.
            state.nameFilledForFaceIds = null;
            updateMatchingSelectionUI();
        });
    }
    if (btnReassignSelected) {
        btnReassignSelected.addEventListener('click', () => {
            const name = inputReassignName.value.trim();
            if (state.selectedFaceIds.length === 0) return;

            // Nothing typed, and the selection disagrees about who it is: send each
            // face to the person its own badge names.
            if (!name && btnReassignSelected.dataset.byBadge) {
                const byName = new Map();
                state.selectedFaceIds.forEach(id => {
                    const person = state.suggestionByFaceId.get(id);
                    if (!person) return;
                    if (!byName.has(person)) byName.set(person, []);
                    byName.get(person).push(id);
                });
                if (byName.size === 0) return;

                const summary = [...byName.entries()]
                    .map(([person, ids]) => `${ids.length} to ${person}`).join(', ');
                const unmatched = state.selectedFaceIds.length
                    - [...byName.values()].reduce((n, ids) => n + ids.length, 0);
                const note = unmatched
                    ? `

${unmatched} selected face(s) resemble nobody yet and are left alone.`
                    : '';
                if (!confirm(`Assign each face to the person it matches?

${summary}${note}`)) {
                    return;
                }

                // One at a time. Each assignment rebuilds the grid and clears the
                // selection on success, so sending them together had them racing --
                // the first to return wiped the state the others were working from.
                const runs = [...byName.entries()];
                const next = (i) => {
                    if (i >= runs.length) return;
                    const [person, ids] = runs[i];
                    Promise.resolve(postMatchBulk(ids, person)).then(() => next(i + 1));
                };
                next(0);
                return;
            }

            if (!name) return;
            if (!personExists(name)) {
                if (!confirm(`"${name}" is not currently in the database. Do you want to create a new person tag and assign it to the selected face(s)?`)) {
                    return;
                }
            } else {
                if (!confirm(`Are you sure you want to assign the ${state.selectedFaceIds.length} selected face(s) to "${name}"?`)) {
                    return;
                }
            }
            postMatchBulk(state.selectedFaceIds, name);
        });
    }

    if (btnRenamePerson) {
        btnRenamePerson.addEventListener('click', () => {
            if (!state.activePersonName) return;
            const newName = prompt(`Rename person "${state.activePersonName}" to:`, state.activePersonName);
            if (newName === null) return;
            const trimmed = newName.trim();
            if (!trimmed) {
                alert('Name cannot be empty.');
                return;
            }
            if (trimmed === state.activePersonName) return;
            
            if (!confirm(`Are you sure you want to rename "${state.activePersonName}" to "${trimmed}"? This will update all of their matched face tags and photo metadata.`)) {
                return;
            }
            
            postRenamePerson(state.activePersonName, trimmed);
        });
    }

    if (btnExcludeSelected) {
        btnExcludeSelected.addEventListener('click', () => {
            if (state.selectedFaceIds.length) postExcludeBulk(state.selectedFaceIds);
        });
    }
    if (btnRestoreSelected) {
        btnRestoreSelected.addEventListener('click', () => {
            if (state.selectedFaceIds.length) postRestoreBulk(state.selectedFaceIds);
        });
    }

    if (btnIgnoreConfirmOk) {
        btnIgnoreConfirmOk.addEventListener('click', () => {
            if (ignoreConfirmDontAsk && ignoreConfirmDontAsk.checked) {
                try {
                    localStorage.setItem(IGNORE_CONFIRM_KEY, 'never');
                } catch (e) { /* the preference just will not stick */ }
            }
            const proceed = state.pendingIgnore;
            closeIgnoreConfirm();
            if (proceed) proceed();
        });
    }
    if (btnIgnoreConfirmCancel) btnIgnoreConfirmCancel.addEventListener('click', closeIgnoreConfirm);
    if (btnIgnoreConfirmClose) btnIgnoreConfirmClose.addEventListener('click', closeIgnoreConfirm);

    if (btnAssignUndo) {
        btnAssignUndo.addEventListener('click', () => {
            let ids = [];
            try {
                ids = JSON.parse(assignUndoBar.dataset.faceIds || '[]');
            } catch (e) { /* nothing to undo */ }
            const kind = assignUndoBar.dataset.undoKind || 'assign';
            hideAssignUndo();
            if (!ids.length) return;
            // Each action has its own way back: an assignment is unmatched, an
            // exclusion is restored. Getting this wrong would quietly do nothing.
            if (kind === 'ignore') postRestoreBulk(ids, { undo: true });
            else postUnmatchBulk(ids, { undo: true });
        });
    }
    if (btnAssignUndoDismiss) {
        btnAssignUndoDismiss.addEventListener('click', hideAssignUndo);
    }
}
