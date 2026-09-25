// TagPup's page: what the selected photos hold, and tagging them all at once.
import { api } from './common/api.js';
import { leafOf, photoAlreadyHas, samePerson, tagProblem } from './common/vocabulary.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import {
    btnApplyRename, bulkAddPeopleInput, bulkAddTagsInput, folderSelectionSidebar,
    selectedThumbnailsCount, selectionDateLabel, selectionDateValue, selectionEmptyHint,
    selectionPeopleList, selectionSuggestedPeopleList, selectionSuggestedTagsList,
    selectionSummaryCount, selectionSummaryScroll, selectionTagsList, statusDot, statusText
} from './elements.js';
import { saveToLocalStorageCache } from './cache.js';
import {
    formatFriendlyDateRange, formatFriendlyDateSingle, getFolderDateStats,
    parseExifDateToLocalDate, takenOf
} from './format.js';
import {
    namesAPerson, resolveTagOrPerson, updatePeopleDatalist, updateTagsDatalist
} from './tags.js';
import { renderFileList } from './folder.js';
import { renderThumbnails } from './grid.js';

export function updateSelectedThumbnailsCount() {
    selectedThumbnailsCount.textContent = `Selected: ${state.selectedThumbnails.length}`;
    selectionSummaryCount.textContent = `Selected: ${state.selectedThumbnails.length}`;
    
    // The panel stays mounted whether or not anything is selected. It used to be
    // hidden on an empty selection, so clearing and re-selecting made the whole
    // right-hand column collapse and reflow on every click.
    folderSelectionSidebar.classList.remove('hidden');
    if (selectionEmptyHint) {
        selectionEmptyHint.classList.toggle('hidden', state.selectedThumbnails.length > 0);
    }
    if (selectionSummaryScroll) {
        selectionSummaryScroll.classList.toggle('hidden', state.selectedThumbnails.length === 0);
    }

    if (state.selectedThumbnails.length > 0) {
        
        // Gather statistics
        const selectedPhotos = state.folderPhotos.filter(p => state.selectedThumbnails.includes(p.path));
        
        // Calculate Date Taken Range
        const dateObjs = [];
        selectedPhotos.forEach(photo => {
            const photoDate = takenOf(photo) && parseExifDateToLocalDate(takenOf(photo));
            if (photoDate) {
                dateObjs.push(photoDate);
            }
        });

        if (dateObjs.length === 0) {
            selectionDateLabel.textContent = "Date Taken";
            selectionDateValue.textContent = "Unknown";
        } else if (dateObjs.length === 1) {
            selectionDateLabel.textContent = "Date Taken";
            const stats = getFolderDateStats();
            selectionDateValue.textContent = formatFriendlyDateSingle(dateObjs[0], stats);
        } else {
            dateObjs.sort((a, b) => a - b);
            selectionDateLabel.textContent = "Date Taken Range";
            selectionDateValue.textContent = formatFriendlyDateRange(dateObjs[0], dateObjs[dateObjs.length - 1]);
        }

        // One chip per person, but remembering every spelling their name actually
        // takes across the selection. The chip is labelled with the leaf and used
        // to be actioned with it too, so removing someone sent "Hazel Brookmire"
        // for a photo tagged "People/Hazel Brookmire": the server removes by exact
        // match, so nothing came off, and every selected file was rewritten anyway.
        const peopleCounts = {};
        const tagCounts = {};

        selectedPhotos.forEach(photo => {
            const photoPeople = photo.people || [];
            const tags = photo.tags || [];

            tags.forEach(tag => {
                const isPerson = namesAPerson(tag) || photoPeople.includes(tag);
                if (isPerson) {
                    const leaf = leafOf(tag);
                    if (!peopleCounts[leaf]) peopleCounts[leaf] = { count: 0, tags: [] };
                    peopleCounts[leaf].count++;
                    if (!peopleCounts[leaf].tags.includes(tag)) {
                        peopleCounts[leaf].tags.push(tag);
                    }
                } else {
                    tagCounts[tag] = (tagCounts[tag] || 0) + 1;
                }
            });
        });
        
        // Render People List
        selectionPeopleList.innerHTML = '';
        const peopleKeys = Object.keys(peopleCounts).sort();
        if (peopleKeys.length === 0) {
            selectionPeopleList.innerHTML = '<span style="color: var(--text-muted); font-size: 12px; padding: 4px 0;">None</span>';
        } else {
            peopleKeys.forEach(p => {
                const { count, tags: spellings } = peopleCounts[p];
                // Apply the pathed form where the selection has one, so applying a
                // person never introduces the bare name the keywords must not hold.
                const applyAs = spellings.find(t => t.includes('/')) || spellings[0];
                const chip = document.createElement('span');
                chip.className = 'selection-summary-chip';
                chip.textContent = `${p} (${count})`;
                chip.title = spellings.join(', ');

                // Show apply arrow only if it's not present on ALL selected photos
                if (count < state.selectedThumbnails.length) {
                    const applyIcon = document.createElement('span');
                    applyIcon.className = 'selection-summary-chip-apply';
                    applyIcon.textContent = ' ➡️';
                    applyIcon.title = `Apply "${applyAs}" to all selected photos`;
                    applyIcon.addEventListener('click', (e) => {
                        e.stopPropagation();
                        applyTagToAllSelected(applyAs, true);
                    });
                    chip.appendChild(applyIcon);
                }

                // Remove icon. Every spelling goes, or a person tagged both ways
                // across the selection comes half off and the chip stays put.
                const removeIcon = document.createElement('span');
                removeIcon.className = 'selection-summary-chip-remove';
                removeIcon.textContent = ' ×';
                removeIcon.title = `Remove "${p}" from all selected photos`;
                removeIcon.addEventListener('click', (e) => {
                    e.stopPropagation();
                    removeTagFromAllSelected(spellings, true);
                });
                chip.appendChild(removeIcon);

                selectionPeopleList.appendChild(chip);
            });
        }
        
        // Render Tags List
        selectionTagsList.innerHTML = '';
        const tagKeys = Object.keys(tagCounts).sort();
        if (tagKeys.length === 0) {
            selectionTagsList.innerHTML = '<span style="color: var(--text-muted); font-size: 12px; padding: 4px 0;">None</span>';
        } else {
            tagKeys.forEach(t => {
                const count = tagCounts[t];
                const chip = document.createElement('span');
                chip.className = 'selection-summary-chip';
                chip.textContent = `${t} (${count})`;
                
                // Show apply arrow only if it's not present on ALL selected photos
                if (count < state.selectedThumbnails.length) {
                    const applyIcon = document.createElement('span');
                    applyIcon.className = 'selection-summary-chip-apply';
                    applyIcon.textContent = ' ➡️';
                    applyIcon.title = `Apply "${t}" to all selected photos`;
                    applyIcon.addEventListener('click', (e) => {
                        e.stopPropagation();
                        applyTagToAllSelected(t, false);
                    });
                    chip.appendChild(applyIcon);
                }
                
                // Remove icon
                const removeIcon = document.createElement('span');
                removeIcon.className = 'selection-summary-chip-remove';
                removeIcon.textContent = ' ×';
                removeIcon.title = `Remove "${t}" from all selected photos`;
                removeIcon.addEventListener('click', (e) => {
                    e.stopPropagation();
                    removeTagFromAllSelected(t, false);
                });
                chip.appendChild(removeIcon);
                selectionTagsList.appendChild(chip);
            });
        }

        // Tally suggested tags & people from folderSuggestions
        const suggPeopleCounts = {};
        const suggTagCounts = {};
        
        selectedPhotos.forEach(photo => {
            const sugg = state.folderSuggestions[photo.path];
            if (sugg) {
                const photoPeople = photo.people || [];
                const tags = photo.tags || [];
                
                // Suggestions might contain people
                if (sugg.people) {
                    sugg.people.forEach(p => {
                        const leaf = leafOf(p.name);
                        // Only suggest someone this photo does not already name.
                        // `tags.includes(leaf)` compares a bare suggestion against
                        // tags that are paths, so it never matched; the list was
                        // right only because photo.people happens to hold leaves,
                        // and would have offered everybody the moment it did not.
                        const alreadyAdded =
                            photoAlreadyHas(photo, p.name, namesAPerson)
                            || photoPeople.some(n => samePerson(n, leaf));
                        if (!alreadyAdded) {
                            noteSuggestion(suggPeopleCounts, leaf, photo.path, p.score);
                        }
                    });
                }
                
                // Suggestions might contain general tags
                if (sugg.tags) {
                    sugg.tags.forEach(t => {
                        const leaf = t.tag;
                        const isPerson = namesAPerson(leaf);
                        // photoAlreadyHas compares people by who they are, so a
                        // suggested "Kira Bao" counts as present on a photo tagged
                        // "People/Kira Bao". A plain keyword still matches exactly.
                        if (photoAlreadyHas(photo, leaf, namesAPerson)) return;
                        if (isPerson) {
                            const cleanLeaf = leafOf(leaf);
                            if (photoPeople.some(n => samePerson(n, cleanLeaf))) return;
                            noteSuggestion(suggPeopleCounts, cleanLeaf, photo.path, t.score);
                        } else {
                            noteSuggestion(suggTagCounts, leaf, photo.path, t.score);
                        }
                    });
                }
            }
        });
        
        renderSuggestionChips(selectionSuggestedPeopleList, suggPeopleCounts, true);
        renderSuggestionChips(selectionSuggestedTagsList, suggTagCounts, false);

        btnApplyRename.disabled = false;
        upper.updateFolderAutoApplyState();
    } else {
        // Empty selection: keep the panel, clear what it was showing.
        selectionDateLabel.textContent = 'Date Taken';
        selectionDateValue.textContent = '--';
        if (selectionPeopleList) selectionPeopleList.innerHTML = '';
        if (selectionTagsList) selectionTagsList.innerHTML = '';
        if (selectionSuggestedPeopleList) selectionSuggestedPeopleList.innerHTML = '';
        if (selectionSuggestedTagsList) selectionSuggestedTagsList.innerHTML = '';
        btnApplyRename.disabled = true;
        upper.updateFolderAutoApplyState();
    }
}

