// TagPup's page: how it words a photo's name, when it was taken and how big its file is.
import { state } from './state.js';

/** When a photo was taken, as the server says (`taken`, from tagpup.core.dates):
 *  "YYYY:MM:DD HH:MM:SS", or null. The page read fields of its own, and took the
 *  date the file was last modified for the date it was taken (#67). */
export function takenOf(photo) {
    return (photo && photo.taken) || null;
}

/** The digits of a stored Date Taken ("2021:03:27 00:00:00", some with a zone after): year, month, day, hours, minutes, seconds. */
export const TAKEN_DIGITS = /^(\d{4})[: -](\d{2})[: -](\d{2})\s+(\d{2}):(\d{2}):(\d{2})/;

/**
 * A time of day on a 12-hour clock: hours 0-23, minutes (and seconds, when given) as two digits: "12:05 AM", "3:42:07 PM".
 * The one spelling of the clock for the PC's details, the phone's facts and the grid.
 */
export function clockSaid(hours, minutes, seconds) {
    const hour = Number(hours);
    return `${hour % 12 || 12}:${minutes}${seconds === undefined ? '' : `:${seconds}`} ${hour >= 12 ? 'PM' : 'AM'}`;
}

/**
 * When the open photo was taken, whole, for the person checking it is right: "3/27/21 12:00 AM", or "Unknown" for a photo
 * with none (owner, 2026-11-19). Read from the digits of the stored Date Taken as they are, a wall-clock time: never
 * through a Date, which would move a time in the hour a clock skips (2:30 AM on the day it goes forward) and could be
 * read in another zone. A zone after the seconds ("-07:00", which some photos carry) is not shown. The grid and the
 * selection keep their short form (formatFriendlyDateSingle).
 */
export function takenSaid(taken) {
    const match = TAKEN_DIGITS.exec(String(taken || '').trim());
    if (!match) return 'Unknown';
    const [, year, month, day, hours, minutes] = match;
    return `${Number(month)}/${Number(day)}/${year.slice(-2)} ${clockSaid(hours, minutes)}`;
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
    'November', 'December'];
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

/**
 * When a photo was taken, read at a glance on the phone's Photo screen: "Sat, June 14, 2019, 3:42 PM"; '' for a photo with
 * none. The digits of the stored Date Taken as they are, a wall-clock time, as takenSaid reads them; the weekday is the
 * date's own (Date.UTC of the day alone: no zone, no clock that skips an hour).
 */
export function whenSaid(taken) {
    const match = TAKEN_DIGITS.exec(String(taken || '').trim());
    if (!match) return '';
    const [, year, month, day, hours, minutes] = match;
    const weekday = WEEKDAYS[new Date(Date.UTC(Number(year), Number(month) - 1, Number(day))).getUTCDay()];
    return `${weekday}, ${MONTHS[Number(month) - 1]} ${Number(day)}, ${year}, ${clockSaid(hours, minutes)}`;
}

/** A file's size as a person reads it: "820 KB", "3.4 MB", "1.2 GB" (of 1,024); "" for a size not read. */
export function fileSizeSaid(bytes) {
    if (!Number.isFinite(bytes) || bytes < 0) return '';
    const units = ['bytes', 'KB', 'MB', 'GB', 'TB'];
    let at = 0;
    let shown = bytes;
    while (shown >= 1024 && at < units.length - 1) { shown /= 1024; at += 1; }
    return `${at === 0 || shown >= 100 ? Math.round(shown) : shown.toFixed(1)} ${units[at]}`;
}

export function parseExifDateToLocalDate(rawStr) {
    const match = String(rawStr).trim().match(TAKEN_DIGITS);
    if (!match) return null;
    
    return new Date(
        parseInt(match[1], 10),
        parseInt(match[2], 10) - 1,
        parseInt(match[3], 10),
        parseInt(match[4], 10),
        parseInt(match[5], 10),
        parseInt(match[6], 10)
    );
}

export function getFolderDateStats() {
    // A library view spans years: every date is written whole.
    if (state.library) return { allWithin7Days: false, sameYearAcrossFolder: false };
    const dates = [];
    state.folderPhotos.forEach(photo => {
        const localD = takenOf(photo) && parseExifDateToLocalDate(takenOf(photo));
        if (localD) dates.push(localD);
    });

    if (dates.length === 0) {
        return {
            allWithin7Days: false,
            sameYearAcrossFolder: false
        };
    }

    let minT = dates[0].getTime();
    let maxT = dates[0].getTime();
    const years = new Set();
    dates.forEach(d => {
        const t = d.getTime();
        if (t < minT) minT = t;
        if (t > maxT) maxT = t;
        years.add(d.getFullYear());
    });

    const spanDays = (maxT - minT) / (1000 * 60 * 60 * 24);
    return {
        allWithin7Days: spanDays <= 7.0,
        sameYearAcrossFolder: years.size <= 1
    };
}

export function getFriendlyDatePart(d, stats) {
    const pad = (n) => String(n).padStart(2, '0');
    if (stats.allWithin7Days) {
        const weekdays = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
        return weekdays[d.getDay()];
    } else if (stats.sameYearAcrossFolder) {
        const month = pad(d.getMonth() + 1);
        const day = pad(d.getDate());
        return `${month}/${day}`;
    } else {
        const month = pad(d.getMonth() + 1);
        const day = pad(d.getDate());
        const year = String(d.getFullYear()).slice(-2);
        return `${month}/${day}/${year}`;
    }
}

export function format12HourTime(d) {
    return clockSaid(d.getHours(), String(d.getMinutes()).padStart(2, '0'), String(d.getSeconds()).padStart(2, '0'));
}

export function formatFriendlyDateSingle(d, stats) {
    const datePart = getFriendlyDatePart(d, stats);
    const timePart = format12HourTime(d);
    return `${datePart} ${timePart}`;
}

export function formatFriendlyDateRange(minDate, maxDate) {
    const stats = getFolderDateStats();
    const d1Str = formatFriendlyDateSingle(minDate, stats);
    const d2Str = formatFriendlyDateSingle(maxDate, stats);
    
    if (minDate.getTime() === maxDate.getTime()) {
        return d1Str;
    }
    
    const sameDay = minDate.getFullYear() === maxDate.getFullYear() &&
                    minDate.getMonth() === maxDate.getMonth() &&
                    minDate.getDate() === maxDate.getDate();
                    
    if (sameDay) {
        const datePart = getFriendlyDatePart(minDate, stats);
        return `${datePart} ${format12HourTime(minDate)} - ${format12HourTime(maxDate)}`;
    } else {
        return `${d1Str} - ${d2Str}`;
    }
}

export function exifDateToIso(exifStr) {
    if (!exifStr) return "";
    const match = String(exifStr).trim().match(TAKEN_DIGITS);
    if (!match) return "";
    
    const year = match[1];
    const month = match[2];
    const day = match[3];
    const hour = match[4];
    const min = match[5];
    const sec = match[6];
    return `${year}-${month}-${day}T${hour}:${min}:${sec}`;
}

export function getCurrentDateTimeIso() {
    const now = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    const y = now.getFullYear();
    const m = pad(now.getMonth() + 1);
    const d = pad(now.getDate());
    const h = pad(now.getHours());
    const min = pad(now.getMinutes());
    const s = pad(now.getSeconds());
    return `${y}-${m}-${d}T${h}:${min}:${s}`;
}
