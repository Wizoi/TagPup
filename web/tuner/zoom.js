// TagTuner's page: the photos that open the shared zoom (web/common/image-zoom.js) --
// the Face Crop Details pane's photo, with the face's box drawn on it, and the Original
// Image of the photo being tuned. Both show a small copy (512 and 1024 pixels); the zoom
// asks for the file itself, from the same route, with no size.
import { api } from './common/api.js';
import { openImageZoom, wireImageZoom } from './common/image-zoom.js';
import { state } from './state.js';

/** Open `image` (a small copy on the page) in the zoom: its original, `box` over it. */
function zoomPhoto(image, photoPath, box) {
    if (!image.getAttribute('src') || !photoPath) return;
    openImageZoom(api.image(`/api/photo-file?path=${encodeURIComponent(photoPath)}`),
        { preview: image.src, box, opener: image });
}

/** Click, or Enter or Space on a photo that has focus. */
function onActivate(image, what) {
    image.addEventListener('click', () => {
        const target = what();
        zoomPhoto(image, target.path, target.box);
    });
    image.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        e.preventDefault();                       // Space would scroll the pane
        const target = what();
        zoomPhoto(image, target.path, target.box);
    });
}

export function wireZoom() {
    wireImageZoom();
    const pane = document.getElementById('matching-detail-img');
    if (pane) {
        // The face in the pane is the one `showFaceDetails` last showed; its box is in
        // the stored pixels of the file, which is what the zoom's original is.
        onActivate(pane, () => {
            const face = state.detailFace;
            return { path: face && face.photo_path, box: face && face.box };
        });
    }
    const original = document.getElementById('main-image');
    if (original) {
        // The photo is the one the image shows, whatever the page last selected.
        onActivate(original, () => ({
            path: original.getAttribute('src') ? new URL(original.src).searchParams.get('path') : null,
            box: null,
        }));
    }
}
