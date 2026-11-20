/**
 * Where a photo's file and a clip's file are asked for: the one spelling of /api/photo-file and /api/video-file, both
 * pages' (the desktop's picture, grid and player; the phone's Photo screen). Each carries the row's modified time
 * (`mtime`, which a folder's record and a library card both carry), so a file turned or rewritten on the PC is a new
 * address, and the browser's kept copy of the old one is not shown.
 */
import { api } from './api.js';

/**
 * The photo's file at `size` pixels on its long side (none: the file whole), for an img's src. TagPup's route turns it
 * upright always; TagTuner's route keeps the stored pixels its face boxes are in unless `upright` asks.
 */
export function photoFileUrl(photo, size = 0, { upright = false } = {}) {
    return api.image(`/api/photo-file?path=${encodeURIComponent(photo.path)}${size ? `&size=${size}` : ''}`
        + (upright ? '&upright=1' : '')
        + `&v=${encodeURIComponent(photo.mtime || 0)}`);
}

/** The route a clip is played from, by its id (not prefixed with the library: api.url and api.fetch put it there). */
export function videoRoute(photo) {
    return `/api/video-file?id=${encodeURIComponent(photo.id)}&v=${encodeURIComponent(photo.mtime || 0)}`;
}
