// TagPup's page: suggestions -- asking for them, following their progress and an index's,
// showing them for a photo, and applying them.
import { api } from './common/api.js';
import { attachPersonFaces } from './common/person-faces.js';
import { samePath } from './common/paths.js';
import { leafOf, peopleListHas, personLabelOf, photoAlreadyHas, sortedTags } from './common/vocabulary.js';
import { state } from './state.js';
import {
    btnFolderAutoApply, btnSuggestCancel, btnSuggestTags, btnSuggestTitleWand, indexProgressBar,
    indexProgressContainer, indexProgressText, inputPhotoTitle, statusDot, statusText,
    suggestedPeopleContainer, suggestedTagsContainer, suggestionsSection, suggestProgressBar,
    suggestProgressContainer, suggestProgressText
} from './elements.js';
import { setStatus } from './status.js';
import { saveToLocalStorageCache } from './cache.js';
import { fetchKnownTagsAndPeople, namesAPerson, resolveTagOrPerson } from './tags.js';
import { postPhotoMetadata, queuePhotoWrite, queueWriteOf, redrawIfShowing } from './edits.js';
import { isJustLooking, libraryName } from './looking.js';
import { isPhotoTagged, renderFileList, scanFolder } from './folder.js';
import { saveSingleTitle } from './photo.js';
import { recordUndo, snapshotPhotos } from './undo.js';
import { noteSkipped } from './write-queue.js';
import { updateSelectedThumbnailsCount } from './selection.js';

export function updateSuggestButtonState(status = null) {
    if (!state.scannedFolder || state.folderPhotos.length === 0) {
        btnSuggestTags.disabled = true;
        return;
    }
    if (status === 'preparing' || status === 'running') {
        btnSuggestTags.disabled = true;
        return;
    }
    // What is on the page was found in memory, and the library holds the folder now: the run that saves
    // is the next Suggest, whatever the page holds already.
    if (state.suggestionsInMemory && samePath(state.suggestionsInMemory, state.scannedFolder) && !isJustLooking()) {
        btnSuggestTags.disabled = false;
        return;
    }
    const hasUnprocessed = state.folderPhotos.some(photo => !state.folderSuggestions[photo.path]);
    btnSuggestTags.disabled = !hasUnprocessed;
}

// Suggest Tags operations
export function startSuggestions() {
    const path = state.scannedFolder;
    if (!path) return;

    statusDot.className = 'status-indicator-dot busy';
    statusText.textContent = 'Suggesting...';
    btnSuggestTags.disabled = true;
    btnFolderAutoApply.disabled = true;

    api.json('/api/folder/suggest-start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_path: path })
    })
    .then(data => {
        if (data.success) {
            state.suggestionsInMemory = data.in_memory ? path : null;
            suggestProgressContainer.classList.remove('hidden');
            checkSuggestionsStatus(path);
        } else {
            updateSuggestButtonState();
            throw new Error(data.error);
        }
    })
    .catch(err => {
        updateSuggestButtonState();
        console.error(err);
        statusDot.className = 'status-indicator-dot';
        statusText.textContent = 'Error';
        alert("Error starting suggestions: " + err.message);
    });
}

/**
 * Stop the folder's Suggest. While it waits -- for the folder's index, or for the graphics card another
 * program has -- it stops at once; under way, once the photos in hand are done. What it kept stays, and
 * the next poll says "cancelled".
 */
export function cancelSuggestions() {
    const path = state.scannedFolder;
    if (!path) return;
    btnSuggestCancel.disabled = true;
    api.json('/api/folder/suggest-cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_path: path })
    })
    .then(data => {
        if (data.cancelled) suggestProgressText.textContent = 'Cancelling...';
        else btnSuggestCancel.disabled = false;
    })
    .catch(err => {
        btnSuggestCancel.disabled = false;
        alert('Could not cancel Suggest: ' + err.message);
    });
}

// There is no Index button any more. Adding a folder to a database is TagTuner's
// job -- it queues folders and survives a page refresh -- and the other half of
// what this button was for, telling the index about keywords TagPup had just
// written, is done by the writers themselves now. checkIndexingStatus stays, so
// an index started elsewhere still shows its progress here.

