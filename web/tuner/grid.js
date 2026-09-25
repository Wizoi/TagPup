// The Identify grid: building it, and its tabs.
import { api } from './common/api.js';
import { state } from './state.js';
import {
    inputReassignName, matchingFacesGrid, matchingPersonCount, modeSelect, tabLowConf,
    tabMatches, tabOutliers,
} from './elements.js';
import { BAND_OF, BUCKET, UNKNOWN_YEAR } from './rules.js';
import { personExists } from './shared.js';
import {
    clearFaceDetails, selectFace, showFaceDetails, updateMatchingSelectionUI,
} from './selection.js';
import {
    groupHeadingText, labelClusterAssign, notAClusterNote, sectionFaceIds, sectionPhotoCount,
    syncIdentifyTabHighlight,
} from './grid-parts.js';
import {
    askBeforeIgnoring, EXCLUDE_IGNORED_CLUSTER, offerAssignUndo, postExcludeBulk, postMatchBulk,
    showAutocompletePopup,
} from './assign.js';

export function renderPersonFaces(faces) {
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
    state.selectedFaceIds = [];
    updateMatchingSelectionUI();
    clearFaceDetails();

    let renderedFaces = faces;

    const high = [];
    const lower = [];
    const unclustered = [];
    const standards = [];
    const outliers = [];

    renderedFaces.forEach(face => {
        if (modeSelect.value === 'unmatched-faces') {
            // When the person being sought has faces already named, the useful
            // number is how much a candidate resembles THEM. Without that, all
            // there is is a candidate's similarity to its own cluster -- a number
            // about the crowd it arrived with, which is why a hundred candidates
            // used to read Likely (0), Possible (0).
            // The server names the band (tagpup.core.clustering.band): against the
            // person's faces when they have any, else against the face's own group.
            const band = BAND_OF[face.band] || 'rest';
            if (band === 'high') high.push(face);
            else if (band === 'lower') lower.push(face);
            else unclustered.push(face);
        } else {
            if (face.possibly_wrong) {
                outliers.push(face);
            } else {
                standards.push(face);
            }
        }
    });

    // Set count header and determine faces to render
    let targetFaces = [];
    let displayedCount = 0;
    let unmatchedText = '';
    
    if (modeSelect.value === 'unmatched-faces') {
        // Land on a tab that has faces in it. The default is the most confident
        // one, but when nothing clustered -- which is the common case for a face
        // seen once or twice -- every candidate is ungrouped, and opening on an
        // empty tab is indistinguishable from the person having no candidates at
        // all. That is what "the sidebar says 2 and the panel shows nothing" was.
        const buckets = { high, lower, low: unclustered };
        if (!buckets[state.activeTab] || buckets[state.activeTab].length === 0) {
            const firstWithFaces = ['high', 'lower', 'low']
                .find(name => buckets[name].length > 0);
            if (firstWithFaces) {
                state.activeTab = firstWithFaces;
                syncIdentifyTabHighlight(state.activeTab);
            }
        }

        if (state.activeTab === 'high') {
            targetFaces = high;
            unmatchedText = 'likely';
        } else if (state.activeTab === 'low') {
            targetFaces = unclustered;
            unmatchedText = 'ungrouped';
        } else {
            targetFaces = lower;
            unmatchedText = 'possible';
        }
        displayedCount = targetFaces.length;
    } else {
        if (state.activeTab === 'matches') {
            targetFaces = standards;
            unmatchedText = 'matched';
        } else {
            targetFaces = outliers;
            unmatchedText = 'outlier';
        }
        displayedCount = targetFaces.length;
    }

    // The window, when it is one: these 500 are the first of 800, and the next
    // appear as these are dealt with.
    const windowNote = (state.activeFacesTotal && state.activeTab === 'low')
        ? ` \u2014 first ${displayedCount} of ${state.activeFacesTotal}, more appear as `
          + `you clear these`
        : '';
    state.activeFacesLabel = unmatchedText;
    matchingPersonCount.textContent =
        `${displayedCount} ${unmatchedText} face${displayedCount !== 1 ? 's' : ''}`
        + windowNote;

    if (displayedCount === 0) {
        const emptyGrid = document.createElement('div');
        emptyGrid.style.textAlign = 'center';
        emptyGrid.style.padding = '40px';
        emptyGrid.style.color = 'var(--text-muted)';
        if (modeSelect.value === 'unmatched-faces') {
            emptyGrid.textContent = {
                high: 'No likely matches for this name.',
                lower: 'No possible matches for this name.',
                low: 'Nothing left over for this name.',
            }[state.activeTab] || 'No candidates for this name.';
        } else {
            if (state.activeTab === 'matches') {
                emptyGrid.textContent = 'No matching faces found for this person.';
            } else {
                emptyGrid.textContent = 'No outliers found for this person.';
            }
        }
        matchingFacesGrid.appendChild(emptyGrid);
        return;
    }

    function renderGroupSection(title, groupFaces, returnElement = false) {
        const section = document.createElement('div');
        section.className = 'matching-group-section';

        const header = document.createElement('div');
        header.className = 'matching-group-header';
        // Layout lives in the stylesheet: fixed slots, so the buttons do not
        // move between one cluster and the next.

        const titleSpan = document.createElement('span');
        titleSpan.className = 'matching-group-title';

        // Reserved whether or not there is a suggestion, so its absence
        // does not slide the buttons across.
        const suggestionSlot = document.createElement('div');
        suggestionSlot.className = 'matching-group-suggestion-slot';
        // These faces did not end up together -- they failed to end up anywhere.
        // Every cluster-wide action below is meaningless for them, and dangerous:
        // one click would assign hundreds of unrelated faces to one person.
        const isUnclustered = title === 'Unclustered'
            || groupFaces.every(f => f.cluster_id === -1);

        if (modeSelect.value === 'unmatched-faces') {
            const numFaces = groupFaces.length;
            const numPhotos = new Set(groupFaces.map(f => f.photo_path)).size;
            // Recorded so the heading can be rewritten when faces leave the group,
            // without rebuilding the grid to find out what it used to say.
            section.dataset.groupTitle = title;
            section.dataset.unclustered = isUnclustered ? '1' : '';
            titleSpan.textContent = groupHeadingText(title, isUnclustered, numFaces, numPhotos);

            if (isUnclustered) {
                titleSpan.appendChild(notAClusterNote());
            }

            // Every other person these photos still have no face for. Without it,
            // a group photo's faces look like the tool guessing wildly.
            const competing = [...new Set(
                groupFaces.flatMap(f => f.other_names || [])
            )];
            // Who the already-named faces say this group is. The queue can only
            // offer a name the photo mentions, which is no help for the photos
            // that name nobody -- and those are most of Unknown Faces.
            const suggestion = isUnclustered
                ? null   // a guess for one of them says nothing about the rest
                : groupFaces.find(f => f.suggested_name);
            if (suggestion) {
                const pct = Math.round((suggestion.suggested_similarity || 0) * 100);
                const guessName = suggestion.suggested_name;
                const guess = document.createElement('span');
                guess.className = 'cluster-suggestion';

                // The label puts the name in the box without committing, for when
                // you want to look before you leap, or edit it first.
                const guessLabel = document.createElement('button');
                guessLabel.className = 'cluster-suggestion-label';
                // A guess at 0.72 and one at 0.95 deserve different words. Both
                // are worth showing -- a weak guess is still a shortlist of one --
                // but only one of them should read as an answer.
                const isConfident = suggestion.suggestion_strength !== 'possible';
                guess.classList.add(isConfident ? 'is-likely' : 'is-possible');
                guessLabel.textContent = isConfident
                    ? `Looks like ${guessName} (${pct}%)`
                    : `Possibly ${guessName} (${pct}%)`;
                guessLabel.title = `Compared against the faces already named `
                    + `${guessName}. `
                    + (isConfident ? '' : 'A weaker match, so check the faces first. ')
                    + `Click to put the name in the box without assigning anything.`;
                guessLabel.addEventListener('click', (e) => {
                    e.stopPropagation();
                    if (inputReassignName) {
                        // The name, and the faces it is a guess for. The box used to
                        // take the name alone, as if typed, and it stayed for every
                        // selection after -- "Assign 3" sent faces from other groups
                        // to this group's guess. Selecting the group's faces makes the
                        // name, the selection and the button agree; filled in rather
                        // than typed, it goes when the selection changes.
                        matchingFacesGrid.querySelectorAll('.face-match-item.selected')
                            .forEach(card => card.classList.remove('selected'));
                        section.querySelectorAll('.face-match-item')
                            .forEach(card => card.classList.add('selected'));
                        state.selectedFaceIds = sectionFaceIds(section);
                        inputReassignName.value = guessName;
                        state.nameFilledForFaceIds = [...state.selectedFaceIds];
                        updateMatchingSelectionUI();
                        inputReassignName.focus();
                    }
                });

                // And the one-click path, for when the suggestion is simply right
                // -- which at these similarities it usually is. No confirmation:
                // a prompt on every cluster is the same friction under another
                // name. The undo offered afterwards is what makes that fair.
                const guessAccept = document.createElement('button');
                guessAccept.className = 'cluster-suggestion-assign';
                guessAccept.dataset.guessName = guessName;
                labelClusterAssign(guessAccept, groupFaces.length);
                guessAccept.addEventListener('click', (e) => {
                    e.stopPropagation();
                    // The faces in the group now, not the ones it was drawn with.
                    const ids = sectionFaceIds(section);
                    if (!ids.length) return;
                    guessAccept.disabled = true;
                    guessAccept.textContent = 'Assigning...';
                    Promise.resolve(postMatchBulk(ids, guessName)).then(assigned => {
                        // Undo only for what the server says it did. Offered
                        // before the reply, a refused batch still read
                        // "Assigned 5" and its Undo unmatched faces nobody had
                        // assigned.
                        if (assigned && assigned.length) {
                            offerAssignUndo(assigned, guessName, 'assign');
                        }
                        // Whatever is left -- all of it, on a failure -- can be
                        // tried again.
                        if (guessAccept.isConnected) {
                            guessAccept.disabled = false;
                            labelClusterAssign(guessAccept, sectionFaceIds(section).length);
                        }
                    });
                });

                guess.appendChild(guessLabel);
                guess.appendChild(guessAccept);
                suggestionSlot.appendChild(guess);
            }

            if (competing.length) {
                const note = document.createElement('span');
                note.className = 'competing-names-note';
                note.textContent = competing.length === 1
                    ? `also names ${competing[0]}`
                    : `also names ${competing.length} others`;
                note.title = `These photos also name: ${competing.join(', ')}.\n\n`
                    + `Faces are offered under every name their photo is still `
                    + `missing, so the same face can appear under more than one `
                    + `person. Assigning one removes it from the others.`;
                titleSpan.appendChild(note);
            }
        } else {
            titleSpan.textContent = title;
        }
        header.appendChild(titleSpan);
        header.appendChild(suggestionSlot);

        // Not for the Unclustered bucket: its faces have nothing in common, so
        // "assign all of these to one person" and "ignore all of these" are both
        // wrong by construction -- and one of them would have excluded up to 500
        // faces without asking, once the confirmation was turned off.
        if (modeSelect.value === 'unmatched-faces' && !isUnclustered) {
            const assignBtn = document.createElement('button');
            assignBtn.className = 'btn btn-primary btn-sm cluster-action';
            assignBtn.style.padding = '2px 8px';
            assignBtn.style.fontSize = '11px';
            assignBtn.textContent = '👤 Assign Cluster';
            assignBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                const executeAssignment = (name) => {
                    if (!personExists(name)) {
                        if (!confirm(`"${name}" is not currently in the database. Do you want to create a new person tag and assign this cluster to it?`)) {
                            return;
                        }
                    } else {
                        if (!confirm(`Are you sure you want to assign all ${sectionFaceIds(section).length} faces in this cluster to "${name}"?`)) {
                            return;
                        }
                    }
                    // Read when the name is given, not when the button was drawn:
                    // faces may have left the group since, or while the prompt
                    // was open.
                    const faceIds = sectionFaceIds(section);
                    if (!faceIds.length) return;
                    postMatchBulk(faceIds, name);
                };

                if (state.activePersonName === BUCKET.UNKNOWN) {
                    showAutocompletePopup(sectionFaceIds(section).length, executeAssignment);
                } else {
                    const name = inputReassignName.value.trim();
                    if (!name) {
                        alert('Please enter or select a name in the "Assign to name..." input field first.');
                        inputReassignName.focus();
                        return;
                    }
                    executeAssignment(name);
                }
            });
            header.appendChild(assignBtn);

            // The counterpart to Assign Cluster: these faces are somebody we are
            // never going to name -- a passer-by, a spectator, a bad crop -- and
            // should stop being offered as a candidate to anyone. Doing it per
            // face meant ticking thirty boxes to say one thing.
            const ignoreBtn = document.createElement('button');
            ignoreBtn.className = 'btn btn-secondary btn-sm cluster-action';
            ignoreBtn.style.padding = '2px 8px';
            ignoreBtn.style.fontSize = '11px';
            ignoreBtn.style.marginLeft = '6px';
            ignoreBtn.textContent = '\uD83D\uDEAB Ignore Cluster';
            ignoreBtn.title =
                'Keep these faces out of matching entirely. They stay in the '
                + 'Excluded bucket and can be put back.';
            ignoreBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                // The faces still in the group. Excluding clears a face's name,
                // so acting on the group as first drawn wiped the names of any
                // faces assigned from it since.
                const shown = sectionFaceIds(section);
                if (!shown.length) return;
                askBeforeIgnoring(shown.length, sectionPhotoCount(section), () => {
                    const ids = sectionFaceIds(section);
                    if (!ids.length) return;
                    Promise.resolve(postExcludeBulk(ids, EXCLUDE_IGNORED_CLUSTER)).then(ok => {
                        if (ok) offerAssignUndo(ids, null, 'ignore');
                    });
                });
            });
            header.appendChild(ignoreBtn);
        }

        section.appendChild(header);

        const grid = document.createElement('div');
        grid.className = 'matching-group-grid';

        groupFaces.forEach(face => {
            const item = document.createElement('div');
            item.className = 'face-match-item';
            item.dataset.faceId = String(face.id);
            state.renderedFacesById.set(face.id, face);
            state.renderedFaceOrder.push(face.id);

            // Why this face was ruled out, on the face. It has been recorded since
            // exclusions existed and never shown, so the one place it could be
            // useful -- reviewing what you ruled out and why -- did not have it.
            if (face.reason) {
                const why = document.createElement('span');
                why.className = 'face-exclusion-reason';
                why.textContent = face.reason;
                why.title = `Excluded as "${face.reason}"`;
                item.appendChild(why);
            }
            // Why this face is here: its photo names somebody else too, and
            // neither has a face yet, so both faces are offered under both names.
            // Saying so turns a confusing grid into a clear task.
            const competingHere = (face.other_names || []).length
                ? `

This photo also names ${face.other_names.join(', ')}. `
                  + `Assign whichever face is this person and the rest stop being `
                  + `offered for them.`
                : '';
            if (competingHere) item.classList.add('has-competing-names');
            // This face's own guess, from the faces already named. A cluster
            // cannot carry one on behalf of faces that resemble nothing, but each
            // face can carry its own -- and that is exactly what this bucket is
            // full of.
            if (face.suggested_name) {
                state.suggestionByFaceId.set(face.id, face.suggested_name);
                const guess = document.createElement('button');
                guess.className = 'face-guess'
                    + (face.suggestion_strength === 'possible' ? ' is-possible' : '');
                const pct = Math.round((face.suggested_similarity || 0) * 100);
                guess.textContent = `${face.suggested_name} ${pct}%`;
                guess.title = `Resembles the faces already named ${face.suggested_name}`
                    + ` (${pct}%). Click to select this face and put the name in the `
                    + `box, then Assign Selected.`;
                guess.addEventListener('click', (ev) => {
                    ev.stopPropagation();
                    if (!state.selectedFaceIds.includes(face.id)) {
                        state.selectedFaceIds.push(face.id);
                        item.classList.add('selected');
                    }
                    if (inputReassignName) {
                        inputReassignName.value = face.suggested_name;
                        state.nameFilledForFaceIds = [...state.selectedFaceIds];
                    }
                    updateMatchingSelectionUI();
                    showFaceDetails(face.id);
                });
                item.appendChild(guess);
            }

            if (face.person_similarity !== undefined) {
                const badge = document.createElement('span');
                badge.className = 'face-resemblance';
                badge.textContent = `${Math.round(face.person_similarity * 100)}%`;
                badge.title = `How much this face resembles the faces already `
                    + `named for this person. The list is ordered by it.`;
                item.appendChild(badge);
            }
            item.title = face.photo_path + competingHere;
            item.setAttribute('data-face-id', face.id);
            // So a group's heading can be recounted from the cards still in it.
            item.dataset.photoPath = face.photo_path || '';

            if (state.selectedFaceIds.includes(face.id)) {
                item.classList.add('selected');
            }

            const img = document.createElement('img');
            img.src = api.image(`/api/face-crop?id=${face.id}`);
            img.alt = `Face crop ${face.id}`;
            img.loading = 'lazy';
            item.appendChild(img);

            item.addEventListener('click', (e) => selectFace(face.id, e));

            grid.appendChild(item);
        });

        section.appendChild(grid);
        if (returnElement) {
            return section;
        } else {
            matchingFacesGrid.appendChild(section);
        }
    }

    if (modeSelect.value === 'unmatched-faces') {
        // 1. Group target faces by cluster name
        const clusterMap = {};
        targetFaces.forEach(face => {
            const clusterName = face.cluster_name || 'Unclustered';
            if (!clusterMap[clusterName]) {
                clusterMap[clusterName] = [];
            }
            clusterMap[clusterName].push(face);
        });

        // Faces the database can put a name to come first. In a bucket of 500
        // strangers the handful it recognises are the whole reason to look, and
        // burying them in arrival order means never finding them.
        Object.keys(clusterMap).forEach(cName => {
            clusterMap[cName].sort((a, b) => {
                // By how much each face resembles the nearest person the database
                // knows -- every face, not only the ones over the floor. The
                // server reports that number even when it withholds the name, so
                // the near-misses land next and the true strangers last. Sorting
                // only the named ones left 470 of 500 in arrival order, which is
                // no order at all.
                const bestA = a.suggested_similarity;
                const bestB = b.suggested_similarity;
                if (bestA !== undefined && bestB !== undefined && bestA !== bestB) {
                    return bestB - bestA;
                }
                if (bestA !== undefined && bestB === undefined) return -1;
                if (bestB !== undefined && bestA === undefined) return 1;
                return (b.similarity || 0) - (a.similarity || 0);
            });
        });

        // 2. Map each cluster to its most recent year
        const yearClusters = {};
        Object.keys(clusterMap).forEach(cName => {
            const faces = clusterMap[cName];
            let maxYear = 0;
            faces.forEach(f => {
                const y = parseInt(f.year);
                if (y && y > maxYear) maxYear = y;
            });
            const yearStr = maxYear > 0 ? maxYear.toString() : UNKNOWN_YEAR;
            if (!yearClusters[yearStr]) {
                yearClusters[yearStr] = [];
            }
            yearClusters[yearStr].push({ name: cName, faces: faces });
        });

        // 3. Sort years descending
        const sortedYears = Object.keys(yearClusters).sort((a, b) => {
            if (a === UNKNOWN_YEAR) return 1;
            if (b === UNKNOWN_YEAR) return -1;
            return parseInt(b) - parseInt(a);
        });

        // 4. Render year headers and clusters
        sortedYears.forEach(year => {
            const yearContainer = document.createElement('div');
            yearContainer.className = 'year-section-container';
            yearContainer.style.marginBottom = '30px';

            const yearHeader = document.createElement('div');
            yearHeader.className = 'year-section-header';
            yearHeader.style.padding = '8px 4px';
            yearHeader.style.fontSize = '18px';
            yearHeader.style.fontWeight = '700';
            yearHeader.style.borderBottom = '2px solid var(--border-color)';
            yearHeader.style.color = 'var(--text-primary)';
            yearHeader.style.marginBottom = '16px';
            yearHeader.textContent = `Year: ${year}`;
            yearContainer.appendChild(yearHeader);

            // Clusters you can act on, first. A named guess is one click from
            // done; a nameless cluster is a puzzle. Sorting by size put the
            // biggest puzzles at the top and scattered the easy wins, so the page
            // opened on the hardest thing on it.
            //
            // Within each band size still decides, because a bigger cluster is
            // more work resolved by the same click.
            const clusters = yearClusters[year];
            const confidenceOf = (cluster) => {
                const withGuess = cluster.faces.find(f => f.suggested_name);
                return withGuess ? (withGuess.suggested_similarity || 0) : 0;
            };
            clusters.sort((a, b) => {
                const diff = confidenceOf(b) - confidenceOf(a);
                if (Math.abs(diff) > 0.0001) return diff;
                return b.faces.length - a.faces.length;
            });

            clusters.forEach(cluster => {
                const section = renderGroupSection(cluster.name, cluster.faces, true);
                if (section) {
                    yearContainer.appendChild(section);
                }
            });

            matchingFacesGrid.appendChild(yearContainer);
        });
    } else {
        // Group by year
        const groups = {};
        targetFaces.forEach(face => {
            const year = face.year || UNKNOWN_YEAR;
            if (!groups[year]) {
                groups[year] = [];
            }
            groups[year].push(face);
        });

        // Sort years descending
        const sortedGroupNames = Object.keys(groups).sort((a, b) => {
            if (a === UNKNOWN_YEAR) return 1;
            if (b === UNKNOWN_YEAR) return -1;
            return b - a;
        });

        // Sort faces within each year descending by mtime
        sortedGroupNames.forEach(year => {
            groups[year].sort((a, b) => {
                const timeA = a.mtime || 0;
                const timeB = b.mtime || 0;
                return timeB - timeA;
            });
        });

        // Render sections
        sortedGroupNames.forEach(groupName => {
            renderGroupSection(groupName, groups[groupName]);
        });
    }

    updateMatchingSelectionUI();
}

