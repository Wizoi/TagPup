/**
 * The pages' dialogs and the keyboard: whether one is open, and how one keeps the keys.
 *
 * TagPup steps its photos on the arrows and acts on Ctrl+Z, Ctrl+D and Ctrl+S; TagTuner
 * moves its sidebar on ArrowUp and ArrowDown. They listen on the document, so a key
 * pressed in a dialog -- or on its background, where a click leaves the focus -- went
 * on to act on the page behind it: a photo stepped, tags carried over, an undo run,
 * with the question still open in front. Each page handler asks dialogOpen() first and
 * leaves the key alone while one is open, whichever dialog it is.
 *
 * A dialog is open when it is shown the pages' way: a `.modal-overlay` or the shared
 * `.tag-editor` (its delete question included) with `active`, or a TagTuner `.modal`
 * without `hidden`. A new dialog built from those classes is covered without a line
 * here; tests/frontend/dialogs-keep-the-keys.test.mjs opens each one.
 *
 * Every dialog keeps the keyboard the same way, here (owner, 2026-11-15): Escape cancels
 * it, Tab and Shift+Tab go round its controls, and the focus goes back to what opened it,
 * so the arrows step photos again at once after Date Taken is saved or cancelled. A
 * dialog calls holdFocus() right after it is shown and releaseFocus() right after it is
 * hidden; the page calls wireDialogKeys() once, where it wires its other features. Enter
 * is left to the browser and to each dialog: on a focused button it presses that button.
 */

/** What an open dialog matches. */
export const OPEN_DIALOG = '.modal-overlay.active, .tag-editor.active, .modal:not(.hidden)';

/** Is a dialog open over the page? */
export function dialogOpen() {
    return document.querySelector(OPEN_DIALOG) !== null;
}

// The dialogs holding the keyboard, the top one last: { dialog, cancel, opener }.
const heldDialogs = [];
// The keys down now, and those whose repeats are swallowed: a key held as a dialog opens or
// closes acts once (Escape held closes one dialog, and its repeats do not reach the page's
// Back; Enter held on the Date Taken button does not save from the field on its repeat).
const keysDown = new Set();
const keysSwallowed = new Set();
// Where the focus waits to go until Enter is up: a dialog opened by an Enter keydown (the placement question, asked as a tag is
// typed) focused a button before the same key's keypress, and the keypress pressed it (#3037); one closed on an Enter keydown
// (Assign cluster's name field) put the focus back on its opener, a button, the same way (#3039). A function that moves it.
let afterEnter = null;
let dialogKeysWired = false;

const TAB_STOPS = 'a[href], button, input, select, textarea, [tabindex], [contenteditable="true"]';

function stillOpen(entry) {
    return entry.dialog.isConnected && entry.dialog.matches(OPEN_DIALOG);
}

/** The dialog on top, entries no longer shown dropped: a close that forgot to release cannot trap the keyboard. */
function topDialog() {
    for (let i = heldDialogs.length - 1; i >= 0; i--) {
        if (!stillOpen(heldDialogs[i])) heldDialogs.splice(i, 1);
    }
    return heldDialogs.length ? heldDialogs[heldDialogs.length - 1] : null;
}

/** Is it shown: no ancestor hidden by attribute, by the pages' `.hidden` class, or by display:none. */
function shownOnPage(element) {
    const view = element.ownerDocument.defaultView;
    for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
        if (node.hidden || node.classList.contains('hidden')) return false;
        if (view.getComputedStyle(node).display === 'none') return false;
    }
    return true;
}

/** Can Tab reach it: enabled, in the tab order, shown; a radio only if checked or if none of its group is. */
function tabReaches(element, dialog) {
    if (element.disabled || element.tabIndex < 0) return false;
    if (element.type === 'radio' && !element.checked && element.name) {
        const group = [...dialog.querySelectorAll('input[type="radio"]')].filter(radio => radio.name === element.name);
        if (group.some(radio => radio.checked)) return false;
    }
    return shownOnPage(element);
}

/** The first (or last) element Tab reaches in the dialog: only the ends are looked at, cheap on a big tag tree. */
function tabEnd(dialog, last = false) {
    const all = dialog.querySelectorAll(TAB_STOPS);
    for (let k = 0; k < all.length; k++) {
        const element = all[last ? all.length - 1 - k : k];
        if (tabReaches(element, dialog)) return element;
    }
    return null;
}

/** The keys down now are swallowed on their repeats, until they are let go or pressed again. */
function swallowHeldKeys() {
    for (const key of keysDown) keysSwallowed.add(key);
}

/**
 * The dialog holds the keyboard: what has the focus now is where it goes back to, and the focus moves
 * to `focus`, else the first enabled primary button, else the first control Tab reaches. Asked again while
 * it holds (a rapid second click), nothing changes: the first opener is kept. `cancel()` is what Escape does.
 */
