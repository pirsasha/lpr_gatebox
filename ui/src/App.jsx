// ui/src/App.jsx
// LPR_GATEBOX UI
// Версия: v0.2.4-fix4
// Обновлено: 2026-02-07
//
// Что исправлено:
// - FIX: опечатка page -> tab (чёрный экран)
// - Навигация по вкладкам без react-router-dom
// - Классы приведены под App.css (topbar/tabs/tab/isActive)

import React, { useState } from "react";

import DashboardPage from "./pages/Dashboard";
import CameraPage from "./pages/Camera";
import EventsPage from "./pages/Events";
import SettingsPage from "./pages/Settings";
import QuickSetupPage from "./pages/QuickSetup";
import SystemPage from "./pages/System";

export default function App() {
  const [tab, setTab] = useState("home");

  return (
    <div className="wrap">
      {/* ---------- TOP BAR ---------- */}
      <div className="topbar">
        <div>
          <div className="brandTitle">LPR GateBox</div>
          <div className="brandSub">
            Панель управления: всё по-русски и без лишних слов
          </div>
        </div>

        <div className="tabs">
          <button
            type="button"
            className={`tab ${tab === "home" ? "isActive" : ""}`}
            onClick={() => setTab("home")}
          >
            Главная
          </button>

          <button
            type="button"
            className={`tab ${tab === "setup" ? "isActive" : ""}`}
            onClick={() => setTab("setup")}
          >
            Быстрая настройка
          </button>

          <button
            type="button"
            className={`tab ${tab === "camera" ? "isActive" : ""}`}
            onClick={() => setTab("camera")}
          >
            Камера
          </button>

          <button
            type="button"
            className={`tab ${tab === "events" ? "isActive" : ""}`}
            onClick={() => setTab("events")}
          >
            События
          </button>

          <button
            type="button"
            className={`tab ${tab === "settings" ? "isActive" : ""}`}
            onClick={() => setTab("settings")}
          >
            Настройки
          </button>

          <button
            type="button"
            className={`tab ${tab === "system" ? "isActive" : ""}`}
            onClick={() => setTab("system")}
          >
            Система
          </button>
        </div>
      </div>

      {/* ---------- CONTENT ---------- */}
      <div className="content">
        {tab === "home" && <DashboardPage />}
        {tab === "setup" && <QuickSetupPage />}
        {tab === "camera" && <CameraPage />}
        {tab === "events" && <EventsPage />}
        {tab === "settings" && <SettingsPage />}
        {tab === "system" && <SystemPage />}
      </div>
    </div>
  );
}