// Its listeners, which main.js adds once the page has loaded.
export function wireGrid() {
    if (tabMatches && tabOutliers && tabLowConf) {
        tabMatches.addEventListener('click', () => {
            const targetMode = modeSelect.value === 'unmatched-faces' ? 'high' : 'matches';
            if (state.activeTab === targetMode) return;
            state.activeTab = targetMode;
            tabMatches.classList.add('active');
            tabOutliers.classList.remove('active');
            tabLowConf.classList.remove('active');
            state.selectedFaceIds = [];
            renderPersonFaces(state.activePersonFaces);
            updateMatchingSelectionUI();
            clearFaceDetails();
        });
        tabOutliers.addEventListener('click', () => {
            const targetMode = modeSelect.value === 'unmatched-faces' ? 'lower' : 'outliers';
            if (state.activeTab === targetMode) return;
            state.activeTab = targetMode;
            tabOutliers.classList.add('active');
            tabMatches.classList.remove('active');
            tabLowConf.classList.remove('active');
            state.selectedFaceIds = [];
            renderPersonFaces(state.activePersonFaces);
            updateMatchingSelectionUI();
            clearFaceDetails();
        });
        tabLowConf.addEventListener('click', () => {
            if (modeSelect.value !== 'unmatched-faces') return;
            if (state.activeTab === 'low') return;
            state.activeTab = 'low';
            tabLowConf.classList.add('active');
            tabMatches.classList.remove('active');
            tabOutliers.classList.remove('active');
            state.selectedFaceIds = [];
            renderPersonFaces(state.activePersonFaces);
            updateMatchingSelectionUI();
            clearFaceDetails();
        });
    }
}
