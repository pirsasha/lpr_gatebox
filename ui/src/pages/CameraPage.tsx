// =========================================================
// Файл: ui/src/pages/CameraPage.tsx
// Проект: LPR GateBox
// Версия: v0.3.x
// =========================================================

import React, { useEffect, useState } from "react";
import { getSettings, saveCameraSettings, testCamera } from "../api/gatebox";

export default function CameraPage() {
  const [loading, setLoading] = useState(true);

  const [rtspUrl, setRtspUrl] = useState("");
  const [enabled, setEnabled] = useState(true);

  const [testState, setTestState] = useState<
    | { kind: "idle" }
    | { kind: "testing" }
    | { kind: "ok"; w: number; h: number; ms: number }
    | { kind: "err"; msg: string }
  >({ kind: "idle" });

  const [saveState, setSaveState] = useState<
    "idle" | "saving" | "saved" | "error"
  >("idle");
  const [saveError, setSaveError] = useState<string>("");

  // загрузка текущих настроек
  useEffect(() => {
    (async () => {
      try {
        const s = await getSettings();
        const cam = s.camera ?? {};
        setRtspUrl(String(cam.rtsp_url ?? ""));
        setEnabled(Boolean(cam.enabled ?? true));
      } catch (e: any) {
        // если не смогли загрузить — не страшно, просто покажем пусто
        console.warn(e);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  async function onTest() {
    setSaveState("idle");
    setSaveError("");
    setTestState({ kind: "testing" });

    const url = rtspUrl.trim();
    if (!url) {
      setTestState({ kind: "err", msg: "Вставь RTSP ссылку" });
      return;
    }

    const res = await testCamera(url);
    if (res.ok) {
      setTestState({ kind: "ok", w: res.width, h: res.height, ms: res.grab_ms });
    } else {
      setTestState({ kind: "err", msg: `Не работает: ${res.error}` });
    }
  }

  async function onSave() {
    setSaveState("saving");
    setSaveError("");

    try {
      await saveCameraSettings({ rtsp_url: rtspUrl.trim(), enabled });
      setSaveState("saved");
      // маленький бонус UX: после сохранения можно сразу подсказать проверить
      if (testState.kind === "idle") {
        // ничего
      }
    } catch (e: any) {
      setSaveState("error");
      setSaveError(e?.message ?? "Ошибка сохранения");
    }
  }

  if (loading) {
    return <div style={{ padding: 16 }}>Загрузка…</div>;
  }

  return (
    <div style={{ padding: 16, maxWidth: 760 }}>
      <h2 style={{ marginTop: 0 }}>Камера</h2>

      <div style={{ marginBottom: 12 }}>
        <label style={{ display: "block", fontWeight: 600, marginBottom: 6 }}>
          RTSP URL
        </label>
        <input
          value={rtspUrl}
          onChange={(e) => setRtspUrl(e.target.value)}
          placeholder="rtsp://user:pass@192.168.1.10:554/stream"
          style={{
            width: "100%",
            padding: "10px 12px",
            borderRadius: 8,
            border: "1px solid #ccc",
            fontSize: 14,
          }}
        />
        <div style={{ fontSize: 12, opacity: 0.7, marginTop: 6 }}>
          Вставь RTSP ссылку от камеры. Можно без логина/пароля, если камера так настроена.
        </div>
      </div>

      <label style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12 }}>
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
        />
        Камера включена
      </label>

      <div style={{ display: "flex", gap: 10, marginBottom: 14 }}>
        <button
          onClick={onTest}
          disabled={testState.kind === "testing"}
          style={{
            padding: "10px 14px",
            borderRadius: 10,
            border: "1px solid #ccc",
            cursor: "pointer",
          }}
        >
          {testState.kind === "testing" ? "Проверяю…" : "Проверить"}
        </button>

        <button
          onClick={onSave}
          disabled={saveState === "saving"}
          style={{
            padding: "10px 14px",
            borderRadius: 10,
            border: "1px solid #ccc",
            cursor: "pointer",
            fontWeight: 600,
          }}
        >
          {saveState === "saving" ? "Сохраняю…" : "Сохранить"}
        </button>
      </div>

      {/* результат проверки */}
      <div style={{ padding: 12, border: "1px solid #eee", borderRadius: 12 }}>
        {testState.kind === "idle" && <div>Нажми “Проверить”, чтобы убедиться что камера доступна.</div>}
        {testState.kind === "testing" && <div>Проверяю RTSP…</div>}
        {testState.kind === "ok" && (
          <div>
            ✅ Камера работает — {testState.w}×{testState.h} — {testState.ms} ms
          </div>
        )}
        {testState.kind === "err" && <div>❌ {testState.msg}</div>}

        {saveState === "saved" && <div style={{ marginTop: 8 }}>✅ Сохранено</div>}
        {saveState === "error" && (
          <div style={{ marginTop: 8 }}>❌ Ошибка сохранения: {saveError}</div>
        )}
      </div>
    </div>
  );
}