// ui/src/pages/System.jsx
// LPR_GATEBOX UI
// Версия: v0.3.3-ru-i18n-system
// Обновлено: 2026-02-11
//
// Что сделано:
// - CHG: Полная русификация страницы "Система"
// - FIX: Ресурсы (CPU/RAM/DISK + docker stats) корректно отображаются по схеме /api/v1/system/metrics
//        host.cpu_pct, host.mem_*_mb, host.disk_*.{used_mb,total_mb}, containers[].raw_mem

import React, { useEffect, useMemo, useState } from "react";
import { apiGet, apiPost, apiDownload, mqttCheck, mqttTestPublish } from "../api";

function fmtMB(x) {
  if (x == null || Number.isNaN(x)) return "—";
  if (x > 1024) return `${(x / 1024).toFixed(1)} ГБ`;
  return `${Math.round(x)} МБ`;
}

function fmtPct(x) {
  if (x == null || Number.isNaN(x)) return "—";
  return `${Number(x).toFixed(1)}%`;
}

function fmtInt(x) {
  if (x == null || Number.isNaN(x)) return "—";
  return `${Math.round(x)}`;
}

function pickContainer(containers, name) {
  if (!Array.isArray(containers)) return null;
  return containers.find((c) => c?.name === name) || null;
}

function KeyVal({ k, v, mono }) {
  return (
    <div className="kv">
      <div className="kvK">{k}</div>
      <div className={`kvV ${mono ? "mono" : ""}`}>{v}</div>
    </div>
  );
}

