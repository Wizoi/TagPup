/**
 * The one request that stops a batch by its ticket (POST /api/activity/batches/cancel). Activity's Now, the clip panel and the batch
 * strip all send it from here; it is a module of its own so Activity does not take the whole strip (and its history dialog) with it.
 */
import { api } from './api.js';

/** Resolves to '' when the server took the request, else the sentence to show (the server's, or that the request was not sent). */
export async function cancelByTicket(library, ticket) {
    try {
        const answer = await api.site.json('/api/activity/batches/cancel', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ library, ticket }),
        });
        return answer.success ? '' : (answer.error || 'The server did not stop it.');
    } catch (err) {
        return `Cancel was not sent (${err.message}).`;
    }
}