export function checkIndexingStatus(folderPath) {
    if (state.indexProgressTimer) clearInterval(state.indexProgressTimer);

    function queryProgress() {
        api.json(`/api/folder/index-status?path=${encodeURIComponent(folderPath)}`)
            .then(data => {
                if (state.library) return;   // a reply that came after a library view opened shows nothing
                if (data.status === 'running') {
                    indexProgressContainer.classList.remove('hidden');
                    const pct = data.percent || 0;
                    indexProgressBar.style.width = `${pct}%`;
                    indexProgressText.textContent = `${data.message || 'Indexing...'}`;
                }
                else if (data.status === 'completed') {
                    clearInterval(state.indexProgressTimer);
                    const wasVisible = !indexProgressContainer.classList.contains('hidden');
                    indexProgressContainer.classList.add('hidden');
                    
                    
                    if (wasVisible) {
                        fetchKnownTagsAndPeople();
                        scanFolder(true);
                        alert(data.message || "Folder successfully added to the database and indexed!");
                    }
                    
                    statusDot.className = 'status-indicator-dot';
                    statusText.textContent = 'Ready';
                }
                else if (data.status === 'failed') {
                    clearInterval(state.indexProgressTimer);
                    const wasVisible = !indexProgressContainer.classList.contains('hidden');
                    indexProgressContainer.classList.add('hidden');
                    
                    updateSuggestButtonState();
                    
                    if (wasVisible) {
                        alert("Indexing failed: " + (data.message || 'Unknown error'));
                    }
                    
                    statusDot.className = 'status-indicator-dot';
                    statusText.textContent = 'Error';
                }
            })
            .catch(err => {
                console.error("Error polling indexing progress:", err);
            });
    }

    queryProgress();
    state.indexProgressTimer = setInterval(queryProgress, 1000);
}

/**
 * The server no longer holds what a Suggest that only looked found (it is kept in memory, for a while): it
 * let it go after a while, or the folder was added to the library and it was dropped. The page says which,
 * forgets the copy it held -- here and in this browser's cache -- and Suggest is run again.
 */
export function suggestionsLetGo() {
    state.suggestionsInMemory = null;
    state.folderSuggestions = {};
    saveToLocalStorageCache();
    btnFolderAutoApply.disabled = true;
    renderFileList();
    updateSelectedThumbnailsCount();
    updateSuggestButtonState();
    if (state.activePhotoPath) renderSuggestionsPanel(state.activePhotoPath);
    // The library holds the folder (it was added: what the membership says, and Add sets): not a thing gone
    // wrong, and the run that saves is the next Suggest.
    const held = state.folderMembership && !(state.folderMembership.photos_not_held > 0);
    if (held) {
        setStatus('ready', 'The folder was added: Suggest again to save its suggestions', { transient: false });
    } else {
        setStatus('error', 'The analysis was let go after a while; run Suggest again', { transient: false });
    }
}

