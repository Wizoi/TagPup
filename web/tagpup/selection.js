// TagPup's page: what the selected photos hold, and tagging them all at once.
import { api } from './common/api.js';
import { buildElement, replaceContent } from './common/dom.js';
import { pathKey, samePath } from './common/paths.js';
import { leafOf, peopleListHas, photoAlreadyHas, sortedTags, tagProblem } from './common/vocabulary.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import {
    btnApplyRename, bulkAddPeopleInput, bulkAddTagsInput, folderSelectionSidebar,
    selectedThumbnailsCount, selectionDateGroup, selectionDateLabel, selectionDateValue, selectionEmptyHint,
    selectionFoldersGroup, selectionNote,
    selectionPeopleList, selectionSuggestedPeopleList, selectionSuggestedTagsList,
    selectionSummaryCount, selectionSummaryScroll, selectionTagsList, statusDot, statusText
} from './elements.js';
import { saveToLocalStorageCache } from './cache.js';
import { noteSkipped, whereWritten } from './write-queue.js';
import {
    formatFriendlyDateRange, formatFriendlyDateSingle, getFolderDateStats,
    parseExifDateToLocalDate, takenOf
} from './format.js';
import {
    namesAPerson, resolveTagOrPerson, updatePeopleDatalist, updateTagsDatalist
} from './tags.js';
import { renderFileList } from './folder.js';
import { renderThumbnails } from './grid.js';
import { queuePhotoWrite, takeWritten } from './edits.js';
import { BULK_CONFIRM_ABOVE, BULK_LIMIT, isSelected, selectionCount, selectionProblem } from './selected.js';
import { setStatus } from './status.js';
import { addTypedToSelection } from './bulk-edit.js';
import { clearTally, selectionTallied } from './tally.js';
import { drawJumpNote, showPanelFor } from './selection-panel.js';

/**
 * A library view's selection that a request cannot carry says why beside the panel, the moment it is so, not when a bulk
 * edit is refused (selected.js selectionProblem; nothing is asked of the server).
 */
function updateSelectionNote() {
    if (!selectionNote) return;
    const problem = state.library ? selectionProblem() : '';
    selectionNote.textContent = problem;
    selectionNote.classList.toggle('hidden', !problem);
}