export function holdFocus(dialog, { cancel, focus } = {}) {
    topDialog();
    if (!dialog) return;
    if (heldDialogs.some(entry => entry.dialog === dialog)) {
        // Asked again (a rapid second click) while the first rebuilt what held the focus: the focus is brought back in.
        if (!dialog.contains(document.activeElement) && !keysDown.has('Enter')) tabEnd(dialog)?.focus();
        return;
    }
    heldDialogs.push({ dialog, cancel: typeof cancel === 'function' ? cancel : null, opener: document.activeElement });
    swallowHeldKeys();
    const primary = [...dialog.querySelectorAll('.btn-primary')].find(button => tabReaches(button, dialog));
    const target = (focus && focus.isConnected && !focus.disabled ? focus : null) || primary || tabEnd(dialog);
    if (target && target.tagName === 'BUTTON' && keysDown.has('Enter')) {
        afterEnter = () => {
            if (target.isConnected && !target.disabled && heldDialogs.some(entry => entry.dialog.contains(target))) target.focus();
        };
    } else if (target) target.focus();
}

/** Is the opener still there to go back to: in the page, enabled and shown. */
function canGoBackTo(opener) {
    return Boolean(opener && opener !== opener.ownerDocument.body && opener.isConnected
        && typeof opener.focus === 'function' && !opener.disabled && shownOnPage(opener));
}

/**
 * The dialog, hidden or removed, lets the keyboard go. If it was on top, the focus goes back to its opener when
 * that is still there; otherwise to the first control of the dialog now on top, else nowhere (the body).
 */
export function releaseFocus(dialog) {
    const at = heldDialogs.findIndex(entry => entry.dialog === dialog);
    if (at < 0) return;
    const onTop = !heldDialogs.slice(at + 1).some(stillOpen);
    const [entry] = heldDialogs.splice(at, 1);
    swallowHeldKeys();
    if (!onTop) return;
    if (canGoBackTo(entry.opener)) {
        if (keysDown.has('Enter')) afterEnter = () => { if (canGoBackTo(entry.opener)) entry.opener.focus(); };
        else entry.opener.focus();
        return;
    }
    const under = topDialog();
    const inside = under ? tabEnd(under.dialog) : null;
    if (inside) inside.focus();
    else if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
}

/** Tab and Shift+Tab go round the top dialog's controls; a focus outside it (the body) is brought in. */
function keepTabInside(event, dialog) {
    const first = tabEnd(dialog);
    if (!first) {
        event.preventDefault();
        return;
    }
    const last = tabEnd(dialog, true);
    const at = document.activeElement;
    // Before the first (the dialog itself, which holds it) or after the last: Tab would leave the dialog.
    const before = (element) => element !== first && Boolean(element.compareDocumentPosition(first) & element.DOCUMENT_POSITION_FOLLOWING);
    const after = (element) => element !== last
        && Boolean(last.compareDocumentPosition(element) & element.DOCUMENT_POSITION_FOLLOWING);
    let to = null;
    if (!at || !dialog.contains(at)) to = event.shiftKey ? last : first;
    else if (event.shiftKey && (at === first || before(at))) to = last;
    else if (!event.shiftKey && (at === last || after(at))) to = first;
    if (to) {
        event.preventDefault();
        to.focus();
    }
}

function onDialogKeyDown(event) {
    keysDown.add(event.key);
    if (event.repeat && keysSwallowed.has(event.key)) {
        event.preventDefault();
        event.stopPropagation();
        return;
    }
    if (!event.repeat) keysSwallowed.delete(event.key);
    if (event.key !== 'Escape' && event.key !== 'Tab') return;
    const top = topDialog();
    if (!top) return;
    if (event.key === 'Escape') {
        // Nothing underneath hears it: a dialog over another closes alone, and the page's Back stays still.
        event.preventDefault();
        event.stopPropagation();
        if (top.cancel) top.cancel();
        return;
    }
    if (event.ctrlKey || event.altKey || event.metaKey) return;
    keepTabInside(event, top.dialog);
}

function onDialogKeyUp(event) {
    keysDown.delete(event.key);
    keysSwallowed.delete(event.key);
    if (event.key !== 'Enter' || !afterEnter) return;
    const move = afterEnter;
    afterEnter = null;
    move();
}

/** The page's one pair of key listeners for every dialog: on the window, capturing, so they hear a key first. */
export function wireDialogKeys() {
    if (dialogKeysWired) return;
    dialogKeysWired = true;
    window.addEventListener('keydown', onDialogKeyDown, true);
    window.addEventListener('keyup', onDialogKeyUp, true);
}
