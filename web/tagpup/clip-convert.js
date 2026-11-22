// TagPup's page: the clip panel's Convert and Make smaller (MP4), beside its Delete: one clip, by its id, converted to an
// MP4 as a batch of one on a thread of the server (POST /api/clip/convert, tagpup.services.convert), answered at once with
// a ticket, how it ended read at /api/clip/converted?ticket=.
//  * Convert is offered for a clip the browser does not play whose plan says REENCODE: the plan media.js asked when the
//    clip failed (GET /api/clip/convert-plan), never asked a second time. Make smaller is offered for a clip that plays
//    with a picture (videoWidth above 0): nothing is asked of such a clip until the button is clicked, when its plan is
//    asked once (an ffprobe on the share for every clip shown is not paid for a button seldom pressed). The confirm is the
//    plan's `replaces`; the mode sent is the button's (one that is not the clip's plan is refused by the server, said).
//  * A ticket live for a clip disables its buttons (rapid clicks send one), shows a line saying so and a Cancel (by the
//    ticket: /api/activity/batches/cancel, as the Activity strip's), wherever the owner goes and comes back to it.
//  * The ticket is read every TICKET_READ_MS while this page is open, wherever it is; its end is taken by the clip's own record,
//    held from the click: its path follows the MP4, its stamp is the MP4's. The panel is redrawn only while that clip is
//    still the one shown: a photo stepped to meanwhile is not touched. A closed tab, or another library (another page),
//    stops the reading and loses nothing: the server finishes the conversion and the clip's row follows the MP4.
import { api } from './common/api.js';
import { cancelByTicket } from './common/batch-cancel.js';
import { showEnded } from './common/batch-strip.js';
import { baseName, samePath } from './common/paths.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import { btnClipConvert, btnClipConvertCancel, btnClipShrink, clipConvertNote, mainVideo } from './elements.js';
import { setStatus } from './status.js';
import { recordsWritten } from './cache.js';
import { hasUnsavedEdits, photoRenamed, queueWriteOf } from './edits.js';
import { stampOf, takeStamp } from './common/photo-write.js';
import { refetchCards } from './library-source.js';

/** How often a live ticket is read (a conversion takes seconds to minutes). */
const TICKET_READ_MS = 2000;

export const SAVE_CLIP_FIRST = 'Save or discard the changes typed on this clip first: then convert it.';
export const CLIP_CHANGED = 'This clip changed on disk since it was opened (another window, or it was converted already). '
    + 'It was read again: look at it, then convert it again.';

/** `120 MB`: a file's size as the end of a conversion says it. */
function megabytes(bytes) {
    return `${Math.max(1, Math.round((Number(bytes) || 0) / (1024 * 1024))).toLocaleString()} MB`;
}

/** The clip shown in the panel (its id, as media.js put it on the player), or null. */
function shownClip() {
    return mainVideo.dataset.route ? mainVideo.dataset.clip : null;
}

/** The buttons, the line and the Cancel, as the clip shown and its live ticket say. */
function draw() {
    if (!btnClipConvert) return;
    const route = mainVideo.dataset.route;
    const offer = state.clipConvert.offer && route && state.clipConvert.offer.route === route ? state.clipConvert.offer : null;
    const clip = shownClip();
    const live = clip === null ? undefined : state.clipConvert.live[clip];
    btnClipConvert.classList.toggle('hidden', !(offer && offer.mode === 'convert'));
    btnClipShrink.classList.toggle('hidden', !(offer && offer.mode === 'shrink'));
    btnClipConvert.disabled = btnClipShrink.disabled = Boolean(live);
    btnClipConvertCancel.classList.toggle('hidden', !(live && live.ticket));
    btnClipConvertCancel.disabled = Boolean(live && live.cancelling);
    clipConvertNote.textContent = live ? live.note : '';
    clipConvertNote.classList.toggle('hidden', !live);
}

/** The player shows another clip, or none (media.js showMedia, stopVideo): the buttons follow it. */
export function clipActionsFollow() {
    draw();
}

