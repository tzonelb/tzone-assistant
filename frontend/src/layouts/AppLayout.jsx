import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  CloseOutlined,
  NotificationsActiveOutlined,
  NotificationsOffOutlined,
} from "@mui/icons-material";

import {
  Outlet,
  useLocation,
  useNavigate,
} from "react-router-dom";

import Sidebar from "../components/layout/Sidebar";
import Topbar from "../components/layout/Topbar";
import SidebarV2 from "../components/layout/SidebarV2";
import TopbarV2 from "../components/layout/TopbarV2";
import { isUiV2Enabled } from "../config/featureFlags";
import "./AppLayoutV2.css";
import { useAuth } from "../contexts/AuthContext";
import { readNotificationPreferences } from "../utils/notificationPreferences";

import {
  getConversationsRequest,
} from "../api/client";


const pageTitles = {
  "/dashboard": "Dashboard",
  "/notifications": "Notification Center",
  "/conversations": "Conversations",
  "/comments": "Comments",
  "/customers": "Customers",
  "/broadcast": "Broadcast",
  "/appointments": "Appointments",
  "/tasks": "Tasks",
  "/catalogue": "Catalogue",
  "/scheduler": "Scheduler",
  "/team-chat": "Team Chat",
  "/knowledge": "Knowledge Base",
  "/ai-teaching": "AI Teaching",
  "/analytics": "Analytics",
  "/channels": "Channels",
  "/settings": "Preferences",
  "/company-settings": "Company Settings",
  "/roles": "Roles & Permissions",
};


function resolvePageTitle(
  pathname,
) {
  if (pathname.startsWith("/conversations")) return "Conversations";
  if (pathname.startsWith("/company-settings")) return "Company Settings";
  if (pathname.startsWith("/broadcast")) return "Broadcast";

  return (
    pageTitles[pathname]
    || "T-ZONE Platform"
  );
}


function messageKey(
  conversation,
) {
  return [
    conversation.channel,
    conversation.external_user_id,
    conversation.updated_at,
    conversation.last_message,
  ].join("::");
}


function isIncomingConversation(
  conversation,
) {
  const direction = String(
    conversation.last_direction
    || "",
  ).toLowerCase();

  return [
    "in",
    "incoming",
    "customer",
  ].includes(direction);
}


function playNotificationSound() {
  try {
    const AudioContextClass =
      window.AudioContext
      || window.webkitAudioContext;

    if (!AudioContextClass) {
      return;
    }

    const context =
      new AudioContextClass();

    const oscillator =
      context.createOscillator();

    const gain =
      context.createGain();

    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(
      880,
      context.currentTime,
    );

    gain.gain.setValueAtTime(
      0.0001,
      context.currentTime,
    );

    gain.gain.exponentialRampToValueAtTime(
      0.18,
      context.currentTime + 0.02,
    );

    gain.gain.exponentialRampToValueAtTime(
      0.0001,
      context.currentTime + 0.28,
    );

    oscillator.connect(gain);
    gain.connect(
      context.destination,
    );

    oscillator.start();
    oscillator.stop(
      context.currentTime + 0.3,
    );

    oscillator.addEventListener(
      "ended",
      () => {
        context.close();
      },
      {
        once: true,
      },
    );
  } catch {
    // Sound failure must not break the dashboard.
  }
}


function formatChannel(
  value,
) {
  return String(value || "")
    .replaceAll("_", " ")
    .replace(
      /\b\w/g,
      (letter) =>
        letter.toUpperCase(),
    );
}


