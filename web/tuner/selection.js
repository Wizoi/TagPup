// Selecting faces in the Identify grid, and the selected face's details.
import { api } from './common/api.js';
import { buildElement, replaceContent } from './common/dom.js';
import { state } from './state.js';
import {
    btnExcludeSelected, btnNewPerson, btnReassignSelected, btnRestoreSelected,
    btnUnmatchSelected, inputReassignName, matchingFacesGrid,
} from './elements.js';
import { BUCKET } from './rules.js';

/** A line the details sidebar shows while it loads, or in place of what it has none of. */
function detailNote(text, style = 'font-size:11px;color:var(--text-muted);font-style:italic;', tag = 'span') {
    return buildElement(tag, { style, text });
}

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

const matchingDetailCrop = document.getElementById('matching-detail-crop');
const matchingDetailCropSize = document.getElementById('matching-detail-crop-size');

const btnMatchingSelectAll = document.getElementById('btn-matching-select-all');

// Populate matching details sidebar
export function showFaceDetails(faceId) {
    if (state.sidebarDetailAbortController) {
        state.sidebarDetailAbortController.abort();
    }
    state.sidebarDetailAbortController = new AbortController();
    const signal = state.sidebarDetailAbortController.signal;

    const face = state.activePersonFaces.find(f => f.id === faceId);
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

    // The crop itself: what the matcher compares, and what you are being asked
    // to recognise. The panel promised it in its title and never showed it.
    if (matchingDetailCrop) {
        matchingDetailCrop.src = api.image(`/api/face-crop?id=${face.id}`);
    }
    if (matchingDetailCropSize && face.box && face.box.length === 4) {
        const w = Math.round(face.box[2] - face.box[0]);
        const h = Math.round(face.box[3] - face.box[1]);
        // A crop far below the detector's working size is worth seeing before
        // you trust a match made from it.
        matchingDetailCropSize.textContent = `${w} x ${h} px`;
        matchingDetailCropSize.classList.toggle('is-small', Math.min(w, h) < 40);
        matchingDetailCropSize.title = Math.min(w, h) < 40
            ? 'Small crops carry less to match on; treat a suggestion from one with care.'
            : '';
    }

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
    matchingDetailImg.src = api.image(`/api/photo-file?path=${encodeURIComponent(face.photo_path)}&size=512`);
    // A cached image can already be complete, in which case the load event never
    // arrives and the box stays zero-sized -- with its 9999px shadow dimming the
    // whole preview behind it. Position it directly when there is nothing to wait for.
    if (matchingDetailImg.complete && matchingDetailImg.naturalWidth > 0) {
        matchingDetailImg.onload();
    }

    // Loading placeholders
    replaceContent(matchingDetailTags, detailNote('Loading tags...'));
    replaceContent(matchingDetailPeople, detailNote('Loading people...'));
    replaceContent(matchingDetailDiagnostics, detailNote('Loading diagnostics...'));

    api.fetch(`/api/photo-details?path=${encodeURIComponent(face.photo_path)}`, { signal })
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
                replaceContent(matchingDetailTags, detailNote('No tags'));
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
                replaceContent(matchingDetailPeople, detailNote('No people resolved'));
            }
        })
        .catch(err => {
            if (err.name === 'AbortError') return;
            console.error('Error loading photo details:', err);
            replaceContent(matchingDetailTags, detailNote('Failed to load', 'font-size:11px;color:#f87171;'));
            replaceContent(matchingDetailPeople, detailNote('Failed to load', 'font-size:11px;color:#f87171;'));
        });

    api.fetch(`/api/face-matches?id=${face.id}`, { signal })
        .then(res => {
            if (!res.ok) throw new Error('Failed to load matches');
            return res.json();
        })
        .then(diagnostics => {
            matchingDetailDiagnostics.innerHTML = '';
            if (!diagnostics || diagnostics.length === 0) {
                replaceContent(matchingDetailDiagnostics, detailNote('No diagnostic matches found',
                    'font-size:12px;color:var(--text-muted);font-style:italic;padding:4px;', 'div'));
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
                    
                    simSpan.style.color = item.band === 'likely' ? '#10b981'
                        : item.band === 'possible' ? '#f59e0b' : '#ef4444';

                    itemDiv.appendChild(nameSpan);
                    itemDiv.appendChild(simSpan);
                    matchingDetailDiagnostics.appendChild(itemDiv);
                });
            }
        })
        .catch(err => {
            if (err.name === 'AbortError') return;
            console.error('Error loading diagnostics:', err);
            replaceContent(matchingDetailDiagnostics, detailNote('Failed to load diagnostics',
                'font-size:11px;color:#f87171;padding:4px;', 'div'));
        });
}

