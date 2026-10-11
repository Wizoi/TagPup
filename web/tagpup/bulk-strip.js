// TagPup's page: the strip that shows the bulk edit of the library's photos -- while it is written (what it does, to how many, an
// indeterminate bar), and when it has been: what was changed, left out and refused, the first 50 errors, Dismiss (docs/ARCHITECTURE.md).
// It draws `state.bulk` and nothing else: bulk-run.js owns the edit.
//
// It is under the header, whatever is open below it. It never takes the focus when it appears. A screen reader is told by `say`.
import { buildElement, replaceContent } from './common/dom.js';
import { state } from './state.js';
import {
    btnBulkDismiss, bulkStrip, bulkStripBar, bulkStripCounts, bulkStripErrorList, bulkStripErrors, bulkStripErrorsSummary,
    bulkStripLive, bulkStripMessage, bulkStripTitle, thumbnailsGrid
} from './elements.js';
import { countsLine, endedSentence, titleOf, widenedSentence, writingSentence } from './bulk-words.js';

/** Errors listed with their file's name; the rest are counted ("N more"). The server names 50 as well. */
export const MOST_ERRORS_SHOWN = 50;

function show(element, shown) {
    element.classList.toggle('hidden', !shown);
}

/** The errors an edit reports: the first MOST_ERRORS_SHOWN with the file's name and why, and how many more there were. */
function drawErrors(result) {
    const listed = ((result && result.errors) || []).slice(0, MOST_ERRORS_SHOWN);
    const total = (result && result.error_count) || listed.length;
    show(bulkStripErrors, total > 0);
    if (!total) return;
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

/** Tell a screen reader something, once, as it appears. */
export function say(words) {
    bulkStripLive.textContent = '';
    bulkStripLive.textContent = words;
}

function flat(title, message, problem) {
    bulkStripTitle.textContent = title;
    bulkStripTitle.title = title;
    show(bulkStripBar, false);
    bulkStripCounts.textContent = '';
    bulkStripMessage.textContent = message;
    bulkStripMessage.classList.toggle('bulk-strip-problem', problem);
    show(bulkStripErrors, false);
}

/** Draw the strip from `state.bulk`: hidden when there is nothing to say. */
export function renderBulkStrip() {
    const bulk = state.bulk;
    const request = bulk.request;
    const desc = request ? request.desc : null;
    show(bulkStrip, bulk.running || Boolean(bulk.problem) || Boolean(bulk.result));
    show(btnBulkDismiss, !bulk.running);
    if (bulk.running) {
        flat(`${titleOf({ total: request.picked, op: request.op }, desc)}...`, writingSentence(desc, request.picked), false);
        bulkStripBar.removeAttribute('value');
        show(bulkStripBar, true);
    } else if (bulk.problem) {
        flat(bulk.problem.title, bulk.problem.sentence, true);
    } else if (bulk.result) {
        const result = bulk.result;
        flat(titleOf(result, desc), endedSentence(result, desc), (result.error_count || 0) > 0);
        if (bulk.notice) bulkStripMessage.textContent += ` ${widenedSentence(bulk.notice)}`;
        bulkStripCounts.textContent = countsLine(result);
        drawErrors(result);
    } else {
        bulkStripLive.textContent = '';
    }
}

/** Wire the strip's Dismiss button: `handlers.dismiss` is bulk-run.js's. */
export function wireBulkStrip(handlers) {
    btnBulkDismiss.addEventListener('click', () => {
        // The button that had the focus is going: the focus goes to the grid, not to the top of the page.
        const inside = bulkStrip.contains(document.activeElement);
        handlers.dismiss();
        if (inside && thumbnailsGrid) thumbnailsGrid.focus({ preventScroll: true });
    });
}
