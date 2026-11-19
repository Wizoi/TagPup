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
    // The view an address names (?view=, web/common/library.js VIEW_PARAMS), opened as the page starts; null for none.
    linked: null,
    // The view the Grid shows: { kind, value, recursive, order, media }, the names of library-source.js.
    view: null,
    // Browse's sections by name: { asked, index, status, message } (mobile-browse.js).
    sections: {},
    // What is held for the Grid's view: a view of library-source.js (newLibrary), its ids and the cards asked so far,
    // kept while a photo of it is open; null when Browse is shown. A reply for a view no longer held is dropped.
    photos: null,
    // The card of the photo the Photo screen shows, null while it is read (mobile-photo.js).
    photo: null,
    // The Grid's windowed grid (vgrid.js), made once as the page is wired.
    grid: null,
    // How many times the library list was asked for: a reply to an older ask is dropped.
    librariesAsked: 0,
};
