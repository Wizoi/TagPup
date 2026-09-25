// Indexing folders and the folder picker.
import { api } from './common/api.js';
import { baseName, samePath } from './common/paths.js';
import { state } from './state.js';
import { fetchPhotos, refreshSidebarQuietly } from './sidebar.js';

// ---- Folder indexing ---------------------------------------------------
// TagTuner owns identity work, so it has to be able to bring new material in
// rather than requiring a trip through TagPup first. Clustering is deliberately
// not requested here: it rewrites every name in the database, not just the folder
// being added, and would discard corrections made here.
const btnAddFolder = document.getElementById('btn-add-folder');
const btnRemoveFolder = document.getElementById('btn-remove-folder');
const indexProgressContainer = document.getElementById('index-progress-container');
const indexProgressBar = document.getElementById('index-progress-bar');
const indexProgressText = document.getElementById('index-progress-text');
const indexQueueSummary = document.getElementById('index-queue-summary');
const btnCancelQueue = document.getElementById('btn-cancel-queue');
const folderPickerModal = document.getElementById('folder-picker-modal');
const folderPickerList = document.getElementById('folder-picker-list');
const folderPickerListWrap = document.getElementById('folder-picker-list-wrap');
const folderPickerEmpty = document.getElementById('folder-picker-empty');
const folderPickerLoading = document.getElementById('folder-picker-loading');
const folderPickerParent = document.getElementById('folder-picker-parent');
const folderPickerSelection = document.getElementById('folder-picker-selection');
const btnFolderPickerBrowse = document.getElementById('btn-folder-picker-browse');
const btnFolderPickerCancel = document.getElementById('btn-folder-picker-cancel');
const btnFolderPickerQueue = document.getElementById('btn-folder-picker-queue');
const btnCloseFolderPicker = document.getElementById('btn-close-folder-picker');
const btnFolderSelectAll = document.getElementById('btn-folder-select-all');
const btnFolderSelectNone = document.getElementById('btn-folder-select-none');
const folderPickerHideIndexed = document.getElementById('folder-picker-hide-indexed');
const removeFolderModal = document.getElementById('remove-folder-modal');
const removeFolderFilter = document.getElementById('remove-folder-filter');
const removeFolderEmpty = document.getElementById('remove-folder-empty');
const removeFolderList = document.getElementById('remove-folder-list');
const removeFolderSelection = document.getElementById('remove-folder-selection');
const btnRemoveFolderCancel = document.getElementById('btn-remove-folder-cancel');
const btnRemoveFolderConfirm = document.getElementById('btn-remove-folder-confirm');
const btnCloseRemoveFolder = document.getElementById('btn-close-remove-folder');

// The buttons carry the state rather than just greying out. A disabled control
// with no explanation reads as broken -- which is exactly how it was reported.
function setIndexingUI(busy, label) {
    if (btnAddFolder) {
        // Adding stays available while indexing: folders queue rather than
        // collide. Only the label changes, so it is clear something is running.
        btnAddFolder.disabled = false;
        btnAddFolder.textContent = busy ? (label || '\u2795 Add to queue') : '\u2795 Add folders';
        btnAddFolder.title = busy
            ? 'Folders you add now are queued behind the one being indexed.'
            : 'Index folders into this database so their faces can be identified';
    }
    if (btnRemoveFolder) {
        btnRemoveFolder.disabled = busy;
        btnRemoveFolder.title = busy
            ? 'Unavailable while indexing'
            : "Remove a folder's photos and faces from this database (photo files are not deleted)";
    }
}

// A freshly loaded page knows nothing about a job that started before it, nor
// about anything queued behind it. Ask.
export function restoreIndexingState() {
    api.fetch('/api/folder/index-active')
        .then(res => res.ok ? res.json() : { active: [], queued: [] })
        .then(data => {
            renderQueueSummary(data);
            const job = (data.active || [])[0];
            if (!job) return;
            setIndexingUI(true);
            indexProgressContainer.classList.remove('hidden');
            indexProgressBar.style.width = `${job.percent || 0}%`;
            indexProgressText.textContent = job.message || 'Indexing...';
            pollIndexStatus(job.folder);
        })
        .catch(() => {});
}