export function checkSuggestionsStatus(folderPath) {
    if (state.progressTimer) clearInterval(state.progressTimer);

    function queryProgress() {
        api.json(`/api/folder/suggest-status?path=${encodeURIComponent(folderPath)}`)
            .then(data => {
                if (state.library) return;   // likewise: its progress and suggestions belong to a folder
                // Where what it found is kept: in memory (a folder the library does not hold), or in the library.
                if (data.in_memory) {
                    state.suggestionsInMemory = folderPath;
                } else if (data.status === 'idle'
                    && state.suggestionsInMemory && samePath(state.suggestionsInMemory, folderPath)) {
                    // Nothing is shown that the server no longer holds.
                    if (Object.keys(state.folderSuggestions).length > 0) {
                        clearInterval(state.progressTimer);
                        suggestProgressContainer.classList.add('hidden');
                        suggestionsLetGo();
                        return;
                    }
                    state.suggestionsInMemory = null;
                } else if (state.suggestionsInMemory && samePath(state.suggestionsInMemory, folderPath)
                    && data.status !== 'idle') {
                    state.suggestionsInMemory = null;
                }
                if (data.status === 'preparing' || data.status === 'running') {
                    suggestProgressContainer.classList.remove('hidden');
                    const total = data.total || 0;
                    const completed = data.completed || 0;
                    if (data.message !== 'Cancelling...') btnSuggestCancel.disabled = false;
                    
                    if (data.status === 'preparing' || total === 0) {
                        suggestProgressBar.style.width = `0%`;
                        // What it waits for, when it waits: the folder's index, or the graphics card and who has it.
                        suggestProgressText.textContent = data.message || `Preparing AI models & scanning folder...`;
                    } else {
                        const pct = Math.round((completed / total) * 100);
                        suggestProgressBar.style.width = `${pct}%`;
                        suggestProgressText.textContent = data.message
                            ? `${completed} / ${total}: ${data.message}`
                            : `Processing: ${completed} / ${total} (${pct}%)`;
                    }
                    
                    // Merge progressive suggestions
                    state.folderSuggestions = data.suggestions || {};
                    renderFileList(); // updates tags badge dynamically
                    updateSelectedThumbnailsCount();
                    saveToLocalStorageCache();
                } 
                else if (data.status === 'completed') {
                    clearInterval(state.progressTimer);
                    suggestProgressContainer.classList.add('hidden');
                    
                    state.folderSuggestions = data.suggestions || {};
                    btnFolderAutoApply.disabled = false;
                    
                    renderFileList();
                    // The selection panel shows suggestions too, and nothing
                    // refreshed it when they arrived. They appeared on the next
                    // unrelated click instead, which read as though that click
                    // had taken tags off the photos.
                    updateSelectedThumbnailsCount();
                    saveToLocalStorageCache();
                    
                    updateSuggestButtonState('completed');
                    
                    // If active photo selected, refresh suggestions pane
                    if (state.activePhotoPath) {
                        renderSuggestionsPanel(state.activePhotoPath);
                    }

                    sayWhereTheyAre(data);
                }
                else {
                    // 'idle' (never run), 'not_started', 'error', or anything unexpected:
                    // stop polling rather than spinning on a status we cannot advance.
                    clearInterval(state.progressTimer);
                    suggestProgressContainer.classList.add('hidden');
                    updateSuggestButtonState(data.status);
                    if (data.status === 'cancelled') {
                        setStatus('ready', 'Suggest was cancelled; what it suggested before is kept.', { transient: false });
                    }
                    if (data.status === 'error') {
                        statusDot.className = 'status-indicator-dot';
                        statusText.textContent = 'Error';
                        if (data.message) {
                            console.error("Suggestions failed:", data.message);
                        }
                    }
                }
            })
            .catch(err => {
                console.error("Error polling suggestions progress:", err);
            });
    }

    // Query once immediately, then poll
    queryProgress();
    state.progressTimer = setInterval(queryProgress, 1500);
}

/**
 * A finished run's closing line. In a folder the library does not hold the run kept what it found
 * in memory (`in_memory`), and the line says nothing was added; what the library had too little of
 * to compare with, and the photos that could not be analysed, are in `notes`.
 */
function sayWhereTheyAre(data) {
    const notes = Array.isArray(data.notes) ? data.notes : [];
    if (!data.in_memory) {
        if (notes.length) {
            setStatus('ready', notes.join(' '), { transient: false });
        } else {
            statusDot.className = 'status-indicator-dot';
            statusText.textContent = 'Ready';
        }
        return;
    }
    const name = libraryName();
    setStatus('ready', [`Analysed against ${name}; nothing was added to ${name}.`, ...notes].join(' '),
        { transient: false });
}

// Render suggestions box in right pane for active photo
export function renderSuggestionsPanel(photoPath) {
    const sugg = state.folderSuggestions[photoPath];
    if (!sugg) {
        suggestionsSection.classList.add('hidden');
        return;
    }

    suggestionsSection.classList.remove('hidden');

    // Configure Title Wand
    if (sugg.title) {
        btnSuggestTitleWand.disabled = false;
        btnSuggestTitleWand.title = `Suggested Title: "${sugg.title}"`;
        btnSuggestTitleWand.setAttribute('data-suggested-title', sugg.title);
    } else {
        btnSuggestTitleWand.disabled = true;
        btnSuggestTitleWand.title = "No AI title suggested";
        btnSuggestTitleWand.removeAttribute('data-suggested-title');
    }

    // Only what is not already on the photo.
    //
    // This listed every suggestion the analysis produced, applied or not, so after
    // Apply All the box sat there repeating the tags it had just written back at
    // you. A suggestion you have taken is not a suggestion; it is a tag, and it is
    // already shown as one a few inches above.
    const photo = state.folderPhotos.find(p => p.path === photoPath);
    const outstanding = (list, key) =>
        (list || []).filter(item => !photoAlreadyHas(photo, item[key], namesAPerson, state.people));

    // A name two people have is offered as one chip for each (replacing the question "which folder?"), and what is already on
    // the photo is taken out after that, so the Sam the photo has is not offered and the other Sam is.
    const people = outstanding(personChips(sugg.people), 'name');
    const tags = outstanding(sugg.tags, 'tag');

    // Nothing left to act on: the box would be a heading over two empty lists.
    if (people.length === 0 && tags.length === 0 && !sugg.title) {
        suggestionsSection.classList.add('hidden');
        return;
    }

    function fill(container, items, key, isPerson) {
        container.innerHTML = '';
        const group = container.closest('.suggestion-item');
        // Hide the half that has nothing rather than label an empty row.
        if (group) group.classList.toggle('hidden', items.length === 0);
        // Ranked by how sure the analysis was, the most sure first; the alphabet breaks a tie.
        const shownOf = item => (isPerson ? personLabelOf(item, key) : item[key]);
        sortedTags(items, shownOf, { rank: item => item.score }).forEach(item => {
            const name = item[key];
            const shown = shownOf(item);
            const pct = Math.round((item.score || 0) * 100);
            const chip = document.createElement('span');
            chip.className = 'suggestion-chip';
            chip.style.cursor = 'pointer';
            chip.textContent = pct ? `${shown} · ${pct}%` : shown;
            chip.title = `Click to add ${shown} to this photo.`;
            if (isPerson) {
                chip.tabIndex = 0;      // focusable, so the keyboard sees their faces as well
                attachPersonFaces(chip, item.person && item.person.id !== null && item.person.id !== undefined ? item.person : name);
            }
            chip.addEventListener('click', () => applySuggestedTagDirect(name, isPerson, photoPath));
            container.appendChild(chip);
        });
    }

    fill(suggestedPeopleContainer, people, 'name', true);
    fill(suggestedTagsContainer, tags, 'tag', false);
}

