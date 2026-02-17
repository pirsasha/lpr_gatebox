import React, { useEffect, useMemo, useState } from "react";
import { addWhitelistPlate, getRecentPlates, getRtspStatus, rtspFrameUrl } from "../api";
import { useEventsStream } from "../hooks/useEventsStream";

type RecentPlateItem = {
  ts: number;
  plate: string;
  conf?: number;
  file: string;
  image_url: string;
};

type RtspStatus = {
  alive: boolean;
  age_ms?: number;
  frozen?: boolean;
  fps?: number;
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
  return new Date(ts * 1000).toLocaleString();
}

export default function DashboardPage() {
  const [rtsp, setRtsp] = useState<RtspStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<number>(0);
  const [showDebug, setShowDebug] = useState<boolean>(false);
  const [recent, setRecent] = useState<RecentPlateItem[]>([]);
  const [wlInfo, setWlInfo] = useState<string>("");

  const { items: events, connected: sseOnline, error: sseErr } = useEventsStream({ includeDebug: showDebug, limit: 40 });

  const [frameUrl, setFrameUrl] = useState<string>(rtspFrameUrl(Date.now()));
  const [frameOk, setFrameOk] = useState<boolean>(false);

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

  useEffect(() => {
    let mounted = true;
    const tick = async () => {
      try {
        const r = await getRecentPlates();
        if (!mounted) return;
        const items = Array.isArray(r?.items) ? r.items : [];
        setRecent(items.slice(0, 5));
      } catch {
        if (!mounted) return;
        setRecent([]);
      }
    };
    tick();
    const t = window.setInterval(tick, 1500);
    return () => {
      mounted = false;
      window.clearInterval(t);
    };
  }, []);

  useEffect(() => {
    const tick = () => setFrameUrl(rtspFrameUrl(Date.now()));
    tick();
    const t = window.setInterval(tick, 1000);
    return () => window.clearInterval(t);
  }, []);

  async function addFromDashboard(plate?: string) {
    const p = String(plate || "").trim();
    if (!p) return;
    try {
      const r = await addWhitelistPlate(p);
      if (r?.ok) {
        setWlInfo(`Добавлено: ${r.plate}`);
        setTimeout(() => setWlInfo(""), 1400);
      }
    } catch (e: any) {
      setWlInfo(`Ошибка: ${e?.message || e}`);
      setTimeout(() => setWlInfo(""), 1800);
    }
  }

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
            {wlInfo && <div className="alert mono">{wlInfo}</div>}

            <div className="row" style={{ justifyContent: "space-between" }}>
              <div className="muted">Обновлено: {lastUpdate ? new Date(lastUpdate).toLocaleTimeString() : "—"}</div>
              <div className="row" style={{ gap: 10 }}>
                <span className="muted">События:</span>
                {sseOnline ? <Badge tone="green">онлайн</Badge> : <Badge tone="red">нет связи</Badge>}
              </div>
            </div>

            <label className="row" style={{ gap: 8, marginTop: 8 }}>
              <input type="checkbox" checked={showDebug} onChange={(e) => setShowDebug(e.target.checked)} />
              <span>Показать диагностику</span>
            </label>

            <div className="frameWrap" style={{ marginTop: 8 }}>
              <img className="frameImg" src={frameUrl} alt="snapshot" onLoad={() => setFrameOk(true)} onError={() => setFrameOk(false)} />
            </div>

            {!frameOk && (
              <div className="hint muted">
                Кадр ещё не доступен. Проверь, что <span className="mono">rtsp_worker</span> пишет в <span className="mono">/config/live/frame.jpg</span>.
              </div>
            )}
          </div>
        </div>

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
                  raw: {last.raw || "—"} conf: {typeof last.conf === "number" ? last.conf.toFixed(4) : "—"}
                </div>
                <div className="row" style={{ marginTop: 10 }}>
                  <button className="btn" onClick={() => addFromDashboard(String(last.plate || ""))}>+ Добавить в белый список</button>
                </div>
              </>
            ) : (
              <div className="muted">Пока событий нет</div>
            )}
          </div>
        </div>
      </div>

      <div className="col">
        <div className="card">
          <div className="cardHead">
            <div className="cardTitle">Последние номера (кропы)</div>
            <div className="muted">Храним только 5 последних, старые удаляются автоматически</div>
          </div>
          <div className="cardBody">
            {!recent.length ? (
              <div className="muted">Пока нет распознанных номеров</div>
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(5, minmax(110px,1fr))", gap: 10 }}>
                {recent.map((it, idx) => (
                  <div key={`${it.file}-${idx}`} style={{ border: "1px solid rgba(255,255,255,.08)", borderRadius: 10, padding: 8 }}>
                    <img src={it.image_url} alt={it.plate || "plate"} style={{ width: "100%", aspectRatio: "3/1", objectFit: "cover", borderRadius: 8, background: "#111" }} />
                    <div className="mono" style={{ marginTop: 6, fontWeight: 700 }}>{it.plate || "—"}</div>
                    <div className="muted" style={{ fontSize: 12 }}>{it.conf != null ? `conf=${Number(it.conf).toFixed(3)}` : ""}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

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
                  <th style={{ width: 170 }}>В белый список</th>
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
                      <td className="mono" style={{ textAlign: "right" }}>{typeof it.conf === "number" ? it.conf.toFixed(4) : "—"}</td>
                      <td>
                        {it.plate ? (
                          <button className="btn" onClick={() => addFromDashboard(String(it.plate))}>+ Добавить</button>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
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
