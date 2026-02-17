import React, { useEffect, useRef, useState } from "react";
import { cameraTest, getRtspStatus, getSettings, putSettings, rtspBoxes, rtspFrameUrl } from "../api";

type Roi = { x: number; y: number; w: number; h: number };

function clamp(n: number, a: number, b: number) {
  return Math.max(a, Math.min(b, n));
}

function parseRoiStr(roiStr: string): [number, number, number, number] | null {
  const parts = String(roiStr || "").split(",").map((x) => Number(x.trim()));
  if (parts.length !== 4 || parts.some((x) => !Number.isFinite(x))) return null;
  const [x1, y1, x2, y2] = parts;
  if (x2 <= x1 || y2 <= y1) return null;
  return [x1, y1, x2, y2];
}

export default function CameraPage() {
  const [loading, setLoading] = useState(true);

  const [rtspUrl, setRtspUrl] = useState("");
  const [enabled, setEnabled] = useState(true);

  const [status, setStatus] = useState<any>(null);
  const [boxes, setBoxes] = useState<any>(null);
  const [frameTs, setFrameTs] = useState<number>(() => Date.now());

  const [roi, setRoi] = useState<Roi | null>(null);
  const [drawing, setDrawing] = useState(false);
  const dragStart = useRef<{ x: number; y: number } | null>(null);
  const frameWrapRef = useRef<HTMLDivElement | null>(null);
  const [roiLoaded, setRoiLoaded] = useState(false);

  const [testState, setTestState] = useState<
    | { kind: "idle" }
    | { kind: "testing" }
    | { kind: "ok"; w: number; h: number; ms: number }
    | { kind: "err"; msg: string }
  >({ kind: "idle" });

  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveError, setSaveError] = useState<string>("");
  const [info, setInfo] = useState<string>("");

  const frameUrl = rtspFrameUrl(frameTs);

  useEffect(() => {
    let mounted = true;

    (async () => {
      try {
        const s = await getSettings();
        if (!mounted) return;

        const settings = s?.settings || s || {};
        const cam = settings.camera ?? {};
        setRtspUrl(String(cam.rtsp_url ?? ""));
        setEnabled(Boolean(cam.enabled ?? true));

        const roiStr = String(settings?.rtsp_worker?.overrides?.ROI_STR || "");
        const parsed = parseRoiStr(roiStr);
        if (parsed) {
          const [x1, y1, x2, y2] = parsed;
          // Пока не знаем размер контейнера/кадра, временно храним как px кадра в boxes.w/h.
          // Реально пересчитаем ниже, когда появятся размеры.
          setRoi({ x: x1, y: y1, w: x2 - x1, h: y2 - y1 });
        }
      } catch (e) {
        console.warn(e);
      } finally {
        if (mounted) setLoading(false);
      }
    })();

    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    let mounted = true;

    const tick = async () => {
      try {
        const [s, b] = await Promise.all([getRtspStatus(), rtspBoxes()]);
        if (!mounted) return;
        setStatus(s || null);
        setBoxes(b?.boxes || null);
      } catch (_e) {
        if (!mounted) return;
      }
      setFrameTs(Date.now());
    };

    tick();
    const t = window.setInterval(tick, 1000);
    return () => {
      mounted = false;
      window.clearInterval(t);
    };
  }, []);

  // Когда известны размеры живого кадра + контейнера, конвертируем ROI из frame px в UI px
  useEffect(() => {
    if (roiLoaded) return;
    const wrap = frameWrapRef.current;
    const fw = Number(boxes?.w || 0);
    const fh = Number(boxes?.h || 0);
    if (!wrap || fw <= 0 || fh <= 0 || !roi) return;

    const rw = wrap.clientWidth || 1;
    const rh = wrap.clientHeight || 1;

    // эвристика: если roi уже в UI-координатах, не пересчитываем повторно
    const looksLikeFramePx = roi.x <= fw && roi.y <= fh && roi.w <= fw && roi.h <= fh;
    if (looksLikeFramePx) {
      setRoi({
        x: (roi.x / fw) * rw,
        y: (roi.y / fh) * rh,
        w: (roi.w / fw) * rw,
        h: (roi.h / fh) * rh,
      });
    }
    setRoiLoaded(true);
  }, [boxes, roi, roiLoaded]);

  function onDown(e: React.MouseEvent<HTMLDivElement>) {
    const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
    const x = clamp(e.clientX - rect.left, 0, rect.width);
    const y = clamp(e.clientY - rect.top, 0, rect.height);
    dragStart.current = { x, y };
    setDrawing(true);
    setRoi({ x, y, w: 1, h: 1 });
  }

  function onMove(e: React.MouseEvent<HTMLDivElement>) {
    if (!drawing || !dragStart.current) return;
    const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
    const x = clamp(e.clientX - rect.left, 0, rect.width);
    const y = clamp(e.clientY - rect.top, 0, rect.height);
    const x0 = dragStart.current.x;
    const y0 = dragStart.current.y;
    const rx = Math.min(x0, x);
    const ry = Math.min(y0, y);
    const rw = Math.abs(x - x0);
    const rh = Math.abs(y - y0);
    setRoi({ x: rx, y: ry, w: Math.max(2, rw), h: Math.max(2, rh) });
  }

  function onUp() {
    setDrawing(false);
    dragStart.current = null;
  }

  async function onTest() {
    setSaveState("idle");
    setSaveError("");
    setInfo("");
    setTestState({ kind: "testing" });

    const url = rtspUrl.trim();
    if (!url) {
      setTestState({ kind: "err", msg: "Вставь RTSP ссылку" });
      return;
    }

    const res = await cameraTest(url, 6.0);
    if (res.ok) {
      setTestState({ kind: "ok", w: res.width, h: res.height, ms: res.grab_ms });
    } else {
      setTestState({ kind: "err", msg: `Не работает: ${res.error}` });
    }
  }

  async function onSave() {
    setSaveState("saving");
    setSaveError("");
    setInfo("");

    try {
      const wrap = frameWrapRef.current;
      const fw = Number(boxes?.w || 0);
      const fh = Number(boxes?.h || 0);

      let roiStr = String((await getSettings())?.settings?.rtsp_worker?.overrides?.ROI_STR || "");

      if (roi && wrap && fw > 0 && fh > 0) {
        const rw = wrap.clientWidth || 1;
        const rh = wrap.clientHeight || 1;
        const x1 = Math.max(0, Math.min(fw - 1, Math.round((roi.x / rw) * fw)));
        const y1 = Math.max(0, Math.min(fh - 1, Math.round((roi.y / rh) * fh)));
        const x2 = Math.max(x1 + 1, Math.min(fw, Math.round(((roi.x + roi.w) / rw) * fw)));
        const y2 = Math.max(y1 + 1, Math.min(fh, Math.round(((roi.y + roi.h) / rh) * fh)));
        roiStr = `${x1},${y1},${x2},${y2}`;
      }

      await putSettings({
        camera: { rtsp_url: rtspUrl.trim(), enabled },
        rtsp_worker: { overrides: { ROI_STR: roiStr } },
      });

      setSaveState("saved");
      setInfo(roiStr ? `Сохранено ✅ ROI: ${roiStr}` : "Сохранено ✅");
    } catch (e: any) {
      setSaveState("error");
      setSaveError(e?.message ?? "Ошибка сохранения");
    }
  }

  if (loading) return <div className="card"><div className="cardBody">Загрузка…</div></div>;

  return (
    <div className="grid2">
      <div className="card">
        <div className="cardHead">
          <div>
            <div className="cardTitle">Камера и ROI</div>
            <div className="cardSub">Одна точка настройки для операторов и тестировщиков</div>
          </div>
          <span className={`badge ${status?.alive ? "badge-green" : "badge-red"}`}>
            {status?.alive ? "Камера online" : "Нет связи"}
          </span>
        </div>

        <div className="cardBody">
          <div className="row">
            <label className="muted" style={{ width: 140 }}>RTSP URL</label>
            <input
              className="input mono"
              value={rtspUrl}
              onChange={(e) => setRtspUrl(e.target.value)}
              placeholder="rtsp://user:pass@192.168.1.10:554/stream"
            />
          </div>

          <div className="row">
            <label className="muted" style={{ width: 140 }}>Камера</label>
            <label className="checkbox">
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              <span>Включена</span>
            </label>
          </div>

          <div className="row" style={{ gap: 10, marginTop: 12 }}>
            <button className="btn" onClick={onTest} disabled={testState.kind === "testing"}>
              {testState.kind === "testing" ? "Проверяю…" : "Проверить RTSP"}
            </button>
            <button className="btn btn-primary" onClick={onSave} disabled={saveState === "saving"}>
              {saveState === "saving" ? "Сохраняю…" : "Сохранить"}
            </button>
            <button className="btn" onClick={() => setRoi(null)}>
              Сбросить ROI
            </button>
          </div>

          {testState.kind === "ok" && (
            <div className="alert" style={{ marginTop: 10 }}>
              RTSP ok: {testState.w}×{testState.h}, кадр за {testState.ms} мс
            </div>
          )}
          {testState.kind === "err" && <div className="alert alert-error" style={{ marginTop: 10 }}>{testState.msg}</div>}
          {saveError && <div className="alert alert-error" style={{ marginTop: 10 }}>{saveError}</div>}
          {info && <div className="alert" style={{ marginTop: 10 }}>{info}</div>}

          <div className="hint" style={{ marginTop: 10 }}>
            ROI рисуется мышкой по кадру и сохраняется в <span className="mono">settings.json → rtsp_worker.overrides.ROI_STR</span>.
            Применяется на лету без рестарта контейнера.
          </div>
        </div>
      </div>

      <div className="card">
        <div className="cardHead">
          <div className="cardTitle">Живой кадр и выбор ROI</div>
        </div>
        <div className="cardBody">
          <div
            ref={frameWrapRef}
            className="frameWrap"
            onMouseDown={onDown}
            onMouseMove={onMove}
            onMouseUp={onUp}
            onMouseLeave={onUp}
          >
            <img className="frameImg" src={frameUrl} alt="camera" />
            {roi && (
              <div
                className="roiBox"
                style={{ left: roi.x, top: roi.y, width: roi.w, height: roi.h }}
              />
            )}
          </div>

          <div className="row" style={{ marginTop: 10, justifyContent: "space-between" }}>
            <span className="muted mono">frame: {boxes?.w || "?"}×{boxes?.h || "?"}</span>
            <span className="muted mono">fps: {status?.fps != null ? Number(status.fps).toFixed(2) : "?"}</span>
            <span className="muted mono">age: {status?.age_ms ?? "?"}ms</span>
          </div>
        </div>
      </div>
    </div>
  );
}
