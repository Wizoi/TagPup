// TagTuner's page: the gear in its header (web/common/gear.js) -- the tag editor, the
// library's settings (web/common/settings-dialog.js), its history of changes, each
// undoable (web/common/history-dialog.js), and TagPup on this library.
import { wireGear } from './common/gear.js';
import { openHistory } from './common/history-dialog.js';
import { openSettings } from './common/settings-dialog.js';
import { wireTagEditor } from './common/tag-editor.js';
import { fetchKnownPeople } from './shared.js';
import { refreshSidebarQuietly } from './sidebar.js';

/**
 * The tag editor, reading the tree itself each time it opens: this page keeps none.
 * TagTuner has no status line, so the editor says what it is doing in its own footer.
 * After an edit the names offered while typing are read again, and an edit that
 * rewrote photos -- a rename, a delete -- refreshes the list beside the photo.
 */
export function wireTunerGear() {
    const editor = wireTagEditor({
        edited: ({ treeChanged, photosChanged }) => {
            if (treeChanged) fetchKnownPeople();
            if (photosChanged) refreshSidebarQuietly();
        },
    });
    return wireGear(document.getElementById('btn-gear'), document.getElementById('gear-menu'), {
        'tag-editor': editor.open,
        'library-settings': openSettings,
        // An undo may have put back names and tags: the list beside the photo is read again.
        'library-history': () => openHistory({
            undone: () => {
                fetchKnownPeople();
                refreshSidebarQuietly();
            },
        }),
    });
}
