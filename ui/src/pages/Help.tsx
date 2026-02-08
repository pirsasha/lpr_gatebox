// ui/src/pages/Help.tsx
// LPR GateBox UI — Help
// Версия: v0.3.3 (draft)
// Обновлено: 2026-02-08
//
// Что сделано:
// - NEW: страница /help (без изменений gatebox/rtsp_worker)
// - NEW: 3 блока (Сообщество / Инструкции / Донаты)
// - NEW: конфиг из help.json (без хардкода)
// - NEW: копирование реквизитов + toast "Скопировано ✅"
// - NEW: мобильная верстка 320–480px (кнопки w=100%, без горизонтального скролла)

import React, { useEffect, useMemo, useState } from "react";

type GuideLink = { title: string; url: string };
type ChannelLink = { label: string; url: string };

type DonateMethod = {
  id: string;
  label: string;
  url?: string;
  value?: string;
};

type HelpConfig = {
  community: {
    title: string;
    description: string;
    buttonText: string;
    url: string;
  };
  media: {
    title: string;
    channel: ChannelLink;
    guides: GuideLink[];
  };
  donate: {
    title: string;
    description: string;
    methods: DonateMethod[];
    qr?: { enabled: boolean; image: string };
  };
  tips?: { support?: string };
};

const DEFAULT_CFG: HelpConfig = {
  community: {
    title: "Сообщество / Telegram",
    description:
      "Вопросы по настройке, баги, примеры камер и реальные кейсы пользователей.",
    buttonText: "Перейти в чат поддержки",
    url: "https://t.me/",
  },
  media: {
    title: "Видео и инструкции",
    channel: { label: "YouTube-канал", url: "https://youtube.com" },
    guides: [],
  },
  donate: {
    title: "Поддержать проект",
    description:
      "Поддержка помогает развивать проект, добавлять новые функции и поддерживать стабильность.",
    methods: [],
    qr: { enabled: false, image: "" },
  },
  tips: {
    support:
      "При обращении в поддержку желательно приложить: _frame_vis.jpg, _rectify.jpg и описание камеры.",
  },
};

function isHttpUrl(x?: string) {
  if (!x) return false;
  return x.startsWith("http://") || x.startsWith("https://");
}

function safeOpen(url: string) {
  if (!url) return;
  window.open(url, "_blank", "noopener,noreferrer");
}

function normalizeCfg(raw: any): HelpConfig {
  // Best-effort нормализация, чтобы UI не падал от неполного JSON
  const cfg: HelpConfig = {
    ...DEFAULT_CFG,
    ...raw,
    community: { ...DEFAULT_CFG.community, ...(raw?.community || {}) },
    media: {
      ...DEFAULT_CFG.media,
      ...(raw?.media || {}),
      channel: { ...DEFAULT_CFG.media.channel, ...(raw?.media?.channel || {}) },
      guides: Array.isArray(raw?.media?.guides) ? raw.media.guides : DEFAULT_CFG.media.guides,
    },
    donate: {
      ...DEFAULT_CFG.donate,
      ...(raw?.donate || {}),
      methods: Array.isArray(raw?.donate?.methods) ? raw.donate.methods : DEFAULT_CFG.donate.methods,
      qr: raw?.donate?.qr
        ? { enabled: !!raw.donate.qr.enabled, image: String(raw.donate.qr.image || "") }
        : DEFAULT_CFG.donate.qr,
    },
    tips: { ...DEFAULT_CFG.tips, ...(raw?.tips || {}) },
  };
  return cfg;
}

function Toast({ text }: { text: string }) {
  if (!text) return null;
  return (
    <div
      style={{
        position: "fixed",
        left: "50%",
        bottom: 18,
        transform: "translateX(-50%)",
        background: "rgba(18, 22, 32, 0.95)",
        border: "1px solid rgba(255,255,255,.10)",
        padding: "10px 12px",
        borderRadius: 12,
        boxShadow: "0 12px 30px rgba(0,0,0,.35)",
        maxWidth: "calc(100% - 24px)",
        zIndex: 9999,
      }}
    >
      <div style={{ fontWeight: 750 }}>{text}</div>
    </div>
  );
}