/**
 * The people a run suggested, a name that two people have as one suggestion for each of them (the tag of each as its `name`, so a
 * click writes exactly that person). A suggestion that names one person, or whose people the page has not read, stays as it is.
 */
export function personChips(list) {
    const chips = [];
    for (const item of list || []) {
        const person = item.person;
        const called = person ? [] : state.people.called(item.name);
        if (called.length > 1) called.forEach(each => chips.push({ ...item, name: each.tag, person: each }));
        else chips.push(item);
    }
    return chips;
}

/**
 * Add a suggested tag or person to the open photo, as the tag they are filed under.
 *
 * This used to reduce whatever it was given to its leaf and write that. For a
 * keyword it threw away the level the taxonomy had just resolved; for a person it
 * wrote the bare name that the keyword convention does not allow, beside the
 * "People/<name>" the photo was already carrying. Clicking a face TagPup had
 * matched therefore added that person a second time, in the wrong form.
 *
 * Suggestions arrive as leaf names, so the name is resolved to its taxonomy path
 * first. The modal only appears where the name is genuinely ambiguous, which for a
 * recognised face means never: they are in the taxonomy already.
 */
export function applySuggestedTagDirect(tagName, isPerson, forPath = state.activePhotoPath) {
    // A chip or face card belongs to the photo it was drawn for. One left over
    // from another photo must not write that photo's suggestion to this one.
    const path = state.activePhotoPath;
    if (!path || forPath !== path) return;
    const photo = state.folderPhotos.find(p => p.path === path);
    if (!photo) return;

    return queueWriteOf(path, async () => {
        const resolved = await resolveTagOrPerson(tagName, isPerson);
        if (!resolved) return true;

        // Say so rather than doing nothing. A click that silently no-ops reads as
        // a broken button, which is how this was reported.
        if (photoAlreadyHas(photo, resolved, namesAPerson, state.people)) {
            setStatus('ready', `${leafOf(resolved)} is already on this photo`);
            return true;
        }
        const updatedTags = [...(photo.tags || []), resolved];
        const leaf = leafOf(resolved);

        setStatus('busy', 'Saving...');
        try {
            await postPhotoMetadata(photo, { tags: updatedTags });
        } catch (err) {
            console.error(err);
            setStatus('error', 'Error');
            alert("Error adding suggested tag: " + err.message);
            return false;
        }
        photo.tags = updatedTags;
        if (isPerson) {
            if (!photo.people) photo.people = [];
            if (!photo.people.includes(leaf)) photo.people.push(leaf);
        }
        // The suggestion has been taken, so it is no longer a suggestion.
        redrawIfShowing(photo);
        setStatus('ready', 'Ready');
        saveToLocalStorageCache();
        return true;
    });
}

export function applySuggestedTitle() {
    const path = state.activePhotoPath;
    if (!path) return;
    const photo = state.folderPhotos.find(p => p.path === path);
    const sugg = state.folderSuggestions[path];
    if (!photo || !sugg || !sugg.title) return;

    inputPhotoTitle.value = sugg.title;
    saveSingleTitle();
}

