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
    home: 'browse',
    // Where on that screen, as its history entry holds it: a level of Browse ([section, row ids...]), the Grid's view;
    // null for none (the top of Browse).
    place: null,
    // The view an address names (?view=, web/common/library.js VIEW_PARAMS), opened as the page starts; null for none.
    linked: null,
    // The view the Grid shows: { kind, value, recursive, order, media }, the names of library-source.js.
    view: null,
    // Browse's sections by name: { asked, index, status, message } (mobile-browse.js).
    sections: {},
    // How many times the Grid's photos were asked for: a reply to an older ask is dropped.
    gridAsked: 0,
    // How many times the library list was asked for: a reply to an older ask is dropped.
    librariesAsked: 0,
};
