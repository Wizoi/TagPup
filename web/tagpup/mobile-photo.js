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
 *
 * Under the picture, its facts (docs/plans/phone-redesign.md, PHOTO): a sheet whose first part always shows the Date
 * Taken, the title (three lines, then "more"), the people and keywords as chips and Edit (mobile-edit.js); pulled up, or
 * its handle tapped, it shows the rest the page holds of the photo. The date is the card's at once; the rest is the photo's
 * record as /api/library/photo reads it, one out at a time however fast the swipes; the Edit sheet's read of it lands in
 * the same place (recordLanded), so the facts hold one record of the photo, whichever read lands, a failure mended by
 * the next. The people are told from the keywords by the library's people (/api/people, read once a page for the facts
 * and the Edit sheet; whichever asks, the facts are drawn by it). A write of the Edit sheet redraws them (photoWritten).
 * A swipe is refused while the sheet is pulled up, as under Edit.
 */
import { api } from './common/api.js';
import { buildElement, replaceContent } from './common/dom.js';
import { photoFileUrl, videoRoute } from './common/file-addresses.js';
import { closeImageZoom, openImageZoom } from './common/image-zoom.js';
import { folderOf } from './common/paths.js';
import { SWIPE_MIN_PX, enableSwipeNavigation } from './common/swipe.js';
import { PeopleDirectory, leafOf, rootOf, sortedTags } from './common/vocabulary.js';
import { fileSizeSaid, whenSaid } from './format.js';
import { keepCards } from './library-source.js';
import { state } from './mobile-state.js';
import { replacePlace, samePlace, sheetShown } from './mobile-screens.js';

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

const GONE = 'This photo is no longer in the library.';

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

/** The id of the photo the Photo screen shows; null on another screen. */
export function photoShown() {
    const place = state.place;
    return state.screen === 'photo' && place && Number.isInteger(place.id) ? place.id : null;
}

/** Is `tag` one of the library's people (state.people, once read)? */
export const isPerson = (tag) => Boolean(state.people && state.people.ofTag(tag));

/** How a chip names `tag` among the photo's `tags`: its leaf, with its root when another of them has the same leaf. */
export function chipSaid(tags, tag) {
    const twice = tags.filter((each) => leafOf(each).toLowerCase() === leafOf(tag).toLowerCase()).length > 1;
    return twice ? `${leafOf(tag)} (${rootOf(tag)})` : leafOf(tag);
}

/**
 * The library's tags and people (state.tags, state.people), asked once a page for the facts and the Edit sheet alike;
 * resolves once they are held. A failure is asked again at the next ask.
 */
export function readVocabulary() {
    if (state.tags) return Promise.resolve();
    if (state.vocabularyAsked) return state.vocabularyAsked;
    const asked = Promise.all(['/api/tags', '/api/people'].map((route) => api.fetch(route).then((res) => api.answered(res, 'no tags.'))))
        .then(([tags, people]) => {
            state.people = new PeopleDirectory(people);
            state.tags = Array.isArray(tags) ? tags : [];
            state.vocabularyFailed = false;
            if (state.screen === 'photo') drawFacts();
        }, (err) => {
            state.vocabularyFailed = true;
            if (state.screen === 'photo') drawFacts();
            throw err;
        });
    state.vocabularyAsked = asked;
    const done = () => {
        if (state.vocabularyAsked === asked) state.vocabularyAsked = null;
    };
    asked.then(done, done);
    return asked;
}

/** Does the title show more than its three lines hold (the sheet down)? Then "more" pulls the sheet up. */
function measureTitle() {
    const title = byId('photo-title');
    byId('photo-title-more').classList.toggle('hidden', state.factsOpen || title.scrollHeight <= title.clientHeight + 1);
}

/** A row of chips, or the words saying the photo has none; nothing while its record or the library's people are read. */
function drawChips(id, tags, all, none) {
    if (!tags) {
        replaceContent(byId(id));
        return;
    }
    replaceContent(byId(id), ...(tags.length ? tags.map((tag) => buildElement('span', { className: 'photo-chip', text: chipSaid(all, tag) }))
        : [buildElement('span', { className: 'photo-none', text: none })]));
}

