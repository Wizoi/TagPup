// TagPup's page: the Selection Details panel in a narrow window (docs/findings.md, #720; owner, 2026-10-08).
//
// Beside the sidebar (360 px) the panel (340 px) left the grid 296 px at a 1,000 px window and 40 px at 720, where the page scrolled
// sideways. Under 1,100 px (style.css) the panel is out of the row and this button, in the grid's header beside Select All, brings it
// over the grid's right edge and takes it away again; a wide window shows the panel beside the grid and the button not at all. The
// CSS decides what is narrow, so nothing here measures the window: this keeps the button honest (aria-expanded, aria-controls) and
// what this browser last chose (closed until it is opened). The choice is `state.detailsPanel.open`. Opened, the panel covers the
// right of the grid's header, the Details button with it, so the panel has a button of its own to close it; the focus follows: to
// the panel's button as it opens, back to Details as it closes.
import { state } from './state.js';
import { btnDetailsClose, btnDetailsToggle, folderSelectionSidebar } from './elements.js';

/** Where this browser keeps whether the panel is open in a narrow window ('open' or 'closed'). */
export const DETAILS_KEY = 'tagpup.detailsPanel';

function show() {
    const open = state.detailsPanel.open;
    folderSelectionSidebar.classList.toggle('details-open', open);
    btnDetailsToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
}

function setOpen(open, { moveFocus = true } = {}) {
    state.detailsPanel.open = open;
    try {
        window.localStorage.setItem(DETAILS_KEY, open ? 'open' : 'closed');
    } catch (err) { /* this browser keeps nothing: the choice lasts until the page is closed */ }
    show();
    if (moveFocus) (open ? btnDetailsClose : btnDetailsToggle).focus();
}

export function wireDetailsPanel() {
    if (!btnDetailsToggle || !btnDetailsClose || !folderSelectionSidebar) return;
    try {
        state.detailsPanel.open = window.localStorage.getItem(DETAILS_KEY) === 'open';
    } catch (err) { /* closed, then */ }
    btnDetailsToggle.addEventListener('click', () => setOpen(!state.detailsPanel.open));
    btnDetailsClose.addEventListener('click', () => setOpen(false));
    show();
}
