// TagPup's page: suggestions -- asking for them, following their progress and an index's,
// showing them for a photo, and applying them.
import { api } from './common/api.js';
import { attachPersonFaces } from './common/person-faces.js';
import { pressable } from './common/press.js';
import { baseName, isUnder, pathKey, samePath } from './common/paths.js';
import { leafOf, peopleListHas, personLabelOf, photoAlreadyHas, sortedTags } from './common/vocabulary.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import {
    btnApplyAllSingleSugg, btnFolderAutoApply, btnSuggestCancel, btnSuggestTags, btnSuggestTitleWand, indexProgressBar,
    indexProgressContainer, indexProgressText, inputPhotoTitle, statusDot, statusText,
    suggestedPeopleContainer, suggestedTagsContainer, suggestionsSection, suggestProgressBar,
    suggestProgressContainer, suggestProgressText
} from './elements.js';
import { setStatus } from './status.js';
import { forgetScansHolding, photosGone, recordsWritten, saveToLocalStorageCache } from './cache.js';
import { namesAPerson, resolveTagOrPerson } from './tags.js';
import { shownTags } from './pending.js';
import { photoChangedOnDisk, queuePhotoWrite, queueWriteOf, redrawIfShowing } from './edits.js';
import { postPhotoMetadata } from './common/photo-write.js';
import { isJustLooking, libraryName } from './looking.js';
import { countSaid, isPhotoTagged, renderFileList, scanFolder } from './folder.js';
import { saveSingleTitle } from './photo.js';
import { recordUndo, snapshotPhotos } from './undo.js';
import { updateSelectedThumbnailsCount } from './selection.js';
import { selectedRecords } from './selected.js';

/**
 * A photo the folder's run has suggestions for: an entry, and not one the run could not read (the server's _succeeded:
 * the next Suggest tries it again). The Suggest button, the list's row and the grid's card ask this.
 */
export function suggested(entry) {
    return Boolean(entry) && !entry.error;
}

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
    // A photo the run could not read is tried again by the next (the server's _succeeded): not done.
    const hasUnprocessed = state.folderPhotos.some(photo => !suggested(state.folderSuggestions[photo.path]));
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

// ---- Following the library's indexes -------------------------------------------------------------------------------
// The page follows every folder the library's queue indexes (GET /api/folder/index-active, as TagTuner's indexing.js
// reads it), whoever queued it -- Manage folders, Add, TagTuner, the watcher's sync -- whichever folder or view is open,
// from the first ask until the queue is empty; then it asks no more until there is a reason to: the page opening, a
// folder opening, folders queued here. A folder that leaves the queue has ended: its own status (index-status) says how,
// the status line keeps the index's closing sentence, and what it changed is read again (upper.indexEnded).

/** How often the queue is asked while something is queued or being indexed. */
const INDEX_POLL_MS = 1000;
/** Asks in a row with no answer after which the page stops asking until the next reason to: a server that is not
 * answering is not asked once a second for ever, and nothing is said of an index whose end was not seen. */
const INDEX_GIVE_UP = 5;
/** What the queue says of a folder nobody has asked it about (tagpup/jobs/indexing.py READY): no index of it to speak of. */
const NOTHING_TO_SAY = 'Ready';

/**
 * Ask what the library indexes now, and follow it to its end. `queued`: folders just queued here (Manage folders, Add),
 * followed even when their index ends before the next answer could list them. Asked again while an ask is out, it asks
 * once more when that one is in, never two at once: rapid folder switches send one ask after another, not a pile.
 */
export function checkIndexingStatus(queued = []) {
    const follow = state.indexFollow;
    forgetAnotherLibrary(follow);
    for (const folder of queued || []) {
        if (!folder) continue;
        follow.known.set(pathKey(folder), folder);
        follow.counted.add(pathKey(folder));
    }
    follow.failures = 0;
    if (follow.asking) {
        follow.again = true;
        return;
    }
    clearTimeout(follow.timer);
    follow.timer = null;
    askIndexes();
}

/** What the page followed of another library is not this one's (a page is one library; its address moved on). */
function forgetAnotherLibrary(follow) {
    const library = libraryName();
    if (follow.library === library) return;
    follow.library = library;
    follow.known.clear();
    follow.counted.clear();
    follow.ended = 0;
    follow.failures = 0;
}

