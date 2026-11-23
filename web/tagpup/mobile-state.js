/**
 * The phone page's state, one object (docs/plans/phone-viewer.md): a module cannot assign a binding it imports, so
 * what changes lives here, as the desktop page's state.js.
 */
export const state = {
    // The library the page's URL names, '' for none: mobile.js sets it as the page starts. Another library is
    // another page (goToLibrary), so it never changes while the page is open.
    library: '',
    // The screen shown, a data-screen of mobile.html, and the one the page opens on.
    screen: '',
    // The screen shown before it (mobile-screens.js): the Grid scrolls to the photo last open only coming back from the Photo screen.
    cameFrom: '',
    home: 'browse',
    // Where on that screen, as its history entry holds it: a level of Browse ([section, row ids...]), the Grid's view,
    // the Photo screen's { id, at }; null for none (the top of Browse).
    place: null,
    // Whether the step shown is a sheet's whose place was changed on this visit to it (its entry's `moved`, kept through a
    // reload): Back from it to the step under carries that place down onto that step (mobile-screens.js; the step's own record
    // is gone by the time Back lands).
    sheetMoved: false,
    // The view an address names (?view=, web/common/library.js VIEW_PARAMS), opened as the page starts; null for none.
    linked: null,
    // The view the Grid shows: { kind, value, recursive, order, media }, the names of library-source.js.
    view: null,
    // Browse's sections by name: { asked, index, status, message, reading, covers, coversOut } (mobile-browse.js), the
    // Filter sheet's tabs too: `reading` the last read's promise (needSection); `covers` each
    // row's cover answered, by row id (its thumbnail's URL, null for a row with no photo), kept for the page's life, a row
    // scrolled away included; `coversOut` the row ids asked and not answered yet.
    sections: {},
    // Browse's covers (mobile-browse.js): the one IntersectionObserver over the cover boxes drawn, made as the page is
    // wired; what each box is the cover of ({ name, id, tree }); the rows near the screen not asked yet, in the order they
    // came near; how many asks are out.
    covers: { observer: null, wanted: new WeakMap(), queue: [], out: 0 },
    // What is held for the Grid's view: a view of library-source.js (newLibrary), its ids and the cards asked so far,
    // kept while a photo of it is open; null when Browse is shown. A reply for a view no longer held is dropped.
    photos: null,
    // The marks of the Grid's view (mobile-filter.js): a Map of filterKey -> '+', '&' or '-' (navigator-model.js), read
    // back from the view each time the Grid is shown, empty for a view that is no filter. A tap makes the next from it.
    filter: new Map(),
    // The Filter sheet (mobile-filter.js): the tab shown, the rows open under each tab (by section, a Set of row ids), the
    // rows drawn, the sentence of a mark refused, and whether its Close is on its way back. Whether it is open is the step's.
    filterSheet: { tab: 'people', expanded: {}, rows: [], notice: '', closing: false },
    // The card of the photo the Photo screen shows, null while it is read (mobile-photo.js).
    photo: null,
    // The Photo screen's read of the cards around the photo open, while it is out: { lib, ids (a Set), reply }; a swipe
    // onto a photo it names waits for it rather than asking again (mobile-photo.js).
    cardsAsked: null,
    // The facts sheet under the photo (mobile-photo.js): the photo's record as /api/library/photo read it, { id, record,
    // failed }, null before a read lands, the Edit sheet's read landing there too (recordLanded); the read out, { id,
    // dropped } (one at a time: a swipe on while it is out asks the photo then shown when it lands; `dropped`, a later
    // read landed first and its reply is let go); whether the sheet is pulled up (a swipe is refused while it is).
    facts: null,
    factsAsked: null,
    factsOpen: false,
    // The Edit sheet's photo (mobile-edit.js): { id, record (as /api/library/photo read it: the facts' record, the same
    // object, held here too because the facts let it go first when the photo is left and a draft is kept from it), saving,
    // letGo }; null when
    // shut. Drawn from the history step shown, the one record of whether the sheet is open (mobile-screens.js sheetShown).
    edit: null,
    // The tail of the page's writes, one at a time in the order sent; null when none is out.
    writing: null,
    // Chips taken off and not sent yet, each behind its Undo: { id, path (the photo's), tag, timer, close }.
    removals: [],
    // Tag changes waiting their turn behind another write (state.writing): { id, path, change }; the sheet shows a
    // removal among them as gone (mobile-edit.js).
    queued: [],
    // The title and Date Taken typed on a photo's sheet, neither saved nor let go by Close, by the photo's id: left by
    // Back or another screen, they are put back when its sheet opens again (mobile-edit.js). Gone with the page.
    drafts: new Map(),
    // The library's tags (/api/tags) and its people (a PeopleDirectory of /api/people), read once; null until then. The
    // read out, its promise (mobile-photo.js readVocabulary); whether the last one failed (the facts say so).
    tags: null,
    people: null,
    vocabularyAsked: null,
    vocabularyFailed: false,
    // The Grid's windowed grid (vgrid.js), made once as the page is wired.
    grid: null,
    // How many times the library list was asked for: a reply to an older ask is dropped.
    librariesAsked: 0,
};
