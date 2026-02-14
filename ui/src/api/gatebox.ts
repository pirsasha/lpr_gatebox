// =========================================================
// Файл: ui/src/api/gatebox.ts
// Проект: LPR GateBox
// Версия: v0.3.x (fix-save-405)
// Обновлено: 2026-02-08
//
// Что исправлено:
// - FIX: сохранение настроек камеры должно быть PUT, а не POST (иначе 405)
// - FIX: поддержка обоих префиксов API: /api/v1 (новый) и /api (legacy)
// - CHG: подробный текст ошибки из ответа сервера (для UI)
// =========================================================

export type CameraSettings = {
  rtsp_url: string;
  enabled: boolean;
};

export type GateboxSettings = {
  camera?: Partial<CameraSettings>;
  // могут быть и другие секции: mqtt/gate/ui/telegram...
};

const API_V1 = "/api/v1";
const API_LEGACY = "/api";

/** Универсальный fetch с fallback: сначала v1, потом legacy (если 404/405). */
async function fetchWithFallback(
  path: string,
  init: RequestInit,
  opts?: { acceptLegacyOn?: number[] }
): Promise<Response> {
  const acceptLegacyOn = opts?.acceptLegacyOn ?? [404, 405];

  // 1) v1
  const r1 = await fetch(`${API_V1}${path}`, init);
  if (r1.ok) return r1;

  // если метод/путь не поддержан — пробуем legacy
  if (acceptLegacyOn.includes(r1.status)) {
    const r2 = await fetch(`${API_LEGACY}${path}`, init);
    return r2;
  }

  return r1;
}

async function readTextSafe(r: Response): Promise<string> {
  try {
    return await r.text();
  } catch {
    return "";
  }
}

export async function getSettings(): Promise<GateboxSettings> {
  const r = await fetchWithFallback("/settings", { method: "GET" }, { acceptLegacyOn: [404] });
  if (!r.ok) throw new Error(`GET settings failed: ${r.status}${(await readTextSafe(r)) ? ` ${await readTextSafe(r)}` : ""}`);

  const data = await r.json();
  // на бэкенде формат: { ok: true, settings: {...} }
  return (data.settings ?? {}) as GateboxSettings;
}

/**
 * Сохраняем ТОЛЬКО камеру.
 * Бэкенд принимает оба формата:
 *  - {"settings": {...}} (рекомендуемый)
 *  - {...} (старый)
 * Мы шлём рекомендуемый.
 */
export async function saveCameraSettings(camera: CameraSettings): Promise<GateboxSettings> {
  const r = await fetchWithFallback(
    "/settings",
    {
      method: "PUT", // <-- ВАЖНО: PUT, не POST
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: { camera } }),
    },
    // 405 бывает на старом UI или если попали не туда — тогда пробуем legacy /api/settings
    { acceptLegacyOn: [404, 405] }
  );

  if (!r.ok) {
    const t = await readTextSafe(r);
    throw new Error(`PUT settings failed: ${r.status}${t ? ` ${t}` : ""}`);
  }

  const data = await r.json().catch(() => ({}));
  return (data.settings ?? {}) as GateboxSettings;
}

export type CameraTestResult =
  | { ok: true; width: number; height: number; grab_ms: number }
  | { ok: false; error: string; detail?: string };

export async function testCamera(rtsp_url: string): Promise<CameraTestResult> {
  // endpoint по swagger есть и в /api/camera/test и в /api/v1/camera/test
  const r = await fetchWithFallback(
    "/camera/test",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rtsp_url, timeout_sec: 6.0 }),
    },
    { acceptLegacyOn: [404] }
  );

  if (!r.ok) {
    const t = await readTextSafe(r);
    return { ok: false, error: `http_${r.status}`, detail: t };
  }
  return (await r.json()) as CameraTestResult;
}