function askIndexes() {
    const follow = state.indexFollow;
    const library = follow.library;
    follow.asking = true;
    api.fetch('/api/folder/index-active')
        .then(res => (res && res.ok ? res.json() : null))
        .then(data => {
            if (libraryName() !== library) return false;   // another library's queue: dropped
            if (!data || typeof data !== 'object' || Array.isArray(data)) return false;
            return heardIndexes(data, library);
        })
        .catch(err => {
            console.error('Error asking what the library indexes:', err);
            return false;
        })
        .then(heard => {
            follow.asking = false;
            if (libraryName() !== library) return;
            follow.failures = heard ? 0 : follow.failures + 1;
            if (follow.again) {
                follow.again = false;
                askIndexes();
                return;
            }
            if (follow.known.size && follow.failures < INDEX_GIVE_UP) {
                follow.timer = setTimeout(askIndexes, INDEX_POLL_MS);
                return;
            }
            // Nothing queued, or no answer for a while: no more asks, and no bar claiming an index goes on. What is still
            // known is followed again at the next reason to ask.
            follow.timer = null;
            if (!follow.known.size) {
                follow.counted.clear();
                follow.ended = 0;
            }
            state.indexPercent = null;
            indexProgressContainer.classList.add('hidden');
        });
}

/**
 * One answer of the queue: the folders it lists are followed; each the page knew of that it no longer lists has ended,
 * and its status says how (asked for each, and a status that could not be read keeps it followed). True when every
 * read answered.
 */
function heardIndexes(data, library) {
    const follow = state.indexFollow;
    const running = (Array.isArray(data.active) ? data.active : []).filter(job => job && job.folder);
    const waiting = (Array.isArray(data.queued) ? data.queued : []).filter(job => job && job.folder);
    const listed = new Map([...running, ...waiting].map(job => [pathKey(job.folder), job.folder]));
    const gone = [...follow.known].filter(([key]) => !listed.has(key));
    for (const [key, folder] of listed) {
        follow.known.set(key, folder);
        follow.counted.add(key);
    }
    showIndexing(running, waiting);
    return Promise.all(gone.map(([key, folder]) => api.json(`/api/folder/index-status?path=${encodeURIComponent(folder)}`)
        .then(status => ({ key, folder, status }), () => ({ key, folder, status: null }))))
        .then(ends => {
            if (libraryName() !== library) return false;
            let answered = true;
            const finished = [];
            for (const { key, folder, status } of ends) {
                if (!status || typeof status.status !== 'string') {
                    answered = false;
                    continue;
                }
                // Queued again between the two reads (a run once more for files that arrived): still followed.
                if (status.status === 'queued' || status.status === 'running') continue;
                follow.known.delete(key);
                follow.ended += 1;
                finished.push({ folder, status });
            }
            // One answer, one refresh: a sync's batch lists each folder of it and ends them all at once.
            indexesFinished(finished);
            if (ends.length) showIndexing(running, waiting);
            return answered;
        });
}

/** The bar: "Indexing N of M folders", the folder being indexed and how far it has got; Suggest's bar, while it waits
 * for the open folder's index, carries that index's progress (one bar, #2355). */
function showIndexing(running, waiting) {
    const follow = state.indexFollow;
    const open = state.library ? null : state.scannedFolder;
    const covers = job => Boolean(open) && (samePath(job.folder, open) || isUnder(open, job.folder));
    const own = running.find(covers);
    state.indexPercent = own ? (own.percent || 0) : (waiting.some(covers) ? 0 : null);
    const suggesting = !suggestProgressContainer.classList.contains('hidden');
    if (suggesting && state.suggestWaiting && state.indexPercent !== null) {
        suggestProgressBar.style.width = `${state.indexPercent}%`;
    }
    const total = follow.counted.size;
    indexProgressContainer.classList.toggle('hidden', !follow.known.size || suggesting);
    if (!follow.known.size) return;
    const of = `Indexing ${Math.min(total, follow.ended + 1)} of ${total} ${total === 1 ? 'folder' : 'folders'}`;
    const now = running[0];
    indexProgressBar.style.width = `${now ? now.percent || 0 : 0}%`;
    indexProgressText.textContent = now ? `${of} (${now.name || baseName(now.folder)}): ${now.message || 'Indexing...'}`
        : `${of}: waiting to start...`;
}

