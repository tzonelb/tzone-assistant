import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { Toaster } from "react-hot-toast";

import App from "./App";

import { AuthProvider } from "./contexts/AuthContext";
import { NotificationProvider } from "./contexts/NotificationContext";
import { ConversationLiveProvider } from "./contexts/ConversationLiveContext";
import { ThemeProvider } from "./contexts/ThemeContext";

import "./index.css";
import "./styles/theme.css";
import "./styles/global.css";
import "./styles/ui-kit.css";
import "./styles/table.css";
import "./styles/chat.css";
import "./styles/classical-styles.css";
import "./styles/tzone-theme.css";

// tzone-theme package (src/theme/) is intentionally NOT imported here.
// Its --tz-* custom properties collide with this app's own pre-existing
// --tz-* namespace (styles/theme.css, community/CommunityHubPage.css,
// etc.) — loading it globally silently changed values like --tz-space-4
// app-wide and broke real page layouts (e.g. Publish). /theme-preview
// loads it scoped to itself instead — see ThemePreviewPage.jsx.

import { initAndroidShell } from "./native/androidShell";

initAndroidShell();

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <BrowserRouter>
      <ThemeProvider>
        <AuthProvider>
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
        </AuthProvider>
      </ThemeProvider>
    </BrowserRouter>
  </StrictMode>
);