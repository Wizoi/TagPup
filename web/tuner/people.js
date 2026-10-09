// Review People: the people list, and choosing a person.
import { api } from './common/api.js';
import { compareTagNames } from './common/vocabulary.js';
import { state } from './state.js';
import {
    btnRenamePerson, emptyState, faceMatchingContent, inputReassignName, listStats,
    matchingFacesGrid, matchingPersonCount, modeSelect, panelContent, peopleSort, photoList,
    photoSearch, tabLowConf, tabMatches, tabOutliers,
} from './elements.js';
import { BUCKET, isBucket } from './rules.js';
import { updateURLParams } from './shared.js';
import { renderTagList } from './tags.js';
import { clearFaceDetails, updateMatchingSelectionUI } from './selection.js';
import { updateTabLabels } from './grid-parts.js';
import { renderPersonFaces } from './grid.js';

const matchingPersonName = document.getElementById('matching-person-name');
const peopleView = document.getElementById('people-view');
const peopleViewName = document.getElementById('people-view-name');
const peopleViewFace = document.getElementById('people-view-face');

// Fetch people with face counts from backend
export function fetchPeopleWithCounts(isSilent = false, keepTab = false) {
    if (!isSilent) {
        listStats.textContent = 'Loading people...';
        photoList.innerHTML = '';
    }
    
    const mode = modeSelect.value;
    const apiPath = (mode === 'unmatched-faces')
        ? '/api/unmatched-faces/people'
        : '/api/people-with-counts';

    const signal = state.sidebarAbortController.signal;
    // The faces are asked for only when the list is drawn by them, and a failed ask draws
    // placeholders: the names and counts are what the list is for.
    const faces = showingFaces()
        ? api.fetch('/api/people-faces', { signal })
            .then(res => (res.ok ? res.json() : {}))
            // An abort takes the people request with it (one signal), which is handled there.
            .catch(() => ({}))
        : Promise.resolve(null);

    api.fetch(apiPath, { signal })
        .then(res => {
            if (!res.ok) throw new Error('Network response was not ok');
            return res.json();
        })
        .then(data => faces.then(personFaces => {
            state.allPeopleWithCounts = data;
            if (personFaces) state.personFaces = personFaces;
            renderPeopleList(keepTab);
        }))
        .catch(err => {
            if (err.name === 'AbortError') return;
            console.error('Error fetching people with counts:', err);
            listStats.textContent = 'Error loading people';
        });
}

//: Remembered, because it is a standing preference about how you read the list
//: rather than a per-visit decision.
const PEOPLE_SORT_KEY = 'tagtuner.peopleSort';
const PEOPLE_VIEW_KEY = 'tagtuner.peopleView';

// By face is Review People's: Identify Faces' names are candidates, and its buckets have no face.
function showingFaces() {
    return state.peopleView === 'face' && modeSelect.value === 'face-matching';
}

// The By name / By face choice is offered in Review People alone.
export function showPeopleViewChoice() {
    if (!peopleView) return;
    peopleView.classList.toggle('hidden', modeSelect.value !== 'face-matching');
    const byFace = state.peopleView === 'face';
    peopleViewName.classList.toggle('active', !byFace);
    peopleViewFace.classList.toggle('active', byFace);
    peopleViewName.setAttribute('aria-pressed', String(!byFace));
    peopleViewFace.setAttribute('aria-pressed', String(byFace));
}

function choosePeopleView(view) {
    if (state.peopleView === view) return;
    state.peopleView = view;
    try {
        localStorage.setItem(PEOPLE_VIEW_KEY, view);
    } catch (e) { /* the preference just will not stick */ }
    showPeopleViewChoice();
    // The faces come with the list; keep the tab, as re-ordering does.
    fetchPeopleWithCounts(true, true);
}

// One person's row: a name and its count on one line, or a face with the name under it.
function personFaceCard(person, li) {
    const crop = document.createElement('div');
    crop.className = 'person-face-crop';
    // A person with a node is asked for by their id (two people called alike are two); a name only when the server keyed it.
    const faceId = (person.person_id != null && state.personFaces[`id:${person.person_id}`]) || state.personFaces[person.name];
    if (faceId) {
        const img = document.createElement('img');
        img.alt = '';
        // The list can hold hundreds of people: the browser fetches a crop as it scrolls near.
        img.setAttribute('loading', 'lazy');
        img.setAttribute('decoding', 'async');
        img.src = api.image(`/api/face-crop?id=${faceId}`);
        crop.appendChild(img);
    } else {
        const blank = document.createElement('span');
        blank.className = 'person-face-blank';
        blank.textContent = (person.name || '?').trim().charAt(0).toUpperCase();
        crop.appendChild(blank);
    }
    li.appendChild(crop);
    return crop;
}

