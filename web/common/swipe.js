/**
 * Swipe or drag horizontally across a photo to move between photos: the one swipe both TagPup pages use (the desktop
 * page's open photo, web/tagpup/navigation.js; the phone's Photo screen, web/tagpup/mobile-photo.js). Each page says
 * what a step is: `step(1)` the next photo, `step(-1)` the one before.
 *
 * Thresholds do the work: SWIPE_MIN_PX keeps a tap or a jitter from counting, and requiring the horizontal distance to
 * exceed the vertical keeps a scroll that drifts sideways from flipping the photo. Right-to-left goes forwards,
 * matching every photo viewer people already use.
 */
export const SWIPE_MIN_PX = 60;
export const DRAG_MIN_PX = 10;

export function enableSwipeNavigation(surface, step) {
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
        step(dx < 0 ? 1 : -1);
    };

    surface.addEventListener('pointerup', finish);
    surface.addEventListener('pointercancel', () => { pointerId = null; });
}
