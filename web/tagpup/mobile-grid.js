/**
 * The phone's Grid screen (docs/plans/phone-viewer.md, Screens 3). For now it says how many photos the view holds: the
 * thumbnails come next (cut 2b). The view is the Grid's place in history (mobile-screens.js), set by Browse's "Show these
 * photos" and Search, or by an address naming one; `state.view` is it while the Grid is shown.
 *
 * Its ids are asked with the request the desktop page's views make (library-source.js `idsRequest`); a reply to an older
 * ask is dropped, and a read that fails says so with Try again rather than "0 photos".
 */
import { api } from './common/api.js';
import { emptySentence, idsRequest, viewHeading } from './library-source.js';
import { state } from './mobile-state.js';

/** Ask how many photos the Grid's view holds, and say it. */
export function showGrid() {
    const view = state.place;
    state.view = view;
    if (!view) return;
    const asked = ++state.gridAsked;
    const count = document.getElementById('grid-count');
    const problem = document.getElementById('grid-problem');
    document.getElementById('grid-heading').textContent = viewHeading(view);
    count.textContent = 'Loading...';
    problem.classList.add('hidden');
    api.fetch(...idsRequest(view))
        .then((res) => res.json().catch(() => ({})).then((body) => ({ ok: res.ok, body })))
        .then(({ ok, body }) => {
            if (asked !== state.gridAsked) return;
            if (!ok) throw new Error((body && body.error) || '');
            const total = Number.isFinite(body.total) ? body.total : (Array.isArray(body.ids) ? body.ids.length : 0);
            count.textContent = total === 0 ? emptySentence({ ...view, of: body.of })
                : `${total.toLocaleString()} ${total === 1 ? 'photo' : 'photos'}`;
        })
        .catch((err) => {
            if (asked !== state.gridAsked) return;
            count.textContent = '';
            document.getElementById('grid-problem-text').textContent = `Could not load photos. ${err.message}`.trim();
            problem.classList.remove('hidden');
        });
}

/** Try again asks once more. */
export function wireGrid() {
    document.getElementById('grid-retry').addEventListener('click', showGrid);
}
