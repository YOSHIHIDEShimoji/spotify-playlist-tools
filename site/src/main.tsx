import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router-dom";
import "./theme.css";
import "./app.css";
import { App } from "./App";
import { PlayerProvider } from "./lib/player";
import { adoptPatFromUrl } from "./lib/pat";

// URL（#token=...）で渡された操作トークンを取り込んでから描画する（スマホ用・HashRouter 初期化前に実行）。
adoptPatFromUrl();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <HashRouter>
      <PlayerProvider>
        <App />
      </PlayerProvider>
    </HashRouter>
  </StrictMode>,
);
