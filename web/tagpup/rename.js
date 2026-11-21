// TagPup's page: Smart Rename and Shift Date Taken -- Organize's, a folder's; a library view offers neither (#669).
import { api } from './common/api.js';
import { ruleProblem } from './common/validate.js';
import { pathKey, samePath } from './common/paths.js';
import { takeStamp } from './common/photo-write.js';
import { state } from './state.js';
import {
    btnApplyRename, btnToggleRename, btnToggleTimeshift, detailDateTaken, renameGroupingInput, renamePanel,
    statusDot, statusText, thumbnailsGrid, timeshiftCameraSelect, timeshiftMinutesInput,
    timeshiftPanel
} from './elements.js';
import { flagField, setStatus } from './status.js';
import { saveToLocalStorageCache } from './cache.js';
import { updatePeopleDatalist, updateTagsDatalist } from './tags.js';
import { countSaid, renderFileList } from './folder.js';
import { renderThumbnails } from './grid.js';
import { updateSelectedThumbnailsCount } from './selection.js';
import { BULK_LIMIT, clearSelection, selectedRecords } from './selected.js';
import { whereWritten } from './write-queue.js';
import { photosRenamed } from './edits.js';
import { isVideo } from './media.js';
import { isJustLooking } from './looking.js';
import { takenSaid } from './format.js';

/**
 * Why a Smart Rename grouping cannot be used, or null if it can: the server's rule
 * (the "grouping" kind of tagpup/core/validation.py), which Smart Rename refuses by.
 * " - " separates the parts of a photo's name, and editing a caption later finds
 * the photo's number by splitting the name there.
 */
export function groupingProblem(grouping) {
    return ruleProblem('grouping', grouping);
}

export function wireRenameAndTimeShift() {
    btnToggleTimeshift.addEventListener('click', () => {
        timeshiftPanel.classList.toggle('hidden');
        btnToggleTimeshift.classList.toggle('active');
        updateCameraHighlights();
    });
    timeshiftCameraSelect.addEventListener('change', updateCameraHighlights);
    
    btnToggleRename.addEventListener('click', () => {
        renamePanel.classList.toggle('hidden');
        btnToggleRename.classList.toggle('active');
        if (!renamePanel.classList.contains('hidden')) {
            renameGroupingInput.focus();
        }
    });

    btnApplyRename.addEventListener('click', () => {
        const grouping = renameGroupingInput.value.trim();
        const groupingRefused = groupingProblem(grouping);
        if (groupingRefused) {
            flagField(renameGroupingInput, groupingRefused);
            return;
        }

        if (state.selectedThumbnails.length === 0) {
            setStatus('error', 'Select some photos first \u2014 nothing is selected',
                      { transient: false });
            return;
        }

        if (state.selectedThumbnails.length > BULK_LIMIT) {
            alert(`Select up to ${BULK_LIMIT.toLocaleString()} photos at a time.`);
            return;
        }
        // What will be renamed: the selected photos and videos, numbered together (DECISIONS 2026-11-08, A5).
        if (!confirm(`Are you sure you want to smart-rename the ${countSaid(selectedRecords())} selected?`)) {
            return;
        }

        statusDot.className = 'status-indicator-dot busy';
        statusText.textContent = 'Smart renaming...';
        btnApplyRename.disabled = true;

        api.fetch('/api/folder/rename-photos', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                folder_path: state.scannedFolder,
                photo_paths: state.selectedThumbnails,
                grouping: grouping
            })
        })
        .then(res => {
            if (!res.ok) {
                return res.json().then(e => {
                    // Stopped part-way (the second part of a mixed rename): the names that DID change are shown.
                    if (Array.isArray(e.updated_photos)) {
                        photosRenamed(e.updated_paths || {});
                        state.folderPhotos = e.updated_photos;
                        clearSelection();
                        state.lastSelectedPath = null;
                        renderFileList();
                        renderThumbnails();
                        updateSelectedThumbnailsCount();
                        saveToLocalStorageCache();
                    }
                    throw new Error(e.error || 'Rename failed');
                });
            }
            return res.json();
        })
        .then(data => {
            // What moved, by the server's account -- not the selection, which is
            // cleared below and was being counted after that, so this said 0.
            const renamedCount = Object.entries(data.updated_paths || {})
                .filter(([from, to]) => !samePath(from, to)).length;
            photosRenamed(data.updated_paths || {});
            state.folderPhotos = data.updated_photos;
            clearSelection();
            state.lastSelectedPath = null;
            
            renameGroupingInput.value = '';
            renamePanel.classList.add('hidden');
            btnToggleRename.classList.remove('active');
            
            renderFileList();
            renderThumbnails();
            updateSelectedThumbnailsCount();
            saveToLocalStorageCache();
            
            setStatus('ready', `Renamed ${renamedCount} photo(s)` + whereWritten(data));
        })
        .catch(err => {
            console.error(err);
            setStatus('error', 'Smart rename failed', { transient: false });
            btnApplyRename.disabled = false;
            alert("Error during smart rename: " + err.message);
        });
    });
}