/**
 * Folders' indexes ended in one answer of the queue, each `{folder, status}`: what they wrote is read again once
 * (upper.indexEnded), then the status line keeps their closing sentences (the bake, the new faces), not the "Ready" of
 * the scan that read it again. One cancelled wrote nothing, and is not read again.
 */
function indexesFinished(finished) {
    const said = [];
    let kind = 'ready';
    const wrote = [];
    for (const { folder, status } of finished) {
        const sentence = status.message && status.message !== NOTHING_TO_SAY ? `${baseName(folder)}: ${status.message}` : '';
        if (status.status === 'cancelled') {
            if (sentence) setStatus('ready', sentence);
            continue;
        }
        forgetScansHolding(folder);
        wrote.push(folder);
        if (status.status === 'failed') kind = 'error';
        if (sentence) said.push(sentence);
        else if (status.status === 'failed') said.push(`${baseName(folder)}: indexing failed`);
    }
    if (!wrote.length) return;
    Promise.resolve(upper.indexEnded(wrote)).then(() => {
        if (said.length) setStatus(kind, said.join(' '), { transient: false });
    });
}

/**
 * The open folder's suggestions, its run and its poll forgotten: a folder closed or another opened, or the server let
 * them go. A reply of the poll already under way lands nowhere (checkSuggestionsStatus).
 */
export function forgetFolderSuggestions() {
    clearInterval(state.progressTimer);
    state.progressTimer = null;
    state.folderSuggestions = {};
    state.suggestRun = null;
}

/**
 * The server no longer holds what a Suggest that only looked found (it is kept in memory, for a while): it
 * let it go after a while, or the folder was added to the library and it was dropped. The page says which,
 * forgets the copy it held -- here and in this browser's cache -- and Suggest is run again.
 */
