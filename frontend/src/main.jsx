import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { Toaster } from "react-hot-toast";

import App from "./App";

import { AuthProvider } from "./contexts/AuthContext";
import { NotificationProvider } from "./contexts/NotificationContext";
import { ConversationLiveProvider } from "./contexts/ConversationLiveContext";

import "./index.css";
import "./styles/theme.css";
import "./styles/global.css";
import "./styles/ui-kit.css";
import "./styles/table.css";
import "./styles/chat.css";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <BrowserRouter>
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
    </BrowserRouter>
  </StrictMode>
);