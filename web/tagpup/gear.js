// TagPup's page: the gear in its top bar (web/common/gear.js) -- the tag editor, which
// was the Manage Tags button, and TagTuner on this library.
import { wireGear } from './common/gear.js';
import { wireTagEditor } from './common/tag-editor.js';
import { state } from './state.js';
import { statusDot, statusText } from './elements.js';
import { fetchKnownTagsAndPeople, loadTaxonomy } from './tags.js';
import { scanFolder } from './folder.js';

/**
 * The tag editor, over the tree this page keeps (state.taxonomyNodes, read by
 * loadTaxonomy), saying what it does in the page's status line. After an edit the
 * names offered while typing are read again, and an edit that rewrote photos opens
 * the folder again, so its photos show what they now hold.
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
            if (photosChanged && state.scannedFolder) scanFolder(true);
        },
    });
    return wireGear(document.getElementById('btn-gear'), document.getElementById('gear-menu'), {
        'tag-editor': editor.open,
    });
}