// Says what is running and what is behind it. Without this a queue of ten looks
// exactly like a queue of one that happens to be taking a while.
function renderQueueSummary(data) {
    const queued = (data && data.queued) || [];
    const active = (data && data.active) || [];
    if (!indexQueueSummary) return;
    if (!queued.length) {
        indexQueueSummary.classList.add('hidden');
        indexQueueSummary.textContent = '';
        if (btnCancelQueue) btnCancelQueue.classList.add('hidden');
        return;
    }
    // The folder being worked on, and how many are behind it -- nothing else.
    // Listing what was waiting made a line too long to read, which ran off the
    // edge of the bar and buried the one number anybody wants. The names are
    // still there on hover, where their length costs nothing.
    const parts = [];
    if (active.length) parts.push(active[0].name);
    parts.push(`${queued.length} more waiting`);
    indexQueueSummary.textContent = parts.join('  \u00B7  ');
    indexQueueSummary.title = 'Waiting:\n' + queued.map(f => f.name).join('\n');
    indexQueueSummary.classList.remove('hidden');
    if (btnCancelQueue) btnCancelQueue.classList.remove('hidden');
}

// Once the running folder finishes, the next one starts on the server without
// being asked. The page has to notice that and follow it.
function followQueue(delay) {
    if (state.followQueueTimer) clearTimeout(state.followQueueTimer);
    state.followQueueTimer = setTimeout(() => {
        state.followQueueTimer = null;
        api.fetch('/api/folder/index-active')
            .then(res => res.ok ? res.json() : { active: [], queued: [] })
            .then(data => {
                renderQueueSummary(data);
                const job = (data.active || [])[0];
                // Not the folder we just watched finish: that one is done
                // whatever this reply still says about it.
                if (job && !samePath(job.folder, state.lastFinishedFolder)) {
                    setIndexingUI(true);
                    indexProgressContainer.classList.remove('hidden');
                    pollIndexStatus(job.folder);
                    return;
                }
                setIndexingUI(false);
                indexProgressContainer.classList.add('hidden');
                refreshSidebarQuietly();
            })
            .catch(() => {});
    }, delay === undefined ? 400 : delay);
}

function pickFolder() {
    return api.json('/api/browse-folder')
        .then(data => (data && data.path) ? data.path : null);
}

function pollIndexStatus(folderPath) {
    if (state.indexPollTimer) clearInterval(state.indexPollTimer);
    if (!samePath(folderPath, state.lastFinishedFolder)) state.lastFinishedFolder = null;

    const query = () => {
        api.json(`/api/folder/index-status?path=${encodeURIComponent(folderPath)}`)
            .then(data => {
                if (data.status === 'running') {
                    indexProgressContainer.classList.remove('hidden');
                    indexProgressBar.style.width = `${data.percent || 0}%`;
                    indexProgressText.textContent = data.message || 'Indexing...';
                    return;
                }
                // Any terminal status stops the poll. Treating only 'completed' as
                // terminal is how a dead worker turns into a spinner that never ends.
                clearInterval(state.indexPollTimer);
                state.indexPollTimer = null;
                state.lastFinishedFolder = folderPath;

                if (data.status === 'failed') {
                    // One bad folder must not stop the rest of the queue, so this
                    // reports and moves on rather than tearing the whole run down.
                    alert('Indexing failed for this folder: ' +
                          (data.message || 'Unknown error'));
                }
                // fetchPhotos dispatches on the selected mode. Calling
                // fetchPeopleWithCounts directly rendered the people list into the
                // sidebar whatever Tune target said, so finishing an index while in
                // Folder Matches left the list and the dropdown disagreeing.
                refreshSidebarQuietly();
                // Whatever was queued behind this folder is already starting.
                followQueue();
            })
            .catch(err => console.error('Error polling index status:', err));
    };
    query();
    state.indexPollTimer = setInterval(query, 1000);
}

