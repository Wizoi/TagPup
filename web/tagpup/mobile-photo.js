/**
 * The phone's Photo screen (docs/plans/phone-viewer.md, Screens 4): one photo of the Grid's view, large, read-only (the
 * edit sheet is the next cut). Its place in history is { id, at }: the photo's id and its index in the Grid's view. A
 * swipe (web/common/swipe.js) moves through the view and writes the photo it lands on into the same step, so a reload
 * opens that photo and Back is the Grid with its card in view (mobile-grid.js), however many photos were swiped through.
 *
 * The picture is the photo's file at 1024 px (web/common/file-addresses.js, by the card's `mtime`); a tap opens the file whole in the shared zoom (web/common/image-zoom.js),
 * where the phone's own pinch enlarges it. A clip plays from /api/video-file, its poster shown until it does. Which file
 * is the card the Grid holds, or, for a card not asked yet (a window still loading, a swipe past it, a reload), read
 * with the cards around it and kept in the view as the Grid's own (#3151); a reply for a photo swiped past is dropped. A missing file, a share that does not answer, a clip the phone
 * cannot play: a sentence, and the swipe goes on. A picture that failed while the server was updated is asked again by
 * api.js once it is back (imagesAfterAnUpdate): loaded, it is shown again and its sentence goes.
 */
import { api } from './common/api.js';
import { photoFileUrl, videoRoute } from './common/file-addresses.js';
import { closeImageZoom, openImageZoom } from './common/image-zoom.js';
import { enableSwipeNavigation } from './common/swipe.js';
import { keepCards } from './library-source.js';
import { state } from './mobile-state.js';
import { replacePlace, samePlace } from './mobile-screens.js';

const byId = (id) => document.getElementById(id);

/** Where the photo of `place` is in the view held, or -1: its `at` while that is still its id, else looked for. */
function indexIn(lib, place) {
    if (!lib || !place || !Number.isInteger(place.id)) return -1;
    if (Number.isInteger(place.at) && lib.ids[place.at] === place.id) return place.at;
    return lib.ids.indexOf(place.id);
}

/**
 * Where a step of `delta` from the photo of `place` lands in the view held, or -1 (past either end, no view held): from
 * that photo, or, when the view dropped it meanwhile (deleted on the PC: a card window's reply, library-source.js
 * dropPhotos), from where it was, `at`, which the photo after it now holds.
 */
function landing(lib, place, delta) {
    if (!lib || !place) return -1;
    const at = indexIn(lib, place);
    let next = at + delta;
    if (at < 0) {
        if (!Number.isInteger(place.at)) return -1;
        next = delta > 0 ? place.at + delta - 1 : place.at + delta;
    }
    return next >= 0 && next < lib.ids.length ? next : -1;
}

/** Photos either side of the one open read with it: a swipe through photos the Grid has not asked asks once in so many. */
const AROUND = 25;

/**
 * The cards of the photo `id`, at `at` in the view `lib` (-1, null: none held, a reload), and of those around it the view
 * does not hold, kept in it; a read out already that names `id` is waited for, not sent again. Resolves {id: card}.
 */
function readAround(lib, id, at) {
    const out = state.cardsAsked;
    if (out && out.lib === lib && out.ids.has(id)) return out.reply;
    const ids = [id];
    for (let i = Math.max(0, at - AROUND); lib && at >= 0 && i <= Math.min(lib.ids.length - 1, at + AROUND); i += 1) {
        if (lib.ids[i] !== id && !lib.cards.has(lib.ids[i])) ids.push(lib.ids[i]);
    }
    const reply = api.fetch(`/api/library/cards?ids=${ids.join(',')}`)
        .then((res) => api.answered(res, 'Could not load this photo.'))    // a refusal (500, 503) is a failure, not "gone"
        .then((body) => {
            const read = body && Array.isArray(body.cards) ? body.cards.filter((each) => each && ids.includes(each.id)) : [];
            if (lib) keepCards(lib, read);
            return new Map(read.map((each) => [each.id, each]));
        });
    const done = () => {
        if (state.cardsAsked && state.cardsAsked.reply === reply) state.cardsAsked = null;
    };
    reply.then(done, done);
    state.cardsAsked = { lib, ids: new Set(ids), reply };
    return reply;
}

/** Say why the photo is not shown, or '' for nothing to say. */
function say(text) {
    byId('photo-problem').textContent = text;
    byId('photo-problem').classList.toggle('hidden', !text);
}