//: Auto-apply writes everything the panel offers, so there is no bar to be below
//: any more. The score is still shown, because how sure the machine was is worth
//: knowing before you press a button that writes to every selected photo -- but it
//: no longer decides anything, and a suggestion left on screen after Apply All
//: would now be a bug rather than a threshold.
export const CONFIDENT_ENOUGH_TO_LOOK_SURE = 0.75;

/** Record a suggestion, remembering which photos asked for it and how strongly. */
export function noteSuggestion(into, key, photoPath, score) {
    const entry = into[key] || (into[key] = { count: 0, paths: [], score: 0 });
    entry.count++;
    if (!entry.paths.includes(photoPath)) entry.paths.push(photoPath);
    entry.score = Math.max(entry.score, Number(score) || 0);
}

/**
 * Render one list of suggestion chips.
 *
 * Each chip carries its confidence and applies to the photos that actually asked
 * for it. It used to apply to the whole selection: clicking "Anh Tran (1)" with
 * 77 photos selected put her on all 77, which is the opposite of what a suggestion
 * for one photo means.
 */
export function renderSuggestionChips(container, counts, isPerson) {
    container.innerHTML = '';
    const keys = Object.keys(counts).sort();
    if (keys.length === 0) {
        container.innerHTML = '<span style="color: var(--text-muted); font-size: 12px; padding: 4px 0;">None</span>';
        return;
    }
    keys.forEach(name => {
        const { count, paths, score } = counts[name];
        const pct = Math.round(score * 100);
        const unsure = score < CONFIDENT_ENOUGH_TO_LOOK_SURE;

        const chip = document.createElement('span');
        chip.className = 'suggestion-chip' + (unsure ? ' suggestion-chip-unsure' : '');
        chip.style.cursor = 'pointer';
        chip.textContent = `${name} (${count}) · ${pct}%`;
        chip.title = `${pct}% confident— click to add it to ${count} photo(s) now, `
            + `or leave it for Apply All, which writes everything here.`;
        chip.addEventListener('click', (e) => {
            e.stopPropagation();
            applyTagToPhotos(name, isPerson, paths);
        });
        container.appendChild(chip);
    });
}

