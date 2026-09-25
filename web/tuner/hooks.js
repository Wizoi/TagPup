// What a feature calls in one above it.
//
// A module cannot import one that imports it, and a few calls go back up the page: a
// write refreshing what is on screen -- the photo, the people list, the person, the
// sidebar. Those go through here; main.js fills it in before anything runs.
export const upper = {
    selectPhoto: null,
    fetchPeopleWithCounts: null,
    selectPerson: null,
    refreshSidebarQuietly: null,
};
