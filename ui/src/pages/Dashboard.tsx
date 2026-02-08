// ui/src/pages/Dashboard.tsx
// LPR_GATEBOX UI Dashboard
// Версия: v0.2.4-fix3
// Обновлено: 2026-02-02
//
// Что изменено (по сравнению с v0.2.4-fix2):
// - Восстановлен "карточный" дизайн под ui/src/App.css (card/grid2/btn/badge/table).
// - Исправлены exports (default export), чтобы App.jsx не падал.
// - Кадр камеры берём с backend: /api/rtsp/frame.jpg?ts=... (обновление раз в 1 сек).
// - Если кадра ещё нет — показываем подсказку (а не "молчаливые" 404 в UI).
//
// Примечание: сами 404 в консоли будут пропадать, как только rtsp_worker начнёт писать /config/live/frame.jpg.
//            Для этого см. docker-compose fix3 (rtsp_worker должен монтировать ./config:/config).

import React, { useEffect, useMemo, useState } from "react";
import { getRtspStatus, rtspFrameUrl } from "../api";
import { useEventsStream } from "../hooks/useEventsStream";

type EventItem = {
  ts: number;
  plate: string;
  raw?: string;
  conf?: number;
  status?: string;
  message?: string;
};

type RtspStatus = {
  ok: boolean;
  alive: boolean;
  age_ms?: number;
  frozen?: boolean;
  note?: string;
  fps?: number;
  errors?: number;
  sent?: number;
  frame?: { w: number; h: number };
  roi?: [number, number, number, number];
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

function fmtTs(ts: number) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}

