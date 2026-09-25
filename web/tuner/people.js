// Review People: the people list, and choosing a person.
import { api } from './common/api.js';
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

    api.fetch(apiPath, { signal: state.sidebarAbortController.signal })
        .then(res => {
            if (!res.ok) throw new Error('Network response was not ok');
            return res.json();
        })
        .then(data => {
            state.allPeopleWithCounts = data;
            renderPeopleList(keepTab);
        })
        .catch(err => {
            if (err.name === 'AbortError') return;
            console.error('Error fetching people with counts:', err);
            listStats.textContent = 'Error loading people';
        });
}

//: Remembered, because it is a standing preference about how you read the list
//: rather than a per-visit decision.
const PEOPLE_SORT_KEY = 'tagtuner.peopleSort';

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
        if (byName) {
            return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
        }
        return (b.count || 0) - (a.count || 0);
    });
    return people;
}

// Render unique people with counts in the sidebar
function renderPeopleList(keepTab = false) {
    const savedScrollTop = photoList.scrollTop;
    photoList.innerHTML = '';
    
    if (state.allPeopleWithCounts.length === 0) {
        listStats.textContent = 'No people found';
        return;
    }

    listStats.textContent = `Found ${state.allPeopleWithCounts.length} person(s)`;

    // Apply filter directly in case search has value
    const query = photoSearch.value.toLowerCase();

    sortedPeople().forEach(person => {
        const li = document.createElement('li');
        li.className = 'photo-item';
        li.personName = person.name;
        
        if (person.name === state.activePersonName) {
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

        li.appendChild(title);
        li.appendChild(badge);

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
    if (peopleSort) {
        try {
            const saved = localStorage.getItem(PEOPLE_SORT_KEY);
            if (saved) peopleSort.value = saved;
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
