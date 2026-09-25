// TagPup's page: moving between photos, by key, swipe or drag.
import { state } from './state.js';
import { photoList, photoSearch } from './elements.js';
import { hasUnsavedEdits, leavePhotoThen } from './edits.js';
import { carryTagsForward, selectPhoto } from './photo.js';
import { undoLastOperation } from './undo.js';

/**
 * Move the selection through the list by a number of steps.
 *
 * The list order is the photo order, so this is what both the up/down keys and
 * the left/right keys use -- "the row below" and "the next photo" are the same
 * move, and someone flipping through a shoot should not have to know that.
 */
export function stepPhoto(delta) {
    // Ask before working out where to go, so the list does not scroll to a
    // photo you then decide not to leave for.
    if (hasUnsavedEdits() || state.detailSaveInFlight) {
        leavePhotoThen(() => stepPhoto(delta));
        return false;
    }
    const items = Array.from(photoList.querySelectorAll('.photo-item-file'));
    if (items.length === 0) return false;

    const currentIndex = items.findIndex(
        item => item.getAttribute('data-path') === state.activePhotoPath
    );
    let nextIndex;
    if (currentIndex === -1) {
        // Nothing selected yet: forwards starts at the top, backwards at the end.
        nextIndex = delta > 0 ? 0 : items.length - 1;
    } else {
        nextIndex = Math.min(Math.max(currentIndex + delta, 0), items.length - 1);
    }
    if (nextIndex === currentIndex) return false;

    selectPhoto(items[nextIndex].getAttribute('data-path'));
    items[nextIndex].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    return true;
}

/**
 * Should a keystroke be left to the field the caret is in?
 *
 * Only where the keystroke means something to the text being typed. The filter
 * box is not such a field: it is a search control, and the natural move is to
 * narrow the list and then walk the results with the arrow keys. A blanket INPUT
 * check used to catch it and return before preventDefault, so the browser
 * scrolled the page instead -- the keys looked broken in the one place you most
 * wanted them.
 */
export function keystrokeBelongsToField(el) {
    if (!el) return false;
    if (el === photoSearch) return false;
    return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable;
}

export function wireKeyboard() {
    document.addEventListener('keydown', (e) => {
        if (state.leavePrompt) return;   // "Save changes?" is open; its keys are its own
        if (keystrokeBelongsToField(document.activeElement)) return;

        // Ctrl+D copies the previous photo's tags onto this one, so that tagging a
        // shoot is: tag the first, then arrow across and repeat. It is checked
        // before the modifier guard below, which exists to leave browser shortcuts
        // alone.
        if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === 'd' || e.key === 'D')) {
            e.preventDefault();
            carryTagsForward();
            return;
        }
        if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === 'z' || e.key === 'Z')) {
            e.preventDefault();
            undoLastOperation();
            return;
        }
        if (e.ctrlKey || e.metaKey || e.altKey) return;

        // Left/right are the same move as up/down. Up/down reads as "the row below"
        // in the list; left/right reads as "the next photo" over the image. Both are
        // offered because which one a person reaches for depends on where they are
        // looking, and there is no reason to make them guess right.
        const steps = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 };
        if (!(e.key in steps)) return;

        e.preventDefault();
        stepPhoto(steps[e.key]);
    });
}

/**
 * Swipe or drag horizontally across the image to move between photos.
 *
 * Thresholds do the work: SWIPE_MIN_PX keeps a tap or a jitter from counting,
 * and requiring the horizontal distance to exceed the vertical keeps a scroll
 * that drifts sideways from flipping the photo. Right-to-left goes forwards,
 * matching every photo viewer people already use.
 */
export const SWIPE_MIN_PX = 60;
export const DRAG_MIN_PX = 10;
export function enableSwipeNavigation(surface) {
    if (!surface) return;
    let startX = null;
    let startY = null;
    let pointerId = null;

    surface.addEventListener('pointerdown', (e) => {
        // Primary button or touch only; ignore right-click and middle-click.
        if (e.pointerType === 'mouse' && e.button !== 0) return;
        pointerId = e.pointerId;
        startX = e.clientX;
        startY = e.clientY;
        delete surface.dataset.dragged;
    });

    const finish = (e) => {
        if (pointerId === null || e.pointerId !== pointerId) return;
        const dx = e.clientX - startX;
        const dy = e.clientY - startY;
        pointerId = null;
        startX = null;
        startY = null;

        // A drag is not a click. The browser still fires one after a mouse
        // swipe, and the image opens the zoom on click -- this is how that
        // handler tells the two apart.
        if (Math.hypot(dx, dy) > DRAG_MIN_PX) surface.dataset.dragged = 'true';

        if (Math.abs(dx) < SWIPE_MIN_PX) return;      // a tap, or a twitch
        if (Math.abs(dx) <= Math.abs(dy)) return;      // a scroll that drifted
        stepPhoto(dx < 0 ? 1 : -1);
    };

    surface.addEventListener('pointerup', finish);
    surface.addEventListener('pointercancel', () => { pointerId = null; });
}