/** Apply a tag to every selected photo. */
export function applyTagToAllSelected(tag, isPerson) {
    return applyTagToPhotos(tag, isPerson, state.selectedThumbnails);
}

/**
 * Apply a tag to a given set of photos.
 *
 * A suggestion belongs to the photos that produced it, not to whatever happens to
 * be selected at the time.
 */
export function applyTagToPhotos(tag, isPerson, paths) {
    const targets = (paths || []).filter(Boolean);
    if (targets.length === 0) return;
    
    statusDot.className = 'status-indicator-dot busy';
    statusText.textContent = 'Applying tag...';
    
    api.json('/api/photos/bulk-tags', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths: targets, add_tags: [tag], remove_tags: [] })
    })
    .then(data => {
        if (data.success) {
            // Update tags in cache
            targets.forEach(path => {
                const photo = state.folderPhotos.find(p => p.path === path);
                if (photo) {
                    if (!photo.tags.includes(tag)) {
                        photo.tags.push(tag);
                    }
                    if (isPerson) {
                        // photo.people holds leaf names, not paths.
                        const leaf = leafOf(tag);
                        if (!photo.people) photo.people = [];
                        if (!photo.people.includes(leaf)) photo.people.push(leaf);
                    }
                }
            });
            
            updateSelectedThumbnailsCount();
            renderFileList();
            renderThumbnails();
            updateTagsDatalist();
            saveToLocalStorageCache();
            
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Ready';
        } else {
            throw new Error(data.error);
        }
    })
    .catch(err => {
        console.error(err);
        statusDot.className = 'status-indicator-dot';
        statusText.textContent = 'Error';
        alert("Error applying tag selection: " + err.message);
    });
}