export function suggestionsLetGo() {
    state.suggestionsInMemory = null;
    forgetFolderSuggestions();
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
    let timer = null;

    function queryProgress() {
        api.json(`/api/folder/suggest-status?path=${encodeURIComponent(folderPath)}`)
            .then(data => {
                if (state.library) return;   // likewise: its progress and suggestions belong to a folder
                // A poll forgotten or replaced (forgetFolderSuggestions, another checkSuggestionsStatus) shows nothing:
                // its folder's scan may still be the open one while the next folder's is under way.
                if (timer !== state.progressTimer) return;
                // A reply for a folder left behind (another opened meanwhile) is that folder's: it shows nothing here,
                // and its poll stops.
                if (!state.scannedFolder || !samePath(folderPath, state.scannedFolder)) {
                    clearInterval(timer);
                    return;
                }
                state.suggestRun = {
                    status: data.status, message: data.message || '',
                    completed: data.completed || 0, total: data.total || 0,
                };
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
                    indexProgressContainer.classList.add('hidden');
                    const total = data.total || 0;
                    const completed = data.completed || 0;
                    if (data.message !== 'Cancelling...') btnSuggestCancel.disabled = false;
                    
                    state.suggestWaiting = data.status === 'preparing' || total === 0;
                    if (state.suggestWaiting) {
                        suggestProgressBar.style.width = `${state.indexPercent || 0}%`;
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
                    // The open photo's box, hidden or a line, says how far the run has got until its own
                    // suggestions come; chips shown are left (a redraw under the pointer would close a face popup).
                    if (state.activePhotoPath && (suggestionsSection.classList.contains('hidden')
                        || suggestionsSection.querySelector('.suggestions-note'))) {
                        renderSuggestionsPanel(state.activePhotoPath);
                    }
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
                    // The box's waiting line goes: the run ended without this photo's suggestions.
                    if (state.activePhotoPath) renderSuggestionsPanel(state.activePhotoPath);
                    if (data.status === 'cancelled') {
                        setStatus('ready', 'Suggest was cancelled; what it suggested before is kept.', { transient: false });
                    }
                    if (data.status === 'error') {
                        // The server's sentence: another library's run holding other models, a folder with no photo.
                        setStatus('error', data.message || 'Suggest failed', { transient: false });
                    }
                }
            })
            .catch(err => {
                console.error("Error polling suggestions progress:", err);
            });
    }

    // Query once immediately, then poll
    queryProgress();
    timer = state.progressTimer = setInterval(queryProgress, 1500);
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

/**
 * The line the box shows for a photo with nothing to offer yet (owner, 2026-11-05): while the folder's run waits or
 * is under way, the server's own line, else its count; after it, a photo the run could not read says why. Otherwise
 * none, and the box stays hidden as it was: a photo never analysed with no run says nothing.
 */
function suggestionsNote(sugg) {
    const run = state.suggestRun;
    if (run && (run.status === 'preparing' || run.status === 'running')) {
        return run.message || (run.total ? `Suggesting... ${run.completed} / ${run.total}` : 'Suggesting...');
    }
    if (sugg && sugg.error) {
        return `This photo could not be analysed: ${sugg.error}. Start Photo Analysis again to retry.`;
    }
    return null;
}

/** The box holding only a line of words, as the faces' strip does while it loads (photo.js); null puts the lists back. */
function sayAboutSuggestions(words) {
    let note = suggestionsSection.querySelector('.suggestions-note');
    if (words && !note) {
        note = document.createElement('div');
        note.className = 'suggestions-note';
        note.setAttribute('role', 'status');
        suggestionsSection.appendChild(note);
    }
    if (note && !words) note.remove();
    else if (note) note.textContent = words;
    suggestionsSection.querySelector('.suggestions-content').classList.toggle('hidden', Boolean(words));
    btnApplyAllSingleSugg.classList.toggle('hidden', Boolean(words));
}

// Render suggestions box in right pane for active photo
export function renderSuggestionsPanel(photoPath) {
    const sugg = state.folderSuggestions[photoPath];
    const note = !sugg || sugg.error ? suggestionsNote(sugg) : null;
    sayAboutSuggestions(note);
    if (note || !sugg) {
        suggestionsSection.classList.toggle('hidden', !note);
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
    // As the photo shows its tags (pending.js shownTags): a tag taken off and not written yet is offered again.
    const record = state.folderPhotos.find(p => p.path === photoPath);
    const photo = record && { tags: shownTags(record) };
    const outstanding = (list, key) =>
        (list || []).filter(item => !photoAlreadyHas(photo, item[key], namesAPerson, state.people));

    // A name two people have is offered as one chip for each (replacing the question "which folder?"), and what is already on
    // the photo is taken out after that, so the Sam the photo has is not offered and the other Sam is.
    const people = personChips(sugg.people).filter(item => !suggestedPersonOnPhoto(photo, item));
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
            // A person a clip's frames showed says in how many of those looked at (the suggester's `frames`).
            const seenIn = isPerson && item.frames ? `Seen in ${item.frames.seen} of ${item.frames.of} frames. ` : '';
            chip.title = `${seenIn}Click to add ${shown} to this photo.`;
            // A person's chip shows their faces on focus as on hover.
            if (isPerson) attachPersonFaces(chip, item.person);
            chip.addEventListener('click', () => applySuggestedTagDirect(name, isPerson, photoPath));
            container.appendChild(pressable(chip));
        });
    }

    fill(suggestedPeopleContainer, people, 'name', true);
    fill(suggestedTagsContainer, tags, 'tag', false);
}

/**
 * The people a run suggested, a name that two people have as one suggestion for each of them (the tag of each as its `name`, so a
 * click writes exactly that person). A suggestion that names one person, or whose people the page has not read, stays as it is.
 */
/**
 * Is a suggested person on the photo already? When one of its keywords is them, by the id of their node; a name two people have
 * is on it when either is (Apply All asks nothing of that photo either). A keyword only spelled like them names nobody (#1394).
 */
function suggestedPersonOnPhoto(photo, item) {
    const person = item.person || state.people.ofText(item.name);
    const candidates = person ? [person] : state.people.called(item.name);
    return candidates.some(each => photoAlreadyHas(photo, each.tag, namesAPerson, state.people));
}

