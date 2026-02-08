// ui/src/pages/Camera.tsx
// Превью камеры + рамки номера

import React, { useEffect, useMemo, useRef, useState } from "react";
import { rtspBoxes, rtspFrameUrl, getRtspStatus } from "../api";

type BoxItem = {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  conf?: number;
  cls?: string;
};

type BoxesPayload = {
  ts: number;
  w?: number;
  h?: number;
  roi?: [number, number, number, number];
  items: BoxItem[];
};

function Badge({ tone, children }: { tone: "green" | "red" | "blue" | "gray" | "yellow"; children: React.ReactNode }) {
  const cls =
    tone === "green" ? "badge badge-green" :
    tone === "red" ? "badge badge-red" :
    tone === "blue" ? "badge badge-blue" :
    tone === "yellow" ? "badge badge-yellow" :
    "badge";
  return <span className={cls}>{children}</span>;
}

export default function CameraPage() {
  const [frameSrc, setFrameSrc] = useState<string>(rtspFrameUrl(Date.now()));
  const [frameOk, setFrameOk] = useState<boolean>(false);
  const [boxes, setBoxes] = useState<BoxesPayload | null>(null);
  const [alive, setAlive] = useState<boolean>(false);
  const [err, setErr] = useState<string | null>(null);

  const imgRef = useRef<HTMLImageElement | null>(null);

  // polling кадра и боксов (это нормально и надёжно)
  useEffect(() => {
    let mounted = true;

    const tick = async () => {
      try {
        const [b, s] = await Promise.all([rtspBoxes(), getRtspStatus()]);
        if (!mounted) return;
        setBoxes(b?.boxes || null);
        setAlive(!!s?.alive);
        setErr(null);
      } catch (e: any) {
        if (!mounted) return;
        setErr(e?.message || String(e));
      }

      // кадр обновляем всегда (даже если боксов нет)
      if (mounted) setFrameSrc(rtspFrameUrl(Date.now()));
    };

    tick();
    const t = window.setInterval(tick, 700);
    return () => {
      mounted = false;
      window.clearInterval(t);
    };
  }, []);

  const overlay = useMemo(() => {
    if (!boxes || !boxes.w || !boxes.h) return null;
    const w = boxes.w;
    const h = boxes.h;

    const rects = (boxes.items || []).map((it, i) => {
      const x = (it.x1 / w) * 100;
      const y = (it.y1 / h) * 100;
      const rw = ((it.x2 - it.x1) / w) * 100;
      const rh = ((it.y2 - it.y1) / h) * 100;
      return <rect key={i} x={x} y={y} width={rw} height={rh} className="yoloBox" />;
    });

    return (
      <svg className="frameOverlay" viewBox="0 0 100 100" preserveAspectRatio="none">
        {rects}
      </svg>
    );
  }, [boxes]);

  return (
    <div className="col">
      <div className="card">
        <div className="cardHead">
          <div className="row" style={{ justifyContent: "space-between", width: "100%" }}>
            <div className="row" style={{ gap: 10 }}>
              <div className="cardTitle">Камера</div>
              {alive ? <Badge tone="green">работает</Badge> : <Badge tone="red">нет связи</Badge>}
              <span className="muted">
                {boxes?.w && boxes?.h ? `Кадр: ${boxes.w}×${boxes.h}` : ""}
              </span>
            </div>
            <div className="muted">Рамки номера: {boxes?.items?.length || 0}</div>
          </div>
        </div>

        <div className="cardBody">
          {err && <div className="alert alert-error mono">{err}</div>}

          <div className="frameWrap">
            <img
              ref={imgRef}
              className="frameImg"
              src={frameSrc}
              alt="camera"
              onLoad={() => setFrameOk(true)}
              onError={() => setFrameOk(false)}
            />
            {overlay}
          </div>

          {!frameOk && (
            <div className="hint muted">
              Кадр ещё не доступен. Проверь, что <span className="mono">rtsp_worker</span> пишет <span className="mono">/config/live/frame.jpg</span>.
            </div>
          )}

          <div className="hint muted">
            Это превью обновляется автоматически. Рамки появляются, когда детектор видит номер.
          </div>
        </div>
      </div>
    </div>
  );
}
