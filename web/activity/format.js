// How the Activity page says a time, a length of time, a size and a set of counts. The
// server's times are local, "YYYY-MM-DD HH:MM:SS", as the library's records spell them.

/** A server time as a Date, or null. */
export function parseTime(text) {
    if (!text) return null;
    const date = new Date(String(text).replace(' ', 'T'));
    return Number.isNaN(date.getTime()) ? null : date;
}

function span(seconds) {
    if (seconds < 60) return `${seconds} s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)} h`;
    return `${Math.round(seconds / 86400)} d`;
}

/** "5 min ago", "in 3 h", or `otherwise` for no time. */
export function ago(text, now = Date.now(), otherwise = 'never') {
    const date = parseTime(text);
    if (!date) return otherwise;
    const seconds = Math.round((now - date.getTime()) / 1000);
    return seconds < 0 ? `in ${span(-seconds)}` : `${span(seconds)} ago`;
}

/** A number of seconds as "1 min 5 s", or '' for none. */
export function duration(seconds) {
    if (seconds === null || seconds === undefined || seconds === '') return '';
    const whole = Math.max(0, Math.round(Number(seconds)));
    if (whole < 60) return `${whole} s`;
    const minutes = Math.floor(whole / 60);
    if (minutes < 60) return whole % 60 ? `${minutes} min ${whole % 60} s` : `${minutes} min`;
    return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

/** A number of bytes as "1.4 GB". */
export function bytes(count) {
    let value = Number(count) || 0;
    const units = ['bytes', 'KB', 'MB', 'GB', 'TB'];
    let unit = 0;
    while (value >= 1000 && unit < units.length - 1) {
        value /= 1000;
        unit += 1;
    }
    return unit ? `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}` : `${value} bytes`;
}

/** {what: count} as "changed 3, attempted 5": the counts that are not zero. */
export function counts(found) {
    return Object.entries(found || {})
        .filter(([, value]) => typeof value === 'number' && value !== 0)
        .map(([name, value]) => `${name.replace(/_/g, ' ')} ${value}`)
        .join(', ');
}