/** media.js read the plan of the clip shown from `route` (it did not play): REENCODE offers Convert, with its `replaces`. */
export function offerConvert(route, clip, plan) {
    if (!plan || plan.action !== 'REENCODE' || mainVideo.dataset.route !== route) return;
    state.clipConvert.offer = { route, clip: String(clip), mode: 'convert', replaces: plan.replaces || '' };
    draw();
}

/** The clip shown plays with a picture: Make smaller is offered, its plan asked when it is clicked. */
function offerShrink() {
    const route = mainVideo.dataset.route;
    if (!route || mainVideo.error || !(mainVideo.videoWidth > 0)) return;
    state.clipConvert.offer = { route, clip: mainVideo.dataset.clip, mode: 'shrink', replaces: null };
    draw();
}

/** Say how a conversion ended on the status line and in the batch strip (its sentence, with the file's name, not the strip's generic one). */
function sayEnd(sentence, problem) {
    setStatus(problem ? 'error' : 'ready', sentence, { transient: !problem });
    showEnded({ text: sentence, problem });
}

/** End `key`'s live ticket with `sentence` said, nothing taken. */
function endWith(key, live, sentence) {
    if (state.clipConvert.live[key] === live) delete state.clipConvert.live[key];
    draw();
    if (sentence) setStatus('error', sentence, { transient: false });
}

/**
 * Convert the clip shown, as its offer says. Refused, with nothing sent, while text typed on it is unsaved and while a
 * ticket of it is live. Resolves true once the server has taken it (its ticket is then read until it ends).
 */
export async function convertShownClip() {
    const route = mainVideo.dataset.route;
    const offer = state.clipConvert.offer;
    if (!route || !offer || offer.route !== route) return false;
    const key = offer.clip;
    if (state.clipConvert.live[key]) return false;
    const photo = state.folderPhotos.find(p => p.id !== undefined && String(p.id) === key);
    if (!photo) return false;
    if (hasUnsavedEdits()) {
        setStatus('error', SAVE_CLIP_FIRST, { transient: false });
        return false;
    }
    const name = baseName(photo.path);
    // Taken before anything is asked: a second click meanwhile finds it and sends nothing.
    const live = { photo, from: photo.path, ticket: null, library: '', mode: offer.mode, note: `Asking what ${name} needs...`, cancelling: false };
    state.clipConvert.live[key] = live;
    draw();
    const mode = offer.mode;
    let replaces = offer.replaces;
    if (replaces === null) {
        let plan;
        try {
            plan = await api.json(`/api/clip/convert-plan?id=${encodeURIComponent(key)}`);
        } catch (err) {
            endWith(key, live, `Not converted: what ${name} needs could not be read (${err.message}).`);
            return false;
        }
        if (!plan || !plan.replaces) {
            endWith(key, live, `Not converted: ${(plan && plan.error) || 'the server could not say what it needs.'}`);
            return false;
        }
        replaces = plan.replaces;
        // Stepped on while the plan was asked: no question about a clip no longer shown.
        if (mainVideo.dataset.route !== route) {
            endWith(key, live, '');
            return false;
        }
    }
    if (!window.confirm(replaces)) {
        endWith(key, live, '');
        return false;
    }
    live.note = mode === 'convert' ? `Converting ${name} to an MP4...` : `Making ${name} smaller (MP4)...`;
    draw();
    return queueWriteOf(photo.path, async (entry) => {
        let data;
        let res;
        try {
            res = await api.fetch('/api/clip/convert', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: photo.id, mode, stamp: stampOf(photo) }),
            });
            data = await res.json();
        } catch (err) {
            data = { success: false, error: `the request was not answered (${err.message})` };
        }
        if (!res || !res.ok || !data.success) {
            entry.error = data.error || 'the conversion was refused';
            if (data.changed_on_disk) {
                endWith(key, live, '');
                upper.reloadChangedPhoto(photo, () => setStatus('error', CLIP_CHANGED, { transient: false }));
            } else {
                endWith(key, live, `Not converted: ${entry.error}`);
            }
            return false;
        }
        live.ticket = data.ticket;
        live.library = data.library;
        draw();
        pollTicket(key, live);
        return true;
    }, `Convert ${name}`);
}

