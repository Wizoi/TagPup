// TagPup's page: Smart Rename and Shift Date Taken.
import { api } from './common/api.js';
import { ruleProblem } from './common/validate.js';
import { samePath } from './common/paths.js';
import { state } from './state.js';
import {
    btnApplyRename, btnToggleRename, btnToggleTimeshift, renameGroupingInput, renamePanel,
    statusDot, statusText, thumbnailsGrid, timeshiftCameraSelect, timeshiftMinutesInput,
    timeshiftPanel
} from './elements.js';
import { flagField, setStatus } from './status.js';
import { saveToLocalStorageCache } from './cache.js';
import { updatePeopleDatalist, updateTagsDatalist } from './tags.js';
import { renderFileList } from './folder.js';
import { renderThumbnails } from './grid.js';
import { updateSelectedThumbnailsCount } from './selection.js';

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

        if (!confirm(`Are you sure you want to smart-rename the ${state.selectedThumbnails.length} selected photos?`)) {
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
            if (!res.ok) return res.json().then(e => { throw new Error(e.error || 'Rename failed') });
            return res.json();
        })
        .then(data => {
            // What moved, by the server's account -- not the selection, which is
            // cleared below and was being counted after that, so this said 0.
            const renamedCount = Object.entries(data.updated_paths || {})
                .filter(([from, to]) => !samePath(from, to)).length;
            state.folderPhotos = data.updated_photos;
            state.selectedThumbnails = [];
            state.lastSelectedPath = null;
            
            renameGroupingInput.value = '';
            renamePanel.classList.add('hidden');
            btnToggleRename.classList.remove('active');
            
            renderFileList();
            renderThumbnails();
            updateSelectedThumbnailsCount();
            saveToLocalStorageCache();
            
            setStatus('ready', `Renamed ${renamedCount} photo(s)`);
        })
        .catch(err => {
            console.error(err);
            setStatus('error', 'Smart rename failed', { transient: false });
            btnApplyRename.disabled = false;
            alert("Error during smart rename: " + err.message);
        });
    });
}

// The camera a photo came from, as tagpup.core.fields names it for the server's
// time shift (tests/test_rules_have_one_owner.py holds this copy to it): the first
// of CAMERA_FIELDS the photo has, else UNKNOWN_CAMERA. ALL_CAMERAS asks for every one.
export const CAMERA_FIELDS = ['EXIF:Model', 'Model', 'EXIF:Make', 'Make'];
export const UNKNOWN_CAMERA = 'Unknown Camera';
export const ALL_CAMERAS = 'All Cameras';

export function populateCameraModelsDropdown() {
    if (!state.folderPhotos || state.folderPhotos.length === 0) {
        timeshiftPanel.classList.add('hidden');
        btnToggleTimeshift.disabled = true;
        return;
    }

    btnToggleTimeshift.disabled = false;
    timeshiftCameraSelect.innerHTML = '';

    // Group photos by camera model
    const modelCounts = {};
    state.folderPhotos.forEach(photo => {
        const model = cameraModelOf(photo);
        modelCounts[model] = (modelCounts[model] || 0) + 1;
    });

    // The option for every camera
    const optAll = document.createElement('option');
    optAll.value = ALL_CAMERAS;
    optAll.textContent = `${ALL_CAMERAS} (${state.folderPhotos.length} photos)`;
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
    const raw = (photo && photo.raw_metadata) || {};
    const field = CAMERA_FIELDS.find(name => raw[name]);
    return field ? raw[field] : UNKNOWN_CAMERA;
}

/** Is `photo` one a time shift for `camera` is about? As the server decides it. */
export function onCamera(photo, camera) {
    return camera === ALL_CAMERAS || cameraModelOf(photo) === camera;
}

export function applyTimeShift() {
    const folder = state.scannedFolder;
    if (!folder) return;
    
    const cameraModel = timeshiftCameraSelect.value;
    const minutes = parseInt(timeshiftMinutesInput.value, 10);
    
    if (isNaN(minutes) || minutes === 0) {
        // Beside the field, where it can be read and fixed in one motion.
        flagField(timeshiftMinutesInput, 'Enter a shift in minutes (not zero)');
        return;
    }

    // How many photos this is actually about. "All photos for camera X" does not
    // say whether that is four or four hundred, and the two deserve different
    // amounts of hesitation.
    const affected = state.folderPhotos.filter(p => onCamera(p, cameraModel)).length;
    const direction = minutes > 0 ? 'later' : 'earlier';
    const promptMsg = [
        `Shift Date Taken by ${Math.abs(minutes)} minute(s) ${direction}?`,
        '',
        `Camera: ${cameraModel}`,
        `Photos affected: ${affected}`,
        '',
        'This rewrites the timestamp inside each photo file and CANNOT be undone from here.',
    ].join('\n');
    if (!confirm(promptMsg)) return;

    setStatus('busy', `Shifting ${affected} photo(s) by ${minutes} minute(s)...`);
    
    api.json('/api/folder/time-shift', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            folder_path: folder,
            camera_model: cameraModel,
            shift_minutes: minutes
        })
    })
    .then(data => {
        if (data.success) {
            if (data.updated_photos) {
                state.folderPhotos = data.updated_photos;
            }
            
            // What ExifTool wrote, not what was asked: a photo it could not
            // write keeps its old time, and saying so is the only way to know.
            const done = data.updated_count ?? 0;
            const asked = data.requested_count ?? affected;
            if (done < asked) {
                setStatus('error', `Time shift applied to ${done} of ${asked} photo(s); ${asked - done} could not be written`,
                    { transient: false });
            } else {
                setStatus('ready', `Time shift applied to ${done} photo(s)`);
            }

            renderFileList();
            renderThumbnails();
            updateTagsDatalist();
            updatePeopleDatalist();
            populateCameraModelsDropdown();
            saveToLocalStorageCache();
        } else {
            throw new Error(data.error || "Time shift failed");
        }
    })
    .catch(err => {
        console.error(err);
        setStatus('error', 'Time shift failed', { transient: false });
        alert("Error shifting time: " + err.message);
    });
    timeshiftMinutesInput.value = 0; // reset
    updateCameraHighlights();
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