function currentPeopleSort() {
    if (peopleSort && peopleSort.value) return peopleSort.value;
    try {
        return localStorage.getItem(PEOPLE_SORT_KEY) || 'count';
    } catch (e) {
        return 'count';
    }
}

/**
 * The people list in the chosen order.
 *
 * The buckets stay pinned to the top whichever order is chosen: Unknown Faces,
 * Ungrouped and Excluded are not people, and sorting them into the alphabet
 * would bury them somewhere between two names.
 */
const PINNED_BUCKETS = [BUCKET.UNKNOWN, BUCKET.UNGROUPED, BUCKET.EXCLUDED];
function sortedPeople() {
    const people = (state.allPeopleWithCounts || []).slice();
    const rank = (p) => {
        const at = PINNED_BUCKETS.indexOf(p.name);
        return at === -1 ? PINNED_BUCKETS.length : at;
    };
    const byName = currentPeopleSort() === 'name';
    people.sort((a, b) => {
        const pinned = rank(a) - rank(b);
        if (pinned !== 0) return pinned;
        if (byName) return compareTagNames(a.name, b.name);
        // Most first; the alphabet breaks a tie, so the order is not the server's.
        return ((b.count || 0) - (a.count || 0)) || compareTagNames(a.name, b.name);
    });
    return people;
}

// Render unique people with counts in the sidebar
function renderPeopleList(keepTab = false) {
    const savedScrollTop = photoList.scrollTop;
    photoList.innerHTML = '';
    const byFace = showingFaces();
    photoList.classList.toggle('by-face', byFace);
    
    if (state.allPeopleWithCounts.length === 0) {
        state.shownPeople = [];
        listStats.textContent = 'No people found';
        return;
    }

    listStats.textContent = `Found ${state.allPeopleWithCounts.length} person(s)`;

    // Apply filter directly in case search has value
    const query = photoSearch.value.toLowerCase();

    // What the list shows, in the order it shows it: the person to move to when one is emptied is the
    // one beside them here.
    state.shownPeople = sortedPeople();
    state.shownPeople.forEach(person => {
        const li = document.createElement('li');
        li.className = byFace ? 'photo-item person-card' : 'photo-item person-row';
        li.personName = person.name;
        li.title = person.name;
        
        if (person.name === state.activePersonName) {
            li.classList.add('active');
        }

        const match = person.name.toLowerCase().includes(query);
        li.style.display = match ? '' : 'none';

        const crop = byFace ? personFaceCard(person, li) : null;

        const title = document.createElement('div');
        title.className = 'photo-title';
        title.textContent = person.name;

        const badge = document.createElement('span');
        badge.className = 'photo-badge';
        badge.style.color = 'var(--text-secondary)';
        badge.style.backgroundColor = 'var(--bg-tertiary)';
        badge.style.border = '1px solid var(--border-color)';
        if (modeSelect.value === 'unmatched-faces') {
            // The catch-all buckets count faces, because a face is the unit of
            // work in them -- one photo of a start line holds thirty. Saying
            // "photos" there put a number in the sidebar that read as a
            // contradiction of the one in the panel beside it.
            const unit = person.unit === 'face' ? 'face' : 'photo';
            const n = person.count.toLocaleString();
            badge.textContent = `${n} ${unit}${person.count !== 1 ? 's' : ''}`;
            if (person.unit === 'face' && person.photos) {
                badge.title = `${n} faces across ${person.photos.toLocaleString()} photos`;
            }
        } else {
            badge.textContent = `${person.count} face${person.count !== 1 ? 's' : ''}`;
        }

        if (byFace) {
            // The count on the crop's corner, the name under it; "n faces" is too long for it.
            badge.textContent = person.count.toLocaleString();
            badge.title = `${person.count.toLocaleString()} face${person.count !== 1 ? 's' : ''}`;
            crop.appendChild(badge);
            li.appendChild(title);
        } else {
            li.appendChild(title);
            li.appendChild(badge);
        }

        li.addEventListener('click', () => selectPerson(person.name, li));
        photoList.appendChild(li);
    });

    // Restore scroll position
    photoList.scrollTop = savedScrollTop;

    if (state.activePersonName) {
        const exists = state.allPeopleWithCounts.some(p => p.name === state.activePersonName);
        if (exists) {
            if (state.activePersonName === state.lastLoadedPersonName
                    || state.activePersonName === state.loadingPersonName) {
                selectPerson(state.activePersonName, null, true);
            } else {
                selectPerson(state.activePersonName, null, false, keepTab);
            }
        } else {
            state.activePersonName = null;
            updateURLParams();
        }
    }
}

