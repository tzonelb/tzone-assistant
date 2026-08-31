import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { Toaster } from "react-hot-toast";

import App from "./App";

import { AuthProvider } from "./contexts/AuthContext";
import { NotificationProvider } from "./contexts/NotificationContext";
import { ConversationLiveProvider } from "./contexts/ConversationLiveContext";
import { WorkspaceConfigProvider } from "./contexts/WorkspaceConfigContext";
import { ThemeProvider } from "./contexts/ThemeContext";

import "./index.css";
import "./styles/theme.css";
import "./styles/global.css";
import "./styles/ui-kit.css";
import "./styles/table.css";
import "./styles/chat.css";
import "./styles/classical-styles.css";
import "./styles/tzone-theme.css";

async function boot() {
  // Demo mode is compiled in only when the bundle is built with VITE_DEMO_MODE=1
  // (the preview site). Vite resolves the condition at build time, so the
  // production bundle contains neither this branch nor the fixtures behind it.
  if (import.meta.env.VITE_DEMO_MODE) {
    const { installDemoMode } = await import("./demo/index.js");
    installDemoMode();
  }

  createRoot(document.getElementById("root")).render(
    <StrictMode>
      <BrowserRouter basename={import.meta.env.BASE_URL}>
        <ThemeProvider>
        <AuthProvider>
          <WorkspaceConfigProvider>
            <NotificationProvider>
              <ConversationLiveProvider>
                <App />

                <Toaster
                  position="top-right"
                  toastOptions={{
                    duration: 3500,
                    style: {
                      borderRadius: "14px",
                      fontWeight: 700,
                    },
                  }}
                />
              </ConversationLiveProvider>
            </NotificationProvider>
          </WorkspaceConfigProvider>
        </AuthProvider>
        </ThemeProvider>
      </BrowserRouter>
    </StrictMode>
  );
}

boot();
