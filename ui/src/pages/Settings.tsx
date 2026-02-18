import React, { useEffect, useMemo, useRef, useState } from "react";
import { getSettings, putSettings, applySettings, mqttCheck, mqttTestPublish, apiPost, telegramBotInfo } from "../api";

type Settings = any;
type SectionKey = "basic" | "advanced" | "diagnostics";

type SliderProps = {
  label: string;
  hint?: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
};

function SliderRow({ label, hint, value, min, max, step, onChange }: SliderProps) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <label className="muted">{label}</label>
        <span className="mono">{Number.isFinite(value) ? value : "—"}</span>
      </div>
      {hint ? <div className="hint muted" style={{ marginTop: 4 }}>{hint}</div> : null}
      <div className="row" style={{ marginTop: 6 }}>
        <input type="range" min={min} max={max} step={step} value={Number.isFinite(value) ? value : min} onChange={(e) => onChange(Number(e.target.value))} style={{ flex: 1 }} />
      </div>
    </div>
  );
}

type ToggleProps = {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
};

function ToggleRow({ label, hint, checked, onChange }: ToggleProps) {
  return (
    <div className="row" style={{ marginBottom: 10, justifyContent: "space-between" }}>
      <div>
        <div>{label}</div>
        {hint ? <div className="hint muted">{hint}</div> : null}
      </div>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
    </div>
  );
}