// Select a person to display their face matches
export function selectPerson(name, element, skipFetch = false, keepTab = false) {
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

    state.activePersonName = name;
    updateURLParams();

    if (skipFetch) return;

    state.selectedFaceIds = [];
    state.activePersonFaces = [];
    
    const mode = modeSelect.value;
    const tabMatches = document.getElementById('tab-matches');
    const tabOutliers = document.getElementById('tab-outliers');
    const tabLowConf = document.getElementById('tab-low-conf');
    const matchingTabs = document.getElementById('matching-tabs');
    const matchingStaticTitle = document.getElementById('matching-static-title');

    if (!keepTab) {
        if (mode === 'unmatched-faces') {
            state.activeTab = 'high';
            if (tabMatches && tabOutliers && tabLowConf) {
                tabMatches.classList.add('active');
                tabMatches.textContent = 'Likely';
                tabOutliers.classList.remove('active');
                tabOutliers.textContent = 'Possible';
                tabLowConf.classList.remove('active');
                tabLowConf.classList.add('hidden');
            }
            if (inputReassignName) {
                if (!isBucket(name)) {
                    inputReassignName.value = name;
                } else {
                    inputReassignName.value = '';
                }
            }
        } else {
            state.activeTab = 'matches';
            if (tabMatches && tabOutliers) {
                tabMatches.classList.add('active');
                tabMatches.textContent = 'Confirmed';
                tabOutliers.classList.remove('active');
                tabOutliers.textContent = 'Needs Review';
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
                tabMatches.textContent = 'Likely';
                tabOutliers.textContent = 'Possible';
                tabLowConf.classList.add('hidden');
            }
        } else {
            if (tabMatches && tabOutliers) {
                tabMatches.textContent = 'Confirmed';
                tabOutliers.textContent = 'Needs Review';
            }
            if (tabLowConf) {
                tabLowConf.classList.add('hidden');
            }
        }
    }
    
    if (isBucket(name)) {
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
        // Shown for every view, Unknown Faces included. It used to be hidden
        // there on the assumption that each cluster's own Assign Cluster button
        // was enough -- but the Unclustered section has no such button, and
        // cannot have one, because its faces have nothing in common. Selecting
        // the ones you recognise and assigning them is the only way through it.
        matchingActions.classList.remove('hidden');
    }

    updateMatchingSelectionUI();
    clearFaceDetails();


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
    state.renderedFaceOrder = [];
    // Suggestions belong to the grid they were drawn with. Kept across grids, a
    // badge no longer on any card still filled the name box and split assigns.
    state.suggestionByFaceId = new Map();
    state.renderedFacesById = new Map();
    state.removedFromGrid = new Map();
    state.nameFilledForFaceIds = null;

    // Abort any ongoing details fetches
    if (state.detailsAbortController) {
        state.detailsAbortController.abort();
        // A grid load cancelled here is no longer on its way, so the sidebar
        // must be free to ask for it again.
        state.loadingPersonName = null;
    }
    state.detailsAbortController = new AbortController();

    // The Excluded bucket is not a person and has its own listing.
    const apiPath = (name === BUCKET.EXCLUDED)
        ? '/api/faces/excluded'
        : (mode === 'unmatched-faces')
            ? `/api/unmatched-faces/person-matches?name=${encodeURIComponent(name)}`
            : `/api/person-faces?name=${encodeURIComponent(name)}&limit=-1`;

    // Only the Identify Faces grid does work worth reporting on.
    if (mode === 'unmatched-faces' && name !== BUCKET.EXCLUDED) {
        startGridBuildProgress(name);
    }

    state.loadingPersonName = name;
    api.fetch(apiPath, { signal: state.detailsAbortController.signal })
        .then(res => {
            if (!res.ok) throw new Error('Failed to load faces');
            return res.json();
        })
        .then(data => {
            stopGridBuildProgress();
            if (state.loadingPersonName === name) state.loadingPersonName = null;
            state.activePersonFaces = data.faces;
            state.lastLoadedPersonName = name;
            // A capped list presented as a total makes the remainder look lost.
            state.activeFacesTotal = (data.unclustered_total !== undefined
                    && data.unclustered_total > (data.unclustered_shown || 0))
                ? data.unclustered_total
                : null;
            
            // Update tab counts
            updateTabLabels();
            
            renderPersonFaces(state.activePersonFaces);
        })
        .catch(err => {
            stopGridBuildProgress();
            // Only if it is still this load: an aborted one was replaced by the next.
            if (state.loadingPersonName === name && err.name !== 'AbortError') state.loadingPersonName = null;
            if (err.name === 'AbortError') return;
            console.error('Error loading person faces:', err);
            matchingPersonCount.textContent = 'Error loading faces';
        });
}

