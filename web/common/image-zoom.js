/**
 * A photo as large as the window allows -- the one zoom both pages use.
 *
 * TagPup zooms the open photo, to read a sign or a name tag; TagTuner zooms the photo a
 * face was found in, to see the face where it sits. Each page asks for the same thing:
 * openImageZoom(original, { preview, box, opener }). The overlay is made here, once, by
 * wireImageZoom(); its look is web/common/image-zoom.css.
 *
 * The preview the page already shows goes behind as a background, scaled the same way,
 * so the first frame is not empty; the original is the image itself and paints over it
 * when it arrives. If the original is a format the browser cannot draw (TIFF, HEIC) the
 * preview stays, and says so. If neither loads, the overlay says that, rather than show
 * a black window.
 *
 * `box` is [x1, y1, x2, y2] in the stored pixels of the original. It is drawn only over
 * the original, whose pixels those are: over a preview its size is unknown, and a box
 * drawn from a guess is a box on the wrong face. The image is letterboxed (object-fit:
 * contain), so the box is placed by the same arithmetic as the picture, not by the
 * element's size. Sideways photos (EXIF orientation 5-8) are a known open issue: the
 * browser turns the picture, the box stays in the stored pixels.
 *
 * A page that draws over the picture itself (TagPup's boxes on every face of the open photo, with a panel to name
 * each, #859) asks where the picture sits (zoomedPicture), draws into the layer it is given, and hears when that
 * may have changed (onImageZoomChange: opened, the original has come, resized, closed). Clicks in that layer do
 * not close the zoom; a click or Escape elsewhere asks the page first (onImageZoomDismiss), so a panel open over the
 * picture goes before the zoom does.
 *
 * Click or Escape closes it, and the focus goes back to what opened it. While it is
 * open the keys that move around the page behind it do nothing, rather than change the
 * photo underneath. It shows what it was opened with: a selection that changes behind
 * it does not change it, and nothing the owner can reach changes the selection while it
 * covers the window.
 */

const zoom = {
    overlay: null,
    image: null,
    boxEl: null,
    note: null,
    /** The layer a page draws over the picture in (zoomedPicture). */
    layer: null,
    /** What to return the focus to on close. */
    opener: null,
    preview: '',
    box: null,
    /** The preview has replaced an original that would not load. */
    fellBack: false,
};

/** What a page asked to hear: `change` when the picture may have moved, `dismiss` before a click or Escape closes the zoom. */
const zoomListeners = { change: [], dismiss: [] };

/** Call `listener()` when what zoomedPicture() says may have changed. */
export function onImageZoomChange(listener) {
    zoomListeners.change.push(listener);
}

/**
 * Call `handler()` before a click on the backdrop or Escape closes the zoom; if it answers true it has dismissed
 * something of its own (a panel over the picture) and the zoom stays.
 */
export function onImageZoomDismiss(handler) {
    zoomListeners.dismiss.push(handler);
}

function zoomChanged() {
    for (const listener of zoomListeners.change) listener();
}

/** A click or Escape: the page's own first, then the zoom. */
function dismissZoom() {
    if (zoomListeners.dismiss.some((handler) => handler() === true)) return;
    closeImageZoom();
}

/**
 * Where the picture sits while the zoom is open, for a page to draw over it: {layer, left, top, width, height}, the
 * layer (inside the overlay, above the picture, taking no pointer itself) and the picture's room in it -- the image's
 * content box, which `contain` fits the original (or its smaller copy behind it) inside, so a page places a box with
 * boxInContainedImage as it does over its own photo. Null when the zoom is closed or shows nothing.
 */
export function zoomedPicture() {
    if (!imageZoomOpen() || !zoom.image.getAttribute('src')) return null;
    const rect = zoom.image.getBoundingClientRect();
    const frame = zoom.overlay.getBoundingClientRect();
    const pad = parseFloat(window.getComputedStyle(zoom.image).paddingLeft) || 0;
    return {
        layer: zoom.layer,
        left: rect.left - frame.left + pad,
        top: rect.top - frame.top + pad,
        width: rect.width - 2 * pad,
        height: rect.height - 2 * pad,
    };
}

/** Keys that move around the page behind: kept from it while the zoom is open. */
const PAGE_KEYS = ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Enter', ' ',
    'PageUp', 'PageDown', 'Home', 'End'];

/** The layer a page draws over the picture in (zoomedPicture says where, while it is open); null before wireImageZoom. */
export function zoomLayer() {
    return zoom.layer;
}

/** Is the zoom open? */
export function imageZoomOpen() {
    return Boolean(zoom.overlay) && !zoom.overlay.classList.contains('hidden');
}

/**
 * Where `box` falls on an image drawn `contain` inside `area` ({width, height}, the
 * room it has), as {left, top, width, height} from the area's top left; null when there
 * is nothing to draw (no sizes, a box that is not four numbers, or one outside the picture).
 */
export function boxInContainedImage(natural, area, box) {
    const sizes = [natural && natural.width, natural && natural.height, area && area.width, area && area.height];
    if (!sizes.every((n) => Number.isFinite(n) && n > 0)) return null;
    if (!Array.isArray(box) || box.length < 4 || !box.slice(0, 4).every(Number.isFinite)) return null;
    const scale = Math.min(area.width / natural.width, area.height / natural.height);
    const offsetX = (area.width - natural.width * scale) / 2;
    const offsetY = (area.height - natural.height * scale) / 2;
    const clamp = (value, top) => Math.min(Math.max(value, 0), top);
    const x1 = clamp(Math.min(box[0], box[2]), natural.width);
    const x2 = clamp(Math.max(box[0], box[2]), natural.width);
    const y1 = clamp(Math.min(box[1], box[3]), natural.height);
    const y2 = clamp(Math.max(box[1], box[3]), natural.height);
    if (x2 <= x1 || y2 <= y1) return null;
    return {
        left: offsetX + x1 * scale,
        top: offsetY + y1 * scale,
        width: (x2 - x1) * scale,
        height: (y2 - y1) * scale,
    };
}