function TextRow({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div className="row" style={{ marginBottom: 10 }}>
      <label className="muted" style={{ width: 180 }}>{label}</label>
      <input className="input mono" value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function mergeDeep(base: any, patch: any): any {
  if (patch == null || typeof patch !== "object" || Array.isArray(patch)) return patch;
  const out = JSON.parse(JSON.stringify(base || {}));
  for (const [k, v] of Object.entries(patch)) {
    if (v != null && typeof v === "object" && !Array.isArray(v)) {
      out[k] = mergeDeep(out[k], v);
    } else {
      out[k] = v;
    }
  }
  return out;
}

const DAY_PROFILE = {
  gate: { min_conf: 0.8, confirm_n: 2, confirm_window_sec: 2.0, cooldown_sec: 15 },
  rtsp_worker: { overrides: { DET_CONF: 0.28, DET_IOU: 0.45, READ_FPS: 15, DET_FPS: 3, SEND_FPS: 3, AUTO_MODE: 0, AUTO_DROP_ON_BLUR: 0, AUTO_DROP_ON_GLARE: 0, JPEG_QUALITY: 92 } },
};

const NIGHT_PROFILE = {
  gate: { min_conf: 0.86, confirm_n: 3, confirm_window_sec: 2.8, cooldown_sec: 18 },
  rtsp_worker: { overrides: { DET_CONF: 0.35, DET_IOU: 0.45, READ_FPS: 12, DET_FPS: 2, SEND_FPS: 2, AUTO_MODE: 1, AUTO_DROP_ON_BLUR: 1, AUTO_DROP_ON_GLARE: 1, JPEG_QUALITY: 95 } },
};

function extractRuntimeSnapshot(s: any) {
  return {
    gate: {
      min_conf: s?.gate?.min_conf,
      confirm_n: s?.gate?.confirm_n,
      confirm_window_sec: s?.gate?.confirm_window_sec,
      cooldown_sec: s?.gate?.cooldown_sec,
      region_stab: s?.gate?.region_stab,
      region_stab_window_sec: s?.gate?.region_stab_window_sec,
      region_stab_min_hits: s?.gate?.region_stab_min_hits,
      region_stab_min_ratio: s?.gate?.region_stab_min_ratio,
    },
    rtsp_worker: {
      overrides: {
        READ_FPS: s?.rtsp_worker?.overrides?.READ_FPS,
        DET_FPS: s?.rtsp_worker?.overrides?.DET_FPS,
        SEND_FPS: s?.rtsp_worker?.overrides?.SEND_FPS,
        DET_CONF: s?.rtsp_worker?.overrides?.DET_CONF,
        DET_IOU: s?.rtsp_worker?.overrides?.DET_IOU,
        JPEG_QUALITY: s?.rtsp_worker?.overrides?.JPEG_QUALITY,
        AUTO_MODE: s?.rtsp_worker?.overrides?.AUTO_MODE,
        AUTO_DROP_ON_BLUR: s?.rtsp_worker?.overrides?.AUTO_DROP_ON_BLUR,
        AUTO_DROP_ON_GLARE: s?.rtsp_worker?.overrides?.AUTO_DROP_ON_GLARE,
        TRACK_ENABLE: s?.rtsp_worker?.overrides?.TRACK_ENABLE,
      },
    },
  };
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [dirty, setDirty] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [section, setSection] = useState<SectionKey>("basic");
  const [profileName, setProfileName] = useState("my_profile");
  const [selectedCustom, setSelectedCustom] = useState("");
  const importInputRef = useRef<HTMLInputElement | null>(null);
  const [mqttDiagBusy, setMqttDiagBusy] = useState(false);
  const [mqttDiagMsg, setMqttDiagMsg] = useState<string | null>(null);
  const [telegramBusy, setTelegramBusy] = useState(false);
  const [telegramMsg, setTelegramMsg] = useState<string | null>(null);
  const [botLink, setBotLink] = useState<string>("");


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

  useEffect(() => { load(); fetchBotInfo(); }, []);

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

  const applySnapshot = (name: string, snap: any) => {
    setSettings((prev: any) => {
      const base = JSON.parse(JSON.stringify(prev || {}));
      const before = extractRuntimeSnapshot(base);
      const withSnap = mergeDeep(base, snap || {});
      withSnap.ui = withSnap.ui || {};
      withSnap.ui.active_profile = name;
      withSnap.ui.last_profile_snapshot = before;
      return withSnap;
    });
    setDirty(true);
    setInfo(`Профиль «${name}» применён (не забудь Сохранить/Применить).`);
  };

  const saveCurrentAsProfile = () => {
    const name = String(profileName || "").trim();
    if (!name) {
      setErr("Введите имя профиля");
      return;
    }
    setErr(null);
    setSettings((prev: any) => {
      const next = JSON.parse(JSON.stringify(prev || {}));
      next.ui = next.ui || {};
      next.ui.profiles = next.ui.profiles || {};
      next.ui.profiles[name] = extractRuntimeSnapshot(next);
      return next;
    });
    setDirty(true);
    setSelectedCustom(name);
    setInfo(`Профиль «${name}» сохранён.`);
  };

  const applyCustomProfile = () => {
    if (!settings) return;
    const name = String(selectedCustom || "").trim();
    const snap = settings?.ui?.profiles?.[name];
    if (!name || !snap) {
      setErr("Выбери сохранённый профиль");
      return;
    }
    setErr(null);
    applySnapshot(name, snap);
  };

  const rollbackProfile = () => {
    if (!settings?.ui?.last_profile_snapshot) {
      setErr("Нет снимка для отката — сначала примени профиль");
      return;
    }
    setErr(null);
    applySnapshot("rollback", settings.ui.last_profile_snapshot);
  };

  const exportProfiles = () => {
    try {
      const payload = {
        version: 1,
        exported_at: new Date().toISOString(),
        active_profile: settings?.ui?.active_profile || null,
        profiles: settings?.ui?.profiles || {},
      };
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "lpr_profiles.json";
      a.click();
      URL.revokeObjectURL(a.href);
      setInfo("Профили экспортированы в JSON.");
      setErr(null);
    } catch (e: any) {
      setErr(e?.message || "Не удалось экспортировать профили");
    }
  };

  const importProfilesFromFile = async (file: File | null) => {
    if (!file) return;
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      const imported = data?.profiles;
      if (!imported || typeof imported !== "object") {
        setErr("Файл профилей некорректный: нет блока profiles");
        return;
      }
      setSettings((prev: any) => {
        const next = JSON.parse(JSON.stringify(prev || {}));
        next.ui = next.ui || {};
        next.ui.profiles = { ...(next.ui.profiles || {}), ...imported };
        return next;
      });
      setDirty(true);
      setErr(null);
      setInfo("Профили импортированы. Нажми Сохранить/Применить.");
    } catch (e: any) {
      setErr(e?.message || "Не удалось импортировать профили");
    }
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

  const fetchBotInfo = async () => {
    try {
      const r = await telegramBotInfo();
      if (r?.ok && r?.link) setBotLink(String(r.link));
      else setBotLink("");
    } catch {
      setBotLink("");
    }
  };

  const onMqttCheck = async () => {
    try {
      setMqttDiagBusy(true);
      setMqttDiagMsg(null);
      const r = await mqttCheck();
      if (r?.ok) setMqttDiagMsg(`✅ MQTT доступен: ${r.host}:${r.port}`);
      else setMqttDiagMsg(`❌ MQTT: ${r?.error || "недоступен"}`);
    } catch (e: any) {
      setMqttDiagMsg(`❌ MQTT: ${e?.message || String(e)}`);
    } finally {
      setMqttDiagBusy(false);
    }
  };

  const onMqttTestTopic = async () => {
    try {
      setMqttDiagBusy(true);
      setMqttDiagMsg(null);
      const topic = String(settings?.mqtt?.topic || "gate/open");
      const r = await mqttTestPublish(topic, { kind: "ui_test", source: "settings_page", ts: Date.now() / 1000 });
      if (r?.ok) setMqttDiagMsg(`✅ Тестовый топик отправлен: ${r.topic}`);
      else setMqttDiagMsg(`❌ Тестовый топик: ${r?.error || "ошибка"}`);
    } catch (e: any) {
      setMqttDiagMsg(`❌ Тестовый топик: ${e?.message || String(e)}`);
    } finally {
      setMqttDiagBusy(false);
    }
  };

  const onTelegramTest = async () => {
    try {
      setTelegramBusy(true);
      setTelegramMsg(null);
      const withPhoto = !!(settings?.telegram?.send_photo ?? true);
      const r = await apiPost("/api/v1/telegram/test", { text: "✅ GateBox: тест Telegram (из UI → Настройки)", with_photo: withPhoto });
      if (r?.ok) setTelegramMsg("✅ Тест отправлен. Проверь Telegram.");
      else setTelegramMsg(`❌ Telegram: ${r?.error || "ошибка"}`);
    } catch (e: any) {
      setTelegramMsg(`❌ Telegram: ${e?.message || String(e)}`);
    } finally {
      setTelegramBusy(false);
    }
  };

  const onApply = async () => {
    try {
      await applySettings();
      setErr(null);
      setInfo("Применено (gate + mqtt). Параметры rtsp_worker подхватятся автоматически через poll.");
    } catch (e: any) {
      setErr(e?.message || String(e));
    }
  };

  const ov = settings?.rtsp_worker?.overrides || {};
  const customProfiles = useMemo(() => Object.keys(settings?.ui?.profiles || {}), [settings]);

  if (!settings) {
    return <div className="card"><div className="cardBody muted">Загрузка…</div></div>;
  }

  return (
    <div className="col">
      <div className="card">
        <div className="cardHead">
          <div className="cardTitle">Настройки (перенос из ENV)</div>
          <div className="row">
            <button className={`btn ${section === "basic" ? "btn-primary" : "btn-ghost"}`} type="button" onClick={() => setSection("basic")}>Базовые</button>
            <button className={`btn ${section === "advanced" ? "btn-primary" : "btn-ghost"}`} type="button" onClick={() => setSection("advanced")}>Продвинутые</button>
            <button className={`btn ${section === "diagnostics" ? "btn-primary" : "btn-ghost"}`} type="button" onClick={() => setSection("diagnostics")}>Диагностика</button>
          </div>
          <div className="row">
            <button className="btn btn-ghost" type="button" onClick={load}>Обновить</button>
            <button className="btn btn-primary" type="button" onClick={onSave} disabled={!dirty}>Сохранить</button>
            <button className="btn btn-primary" type="button" onClick={onApply}>Применить</button>
          </div>
        </div>

        <div className="cardBody">
          {err ? <div className="alert alert-error mono">{err}</div> : null}
          {info ? <div className="alert mono">{info}</div> : null}

          <div className="card" style={{ marginBottom: 12 }}>
            <div className="cardHead"><div className="cardTitle">Профили (день / ночь / свои)</div></div>
            <div className="cardBody">
              <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
                <button className="btn" onClick={() => applySnapshot("day", DAY_PROFILE)}>Применить «День»</button>
                <button className="btn" onClick={() => applySnapshot("night", NIGHT_PROFILE)}>Применить «Ночь»</button>
                <button className="btn" onClick={rollbackProfile}>Откатить последний профиль</button>
                <span className="muted mono">active: {String(settings?.ui?.active_profile || "—")}</span>
              </div>

              <div className="row" style={{ marginTop: 10, gap: 10, flexWrap: "wrap" }}>
                <input className="input mono" style={{ width: 220 }} value={profileName} onChange={(e) => setProfileName(e.target.value)} placeholder="имя профиля" />
                <button className="btn" onClick={saveCurrentAsProfile}>Сохранить текущие как профиль</button>
                <select className="input mono" style={{ width: 220 }} value={selectedCustom} onChange={(e) => setSelectedCustom(e.target.value)}>
                  <option value="">— выбрать свой профиль —</option>
                  {customProfiles.map((k) => <option key={k} value={k}>{k}</option>)}
                </select>
                <button className="btn" onClick={applyCustomProfile}>Применить свой профиль</button>
                <button className="btn" onClick={exportProfiles}>Экспорт профилей</button>
                <button className="btn" onClick={() => importInputRef.current?.click()}>Импорт профилей</button>
                <input
                  ref={importInputRef}
                  type="file"
                  accept="application/json,.json"
                  style={{ display: "none" }}
                  onChange={(e) => {
                    const f = e.target.files?.[0] || null;
                    importProfilesFromFile(f);
                    e.currentTarget.value = "";
                  }}
                />
              </div>

              <div className="hint muted" style={{ marginTop: 8 }}>
                Профили меняют только рабочие параметры распознавания/детекции. Можно экспортировать/импортировать JSON. После любых изменений нажмите
                <span className="mono"> Сохранить</span> и <span className="mono">Применить</span>.
              </div>
            </div>
          </div>

          {section === "basic" ? (
            <div className="grid2">
              <div className="card">
                <div className="cardHead"><div className="cardTitle">Gate / решение о проезде</div></div>
                <div className="cardBody">
                  <SliderRow label="Порог уверенности OCR (MIN_CONF)" hint="Чем выше, тем меньше ложных срабатываний" value={Number(settings?.gate?.min_conf ?? 0.85)} min={0.5} max={0.99} step={0.01} onChange={(v) => patch("gate.min_conf", v)} />
                  <SliderRow label="Подтверждений подряд (CONFIRM_N)" value={Number(settings?.gate?.confirm_n ?? 2)} min={1} max={6} step={1} onChange={(v) => patch("gate.confirm_n", v)} />
                  <SliderRow label="Окно подтверждения, сек" value={Number(settings?.gate?.confirm_window_sec ?? 2)} min={0.5} max={8} step={0.1} onChange={(v) => patch("gate.confirm_window_sec", v)} />
                  <SliderRow label="Cooldown после открытия, сек" value={Number(settings?.gate?.cooldown_sec ?? 15)} min={1} max={60} step={1} onChange={(v) => patch("gate.cooldown_sec", v)} />
                  <ToggleRow label="Проверка формата региона РФ (REGION_CHECK)" checked={!!settings?.gate?.region_check} onChange={(v) => patch("gate.region_check", v)} />
                </div>
              </div>

              <div className="card">
                <div className="cardHead"><div className="cardTitle">RTSP worker / ключевые параметры</div></div>
                <div className="cardBody">
                  <SliderRow label="READ_FPS" value={Number(ov.READ_FPS ?? 15)} min={1} max={30} step={1} onChange={(v) => patch("rtsp_worker.overrides.READ_FPS", v)} />
                  <SliderRow label="DET_FPS" value={Number(ov.DET_FPS ?? 2)} min={1} max={15} step={0.5} onChange={(v) => patch("rtsp_worker.overrides.DET_FPS", v)} />
                  <SliderRow label="SEND_FPS" value={Number(ov.SEND_FPS ?? 2.5)} min={0.5} max={15} step={0.5} onChange={(v) => patch("rtsp_worker.overrides.SEND_FPS", v)} />
                  <SliderRow label="Порог детекции (DET_CONF)" value={Number(ov.DET_CONF ?? 0.3)} min={0.05} max={0.95} step={0.01} onChange={(v) => patch("rtsp_worker.overrides.DET_CONF", v)} />
                  <ToggleRow label="Включить авто-режим день/ночь (AUTO_MODE)" checked={Number(ov.AUTO_MODE ?? 0) !== 0} onChange={(v) => patch("rtsp_worker.overrides.AUTO_MODE", v ? 1 : 0)} />
                  <ToggleRow label="Tracking" checked={Number(ov.TRACK_ENABLE ?? 1) !== 0} onChange={(v) => patch("rtsp_worker.overrides.TRACK_ENABLE", v ? 1 : 0)} />
                </div>
              </div>
            </div>
          ) : null}

          {section === "advanced" ? (
            <div className="grid2">
              <div className="card">
                <div className="cardHead"><div className="cardTitle">Gate / стабилизация региона</div></div>
                <div className="cardBody">
                  <ToggleRow label="Стабилизация региона (REGION_STAB)" checked={!!settings?.gate?.region_stab} onChange={(v) => patch("gate.region_stab", v)} />
                  <SliderRow label="Окно стабилизации региона, сек" value={Number(settings?.gate?.region_stab_window_sec ?? 2.5)} min={0.5} max={8} step={0.1} onChange={(v) => patch("gate.region_stab_window_sec", v)} />
                  <SliderRow label="Минимум попаданий региона" value={Number(settings?.gate?.region_stab_min_hits ?? 3)} min={1} max={10} step={1} onChange={(v) => patch("gate.region_stab_min_hits", v)} />
                  <SliderRow label="Доля совпадений региона" value={Number(settings?.gate?.region_stab_min_ratio ?? 0.6)} min={0.3} max={1} step={0.01} onChange={(v) => patch("gate.region_stab_min_ratio", v)} />
                </div>
              </div>

              <div className="card">
                <div className="cardHead"><div className="cardTitle">RTSP worker / расширенная обработка</div></div>
                <div className="cardBody">
                  <SliderRow label="IOU NMS (DET_IOU)" value={Number(ov.DET_IOU ?? 0.45)} min={0.1} max={0.9} step={0.01} onChange={(v) => patch("rtsp_worker.overrides.DET_IOU", v)} />
                  <SliderRow label="JPEG качество" value={Number(ov.JPEG_QUALITY ?? 94)} min={60} max={100} step={1} onChange={(v) => patch("rtsp_worker.overrides.JPEG_QUALITY", v)} />
                  <ToggleRow label="Rectify (выпрямление номера)" checked={Number(ov.RECTIFY ?? 1) !== 0} onChange={(v) => patch("rtsp_worker.overrides.RECTIFY", v ? 1 : 0)} />
                  <ToggleRow label="Upscale перед OCR" checked={Number(ov.UPSCALE_ENABLE ?? 1) !== 0} onChange={(v) => patch("rtsp_worker.overrides.UPSCALE_ENABLE", v ? 1 : 0)} />
                  <ToggleRow label="AUTO_DROP_ON_BLUR" checked={Number(ov.AUTO_DROP_ON_BLUR ?? 1) !== 0} onChange={(v) => patch("rtsp_worker.overrides.AUTO_DROP_ON_BLUR", v ? 1 : 0)} />
                  <ToggleRow label="AUTO_DROP_ON_GLARE" checked={Number(ov.AUTO_DROP_ON_GLARE ?? 1) !== 0} onChange={(v) => patch("rtsp_worker.overrides.AUTO_DROP_ON_GLARE", v ? 1 : 0)} />
                  <ToggleRow label="AUTO_RECTIFY" checked={Number(ov.AUTO_RECTIFY ?? 1) !== 0} onChange={(v) => patch("rtsp_worker.overrides.AUTO_RECTIFY", v ? 1 : 0)} />
                  <ToggleRow label="AUTO_PAD_ENABLE" checked={Number(ov.AUTO_PAD_ENABLE ?? 1) !== 0} onChange={(v) => patch("rtsp_worker.overrides.AUTO_PAD_ENABLE", v ? 1 : 0)} />
                  <ToggleRow label="AUTO_UPSCALE_ENABLE" checked={Number(ov.AUTO_UPSCALE_ENABLE ?? 1) !== 0} onChange={(v) => patch("rtsp_worker.overrides.AUTO_UPSCALE_ENABLE", v ? 1 : 0)} />
                  <TextRow label="RTSP_TRANSPORT" value={String(ov.RTSP_TRANSPORT ?? "tcp")} onChange={(v) => patch("rtsp_worker.overrides.RTSP_TRANSPORT", v)} />
                </div>
              </div>
            </div>
          ) : null}

          {section === "diagnostics" ? (
            <div className="grid2">
              <div className="card">
                <div className="cardHead"><div className="cardTitle">MQTT</div></div>
                <div className="cardBody">
                  <ToggleRow label="Включить MQTT" checked={!!settings?.mqtt?.enabled} onChange={(v) => patch("mqtt.enabled", v)} />
                  <TextRow label="MQTT host" value={String(settings?.mqtt?.host || "")} onChange={(v) => patch("mqtt.host", v)} />
                  <TextRow label="MQTT port" value={String(settings?.mqtt?.port ?? "")} onChange={(v) => patch("mqtt.port", Number(v || 0))} />
                  <TextRow label="MQTT user" value={String(settings?.mqtt?.user || "")} onChange={(v) => patch("mqtt.user", v)} />
                  <TextRow label="MQTT pass" value={String(settings?.mqtt?.pass || "")} onChange={(v) => patch("mqtt.pass", v)} />
                  <TextRow label="MQTT topic" value={String(settings?.mqtt?.topic || "")} onChange={(v) => patch("mqtt.topic", v)} />
                  <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
                    <button className="btn btn-ghost" type="button" onClick={onMqttCheck} disabled={mqttDiagBusy}>Проверить связь</button>
                    <button className="btn btn-primary" type="button" onClick={onMqttTestTopic} disabled={mqttDiagBusy}>Отправить тестовый топик</button>
                  </div>
                  {mqttDiagMsg ? <div className="hint" style={{ marginTop: 8 }}>{mqttDiagMsg}</div> : null}
                </div>
              </div>

              <div className="card">
                <div className="cardHead"><div className="cardTitle">Telegram</div></div>
                <div className="cardBody">
                  <ToggleRow label="Включить Telegram" checked={!!settings?.telegram?.enabled} onChange={(v) => patch("telegram.enabled", v)} />
                  <ToggleRow label="Присылать фото" checked={!!(settings?.telegram?.send_photo ?? true)} onChange={(v) => patch("telegram.send_photo", v)} />
                  <TextRow label="Bot token" value={String(settings?.telegram?.bot_token || "")} onChange={(v) => patch("telegram.bot_token", v)} />
                  <TextRow label="Chat ID" value={String(settings?.telegram?.chat_id ?? "")} onChange={(v) => patch("telegram.chat_id", v.trim() || null)} />
                  {botLink ? <div className="hint" style={{ marginBottom: 8 }}>Ссылка на бота: <a href={botLink} target="_blank" rel="noreferrer">{botLink}</a></div> : null}
                  <div className="hint" style={{ marginBottom: 8 }}>Открой бота, нажми <span className="mono">/start</span>, после чего проверь/заполни <span className="mono">Chat ID</span>.</div>
                  <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
                    <button className="btn btn-ghost" type="button" onClick={fetchBotInfo} disabled={telegramBusy}>Обновить ссылку на бота</button>
                    <button className="btn btn-primary" type="button" onClick={onTelegramTest} disabled={telegramBusy}>Отправить тест</button>
                  </div>
                  {telegramMsg ? <div className="hint" style={{ marginTop: 8 }}>{telegramMsg}</div> : null}
                </div>
              </div>

              <div className="card">
                <div className="cardHead"><div className="cardTitle">RTSP worker / live и отладка</div></div>
                <div className="cardBody">
                  <ToggleRow label="LIVE_DRAW_YOLO" checked={Number(ov.LIVE_DRAW_YOLO ?? 1) !== 0} onChange={(v) => patch("rtsp_worker.overrides.LIVE_DRAW_YOLO", v ? 1 : 0)} />
                  <ToggleRow label="LIVE_SAVE_QUAD" checked={Number(ov.LIVE_SAVE_QUAD ?? 1) !== 0} onChange={(v) => patch("rtsp_worker.overrides.LIVE_SAVE_QUAD", v ? 1 : 0)} />
                  <ToggleRow label="Freeze watchdog" checked={Number(ov.FREEZE_ENABLE ?? 0) !== 0} onChange={(v) => patch("rtsp_worker.overrides.FREEZE_ENABLE", v ? 1 : 0)} />
                  <SliderRow label="LOG_EVERY_SEC" hint="Период alive-лога" value={Number(ov.LOG_EVERY_SEC ?? 5)} min={0} max={30} step={1} onChange={(v) => patch("rtsp_worker.overrides.LOG_EVERY_SEC", v)} />
                  <SliderRow label="SAVE_EVERY" hint="Сохранять каждый N-й отправленный кадр (0=выкл)" value={Number(ov.SAVE_EVERY ?? 0)} min={0} max={30} step={1} onChange={(v) => patch("rtsp_worker.overrides.SAVE_EVERY", v)} />
                  <ToggleRow label="SAVE_FULL_FRAME" checked={Number(ov.SAVE_FULL_FRAME ?? 0) !== 0} onChange={(v) => patch("rtsp_worker.overrides.SAVE_FULL_FRAME", v ? 1 : 0)} />
                  <ToggleRow label="SAVE_WITH_ROI" checked={Number(ov.SAVE_WITH_ROI ?? 0) !== 0} onChange={(v) => patch("rtsp_worker.overrides.SAVE_WITH_ROI", v ? 1 : 0)} />
                  <TextRow label="SAVE_DIR" value={String(ov.SAVE_DIR ?? "/debug")} onChange={(v) => patch("rtsp_worker.overrides.SAVE_DIR", v)} />
                </div>
              </div>
            </div>
          ) : null}

          <div className="hint muted" style={{ marginTop: 10 }}>
            Разделение сделано по ролям: <span className="mono">Базовые</span> для ежедневной настройки,
            <span className="mono"> Продвинутые</span> для тонкого тюнинга и <span className="mono">Диагностика</span> для логов/live.
          </div>
        </div>
      </div>
    </div>
  );
}
