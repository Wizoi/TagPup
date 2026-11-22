// TagPup's page: the Edit Date Taken dialog of the open photo or clip -- the file's name, the date field, Save (a click or
// Enter in the field) and Cancel (or Escape); the write goes through the photo write queue with every other.
import { baseName } from './common/paths.js';
import { holdFocus, releaseFocus } from './common/dialog.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import {
    btnCancelDateModal, btnCloseDateModal, btnEditDateTaken, btnSaveDateModal, dateTakenFile, dateTakenModal,
    dateTakenNote, detailDateTaken, inputDateTaken
} from './elements.js';
import { setStatus } from './status.js';
import { whereWritten } from './write-queue.js';
import { recordsWritten } from './cache.js';
import { exifDateToIso, getCurrentDateTimeIso, takenOf, takenSaid } from './format.js';
import { photoChangedOnDisk, queueWriteOf } from './edits.js';
import { postPhotoMetadata } from './common/photo-write.js';
import { renderFileList } from './folder.js';
import { isVideo } from './media.js';

export function wireDateTakenModal() {
    if (btnEditDateTaken && dateTakenModal) {
        btnEditDateTaken.addEventListener('click', () => {
            const photo = state.folderPhotos.find(p => p.path === state.activePhotoPath);
            if (!photo) return;
            
            const isoDate = exifDateToIso(takenOf(photo)) || getCurrentDateTimeIso();
            inputDateTaken.value = isoDate;
            // The date is often in the file's name: shown, as text to select, whole in the tooltip. One photo is
            // ever the subject here (the button is the details panel's, which a selection of several does not show).
            const name = baseName(photo.path);
            dateTakenFile.textContent = name;
            dateTakenFile.title = name;
            dateTakenNote.textContent = `Specify the date and time when this ${isVideo(photo) ? 'photo or video' : 'photo'} was taken.`;
            dateTakenModal.classList.add('active');
            holdFocus(dateTakenModal, { cancel: closeDateModal, focus: inputDateTaken });
        });

        // Closed by Save, Cancel or Escape, the focus is back on the Date Taken button: the arrows step photos again.
        const closeDateModal = () => {
            dateTakenModal.classList.remove('active');
            releaseFocus(dateTakenModal);
        };

        btnCloseDateModal.addEventListener('click', closeDateModal);
        btnCancelDateModal.addEventListener('click', closeDateModal);

        const saveDate = () => {
            // Closed means asked already: a second Enter or click while the first is being written finds it shut.
            if (!dateTakenModal.classList.contains('active')) return;
            const path = state.activePhotoPath;
            if (!path) return;

            const photo = state.folderPhotos.find(p => p.path === path);
            if (!photo) return;

            const newDateVal = inputDateTaken.value; // format: "YYYY-MM-DDTHH:MM:SS.sss"; '' when empty or not a whole date
            if (!newDateVal) {
                alert("Please enter a valid date and time.");
                return;
            }

            closeDateModal();

            // In the photo write queue with every other write: it sends the tags and
            // title too, and those are read when it runs, not when Save was clicked.
            queueWriteOf(photo.path, async () => {
                let saved;
                setStatus('busy', 'Saving date taken...');
                try {
                    saved = await postPhotoMetadata(photo, { date_taken: newDateVal }, photoChangedOnDisk);
                } catch (err) {
                    console.error(err);
                    setStatus('error', 'Error');
                    alert("Error saving date taken: " + err.message);
                    return false;
                }
                const formattedDate = saved.taken;   // as the server spells it (fields.date_taken_value)

                // What the server now records as when it was taken.
                photo.taken = formattedDate;
                // And its place in the folder, as the server keys it: it moves there at once, in the open folder and in
                // every scan kept holding it (recordsWritten), as after a Time Shift (owner, 2026-11-22).
                if ('order' in saved) photo.order = saved.order;
                recordsWritten([photo]);

                // Format and display in UI -- if this photo is still the one shown.
                if (state.activePhotoPath === photo.path) detailDateTaken.textContent = takenSaid(formattedDate);

                setStatus('ready', 'Saved the date taken.' + whereWritten(saved));
                renderFileList();
                upper.renderThumbnails();
                return true;
            });
        };
        btnSaveDateModal.addEventListener('click', saveDate);

        // Enter in the date field saves (also from inside its day, month and time parts); a button keeps its own
        // Enter, and Escape is web/common/dialog.js's. The dialog hears the keys alone: closing it here must not let
        // the same Enter reach the page's grid (it would open a photo), as askToSaveEdits does.
        dateTakenModal.addEventListener('keydown', (e) => {
            e.stopPropagation();
            if (e.key === 'Enter' && !(e.target.closest && e.target.closest('button'))) {
                e.preventDefault();
                saveDate();
            }
        });
    }
}