/** The facts of the photo shown: the card's date at once, the rest as its record says once read. */
function drawFacts() {
    const id = photoShown();
    const card = state.photo && state.photo.id === id ? state.photo : null;
    const read = state.facts && state.facts.id === id ? state.facts : null;
    const record = read && read.record;
    const when = whenSaid(record ? record.taken : card && card.taken);
    byId('photo-when').textContent = when || (record || card ? 'Unknown' : '');
    byId('photo-when').classList.toggle('photo-none', !when);
    byId('photo-title').textContent = (record && record.title) || '';
    const tags = record && state.people ? sortedTags(record.tags || []) : null;
    drawChips('photo-people', tags && tags.filter(isPerson), tags, 'No people');
    drawChips('photo-keywords', tags && tags.filter((tag) => !isPerson(tag)), tags, 'No keywords');
    if (read && read.gone) say(GONE);    // deleted on the PC, its card still held: that alone, not a read that failed
    const why = read && read.failed ? 'Could not read the title and tags of this photo. Swipe on, or come back to it later.'
        : record && state.vocabularyFailed ? 'Could not read the library\'s people and keywords. Swipe on to try again.' : '';
    byId('photo-facts-problem').textContent = why;
    byId('photo-facts-problem').classList.toggle('hidden', !why);
    const path = (record && record.path) || (card && card.path) || '';
    const media = (record && record.media) || (card && card.media);
    const more = [['photo-name', card ? card.name : ''], ['photo-folder', folderOf(path)],
        ['photo-kind', media === 'video' ? 'Clip' : ''], ['photo-size', record ? fileSizeSaid(record.size) : ''],
        ['photo-camera', record && record.camera], ['photo-lens', record && record.lens]];
    for (const [each, text] of more) {
        byId(each).textContent = text || '';
        byId(each).parentElement.classList.toggle('hidden', !text);
    }
    measureTitle();
}

/**
 * The record of the photo shown, read; one read out at a time, and when it lands the photo shown then is read if not yet.
 * A reply is let go when its photo is no longer shown (swiped past, or Back: the same card tapped again is read again)
 * or it is `dropped`: the Edit sheet's read overtook it (recordLanded), or the photo was written while it was out
 * (photoWritten), and it is sent again.
 */
function readFacts() {
    const id = photoShown();
    if (id === null || state.factsAsked !== null || (state.facts && state.facts.id === id)) return;
    const asked = { id, dropped: false };
    state.factsAsked = asked;
    const landed = (facts) => {
        state.factsAsked = null;
        if (!asked.dropped && photoShown() === id) state.facts = facts;
        if (photoShown() === id) drawFacts();
        readFacts();    // swiped on while it was out: the photo shown now
    };
    api.fetch(`/api/library/photo?id=${id}`)
        .then((res) => (res.status === 404 ? { gone: true } : api.answered(res, 'Could not read this photo.')))
        .then((body) => landed(body && body.photo ? { id, record: body.photo, failed: false }
            : { id, record: null, failed: !(body && body.gone), gone: Boolean(body && body.gone) }),
        () => landed({ id, record: null, failed: true }));
}

/**
 * Photo `id` read by the Edit sheet (mobile-edit.js), as `record`: the facts' record of it, the one the sheet edits too,
 * a failed read of the facts mended and a facts read still out let go (it was sent earlier). Returns the record.
 */
export function recordLanded(id, record) {
    if (state.factsAsked && state.factsAsked.id === id) state.factsAsked.dropped = true;
    state.facts = { id, record, failed: false };
    if (photoShown() === id) drawFacts();
    return record;
}

/** Photo `id` was written (mobile-edit.js), its file now holding `fields`: its facts say so, a read of them out read again. */
export function photoWritten(id, fields) {
    if (state.factsAsked && state.factsAsked.id === id) state.factsAsked.dropped = true;    // it may have read the file before
    if (state.facts && state.facts.id === id && state.facts.record) Object.assign(state.facts.record, fields);
    if (photoShown() === id) drawFacts();
}

