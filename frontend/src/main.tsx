import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { ConnectPage } from "./components/ConnectPage";
import { SharePage } from "./components/SharePage";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {window.location.pathname === "/connect" ? <ConnectPage />
      : window.location.pathname.startsWith("/share/") ? <SharePage token={decodeURIComponent(window.location.pathname.slice("/share/".length))} />
      : <App />}
  </StrictMode>,
);
