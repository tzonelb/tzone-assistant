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

function filtersEqual(a, b) {
  const left = a || {};
  const right = b || {};
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  if (leftKeys.length !== rightKeys.length) return false;
  return leftKeys.every((key) => left[key] === right[key]);
}

export function NotificationProvider({ children }) {
  const { authenticated } = useAuth();
  const [items, setItems] = useState([]);
  const [summary, setSummary] = useState({ unread: 0, total: 0, read: 0 });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const inFlightRef = useRef(false);
  // The filter set the currently in-flight fetch was launched with (null
  // when nothing is in flight). Used to detect whether a call blocked by
  // the in-flight guard below actually needs a follow-up fetch, or whether
  // the in-flight fetch already matches what was just requested.
  const inFlightFiltersRef = useRef(null);
  // Set when an explicit-filters call is blocked by the in-flight guard
  // while the filters it recorded differ from the ones the in-flight fetch
  // is using. Consumed by the in-flight fetch's `finally` block to trigger
  // an immediate follow-up fetch, so the UI converges on the correct
  // filtered view right away instead of waiting up to POLL_INTERVAL_MS for
  // the next natural trigger. Carries the `silent` flag of the blocked call
  // so the follow-up preserves its loading-indicator behavior.
  const pendingRerunRef = useRef(null);
  // Remembers the last explicitly-applied filter set so background refreshes
  // (interval / focus / visibilitychange) keep respecting it instead of
  // silently reverting to the unfiltered list. Only calls that pass a
  // `filters` argument update this; calls that omit it (background polling,
  // post-mutation refreshes) reuse whatever was last applied.
  //
  // This ref is Provider-wide (shared by NotificationsPage, the bell
  // dropdown, and the Topbar badge's `items`-derived state), but only
  // NotificationsPage ever applies a non-default filter. NotificationsPage
  // is therefore responsible for releasing it — via `refresh({ filters: {} })`
  // in an unmount cleanup effect — whenever it stops being the active view,
  // so the filter can't outlive the page and leak into the dropdown/badge
  // for the rest of the session. See NotificationsPage.jsx.
  const lastFiltersRef = useRef({});

  // Performs the actual fetch. Split out from `refresh` so the in-flight
  // guard's queued follow-up (see `pendingRerunRef`) can re-invoke it
  // directly once the current fetch clears, without re-running the
  // filters-recording logic in `refresh` itself.
  const runFetch = useCallback(async (silent) => {
    inFlightRef.current = true;
    if (!silent) setLoading(true);
    const appliedFilters = lastFiltersRef.current;
    inFlightFiltersRef.current = appliedFilters;
    try {
      const [nextItems, summaryPayload] = await Promise.all([
        getNotificationsRequest({ pageSize: 100, ...appliedFilters }),
        getNotificationSummaryRequest(),
      ]);
      const normalizedItems = Array.isArray(nextItems) ? nextItems : [];
      setItems(normalizedItems);
      setSummary(normalizeSummary(summaryPayload, normalizedItems));
      setError("");
    } catch (requestError) {
      if (!silent) {
        setError(requestError?.message || "Failed to load notifications.");
      }
    } finally {
      inFlightRef.current = false;
      inFlightFiltersRef.current = null;
      if (!silent) setLoading(false);
      if (pendingRerunRef.current) {
        const pending = pendingRerunRef.current;
        pendingRerunRef.current = null;
        // Re-run immediately (rather than waiting for the next poll/focus
        // trigger) so a filter change that arrived mid-fetch is reflected
        // right away instead of leaving a stale-filtered view on screen.
        runFetch(pending.silent);
      }
    }
  }, []);

  const refresh = useCallback(async ({ silent = false, filters } = {}) => {
    // Record an explicitly-supplied filter set (including NotificationsPage's
    // unmount cleanup passing `{}` to release it) even if a fetch is already
    // in flight, so the applied filter is never lost to the in-flight guard
    // below and background refreshes pick up the change on their next tick.
    if (filters !== undefined) {
      lastFiltersRef.current = filters;
    }
    if (!authenticated) return;
    if (inFlightRef.current) {
      // The ref above is updated, but the fetch already underway snapshotted
      // the *previous* filters and will still resolve with them. If what we
      // just recorded actually differs from that snapshot, queue an
      // immediate follow-up fetch (fired from runFetch's `finally`) instead
      // of letting the in-flight fetch's stale-filtered result stand until
      // the next poll tick.
      if (filters !== undefined && !filtersEqual(filters, inFlightFiltersRef.current)) {
        pendingRerunRef.current = { silent };
      }
      return;
    }
    await runFetch(silent);
  }, [authenticated, runFetch]);

  useEffect(() => {
    if (!authenticated) {
      setItems([]);
      setSummary({ unread: 0, total: 0, read: 0 });
      lastFiltersRef.current = {};
      pendingRerunRef.current = null;
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
