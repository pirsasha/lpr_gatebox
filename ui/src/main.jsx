/**
 * FILE: ui/src/main.jsx
 * UPDATED: 2026-02-01 20:25 (+0300)
 * WHAT: Точка входа React. Импортируем App.css.
 */

import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import "./App.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
