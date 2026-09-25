// TagPup's page: how it words a photo's name and when it was taken.
import { state } from './state.js';

/** When a photo was taken, as the server says (`taken`, from tagpup.core.dates):
 *  "YYYY:MM:DD HH:MM:SS", or null. The page read fields of its own, and took the
 *  date the file was last modified for the date it was taken (#67). */
export function takenOf(photo) {
    return (photo && photo.taken) || null;
}

/** The last segment of a path: a photo's file name, or a folder as you know it. */
export function baseName(p) {
    if (!p) return '';
    return String(p).replace(/[\\/]+$/, '').split(/[\\/]/).pop() || '';
}

export function parseExifDateToLocalDate(rawStr) {
    const regex = /^(\d{4})[: -](\d{2})[: -](\d{2})\s+(\d{2}):(\d{2}):(\d{2})/;
    const match = String(rawStr).trim().match(regex);
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
    const hours24 = d.getHours();
    const mins = String(d.getMinutes()).padStart(2, '0');
    const secs = String(d.getSeconds()).padStart(2, '0');
    const ampm = hours24 >= 12 ? 'PM' : 'AM';
    const hours12 = hours24 % 12 || 12;
    return `${hours12}:${mins}:${secs} ${ampm}`;
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
    // Match standard formats like YYYY:MM:DD HH:MM:SS or similar
    const regex = /^(\d{4})[: -](\d{2})[: -](\d{2})\s+(\d{2}):(\d{2}):(\d{2})/;
    const match = String(exifStr).trim().match(regex);
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
