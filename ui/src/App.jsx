// =========================================================
// Файл: ui/src/App.jsx
// Проект: LPR GateBox UI
// Версия: v0.1
// Обновлено: 2026-02-08
//
// Что тут происходит (простыми словами):
// - Навигация по вкладкам БЕЗ react-router-dom
// - Переходы через history.pushState
// - Каждая вкладка = своя страница
// - Вкладка "Камера" открывает НОВУЮ страницу CameraPage
// =========================================================

import React, { useEffect, useMemo, useState } from "react";

// ---------------------------------------------------------
// Страницы (каждая — отдельный экран UI)
// ---------------------------------------------------------

import DashboardPage from "./pages/Dashboard";
import EventsPage from "./pages/Events";
import SettingsPage from "./pages/Settings";
import WhitelistPage from "./pages/Whitelist";
import SystemPage from "./pages/System";
import HelpPage from "./pages/Help";

// ⚠️ ВАЖНО
// Это НОВАЯ страница камеры, которую мы сделали
// Она умеет:
// - проверять RTSP
// - сохранять настройки камеры
import CameraPage from "./pages/CameraPage";

// ---------------------------------------------------------
// Определяем вкладку по URL
// Например:
//   /camera  -> tab = "camera"
//   /help    -> tab = "help"
// ---------------------------------------------------------
function tabFromPath(pathname) {
  const p = (pathname || "/").toLowerCase();

  if (p === "/help" || p.startsWith("/help/")) return "help";
  if (p === "/whitelist" || p.startsWith("/whitelist/")) return "whitelist";
  if (p === "/setup" || p.startsWith("/setup/")) return "whitelist";
  if (p === "/camera" || p.startsWith("/camera/")) return "camera";
  if (p === "/events" || p.startsWith("/events/")) return "events";
  if (p === "/settings" || p.startsWith("/settings/")) return "settings";
  if (p === "/system" || p.startsWith("/system/")) return "system";

  // по умолчанию — главная
  return "home";
}

// ---------------------------------------------------------
// Определяем URL по вкладке
// Например:
//   tab = "camera" -> /camera
// ---------------------------------------------------------
function pathFromTab(tab) {
  switch (tab) {
    case "help":
      return "/help";
    case "whitelist":
      return "/whitelist";
    case "camera":
      return "/camera";
    case "events":
      return "/events";
    case "settings":
      return "/settings";
    case "system":
      return "/system";
    case "home":
    default:
      return "/";
  }
}

// =========================================================
// ГЛАВНЫЙ КОМПОНЕНТ ПРИЛОЖЕНИЯ
// =========================================================
export default function App() {
  // Определяем стартовую вкладку по URL
  const initialTab = useMemo(
    () => tabFromPath(window.location.pathname),
    []
  );

  const [tab, setTab] = useState(initialTab);

  // -------------------------------------------------------
  // Обработка кнопок "назад / вперёд" в браузере
  // -------------------------------------------------------
  useEffect(() => {
    const onPop = () => setTab(tabFromPath(window.location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // -------------------------------------------------------
  // Переход между вкладками
  // -------------------------------------------------------
  function go(nextTab) {
    setTab(nextTab);

    const nextPath = pathFromTab(nextTab);
    if (window.location.pathname !== nextPath) {
      window.history.pushState({}, "", nextPath);
    }
  }

  // ======================================================
  // RENDER
  // ======================================================
  return (
    <div className="wrap">
      {/* ================= TOP BAR ================= */}
      <div className="topbar">
        <div>
          <div className="brandTitle">LPR GateBox v0.1</div>
          <div className="brandSub">
            Разработчик: Александр · Telegram-канал:{" "}
            <a
              className="brandLink"
              href="https://t.me/+1FZ-SJ5hs8phOTNi"
              target="_blank"
              rel="noreferrer"
            >
              @lpr_gatebox
            </a>
          </div>
        </div>

        {/* ---------- ВКЛАДКИ ---------- */}
        <div className="tabs">
          <button
            type="button"
            className={`tab ${tab === "home" ? "isActive" : ""}`}
            onClick={() => go("home")}
          >
            Главная
          </button>

          <button
            type="button"
            className={`tab ${tab === "whitelist" ? "isActive" : ""}`}
            onClick={() => go("whitelist")}
          >
            Белый список
          </button>

          <button
            type="button"
            className={`tab ${tab === "camera" ? "isActive" : ""}`}
            onClick={() => go("camera")}
          >
            Камера
          </button>

          <button
            type="button"
            className={`tab ${tab === "events" ? "isActive" : ""}`}
            onClick={() => go("events")}
          >
            События
          </button>

          <button
            type="button"
            className={`tab ${tab === "settings" ? "isActive" : ""}`}
            onClick={() => go("settings")}
          >
            Настройки
          </button>

          <button
            type="button"
            className={`tab ${tab === "system" ? "isActive" : ""}`}
            onClick={() => go("system")}
          >
            Система
          </button>

          <button
            type="button"
            className={`tab ${tab === "help" ? "isActive" : ""}`}
            onClick={() => go("help")}
          >
            Помощь
          </button>
        </div>
      </div>

      {/* ================= CONTENT ================= */}
      <div className="content">
        {tab === "home" && <DashboardPage />}
        {tab === "whitelist" && <WhitelistPage />}
        {tab === "camera" && <CameraPage />}
        {tab === "events" && <EventsPage />}
        {tab === "settings" && <SettingsPage />}
        {tab === "system" && <SystemPage />}
        {tab === "help" && <HelpPage />}
      </div>
    </div>
  );
}