export default function SystemPage() {
  const [health, setHealth] = useState(null);
  const [metrics, setMetrics] = useState(null);

  const [updStatus, setUpdStatus] = useState(null);
  const [updLog, setUpdLog] = useState([]);

  const [err, setErr] = useState("");

  const [mqttInfo, setMqttInfo] = useState("");
  const [mqttErr, setMqttErr] = useState("");
  const [mqttBusy, setMqttBusy] = useState(false);


  async function loadHealth() {
    const h = await apiGet("/api/v1/health");
    setHealth(h);
  }

  async function loadMetrics() {
    const m = await apiGet("/api/v1/system/metrics");
    setMetrics(m);
  }

  async function loadUpdaterStatus() {
    const s = await apiGet("/api/v1/update/status");
    setUpdStatus(s);
  }

  async function loadUpdaterLog() {
    const l = await apiGet("/api/v1/update/log");
    setUpdLog(Array.isArray(l?.log) ? l.log : []);
  }

  async function onMqttCheck() {
    try {
      setMqttErr("");
      setMqttInfo("");
      setMqttBusy(true);
      const r = await mqttCheck();
      if (r?.ok) setMqttInfo(`MQTT доступен: ${r.host}:${r.port}`);
      else setMqttErr(r?.error || "MQTT недоступен");
    } catch (e) {
      setMqttErr(String(e?.message || e));
    } finally {
      setMqttBusy(false);
    }
  }

  async function onMqttTestPublish() {
    try {
      setMqttErr("");
      setMqttInfo("");
      setMqttBusy(true);
      const topic = String(health?.mqtt?.topic || "gate/open");
      const r = await mqttTestPublish(topic, { kind: "ui_test", source: "system_page", ts: Date.now() / 1000 });
      if (r?.ok) setMqttInfo(`Тестовый топик отправлен: ${r.topic}`);
      else setMqttErr(r?.error || "Не удалось отправить тестовый топик");
    } catch (e) {
      setMqttErr(String(e?.message || e));
    } finally {
      setMqttBusy(false);
    }
  }


  async function onMqttCheck() {
    try {
      setMqttErr("");
      setMqttInfo("");
      setMqttBusy(true);
      const r = await mqttCheck();
      if (r?.ok) setMqttInfo(`MQTT доступен: ${r.host}:${r.port}`);
      else setMqttErr(r?.error || "MQTT недоступен");
    } catch (e) {
      setMqttErr(String(e?.message || e));
    } finally {
      setMqttBusy(false);
    }
  }

  async function onMqttTestPublish() {
    try {
      setMqttErr("");
      setMqttInfo("");
      setMqttBusy(true);
      const topic = String(tgSettings?.mqtt?.topic || health?.mqtt?.topic || "gate/open");
      const r = await mqttTestPublish(topic, { kind: "ui_test", source: "system_page", ts: Date.now() / 1000 });
      if (r?.ok) setMqttInfo(`Тестовый топик отправлен: ${r.topic}`);
      else setMqttErr(r?.error || "Не удалось отправить тестовый топик");
    } catch (e) {
      setMqttErr(String(e?.message || e));
    } finally {
      setMqttBusy(false);
    }
  }

  async function loadAll() {
    try {
      setErr("");
      await Promise.all([loadHealth(), loadMetrics(), loadUpdaterStatus()]);
      await loadUpdaterLog();
    } catch (e) {
      setErr(String(e?.message || e));
    }
  }

  useEffect(() => {
    loadAll();
    const t = setInterval(() => {
      loadAll();
    }, 5000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const host = metrics?.host || null;
  const containers = metrics?.containers || null;

  const cGatebox = useMemo(() => pickContainer(containers, "gatebox"), [containers]);
  const cWorker = useMemo(() => pickContainer(containers, "rtsp_worker"), [containers]);
  const cUpdater = useMemo(() => pickContainer(containers, "updater"), [containers]);

  const diskRoot = host?.disk_root || null;
  const diskProject = host?.disk_project || null;
  const diskConfig = host?.disk_config || null;

  async function onCheck() {
    try {
      setErr("");
      await apiPost("/api/v1/update/check", {});
      await loadUpdaterStatus();
      await loadUpdaterLog();
    } catch (e) {
      setErr(String(e?.message || e));
    }
  }

  async function onStart() {
    try {
      setErr("");
      await apiPost("/api/v1/update/start", {});
      await loadUpdaterStatus();
      await loadUpdaterLog();
    } catch (e) {
      setErr(String(e?.message || e));
    }
  }

  async function onReport() {
    try {
      setErr("");
      await apiDownload("/api/v1/update/report", "gatebox_report.zip");
    } catch (e) {
      setErr(String(e?.message || e));
    }
  }

  async function onRefreshLog() {
    try {
      setErr("");
      await loadUpdaterLog();
    } catch (e) {
      setErr(String(e?.message || e));
    }
  }

  const updRunning = !!updStatus?.running;
  const updStep = updStatus?.step || "—";
  const updLast = updStatus?.last_result || "—";

  return (
    <div className="col">
      {err ? (
        <div className="alert alert-error">
          <div style={{ fontWeight: 800, marginBottom: 6 }}>Ошибка</div>
          <div className="mono">{err}</div>
        </div>
      ) : null}

      <div className="grid2">
        {/* HEALTH */}
        <div className="card">
          <div className="cardHead">
            <div className="cardTitle">Состояние</div>
            <div className="row">
              <span className={`badge ${health?.ok ? "badge-green" : "badge-red"}`}>
                {health?.ok ? "ОК" : "ПЛОХО"}
              </span>
            </div>
          </div>
          <div className="cardBody">
            <div className="kvGrid">
              <KeyVal k="версия" v={health?.version || "—"} mono />
              <KeyVal k="git" v={health?.git || "—"} mono />
              <KeyVal k="сборка" v={health?.build_time || "—"} mono />
              <KeyVal k="аптайм" v={health?.uptime_sec != null ? `${health.uptime_sec}s` : "—"} mono />
              <KeyVal k="модель OCR" v={health?.model || "—"} mono />
              <KeyVal k="settings" v={health?.settings_path || "—"} mono />
            </div>

            <div className="lastBlock">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div>
                  <div className="muted">MQTT</div>
                  <div className="mono">
                    {health?.mqtt?.enabled ? "включено" : "выключено"}{" "}
                    {health?.mqtt?.host ? `@ ${health.mqtt.host}:${health.mqtt.port}` : ""}
                  </div>
                </div>
                <div className="badge badge-blue">
                  топик: <span className="mono">{health?.mqtt?.topic || "—"}</span>
                </div>
              </div>

              <div style={{ marginTop: 10 }}>
                <div className="muted">Последний номер</div>
                <div className="plateBig mono">{health?.last_plate || "—"}</div>
              </div>

              <div className="row" style={{ marginTop: 12, gap: 10, flexWrap: "wrap" }}>
                <button className="btn btn-ghost" type="button" onClick={onMqttCheck} disabled={mqttBusy}>
                  Проверить MQTT
                </button>
                <button className="btn btn-primary" type="button" onClick={onMqttTestPublish} disabled={mqttBusy}>
                  Отправить тестовый топик
                </button>
              </div>
              {mqttErr ? <div className="hint" style={{ marginTop: 8, color: "#ff8a8a" }}>{mqttErr}</div> : null}
              {mqttInfo ? <div className="hint" style={{ marginTop: 8 }}>{mqttInfo}</div> : null}
            </div>
          </div>
        </div>

        {/* UPDATER */}
        <div className="card">
          <div className="cardHead">
            <div className="cardTitle">Обновления</div>
            <div className="row">
              <button className="btn btn-ghost" type="button" onClick={loadAll}>
                Обновить
              </button>
            </div>
          </div>
          <div className="cardBody">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className={`badge ${updRunning ? "badge-yellow" : "badge-gray"}`}>
                {updRunning ? "В ПРОЦЕССЕ" : "ОЖИДАНИЕ"}
              </span>
              <span className="badge badge-gray">
                шаг: <span className="mono">{updStep}</span>
              </span>
              <span
                className={`badge ${
                  updLast === "ok" ? "badge-green" : updLast === "error" ? "badge-red" : "badge-gray"
                }`}
              >
                итог: <span className="mono">{updLast}</span>
              </span>
            </div>

            <div className="row" style={{ marginTop: 12, gap: 10 }}>
              <button className="btn btn-primary" type="button" onClick={onCheck} disabled={updRunning}>
                Проверить
              </button>
              <button className="btn btn-danger" type="button" onClick={onStart} disabled={updRunning}>
                Обновить сейчас
              </button>
              <button className="btn btn-ghost" type="button" onClick={onReport}>
                Скачать отчёт
              </button>
            </div>

            <div className="lastBlock" style={{ marginTop: 12 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div className="cardTitle" style={{ fontSize: 14 }}>
                  Логи updater (хвост)
                </div>
                <div className="row">
                  <button className="btn btn-ghost" type="button" onClick={onRefreshLog}>
                    Обновить логи
                  </button>
                </div>
              </div>

              <div
                className="mono"
                style={{
                  marginTop: 10,
                  whiteSpace: "pre-wrap",
                  maxHeight: 220,
                  overflow: "auto",
                  background: "rgba(0,0,0,.22)",
                  border: "1px solid rgba(255,255,255,.06)",
                  borderRadius: 12,
                  padding: 10,
                }}
              >
                {updLog && updLog.length ? updLog.slice(-120).join("\n") : "—"}
              </div>
            </div>

            <div className="hint">
              «Обновить сейчас» делает <span className="mono">docker compose pull</span> +{" "}
              <span className="mono">up -d</span>. Во время обновления UI может кратко моргнуть — это нормально.
            </div>
          </div>
        </div>
      </div>

      {/* RESOURCES */}
      <div className="card">
        <div className="cardHead">
          <div className="cardTitle">Ресурсы</div>
          <div className="row">
            <span className="badge badge-gray">хост</span>
            <span className="badge badge-yellow">rtsp_worker</span>
            <span className="badge badge-blue">gatebox</span>
            <span className="badge badge-gray">updater</span>
          </div>
        </div>

        <div className="cardBody">
          {!metrics ? (
            <div className="muted">Загрузка…</div>
          ) : (
            <div className="grid2">
              <div className="card" style={{ background: "rgba(255,255,255,.02)" }}>
                <div className="cardHead">
                  <div className="cardTitle">Хост</div>
                  <div className="badge badge-gray">load1: {host?.load1 != null ? host.load1 : "—"}</div>
                </div>
                <div className="cardBody">
                  <div className="kvGrid">
                    <KeyVal k="CPU" v={fmtPct(host?.cpu_pct)} mono />
                    <KeyVal k="RAM занято" v={fmtMB(host?.mem_used_mb)} mono />
                    <KeyVal k="RAM всего" v={fmtMB(host?.mem_total_mb)} mono />
                    <KeyVal k="RAM доступно" v={fmtMB(host?.mem_avail_mb)} mono />
                  </div>

                  <div className="lastBlock">
                    <div className="muted">Диск /</div>
                    <div className="mono">
                      {diskRoot?.used_mb != null && diskRoot?.total_mb != null
                        ? `${fmtInt(diskRoot.used_mb)} МБ / ${fmtInt(diskRoot.total_mb)} МБ`
                        : "—"}
                    </div>

                    <div style={{ marginTop: 10 }} className="muted">
                      Диск /project
                    </div>
                    <div className="mono">
                      {diskProject?.used_mb != null && diskProject?.total_mb != null
                        ? `${fmtInt(diskProject.used_mb)} МБ / ${fmtInt(diskProject.total_mb)} МБ`
                        : "—"}
                    </div>

                    <div style={{ marginTop: 10 }} className="muted">
                      Диск /config
                    </div>
                    <div className="mono">
                      {diskConfig?.used_mb != null && diskConfig?.total_mb != null
                        ? `${fmtInt(diskConfig.used_mb)} МБ / ${fmtInt(diskConfig.total_mb)} МБ`
                        : "—"}
                    </div>
                  </div>

                  <div className="footer">
                    <span className="badge badge-gray">
                      ts: <span className="mono">{host?.ts || "—"}</span>
                    </span>
                  </div>
                </div>
              </div>

              <div className="card" style={{ background: "rgba(255,255,255,.02)" }}>
                <div className="cardHead">
                  <div className="cardTitle">Контейнеры</div>
                  <div className="muted">docker stats</div>
                </div>
                <div className="cardBody">
                  <div className="table">
                    <div className="tr th">
                      <div>контейнер</div>
                      <div>CPU</div>
                      <div>память</div>
                    </div>

                    {Array.isArray(containers) && containers.length ? (
                      containers.map((c, idx) => (
                        <div className="tr" key={idx}>
                          <div className="mono">{c?.name || "—"}</div>
                          <div className="mono">{c?.cpu_pct != null ? fmtPct(c.cpu_pct) : "—"}</div>
                          <div className="mono">{c?.raw_mem || "—"}</div>
                        </div>
                      ))
                    ) : (
                      <div className="muted">Нет данных</div>
                    )}
                  </div>

                  <div className="hint" style={{ marginTop: 10 }}>
                    Если <span className="mono">rtsp_worker</span> стабильно &gt;100% CPU — это нормально (YOLO + декод).
                    Можно снижать <span className="mono">DET_FPS</span>/<span className="mono">READ_FPS</span> или уменьшать{" "}
                    <span className="mono">imgsz</span>.
                  </div>

                  <div className="footer">
                    <span className="badge badge-gray">
                      gatebox CPU: <span className="mono">{cGatebox?.cpu_pct != null ? fmtPct(cGatebox.cpu_pct) : "—"}</span>
                    </span>
                    <span className="badge badge-gray">
                      worker CPU: <span className="mono">{cWorker?.cpu_pct != null ? fmtPct(cWorker.cpu_pct) : "—"}</span>
                    </span>
                    <span className="badge badge-gray">
                      updater CPU: <span className="mono">{cUpdater?.cpu_pct != null ? fmtPct(cUpdater.cpu_pct) : "—"}</span>
                    </span>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