/** The facts sheet pulled up (`open`) or let down: the rest of the facts shown or not, a swipe refused while it is up. */
function openFacts(open) {
    state.factsOpen = open && photoShown() !== null;
    byId('photo-facts').classList.toggle('open', state.factsOpen);
    const handle = byId('photo-facts-handle');
    handle.setAttribute('aria-expanded', String(state.factsOpen));
    handle.setAttribute('aria-label', state.factsOpen ? 'Less about this photo' : 'More about this photo');
    byId('photo-more').classList.toggle('hidden', !state.factsOpen);
    measureTitle();
}

/** The facts of the photo shown, drawn as held and read when not; the library's people read once for them (and draw them). */
function showFacts() {
    drawFacts();
    readFacts();
    readVocabulary().catch(() => {});
}

/** Show the photo of the Photo screen's place, where it is in the view, and its facts. */
export function showPhoto() {
    const place = state.place;
    const lib = state.photos;
    const at = indexIn(lib, place);
    byId('photo-place').textContent = at >= 0
        ? `${(at + 1).toLocaleString('en-US')} of ${lib.ids.length.toLocaleString('en-US')}` : '';
    const card = lib && place ? lib.cards.get(place.id) : null;
    if (card) {
        draw(card);
        showFacts();
        return;
    }
    state.photo = null;
    blank();
    say('');
    showFacts();
    if (!place || !Number.isInteger(place.id)) return;
    const current = () => state.screen === 'photo' && samePlace(state.place, place);    // not swiped past, nor left
    readAround(lib, place.id, at)
        .then((got) => {
            if (!current()) return;
            const read = got.get(place.id);
            if (read) {
                draw(read);
                drawFacts();
            } else say(GONE);
        })
        .catch(() => {
            if (current()) say('Could not load this photo. Swipe on, or come back to it later.');
        });
}

/** The next photo of the view (1) or the one before (-1); nothing at its first or last, or with no view held (a reload). */
export function stepPhoto(delta) {
    if (state.screen !== 'photo' || sheetShown() || state.factsOpen) return;    // a sheet up: Close or let it down first
    const lib = state.photos;
    const next = landing(lib, state.place, delta);
    if (next < 0) return;
    // The photo open, as a tap sets it (mobile-grid.js): its card is brought into view when the Grid is shown again.
    lib.activeId = lib.ids[next];
    replacePlace({ id: lib.ids[next], at: next });
}

/** Another screen is shown: the clip stops, the zoom closes, the facts sheet goes down and its record is let go. */
export function leavePhoto() {
    stopClip();
    closeImageZoom();
    openFacts(false);
    state.facts = null;
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
        if (sheetShown()) return;    // the picture above the Edit sheet: Back would shut the sheet, not the zoom
        if (state.photo && state.photo.media !== 'video') {
            openImageZoom(photoFileUrl(state.photo), { preview: picture.getAttribute('src') || '', opener: picture });
        }
    });
    picture.addEventListener('error', () => {
        if (!picture.getAttribute('src') || state.screen !== 'photo') return;
        picture.classList.add('hidden');
        if (state.facts && state.facts.id === photoShown() && state.facts.gone) return;    // said: no longer in the library
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
    // The facts sheet: its handle pulled up or down, or tapped; "more" under a long title pulls it up.
    const handle = byId('photo-facts-handle');
    let pull = null;
    handle.addEventListener('pointerdown', (event) => {
        delete handle.dataset.dragged;
        pull = { id: event.pointerId, y: event.clientY };
    });
    handle.addEventListener('pointerup', (event) => {
        if (!pull || event.pointerId !== pull.id) return;
        const dy = event.clientY - pull.y;
        pull = null;
        if (Math.abs(dy) < SWIPE_MIN_PX) return;    // a tap: its click
        handle.dataset.dragged = 'true';
        openFacts(dy < 0);
    });
    handle.addEventListener('pointercancel', () => { pull = null; });
    handle.addEventListener('click', () => {
        if (handle.dataset.dragged === 'true') {
            delete handle.dataset.dragged;    // that was a pull
            return;
        }
        openFacts(!state.factsOpen);
    });
    byId('photo-title-more').addEventListener('click', () => openFacts(true));
    window.addEventListener('resize', measureTitle);    // a phone turned: three lines hold another length
}
