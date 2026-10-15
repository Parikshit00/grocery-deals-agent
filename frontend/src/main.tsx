import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import { ReplayBanner } from "./components/ReplayBanner";
import "./index.css";
import { REPLAY } from "./lib/api";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {REPLAY ? <ReplayBanner /> : null}
    <App />
  </React.StrictMode>,
);
