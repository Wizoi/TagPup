// Indexing folders: the header's progress bar, following the queue the server runs. Folders are added to the library
// and removed from it in Manage Folders (web/common/folder-manager.js), opened from the gear (gear.js), which calls
// indexingQueued once folders are queued.
import { api } from './common/api.js';
import { buildElement, replaceContent, withAction } from './common/dom.js';
import { samePath } from './common/paths.js';
import { state } from './state.js';
import { refreshSidebarQuietly } from './sidebar.js';
import { REEXAMINE_TITLE, reexamineFolderAt } from './reexamine.js';

// ---- Folder indexing ---------------------------------------------------
// TagTuner owns identity work, so it has to be able to bring new material in
// rather than requiring a trip through TagPup first. Clustering is deliberately
// not requested here: it rewrites every name in the database, not just the folder
// being added, and would discard corrections made here.
const indexProgressContainer = document.getElementById('index-progress-container');
const indexProgressBar = document.getElementById('index-progress-bar');
const indexProgressText = document.getElementById('index-progress-text');
const indexQueueSummary = document.getElementById('index-queue-summary');
const btnCancelQueue = document.getElementById('btn-cancel-queue');

/**
 * The bar's line: the server's sentence. Given the `folder` an index that ended offers Re-examine of (its status's
 * `reexamine`: some of its new faces have a likely name, or their matching failed; a batch's is its first folder, #3417),
 * its Re-examine is a button that runs Re-examine on that folder here (reexamine.js), on the sentence's word or after it;
 * while an index runs, its words are words: its bake still writes the folder's files. Not drawn again while it says the
 * same of the same folder.
 */
function sayOnTheBar(message, folder = null) {
    const text = message || 'Indexing...';
    indexProgressText.title = text;
    if (indexProgressText.dataset.said === text && indexProgressText.dataset.folder === (folder || '')) return;
    indexProgressText.dataset.said = text;
    indexProgressText.dataset.folder = folder || '';
    if (!folder) {
        indexProgressText.textContent = text;
        return;
    }
    replaceContent(indexProgressText, ...withAction(text, 'Re-examine', () => {
        const button = buildElement('button', {
            className: 'btn-index-reexamine', text: 'Re-examine', title: REEXAMINE_TITLE, attrs: { type: 'button' },
        });
        button.addEventListener('click', () => reexamineFolderAt(folder, button));
        return button;
    }));
}

/** With nothing indexing: the last index's closing sentence when it offers Re-examine, else no bar. */
function showEndOrHide() {
    const ending = state.indexEnding;
    if (!ending) {
        indexProgressContainer.classList.add('hidden');
        return;
    }
    indexProgressContainer.classList.remove('hidden');
    indexProgressBar.style.width = '100%';
    sayOnTheBar(ending.message, ending.folder);
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
            indexProgressContainer.classList.remove('hidden');
            indexProgressBar.style.width = `${job.percent || 0}%`;
            sayOnTheBar(job.message);
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
                    indexProgressContainer.classList.remove('hidden');
                    pollIndexStatus(job.folder);
                    return;
                }
                showEndOrHide();
                refreshSidebarQuietly();
            })
            .catch(() => {});
    }, delay === undefined ? 400 : delay);
}

function pollIndexStatus(folderPath) {
    if (state.indexPollTimer) clearInterval(state.indexPollTimer);
    if (!samePath(folderPath, state.lastFinishedFolder)) state.lastFinishedFolder = null;

    const query = () => {
        api.json(`/api/folder/index-status?path=${encodeURIComponent(folderPath)}`)
            .then(data => {
                if (data.status === 'running') {
                    // Another index under way: the last one's closing sentence is not kept past it.
                    state.indexEnding = null;
                    indexProgressContainer.classList.remove('hidden');
                    indexProgressBar.style.width = `${data.percent || 0}%`;
                    sayOnTheBar(data.message);
                    return;
                }
                // Any terminal status stops the poll. Treating only 'completed' as
                // terminal is how a dead worker turns into a spinner that never ends.
                clearInterval(state.indexPollTimer);
                state.indexPollTimer = null;
                state.lastFinishedFolder = folderPath;
                // Its closing sentence stays on the bar when it offers Re-examine (`reexamine`, the folder it offers
                // it of): the button there is the way to it.
                state.indexEnding = data.status === 'completed' && data.reexamine
                    ? { message: data.message, folder: data.reexamine } : null;

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

/** Folders were queued (Manage Folders, from the gear): show the bar and follow what the server now runs. */
export function indexingQueued() {
    state.indexEnding = null;
    indexProgressContainer.classList.remove('hidden');
    sayOnTheBar('Starting...');
    // A folder that finished earlier and has just been queued again is a new
    // job. Left set, followQueue took it for the finished one and stopped
    // following: no progress shown while it indexed.
    state.lastFinishedFolder = null;
    followQueue();
}

// Its listeners, which main.js adds once the page has loaded.
export function wireIndexing() {
    if (btnCancelQueue) {
        btnCancelQueue.addEventListener('click', () => {
            if (!confirm('Forget the folders that have not started yet?\n\n' +
                         'A folder still waiting for the graphics card stops too, having indexed nothing. ' +
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
}
