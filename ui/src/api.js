// ui/src/api.js
// v0.2.4-fix6
//
// DEV: через Vite proxy: /api -> http://127.0.0.1:8080
// PROD: можно задать VITE_API_BASE=http://127.0.0.1:8080

export const API_BASE = String(import.meta?.env?.VITE_API_BASE || "").replace(/\/$/, "");
export const API_PREFIX = "/api/v1";

export function apiUrl(path) {
  if (!path.startsWith("/")) path = `/${path}`;
  return API_BASE ? `${API_BASE}${path}` : path;
}

async function check(r) {
  if (r.ok) return r;
  let text = "";
  try {
    text = await r.text();
  } catch {
    /* ignore */
  }
  throw new Error(`${r.status} ${r.statusText}${text ? `: ${text}` : ""}`);
}

export async function apiGet(path) {
  const r = await fetch(apiUrl(path), { method: "GET" });
  await check(r);
  return r.json();
}

export async function apiPut(path, body) {
  const r = await fetch(apiUrl(path), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  await check(r);
  return r.json();
}

export async function apiPost(path, body) {
  const r = await fetch(apiUrl(path), {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  await check(r);
  return r.json();
}

// -------- API methods --------

export function getEvents(limit = 30, opts = {}) {
  const afterTs = opts.after_ts != null ? `&after_ts=${encodeURIComponent(String(opts.after_ts))}` : "";
  const inc = opts.include_debug ? "&include_debug=1" : "";
  return apiGet(`${API_PREFIX}/events?limit=${encodeURIComponent(String(limit))}${afterTs}${inc}`);
}

export function getRtspStatus() {
  return apiGet(`${API_PREFIX}/rtsp/status`);
}

export function reloadWhitelist() {
  return apiPost(`${API_PREFIX}/whitelist/reload`);
}

export function getWhitelist() {
  return apiGet(`${API_PREFIX}/whitelist`);
}

export function putWhitelist(plates) {
  return apiPut(`${API_PREFIX}/whitelist`, { plates });
}

export function getSettings() {
  return apiGet(`${API_PREFIX}/settings`);
}

export function putSettings(partial) {
  return apiPut(`${API_PREFIX}/settings`, partial);
}

export function applySettings() {
  return apiPost(`${API_PREFIX}/settings/apply`);
}

export function rtspFrameUrl(ts) {
  const q = ts ? `?ts=${encodeURIComponent(String(ts))}` : "";
  return apiUrl(`${API_PREFIX}/rtsp/frame.jpg${q}`);
}

export function rtspBoxes() {
  return apiGet(`${API_PREFIX}/rtsp/boxes`);
}

export function eventsStreamUrl(opts = {}) {
  const inc = opts.include_debug ? "?include_debug=1" : "";
  return apiUrl(`${API_PREFIX}/events/stream${inc}`);
}

export async function apiDownload(path, filename = "download.bin") {
  const res = await fetch(apiUrl(path));
  await check(res);
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
}