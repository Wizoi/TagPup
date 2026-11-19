// TagPup's page: the note over the photo while a move off it waits for its save (edits.js leavePhotoThen).
//
// The status line in the corner said "Saving..." and nothing near the photo did: the page looked stuck, and the arrow presses
// made meanwhile each moved on once the save was written, skipping photos (owner, 2026-11-19). The note is not a dialog:
// it takes no focus and holds no keys, and it covers only the photo, never the status line or Undo.
import { buildElement } from './common/dom.js';
import { imageViewer } from './elements.js';

// A save written within this long moves on with nothing shown: the note would only flash.
export const MOVE_WAIT_SHOWN_AFTER_MS = 300;

/** Put the note over the photo; `letGo` is its Stay here. Returns the note, for hideMoveWaiting. */
export function showMoveWaiting(fileName, letGo) {
    const stay = buildElement('button', { className: 'btn btn-secondary btn-sm', text: 'Stay here' });
    stay.addEventListener('click', letGo);
    const note = buildElement('div', { className: 'move-waiting', attrs: { role: 'status', 'aria-live': 'polite' } }, [
        // The file name goes in as text, never as markup.
        buildElement('span', { text: `Saving ${fileName}... You will move on when it is saved (Esc stays here).` }),
        stay,
    ]);
    imageViewer.appendChild(note);
    return note;
}

/** Take the note away; `note` is what showMoveWaiting returned, or null when it was never shown. */
export function hideMoveWaiting(note) {
    if (note) note.remove();
}