export default function DashboardPage() {
  const [rtsp, setRtsp] = useState<RtspStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<number>(0);
  const [showDebug, setShowDebug] = useState<boolean>(false);

  // события по SSE
  const { items: events, connected: sseOnline, error: sseErr } = useEventsStream({ includeDebug: showDebug, limit: 40 });

  // кадр: просто меняем ts в URL — браузер заново качает jpeg
  const [frameUrl, setFrameUrl] = useState<string>(rtspFrameUrl(Date.now()));
  const [frameOk, setFrameOk] = useState<boolean>(false);

  // rtsp status обновляем раз в 1 сек
  useEffect(() => {
    let mounted = true;
    const tick = async () => {
      try {
        const rs = await getRtspStatus();
        if (!mounted) return;
        setRtsp(rs || null);
        setLastUpdate(Date.now());
        setErr(null);
      } catch (e: any) {
        if (!mounted) return;
        setErr(e?.message || String(e));
      }
    };
    tick();
    const t = window.setInterval(tick, 1000);
    return () => {
      mounted = false;
      window.clearInterval(t);
    };
  }, []);

  // автообновление кадра раз в 1 сек (чуть смещаем, чтобы не совпадало с refresh)
  useEffect(() => {
    const tick = () => setFrameUrl(rtspFrameUrl(Date.now()));
    tick();
    const t = window.setInterval(tick, 1000);
    return () => window.clearInterval(t);
  }, []);

  const last = events?.[0];

  const rtspLine = useMemo(() => {
    if (!rtsp) return "Камера: нет данных";
    const parts: string[] = [];
    parts.push(rtsp.alive ? "работает" : "нет связи");
    if (typeof rtsp.fps === "number") parts.push(`fps=${rtsp.fps.toFixed(2)}`);
    if (typeof rtsp.age_ms === "number") parts.push(`задержка=${rtsp.age_ms}мс`);
    if (rtsp.frozen) parts.push("кадр завис");
    return `Камера: ${parts.join(" · ")}`;
  }, [rtsp]);

  const statusBadge = useMemo(() => {
    if (!rtsp) return <Badge tone="gray">нет</Badge>;
    if (!rtsp.alive) return <Badge tone="red">нет связи</Badge>;
    if (rtsp.frozen) return <Badge tone="yellow">завис</Badge>;
    return <Badge tone="green">работает</Badge>;
  }, [rtsp]);



  return (
    <div className="grid2">
      {/* LEFT */}
      <div className="col">
        <div className="card">
          <div className="cardHead">
            <div className="cardTitle">Панель</div>
            <div className="row">
              {statusBadge}
              <span className="muted">{rtspLine}</span>
            </div>
          </div>

          <div className="cardBody">
            {(err || sseErr) && <div className="alert alert-error mono">{err || sseErr}</div>}

            <div className="row" style={{ justifyContent: "space-between" }}>
              <div className="muted">Обновлено: {lastUpdate ? new Date(lastUpdate).toLocaleTimeString() : "—"}</div>

              <div className="row" style={{ justifyContent: "space-between", width: "100%" }}>
                <div className="row" style={{ gap: 10 }}>
                  <span className="muted">События:</span>
                  {sseOnline ? <Badge tone="green">онлайн</Badge> : <Badge tone="red">нет связи</Badge>}
                </div>

                <label className="row" style={{ gap: 8 }}>
                  <input type="checkbox" checked={showDebug} onChange={(e) => setShowDebug(e.target.checked)} />
                  <span>Показать диагностику</span>
                </label>
              </div>
            </div>

            {/* Snapshot */}
            <div className="frameWrap">
              <img
                className="frameImg"
                src={frameUrl}
                alt="snapshot"
                onLoad={() => setFrameOk(true)}
                onError={() => setFrameOk(false)}
              />
            </div>

            {!frameOk && (
              <div className="hint muted">
                Кадр ещё не доступен. Проверь, что <span className="mono">rtsp_worker</span> пишет в{" "}
                <span className="mono">/config/live/frame.jpg</span> (в docker-compose fix3 добавлен mount{" "}
                <span className="mono">./config:/config</span>).
              </div>
            )}

            <div className="hint muted">
              Подсказка: детектор может не увидеть номер в каждом кадре — это нормально. Основной контроль смотри во вкладке <span className="mono">События</span>.
            </div>
          </div>
        </div>

        {/* Last plate */}
        <div className="card lastBlock">
          <div className="cardHead">
            <div className="cardTitle">Последний номер</div>
          </div>
          <div className="cardBody">
            {last ? (
              <>
                <div className="plateBig mono">{last.plate || "—"}</div>
                <div className="muted">{fmtTs(last.ts)}</div>
                <div className="muted mono" style={{ marginTop: 8 }}>
                  raw: {last.raw || "—"} conf: {(last.conf ?? 0).toFixed ? (last.conf as number).toFixed(4) : last.conf}
                </div>
              </>
            ) : (
              <div className="muted">Пока событий нет</div>
            )}
          </div>
        </div>
      </div>

      {/* RIGHT */}
      <div className="col">
        <div className="card">
          <div className="cardHead">
            <div className="cardTitle">Последние события — {events?.length || 0}</div>
          </div>

          <div className="cardBody" style={{ padding: 0 }}>
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: 160 }}>Время</th>
                  <th style={{ width: 140 }}>Номер</th>
                  <th style={{ width: 90 }}>Статус</th>
                  <th>Сообщение</th>
                  <th style={{ width: 80, textAlign: "right" }}>Conf</th>
                </tr>
              </thead>
              <tbody>
                {(events || []).map((it, idx) => {
                  const tone =
                    it.status === "sent" ? "green" :
                    it.status === "denied" ? "red" :
                    it.status === "invalid" ? "gray" :
                    it.status === "cooldown" ? "blue" :
                    "gray";
                  return (
                    <tr key={idx}>
                      <td className="muted">{fmtTs(it.ts)}</td>
                      <td className="mono" style={{ fontWeight: 800 }}>{it.plate}</td>
                      <td><Badge tone={tone as any}>{it.status || "—"}</Badge></td>
                      <td className="muted mono">{it.message || ""}</td>
                      <td className="mono" style={{ textAlign: "right" }}>{(it.conf ?? 0).toFixed ? (it.conf as number).toFixed(4) : it.conf}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
