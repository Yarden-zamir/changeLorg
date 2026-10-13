import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { ConnectPage } from "./components/ConnectPage";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {window.location.pathname === "/connect" ? <ConnectPage /> : <App />}
  </StrictMode>,
);