export function updateSelectedThumbnailsCount() {
    updateSelectionNote();
    // A folder's selection is paths; a library view's is photo ids, and the count is the ids' (selected.js).
    const count = selectionCount();
    const shown = state.library ? count.toLocaleString() : String(count);
    selectedThumbnailsCount.textContent = `Selected: ${shown}`;
    selectionSummaryCount.textContent = `Selected: ${shown}`;
    
    // The panel stays mounted whether or not anything is selected. It used to be
    // hidden on an empty selection, so clearing and re-selecting made the whole
    // right-hand column collapse and reflow on every click.
    folderSelectionSidebar.classList.remove('hidden');
    if (selectionEmptyHint) {
        selectionEmptyHint.classList.toggle('hidden', count > 0);
    }
    if (selectionSummaryScroll) {
        selectionSummaryScroll.classList.toggle('hidden', count === 0);
    }

    showPanelFor(Boolean(state.library));
    if (state.library) {
        showLibrarySelection();
        return;
    }
    // Organize's panel: Date Taken, and no Folders to Organize (the folder is open).
    if (selectionDateGroup) selectionDateGroup.classList.remove('hidden');
    if (selectionFoldersGroup) selectionFoldersGroup.classList.add('hidden');
    if (state.selectedThumbnails.length > 0) {
        
        // Gather statistics
        const selectedPhotos = state.folderPhotos.filter(p => isSelected(p.path));
        
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
                    // One entry per PERSON: a tag the people records know is that person's id, so two people called Sam are two chips.
                    const leaf = leafOf(tag);
                    const known = state.people.ofText(tag);
                    const key = known ? `id:${known.id}` : leaf;
                    if (!peopleCounts[key]) peopleCounts[key] = { count: 0, tags: [], shown: known ? known.name : leaf, title: known ? known.tag : '' };
                    peopleCounts[key].count++;
                    if (!peopleCounts[key].tags.includes(tag)) {
                        peopleCounts[key].tags.push(tag);
                    }
                } else {
                    tagCounts[tag] = (tagCounts[tag] || 0) + 1;
                }
            });
        });
        
        // Render People List
        selectionPeopleList.innerHTML = '';
        const peopleKeys = sortedTags(Object.keys(peopleCounts), key => peopleCounts[key].shown);
        if (peopleKeys.length === 0) {
            replaceContent(selectionPeopleList, noneChip());
        } else {
            peopleKeys.forEach(key => {
                const { count, tags: spellings, shown: p } = peopleCounts[key];
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
        const tagKeys = sortedTags(Object.keys(tagCounts));
        if (tagKeys.length === 0) {
            replaceContent(selectionTagsList, noneChip());
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
                            photoAlreadyHas(photo, p.name, namesAPerson, state.people)
                            || peopleListHas(photoPeople, leaf, state.people);
                        if (!alreadyAdded) {
                            // A person who shares a name is counted, and applied, by their tag: not as the other Sam.
                            const known = state.people.ofText(p.name);
                            noteSuggestion(suggPeopleCounts, known && state.people.shared(known.name) ? known.tag : leaf, photo.path, p.score);
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
                        if (photoAlreadyHas(photo, leaf, namesAPerson, state.people)) return;
                        if (isPerson) {
                            const cleanLeaf = leafOf(leaf);
                            if (peopleListHas(photoPeople, cleanLeaf, state.people)) return;
                            const known = state.people.ofText(leaf);
                            noteSuggestion(suggPeopleCounts, known && state.people.shared(known.name) ? known.tag : cleanLeaf, photo.path, t.score);
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

/**
 * The selection panel for a selection in a library view. The page holds only the cards near the window, so what the
 * selection carries is counted by the server (tally.js).
 */
function showLibrarySelection() {
    // No Date Taken in a view (#675: a range across the library says nothing); the folders the selection is in instead.
    if (selectionDateGroup) selectionDateGroup.classList.add('hidden');
    if (selectionFoldersGroup) selectionFoldersGroup.classList.remove('hidden');
    if (selectionSuggestedPeopleList) selectionSuggestedPeopleList.innerHTML = '';
    if (selectionSuggestedTagsList) selectionSuggestedTagsList.innerHTML = '';
    // What the selection carries is counted by the server, 250 ms after the selection stops changing (tally.js); none, nothing.
    if (selectionCount() === 0) {
        clearTally();
        if (selectionPeopleList) selectionPeopleList.innerHTML = '';
        if (selectionTagsList) selectionTagsList.innerHTML = '';
        drawJumpNote('');
    } else {
        selectionTallied();
    }
    btnApplyRename.disabled = true;
    upper.updateFolderAutoApplyState();
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
    // A person who shares a name is keyed by their tag (what a click writes); shown by their label.
    const shownOf = name => (isPerson && state.people.ofTag(name) ? state.people.ofTag(name).name : name);
    const keys = sortedTags(Object.keys(counts), shownOf);
    if (keys.length === 0) {
        replaceContent(container, noneChip());
        return;
    }
    keys.forEach(name => {
        const { count, paths, score } = counts[name];
        const pct = Math.round(score * 100);
        const unsure = score < CONFIDENT_ENOUGH_TO_LOOK_SURE;

        const chip = document.createElement('span');
        chip.className = 'suggestion-chip' + (unsure ? ' suggestion-chip-unsure' : '');
        chip.style.cursor = 'pointer';
        chip.textContent = `${shownOf(name)} (${count}) · ${pct}%`;
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
 * One write of tags to many photos, in the photo write queue (edits.js): after every
 * write clicked before it, never beside it.
 *
 * Each click posted at once, so three quick clicks were three writes of the same
 * files at the same time on the server. Each planned from the files as they were
 * before the others, ExifTool refused a file another write had open, and one set of
 * tags was reported written, one failed, and one was gone from the files without a
 * word. In the queue they go in the order clicked, each says how it went -- a failed
 * one in an alert -- and the next goes on either way. `targets` are the photos as
 * they were when it was clicked. Resolves true when written.
 */
function queueBulkTags({ label, busy, failed, targets, add = [], remove = [], written, datalist = updateTagsDatalist }) {
    return queuePhotoWrite((entry) => {
        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = busy;
        return api.json('/api/photos/bulk-tags', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paths: targets, add_tags: add, remove_tags: remove })
        })
        .then(data => {
            if (!data.success) {
                // Stopped part-way: the photos before the one that failed are written,
                // and the page's records must say so.
                const done = Object.keys(data.written || {});
                if (done.length) {
                    targets.filter(path => done.some(d => samePath(d, path))).forEach(path => {
                        const photo = state.folderPhotos.find(p => p.path === path);
                        if (photo) written(photo);
                    });
                    updateSelectedThumbnailsCount();
                    renderFileList();
                    renderThumbnails();
                    datalist();
                    saveToLocalStorageCache();
                }
                throw new Error(data.error);
            }
            // A photo found damaged was skipped, nothing written to it: its record stays.
            const skipped = (data.skipped || []).map(each => each.path);
            // Each photo written is of the file as it is now: the stamp its next save names.
            const stamps = new Map(Object.entries(data.stamps || {}).map(([path, stamp]) => [pathKey(path), stamp]));
            const heldTags = new Map(Object.entries(data.written || {}).map(([path, held]) => [pathKey(path), held]));
            targets.filter(path => !skipped.some(s => samePath(s, path))).forEach(path => {
                const photo = state.folderPhotos.find(p => p.path === path);
                if (photo) {
                    takeWritten(photo, stamps.get(pathKey(path)), heldTags.get(pathKey(path)));
                    written(photo);
                }
            });
            noteSkipped(entry, data.skipped_damaged);
            updateSelectedThumbnailsCount();
            renderFileList();
            renderThumbnails();
            datalist();
            saveToLocalStorageCache();
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = data.file_only ? `Saved.${whereWritten(data)}` : 'Ready';
            // Photos whose files are gone were left out by the server.
            const gone = data.skipped_missing || 0;
            if (gone) statusText.textContent = `${gone.toLocaleString()} ${gone === 1 ? 'photo is' : 'photos are'} missing on disk and ${gone === 1 ? 'was' : 'were'} left out.${data.file_only ? whereWritten(data) : ''}`;
            return true;
        })
        .catch(err => {
            console.error(err);
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Error';
            entry.error = err.message;
            alert(`${failed}: ${err.message}`);
            return false;
        });
    }, label);
}

/**
 * May a bulk write over `count` photos go ahead? One over BULK_LIMIT is refused with the server's sentence; one
 * over BULK_CONFIRM_ABOVE asks first, naming the write -- "Add Trips/Coast to 3,412 photos?" -- since it runs
 * under the lock of every write to the library's files, with no progress, for as long as it takes (findings #535).
 */
export function confirmBulkWrite(what, count) {
    if (count > BULK_LIMIT) {
        alert(`Narrow the selection: bulk edits over ${BULK_LIMIT} photos arrive with the editing stage. ${count.toLocaleString()} are selected.`);
        return false;
    }
    if (count > BULK_CONFIRM_ABOVE) return confirm(`${what} ${count.toLocaleString()} photos?`);
    return true;
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
    if (!confirmBulkWrite(`Add ${tag} to`, targets.length)) return;
    return queueBulkTags({
        label: `Add ${tag} to ${targets.length} photo(s)`,
        busy: 'Applying tag...',
        failed: 'Error applying tag selection',
        targets,
        add: [tag],
        written: photo => {
            if (!photo.tags.includes(tag)) photo.tags.push(tag);
            if (isPerson) {
                // photo.people holds leaf names, not paths.
                const leaf = leafOf(tag);
                if (!photo.people) photo.people = [];
                if (!photo.people.includes(leaf)) photo.people.push(leaf);
            }
        },
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
    const targets = state.selectedThumbnails.slice();
    if (!confirmBulkWrite(`Remove ${tags.join(', ')} from`, targets.length)) return;
    return queueBulkTags({
        label: `Remove ${tags.join(', ')} from ${targets.length} photo(s)`,
        busy: 'Removing tag...',
        failed: 'Error removing tag selection',
        targets,
        remove: tags,
        written: photo => {
            photo.tags = photo.tags.filter(t => !tags.includes(t));
            if (isPerson && photo.people) {
                photo.people = photo.people.filter(p => !leaves.includes(leafOf(p).toLowerCase()));
            }
        },
    });
}

// Bulk Editing operations
export async function bulkAddPeopleToSelection() {
    // A library view's selection is photo ids and its edit a job (bulk-edit.js); a folder's is paths and a request.
    if (state.library) return addTypedToSelection(true);
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
    const targets = state.selectedThumbnails.slice();
    // Before a name is resolved: resolving may make a tree node, and a refused or cancelled write makes none.
    if (!confirmBulkWrite(`Add ${peopleList.join(', ')} to`, targets.length)) return;

    const resolvedPeople = [];
    for (const p of peopleList) {
        const resolved = await resolveTagOrPerson(p, true);
        if (resolved) {
            resolvedPeople.push(resolved);
        }
    }
    if (resolvedPeople.length === 0) return;

    const done = await queueBulkTags({
        label: `Add ${resolvedPeople.join(', ')} to ${targets.length} photo(s)`,
        busy: 'Adding people...',
        failed: 'Error bulk adding people',
        targets,
        add: resolvedPeople,
        written: photo => {
            if (!photo.people) photo.people = [];
            resolvedPeople.forEach(p => {
                if (!photo.tags.includes(p)) photo.tags.push(p);
                const leaf = leafOf(p);
                if (!photo.people.includes(leaf)) photo.people.push(leaf);
            });
        },
        datalist: updatePeopleDatalist,
    });
    // Only the text that was written: what was typed since stays.
    if (done && bulkAddPeopleInput.value.trim() === val) bulkAddPeopleInput.value = '';
    return done;
}

export async function bulkAddTagsToSelection() {
    if (state.library) return addTypedToSelection(false);
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
    const targets = state.selectedThumbnails.slice();
    if (!confirmBulkWrite(`Add ${tagsList.join(', ')} to`, targets.length)) return;

    const resolvedTags = [];
    for (const t of tagsList) {
        const resolved = await resolveTagOrPerson(t, false);
        if (resolved) {
            resolvedTags.push(resolved);
        }
    }
    if (resolvedTags.length === 0) return;

    const done = await queueBulkTags({
        label: `Add ${resolvedTags.join(', ')} to ${targets.length} photo(s)`,
        busy: 'Adding tags...',
        failed: 'Error bulk adding tags',
        targets,
        add: resolvedTags,
        written: photo => {
            resolvedTags.forEach(t => {
                if (!photo.tags.includes(t)) photo.tags.push(t);
            });
        },
    });
    // Only the text that was written: what was typed since stays.
    if (done && bulkAddTagsInput.value.trim() === val) bulkAddTagsInput.value = '';
    return done;
}


/** What a selection's list shows when it holds nothing. */
function noneChip() {
    return buildElement('span', { style: 'color: var(--text-muted); font-size: 12px; padding: 4px 0;', text: 'None' });
}
