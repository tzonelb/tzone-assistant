import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { bootstrap } from "./core/bootstrap";
import { I18nProvider } from "./core/i18n";
import { installModules } from "./core/registry";
import { syncEngine } from "./core/sync";
import { ALL_MODULES } from "./modules";
import "./styles.css";

async function start() {
  // 1. Assemble the application from its modules — this decides the schema, the routes and the
  //    menu, so it must happen before anything touches the database or renders.
  installModules(ALL_MODULES);

  // 2. Open the local database and let each module seed what it needs.
  await bootstrap();

  // 3. Replicate in the background. Everything already works without this step.
  syncEngine.start();

  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <I18nProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </I18nProvider>
    </StrictMode>,
  );

  if ("serviceWorker" in navigator && import.meta.env.PROD) {
    void navigator.serviceWorker.register("/sw.js");
  }
}

void start();
