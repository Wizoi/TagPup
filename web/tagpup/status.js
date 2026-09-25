// TagPup's page: the status line, and a field that says what is wrong with it.
import { state } from './state.js';
import { statusDot, statusText } from './elements.js';

/**
 * Say what just happened, without stopping the work to say it.
 *
 * The status line was already carrying this; the modals were stacked on top of
 * it, so finishing a bulk rename meant dismissing a box to report that the thing
 * you watched happen had happened. Modals are kept for the two cases that earn
 * them: a question that must be answered before acting, and a failure that would
 * otherwise pass unnoticed.
 *
 * `kind` is 'ready', 'busy' or 'error'. A 'ready' message with `transient` set
 * falls back to Ready on its own, so the line does not keep claiming the result
 * of something you did five minutes ago.
 */
export function setStatus(kind, message, { transient = true } = {}) {
    if (state.statusResetTimer) {
        clearTimeout(state.statusResetTimer);
        state.statusResetTimer = null;
    }
    statusDot.className = kind === 'ready'
        ? 'status-indicator-dot'
        : `status-indicator-dot ${kind}`;
    statusText.textContent = message;

    if (kind === 'ready' && transient && message !== 'Ready') {
        state.statusResetTimer = setTimeout(() => {
            statusText.textContent = 'Ready';
            state.statusResetTimer = null;
        }, 6000);
    }
}

/**
 * Put a validation message beside the field it is about.
 *
 * "Please enter a grouping name" in a modal hides the form you need to correct.
 * Said next to the field, it can be read and fixed in one motion.
 */
export function flagField(input, message) {
    if (!input) {
        setStatus('error', message, { transient: false });
        return;
    }
    input.classList.add('field-invalid');
    input.setAttribute('title', message);
    setStatus('error', message, { transient: false });
    input.focus();
    const clear = () => {
        input.classList.remove('field-invalid');
        input.removeAttribute('title');
        input.removeEventListener('input', clear);
    };
    input.addEventListener('input', clear);
}
