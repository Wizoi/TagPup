/**
 * Whether a dialog is open over the page: the one rule the pages' shortcuts consult.
 *
 * TagPup steps its photos on the arrows and acts on Ctrl+Z, Ctrl+D and Ctrl+S; TagTuner
 * moves its sidebar on ArrowUp and ArrowDown. They listen on the document, so a key
 * pressed in a dialog -- or on its background, where a click leaves the focus -- went
 * on to act on the page behind it: a photo stepped, tags carried over, an undo run,
 * with the question still open in front. The tag editor kept those keys with a guard
 * of its own; no other dialog did. Now each page handler asks dialogOpen() first and
 * leaves the key alone while one is open, whichever dialog it is.
 *
 * A dialog is open when it is shown the pages' way: a `.modal-overlay` or the shared
 * `.tag-editor` (its delete question included) with `active`, or a TagTuner `.modal`
 * without `hidden`. A new dialog built from those classes is covered without a line
 * here; tests/frontend/dialogs-keep-the-keys.test.mjs opens each one.
 */

/** What an open dialog matches. */
export const OPEN_DIALOG = '.modal-overlay.active, .tag-editor.active, .modal:not(.hidden)';

/** Is a dialog open over the page? */
export function dialogOpen() {
    return document.querySelector(OPEN_DIALOG) !== null;
}
