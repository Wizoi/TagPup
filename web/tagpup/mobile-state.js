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
    // How many times the library list was asked for: a reply to an older ask is dropped.
    librariesAsked: 0,
};
