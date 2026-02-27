// ui/src/api.js
// v0.2.4-fix12
//
// Под твою Swagger-картину:
// - Settings: /api/v1/settings (GET/PUT) ✅
// - Apply: /api/v1/settings/apply ✅
// - RTSP: /api/rtsp/* (legacy) ✅
// - Events/Whitelist: /api/v1/* ✅
// - Camera test: /api/v1/camera/test ✅
//
// FIX12:
// - Вернули export apiDownload (нужен System.jsx)
// - Экспортируем apiGet/apiPost/apiPut (как у тебя было)

export const API_BASE = String(import.meta?.env?.VITE_API_BASE || "").replace(/\/$/, "");

export const API_V1 = "/api/v1";
export const API_LEGACY = "/api";

export function apiUrl(path) {
  if (!path.startsWith("/")) path = `/${path}`;
  return API_BASE ? `${API_BASE}${path}` : path;
}

async function check(r) {
  if (r.ok) return r;
  let text = "";
  try {
    text = await r.text();
  } catch (e) {
    console.debug("cannot read error response text", e);
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

// ====== API methods ======

// Events (v1)
export function getEvents(limit = 30, opts = {}) {
  const afterTs =
    opts.after_ts != null ? `&after_ts=${encodeURIComponent(String(opts.after_ts))}` : "";
  const inc = opts.include_debug ? "&include_debug=1" : "";
  return apiGet(`${API_V1}/events?limit=${encodeURIComponent(String(limit))}${afterTs}${inc}`);
}

export function eventsStreamUrl(opts = {}) {
  const inc = opts.include_debug ? "?include_debug=1" : "";
  return apiUrl(`${API_V1}/events/stream${inc}`);
}

// RTSP (legacy) — чтобы старая камера жила
export function getRtspStatus() {
  return apiGet(`${API_LEGACY}/rtsp/status`);
}

export function rtspFrameUrl(ts) {
  const q = ts ? `?ts=${encodeURIComponent(String(ts))}` : "";
  return apiUrl(`${API_LEGACY}/rtsp/frame.jpg${q}`);
}

export function rtspBoxes() {
  return apiGet(`${API_LEGACY}/rtsp/boxes`);
}

export function rtspSnapshot() {
  return apiPost(`${API_V1}/rtsp/snapshot`, {});
}

// Whitelist (v1)
export function reloadWhitelist() {
  return apiPost(`${API_V1}/whitelist/reload`);
}

export function getWhitelist() {
  return apiGet(`${API_V1}/whitelist`);
}

export function putWhitelist(plates) {
  return apiPut(`${API_V1}/whitelist`, { plates });
}

// Settings (v1) — продуктово
export function getSettings() {
  return apiGet(`${API_V1}/settings`);
}

export function getRtspWorkerCapabilities() {
  return apiGet(`${API_V1}/rtsp_worker/capabilities`);
}

export function putSettings(partial) {
  // backend ждёт { settings: {...} }
  return apiPut(`${API_V1}/settings`, { settings: partial });
}

export function applySettings() {
  return apiPost(`${API_V1}/settings/apply`);
}

// Camera test (v1)
export function cameraTest(rtsp_url, timeout_sec = 6.0) {
  return apiPost(`${API_V1}/camera/test`, { rtsp_url, timeout_sec });
}

// Download helper (нужен System.jsx)
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
// Recent recognized plates (v1)
export function getRecentPlates() {
  return apiGet(`${API_V1}/recent_plates`);
}

export function recentPlateImageUrl(file) {
  return apiUrl(`${API_V1}/recent_plates/image/${encodeURIComponent(String(file || ""))}`);
}

export async function addWhitelistPlate(plate) {
  const p = String(plate || "").trim().toUpperCase().replace(/[\s\-_]+/g, "");
  if (!p) return { ok: false, reason: "empty" };

  const cur = await getWhitelist();
  const list = Array.isArray(cur?.plates) ? cur.plates.map((x) => String(x).trim().toUpperCase()) : [];
  if (!list.includes(p)) {
    list.unshift(p);
    await putWhitelist(Array.from(new Set(list)));
    await reloadWhitelist();
  }
  return { ok: true, plate: p };
}

export function mqttCheck() {
  return apiPost(`${API_V1}/mqtt/check`, {});
}

export function mqttTestPublish(topic, payload) {
  return apiPost(`${API_V1}/mqtt/test_publish`, { topic, payload });
}

export function telegramBotInfo() {
  return apiGet(`${API_V1}/telegram/bot_info`);
}


export function cloudpubStatus() {
  return apiGet(`${API_V1}/cloudpub/status`);
}

export function cloudpubConnect(payload = {}) {
  return apiPost(`${API_V1}/cloudpub/connect`, payload);
}

export function cloudpubDisconnect() {
  return apiPost(`${API_V1}/cloudpub/disconnect`, {});
}


export function cloudpubClearAudit() {
  return apiPost(`${API_V1}/cloudpub/audit/clear`, {});
}
