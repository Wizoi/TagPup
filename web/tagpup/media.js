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
//    answering, the server refused it in a sentence of its own (409: its file is being deleted, from this page or
//    another), or what it needs to play, the server's sentence (GET /api/clip/convert-plan, tagpup.files.convertible:
//    an older format, a picture or a sound no browser plays, an HEVC clip without the HEVC extension; FORMAT when the
//    server cannot say). The browser gives the same error for a refused request as for a format it cannot play, so the
//    server is asked which it was;
//  * a video whose picture the browser does not decode and says nothing (Chrome plays the sound of an MPEG-4 Part 2
//    clip with no picture and no error: its videoWidth is 0 once its first data is loaded) is asked about the same way;
//  * an answer about a video already left says nothing: every sentence is said only while its video is the one shown;
//  * which files a delete is working on is the server's to know (tagpup.files.recycle_bin.being_deleted), never this
//    page's guess: a clip is always asked for, and one that failed is asked for again when it is shown again.
// Switching library is another page: the video goes with it. A video's title, people and keywords are edited as a
// photo's, and it is renamed and deleted as one is (a delete stops it first: photo.js deleteActivePhoto); Rotate and Date
// Taken are inert while one is shown (stale.js applyPhotoStale, from its record's `writes`), as the server refuses them of a
// clip. Zoom stands down because there is no picture. Its Convert and Make smaller follow the clip shown, Convert offered
// from the plan read here (clip-convert.js).
import { api } from './common/api.js';
import { videoRoute } from './common/file-addresses.js';
import { clipActionsFollow, offerConvert } from './clip-convert.js';
import { detailFileSize, detailVideoSize, mainImage, mainVideo, panelContent } from './elements.js';
import { fileSizeSaid } from './format.js';
import { setStatus } from './status.js';
import { state } from './state.js';

/** Is `photo` a video? Only what the library says: a record without `media` is a photo. */
export function isVideo(photo) {
    return Boolean(photo) && photo.media === 'video';
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
    if (mainVideo.dataset.route === route && !mainVideo.error) return;   // the same video again: it plays on (one that failed is asked again)
    mainVideo.dataset.route = route;
    mainVideo.dataset.clip = String(photo.id);
    mainVideo.src = api.url(route);
    clipActionsFollow();
}

/** Stop the video and let go of its stream: no src, and load() so the browser drops what it held. */
export function stopVideo() {
    mainVideo.classList.add('hidden');
    if (!mainVideo.getAttribute('src')) return;
    mainVideo.pause();
    mainVideo.removeAttribute('src');
    delete mainVideo.dataset.route;
    delete mainVideo.dataset.clip;
    mainVideo.load();
    clipActionsFollow();
}


/**
 * A write to a photo file is about to start: a clip playing lets go of its file (the server reads it a range at a time, and a
 * range being read as the file is written makes Windows refuse the write), and carries on after `resumeAfterWrite`. A clip
 * that is paused, or none shown, is left alone; a second call while one is held changes nothing.
 */
export function holdForWrite() {
    if (state.heldForWrite || !mainVideo.getAttribute('src') || mainVideo.paused || mainVideo.ended) return;
    state.heldForWrite = { clip: mainVideo.dataset.clip, time: mainVideo.currentTime };
    mainVideo.pause();
    mainVideo.removeAttribute('src');
    mainVideo.load();
}

/** The writes are done: the held clip plays on from where it stopped, when it is still the one shown (the file may have changed: its route is asked again). */
export function resumeAfterWrite() {
    const held = state.heldForWrite;
    state.heldForWrite = null;
    if (!held || mainVideo.dataset.clip !== held.clip || mainVideo.classList.contains('hidden')) return;
    const start = () => {
        mainVideo.currentTime = held.time;
        mainVideo.play().catch(() => {});   // the browser may refuse a play it did not see asked for: the clip then waits, paused
    };
    // A redraw may have set the clip again already (showMedia, the new mtime in its route): it is only seeked and played.
    if (!mainVideo.getAttribute('src')) mainVideo.src = api.url(mainVideo.dataset.route);
    if (mainVideo.readyState >= 1) start();
    else mainVideo.addEventListener('loadedmetadata', start, { once: true });
}

const FORMAT = 'This video’s format cannot be played in this browser (an HEVC video needs the HEVC extension).';
const UNREAD = 'This video could not be read.';
const GONE = 'This video’s file is not there.';
const NOT_INDEXED = 'This video has not been indexed yet: it plays here once its folder is indexed.';
const AWAY = 'This video could not be read just now: the share or the server is not answering. Open it again in a moment.';

/** `sentence` on the status line, while the video at `route` is still the one shown. */
function sayOf(route, sentence) {
    if (mainVideo.dataset.route === route) setStatus('error', sentence, { transient: false });
}

/**
 * Ask the server what clip `clip`, shown from `route`, needs before this browser plays it, and say its sentence (or its
 * refusal's: a share away, ffprobe unable to read it); `otherwise` when no sentence comes back.
 */
function sayWhatItNeeds(route, clip, otherwise) {
    if (mainVideo.dataset.route !== route) return;
    api.fetch(`/api/clip/convert-plan?id=${encodeURIComponent(clip)}`).then(
        res => res.json().then(
            body => {
                sayOf(route, (body && (res.ok ? body.sentence : body.error)) || otherwise);
                if (res.ok) offerConvert(route, clip, body);
            },
            () => sayOf(route, otherwise)),
        () => sayOf(route, otherwise),
    );
}

/** The video failed: ask the server whether it refused (gone, away, in a sentence of its own) before asking what it needs to play, and say which. */
function videoFailed() {
    const route = mainVideo.dataset.route;
    if (!route) return;                                  // stopped already: an error of the stream let go
    const clip = mainVideo.dataset.clip;
    const code = mainVideo.error ? mainVideo.error.code : 0;
    const say = (sentence) => sayOf(route, sentence);
    api.fetch(route, { headers: { Range: 'bytes=0-0' } }).then(
        res => {
            if (res.ok) return code === 3 || code === 4 ? sayWhatItNeeds(route, clip, code === 4 ? FORMAT : UNREAD) : say(UNREAD);   // a stream that broke (2) is no verdict on the format
            if (res.status === 404) return say(GONE);
            if (res.status === 409) return res.json().then(body => say(body.error || AWAY), () => say(AWAY));
            return say(AWAY);
        },
        () => say(AWAY),
    );
}

/** Its first data is loaded and there is no picture, with no error: a picture this browser does not decode. */
function pictureMissing() {
    const route = mainVideo.dataset.route;
    if (!route || mainVideo.error || mainVideo.videoWidth > 0) return;
    sayWhatItNeeds(route, mainVideo.dataset.clip, FORMAT);
}

/** The player's wiring: stopped whenever the panel is hidden, and a failure said on the status line. */
export function wireMedia() {
    new MutationObserver(() => {
        if (panelContent.classList.contains('hidden')) stopVideo();
    }).observe(panelContent, { attributes: true, attributeFilter: ['class'] });
    mainVideo.addEventListener('error', videoFailed);
    mainVideo.addEventListener('loadeddata', pictureMissing);
}

/** The size sits under the path for a photo and under Date Taken for a video: one formatter, one of the two lines shown. */
export function showFileSize(photo) {
    const video = isVideo(photo), said = fileSizeSaid(photo.size);
    detailFileSize.textContent = video ? '' : said;
    detailVideoSize.textContent = video ? said : '';
    detailVideoSize.classList.toggle('hidden', !video);
}
