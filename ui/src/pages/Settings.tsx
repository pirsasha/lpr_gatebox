// ui/src/pages/Settings.tsx
// LPR_GATEBOX UI Settings
// Версия: v0.2.4-fix3
// Обновлено: 2026-02-02
//
// Что изменено:
// - Исправлены exports (default export), чтобы App.jsx не падал.
// - Привёл разметку под ui/src/App.css (card/row/btn/alert/mono).
// - Настройки реально пишем в /api/settings (PUT) и применяем через /api/settings/apply (POST).

import React, { useEffect, useMemo, useState } from "react";
import { getSettings, putSettings, applySettings } from "../api";

type Settings = any;

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [dirty, setDirty] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const load = async () => {
    try {
      const r = await getSettings();
      setSettings(r.settings);
      setDirty(false);
      setErr(null);
      setInfo(null);
    } catch (e: any) {
      setErr(e?.message || String(e));
    }
  };

  useEffect(() => { load(); }, []);

  const patch = (path: string, value: any) => {
    setSettings((prev: any) => {
      const next = JSON.parse(JSON.stringify(prev || {}));
      const parts = path.split(".");
      let cur = next;
      for (let i = 0; i < parts.length - 1; i++) {
        const k = parts[i];
        if (cur[k] == null || typeof cur[k] !== "object") cur[k] = {};
        cur = cur[k];
      }
      cur[parts[parts.length - 1]] = value;
      return next;
    });
    setDirty(true);
  };

  const onSave = async () => {
    try {
      const r = await putSettings(settings);
      setSettings(r.settings);
      setDirty(false);
      setErr(null);
      setInfo("Сохранено в settings.json");
    } catch (e: any) {
      setErr(e?.message || String(e));
    }
  };

  const onApply = async () => {
    try {
      const r = await applySettings();
      setErr(null);
      setInfo("Применено (gate + mqtt)");
      return r;
    } catch (e: any) {
      setErr(e?.message || String(e));
    }
  };

  if (!settings) {
    return (
      <div className="card">
        <div className="cardHead"><div className="cardTitle">Настройки</div></div>
        <div className="cardBody muted">Загрузка…</div>
      </div>
    );
  }

  return (
    <div className="col">
      <div className="card">
        <div className="cardHead">
          <div className="cardTitle">Настройки</div>
          <div className="row">
            <button className="btn btn-ghost" type="button" onClick={load}>Обновить</button>
            <button className="btn btn-primary" type="button" onClick={onSave} disabled={!dirty}>Сохранить</button>
            <button className="btn btn-primary" type="button" onClick={onApply}>Применить</button>
          </div>
        </div>

        <div className="cardBody">
          {err && <div className="alert alert-error mono">{err}</div>}
          {info && <div className="alert mono">{info}</div>}

          <div className="grid2">
            <div className="card">
              <div className="cardHead"><div className="cardTitle">MQTT</div></div>
              <div className="cardBody">
                <div className="row">
                  <label className="muted" style={{ width: 140 }}>Enabled</label>
                  <input type="checkbox" checked={!!settings?.mqtt?.enabled} onChange={(e)=>patch("mqtt.enabled", e.target.checked)} />
                </div>

                <div className="row">
                  <label className="muted" style={{ width: 140 }}>Host</label>
                  <input className="input mono" value={settings?.mqtt?.host || ""} onChange={(e)=>patch("mqtt.host", e.target.value)} />
                </div>

                <div className="row">
                  <label className="muted" style={{ width: 140 }}>Port</label>
                  <input className="input mono" value={String(settings?.mqtt?.port ?? "")} onChange={(e)=>patch("mqtt.port", Number(e.target.value || 0))} />
                </div>

                <div className="row">
                  <label className="muted" style={{ width: 140 }}>User</label>
                  <input className="input mono" value={settings?.mqtt?.user || ""} onChange={(e)=>patch("mqtt.user", e.target.value)} />
                </div>

                <div className="row">
                  <label className="muted" style={{ width: 140 }}>Pass</label>
                  <input className="input mono" type="password" value={settings?.mqtt?.pass || ""} onChange={(e)=>patch("mqtt.pass", e.target.value)} />
                </div>

                <div className="row">
                  <label className="muted" style={{ width: 140 }}>Topic</label>
                  <input className="input mono" value={settings?.mqtt?.topic || ""} onChange={(e)=>patch("mqtt.topic", e.target.value)} />
                </div>
              </div>
            </div>

            <div className="card">
              <div className="cardHead"><div className="cardTitle">Gate</div></div>
              <div className="cardBody">
                <div className="row">
                  <label className="muted" style={{ width: 180 }}>min_conf</label>
                  <input className="input mono" value={String(settings?.gate?.min_conf ?? "")} onChange={(e)=>patch("gate.min_conf", Number(e.target.value || 0))} />
                </div>
                <div className="row">
                  <label className="muted" style={{ width: 180 }}>confirm_n</label>
                  <input className="input mono" value={String(settings?.gate?.confirm_n ?? "")} onChange={(e)=>patch("gate.confirm_n", Number(e.target.value || 0))} />
                </div>
                <div className="row">
                  <label className="muted" style={{ width: 180 }}>confirm_window_sec</label>
                  <input className="input mono" value={String(settings?.gate?.confirm_window_sec ?? "")} onChange={(e)=>patch("gate.confirm_window_sec", Number(e.target.value || 0))} />
                </div>
                <div className="row">
                  <label className="muted" style={{ width: 180 }}>cooldown_sec</label>
                  <input className="input mono" value={String(settings?.gate?.cooldown_sec ?? "")} onChange={(e)=>patch("gate.cooldown_sec", Number(e.target.value || 0))} />
                </div>
                <div className="row">
                  <label className="muted" style={{ width: 180 }}>whitelist_path</label>
                  <input className="input mono" value={String(settings?.gate?.whitelist_path ?? "")} onChange={(e)=>patch("gate.whitelist_path", e.target.value)} />
                </div>
              </div>
            </div>
          </div>

          <div className="hint muted">
            Логика: сначала <span className="mono">Сохранить</span> (пишем settings.json), потом <span className="mono">Применить</span> (перечитываем и обновляем gate/mqtt в runtime).
          </div>
        </div>
      </div>
    </div>
  );
}
