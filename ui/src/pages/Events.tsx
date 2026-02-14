// =========================================================
// FILE: ui/src/pages/Events.tsx
// PROJECT: LPR GateBox UI
// UPDATED: 2026-02-11 (UTC+3)
//
// WHAT:
// - Журнал событий (SSE)
// - Цветные бейджи (зелёный/красный/жёлтый/синий/серый)
// - Русские статусы + русские причины (message)
// =========================================================

import React, { useMemo, useState } from "react";
import { useEventsStream } from "../hooks/useEventsStream";

type Tone = "green" | "red" | "blue" | "gray" | "yellow";

function Badge({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  const cls =
    tone === "green" ? "badge badge-green" :
    tone === "red" ? "badge badge-red" :
    tone === "blue" ? "badge badge-blue" :
    tone === "yellow" ? "badge badge-yellow" :
    "badge badge-gray";
  return <span className={cls}>{children}</span>;
}

function fmtTs(ts: number) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}

// status из backend: sent / denied / invalid / cooldown / info (и т.п.)
function statusToTone(status?: string, message?: string): Tone {
  const s = (status || "").toLowerCase();
  const m = (message || "").toLowerCase();

  if (s === "sent") return "green";

  // denied — красный, но если причина "low_conf" — лучше жёлтый (не ошибка, а качество)
  if (s === "denied") {
    if (m.includes("low_conf")) return "yellow";
    return "red";
  }

  // invalid — серый, но "noise_ocr" тоже лучше серым
  if (s === "invalid") return "gray";

  if (s === "cooldown") return "blue";
  if (s === "info") return "gray";

  // fallback: если message явно про low_conf
  if (m.includes("low_conf")) return "yellow";

  return "gray";
}

function statusRu(status?: string) {
  const s = (status || "").toLowerCase();
  if (s === "sent") return "ОК";
  if (s === "cooldown") return "ПАУЗА";
  if (s === "denied") return "ОТКАЗ";
  if (s === "invalid") return "НЕ НОМЕР";
  if (s === "info") return "ИНФО";
  return (status || "—").toUpperCase();
}

function messageRu(message?: string) {
  const m = (message || "").toLowerCase();

  // из gatebox:
  if (m === "not_in_whitelist") return "Нет в белом списке";
  if (m === "invalid_format_or_region") return "Неверный формат/регион";
  if (m === "low_conf") return "Низкая уверенность";
  if (m === "ocr_failed") return "OCR не смог распознать";
  if (m === "noise_ocr") return "Мусор OCR (отфильтровано)";

  // иногда reason приходит сложнее: "http_error: ..."
  if (m.startsWith("http_error")) return "Ошибка связи с gatebox";

  // если уже человекочитаемо — оставляем как есть
  return message || "";
}

export default function EventsPage() {
  const [showDebug, setShowDebug] = useState(false);

  // limit можно держать 150-300
  const { items, connected, error } = useEventsStream({ includeDebug: showDebug, limit: 150 });

  const head = useMemo(() => {
    return (
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <div className="row" style={{ gap: 10 }}>
          <div className="cardTitle">Последние события</div>
          <span className="muted">{connected ? "онлайн" : "нет связи"}</span>
        </div>

        <div className="row" style={{ gap: 12 }}>
          <label className="row" style={{ gap: 8 }}>
            <input
              type="checkbox"
              checked={showDebug}
              onChange={(e) => setShowDebug(e.target.checked)}
            />
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
          {error && (
            <div className="alert alert-error mono" style={{ margin: 14 }}>
              {error}
            </div>
          )}

          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 190 }}>Время</th>
                <th style={{ width: 150 }}>Номер</th>
                <th style={{ width: 130 }}>Статус</th>
                <th>Сообщение</th>
                <th style={{ width: 90, textAlign: "right" }}>Conf</th>
              </tr>
            </thead>

            <tbody>
              {items.map((it, idx) => {
                const tone = statusToTone(it.status, it.message);
                return (
                  <tr key={`${it.ts}-${idx}`}>
                    <td className="muted">{fmtTs(it.ts)}</td>
                    <td className="mono" style={{ fontWeight: 900 }}>
                      {it.plate || "—"}
                    </td>
                    <td>
                      <Badge tone={tone}>
                        {statusRu(it.status)}
                      </Badge>
                    </td>
                    <td className="muted mono">{messageRu(it.message)}</td>
                    <td className="mono" style={{ textAlign: "right" }}>
                      {typeof it.conf === "number" ? it.conf.toFixed(4) : "—"}
                    </td>
                  </tr>
                );
              })}

              {!items.length && (
                <tr>
                  <td colSpan={5} className="muted" style={{ padding: 14 }}>
                    Пока нет событий. Если камера смотрит на дорогу — события появятся сами.
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