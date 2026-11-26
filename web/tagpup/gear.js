// TagPup's page: the gear in its top bar (web/common/gear.js) -- the tag editor, which
// was the Manage Tags button, the settings of TagPup's own features (Suggest's words,
// Smart Rename's format: web/common/settings-dialog.js), the library's history of
// changes, each undoable (web/common/history-dialog.js), Name faces from tags in the whole
// library (name-faces.js), adding and removing folders (web/common/folder-manager.js), and
// TagTuner on this library.
import { openFolderManager } from './common/folder-manager.js';
import { wireGear } from './common/gear.js';
import { NEW_LIBRARY_LINE, takeNewLibraryWelcome } from './common/library.js';
import { wireTagEditor } from './common/tag-editor.js';
import { openSettings } from './common/settings-dialog.js';
import { openHistory } from './common/history-dialog.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import { statusDot, statusText } from './elements.js';
import { fetchKnownTagsAndPeople, loadTaxonomy } from './tags.js';
import { scanFolder } from './folder.js';
import { checkIndexingStatus } from './suggestions.js';
import { browseWholeLibrary, libraryViewOfFolder, refreshLibraryView, syncLibraryMenu } from './library-view.js';
import { startNamingFacesInLibrary } from './name-faces.js';

/**
 * The tag editor, over the tree this page keeps (state.taxonomyNodes, read by
 * loadTaxonomy), saying what it does in the page's status line. After an edit the
 * names offered while typing are read again -- the fields' lists, and the navigator's
 * keywords and people, which the search's picker offers -- and an edit that rewrote
 * photos opens the folder again, so its photos show what they now hold.
 */
export function wireTagPupGear() {
    const editor = wireTagEditor({
        nodes: () => state.taxonomyNodes,
        reload: loadTaxonomy,
        status: (text, busy) => {
            statusDot.className = busy ? 'status-indicator-dot busy' : 'status-indicator-dot';
            statusText.textContent = text;
        },
        edited: ({ treeChanged, photosChanged }) => {
            if (treeChanged) fetchKnownTagsAndPeople();
            if (treeChanged || photosChanged) upper.navigatorCountsChanged();
            if (photosChanged && state.scannedFolder) scanFolder(true);
        },
    });
    const button = document.getElementById('btn-gear');
    const menu = document.getElementById('gear-menu');
    // The menu's library views: the whole library, or the folder that is open (library-view.js).
    if (button) button.addEventListener('click', () => syncLibraryMenu(menu));
    // Folders queued: their indexes are followed to their end, whichever folder or view is open (suggestions.js), and the
    // open folder is asked again whether it is the library's (membership.js). A folder removed: the navigator's counts and
    // the view that is open are read again.
    const manageFolders = (welcome) => openFolderManager({
        welcome,
        queued: (data) => {
            checkIndexingStatus(data.queued || []);
            if (state.scannedFolder) upper.checkFolderMembership(state.scannedFolder);
        },
        removed: () => {
            upper.navigatorCountsChanged();
            if (state.library) refreshLibraryView();
            else if (state.scannedFolder) scanFolder(true);
        },
    });
    const gear = wireGear(button, menu, {
        'browse-library': browseWholeLibrary,
        'library-of-folder': libraryViewOfFolder,
        'tag-editor': editor.open,
        'library-settings': openSettings,
        'name-faces': startNamingFacesInLibrary,
        // An undo may have put back tags, captions and names: what the page shows is read again.
        'library-history': () => openHistory({
            undone: () => {
                fetchKnownTagsAndPeople();
                if (state.scannedFolder) scanFolder(true);
            },
        }),
        'manage-folders': () => manageFolders(),
    });
    // A library made a moment ago opens on Manage folders, once (library.js).
    if (takeNewLibraryWelcome()) manageFolders(NEW_LIBRARY_LINE);
    return gear;
}
