// TagPup's page: suggestions -- asking for them, following their progress and an index's,
// showing them for a photo, and applying them.
import { api } from './common/api.js';
import { leafOf, photoAlreadyHas } from './common/vocabulary.js';
import { state } from './state.js';
import {
    btnFolderAutoApply, btnSuggestTags, btnSuggestTitleWand, indexProgressBar,
    indexProgressContainer, indexProgressText, inputPhotoTitle, statusDot, statusText,
    suggestedPeopleContainer, suggestedTagsContainer, suggestionsSection, suggestProgressBar,
    suggestProgressContainer, suggestProgressText
} from './elements.js';
import { setStatus } from './status.js';
import { saveToLocalStorageCache } from './cache.js';
import { fetchKnownTagsAndPeople, namesAPerson, resolveTagOrPerson } from './tags.js';
import { postPhotoMetadata, queuePhotoWrite, redrawIfShowing } from './edits.js';
import { isPhotoTagged, renderFileList, scanFolder } from './folder.js';
import { saveSingleTitle } from './photo.js';
import { recordUndo, snapshotPhotos } from './undo.js';
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

export function checkSuggestionsStatus(folderPath) {
    if (state.progressTimer) clearInterval(state.progressTimer);

    function queryProgress() {
        api.json(`/api/folder/suggest-status?path=${encodeURIComponent(folderPath)}`)
            .then(data => {
                if (data.status === 'preparing' || data.status === 'running') {
                    suggestProgressContainer.classList.remove('hidden');
                    const total = data.total || 0;
                    const completed = data.completed || 0;
                    
                    if (data.status === 'preparing' || total === 0) {
                        suggestProgressBar.style.width = `0%`;
                        suggestProgressText.textContent = `Preparing AI models & scanning folder...`;
                    } else {
                        const pct = Math.round((completed / total) * 100);
                        suggestProgressBar.style.width = `${pct}%`;
                        suggestProgressText.textContent = `Processing: ${completed} / ${total} (${pct}%)`;
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

                    statusDot.className = 'status-indicator-dot';
                    statusText.textContent = 'Ready';
                }
                else {
                    // 'idle' (never run), 'not_started', 'error', or anything unexpected:
                    // stop polling rather than spinning on a status we cannot advance.
                    clearInterval(state.progressTimer);
                    suggestProgressContainer.classList.add('hidden');
                    updateSuggestButtonState(data.status);
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
        (list || []).filter(item => !photoAlreadyHas(photo, item[key], namesAPerson));

    const people = outstanding(sugg.people, 'name');
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
        items.forEach(item => {
            const name = item[key];
            const pct = Math.round((item.score || 0) * 100);
            const chip = document.createElement('span');
            chip.className = 'suggestion-chip';
            chip.style.cursor = 'pointer';
            chip.textContent = pct ? `${name} · ${pct}%` : name;
            chip.title = `Click to add ${name} to this photo.`;
            chip.addEventListener('click', () => applySuggestedTagDirect(name, isPerson, photoPath));
            container.appendChild(chip);
        });
    }

    fill(suggestedPeopleContainer, people, 'name', true);
    fill(suggestedTagsContainer, tags, 'tag', false);
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

    return queuePhotoWrite(async () => {
        const resolved = await resolveTagOrPerson(tagName, isPerson);
        if (!resolved) return true;

        // Say so rather than doing nothing. A click that silently no-ops reads as
        // a broken button, which is how this was reported.
        if (photoAlreadyHas(photo, resolved, namesAPerson)) {
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

    return queuePhotoWrite(async () => {
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
            if (photoAlreadyHas(photo, resolved, namesAPerson)) continue;
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

    const before = snapshotPhotos(state.selectedThumbnails);
    setStatus('busy', `Applying suggestions to ${state.selectedThumbnails.length} photo(s)...`);

    api.json('/api/folder/auto-apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
            folder_path: folder, 
            photo_paths: state.selectedThumbnails,
            // Apply everything the panel is showing. A button called Apply All
            // that applied 141 of 153 suggestions and left 12 on screen read as
            // a failure, and the 12 it skipped were indistinguishable from the
            // ones it wrote. What is offered is what gets applied.
            threshold: 0.0 
        })
    })
    .then(data => {
        if (data.success) {
            recordUndo({
                label: `auto-apply to ${before.length} photo(s)`,
                photos: before,
            });
            setStatus('ready',
                `Suggestions applied to ${before.length} photo(s) \u2014 Ctrl+Z to undo`);
            scanFolder(true); // Rescan folder to load updated tags
        } else {
            throw new Error(data.error);
        }
    })
    .catch(err => {
        console.error(err);
        // A failure that would otherwise pass unnoticed still earns a modal.
        setStatus('error', 'Applying suggestions failed', { transient: false });
        alert("Error applying suggestions: " + err.message);
    });
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
                if (!photoAlreadyHas(photo, p.name, namesAPerson)
                    && !photoPeople.some(n => leafOf(n).toLowerCase() === leaf.toLowerCase())) {
                    hasSomethingToApply = true;
                    break;
                }
            }
        }
        if (hasSomethingToApply) break;
    }
    
    btnFolderAutoApply.disabled = !hasSomethingToApply;
}
