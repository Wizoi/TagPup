// TagPup's page: the strip that shows a bulk edit of the library's photos -- a bar, `done of total`, what was changed, left out and
// refused, the time left, Cancel while it runs; when it ends, the sentence it ends in, the first 50 errors, and Resume or Start again
// where they apply (docs/ARCHITECTURE.md, phase 9d-2). It draws `state.bulk` and nothing else: bulk-job.js owns the job.
//
// It is under the header, whatever is open below it, because the job belongs to the library and not to the view or the folder. It
// never takes the focus when it appears. A screen reader is told at coarse steps (the start, each tenth, the end), not each second.
import { buildElement, replaceContent } from './common/dom.js';
import { state } from './state.js';
import {
    btnBulkAgain, btnBulkCancel, btnBulkDismiss, btnBulkResume, btnBulkShow,
    bulkStrip, bulkStripBar, bulkStripCounts, bulkStripErrorList, bulkStripErrors, bulkStripErrorsSummary,
    bulkStripEta, bulkStripLive, bulkStripMessage, bulkStripProgress, bulkStripTitle, thumbnailsGrid
} from './elements.js';
import { countsLine, endedSentence, etaText, isRunningRefusal, titleOf, widenedSentence } from './bulk-words.js';

/** Errors listed with their file's name; the rest are counted ("N more"). The server keeps 50 as well. */
export const MOST_ERRORS_SHOWN = 50;

/** Is the job one that has not ended? */
export function isRunning(job) {
    return Boolean(job) && job.state === 'running';
}

function show(element, shown) {
    element.classList.toggle('hidden', !shown);
}

/** The errors a job reports: the first MOST_ERRORS_SHOWN with the file's name and why, and how many more there were. */
function drawErrors(job) {
    const listed = (job.errors || []).slice(0, MOST_ERRORS_SHOWN);
    const total = job.error_count || listed.length;
    const key = `${job.job}:${listed.length}:${total}`;
    show(bulkStripErrors, total > 0);
    if (!total || bulkStripErrorList.dataset.key === key) return;
    bulkStripErrorList.dataset.key = key;
    bulkStripErrorsSummary.textContent = total > listed.length
        ? `${total.toLocaleString()} errors: the first ${listed.length} are listed`
        : `${total.toLocaleString()} ${total === 1 ? 'error' : 'errors'}`;
    const rows = listed.map(each => buildElement('li', {}, [
        buildElement('strong', { text: String(each.name || `photo ${each.id}`) }),
        ` — ${each.why}`,
    ]));
    const more = total - listed.length;
    if (more > 0) rows.push(buildElement('li', { text: `${more.toLocaleString()} more, not listed` }));
    replaceContent(bulkStripErrorList, ...rows);
}

/** What a screen reader is told: at the start, at each tenth, at the end -- and only when it is a new thing to say. */
function announce(job, desc) {
    const bulk = state.bulk;
    let step;
    let words;
    if (isRunning(job)) {
        const tenth = job.total ? Math.floor((job.done / job.total) * 10) : 0;
        step = `running:${tenth}`;
        words = tenth === 0 ? `${titleOf(job, desc)} has started.` : `${tenth * 10} percent: ${job.done.toLocaleString()} of ${job.total.toLocaleString()} photos.`;
    } else {
        step = `${job.state}:${job.finished || ''}`;
        words = endedSentence(job, desc);
    }
    if (bulk.announced === step) return;
    bulk.announced = step;
    bulkStripLive.textContent = words;
}

/** Tell a screen reader something that is not progress (a refusal, a job that took more than was picked): once, as it appears. */
export function say(words) {
    bulkStripLive.textContent = '';
    bulkStripLive.textContent = words;
}