export default function AppLayout() {
  // Which shell to render. The redesigned one is the default; a user who turned
  // it off for their own browser keeps the previous layout. The custom event is
  // what makes the switch take effect in the tab that made it -- localStorage's
  // own "storage" event only fires in other tabs.
  const [uiV2, setUiV2] = useState(isUiV2Enabled);

  useEffect(() => {
    function handleFlagChange() {
      setUiV2(isUiV2Enabled());
    }

    window.addEventListener("tzone:ui-v2-changed", handleFlagChange);
    window.addEventListener("storage", handleFlagChange);

    return () => {
      window.removeEventListener("tzone:ui-v2-changed", handleFlagChange);
      window.removeEventListener("storage", handleFlagChange);
    };
  }, []);

  useEffect(() => {
    const applyAppearance = () => {
      const font = localStorage.getItem("tzone_ui_font");
      const size = localStorage.getItem("tzone_ui_font_size");
      const density = localStorage.getItem("tzone_ui_density") || "comfortable";
      const preference = localStorage.getItem("tzone_ui_theme") || "light";
      const resolvedTheme = preference === "auto"
        ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
        : preference;
      if (font) document.documentElement.style.fontFamily = font;
      if (size) document.documentElement.style.fontSize = `${size}%`;
      document.body.dataset.uiDensity = density;
      document.documentElement.dataset.theme = resolvedTheme;
      document.documentElement.style.colorScheme = resolvedTheme;
    };

    const media = window.matchMedia("(prefers-color-scheme: dark)");
    applyAppearance();
    window.addEventListener("storage", applyAppearance);
    window.addEventListener("tzone:appearance-changed", applyAppearance);
    media.addEventListener?.("change", applyAppearance);
    return () => {
      window.removeEventListener("storage", applyAppearance);
      window.removeEventListener("tzone:appearance-changed", applyAppearance);
      media.removeEventListener?.("change", applyAppearance);
    };
  }, []);
  const [
    sidebarOpen,
    setSidebarOpen,
  ] = useState(false);

  const [
    sidebarCollapsed,
    setSidebarCollapsed,
  ] = useState(() => (
    window.localStorage.getItem(
      "tzone_sidebar_collapsed",
    ) === "true"
  ));

  const [
    notificationPermission,
    setNotificationPermission,
  ] = useState(
    typeof Notification !== "undefined"
      ? Notification.permission
      : "unsupported",
  );

  const [
    popupNotification,
    setPopupNotification,
  ] = useState(null);

  const initializedRef =
    useRef(false);

  const knownMessagesRef =
    useRef(new Map());

  const popupTimerRef =
    useRef(null);

  const location =
    useLocation();

  const navigate =
    useNavigate();

  const { companies, user } =
    useAuth();

  const [notificationPreferences, setNotificationPreferences] = useState(() => readNotificationPreferences(user));

  useEffect(() => {
    setNotificationPreferences(readNotificationPreferences(user));
    const handleSettingsChanged = (event) => setNotificationPreferences(event?.detail || readNotificationPreferences(user));
    window.addEventListener("tzone:notification-settings-changed", handleSettingsChanged);
    return () => window.removeEventListener("tzone:notification-settings-changed", handleSettingsChanged);
  }, [user]);

  const pageTitle =
    useMemo(
      () =>
        resolvePageTitle(
          location.pathname,
        ),
      [
        location.pathname,
      ],
    );

  const companyName =
    companies?.[0]?.name
    || "T-ZONE";

  const toggleSidebarCollapsed =
    useCallback(() => {
      setSidebarCollapsed(
        (currentValue) => {
          const nextValue =
            !currentValue;

          window.localStorage.setItem(
            "tzone_sidebar_collapsed",
            String(nextValue),
          );

          return nextValue;
        },
      );
    }, []);


  const openConversation =
    useCallback(
      (
        channel,
        externalUserId,
      ) => {
        navigate(
          `/conversations/${encodeURIComponent(
            channel,
          )}/${encodeURIComponent(
            externalUserId,
          )}`,
        );
      },
      [
        navigate,
      ],
    );


  const showPopup =
    useCallback(
      (conversation) => {
        const notificationData = {
          id: messageKey(
            conversation,
          ),
          title:
            conversation.customer_name
            || `${formatChannel(
              conversation.channel,
            )} customer`,
          channel:
            formatChannel(
              conversation.channel,
            ),
          text:
            conversation.last_message
            || "New customer message",
          raw: conversation,
        };

        if (!notificationPreferences.enabled) return;
        if (!notificationPreferences.channels?.[String(conversation.channel || "").toLowerCase()]) return;

        const activeConversationPath = `/conversations/${encodeURIComponent(conversation.channel)}/${encodeURIComponent(conversation.external_user_id)}`;
        const suppressForActive = notificationPreferences.suppressActiveConversation && location.pathname === activeConversationPath;

        if (notificationPreferences.inAppPopup && !suppressForActive) {
          setPopupNotification(notificationData);
          if (popupTimerRef.current) window.clearTimeout(popupTimerRef.current);
          popupTimerRef.current = window.setTimeout(() => setPopupNotification(null), 9000);
        }

        if (notificationPreferences.sound && !suppressForActive) playNotificationSound();

        if (
          notificationPreferences.desktop
          && !suppressForActive
          && typeof Notification !== "undefined"
          && Notification.permission === "granted"
        ) {
          const browserNotification =
            new Notification(
              notificationData.title,
              {
                body:
                  `${notificationData.channel}: ` +
                  notificationData.text,
                tag:
                  notificationData.id,
                renotify: true,
                silent: true,
              },
            );

          browserNotification.onclick =
            () => {
              window.focus();

              openConversation(
                conversation.channel,
                conversation.external_user_id,
              );

              browserNotification.close();
            };
        }
      },
      [
        openConversation,
        notificationPreferences,
        location.pathname,
      ],
    );


  const checkForNewMessages =
    useCallback(
      async () => {
        try {
          const result =
            await getConversationsRequest({
              channel: "all",
              status: "all",
              department: "all",
              page: 1,
              pageSize: 100,
            });

          const conversations =
            Array.isArray(
              result?.items,
            )
              ? result.items
              : [];

          const currentMessages =
            new Map();

          conversations.forEach(
            (conversation) => {
              const conversationId =
                `${conversation.channel}:` +
                conversation.external_user_id;

              const currentKey =
                messageKey(
                  conversation,
                );

              currentMessages.set(
                conversationId,
                currentKey,
              );

              if (
                !initializedRef.current
              ) {
                return;
              }

              const previousKey =
                knownMessagesRef
                  .current
                  .get(
                    conversationId,
                  );

              if (
                previousKey
                && previousKey
                  !== currentKey
                && isIncomingConversation(
                  conversation,
                )
              ) {
                showPopup(
                  conversation,
                );
              }
            },
          );

          knownMessagesRef.current =
            currentMessages;

          initializedRef.current =
            true;
        } catch {
          // Notification polling should never break the main interface.
        }
      },
      [
        showPopup,
      ],
    );


  useEffect(() => {
    checkForNewMessages();

    const interval =
      window.setInterval(
        checkForNewMessages,
        5000,
      );

    return () => {
      window.clearInterval(
        interval,
      );
    };
  }, [
    checkForNewMessages,
  ]);


  useEffect(() => {
    return () => {
      if (
        popupTimerRef.current
      ) {
        window.clearTimeout(
          popupTimerRef.current,
        );
      }
    };
  }, []);


  async function enableNotifications() {
    if (
      typeof Notification
        === "undefined"
    ) {
      setNotificationPermission(
        "unsupported",
      );

      return;
    }

    try {
      const permission =
        await Notification
          .requestPermission();

      setNotificationPermission(
        permission,
      );

      if (
        permission === "granted"
      ) {
        playNotificationSound();

        setPopupNotification({
          id:
            "notifications-enabled",
          title:
            "Notifications enabled",
          channel:
            "T-ZONE",
          text:
            "New customer messages will appear here with sound.",
          raw: null,
        });

        if (
          popupTimerRef.current
        ) {
          window.clearTimeout(
            popupTimerRef.current,
          );
        }

        popupTimerRef.current =
          window.setTimeout(
            () => {
              setPopupNotification(
                null,
              );
            },
            5000,
          );
      }
    } catch {
      setNotificationPermission(
        "denied",
      );
    }
  }


  function handlePopupClick() {
    const conversation =
      popupNotification?.raw;

    if (!conversation) {
      setPopupNotification(
        null,
      );

      return;
    }

    openConversation(
      conversation.channel,
      conversation.external_user_id,
    );

    setPopupNotification(
      null,
    );
  }


  const companySettingsMode = location.pathname.startsWith("/company-settings");
  const standaloneSettingsMode = companySettingsMode || location.pathname === "/settings";

  const SidebarComponent = uiV2 ? SidebarV2 : Sidebar;
  const TopbarComponent = uiV2 ? TopbarV2 : Topbar;

  return (
    <div
      className={
        uiV2
          ? "tzv2 app-layout-v2"
          : `app-layout ${standaloneSettingsMode ? "company-settings-mode" : ""} ${
          sidebarCollapsed
            ? "app-layout-sidebar-collapsed"
            : ""
        }`
      }
    >
      {!standaloneSettingsMode ? <SidebarComponent
        open={sidebarOpen}
        collapsed={sidebarCollapsed}
        companyName={companyName}
        onClose={() =>
          setSidebarOpen(false)
        }
        // SidebarV2 renders its own collapse control and calls this. Without
        // it the button is drawn and does nothing.
        onToggleCollapsed={toggleSidebarCollapsed}
      /> : null}

      <div className={uiV2 ? "app-main-v2" : "app-main"}>
        {!standaloneSettingsMode ? <TopbarComponent
          title={pageTitle}
          sidebarCollapsed={
            sidebarCollapsed
          }
          onOpenSidebar={() =>
            setSidebarOpen(true)
          }
          onToggleSidebar={
            toggleSidebarCollapsed
          }
        /> : null}

        {notificationPreferences.desktop && notificationPermission
          !== "granted" ? (
          <button
            type="button"
            onClick={
              enableNotifications
            }
            title="Enable message notifications"
            style={{
              position: "fixed",
              zIndex: 1100,
              right: "20px",
              bottom: "20px",
              display: "inline-flex",
              alignItems: "center",
              gap: "8px",
              padding: "10px 14px",
              border: "1px solid #d9e2ef",
              borderRadius: "12px",
              color: "#0b6fce",
              background: "#ffffff",
              boxShadow:
                "0 10px 30px rgba(15, 42, 75, 0.16)",
              fontWeight: 800,
              cursor: "pointer",
            }}
          >
            {notificationPermission
              === "denied" ? (
                <NotificationsOffOutlined />
              ) : (
                <NotificationsActiveOutlined />
              )}

            {notificationPermission
              === "denied"
                ? "Notifications blocked"
                : "Enable notifications"}
          </button>
        ) : null}

        {popupNotification ? (
          <div
            role="button"
            tabIndex={0}
            onClick={
              handlePopupClick
            }
            onKeyDown={(
              event,
            ) => {
              if (
                event.key === "Enter"
                || event.key === " "
              ) {
                handlePopupClick();
              }
            }}
            style={{
              position: "fixed",
              zIndex: 1200,
              right: "22px",
              top: "90px",
              width: "min(390px, calc(100vw - 44px))",
              display: "grid",
              gridTemplateColumns:
                "46px minmax(0, 1fr) auto",
              gap: "12px",
              alignItems: "center",
              padding: "14px",
              border: "1px solid #d9e2ef",
              borderRadius: "16px",
              color: "#10233f",
              background: "#ffffff",
              boxShadow:
                "0 18px 50px rgba(15, 42, 75, 0.22)",
              cursor:
                popupNotification.raw
                  ? "pointer"
                  : "default",
            }}
          >
            <div
              style={{
                width: "46px",
                height: "46px",
                display: "grid",
                placeItems: "center",
                borderRadius: "14px",
                color: "#ffffff",
                background:
                  "linear-gradient(135deg, #1689e8, #18bfa4)",
              }}
            >
              <NotificationsActiveOutlined />
            </div>

            <div
              style={{
                minWidth: 0,
              }}
            >
              <strong
                style={{
                  display: "block",
                  overflow: "hidden",
                  fontSize: "14px",
                  textOverflow:
                    "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {popupNotification.title}
              </strong>

              <span
                style={{
                  display: "block",
                  marginTop: "2px",
                  color: "#708198",
                  fontSize: "10px",
                  fontWeight: 800,
                  textTransform:
                    "uppercase",
                }}
              >
                {popupNotification.channel}
              </span>

              <p
                style={{
                  margin: "6px 0 0",
                  overflow: "hidden",
                  color: "#34465e",
                  fontSize: "12px",
                  lineHeight: 1.45,
                  textOverflow:
                    "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {popupNotification.text}
              </p>
            </div>

            <button
              type="button"
              title="Close notification"
              onClick={(
                event,
              ) => {
                event.stopPropagation();

                setPopupNotification(
                  null,
                );
              }}
              style={{
                width: "32px",
                height: "32px",
                display: "grid",
                placeItems: "center",
                border: 0,
                borderRadius: "9px",
                color: "#708198",
                background: "#f1f5f9",
                cursor: "pointer",
              }}
            >
              <CloseOutlined
                fontSize="small"
              />
            </button>
          </div>
        ) : null}

        <main className={uiV2
          ? `app-content-v2 ${location.pathname.startsWith("/conversations") ? "app-content-workspace-v2" : ""}`
          : `app-content ${location.pathname.startsWith("/conversations") ? "app-content-workspace" : "app-content-scroll"}`
        }>
          <Outlet />
        </main>
      </div>
    </div>
  );
}