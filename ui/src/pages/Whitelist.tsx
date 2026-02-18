import React, { useEffect, useMemo, useState } from "react";
import { addWhitelistPlate, getSettings, getWhitelist, putSettings, putWhitelist, reloadWhitelist } from "../api";
import { useEventsStream } from "../hooks/useEventsStream";

type MetaItem = { direction?: "entry" | "exit" | "unknown"; location?: string };

function normPlate(x: string) {
  return String(x || "").trim().toUpperCase().replace(/[\s\-_]+/g, "");
}

function fmtTs(ts?: number) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}

export default function WhitelistPage() {
  const [plates, setPlates] = useState<string[]>([]);
  const [meta, setMeta] = useState<Record<string, MetaItem>>({});
  const [input, setInput] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const { items } = useEventsStream({ includeDebug: false, limit: 250 });

  const lastByPlate = useMemo(() => {
    const m: Record<string, any> = {};
    for (const it of items || []) {
      const p = normPlate(it?.plate || "");
      if (!p) continue;
      if (!m[p]) m[p] = it;
    }
    return m;
  }, [items]);

  async function loadAll() {
    try {
      const [wl, st] = await Promise.all([getWhitelist(), getSettings()]);
      const list = Array.isArray(wl?.plates) ? wl.plates.map((x) => normPlate(String(x))).filter(Boolean) : [];
      setPlates(Array.from(new Set(list)).sort());
      const settings = st?.settings || st || {};
      setMeta((settings.whitelist_meta && typeof settings.whitelist_meta === "object") ? settings.whitelist_meta : {});
      setErr(null);
    } catch (e: any) {
      setErr(e?.message || String(e));
    }
  }

  useEffect(() => {
    loadAll();
  }, []);

  async function onAdd() {
    const p = normPlate(input);
    if (!p) return;
    try {
      await addWhitelistPlate(p);
      setInput("");
      setInfo(`Добавлено: ${p}`);
      setTimeout(() => setInfo(null), 1400);
      await loadAll();
    } catch (e: any) {
      setErr(e?.message || String(e));
    }
  }

  async function onDelete(p: string) {
    const next = plates.filter((x) => x !== p);
    try {
      await putWhitelist(next);
      await reloadWhitelist();
      const nextMeta = { ...meta };
      delete nextMeta[p];
      setMeta(nextMeta);
      await putSettings({ whitelist_meta: nextMeta });
      setPlates(next);
    } catch (e: any) {
      setErr(e?.message || String(e));
    }
  }

  async function onSaveMeta() {
    setSaving(true);
    try {
      await putSettings({ whitelist_meta: meta });
      setInfo("Шаблоны сохранены ✅");
      setTimeout(() => setInfo(null), 1500);
      setErr(null);
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="col">
      <div className="card">
        <div className="cardHead">
          <div>
            <div className="cardTitle">Белый список автомобилей</div>
            <div className="cardSub">Добавляй/удаляй номера и заполняй шаблон направления (въезд/выезд)</div>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <button className="btn" onClick={loadAll}>Обновить</button>
            <button className="btn btn-primary" onClick={onSaveMeta} disabled={saving}>{saving ? "Сохраняю…" : "Сохранить шаблоны"}</button>
          </div>
        </div>

        <div className="cardBody">
          {err && <div className="alert alert-error mono">{err}</div>}
          {info && <div className="alert">{info}</div>}

          <div className="row" style={{ gap: 10 }}>
            <input
              className="input mono"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="У616НН761"
            />
            <button className="btn btn-primary" onClick={onAdd}>Добавить в белый список</button>
          </div>

          <div className="hint" style={{ marginTop: 10 }}>
            Поле «Направление» и «Шаблон локации» закладываются на будущее для сценария с двумя камерами.
          </div>

          <div style={{ marginTop: 12, overflowX: "auto" }}>
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: 170 }}>Номер</th>
                  <th style={{ width: 180 }}>Последнее определение</th>
                  <th style={{ width: 180 }}>Статус</th>
                  <th style={{ width: 150 }}>Направление</th>
                  <th>Шаблон локации</th>
                  <th style={{ width: 120 }}>Действия</th>
                </tr>
              </thead>
              <tbody>
                {plates.map((p) => {
                  const last = lastByPlate[p];
                  const m = meta[p] || {};
                  return (
                    <tr key={p}>
                      <td className="mono" style={{ fontWeight: 800 }}>{p}</td>
                      <td className="muted">{fmtTs(last?.ts)}</td>
                      <td className="mono">{last?.status || "—"}</td>
                      <td>
                        <select
                          className="input"
                          value={m.direction || "unknown"}
                          onChange={(e) => setMeta((prev) => ({ ...prev, [p]: { ...prev[p], direction: e.target.value as any } }))}
                        >
                          <option value="unknown">Не задано</option>
                          <option value="entry">Въезд</option>
                          <option value="exit">Выезд</option>
                        </select>
                      </td>
                      <td>
                        <input
                          className="input"
                          value={m.location || ""}
                          onChange={(e) => setMeta((prev) => ({ ...prev, [p]: { ...prev[p], location: e.target.value } }))}
                          placeholder="Шлагбаум 1 / Парковка / ..."
                        />
                      </td>
                      <td>
                        <button className="btn" onClick={() => onDelete(p)}>Удалить</button>
                      </td>
                    </tr>
                  );
                })}

                {!plates.length && (
                  <tr>
                    <td colSpan={6} className="muted" style={{ padding: 14 }}>
                      Белый список пуст. Добавь первый номер.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