// The camera a photo came from is named by the server (tagpup.core.photo_meta.camera_name, the photo record's `camera`),
// as it chooses the photos of a time shift by it (tagpup.core.fields.camera_of): the page keeps no copy of the rule.
// UNKNOWN_CAMERA is the name of a photo that has none; ALL_CAMERAS asks for every one.
export const UNKNOWN_CAMERA = 'Unknown Camera';
export const ALL_CAMERAS = 'All Cameras';

export function populateCameraModelsDropdown() {
    // The cameras are a folder's: a view has none to list, and its time shift is not held back by an empty folder.
    if (state.library) return;
    if (!state.folderPhotos || state.folderPhotos.length === 0) {
        timeshiftPanel.classList.add('hidden');
        btnToggleTimeshift.disabled = true;
        return;
    }

    btnToggleTimeshift.disabled = false;
    timeshiftCameraSelect.innerHTML = '';

    // Group photos by camera model: a folder's clips are listed with its photos, and a time shift is its photos only.
    const modelCounts = {};
    const shiftable = state.folderPhotos.filter(photo => !isVideo(photo));
    shiftable.forEach(photo => {
        const model = cameraModelOf(photo);
        modelCounts[model] = (modelCounts[model] || 0) + 1;
    });

    // The option for every camera
    const optAll = document.createElement('option');
    optAll.value = ALL_CAMERAS;
    optAll.textContent = `${ALL_CAMERAS} (${shiftable.length} photos)`;
    timeshiftCameraSelect.appendChild(optAll);

    // Sort camera models alphabetically
    const sortedModels = Object.keys(modelCounts).sort();
    sortedModels.forEach(model => {
        const opt = document.createElement('option');
        opt.value = model;
        opt.textContent = `${model} (${modelCounts[model]} photos)`;
        timeshiftCameraSelect.appendChild(opt);
    });
    
    timeshiftMinutesInput.value = 0; // reset
    updateCameraHighlights();
}

/** The camera a photo came from, as the time-shift dropdown labels it. */
export function cameraModelOf(photo) {
    return (photo && photo.camera) || UNKNOWN_CAMERA;
}

/** Is `photo` one a time shift for `camera` is about? As the server decides it: never a clip (folder_time_shift). */
export function onCamera(photo, camera) {
    return !isVideo(photo) && (camera === ALL_CAMERAS || cameraModelOf(photo) === camera);
}

export function applyTimeShift() {
    // A folder shifts a camera's photos; a library view has no time shift (#669).
    if (state.library) return;
    const folder = state.scannedFolder;
    if (!folder) return;
    
    const cameraModel = timeshiftCameraSelect.value;
    const minutes = parseInt(timeshiftMinutesInput.value, 10);
    
    if (isNaN(minutes) || minutes === 0) {
        // Beside the field, where it can be read and fixed in one motion.
        flagField(timeshiftMinutesInput, 'Enter a shift in minutes (not zero)');
        return;
    }

    // The photos this is about, chosen here (never a clip: onCamera) and sent by path, as a folder's records carry no
    // id. How many matters too: "All photos for camera X" does not say whether that is four or four hundred, and the
    // two deserve different amounts of hesitation.
    const shifted = state.folderPhotos.filter(p => onCamera(p, cameraModel));
    if (!shifted.length) {
        // A folder of clips only (its list of cameras is of its photos: populateCameraModelsDropdown): there is nothing
        // to send, and the server refuses a shift of no photo.
        setStatus('error', "No photos to shift: a clip's Date Taken is not changed", { transient: false });
        return;
    }
    const affected = shifted.length;
    const direction = minutes > 0 ? 'later' : 'earlier';
    const promptMsg = [
        `Shift Date Taken by ${Math.abs(minutes)} minute(s) ${direction}?`,
        '',
        `Camera: ${cameraModel}`,
        `Photos affected: ${affected}`,
        '',
        isJustLooking()
            ? 'This rewrites the timestamp inside each photo file and CANNOT be undone from here.'
            : 'This rewrites the timestamp inside each photo file; it can be undone from History.',
    ].join('\n');
    if (!confirm(promptMsg)) return;

    setStatus('busy', `Shifting ${affected} photo(s) by ${minutes} minute(s)...`);

    // One request, through the one entry point of photo writes (plan one-write-path): the server writes them as one
    // change, under its own cap of a write's photos.
    api.json('/api/photos/write', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            op: 'shift',
            params: { minutes },
            changes: Object.fromEntries(shifted.map(p => [p.path, { id: null }])),
        })
    })
    .then(data => tookShift(folder, data))
    .catch(err => {
        console.error(err);
        setStatus('error', 'Time shift failed', { transient: false });
        alert("Error shifting time: " + err.message);
    });
    timeshiftMinutesInput.value = 0; // reset
    updateCameraHighlights();
}

