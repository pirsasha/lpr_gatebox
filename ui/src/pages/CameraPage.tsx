import React, { useEffect, useRef, useState } from "react";
import { cameraTest, getRtspStatus, getSettings, putSettings, rtspBoxes, rtspFrameUrl } from "../api";

type Pt = { x: number; y: number };

function clamp(n: number, a: number, b: number) {
  return Math.max(a, Math.min(b, n));
}

function parseRoiPolyStr(roiPolyStr: string): Pt[] {
  const pts: Pt[] = [];
  for (const raw of String(roiPolyStr || "").split(";")) {
    const p = raw.trim();
    if (!p) continue;
    const xy = p.split(",").map((x) => Number(x.trim()));
    if (xy.length !== 2 || xy.some((v) => !Number.isFinite(v))) continue;
    pts.push({ x: xy[0], y: xy[1] });
  }
  return pts;
}

function parseRoiStrAsPoly(roiStr: string): Pt[] {
  const parts = String(roiStr || "").split(",").map((x) => Number(x.trim()));
  if (parts.length !== 4 || parts.some((x) => !Number.isFinite(x))) return [];
  const [x1, y1, x2, y2] = parts;
  if (x2 <= x1 || y2 <= y1) return [];
  return [
    { x: x1, y: y1 },
    { x: x2, y: y1 },
    { x: x2, y: y2 },
    { x: x1, y: y2 },
  ];
}

function pointsToRoiPolyStr(pts: Pt[]): string {
  return pts.map((p) => `${Math.round(p.x)},${Math.round(p.y)}`).join(";");
}