// ---------------------------------------------------------------- picker --
// The native folder dialog returns one path and cannot multi-select, so choosing
// a season of shoots meant opening it once per folder and waiting for each to
// finish. Browsing to the parent and ticking its children does it in one pass.

function openFolderPicker() {
    state.pickerFolders = [];
    state.pickerSelected = new Set();
    folderPickerParent.textContent = '';
    folderPickerList.innerHTML = '';
    folderPickerListWrap.classList.add('hidden');
    folderPickerLoading.classList.add('hidden');
    folderPickerEmpty.classList.remove('hidden');
    updatePickerSelectionUI();
    folderPickerModal.classList.remove('hidden');
}

function closeFolderPicker() {
    folderPickerModal.classList.add('hidden');
}

function loadSubfolders(parent) {
    folderPickerEmpty.classList.add('hidden');
    folderPickerListWrap.classList.add('hidden');
    folderPickerLoading.classList.remove('hidden');
    folderPickerParent.textContent = parent;

    api.fetch(`/api/folder/subfolders?path=${encodeURIComponent(parent)}`)
        .then(res => res.ok ? res.json() : res.json().then(e => { throw new Error(e.error || 'failed'); }))
        .then(data => {
            folderPickerLoading.classList.add('hidden');
            state.pickerFolders = [];

            // The parent itself is offered whenever it holds images directly --
            // a folder of folders usually holds none, and a leaf folder is the
            // whole answer.
            if (data.own_images > 0 || !data.folders.length) {
                state.pickerFolders.push({
                    path: data.parent,
                    name: `${baseName(data.parent) || data.parent}  (this folder itself)`,
                    images: data.own_images,
                    // Its own photos, counted the same way as its images. This
                    // said 0, so an indexed leaf folder was always offered as new.
                    indexed: data.own_indexed || 0,
                    isParent: true,
                });
            }
            (data.folders || []).forEach(f => state.pickerFolders.push(f));

            // Nothing worth offering: say so rather than showing an empty box.
            if (!state.pickerFolders.length) {
                folderPickerEmpty.textContent =
                    'No subfolders and no images directly in that folder.';
                folderPickerEmpty.classList.remove('hidden');
                updatePickerSelectionUI();
                return;
            }

            // Preselect what has images and is not already fully indexed --
            // the common case is "everything new under here".
            state.pickerSelected = new Set(
                state.pickerFolders
                    .filter(f => f.images > 0 && f.indexed < f.images)
                    .map(f => f.path)
            );
            renderPickerList();
            folderPickerListWrap.classList.remove('hidden');
        })
        .catch(err => {
            folderPickerLoading.classList.add('hidden');
            folderPickerEmpty.textContent = 'Could not read that folder: ' + err.message;
            folderPickerEmpty.classList.remove('hidden');
        });
}

function renderPickerList() {
    folderPickerList.innerHTML = '';
    const hideIndexed = folderPickerHideIndexed && folderPickerHideIndexed.checked;

    state.pickerFolders.forEach(folder => {
        const fullyIndexed = folder.images > 0 && folder.indexed >= folder.images;
        if (hideIndexed && fullyIndexed) return;

        const row = document.createElement('label');
        row.className = 'folder-picker-row' + (fullyIndexed ? ' already-indexed' : '');

        const box = document.createElement('input');
        box.type = 'checkbox';
        box.checked = state.pickerSelected.has(folder.path);
        box.addEventListener('change', () => {
            if (box.checked) state.pickerSelected.add(folder.path);
            else state.pickerSelected.delete(folder.path);
            updatePickerSelectionUI();
        });

        const name = document.createElement('span');
        name.className = 'folder-picker-name';
        name.textContent = folder.name;

        const meta = document.createElement('span');
        meta.className = 'folder-picker-meta';
        if (!folder.images) {
            meta.textContent = 'no images';
        } else if (fullyIndexed) {
            meta.textContent = `${folder.images} image(s) \u2014 already indexed`;
        } else if (folder.indexed) {
            meta.textContent = `${folder.images} image(s), ${folder.indexed} already indexed`;
        } else {
            meta.textContent = `${folder.images} image(s)`;
        }

        row.appendChild(box);
        row.appendChild(name);
        row.appendChild(meta);
        folderPickerList.appendChild(row);
    });
    updatePickerSelectionUI();
}

