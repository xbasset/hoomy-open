export class HttpError extends Error {
  constructor(status, code, message, details) { super(message); this.status = status; this.code = code; this.details = details; }
}
export function pilotClient(base) {
  return async (path, body) => {
    // Public routes select these paths; client-supplied paths never reach the pilot.
    const response = await fetch(new URL(path, base), { method: body === undefined ? 'GET' : 'POST', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(12000), redirect: 'error' });
    const data = await response.json();
    if (!response.ok) {
      // Ingestion errors never forward source content or private error details.
      if (path.startsWith('/api/ingestion/')) throw new HttpError(response.status, response.status === 409 ? 'ingestion_conflict' : 'ingestion_error', 'The central evidence operation could not complete.');
      if (path.startsWith('/api/project-state/')) throw new HttpError(response.status, response.status === 409 ? 'state_conflict' : 'state_error', 'The central project state operation could not complete.');
      throw new HttpError(response.status, response.status === 409 ? 'revision_conflict' : 'pilot_error', data.error || 'The project operation failed.', data);
    }
    return data;
  };
}