// Clear matching details sidebar
export function clearFaceDetails() {
    if (state.sidebarDetailAbortController) {
        state.sidebarDetailAbortController.abort();
    }
    matchingDetailsPlaceholder.classList.remove('hidden');
    matchingDetailsContent.classList.add('hidden');
    matchingDetailImg.onload = null;
    matchingDetailImg.src = '';
}

/**
 * Select a face the way a file list does.
 *
 * Plain click selects only that face. Ctrl (or Cmd) adds and removes one at a
 * time. Shift takes everything between it and the last face clicked on its own,
 * and Ctrl+Shift adds that run to what is already selected.
 *
 * Every click used to toggle, which is fine for two faces and unusable for two
 * hundred: picking a run meant two hundred clicks, and one stray click in the
 * middle of it silently removed a face from a selection about to be assigned.
 */
export function selectFace(faceId, event) {
    const additive = event && (event.ctrlKey || event.metaKey);
    const ranged = event && event.shiftKey;

    if (ranged && state.selectionAnchorId !== null) {
        const from = state.renderedFaceOrder.indexOf(state.selectionAnchorId);
        const to = state.renderedFaceOrder.indexOf(faceId);
        if (from > -1 && to > -1) {
            const run = state.renderedFaceOrder.slice(Math.min(from, to), Math.max(from, to) + 1);
            // Ctrl+Shift extends; Shift alone replaces, so a second Shift-click
            // re-measures from the same anchor rather than growing forever.
            state.selectedFaceIds = additive
                ? [...new Set([...state.selectedFaceIds, ...run])]
                : run;
            applyFaceSelection(faceId);
            return;
        }
    }

    if (additive) {
        const at = state.selectedFaceIds.indexOf(faceId);
        if (at > -1) state.selectedFaceIds.splice(at, 1);
        else state.selectedFaceIds.push(faceId);
    } else {
        // Plain click starts again, unless it lands on the only thing selected --
        // then it clears, so there is a way back to nothing without the keyboard.
        const onlyThis = state.selectedFaceIds.length === 1 && state.selectedFaceIds[0] === faceId;
        state.selectedFaceIds = onlyThis ? [] : [faceId];
    }
    state.selectionAnchorId = faceId;
    applyFaceSelection(faceId);
}

/** Paint the selection onto the grid, and show the face just acted on. */
function applyFaceSelection(focusFaceId) {
    forgetStaleBadgeName();
    const chosen = new Set(state.selectedFaceIds);
    matchingFacesGrid.querySelectorAll('.face-match-item').forEach(item => {
        const id = Number(item.dataset.faceId);
        item.classList.toggle('selected', chosen.has(id));
    });
    updateMatchingSelectionUI();

    if (state.selectedFaceIds.length === 0) {
        clearFaceDetails();
        return;
    }
    const show = chosen.has(focusFaceId)
        ? focusFaceId
        : state.selectedFaceIds[state.selectedFaceIds.length - 1];
    showFaceDetails(show);
}

/**
 * Forget a badge-filled name once it no longer describes the selection.
 *
 * Clicking a badge puts that person in the box. Select some other faces afterwards
 * and the name stays behind, so Assign Selected would send all of them to whoever
 * the first badge named -- silently, because the box looks the same whether a name
 * was typed or filled in. A name the user typed is theirs and is left alone.
 */
function forgetStaleBadgeName() {
    if (!state.nameFilledForFaceIds || !inputReassignName) return;
    const same = state.nameFilledForFaceIds.length === state.selectedFaceIds.length
        && state.nameFilledForFaceIds.every(id => state.selectedFaceIds.includes(id));
    if (same) return;
    inputReassignName.value = '';
    state.nameFilledForFaceIds = null;
}

