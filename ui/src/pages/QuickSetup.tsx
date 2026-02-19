// ui/src/pages/QuickSetup.tsx
// Мастер "Быстрая настройка" — простыми словами

import React, { useEffect, useMemo, useRef, useState } from "react";
import { getRtspStatus, rtspFrameUrl, rtspBoxes, getWhitelist, putWhitelist, reloadWhitelist, putSettings } from "../api";
import { useEventsStream } from "../hooks/useEventsStream";

type Roi = { x: number; y: number; w: number; h: number };

function clamp(n: number, a: number, b: number) {
  return Math.max(a, Math.min(b, n));
}

export default function QuickSetupPage() {
  const [step, setStep] = useState(1);

  // Камера
  const [status, setStatus] = useState<any>(null);
  const [boxes, setBoxes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [frameTs, setFrameTs] = useState<number>(() => Date.now());

  // ROI (пока только для удобства в UI)
  const [roi, setRoi] = useState<Roi | null>(() => {
    try {
      const s = localStorage.getItem("lpr_gatebox_roi");
      return s ? JSON.parse(s) : null;
    } catch {
      return null;
    }
  });
  const [drawing, setDrawing] = useState(false);
  const dragStart = useRef<{ x: number; y: number } | null>(null);
  const frameWrapRef = useRef<HTMLDivElement | null>(null);

  // Whitelist
  const [plates, setPlates] = useState<string[]>([]);
  const [plateInput, setPlateInput] = useState("");
  const [wlInfo, setWlInfo] = useState<string | null>(null);
  const [wlErr, setWlErr] = useState<string | null>(null);

  // События (в мастере показываем и диагностику, чтобы было видно тесты)
  const events = useEventsStream({ includeDebug: true, limit: 50 });
  const last = events.items?.[0] || null;

  const alive = !!status?.alive;
  const ageMs = status?.age_ms ?? null;
  const fps = status?.fps ?? null;

  // polling
  useEffect(() => {
    let ok = true;
    const tick = async () => {
      try {
        const [s, b] = await Promise.all([getRtspStatus(), rtspBoxes()]);
        if (!ok) return;
        setStatus(s);
        setBoxes(b?.boxes || null);
        setErr(null);
      } catch (e: any) {
        if (!ok) return;
        setErr(String(e?.message || e));
      }
      setFrameTs(Date.now());
    };
    tick();
    const id = setInterval(tick, 800);
    return () => {
      ok = false;
      clearInterval(id);
    };
  }, []);

  // load whitelist (если backend уже обновлён)
  useEffect(() => {
    let ok = true;
    (async () => {
      try {
        const r = await getWhitelist();
        if (!ok) return;
        const list = Array.isArray(r?.plates) ? r.plates : [];
        setPlates(list.map((x: any) => String(x)));
        setWlErr(null);
      } catch {
        if (!ok) return;
        setWlErr("Список номеров пока недоступен. Надо обновить сервер (я дам готовую правку).");
      }
    })();
    return () => {
      ok = false;
    };
  }, []);

  const title = useMemo(() => {
    if (step === 1) return "Проверяем камеру";
    if (step === 2) return "Выделяем место номера";
    if (step === 3) return "Проверяем распознавание";
    if (step === 4) return "Добавляем свои номера";
    return "Готово";
  }, [step]);

  const badge = alive ? "badge badge-green" : "badge badge-red";

  const frameUrl = rtspFrameUrl(frameTs);

  // ROI drawing handlers (на контейнере frameWrap)
  function onDown(e: React.MouseEvent<HTMLDivElement>) {
    if (step !== 2) return;
    const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
    const x = clamp(e.clientX - rect.left, 0, rect.width);
    const y = clamp(e.clientY - rect.top, 0, rect.height);
    dragStart.current = { x, y };
    setDrawing(true);
    setRoi({ x, y, w: 1, h: 1 });
  }

  function onMove(e: React.MouseEvent<HTMLDivElement>) {
    if (step !== 2) return;
    if (!drawing || !dragStart.current) return;
    const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
    const x = clamp(e.clientX - rect.left, 0, rect.width);
    const y = clamp(e.clientY - rect.top, 0, rect.height);
    const x0 = dragStart.current.x;
    const y0 = dragStart.current.y;
    const rx = Math.min(x0, x);
    const ry = Math.min(y0, y);
    const rw = Math.abs(x - x0);
    const rh = Math.abs(y - y0);
    setRoi({ x: rx, y: ry, w: Math.max(2, rw), h: Math.max(2, rh) });
  }

  function onUp() {
    if (step !== 2) return;
    setDrawing(false);
    dragStart.current = null;
  }

  async function saveRoi() {
    if (!roi) return;

    try {
      localStorage.setItem("lpr_gatebox_roi", JSON.stringify(roi));
    } catch (e) {
      console.warn("cannot save ROI to localStorage", e);
    }

    const wrap = frameWrapRef.current;
    const fw = Number(boxes?.w || 0);
    const fh = Number(boxes?.h || 0);

    if (!wrap || fw <= 0 || fh <= 0) {
      setWlErr("Не удалось сохранить ROI в систему: нет размеров кадра");
      return;
    }

    const rw = wrap.clientWidth || 1;
    const rh = wrap.clientHeight || 1;

    const x1 = Math.max(0, Math.min(fw - 1, Math.round((roi.x / rw) * fw)));
    const y1 = Math.max(0, Math.min(fh - 1, Math.round((roi.y / rh) * fh)));
    const x2 = Math.max(x1 + 1, Math.min(fw, Math.round(((roi.x + roi.w) / rw) * fw)));
    const y2 = Math.max(y1 + 1, Math.min(fh, Math.round(((roi.y + roi.h) / rh) * fh)));

    const roiStr = `${x1},${y1},${x2},${y2}`;

    try {
      await putSettings({ rtsp_worker: { overrides: { ROI_STR: roiStr } } });
      setWlErr(null);
      setWlInfo(`Зона сохранена в систему ✅ (${roiStr})`);
      setTimeout(() => setWlInfo(null), 1800);
    } catch (e: any) {
      setWlErr(`Ошибка сохранения ROI: ${e?.message || e}`);
    }
  }

  async function addPlate() {
    const p = plateInput.trim().toUpperCase();
    if (!p) return;
    const next = Array.from(new Set([p, ...plates]));
    setPlates(next);
    setPlateInput("");
    try {
      await putWhitelist(next);
      await reloadWhitelist();
      setWlInfo("Сохранено ✅");
      setWlErr(null);
      setTimeout(() => setWlInfo(null), 1500);
    } catch {
      setWlErr("Не получилось сохранить. Надо обновить сервер (я дам готовую правку).");
    }
  }

  async function removePlate(p: string) {
    const next = plates.filter((x) => x !== p);
    setPlates(next);
    try {
      await putWhitelist(next);
      await reloadWhitelist();
      setWlInfo("Сохранено ✅");
      setWlErr(null);
      setTimeout(() => setWlInfo(null), 1500);
    } catch {
      setWlErr("Не получилось сохранить. Надо обновить сервер (я дам готовую правку).");
    }
  }

  return (
    <div className="col">
      <div className="card">
        <div className="cardHead">
          <div>
            <div className="cardTitle">Быстрая настройка</div>
            <div className="cardSub">
              Шаг {step} из 5 — {title}
            </div>
          </div>

          <div className="row" style={{ gap: 10 }}>
            <button className="btn" onClick={() => setStep((s) => clamp(s - 1, 1, 5))} disabled={step === 1}>
              Назад
            </button>
            <button className="btn btn-primary" onClick={() => setStep((s) => clamp(s + 1, 1, 5))} disabled={step === 5}>
              Далее
            </button>
          </div>
        </div>
      </div>

      {/* STEP 1 */}
      {step === 1 && (
        <div className="grid2">
          <div className="card">
            <div className="cardBody">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div>
                  <div className="cardTitle">Камера</div>
                  <div className="cardSub">Должна быть “Работает”</div>
                </div>
                <span className={badge}>{alive ? "Работает" : "Нет связи"}</span>
              </div>

              <div className="cardSub" style={{ marginTop: 12 }}>
                {err ? (
                  <>Ошибка: {err}</>
                ) : (
                  <>
                    {ageMs != null ? <>Обновление: {ageMs} мс</> : <>Ожидаем данные...</>}
                    {fps != null ? <> • FPS: {Number(fps).toFixed(1)}</> : null}
                  </>
                )}
              </div>

              <div className="hint" style={{ marginTop: 12 }}>
                Если “Нет связи” — проверь питание камеры и сеть. Если кадр есть — жми “Далее”.
              </div>
            </div>
          </div>

          <div className="card">
            <div className="cardBody">
              <div className="cardTitle">Картинка</div>
              <div className="frameWrap">
                <img className="frameImg" src={frameUrl} alt="frame" />
              </div>
            </div>
          </div>
        </div>
      )}

      {/* STEP 2 */}
      {step === 2 && (
        <div className="grid2">
          <div className="card">
            <div className="cardBody">
              <div className="cardTitle">Зона номера</div>
              <div className="cardSub" style={{ marginTop: 6 }}>
                Протяни мышкой прямоугольник там, где обычно появляется номер.
              </div>

              <div className="hint" style={{ marginTop: 12 }}>
                Зона сохраняется в settings.json и применяется rtsp_worker на лету (без перезапуска).
              </div>

              <div className="row" style={{ gap: 10, marginTop: 12 }}>
                <button className="btn btn-primary" onClick={saveRoi} disabled={!roi}>
                  Сохранить зону
                </button>
                <button className="btn" onClick={() => setRoi(null)}>
                  Сбросить
                </button>
              </div>

              {wlInfo ? <div className="hint" style={{ marginTop: 10 }}>{wlInfo}</div> : null}
            </div>
          </div>

          <div className="card">
            <div className="cardBody">
              <div className="cardTitle">Выбор зоны</div>

              <div ref={frameWrapRef} className="frameWrap" onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={onUp}>
                <img className="frameImg" src={frameUrl} alt="frame" />
                {roi && (
                  <div
                    className="roiBox"
                    style={{ left: roi.x, top: roi.y, width: roi.w, height: roi.h }}
                  />
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* STEP 3 */}
      {step === 3 && (
        <div className="grid2">
          <div className="card">
            <div className="cardBody">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div>
                  <div className="cardTitle">Проверка распознавания</div>
                  <div className="cardSub">Показываем последнее событие</div>
                </div>
                <span className={events.connected ? "badge badge-green" : "badge badge-yellow"}>
                  {events.connected ? "Связь есть" : "Подключаемся..."}
                </span>
              </div>

              {last ? (
                <div className="hint" style={{ marginTop: 12 }}>
                  <div style={{ fontSize: 18, fontWeight: 900 }}>{last.plate || "—"}</div>
                  <div style={{ marginTop: 6, opacity: 0.9 }}>
                    {last.message || ""}
                    {last.conf != null ? <> • уверенность: {Number(last.conf).toFixed(3)}</> : null}
                  </div>
                  <div style={{ marginTop: 6, opacity: 0.7 }}>
                    Статус: {String(last.status || "info")}
                  </div>
                </div>
              ) : (
                <div className="hint" style={{ marginTop: 12 }}>
                  Пока нет событий. Это нормально. Можно сделать тест /infer.
                </div>
              )}

              <div className="hint" style={{ marginTop: 12 }}>
                Если видишь “noise_ocr” — это просто “мусор”, когда номера в кадре нет.
              </div>
            </div>
          </div>

          <div className="card">
            <div className="cardBody">
              <div className="cardTitle">Кадр</div>
              <div className="frameWrap">
                <img className="frameImg" src={frameUrl} alt="frame" />

                {/* рамки (если есть) */}
                {boxes?.items?.map((b: any, i: number) => {
                  const w = Number(boxes?.w || 0);
                  const h = Number(boxes?.h || 0);
                  if (!w || !h) return null;
                  const left = (Number(b.x1) / w) * 100;
                  const top = (Number(b.y1) / h) * 100;
                  const ww = ((Number(b.x2) - Number(b.x1)) / w) * 100;
                  const hh = ((Number(b.y2) - Number(b.y1)) / h) * 100;
                  return <div key={i} className="yoloBox" style={{ left: `${left}%`, top: `${top}%`, width: `${ww}%`, height: `${hh}%` }} />;
                })}
              </div>

              <div className="cardSub" style={{ marginTop: 10 }}>
                {boxes?.items?.length ? `Рамок: ${boxes.items.length}` : "Рамок пока нет (это нормально)."}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* STEP 4 */}
      {step === 4 && (
        <div className="grid2">
          <div className="card">
            <div className="cardBody">
              <div className="cardTitle">Свои номера</div>
              <div className="cardSub" style={{ marginTop: 6 }}>
                Добавь номера, которым можно открывать ворота.
              </div>

              <div className="row" style={{ gap: 10, marginTop: 12 }}>
                <input
                  className="input"
                  placeholder="Например: А123ВС77"
                  value={plateInput}
                  onChange={(e) => setPlateInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") addPlate(); }}
                />
                <button className="btn btn-primary" onClick={addPlate}>
                  Добавить
                </button>
              </div>

              {wlInfo ? <div className="hint" style={{ marginTop: 10 }}>{wlInfo}</div> : null}
              {wlErr ? <div className="alert alert-error" style={{ marginTop: 10 }}>{wlErr}</div> : null}
            </div>
          </div>

          <div className="card">
            <div className="cardBody">
              <div className="cardTitle">Список</div>

              {plates.length ? (
                <div className="list" style={{ marginTop: 10 }}>
                  {plates.map((p) => (
                    <div key={p} className="listRow">
                      <div className="mono">{p}</div>
                      <button className="btn" onClick={() => removePlate(p)}>Удалить</button>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="hint" style={{ marginTop: 10 }}>
                  Пока список пуст. Добавь первый номер слева.
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* STEP 5 */}
      {step === 5 && (
        <div className="card">
          <div className="cardBody">
            <div className="cardTitle" style={{ fontSize: 22 }}>Готово ✅</div>
            <div className="cardSub" style={{ marginTop: 8 }}>
              Всё настроено. Теперь можно просто пользоваться.
            </div>

            <div className="row" style={{ gap: 10, marginTop: 14 }}>
              <button className="btn btn-primary" onClick={() => setStep(1)}>
                Пройти ещё раз
              </button>
              <button className="btn" onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}>
                Наверх
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
