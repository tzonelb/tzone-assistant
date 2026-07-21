import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  getConversationsRequest,
  subscribeConversationEvents,
} from "../api/client";
import { useAuth } from "./AuthContext";

const ConversationLiveContext = createContext(null);
const POLL_INTERVAL_MS = 10000;
const RECONNECT_MIN_MS = 1500;
const RECONNECT_MAX_MS = 30000;

function normalizeItems(payload) {
  return Array.isArray(payload?.items) ? payload.items : [];
}

export function ConversationLiveProvider({ children }) {
  const { authenticated } = useAuth();
  const [items, setItems] = useState([]);
  const [connected, setConnected] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState(null);
  const [error, setError] = useState("");
  const mountedRef = useRef(true);
  const refreshInFlightRef = useRef(false);
  const listenersRef = useRef(new Set());

  const publish = useCallback((event) => {
    listenersRef.current.forEach((listener) => {
      try { listener(event); } catch { /* listeners are isolated */ }
    });
  }, []);

  const refresh = useCallback(async ({ silent = true } = {}) => {
    if (!authenticated || refreshInFlightRef.current) return null;
    refreshInFlightRef.current = true;
    if (!silent) setRefreshing(true);
    try {
      const result = await getConversationsRequest({
        channel: "all",
        status: "all",
        department: "all",
        folder: "inbox",
        page: 1,
        pageSize: 100,
      });
      if (!mountedRef.current) return result;
      const nextItems = normalizeItems(result);
      setItems(nextItems);
      setLastUpdatedAt(new Date().toISOString());
      setError("");
      publish({ type: "snapshot", items: nextItems, payload: result });
      return result;
    } catch (requestError) {
      if (mountedRef.current) setError(requestError?.message || "Live conversations could not be refreshed.");
      return null;
    } finally {
      refreshInFlightRef.current = false;
      if (mountedRef.current) setRefreshing(false);
    }
  }, [authenticated, publish]);

  const subscribe = useCallback((listener) => {
    listenersRef.current.add(listener);
    return () => listenersRef.current.delete(listener);
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    if (!authenticated) {
      setItems([]);
      setConnected(false);
      setError("");
      return undefined;
    }

    let stopped = false;
    let reconnectDelay = RECONNECT_MIN_MS;
    let reconnectTimer = null;
    let controller = null;

    const connect = async () => {
      if (stopped) return;
      controller = new AbortController();
      await subscribeConversationEvents({
        signal: controller.signal,
        onOpen: () => {
          if (stopped) return;
          reconnectDelay = RECONNECT_MIN_MS;
          setConnected(true);
          setError("");
        },
        onError: (streamError) => {
          if (stopped) return;
          setConnected(false);
          // Polling remains active; do not show a permanent reconnecting error to users.
          if (!navigator.onLine) {
            setError(streamError?.message || "Network connection is unavailable.");
          }
        },
        onEvent: ({ event, data }) => {
          if (stopped) return;
          publish({ type: "stream", event, data });
          if (data?.type === "conversations_updated") {
            const nextItems = Array.isArray(data.items) ? data.items : [];
            if (nextItems.length) {
              setItems(nextItems);
              setLastUpdatedAt(new Date().toISOString());
              publish({ type: "snapshot", items: nextItems, payload: data });
            } else {
              refresh({ silent: true });
            }
          }
        },
      });

      if (!stopped) {
        setConnected(false);
        reconnectTimer = window.setTimeout(connect, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
      }
    };

    refresh({ silent: false });
    connect();
    const pollTimer = window.setInterval(() => refresh({ silent: true }), POLL_INTERVAL_MS);

    const onVisibility = () => {
      if (document.visibilityState === "visible") refresh({ silent: true });
    };
    const onOnline = () => refresh({ silent: true });
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("online", onOnline);

    return () => {
      stopped = true;
      controller?.abort();
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      window.clearInterval(pollTimer);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("online", onOnline);
      setConnected(false);
    };
  }, [authenticated, publish, refresh]);

  const value = useMemo(() => ({
    items,
    connected,
    refreshing,
    lastUpdatedAt,
    error,
    refresh,
    subscribe,
  }), [items, connected, refreshing, lastUpdatedAt, error, refresh, subscribe]);

  return (
    <ConversationLiveContext.Provider value={value}>
      {children}
    </ConversationLiveContext.Provider>
  );
}

export function useConversationLive() {
  const context = useContext(ConversationLiveContext);
  if (!context) throw new Error("useConversationLive must be used inside ConversationLiveProvider.");
  return context;
}