export function updateMatchingSelectionUI() {
    if (!btnUnmatchSelected) return;
    const count = state.selectedFaceIds.length;
    btnUnmatchSelected.textContent = `⚠️ Unmatch Selected (${count})`;
    btnUnmatchSelected.disabled = count === 0;

    // A selection whose faces each resemble somebody different cannot be assigned
    // to one typed name, and asking for one is what made this box confusing. The
    // badges already say who each face is, so the button uses them.
    const suggested = state.selectedFaceIds
        .map(id => state.suggestionByFaceId.get(id))
        .filter(Boolean);
    const distinct = new Set(suggested);
    // Not every selected face needs a badge. The Unclustered bucket is mostly
    // faces that resemble nothing, so requiring all of them to carry a suggestion
    // meant this never appeared in the one place it was for: a single unmatched
    // face in the selection disabled the button outright.
    const assignByBadge = count > 1 && distinct.size > 1
        && !inputReassignName.value.trim();

    // When the selection agrees about who it is, say so in the box. Selecting
    // four faces all badged with one name left it empty and the button dead --
    // the easiest case in the bucket, and the only one that needed typing, while
    // a selection of four different people worked by itself.
    //
    // Filled rather than assumed: it is visible, it is editable, and it clears
    // when the selection changes, like any other badge-filled name.
    // "Agrees" means every selected face carries that one badge. One badged face
    // among nine unbadged ones filled the box with its name, and all ten went to
    // that person -- while the by-badge path leaves unbadged faces alone.
    const agreed = count > 0 && distinct.size === 1 && suggested.length === count;
    if (inputReassignName && agreed && !inputReassignName.value.trim()) {
        inputReassignName.value = [...distinct][0];
        state.nameFilledForFaceIds = [...state.selectedFaceIds];
    }
    if (inputReassignName) {
        inputReassignName.placeholder = assignByBadge
            ? `Multiple (${distinct.size} people) — or type one name for all`
            : 'Assign to name...';
        inputReassignName.classList.toggle('is-multiple', assignByBadge);
    }

    if (btnReassignSelected) {
        btnReassignSelected.disabled = count === 0
            || (!inputReassignName.value.trim() && !assignByBadge);
        // The label names who the click will send them to: the box, which is what
        // the click reads. It named the badges' person, so in Rowan's grid three
        // faces badged Imogen read "Assign 3 to Imogen" and went to Rowan.
        const sending = inputReassignName.value.trim();
        btnReassignSelected.textContent = assignByBadge
            ? `Assign ${suggested.length} to their matches`
            : (sending ? `Assign ${count} to ${sending}` : 'Assign Selected');
        btnReassignSelected.title = assignByBadge
            ? 'Each selected face goes to the person its badge names'
            : '';
        btnReassignSelected.dataset.byBadge = assignByBadge ? '1' : '';
    }

    if (btnNewPerson) {
        btnNewPerson.disabled = count !== 1;
    }

    // In the Excluded bucket the useful action is the opposite one, so the two
    // buttons swap rather than sitting side by side offering a contradiction.
    const viewingExcluded = state.activePersonName === BUCKET.EXCLUDED;
    if (btnExcludeSelected) {
        btnExcludeSelected.classList.toggle('hidden', viewingExcluded);
        btnExcludeSelected.textContent = `🚫 Exclude (${count})`;
        btnExcludeSelected.disabled = count === 0;
    }
    if (btnRestoreSelected) {
        btnRestoreSelected.classList.toggle('hidden', !viewingExcluded);
        btnRestoreSelected.textContent = `↩️ Restore (${count})`;
        btnRestoreSelected.disabled = count === 0;
    }
    if (btnUnmatchSelected) {
        btnUnmatchSelected.classList.toggle('hidden', viewingExcluded);
    }

    const btnMatchingSelectAll = document.getElementById('btn-matching-select-all');
    if (btnMatchingSelectAll) {
        const faceItems = matchingFacesGrid.querySelectorAll('.face-match-item');
        if (faceItems.length === 0) {
            btnMatchingSelectAll.disabled = true;
            btnMatchingSelectAll.textContent = 'Select All';
        } else {
            btnMatchingSelectAll.disabled = false;
            const selected = new Set(state.selectedFaceIds);
            const allSelected = Array.from(faceItems)
                .every(item => selected.has(parseInt(item.getAttribute('data-face-id'))));
            btnMatchingSelectAll.textContent = allSelected ? 'Select None' : 'Select All';
        }
    }
}

// Its listeners, which main.js adds once the page has loaded.
export function wireSelection() {
    if (btnMatchingSelectAll) {
        btnMatchingSelectAll.addEventListener('click', () => {
            const faceItems = matchingFacesGrid.querySelectorAll('.face-match-item');
            const faceIds = Array.from(faceItems).map(item => parseInt(item.getAttribute('data-face-id')));
            // Sets, not Array.includes inside a loop over every card: on a grid of
            // 24,000 faces that was hundreds of millions of comparisons per click.
            const selected = new Set(state.selectedFaceIds);
            const onScreen = new Set(faceIds);

            const allSelected = faceIds.every(id => selected.has(id));

            if (allSelected) {
                state.selectedFaceIds = state.selectedFaceIds.filter(id => !onScreen.has(id));
                faceItems.forEach(item => item.classList.remove('selected'));
            } else {
                faceIds.forEach(id => {
                    if (!selected.has(id)) {
                        selected.add(id);
                        state.selectedFaceIds.push(id);
                    }
                });
                faceItems.forEach(item => item.classList.add('selected'));
            }
            
            updateMatchingSelectionUI();
            
            if (state.selectedFaceIds.length > 0) {
                showFaceDetails(state.selectedFaceIds[state.selectedFaceIds.length - 1]);
            } else {
                clearFaceDetails();
            }
        });
    }
}
