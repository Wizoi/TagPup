// TagPup's page: a video in the photo panel. A library view's card and photo carry `media` ('photo' or 'video', from the
// library's row: GET /api/library/cards and /api/library/photo), and so does a folder's (GET /api/folder/scan); a record
// without it is a photo. A video is played by the browser from GET /api/video-file by its id, which answers ranges, so
// seeking asks for the part it needs.
//
//  * a folder's clip the index has not recorded has no id: nothing is asked, and the status line says it plays once
//    the folder is indexed (its card is a placeholder until then: grid.js);
//  * the panel shows one of the two: a video hides the image and empties its src, so the zoom and the face boxes, which
//    look for a picture, stand down; a photo stops the video and shows the image (stale.js says whether it is there);
//  * the video stops whenever the panel is hidden -- Back, Escape, another view, a folder -- and when another photo is
//    shown. Stopped is no src at all: the browser lets go of the stream, and the server of the file;
//  * the same video shown again (a refresh of the panel, a save) keeps playing where it is;
//  * a video the browser cannot play says why on the status line: its file is gone, the share or the server is not
//    answering, or its format is not one this browser plays (an HEVC clip without the HEVC extension). The browser
//    gives the same error for a refused request as for a format it cannot play, so the server is asked which it was.
// Switching library is another page: the video goes with it. A video's title, people and keywords are edited as a
// photo's, and it is renamed as one is; Rotate, Date Taken and delete are inert while one is shown (stale.js
// applyPhotoStale, from its record's `writes`), as the server refuses them of a clip. Zoom stands down because there is no picture.
import { api } from './common/api.js';
import { mainImage, mainVideo, panelContent } from './elements.js';
import { setStatus } from './status.js';

/** Is `photo` a video? Only what the library says: a record without `media` is a photo. */
export function isVideo(photo) {
    return Boolean(photo) && photo.media === 'video';
}

/** The route a video is played from (not prefixed with the library: api.url and api.fetch put it there). */
function videoRoute(photo) {
    return `/api/video-file?id=${encodeURIComponent(photo.id)}&v=${encodeURIComponent(photo.mtime || 0)}`;
}

/**
 * Show `photo` in the panel: a video in the player, anything else as `pictureUrl` in the image ('' for none). Called
 * after applyPhotoStale, which has said whether the image is there; a video whose file is gone is shown as a photo
 * with no picture (the notice says why).
 */
export function showMedia(photo, pictureUrl) {
    if (!isVideo(photo) || photo.missing) {
        stopVideo();
        mainImage.src = pictureUrl;
        return;
    }
    if (photo.id === undefined) {
        // A folder's clip the index has not recorded: never its file as a picture (/api/photo-file refuses a video).
        stopVideo();
        mainImage.src = '';
        setStatus('ready', NOT_INDEXED, { transient: false });
        return;
    }
    mainImage.src = '';
    mainImage.classList.add('hidden');
    mainVideo.classList.remove('hidden');
    const route = videoRoute(photo);
    if (mainVideo.dataset.route === route) return;       // the same video again: it plays on
    mainVideo.dataset.route = route;
    mainVideo.src = api.url(route);
}

/** Stop the video and let go of its stream: no src, and load() so the browser drops what it held. */
export function stopVideo() {
    mainVideo.classList.add('hidden');
    if (!mainVideo.getAttribute('src')) return;
    mainVideo.pause();
    mainVideo.removeAttribute('src');
    delete mainVideo.dataset.route;
    mainVideo.load();
}

const FORMAT = 'This video’s format cannot be played in this browser (an HEVC video needs the HEVC extension).';
const UNREAD = 'This video could not be read.';
const GONE = 'This video’s file is not there.';
const NOT_INDEXED = 'This video has not been indexed yet: it plays here once its folder is indexed.';
const AWAY = 'This video could not be read just now: the share or the server is not answering. Open it again in a moment.';

/** The video failed: ask the server whether it refused (gone, away) before blaming the format, and say which. */
function videoFailed() {
    const route = mainVideo.dataset.route;
    if (!route) return;                                  // stopped already: an error of the stream let go
    const code = mainVideo.error ? mainVideo.error.code : 0;
    const say = (sentence) => {
        if (mainVideo.dataset.route === route) setStatus('error', sentence, { transient: false });
    };
    api.fetch(route, { headers: { Range: 'bytes=0-0' } }).then(
        res => say(res.ok ? (code === 4 ? FORMAT : UNREAD) : (res.status === 404 ? GONE : AWAY)),
        () => say(AWAY),
    );
}

/** The player's wiring: stopped whenever the panel is hidden, and a failure said on the status line. */
export function wireMedia() {
    new MutationObserver(() => {
        if (panelContent.classList.contains('hidden')) stopVideo();
    }).observe(panelContent, { attributes: true, attributeFilter: ['class'] });
    mainVideo.addEventListener('error', videoFailed);
}
