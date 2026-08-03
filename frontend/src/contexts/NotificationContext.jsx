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
  clearVisibleNotificationsRequest,
  getNotificationsRequest,
  getNotificationSummaryRequest,
  markAllNotificationsReadRequest,
  markNotificationReadRequest,
  markNotificationUnreadRequest,
} from "../api/client";
import { useAuth } from "./AuthContext";
import { readNotificationPreferences } from "../utils/notificationPreferences";

function playNotificationSound() {
  try {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return;
    const ctx = new AudioContextClass();
    const oscillator = ctx.createOscillator();
    const gain = ctx.createGain();
    oscillator.type = "sine";
    oscillator.frequency.value = 880;
    gain.gain.setValueAtTime(0.0001, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.15, ctx.currentTime + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.25);
    oscillator.connect(gain);
    gain.connect(ctx.destination);
    oscillator.start();
    oscillator.stop(ctx.currentTime + 0.25);
    oscillator.onended = () => ctx.close();
  } catch {
    // Sound is a nice-to-have; never let it break notification loading.
  }
}

const NotificationContext = createContext(null);
const POLL_INTERVAL_MS = 15000;

function normalizeSummary(payload, fallbackItems = []) {
  const unread = Number(
    payload?.unread
      ?? fallbackItems.filter((item) => !item?.is_read).length,
  );
  const total = Number(payload?.total ?? fallbackItems.length);
  const read = Number(payload?.read ?? Math.max(total - unread, 0));
  return {
    unread: Number.isFinite(unread) ? unread : 0,
    total: Number.isFinite(total) ? total : 0,
    read: Number.isFinite(read) ? read : 0,
  };
}

function notificationIdentity(item) {
  return String(
    item?.id
      ?? item?.notification_id
      ?? `${item?.created_at}:${item?.title}`,
  );
}

function groupIds(item) {
  const ids = item?.data?.group_notification_ids;
  if (Array.isArray(ids) && ids.length) {
    return ids.map(Number).filter(Number.isFinite);
  }
  const id = Number(item?.id);
  return Number.isFinite(id) ? [id] : [];
}

function groupUnreadCount(item) {
  const groupItems = item?.data?.group_items;
  if (Array.isArray(groupItems) && groupItems.length) {
    return groupItems.filter((entry) => !entry?.is_read).length;
  }
  return item?.is_read ? 0 : groupIds(item).length;
}

function itemTouchesIds(item, targetIds) {
  return groupIds(item).some((id) => targetIds.has(id));
}

