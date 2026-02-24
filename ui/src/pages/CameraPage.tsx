import React, { useEffect, useMemo, useState } from "react";
import { cameraTest, getRtspStatus, getSettings, putSettings, rtspBoxes, rtspFrameUrl } from "../api";

type Pt = { x: number; y: number };

type BoxItem = {
  x1?: number;
  y1?: number;
  x2?: number;
  y2?: number;
  conf?: number;
};

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

function mapPointsToPercent(pts: Pt[], fw: number, fh: number): Pt[] {
  if (fw <= 0 || fh <= 0) return [];
  return pts.map((p) => ({ x: (p.x / fw) * 100, y: (p.y / fh) * 100 }));
}

export default function CameraPage() {
  const [loading, setLoading] = useState(true);
  const [rtspUrl, setRtspUrl] = useState("");
  const [enabled, setEnabled] = useState(true);

  const [status, setStatus] = useState<any>(null);
  const [boxes, setBoxes] = useState<any>(null);
  const [frameTs, setFrameTs] = useState<number>(() => Date.now());

  const [roiPts, setRoiPts] = useState<Pt[]>([]);
  const [savedRoiPts, setSavedRoiPts] = useState<Pt[]>([]);
  const [showBbox, setShowBbox] = useState(true);

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
          setSavedRoiPts(poly);
        } else {
          const roiStr = String(settings?.rtsp_worker?.overrides?.ROI_STR || "");
          const rectPoly = parseRoiStrAsPoly(roiStr);
          if (rectPoly.length >= 3) {
            setRoiPts(rectPoly);
            setSavedRoiPts(rectPoly);
          }
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

  function getMousePtFrame(e: React.MouseEvent<HTMLDivElement>): Pt | null {
    const fw = Number(boxes?.w || 0);
    const fh = Number(boxes?.h || 0);
    if (fw <= 0 || fh <= 0) return null;
    const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
    const px = clamp(e.clientX - rect.left, 0, rect.width);
    const py = clamp(e.clientY - rect.top, 0, rect.height);
    return {
      x: clamp(Math.round((px / Math.max(1, rect.width)) * fw), 0, fw - 1),
      y: clamp(Math.round((py / Math.max(1, rect.height)) * fh), 0, fh - 1),
    };
  }

  function onCanvasClick(e: React.MouseEvent<HTMLDivElement>) {
    const p = getMousePtFrame(e);
    if (!p) {
      setInfo("Сначала дождись размеров кадра (frame w×h)");
      return;
    }
    setInfo("");
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
      const fw = Number(boxes?.w || 0);
      const fh = Number(boxes?.h || 0);

      let roiPolyStr = "";
      let roiStr = String((await getSettings())?.settings?.rtsp_worker?.overrides?.ROI_STR || "");

      if (roiPts.length >= 3 && fw > 0 && fh > 0) {
        const ptsFrame = roiPts.map((p) => ({
          x: Math.max(0, Math.min(fw - 1, Math.round(p.x))),
          y: Math.max(0, Math.min(fh - 1, Math.round(p.y))),
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

      setSavedRoiPts(roiPts);
      setSaveState("saved");
      setInfo(roiPolyStr ? `Сохранено ✅ Полигон ROI: ${roiPolyStr}` : "Сохранено ✅");
    } catch (e: any) {
      setSaveState("error");
      setSaveError(e?.message ?? "Ошибка сохранения");
    }
  }
  const fw = Number(boxes?.w || 0);
  const fh = Number(boxes?.h || 0);

  const polygonPoints = useMemo(() => mapPointsToPercent(roiPts, fw, fh).map((p) => `${p.x},${p.y}`).join(" "), [roiPts, fw, fh]);
  const savedPolygonPoints = useMemo(() => mapPointsToPercent(savedRoiPts, fw, fh).map((p) => `${p.x},${p.y}`).join(" "), [savedRoiPts, fw, fh]);
  const yoloRects = useMemo(() => {
    if (!showBbox || fw <= 0 || fh <= 0) return [];
    const items: BoxItem[] = Array.isArray(boxes?.items) ? boxes.items : [];
    return items
      .map((it, i) => {
        if (![it.x1, it.y1, it.x2, it.y2].every((v) => Number.isFinite(v))) return null;
        const x = ((Number(it.x1) || 0) / fw) * 100;
        const y = ((Number(it.y1) || 0) / fh) * 100;
        const w = (((Number(it.x2) || 0) - (Number(it.x1) || 0)) / fw) * 100;
        const h = (((Number(it.y2) || 0) - (Number(it.y1) || 0)) / fh) * 100;
        if (w <= 0 || h <= 0) return null;
        return { key: `box-${i}`, x, y, w, h };
      })
      .filter(Boolean) as Array<{ key: string; x: number; y: number; w: number; h: number }>;
  }, [showBbox, boxes, fw, fh]);

  if (loading) return <div className="card"><div className="cardBody">Загрузка…</div></div>;

  return (
    <div className="grid2">
      <div className="card">
        <div className="cardHead">
          <div>
            <div className="cardTitle">Камера и ROI</div>
            <div className="cardSub">Рисование ROI точками (треугольник, трапеция, произвольная зона)</div>
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

          <div className="row" style={{ gap: 10, marginTop: 12, flexWrap: "wrap" }}>
            <button className="btn" onClick={onTest} disabled={testState.kind === "testing"}>
              {testState.kind === "testing" ? "Проверяю…" : "Проверить RTSP"}
            </button>
            <button className="btn btn-primary" onClick={onSave} disabled={saveState === "saving"}>
              {saveState === "saving" ? "Сохраняю…" : "Сохранить"}
            </button>
            <button className="btn" onClick={onUndoPoint} disabled={roiPts.length === 0}>Удалить точку</button>
            <button className="btn" onClick={onClearRoi} disabled={roiPts.length === 0}>Сбросить ROI</button>
            <label className="checkbox" style={{ marginLeft: 4 }}>
              <input type="checkbox" checked={showBbox} onChange={(e) => setShowBbox(e.target.checked)} />
              <span>Показывать BBOX</span>
            </label>
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
            ROI хранится в координатах исходного кадра, поэтому при обновлении страницы остаётся на своём месте.
          </div>
          <div className="hint" style={{ marginTop: 6 }}>
            Минимум 3 точки. Полигон сохраняется в <span className="mono">ROI_POLY_STR</span>, bbox для совместимости — в <span className="mono">ROI_STR</span>.
          </div>
        </div>
      </div>

      <div className="col">
        <div className="card">
          <div className="cardHead">
            <div className="cardTitle">Живой кадр с ROI и BBOX</div>
          </div>
          <div className="cardBody">
            <div className="frameWrap" onClick={onCanvasClick} title="Клик — добавить точку ROI">
              <img className="frameImg" src={frameUrl} alt="camera" />
              <svg className="frameOverlay" viewBox="0 0 100 100" preserveAspectRatio="none">
                {yoloRects.map((r) => (
                  <rect key={r.key} className="yoloBox" x={r.x} y={r.y} width={r.w} height={r.h} />
                ))}
                {roiPts.length >= 3 && <polygon className="roiPoly" points={polygonPoints} />}
                {roiPts.length >= 2 && <polyline className="roiLine" points={polygonPoints} />}
                {mapPointsToPercent(roiPts, fw, fh).map((p, i) => (
                  <circle key={`pt-${i}`} className="roiPt" cx={p.x} cy={p.y} r={0.9} />
                ))}
              </svg>
            </div>

            <div className="row" style={{ marginTop: 10, justifyContent: "space-between" }}>
              <span className="muted mono">frame: {boxes?.w || "?"}×{boxes?.h || "?"}</span>
              <span className="muted mono">fps: {status?.fps != null ? Number(status.fps).toFixed(2) : "?"}</span>
              <span className="muted mono">age: {status?.age_ms ?? "?"}ms</span>
              <span className="muted mono">pts: {roiPts.length}</span>
              <span className="muted mono">bbox: {Array.isArray(boxes?.items) ? boxes.items.length : 0}</span>
            </div>
          </div>
        </div>

        <div className="card">
          <div className="cardHead">
            <div className="cardTitle">Отдельный вид: сохранённый ROI</div>
          </div>
          <div className="cardBody">
            <div className="frameWrap" title="Только сохранённый ROI">
              <img className="frameImg" src={frameUrl} alt="saved-roi" />
              <svg className="frameOverlay" viewBox="0 0 100 100" preserveAspectRatio="none">
                {savedRoiPts.length >= 3 && <polygon className="roiPoly" points={savedPolygonPoints} />}
                {savedRoiPts.length >= 2 && <polyline className="roiLine" points={savedPolygonPoints} />}
              </svg>
            </div>
            <div className="hint muted" style={{ marginTop: 8 }}>
              Здесь отображается именно последний сохранённый ROI. Если поменял точки — нажми «Сохранить».
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