/** Draw the strip from `state.bulk`: hidden when there is nothing to say. */
export function renderBulkStrip() {
    const bulk = state.bulk;
    const job = bulk.job;
    const desc = bulk.request ? bulk.request.desc : null;
    const showing = Boolean(job) || bulk.starting || Boolean(bulk.conflict);
    show(bulkStrip, showing);
    if (!showing) {
        bulkStripLive.textContent = '';
        bulk.announced = null;
        return;
    }
    if (!job) {
        // Starting, or refused because another is running: no job of this page's to draw.
        const another = Boolean(bulk.conflict) && isRunningRefusal(bulk.conflict);
        bulkStripTitle.textContent = !bulk.conflict ? 'Starting the bulk edit...' : another ? 'Another bulk edit is running' : 'The edit did not start';
        bulkStripBar.removeAttribute('value');
        show(bulkStripBar, !bulk.conflict);
        bulkStripProgress.textContent = '';
        bulkStripEta.textContent = '';
        bulkStripCounts.textContent = '';
        bulkStripMessage.textContent = bulk.conflict || '';
        bulkStripMessage.classList.toggle('bulk-strip-problem', Boolean(bulk.conflict));
        show(bulkStripErrors, false);
        show(btnBulkCancel, false);
        show(btnBulkResume, false);
        show(btnBulkAgain, false);
        show(btnBulkShow, another);
        btnBulkShow.textContent = 'Show it';
        show(btnBulkDismiss, Boolean(bulk.conflict));
        return;
    }
    const running = isRunning(job);
    const total = job.total || 0;
    bulkStripTitle.textContent = titleOf(job, desc);
    bulkStripTitle.title = bulkStripTitle.textContent;
    show(bulkStripBar, true);
    bulkStripBar.max = 100;
    bulkStripBar.value = total ? Math.min(100, Math.round((job.done / total) * 100)) : 0;
    bulkStripProgress.textContent = `${(job.done || 0).toLocaleString()} of ${total.toLocaleString()}`;
    bulkStripEta.textContent = etaText(job);
    bulkStripCounts.textContent = countsLine(job);
    // The message: the sentence an ended job ends in; while it runs, only a trouble in asking, or a cancel under way.
    let message = '';
    let problem = false;
    if (!running) {
        message = endedSentence(job, desc);
        problem = job.state !== 'done' || (job.error_count || 0) > 0;
        if (!bulk.request && job.op === 'delete' && job.state !== 'done') {
            message += ' Select the photos that are left and delete them again to finish it.';
        } else if (!bulk.request && job.op !== 'time_shift' && job.state !== 'done') {
            message += ' Select the photos and make the edit again to finish it: adding or removing what a photo holds already changes nothing.';
        }
    } else if (bulk.cancelling || job.cancelling) {
        message = 'Cancelling: the photos being written now are finished first.';
    }
    if (bulk.trouble) {
        message = message ? `${message} ${bulk.trouble}` : bulk.trouble;
        problem = true;
    }
    if (bulk.notice) {
        const noted = widenedSentence(bulk.notice, running);
        message = message ? `${message} ${noted}` : noted;
        problem = problem || running;
    }
    bulkStripMessage.textContent = message;
    bulkStripMessage.classList.toggle('bulk-strip-problem', problem);
    drawErrors(job);
    show(btnBulkCancel, running);
    btnBulkCancel.disabled = Boolean(bulk.cancelling || job.cancelling);
    btnBulkCancel.textContent = btnBulkCancel.disabled ? 'Cancelling...' : 'Cancel';
    show(btnBulkResume, !running && Boolean(job.resumable));
    show(btnBulkAgain, !running && Boolean(bulk.request) && !job.resumable && job.state !== 'done');
    show(btnBulkShow, bulk.gaveUp);
    btnBulkShow.textContent = 'Ask again';
    show(btnBulkDismiss, !running);
    announce(job, desc);
}

/** Wire the strip's buttons: `handlers` -- cancel, resume, again, show, dismiss -- are bulk-job.js's. */
export function wireBulkStrip(handlers) {
    btnBulkCancel.addEventListener('click', handlers.cancel);
    btnBulkResume.addEventListener('click', handlers.resume);
    btnBulkAgain.addEventListener('click', handlers.again);
    btnBulkShow.addEventListener('click', handlers.show);
    btnBulkDismiss.addEventListener('click', () => {
        // The button that had the focus is going: the focus goes to the grid, not to the top of the page.
        const inside = bulkStrip.contains(document.activeElement);
        handlers.dismiss();
        if (inside && thumbnailsGrid) thumbnailsGrid.focus({ preventScroll: true });
    });
}
