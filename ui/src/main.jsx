/**
 * FILE: ui/src/main.jsx
 * UPDATED: 2026-02-20 21:xx (+0300)
 * WHAT: React entrypoint. Mount to #root (preferred) or legacy #r.
 */

import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import "./App.css";

const el = document.getElementById("root") || document.getElementById("r");

if (!el) {
  // Failsafe: show error instead of blank screen
  document.body.innerHTML =
    "<pre style='color:#fff;padding:16px'>UI boot failed: mount element #root/#r not found</pre>";
} else {
  ReactDOM.createRoot(el).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}