export function personChips(list) {
    const chips = [];
    for (const item of list || []) {
        const person = item.person || state.people.ofTag(item.name);
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
    return applySuggestedTagsDirect([tagName], isPerson, forPath);
}

/**
 * The tags of `tagNames` the open photo does not hold yet, added in one write of the file (Add all names each face of
 * the photo, and puts their people on it: one save, not one per person).
 */
export function applySuggestedTagsDirect(tagNames, isPerson, forPath = state.activePhotoPath) {
    // A chip or face card belongs to the photo it was drawn for. One left over
    // from another photo must not write that photo's suggestion to this one.
    const path = state.activePhotoPath;
    if (!path || forPath !== path) return;
    const photo = state.folderPhotos.find(p => p.path === path);
    if (!photo) return;

    return queueWriteOf(path, async () => {
        const added = [];
        let had = null;
        for (const tagName of tagNames) {
            const resolved = await resolveTagOrPerson(tagName, isPerson);
            if (!resolved || added.includes(resolved)) continue;
            if (photoAlreadyHas(photo, resolved, namesAPerson, state.people)) had = resolved;
            else added.push(resolved);
        }
        // Say so rather than doing nothing. A click that silently no-ops reads as
        // a broken button, which is how this was reported.
        if (added.length === 0) {
            if (had) setStatus('ready', `${leafOf(had)} is already on this photo`);
            return true;
        }
        const updatedTags = [...(photo.tags || []), ...added];

        setStatus('busy', 'Saving...');
        let saved;
        try {
            saved = await postPhotoMetadata(photo, { tags: updatedTags }, photoChangedOnDisk);
        } catch (err) {
            console.error(err);
            setStatus('error', 'Error');
            alert("Error adding suggested tag: " + err.message);
            return false;
        }
        photo.tags = updatedTags;
        if (isPerson) {
            if (!photo.people) photo.people = [];
            for (const each of added) {
                if (!photo.people.includes(leafOf(each))) photo.people.push(leafOf(each));
            }
        }
        recordsWritten([photo]);
        // The suggestion has been taken, so it is no longer a suggestion.
        redrawIfShowing(photo, saved);
        setStatus('ready', 'Ready');
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
        let saved;
        try {
            saved = await postPhotoMetadata(photo, { tags: updatedTags }, photoChangedOnDisk);
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
        recordsWritten([photo]);
        redrawIfShowing(photo, saved);
        setStatus('ready', 'Ready');
        return true;
    });
}

export function applyFolderSuggestionsLevel() {
    const folder = state.scannedFolder;
    if (!folder || state.selectedThumbnails.length === 0) return;

    // Say what will be written, and to how many photos that already carry work,
    // before writing it. The common mistake is not misreading the button -- it is
    // having the wrong selection, and a count of photos alone does not surface
    // that. This is the last point at which it costs nothing. Suggest offers a clip its tags as a photo its own: the
    // count is of both, as Smart Rename's (#2565).
    const records = selectedRecords();
    const alreadyTagged = records.filter(isPhotoTagged).length;
    const scope = [
        `Auto-apply AI suggestions to the ${countSaid(records)} selected?`,
        '',
        alreadyTagged
            ? `${alreadyTagged} of them already have tags. Suggestions are added to what is there; nothing is removed.`
            : 'None of them are tagged yet.',
        '',
        'This writes keywords into the files. Undo restores the previous tags for this session only.',
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
    return queuePhotoWrite(() => targets, (entry) => {
        const before = snapshotPhotos(targets);
        entry.undoSoFar = prior;
        setStatus('busy', `Applying suggestions to ${targets.length} photo(s)...`);
        return api.json('/api/folder/auto-apply', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                folder_path: folder,
                // Everything the panel is showing: the server applies what was offered, with no floor. A button
                // called Apply All that applied 141 of 153 suggestions and left 12 on screen read as a failure.
                photo_paths: targets
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
            setStatus('ready', `Suggestions applied to ${before.length} photo(s)` + ' \u2014 Ctrl+Z to undo');
            // The reply names no stamps: every scan kept holding them is forgotten, and the folder read again.
            photosGone(targets);
            scanFolder(true);
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
            // folder is read again, so the page's records say what the files hold, and every scan kept of them forgotten.
            photosGone(targets);
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

        // Check people suggestions. A person the photo's keywords already are (by id,
        // suggestedPersonOnPhoto) is nothing to apply, nor one the photo lists.
        if (sugg.people) {
            for (let p of sugg.people) {
                const leaf = leafOf(p.name);
                if (!suggestedPersonOnPhoto(photo, p)
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