/** Read `live`'s ticket after TICKET_READ_MS, and again until it ends. Nothing answering is asked again at the next turn. */
function pollTicket(key, live) {
    window.setTimeout(async () => {
        if (state.clipConvert.live[key] !== live) return;
        let said;
        try {
            said = await api.json(`/api/clip/converted?ticket=${encodeURIComponent(live.ticket)}`);
        } catch (err) {
            console.error('Could not read how the conversion is going:', err);
            return pollTicket(key, live);
        }
        if (state.clipConvert.live[key] !== live) return;
        if (said && said.done === false) return pollTicket(key, live);
        if (!said || said.done !== true) {
            // 404: this server has no such ticket (it restarted): the conversion was settled without this page.
            return endWith(key, live, `How the conversion of ${baseName(live.from)} ended is not known to the server now `
                + `(${(said && said.error) || 'no answer'}): open the clip again to see its file.`);
        }
        converted(key, live, said);
    }, TICKET_READ_MS);
}

/** The ticket ended: taken by the clip's own record, and the panel redrawn while it is still the clip shown. */
function converted(key, live, said) {
    if (!said.success) {
        endWith(key, live, '');
        return sayEnd(`Could not convert ${baseName(live.from)}: ${said.error || 'the server gave no reason'}`, true);
    }
    delete state.clipConvert.live[key];
    const photo = live.photo;
    takeStamp(photo, said);
    if (said.renamed) photoRenamed(photo, said.new_path);
    // The clip is converted: a failure redrawing the page is logged, not said as a conversion that failed.
    try {
        recordsWritten([photo], { [live.from]: photo.path });
        if (samePath(state.activePhotoPath, live.from)) {
            state.activePhotoPath = photo.path;
            upper.selectPhoto(photo.path);
        }
        upper.renderFileList();
        upper.renderThumbnails();
        if (state.library && photo.id !== undefined) refetchCards([photo.id]);
    } catch (err) {
        console.error('Converted, but redrawing the page failed:', err);
    }
    draw();
    const sizes = `${megabytes(said.size_before)} to ${megabytes(said.size_after)}`;
    const verb = live.mode === 'shrink' ? 'Made smaller' : 'Converted';
    if (said.original_kept) {
        // The server's sentence is a fragment, as its other reasons are (no capital, no full stop): it is made one here.
        const kept = said.original_kept.charAt(0).toUpperCase() + said.original_kept.slice(1);
        sayEnd(`${verb} ${baseName(photo.path)}: ${sizes}. ${kept}.`, true);
    } else {
        sayEnd(`${verb} ${baseName(photo.path)}: ${sizes}`, false);
    }
}

/** Cancel the live ticket of the clip shown: the server stops ffmpeg and leaves the original; the ticket's end says so. */
async function cancelShown() {
    const key = shownClip();
    const live = key === null ? undefined : state.clipConvert.live[key];
    if (!live || !live.ticket || live.cancelling) return;
    const note = live.note;
    live.cancelling = true;
    live.note = `Stopping the conversion of ${baseName(live.from)}...`;
    draw();
    const refused = await cancelByTicket(live.library, live.ticket);
    // Not stopped: it goes on, and Cancel may be pressed again (a ticket that ended meanwhile is said by its end).
    if (refused && state.clipConvert.live[key] === live) {
        live.cancelling = false;
        live.note = note;
        draw();
        setStatus('error', refused, { transient: false });
    }
}

export function wireClipConvert() {
    if (!btnClipConvert) return;
    btnClipConvert.addEventListener('click', () => convertShownClip());
    btnClipShrink.addEventListener('click', () => convertShownClip());
    btnClipConvertCancel.addEventListener('click', () => cancelShown());
    mainVideo.addEventListener('loadeddata', offerShrink);
}
