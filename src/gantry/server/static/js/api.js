// api.js: the ONLY module that knows endpoint paths (docs/PROTOCOL.md section 2).
import { openStream } from './core/stream.js';

export class ApiError extends Error {
  constructor(status, message) {
    super(message || `HTTP ${status}`);
    this.status = status;
  }
}

export async function api(method, path, body) {
  const res = await fetch(path, {
    method, cache: 'no-store',
    headers: { Accept: 'application/json', ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* empty body */
  }
  if (!res.ok) throw new ApiError(res.status, (data && (data.error || data.detail)) || `HTTP ${res.status}`);
  return data;
}

const m = (id) => `/api/matches/${encodeURIComponent(id)}`;
export const boot = () => api('GET', '/api/boot');
export const newMatch = (opts) => api('POST', '/api/matches', opts);
export const snapshot = (id) => api('GET', m(id));
export const control = (id, op, value) => api('POST', `${m(id)}/control`, { op, value });
export const trigger = (id) => api('POST', `${m(id)}/trigger`);
export const say = (id, name, text) => api('POST', `${m(id)}/chat`, { name, text });
export const vote = (id, pollId, option) => api('POST', `${m(id)}/vote`, { poll_id: pollId, option });
export const decide = (id, body) => api('POST', `${m(id)}/decide`, body);
export const inject = (id, ev) => api('POST', `${m(id)}/events`, ev);
export const threads = (id) => api('GET', `${m(id)}/threads`);
export const memory = () => api('GET', '/api/memory');
export const resetMemory = () => api('POST', '/api/memory/reset');

/** Follow a broadcast. Reconnects on its own and resumes from the last event id, so nothing is missed or repeated. */
export function follow(id, since, onMessage, onState) {
  return openStream({
    url: `${m(id)}/stream`, lastEventId: String(since), idleTimeoutMs: 45000,
    onEvent(ev) {
      let data = null;
      try {
        data = JSON.parse(ev.data);
      } catch {
        return;
      }
      onMessage(ev.event, data);
    },
    onState,
  });
}