export async function applyAllSingleSuggestions() {
    const path = state.activePhotoPath;
    if (!path) return;
    const photo = state.folderPhotos.find(p => p.path === path);
    const sugg = state.folderSuggestions[path];
    if (!photo || !sugg) return;

    return queueWriteOf(path, async () => {
        // Resolve each suggestion to the tag it is filed under before writing it,
        // and skip anyone the photo already names -- as it is now, after whatever
        // was queued ahead. Applying the list raw wrote bare leaves.
        const wanted = [
            ...(sugg.tags || []).map(t => ({ name: t.tag, isPerson: false })),
            ...(sugg.people || []).map(p => ({ name: p.name, isPerson: true })),
        ];
        const resolvedSuggestions = [];
        for (const item of wanted) {
            const resolved = await resolveTagOrPerson(item.name, item.isPerson);
            if (!resolved) continue;
            if (photoAlreadyHas(photo, resolved, namesAPerson, state.people)) continue;
            if (resolvedSuggestions.includes(resolved)) continue;
            resolvedSuggestions.push(resolved);
        }
        if (resolvedSuggestions.length === 0) return true;

        // The suggested title is not applied automatically.
        const updatedTags = Array.from(new Set([...(photo.tags || []), ...resolvedSuggestions]));

        setStatus('busy', 'Saving...');
        try {
            await postPhotoMetadata(photo, { tags: updatedTags });
        } catch (err) {
            console.error(err);
            setStatus('error', 'Error');
            alert("Error applying all suggestions: " + err.message);
            return false;
        }
        photo.tags = updatedTags;
        if (!photo.people) photo.people = [];
        resolvedSuggestions.filter(namesAPerson).forEach(t => {
            const leaf = leafOf(t);
            if (!photo.people.includes(leaf)) photo.people.push(leaf);
        });
        redrawIfShowing(photo);
        setStatus('ready', 'Ready');
        saveToLocalStorageCache();
        return true;
    });
}

export function applyFolderSuggestionsLevel() {
    const folder = state.scannedFolder;
    if (!folder || state.selectedThumbnails.length === 0) return;

    // Say what will be written, and to how many photos that already carry work,
    // before writing it. The common mistake is not misreading the button -- it is
    // having the wrong selection, and a count of photos alone does not surface
    // that. This is the last point at which it costs nothing.
    const alreadyTagged = state.selectedThumbnails.filter(p => {
        const photo = state.folderPhotos.find(x => x.path === p);
        return photo && isPhotoTagged(photo);
    }).length;
    const scope = [
        `Auto-apply AI suggestions to ${state.selectedThumbnails.length} selected photo(s)?`,
        '',
        alreadyTagged
            ? `${alreadyTagged} of them already have tags. Suggestions are added to what is there; nothing is removed.`
            : 'None of them are tagged yet.',
        '',
        'This writes keywords into the photo files. Undo restores the previous tags for this session only.',
    ].join('\n');
    if (!confirm(scope)) return;

    // The photos selected when it was clicked. It waits in the photo write queue
    // (edits.js) behind every write clicked before it, as a bulk tag write does
    // (selection.js), and the photos are snapshotted for undo as those left them.
    return queueApplyAll(folder, state.selectedThumbnails.slice(), []);
}

/**
 * One Apply All of `targets`, in the write queue. `prior`: the undo record of an attempt that failed part-way, when this
 * is its Retry -- the photos it wrote, as they were before it. Kept on the failed entry (`undoSoFar`), so that the undo
 * recorded now covers both attempts: this one snapshots the photos after the first attempt wrote some, and its record
 * alone would leave those without a way back (findings #879).
 */
