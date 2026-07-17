import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router-dom";
import "./theme.css";
import "./app.css";
import { App } from "./App";
import { PlayerProvider } from "./lib/player";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <HashRouter>
      <PlayerProvider>
        <App />
      </PlayerProvider>
    </HashRouter>
  </StrictMode>,
);