// Render face match items in grid
const gridBuildProgress = document.getElementById('grid-build-progress');
const gridBuildBar = document.getElementById('grid-build-bar');
const gridBuildText = document.getElementById('grid-build-text');

/**
 * Show how far along the server is with a grid we are waiting for.
 *
 * The response is one request that takes most of a minute on a large library, so
 * there is nothing to stream and nothing to page. The request doing the work
 * publishes its progress instead, and this polls for it on the side while the fetch
 * is still in flight -- the server is threaded, and the status route touches no
 * database, so asking costs nothing.
 *
 * A cached grid comes back at once and never reports progress, which is why the bar
 * only appears after a moment rather than flashing on every person you click.
 */
function startGridBuildProgress(name) {
    stopGridBuildProgress();
    if (!gridBuildProgress) return;

    let shown = false;
    const poll = () => {
        api.fetch(`/api/unmatched-faces/build-status?name=${encodeURIComponent(name)}`)
            .then(res => (res.ok ? res.json() : null))
            .then(status => {
                if (!status || !status.active || state.gridBuildTimer === null) return;
                if (!shown) {
                    shown = true;
                    gridBuildProgress.classList.remove('hidden');
                    matchingPersonCount.textContent = 'Building this grid...';
                }
                gridBuildBar.style.width = `${status.percent || 0}%`;
                gridBuildText.textContent =
                    `${status.message || 'Working'} — ${status.percent || 0}%`;
            })
            .catch(() => {});
    };

    // Not immediately: a cached grid answers before the first poll would, and a bar
    // that appears and vanishes reads as a glitch.
    state.gridBuildTimer = window.setInterval(poll, 700);
}

function stopGridBuildProgress() {
    if (state.gridBuildTimer !== null) {
        window.clearInterval(state.gridBuildTimer);
        state.gridBuildTimer = null;
    }
    if (gridBuildProgress) gridBuildProgress.classList.add('hidden');
    if (gridBuildBar) gridBuildBar.style.width = '0%';
}

// Its listeners, which main.js adds once the page has loaded.
export function wirePeople() {
    if (peopleView) {
        try {
            if (localStorage.getItem(PEOPLE_VIEW_KEY) === 'face') state.peopleView = 'face';
        } catch (e) { /* by name, then */ }
        peopleViewName.addEventListener('click', () => choosePeopleView('name'));
        peopleViewFace.addEventListener('click', () => choosePeopleView('face'));
        showPeopleViewChoice();
    }
    if (peopleSort) {
        try {
            const saved = localStorage.getItem(PEOPLE_SORT_KEY);
            // Only a choice the list offers; the first visit has none and starts on Name (A-Z) (#793).
            if (saved && [...peopleSort.options].some(o => o.value === saved)) peopleSort.value = saved;
        } catch (e) { /* the preference just will not stick */ }

        peopleSort.addEventListener('change', () => {
            try {
                localStorage.setItem(PEOPLE_SORT_KEY, peopleSort.value);
            } catch (e) { /* as above */ }
            // Keep the tab, so re-ordering the list does not throw away the view.
            if (modeSelect.value === 'tags') renderTagList();
            else renderPeopleList(true);
        });
    }
}