/**
 * Take a tag off every selected photo. Accepts one tag or several.
 *
 * Several, because a person can be spelled more than one way across a selection --
 * "People/Hazel Brookmire" on the photos tagged properly and "Hazel Brookmire" on
 * the ones an earlier bug reached. The server removes by exact match, so removing
 * only one of those leaves the chip in place and looks like the button is broken.
 */
export function removeTagFromAllSelected(tagOrTags, isPerson) {
    if (state.selectedThumbnails.length === 0) return;
    const tags = Array.isArray(tagOrTags) ? tagOrTags : [tagOrTags];
    if (tags.length === 0) return;
    const leaves = tags.map(t => leafOf(t).toLowerCase());

    statusDot.className = 'status-indicator-dot busy';
    statusText.textContent = 'Removing tag...';

    api.json('/api/photos/bulk-tags', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths: state.selectedThumbnails, add_tags: [], remove_tags: tags })
    })
    .then(data => {
        if (data.success) {
            // Update tags in cache
            state.selectedThumbnails.forEach(path => {
                const photo = state.folderPhotos.find(p => p.path === path);
                if (photo) {
                    photo.tags = photo.tags.filter(t => !tags.includes(t));
                    if (isPerson && photo.people) {
                        photo.people = photo.people.filter(
                            p => !leaves.includes(leafOf(p).toLowerCase()));
                    }
                }
            });
            
            updateSelectedThumbnailsCount();
            renderFileList();
            renderThumbnails();
            updateTagsDatalist();
            saveToLocalStorageCache();
            
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Ready';
        } else {
            throw new Error(data.error);
        }
    })
    .catch(err => {
        console.error(err);
        statusDot.className = 'status-indicator-dot';
        statusText.textContent = 'Error';
        alert("Error removing tag selection: " + err.message);
    });
}