/**
 * A time shift's reply (POST /api/photos/write's envelope, op `shift`) taken into the folder it was sent from: each photo
 * written takes its file's stamp and its new Date Taken as the server spells it (`written[path].taken`), and the folder is
 * put back in Date Taken order (inTakenOrder). A share that stopped part-way answers 400 with `written` naming only what
 * landed: those are taken all the same, and the status says how many were not. A folder left before the reply (another
 * folder, a library view) is not touched: its records are not these. A reply that is no envelope (a refusal before any
 * write: a library being renamed, over the cap, cancelled while waiting its turn) is the failure it says.
 */
function tookShift(folder, data) {
    if (!data || typeof data.attempted !== 'number') throw new Error((data && data.error) || 'Time shift failed');
    if (!state.library && samePath(state.scannedFolder, folder)) {
        const written = new Map(Object.entries(data.written || {}).map(([path, w]) => [pathKey(path), w]));
        for (const record of state.folderPhotos) {
            const w = written.get(pathKey(record.path));
            if (!w) continue;
            takeStamp(record, w);
            record.taken = w.taken;
            if (samePath(state.activePhotoPath, record.path)) detailDateTaken.textContent = takenSaid(w.taken);
        }
        state.folderPhotos = inTakenOrder(state.folderPhotos);
        renderFileList();
        renderThumbnails();
        updateTagsDatalist();
        updatePeopleDatalist();
        populateCameraModelsDropdown();
        saveToLocalStorageCache();
    }
    // What was written, not what was asked: a photo that could not be written keeps its old time, and saying so is the
    // only way to know.
    const changed = data.changed ?? 0;
    const attempted = data.attempted;
    if (changed < attempted) {
        const why = data.error || (data.errors && data.errors[0] && data.errors[0].why)
            || (data.skipped && data.skipped[0] && data.skipped[0].why) || '';
        setStatus('error', `Time shift applied to ${changed} of ${attempted} photo(s); ${attempted - changed} could not be `
            + `written${why ? `: ${why}` : ''}`, { transient: false });
    } else {
        setStatus('ready', `Time shift applied to ${changed} photo(s).${whereWritten(data)}`.replace(/\.$/, ''));
    }
}

/**
 * The folder's photos in the server's folder order (tagpup.services.photos.taken_order): by Date Taken as the library
 * spells it, those with none after them in the order they had. Stable, so photos taken at one moment keep theirs.
 */
function inTakenOrder(photos) {
    return photos
        .map((photo, at) => ({ photo, at }))
        .sort((a, b) => {
            const x = a.photo.taken || null;
            const y = b.photo.taken || null;
            if (x && y && x !== y) return x < y ? -1 : 1;
            if (x && !y) return -1;
            if (!x && y) return 1;
            return a.at - b.at;
        })
        .map(each => each.photo);
}

export function updateCameraHighlights() {
    if (timeshiftPanel.classList.contains('hidden')) {
        thumbnailsGrid.querySelectorAll('.thumbnail-card').forEach(card => {
            card.classList.remove('timeshift-highlighted');
        });
        return;
    }

    const cameraModel = timeshiftCameraSelect.value;
    
    thumbnailsGrid.querySelectorAll('.thumbnail-card').forEach(card => {
        const path = card.getAttribute('data-path');
        const photo = state.folderPhotos.find(p => p.path === path);
        if (photo) {
            if (onCamera(photo, cameraModel)) {
                card.classList.add('timeshift-highlighted');
            } else {
                card.classList.remove('timeshift-highlighted');
            }
        } else {
            card.classList.remove('timeshift-highlighted');
        }
    });
}