function queueApplyAll(folder, targets, prior) {
    return queuePhotoWrite((entry) => {
        const before = snapshotPhotos(targets);
        entry.undoSoFar = prior;
        setStatus('busy', `Applying suggestions to ${targets.length} photo(s)...`);
        return api.json('/api/folder/auto-apply', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                folder_path: folder,
                photo_paths: targets,
                // Apply everything the panel is showing. A button called Apply All
                // that applied 141 of 153 suggestions and left 12 on screen read as
                // a failure, and the 12 it skipped were indistinguishable from the
                // ones it wrote. What is offered is what gets applied.
                threshold: 0.0
            })
        })
        .then(data => {
            if (!data.success) {
                // Stopped part-way: the reply names the photos it wrote, and undo is of those (findings #391).
                // One that wrote none leaves the operation before it to Ctrl+Z.
                const kept = recordAutoApplyUndo(before, data.written, false, prior);
                entry.undoSoFar = kept;
                entry.error = kept.length ? `${data.error} (${kept.length} written: Ctrl+Z takes them back)` : data.error;
                throw new Error(data.error);
            }
            recordAutoApplyUndo(before, data.written, true, prior);
            // A photo found damaged was skipped, nothing written to it (write-queue.js).
            const skipped = data.skipped_damaged || 0;
            noteSkipped(entry, skipped);
            setStatus('ready', `Suggestions applied to ${before.length - skipped} photo(s)`
                + (skipped ? `, ${skipped} skipped: damaged` : '') + ' \u2014 Ctrl+Z to undo');
            scanFolder(true); // Rescan folder to load updated tags
            return true;
        })
        .catch(err => {
            console.error(err);
            // What a Suggest that only looked found is kept in the server's memory for a while: gone, nothing
            // was written, and it is said so rather than shown as a failure of the write.
            if (state.suggestionsInMemory && /No suggestions found/.test(err.message || '')) {
                suggestionsLetGo();
                entry.error = 'The analysis was let go';
                return false;
            }
            // Some photos may have been written before the one that failed: the
            // folder is read again, so the page's records say what the files hold.
            scanFolder(true);
            // A failure that would otherwise pass unnoticed still earns a modal.
            setStatus('error', 'Applying suggestions failed', { transient: false });
            entry.error = entry.error || err.message;
            alert("Error applying suggestions: " + err.message);
            return false;
        });
    }, `Apply All suggestions (${targets.length} photos)`, (failed) => queueApplyAll(folder, targets, failed.undoSoFar || []));
}

/**
 * Undo's record of an Apply All, built from the REPLY's `written` (path -> the tags the file holds now), never
 * from what was attempted: undo takes back the difference (undo.js). `all`: a finished write, which records
 * every photo it was asked for (those it left alone differ by nothing); else only the photos named. Returns how
 * the photos the record holds ([] if it recorded nothing). `prior`: the photos an earlier attempt wrote, kept as they
 * were before it, with the tags this attempt's reply says they hold now.
 */
function recordAutoApplyUndo(before, writtenByPath, all, prior = []) {
    const written = Object.entries(writtenByPath || {});
    const named = before.filter(photo => written.some(([path]) => samePath(path, photo.path)));
    const photos = (all ? before : named).map(photo => {
        const now = written.find(([path]) => samePath(path, photo.path));
        return { ...photo, after: now ? now[1] : photo.before };
    });
    const merged = prior.map(earlier => {
        const again = photos.find(photo => samePath(photo.path, earlier.path));
        return again ? { ...earlier, after: again.after } : earlier;
    }).concat(photos.filter(photo => !prior.some(earlier => samePath(earlier.path, photo.path))));
    // Nothing written this time: the record that stands (the earlier attempt's, or none) is left to Ctrl+Z.
    if (!photos.length) return prior;
    recordUndo({ label: `auto-apply to ${merged.length} photo(s)`, photos: merged });
    return merged;
}

export function updateFolderAutoApplyState() {
    if (!state.scannedFolder || state.selectedThumbnails.length === 0) {
        btnFolderAutoApply.disabled = true;
        return;
    }
    
    let hasSomethingToApply = false;
    
    for (let path of state.selectedThumbnails) {
        const sugg = state.folderSuggestions[path];
        if (!sugg) continue;
        
        const photo = state.folderPhotos.find(p => p.path === path);
        if (!photo) continue;
        
        const currentTags = photo.tags || [];
        const photoPeople = photo.people || [];

        // Check general tags suggestions
        if (sugg.tags) {
            for (let t of sugg.tags) {
                if (!currentTags.includes(t.tag)) {
                    hasSomethingToApply = true;
                    break;
                }
            }
        }
        if (hasSomethingToApply) break;

        // Check people suggestions. A person the photo already names under their
        // path is nothing to apply, so compare by leaf rather than by spelling --
        // otherwise the button offers to add someone who is already there.
        if (sugg.people) {
            for (let p of sugg.people) {
                const leaf = leafOf(p.name);
                if (!photoAlreadyHas(photo, p.name, namesAPerson, state.people)
                    && !peopleListHas(photoPeople, leaf, state.people)) {
                    hasSomethingToApply = true;
                    break;
                }
            }
        }
        if (hasSomethingToApply) break;
    }
    
    btnFolderAutoApply.disabled = !hasSomethingToApply;
}