/** Let go of the clip: paused, its stream dropped. */
function stopClip() {
    const clip = byId('photo-clip');
    clip.classList.add('hidden');
    if (!clip.getAttribute('src')) return;
    clip.pause();
    clip.removeAttribute('src');
    clip.load();
}

/** No picture and no clip: between a swipe and its card, or a photo that cannot be shown. */
function blank() {
    stopClip();
    const picture = byId('photo-picture');
    picture.classList.add('hidden');
    picture.removeAttribute('src');
    delete picture.dataset.photo;
}

/** Show `card`: its picture, or its clip with its poster; a file that is gone says so. */
function draw(card) {
    state.photo = card;
    byId('photo-name').textContent = card.name || '';
    if (card.stale === 'missing') {
        blank();
        say('This file is not where the library last saw it: moved or deleted on the PC.');
        return;
    }
    say('');
    const picture = byId('photo-picture');
    if (card.media !== 'video') {
        stopClip();
        picture.src = photoFileUrl(card, 1024);
        picture.dataset.photo = String(card.id);    // whose picture a load is (api.js may ask it again)
        picture.classList.remove('hidden');
        return;
    }
    picture.classList.add('hidden');
    picture.removeAttribute('src');
    delete picture.dataset.photo;
    const clip = byId('photo-clip');
    clip.classList.remove('hidden');
    clip.poster = card.thumb ? api.image(card.thumb) : '';
    clip.src = api.url(videoRoute(card));
}

/** Show the photo of the Photo screen's place, and where it is in the view. */
export function showPhoto() {
    const place = state.place;
    const lib = state.photos;
    const at = indexIn(lib, place);
    byId('photo-at').textContent = at >= 0
        ? `${(at + 1).toLocaleString('en-US')} of ${lib.ids.length.toLocaleString('en-US')}` : '';
    const card = lib && place ? lib.cards.get(place.id) : null;
    if (card) {
        draw(card);
        return;
    }
    state.photo = null;
    blank();
    say('');
    byId('photo-name').textContent = '';
    if (!place || !Number.isInteger(place.id)) return;
    const current = () => state.screen === 'photo' && samePlace(state.place, place);    // not swiped past, nor left
    readAround(lib, place.id, at)
        .then((got) => {
            if (!current()) return;
            const read = got.get(place.id);
            if (read) draw(read);
            else say('This photo is no longer in the library.');
        })
        .catch(() => {
            if (current()) say('Could not load this photo. Swipe on, or come back to it later.');
        });
}

/** The next photo of the view (1) or the one before (-1); nothing at its first or last, or with no view held (a reload). */
export function stepPhoto(delta) {
    if (state.screen !== 'photo') return;
    const lib = state.photos;
    const next = landing(lib, state.place, delta);
    if (next < 0) return;
    // The photo open, as a tap sets it (mobile-grid.js): its card is brought into view when the Grid is shown again.
    lib.activeId = lib.ids[next];
    replacePlace({ id: lib.ids[next], at: next });
}

/** Another screen is shown: the clip stops and the zoom closes. */
export function leavePhoto() {
    stopClip();
    closeImageZoom();
}

/** The swipe, a tap to zoom, and what a picture or a clip that will not load says. */
export function wirePhoto() {
    const stage = byId('photo-stage');
    const picture = byId('photo-picture');
    const clip = byId('photo-clip');
    enableSwipeNavigation(stage, stepPhoto);
    picture.addEventListener('click', () => {
        if (stage.dataset.dragged === 'true') {
            delete stage.dataset.dragged;    // that was a swipe
            return;
        }
        if (state.photo && state.photo.media !== 'video') {
            openImageZoom(photoFileUrl(state.photo), { preview: picture.getAttribute('src') || '', opener: picture });
        }
    });
    picture.addEventListener('error', () => {
        if (!picture.getAttribute('src') || state.screen !== 'photo') return;
        picture.classList.add('hidden');
        // Not "the PC could not read it": a server being updated fails a picture too, and api.js asks it again.
        say('Could not load this photo just now. Swipe on, or come back to it later.');
    });
    picture.addEventListener('load', () => {
        if (!picture.getAttribute('src') || !state.photo || picture.dataset.photo !== String(state.photo.id)) return;
        picture.classList.remove('hidden');    // asked again after an update, and there now
        say('');
    });
    clip.addEventListener('error', () => {
        if (!clip.getAttribute('src') || state.screen !== 'photo') return;
        say('This clip will not play here. Play it on the PC.');
    });
}