function say(text) {
    zoom.note.textContent = text || '';
    zoom.note.classList.toggle('hidden', !text);
}

/** Draw the box over the original, or hide it. */
function placeBox() {
    const el = zoom.boxEl;
    const image = zoom.image;
    el.classList.add('hidden');
    if (!imageZoomOpen() || !zoom.box || zoom.fellBack || !image.getAttribute('src')) return;
    if (!image.complete || !image.naturalWidth) return;
    const rect = image.getBoundingClientRect();
    const frame = zoom.overlay.getBoundingClientRect();
    const pad = parseFloat(window.getComputedStyle(image).paddingLeft) || 0;
    const placed = boxInContainedImage(
        { width: image.naturalWidth, height: image.naturalHeight },
        { width: rect.width - 2 * pad, height: rect.height - 2 * pad },
        zoom.box);
    if (!placed) return;
    el.style.left = `${rect.left - frame.left + pad + placed.left}px`;
    el.style.top = `${rect.top - frame.top + pad + placed.top}px`;
    el.style.width = `${placed.width}px`;
    el.style.height = `${placed.height}px`;
    el.classList.remove('hidden');
}

/**
 * Open the zoom on `src`, the original. `preview` is the copy the page already shows,
 * `box` the face's [x1, y1, x2, y2] in the original's pixels, `opener` what gets the
 * focus back (the focused element when omitted). Opening it again replaces what it shows.
 */
export function openImageZoom(src, { preview = '', box = null, opener = null } = {}) {
    if (!zoom.overlay || !src) return;
    if (!imageZoomOpen()) zoom.opener = opener || document.activeElement;
    zoom.preview = preview;
    zoom.box = box;
    zoom.fellBack = false;
    say('');
    zoom.boxEl.classList.add('hidden');
    zoom.image.style.backgroundImage = preview ? `url("${preview}")` : '';
    zoom.image.src = src;
    zoom.overlay.classList.remove('hidden');
    zoom.overlay.focus({ preventScroll: true });
    zoomChanged();
}

/** Close it, and give the focus back. */
export function closeImageZoom() {
    if (!imageZoomOpen()) return;
    zoom.overlay.classList.add('hidden');
    zoom.image.removeAttribute('src');          // stop a large download nobody wants now
    zoom.image.style.backgroundImage = '';
    zoom.boxEl.classList.add('hidden');
    say('');
    const opener = zoom.opener;
    zoom.opener = null;
    zoom.box = null;
    zoomChanged();
    if (opener && opener !== document.body && opener.isConnected && typeof opener.focus === 'function') {
        opener.focus({ preventScroll: true });
    }
}

/** Make the overlay (once) and listen for what closes it. Each page calls it from its main. */
export function wireImageZoom() {
    if (zoom.overlay) return;
    const overlay = document.getElementById('image-zoom') || document.createElement('div');
    overlay.id = 'image-zoom';
    overlay.className = 'image-zoom-overlay hidden';
    overlay.title = 'Click or press Escape to close';
    overlay.tabIndex = -1;
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', 'Photo, enlarged');
    const image = document.createElement('img');
    image.id = 'image-zoom-img';
    image.alt = '';
    const boxEl = document.createElement('div');
    boxEl.className = 'image-zoom-box hidden';
    const note = document.createElement('div');
    note.className = 'image-zoom-note hidden';
    note.setAttribute('role', 'status');
    const layer = document.createElement('div');
    layer.className = 'image-zoom-layer';
    // What is drawn in it is the page's own, and a click in it is not a click on the backdrop.
    layer.addEventListener('click', (e) => e.stopPropagation());
    overlay.replaceChildren(image, boxEl, note, layer);
    if (!overlay.isConnected) document.body.append(overlay);
    Object.assign(zoom, { overlay, image, boxEl, note, layer });

    overlay.addEventListener('click', dismissZoom);
    image.addEventListener('load', () => {
        if (!imageZoomOpen() || !image.getAttribute('src')) return;
        // The original (or the fallback preview) has arrived; drop the background so a
        // transparent PNG does not show the preview through.
        image.style.backgroundImage = '';
        placeBox();
        zoomChanged();
    });
    image.addEventListener('error', () => {
        if (!imageZoomOpen() || !image.getAttribute('src')) return;
        // Not drawable here: fall back to the preview, scaled up. Once only -- the preview
        // failing too must not loop.
        if (zoom.preview && !zoom.fellBack && image.getAttribute('src') !== zoom.preview) {
            zoom.fellBack = true;
            zoom.boxEl.classList.add('hidden');
            say('The original could not be shown here; this is the smaller copy.');
            image.src = zoom.preview;
            return;
        }
        image.removeAttribute('src');
        image.style.backgroundImage = '';
        zoom.boxEl.classList.add('hidden');
        say('This photo could not be loaded.');
    });
    window.addEventListener('resize', () => {
        placeBox();
        if (imageZoomOpen()) zoomChanged();
    });

    // Captured, so it is decided before a page's own key handling hears it. Ctrl+S and
    // the other chords are left alone. Escape closes the zoom and nothing else: text
    // typed in a box elsewhere on the page is not abandoned.
    document.addEventListener('keydown', (e) => {
        if (!imageZoomOpen()) return;
        if (e.key === 'Escape') {
            e.preventDefault();
            e.stopPropagation();
            dismissZoom();
            return;
        }
        if (PAGE_KEYS.includes(e.key)) {
            e.preventDefault();
            e.stopPropagation();
        }
    }, true);
}