export default function HelpPage() {
  const [cfg, setCfg] = useState<HelpConfig>(DEFAULT_CFG);
  const [loading, setLoading] = useState(true);
  const [toastText, setToastText] = useState("");

  const toast = (txt: string) => {
    setToastText(txt);
    window.setTimeout(() => setToastText(""), 1800);
  };

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        // help.json лежит в public/ => доступен как /help.json
        // cache-bust, чтобы при обновлении не залипало
        const r = await fetch(`/help.json?ts=${Date.now()}`, { cache: "no-store" });
        if (!r.ok) throw new Error(`help.json http ${r.status}`);
        const j = await r.json();
        if (!alive) return;
        setCfg(normalizeCfg(j));
      } catch (e) {
        if (!alive) return;
        setCfg(DEFAULT_CFG);
      } finally {
        if (!alive) return;
        setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const donateHasAny = useMemo(() => {
    return (cfg.donate?.methods || []).some((m) => !!m.value || !!m.url);
  }, [cfg]);

  const isMobile = useMemo(
    () => window.matchMedia && window.matchMedia("(max-width: 560px)").matches,
    []
  );

  const cardBtnStyle: React.CSSProperties = useMemo(
    () => ({ width: "100%", display: "inline-flex", justifyContent: "center" }),
    []
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <Toast text={toastText} />

      {/* --------- COMMUNITY --------- */}
      <div className="card">
        <div className="cardHead">
          <div className="cardTitle">{cfg.community.title}</div>
          {loading && (
            <div className="muted" style={{ fontSize: 12 }}>
              загрузка…
            </div>
          )}
        </div>
        <div className="cardBody">
          <div className="muted" style={{ marginBottom: 10 }}>
            {cfg.community.description}
          </div>

          <button
            type="button"
            className="btn btn-primary"
            style={cardBtnStyle}
            onClick={() => safeOpen(cfg.community.url)}
          >
            {cfg.community.buttonText}
          </button>
        </div>
      </div>

      {/* --------- MEDIA / GUIDES --------- */}
      <div className="card">
        <div className="cardHead">
          <div className="cardTitle">{cfg.media.title}</div>
        </div>
        <div className="cardBody">
          <button
            type="button"
            className="btn btn-ghost"
            style={{ ...cardBtnStyle, marginBottom: 10 }}
            onClick={() => safeOpen(cfg.media.channel.url)}
          >
            {cfg.media.channel.label}
          </button>

          {(cfg.media.guides || []).length > 0 ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {(cfg.media.guides || []).map((g) => (
                <a
                  key={g.title}
                  href={g.url}
                  target="_blank"
                  rel="noreferrer"
                  className="muted"
                  style={{
                    textDecoration: "underline",
                    overflowWrap: "anywhere",
                    fontWeight: 650,
                  }}
                >
                  {g.title}
                </a>
              ))}
            </div>
          ) : (
            <div className="muted">Скоро появятся гайды: первый запуск, камера, ROI, MQTT.</div>
          )}
        </div>
      </div>

      {/* --------- DONATE --------- */}
      <div className="card">
        <div className="cardHead">
          <div className="cardTitle">{cfg.donate.title}</div>
        </div>
        <div className="cardBody">
          <div className="muted" style={{ marginBottom: 10 }}>
            {cfg.donate.description}
          </div>

          {/* быстрые кнопки (url) */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: isMobile ? "1fr" : "1fr 1fr",
              gap: 10,
              marginBottom: 10,
            }}
          >
            {(cfg.donate.methods || [])
              .filter((m) => !!m.url && isHttpUrl(m.url))
              .map((m) => (
                <button
                  key={m.id}
                  type="button"
                  className="btn btn-primary"
                  style={cardBtnStyle}
                  onClick={() => safeOpen(m.url!)}
                >
                  {m.label}
                </button>
              ))}
          </div>

          {/* реквизиты в "аккордеоне" */}
          <details
            style={{
              border: "1px dashed rgba(255,255,255,.12)",
              borderRadius: 14,
              padding: 12,
              background: "rgba(255,255,255,.03)",
            }}
          >
            <summary style={{ cursor: "pointer", fontWeight: 800 }}>
              Показать реквизиты
            </summary>

            <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 10 }}>
              {donateHasAny ? (
                (cfg.donate.methods || [])
                  .filter((m) => !!m.value || !!m.url)
                  .map((m) => (
                    <div
                      key={m.id}
                      style={{
                        display: "flex",
                        gap: 10,
                        alignItems: "center",
                        justifyContent: "space-between",
                        flexWrap: "wrap",
                        border: "1px solid rgba(255,255,255,.06)",
                        borderRadius: 12,
                        padding: 10,
                        background: "rgba(0,0,0,.12)",
                      }}
                    >
                      <div style={{ minWidth: 140 }}>
                        <div style={{ fontWeight: 800 }}>{m.label}</div>
                        <div
                          className="muted mono"
                          style={{
                            marginTop: 4,
                            maxWidth: "100%",
                            overflowWrap: "anywhere",
                          }}
                        >
                          {m.value || m.url}
                        </div>
                      </div>

                      <div style={{ display: "flex", gap: 8, width: isMobile ? "100%" : "auto" }}>
                        {m.value && (
                          <button
                            type="button"
                            className="btn btn-ghost"
                            style={isMobile ? { width: "100%" } : undefined}
                            onClick={async () => {
                              try {
                                await navigator.clipboard.writeText(m.value!);
                                toast("Скопировано ✅");
                              } catch (e) {
                                toast("Не удалось скопировать");
                              }
                            }}
                          >
                            Скопировать
                          </button>
                        )}
                        {m.url && isHttpUrl(m.url) && (
                          <button
                            type="button"
                            className="btn btn-primary"
                            style={isMobile ? { width: "100%" } : undefined}
                            onClick={() => safeOpen(m.url!)}
                          >
                            Открыть
                          </button>
                        )}
                      </div>
                    </div>
                  ))
              ) : (
                <div className="muted">Реквизиты появятся позже (настраивается в help.json).</div>
              )}

              {cfg.donate.qr?.enabled && cfg.donate.qr.image ? (
                <div style={{ textAlign: "center" }}>
                  <div className="muted" style={{ marginBottom: 8 }}>
                    QR-код
                  </div>
                  <img
                    src={cfg.donate.qr.image}
                    alt="Donate QR"
                    style={{
                      maxWidth: "240px",
                      width: "100%",
                      height: "auto",
                      borderRadius: 14,
                      border: "1px solid rgba(255,255,255,.08)",
                    }}
                  />
                </div>
              ) : null}
            </div>
          </details>

          {/* подсказка */}
          {cfg.tips?.support ? (
            <div className="hint" style={{ marginTop: 10 }}>
              <div style={{ fontWeight: 800, marginBottom: 4 }}>Подсказка</div>
              <div className="muted" style={{ overflowWrap: "anywhere" }}>
                {cfg.tips.support}
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}