export default function CameraPage() {
  const [loading, setLoading] = useState(true);
  const [rtspUrl, setRtspUrl] = useState("");
  const [enabled, setEnabled] = useState(true);

  const [status, setStatus] = useState<any>(null);
  const [boxes, setBoxes] = useState<any>(null);
  const [frameTs, setFrameTs] = useState<number>(() => Date.now());

  const frameWrapRef = useRef<HTMLDivElement | null>(null);
  const [roiPts, setRoiPts] = useState<Pt[]>([]);
  const [roiLoaded, setRoiLoaded] = useState(false);
  const [overlaySize, setOverlaySize] = useState({ w: 1, h: 1 });

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

        const roiPolyStr = String(settings?.rtsp_worker?.overrides?.ROI_POLY_STR || "");
        const poly = parseRoiPolyStr(roiPolyStr);
        if (poly.length >= 3) {
          setRoiPts(poly);
        } else {
          const roiStr = String(settings?.rtsp_worker?.overrides?.ROI_STR || "");
          const rectPoly = parseRoiStrAsPoly(roiStr);
          if (rectPoly.length >= 3) setRoiPts(rectPoly);
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

  // convert frame-px points into UI points once we know frame/render sizes
  useEffect(() => {
    if (roiLoaded) return;
    const wrap = frameWrapRef.current;
    const fw = Number(boxes?.w || 0);
    const fh = Number(boxes?.h || 0);
    if (!wrap || fw <= 0 || fh <= 0 || roiPts.length < 3) return;

    const rw = wrap.clientWidth || 1;
    const rh = wrap.clientHeight || 1;

    const looksLikeFramePx = roiPts.every((p) => p.x <= fw && p.y <= fh);
    if (looksLikeFramePx) {
      setRoiPts(roiPts.map((p) => ({ x: (p.x / fw) * rw, y: (p.y / fh) * rh })));
    }
    setRoiLoaded(true);
  }, [boxes, roiPts, roiLoaded]);


  useEffect(() => {
    const upd = () => {
      const w = Math.max(1, frameWrapRef.current?.clientWidth || 1);
      const h = Math.max(1, frameWrapRef.current?.clientHeight || 1);
      setOverlaySize({ w, h });
    };
    upd();
    window.addEventListener("resize", upd);
    const t = window.setInterval(upd, 1000);
    return () => {
      window.removeEventListener("resize", upd);
      window.clearInterval(t);
    };
  }, []);

  function getMousePt(e: React.MouseEvent<HTMLDivElement>): Pt {
    const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
    return {
      x: clamp(e.clientX - rect.left, 0, rect.width),
      y: clamp(e.clientY - rect.top, 0, rect.height),
    };
  }

  function onCanvasClick(e: React.MouseEvent<HTMLDivElement>) {
    const p = getMousePt(e);
    setRoiPts((prev) => [...prev, p]);
  }

  function onUndoPoint() {
    setRoiPts((prev) => prev.slice(0, -1));
  }

  function onClearRoi() {
    setRoiPts([]);
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

      let roiPolyStr = "";
      let roiStr = String((await getSettings())?.settings?.rtsp_worker?.overrides?.ROI_STR || "");

      if (roiPts.length >= 3 && wrap && fw > 0 && fh > 0) {
        const rw = wrap.clientWidth || 1;
        const rh = wrap.clientHeight || 1;

        const ptsFrame = roiPts.map((p) => ({
          x: Math.max(0, Math.min(fw - 1, Math.round((p.x / rw) * fw))),
          y: Math.max(0, Math.min(fh - 1, Math.round((p.y / rh) * fh))),
        }));

        roiPolyStr = pointsToRoiPolyStr(ptsFrame);

        const xs = ptsFrame.map((p) => p.x);
        const ys = ptsFrame.map((p) => p.y);
        const x1 = Math.max(0, Math.min(...xs));
        const y1 = Math.max(0, Math.min(...ys));
        const x2 = Math.min(fw, Math.max(...xs) + 1);
        const y2 = Math.min(fh, Math.max(...ys) + 1);
        roiStr = `${x1},${y1},${x2},${y2}`;
      }

      await putSettings({
        camera: { rtsp_url: rtspUrl.trim(), enabled },
        rtsp_worker: { overrides: { ROI_STR: roiStr, ROI_POLY_STR: roiPolyStr } },
      });

      setSaveState("saved");
      setInfo(roiPolyStr ? `Сохранено ✅ Полигон ROI: ${roiPolyStr}` : "Сохранено ✅");
    } catch (e: any) {
      setSaveState("error");
      setSaveError(e?.message ?? "Ошибка сохранения");
    }
  }

  if (loading) return <div className="card"><div className="cardBody">Загрузка…</div></div>;

  const polygonPoints = roiPts.map((p) => `${p.x},${p.y}`).join(" ");

  return (
    <div className="grid2">
      <div className="card">
        <div className="cardHead">
          <div>
            <div className="cardTitle">Камера и ROI</div>
            <div className="cardSub">Рисование ROI точками (полигон: треугольник, трапеция, любая зона)</div>
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
            <button className="btn" onClick={onUndoPoint} disabled={roiPts.length === 0}>Удалить точку</button>
            <button className="btn" onClick={onClearRoi} disabled={roiPts.length === 0}>Сбросить ROI</button>
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
            Нажимай по кадру, чтобы добавить точки ROI. Минимум 3 точки. Полигон сохраняется в
            <span className="mono"> settings.json → rtsp_worker.overrides.ROI_POLY_STR</span>,
            а bbox для совместимости — в <span className="mono">ROI_STR</span>.
          </div>
        </div>
      </div>

      <div className="card">
        <div className="cardHead">
          <div className="cardTitle">Живой кадр и полигон ROI</div>
        </div>
        <div className="cardBody">
          <div
            ref={frameWrapRef}
            className="frameWrap"
            onClick={onCanvasClick}
            title="Клик — добавить точку ROI"
          >
            <img className="frameImg" src={frameUrl} alt="camera" />
            <svg className="frameOverlay" viewBox={`0 0 ${overlaySize.w} ${overlaySize.h}`} preserveAspectRatio="none">
              {roiPts.length >= 3 && <polygon className="roiPoly" points={polygonPoints} />}
              {roiPts.length >= 2 && <polyline className="roiLine" points={polygonPoints} />}
              {roiPts.map((p, i) => (
                <circle key={`pt-${i}`} className="roiPt" cx={p.x} cy={p.y} r={4} />
              ))}
            </svg>
          </div>

          <div className="row" style={{ marginTop: 10, justifyContent: "space-between" }}>
            <span className="muted mono">frame: {boxes?.w || "?"}×{boxes?.h || "?"}</span>
            <span className="muted mono">fps: {status?.fps != null ? Number(status.fps).toFixed(2) : "?"}</span>
            <span className="muted mono">age: {status?.age_ms ?? "?"}ms</span>
            <span className="muted mono">pts: {roiPts.length}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
