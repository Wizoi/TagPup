/**
 * The Re-examine an index's closing sentence offers ("3 of the 5 new faces have a likely name: open Re-examine on their
 * folder to confirm them.", tagpup/services/indexing.py new_faces_said), made something to press on the page that shows
 * the sentence: TagPup's status line links to TagTuner's Re-examine of the folder (TagTuner's address `reexamine=`,
 * web/tuner/reexamine.js), and TagTuner's index line runs it in the page. A sentence that offers none -- no new face has
 * a likely name -- is left as it is. The words are the server's; tests/test_indexing_names_a_real_control.py holds the
 * sentence to them.
 */

/** The words of the sentence that offer Re-examine. */
export const REEXAMINE_OFFER = 'open Re-examine on their folder';
/** The word of them made the action. */
const WORD = 'Re-examine';

/** Does `sentence` offer Re-examine? */
export function offersReexamine(sentence) {
    return String(sentence || '').includes(REEXAMINE_OFFER);
}

/**
 * `sentence` as the pieces a line is made of: [the words before, `makeAction()` (an element saying Re-examine), the words
 * after] when it offers Re-examine, else [sentence]. The pieces' text is the sentence's.
 */
export function withReexamine(sentence, makeAction) {
    const text = String(sentence || '');
    if (!offersReexamine(text)) return [text];
    const word = text.indexOf(REEXAMINE_OFFER) + REEXAMINE_OFFER.indexOf(WORD);
    return [text.slice(0, word), makeAction(), text.slice(word + WORD.length)];
}