function updatePickerSelectionUI() {
    const n = state.pickerSelected.size;
    const images = state.pickerFolders
        .filter(f => state.pickerSelected.has(f.path))
        .reduce((sum, f) => sum + (f.images || 0), 0);
    folderPickerSelection.textContent = n
        ? `${n} folder(s), ${images} image(s)`
        : 'Nothing selected';
    btnFolderPickerQueue.disabled = n === 0;
    btnFolderPickerQueue.textContent = n > 1 ? `Add ${n} folders to queue` : 'Add to queue';
}

function queueSelectedFolders() {
    const folders = state.pickerFolders
        .filter(f => state.pickerSelected.has(f.path))
        .map(f => f.path);
    if (!folders.length) return;

    btnFolderPickerQueue.disabled = true;
    api.fetch('/api/folder/index-start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_paths: folders })
    })
    .then(res => res.ok ? res.json() : res.json().then(e => { throw new Error(e.error || 'failed'); }))
    .then(data => {
        closeFolderPicker();
        setIndexingUI(true, '\u2795 Add to queue');
        indexProgressContainer.classList.remove('hidden');
        indexProgressText.textContent = 'Starting...';
        if ((data.already_queued || []).length) {
            console.info('Already queued or running, skipped:', data.already_queued);
        }
        // A folder that finished earlier and has just been queued again is a new
        // job. Left set, followQueue took it for the finished one and stopped
        // following: no progress, and Remove Folder enabled while it indexed.
        state.lastFinishedFolder = null;
        followQueue();
    })
    .catch(err => {
        btnFolderPickerQueue.disabled = false;
        alert('Could not queue those folders: ' + err.message);
    });
}

// ---- Remove Folder ------------------------------------------------------
// It offers the folders the library holds (/api/folder/indexed), not the disk's: the
// usual reason to remove a folder is that it is gone from disk, and the system dialog
// could not pick one that is (#47). Each is sent back as the server spelled it.

function openRemoveFolder() {
    state.removeFolders = [];
    state.removeFolderChosen = null;
    removeFolderFilter.value = '';
    removeFolderList.replaceChildren();
    removeFolderList.classList.add('hidden');
    removeFolderEmpty.textContent = "Reading the library's folders...";
    removeFolderEmpty.classList.remove('hidden');
    updateRemoveFolderUI();
    removeFolderModal.classList.remove('hidden');

    api.json('/api/folder/indexed')
        .then(data => {
            state.removeFolders = (data && data.folders) || [];
            renderRemoveFolderList();
        })
        .catch(err => {
            removeFolderEmpty.textContent = "Could not read the library's folders: " + err.message;
        });
}

function closeRemoveFolder() {
    removeFolderModal.classList.add('hidden');
}

function renderRemoveFolderList() {
    const wanted = removeFolderFilter.value.trim().toLowerCase();
    const shown = state.removeFolders.filter(f => !wanted || f.path.toLowerCase().includes(wanted));
    removeFolderList.replaceChildren();
    shown.forEach(folder => {
        const row = document.createElement('label');
        row.className = 'folder-picker-row' + (folder.on_disk ? '' : ' not-on-disk');

        const choice = document.createElement('input');
        choice.type = 'radio';
        choice.name = 'remove-folder-choice';
        choice.checked = folder.path === state.removeFolderChosen;
        choice.addEventListener('change', () => {
            state.removeFolderChosen = folder.path;
            updateRemoveFolderUI();
        });

        const name = document.createElement('span');
        name.className = 'folder-picker-name';
        name.textContent = folder.path;
        name.title = folder.path;

        const meta = document.createElement('span');
        meta.className = 'folder-picker-meta';
        meta.textContent = `${folder.photos} photo(s)` + (folder.on_disk ? '' : ' \u2014 not on disk');

        row.append(choice, name, meta);
        removeFolderList.appendChild(row);
    });
    const none = !shown.length;
    removeFolderEmpty.textContent = state.removeFolders.length
        ? 'No folder matches that.' : 'This library holds no photos.';
    removeFolderEmpty.classList.toggle('hidden', !none);
    removeFolderList.classList.toggle('hidden', none);
    updateRemoveFolderUI();
}