export function NotificationProvider({ children }) {
  const { authenticated, user } = useAuth();
  const [items, setItems] = useState([]);
  const [summary, setSummary] = useState({ unread: 0, total: 0, read: 0 });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const inFlightRef = useRef(false);
  const pendingRefreshRef = useRef(false);

  const seenIdsRef = useRef(null);

  // Same preferences the desktop-popup path (AppLayout.jsx) already
  // respects - the bell used to poll and play a sound completely
  // unfiltered, ignoring a user's own "mute this channel" / "no sound"
  // settings.
  // Remembers the last explicitly-requested filter set so that silent,
  // filter-less refreshes (the 15s background poll, the focus/visibility
  // listener, and the optimistic refresh inside markRead/markUnread/
  // markAllRead) don't clobber whatever the Notification Center page has
  // currently filtered to. Without this, ticking "Unread only" (or picking
  // a type/channel/date) would silently revert to showing everything again
  // within one poll interval, even though the filter control still looked
  // active.
  const filtersRef = useRef({});
  const [preferences, setPreferences] = useState(() => readNotificationPreferences(user));
  useEffect(() => {
    setPreferences(readNotificationPreferences(user));
    const handleSettingsChanged = (event) => setPreferences(event?.detail || readNotificationPreferences(user));
    window.addEventListener("tzone:notification-settings-changed", handleSettingsChanged);
    return () => window.removeEventListener("tzone:notification-settings-changed", handleSettingsChanged);
  }, [user]);
  const preferencesRef = useRef(preferences);
  preferencesRef.current = preferences;

  const refresh = useCallback(async ({ silent = false, filters } = {}) => {
    if (!authenticated) return;
    // A filter change while a background poll is already in flight used to
    // bail out here BEFORE recording the new filter, so filtersRef stayed
    // stale until the user touched a control again. Record it unconditionally
    // now, and re-run once the in-flight request settles so the change the
    // user just made actually takes effect promptly, not on the next poll.
    const effectiveFilters = filters !== undefined ? filters : filtersRef.current;
    const filtersChanged = filters !== undefined && JSON.stringify(filters) !== JSON.stringify(filtersRef.current);
    filtersRef.current = effectiveFilters;
    if (inFlightRef.current) {
      if (filtersChanged) pendingRefreshRef.current = true;
      return;
    }
    inFlightRef.current = true;
    if (!silent) setLoading(true);
    try {
      const [rawItems, summaryPayload] = await Promise.all([
        getNotificationsRequest({ pageSize: 100, ...effectiveFilters }),
        getNotificationSummaryRequest(),
      ]);
      const prefs = preferencesRef.current;
      const normalizedItems = (Array.isArray(rawItems) ? rawItems : []).filter((item) => {
        const channel = String(item?.channel || "").toLowerCase();
        return !channel || prefs.channels?.[channel] !== false;
      });

      const currentUnreadIds = new Set(
        normalizedItems.filter((item) => !item?.is_read).map(notificationIdentity),
      );
      if (seenIdsRef.current !== null && prefs.enabled && prefs.sound) {
        const hasNewUnread = [...currentUnreadIds].some(
          (id) => !seenIdsRef.current.has(id),
        );
        if (hasNewUnread) playNotificationSound();
      }
      seenIdsRef.current = currentUnreadIds;

      setItems(normalizedItems);
      setSummary(normalizeSummary(summaryPayload, normalizedItems));
      setError("");
    } catch (requestError) {
      if (!silent) {
        setError(requestError?.message || "Failed to load notifications.");
      }
    } finally {
      inFlightRef.current = false;
      if (!silent) setLoading(false);
      if (pendingRefreshRef.current) {
        pendingRefreshRef.current = false;
        refresh({ silent: true });
      }
    }
  }, [authenticated]);

  useEffect(() => {
    if (!authenticated) {
      setItems([]);
      setSummary({ unread: 0, total: 0, read: 0 });
      seenIdsRef.current = null;
      return undefined;
    }

    refresh();
    const intervalId = window.setInterval(
      () => refresh({ silent: true }),
      POLL_INTERVAL_MS,
    );
    const handleVisibility = () => {
      if (document.visibilityState === "visible") {
        refresh({ silent: true });
      }
    };
    window.addEventListener("focus", handleVisibility);
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      window.clearInterval(intervalId);
      window.removeEventListener("focus", handleVisibility);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [authenticated, refresh]);

  const markRead = useCallback(async (item) => {
    const id = item?.id ?? item;
    const target = typeof item === "object"
      ? item
      : items.find(
        (entry) => notificationIdentity(entry) === String(id),
      );
    if (!target || target.is_read) return;

    const ids = groupIds(target);
    const targetIds = new Set(ids);
    const unreadDelta = Math.max(1, groupUnreadCount(target));
    const readAt = new Date().toISOString();

    // Update immediately: the bell popup shows unread notifications only,
    // so the clicked item disappears without waiting for a network refresh.
    setItems((current) => current.map((entry) => (
      itemTouchesIds(entry, targetIds)
        ? {
            ...entry,
            is_read: true,
            read_at: readAt,
            data: {
              ...(entry.data || {}),
              group_items: Array.isArray(entry?.data?.group_items)
                ? entry.data.group_items.map((groupItem) => ({
                    ...groupItem,
                    is_read: true,
                  }))
                : entry?.data?.group_items,
            },
          }
        : entry
    )));
    setSummary((current) => ({
      ...current,
      unread: Math.max(0, current.unread - unreadDelta),
      read: Math.min(current.total, current.read + unreadDelta),
    }));

    try {
      await markNotificationReadRequest(id, ids);
      await refresh({ silent: true });
    } catch (requestError) {
      await refresh({ silent: true });
      throw requestError;
    }
  }, [items, refresh]);

  const markUnread = useCallback(async (item) => {
    const id = item?.id ?? item;
    const target = typeof item === "object"
      ? item
      : items.find(
        (entry) => notificationIdentity(entry) === String(id),
      );
    if (!target || !target.is_read) return;

    const ids = groupIds(target);
    const targetIds = new Set(ids);
    const unreadDelta = Math.max(1, ids.length);

    setItems((current) => current.map((entry) => (
      itemTouchesIds(entry, targetIds)
        ? {
            ...entry,
            is_read: false,
            read_at: null,
            data: {
              ...(entry.data || {}),
              group_items: Array.isArray(entry?.data?.group_items)
                ? entry.data.group_items.map((groupItem) => ({
                    ...groupItem,
                    is_read: false,
                  }))
                : entry?.data?.group_items,
            },
          }
        : entry
    )));
    setSummary((current) => ({
      ...current,
      unread: Math.min(current.total, current.unread + unreadDelta),
      read: Math.max(0, current.read - unreadDelta),
    }));

    try {
      await markNotificationUnreadRequest(id, ids);
      await refresh({ silent: true });
    } catch (requestError) {
      await refresh({ silent: true });
      throw requestError;
    }
  }, [items, refresh]);

  const markAllRead = useCallback(async () => {
    if (summary.unread <= 0) return;
    const readAt = new Date().toISOString();
    setItems((current) => current.map((item) => ({
      ...item,
      is_read: true,
      read_at: readAt,
      data: {
        ...(item.data || {}),
        group_items: Array.isArray(item?.data?.group_items)
          ? item.data.group_items.map((groupItem) => ({
              ...groupItem,
              is_read: true,
            }))
          : item?.data?.group_items,
      },
    })));
    setSummary((current) => ({
      ...current,
      unread: 0,
      read: current.total,
    }));

    try {
      await markAllNotificationsReadRequest();
      await refresh({ silent: true });
    } catch (requestError) {
      await refresh({ silent: true });
      throw requestError;
    }
  }, [refresh, summary.unread]);

  const clearShown = useCallback(async (shownItems) => {
    const shown = shownItems || [];
    const ids = Array.from(new Set(shown.flatMap(groupIds)));
    if (!ids.length) return;

    const targetIds = new Set(ids);
    const unreadDeleted = shown.reduce(
      (total, item) => total + groupUnreadCount(item),
      0,
    );

    // Only the five notifications currently visible inside the bell popup
    // are removed.  The Notification Center has no Clear shown action.
    setItems((current) => current.filter(
      (item) => !itemTouchesIds(item, targetIds),
    ));
    setSummary((current) => ({
      total: Math.max(0, current.total - ids.length),
      unread: Math.max(0, current.unread - unreadDeleted),
      read: Math.max(
        0,
        current.read - Math.max(0, ids.length - unreadDeleted),
      ),
    }));

    try {
      await clearVisibleNotificationsRequest(ids);
      await refresh({ silent: true });
    } catch (requestError) {
      await refresh({ silent: true });
      throw requestError;
    }
  }, [refresh]);

  const value = useMemo(() => ({
    items,
    summary,
    unreadCount: summary.unread,
    loading,
    error,
    refresh,
    markRead,
    markUnread,
    markAllRead,
    clearShown,
    notificationIdentity,
  }), [
    items,
    summary,
    loading,
    error,
    refresh,
    markRead,
    markUnread,
    markAllRead,
    clearShown,
  ]);

  return (
    <NotificationContext.Provider value={value}>
      {children}
    </NotificationContext.Provider>
  );
}

export function useNotifications() {
  const context = useContext(NotificationContext);
  if (!context) {
    throw new Error(
      "useNotifications must be used inside NotificationProvider.",
    );
  }
  return context;
}