// Bulk Editing operations
export async function bulkAddPeopleToSelection() {
    if (state.selectedThumbnails.length === 0) return;
    const val = bulkAddPeopleInput.value.trim();
    if (!val) return;
    
    const peopleList = val.split(',').map(p => p.trim()).filter(p => p);
    if (peopleList.length === 0) return;
    // All or nothing, and the text stays to be corrected.
    const refused = peopleList.map(tagProblem).find(Boolean);
    if (refused) {
        alert(refused);
        return;
    }

    const resolvedPeople = [];
    for (const p of peopleList) {
        const resolved = await resolveTagOrPerson(p, true);
        if (resolved) {
            resolvedPeople.push(resolved);
        }
    }
    if (resolvedPeople.length === 0) return;

    statusDot.className = 'status-indicator-dot busy';
    statusText.textContent = 'Adding people...';

    api.json('/api/photos/bulk-tags', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths: state.selectedThumbnails, add_tags: resolvedPeople, remove_tags: [] })
    })
    .then(data => {
        if (data.success) {
            state.selectedThumbnails.forEach(path => {
                const photo = state.folderPhotos.find(p => p.path === path);
                if (photo) {
                    if (!photo.people) photo.people = [];
                    resolvedPeople.forEach(p => {
                        if (!photo.tags.includes(p)) photo.tags.push(p);
                        const leaf = leafOf(p);
                        if (!photo.people.includes(leaf)) photo.people.push(leaf);
                    });
                }
            });

            bulkAddPeopleInput.value = '';
            updateSelectedThumbnailsCount();
            renderFileList();
            renderThumbnails();
            updatePeopleDatalist();
            saveToLocalStorageCache();

            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Ready';
        } else {
            throw new Error(data.error);
        }
    })
    .catch(err => {
        console.error(err);
        statusDot.className = 'status-indicator-dot';
        statusText.textContent = 'Error';
        alert("Error bulk adding people: " + err.message);
    });
}

export async function bulkAddTagsToSelection() {
    if (state.selectedThumbnails.length === 0) return;
    const val = bulkAddTagsInput.value.trim();
    if (!val) return;
    
    const tagsList = val.split(',').map(t => t.trim()).filter(t => t);
    if (tagsList.length === 0) return;
    // All or nothing, and the text stays to be corrected.
    const refused = tagsList.map(tagProblem).find(Boolean);
    if (refused) {
        alert(refused);
        return;
    }

    const resolvedTags = [];
    for (const t of tagsList) {
        const resolved = await resolveTagOrPerson(t, false);
        if (resolved) {
            resolvedTags.push(resolved);
        }
    }
    if (resolvedTags.length === 0) return;

    statusDot.className = 'status-indicator-dot busy';
    statusText.textContent = 'Adding tags...';

    api.json('/api/photos/bulk-tags', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths: state.selectedThumbnails, add_tags: resolvedTags, remove_tags: [] })
    })
    .then(data => {
        if (data.success) {
            state.selectedThumbnails.forEach(path => {
                const photo = state.folderPhotos.find(p => p.path === path);
                if (photo) {
                    resolvedTags.forEach(t => {
                        if (!photo.tags.includes(t)) photo.tags.push(t);
                    });
                }
            });

            bulkAddTagsInput.value = '';
            updateSelectedThumbnailsCount();
            renderFileList();
            renderThumbnails();
            updateTagsDatalist();
            saveToLocalStorageCache();

            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Ready';
        } else {
            throw new Error(data.error);
        }
    })
    .catch(err => {
        console.error(err);
        statusDot.className = 'status-indicator-dot';
        statusText.textContent = 'Error';
        alert("Error bulk adding tags: " + err.message);
    });
}
