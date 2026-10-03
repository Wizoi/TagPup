/**
 * The quiet line a page shows where another program's hold on a file could be the reason something
 * failed or was slow: one sentence and a link to the Activity page's File access section. No popup,
 * no banner on the main page, no request: the findings are the Activity page's to show
 * (web/activity/file-access.js; tagpup.services.file_access).
 */
import { buildElement } from './dom.js';

/** Where the findings are. */
export const FILE_ACCESS_URL = '/activity/#file-access';

/** A paragraph: "Other programs may be scanning these files: see Activity > File access" (the last a link). */
export function fileAccessNote(className = 'file-access-note') {
    return buildElement('p', { className }, [
        'Other programs may be scanning these files: see ',
        buildElement('a', { text: 'Activity > File access', attrs: { href: FILE_ACCESS_URL, target: '_blank', rel: 'noopener' } }),
    ]);
}
