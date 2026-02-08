// ui/src/pages/Events.tsx
// Журнал событий (SSE)

import React, { useMemo, useState } from "react";
import { useEventsStream } from "../hooks/useEventsStream";

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

function statusToTone(status?: string) {
  if (status === "sent") return "green";
  if (status === "cooldown") return "blue";
  if (status === "denied") return "red";
  if (status === "invalid") return "gray";
  if (status === "info") return "gray";
  return "gray";
}

function statusRu(status?: string) {
  if (status === "sent") return "Отправлено";
  if (status === "cooldown") return "Пауза";
  if (status === "denied") return "Не в списке";
  if (status === "invalid") return "Не номер";
  if (status === "info") return "Инфо";
  return status || "—";
}

export default function EventsPage() {
  const [showDebug, setShowDebug] = useState(false);
  const { items, connected, error } = useEventsStream({ includeDebug: showDebug, limit: 150 });

  const head = useMemo(() => {
    return (
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <div className="row" style={{ gap: 10 }}>
          <div className="cardTitle">Журнал событий</div>
          <span className="muted">{connected ? "онлайн" : "нет связи"}</span>
        </div>

        <div className="row" style={{ gap: 12 }}>
          <label className="row" style={{ gap: 8 }}>
            <input type="checkbox" checked={showDebug} onChange={(e) => setShowDebug(e.target.checked)} />
            <span>Показать диагностику</span>
          </label>
        </div>
      </div>
    );
  }, [connected, showDebug]);

  return (
    <div className="col">
      <div className="card">
        <div className="cardHead">{head}</div>
        <div className="cardBody" style={{ padding: 0 }}>
          {error && <div className="alert alert-error mono" style={{ margin: 14 }}>{error}</div>}

          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 190 }}>Время</th>
                <th style={{ width: 150 }}>Номер</th>
                <th style={{ width: 130 }}>Статус</th>
                <th>Сообщение</th>
                <th style={{ width: 90, textAlign: "right" }}>Точность</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it, idx) => (
                <tr key={`${it.ts}-${idx}`}>
                  <td className="muted">{fmtTs(it.ts)}</td>
                  <td className="mono" style={{ fontWeight: 900 }}>{it.plate || "—"}</td>
                  <td><Badge tone={statusToTone(it.status) as any}>{statusRu(it.status)}</Badge></td>
                  <td className="muted mono">{it.message || ""}</td>
                  <td className="mono" style={{ textAlign: "right" }}>
                    {typeof it.conf === "number" ? it.conf.toFixed(4) : "—"}
                  </td>
                </tr>
              ))}

              {!items.length && (
                <tr>
                  <td colSpan={5} className="muted" style={{ padding: 14 }}>
                    Пока нет событий. Если камера смотрит на дорогу, то события появятся сами.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