function updateRemoveFolderUI() {
    const chosen = state.removeFolders.find(f => f.path === state.removeFolderChosen);
    removeFolderSelection.textContent = chosen ? `${chosen.photos} photo(s) under it` : 'Nothing chosen';
    btnRemoveFolderConfirm.disabled = !chosen;
}

function removeChosenFolder() {
    const folderPath = state.removeFolderChosen;
    if (!folderPath) return;
    if (!confirm(
        `Remove every photo under\n\n${folderPath}\n\nfrom this database?\n\n` +
        `The photo files are NOT deleted, but any face work on them -- assigned ` +
        `names and exclusions -- is discarded with the rows.`
    )) return;
    closeRemoveFolder();

    btnRemoveFolder.disabled = true;
    api.fetch('/api/folder/remove', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_path: folderPath })
    })
    .then(res => res.ok ? res.json() : res.json().then(e => { throw new Error(e.error || 'failed'); }))
    .then(data => {
        let msg = `Removed ${data.photos_removed} photo(s) and ${data.faces_removed} face(s).`;
        if (data.manual_lost || data.excluded_lost) {
            msg += `\n\nThat included ${data.manual_lost} manually assigned name(s) ` +
                   `and ${data.excluded_lost} exclusion(s).`;
        }
        alert(msg);
        fetchPhotos();
    })
    .catch(err => alert('Error removing folder: ' + err.message))
    .finally(() => { btnRemoveFolder.disabled = false; });
}

// Its listeners, which main.js adds once the page has loaded.
export function wireIndexing() {
    if (btnAddFolder) {
        btnAddFolder.addEventListener('click', openFolderPicker);
    }
    if (btnFolderPickerBrowse) {
        btnFolderPickerBrowse.addEventListener('click', () => {
            pickFolder().then(parent => { if (parent) loadSubfolders(parent); });
        });
    }
    if (btnCloseFolderPicker) btnCloseFolderPicker.addEventListener('click', closeFolderPicker);
    if (btnFolderPickerCancel) btnFolderPickerCancel.addEventListener('click', closeFolderPicker);
    if (btnFolderPickerQueue) btnFolderPickerQueue.addEventListener('click', queueSelectedFolders);
    if (btnFolderSelectAll) {
        btnFolderSelectAll.addEventListener('click', () => {
            state.pickerFolders.forEach(f => { if (f.images > 0) state.pickerSelected.add(f.path); });
            renderPickerList();
        });
    }
    if (btnFolderSelectNone) {
        btnFolderSelectNone.addEventListener('click', () => {
            state.pickerSelected = new Set();
            renderPickerList();
        });
    }
    if (folderPickerHideIndexed) {
        folderPickerHideIndexed.addEventListener('change', renderPickerList);
    }
    if (btnCancelQueue) {
        btnCancelQueue.addEventListener('click', () => {
            if (!confirm('Forget the folders that have not started yet?\n\n' +
                         'The folder being indexed now carries on.')) return;
            api.json('/api/folder/index-cancel', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ all: true })
            })
            .then(() => followQueue())
            .catch(err => alert('Could not cancel the queue: ' + err.message));
        });
    }

    if (btnRemoveFolder) btnRemoveFolder.addEventListener('click', openRemoveFolder);
    if (btnCloseRemoveFolder) btnCloseRemoveFolder.addEventListener('click', closeRemoveFolder);
    if (btnRemoveFolderCancel) btnRemoveFolderCancel.addEventListener('click', closeRemoveFolder);
    if (btnRemoveFolderConfirm) btnRemoveFolderConfirm.addEventListener('click', removeChosenFolder);
    if (removeFolderFilter) removeFolderFilter.addEventListener('input', renderRemoveFolderList);
}
