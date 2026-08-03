import { act, render, screen, waitFor } from "@testing-library/react";
import { useEffect, useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock the API layer so the test can assert exactly which filters get sent
// to the notifications endpoint on each refresh.
vi.mock("../../../api/client", () => ({
  clearVisibleNotificationsRequest: vi.fn(),
  getNotificationsRequest: vi.fn(),
  getNotificationSummaryRequest: vi.fn(),
  markAllNotificationsReadRequest: vi.fn(),
  markNotificationReadRequest: vi.fn(),
  markNotificationUnreadRequest: vi.fn(),
}));

// NotificationProvider reads `authenticated` from AuthContext; stub it so
// the provider runs its normal fetch/poll wiring without a real login flow.
vi.mock("../../../contexts/AuthContext", () => ({
  useAuth: () => ({ authenticated: true }),
}));

import {
  getNotificationsRequest,
  getNotificationSummaryRequest,
} from "../../../api/client";
import { NotificationProvider, useNotifications } from "../../../contexts/NotificationContext";
import NotificationsPage from "../NotificationsPage";

// Stands in for the always-mounted consumers (bell dropdown / Topbar badge)
// that sit alongside NotificationsPage in the real app shell and must keep
// seeing the default, unfiltered view regardless of what the page did.
function BackgroundConsumer({ onReady }) {
  const { refresh } = useNotifications();
  useEffect(() => { onReady(refresh); }, [refresh, onReady]);
  return null;
}

// Mimics the real app shell: NotificationsPage mounts/unmounts as the user
// navigates, while the dropdown/badge-like consumer stays mounted the whole
// time, sharing the same NotificationProvider.
function Harness({ showPage, onBackgroundRefreshReady }) {
  return (
    <NotificationProvider>
      {showPage ? <NotificationsPage /> : null}
      <BackgroundConsumer onReady={onBackgroundRefreshReady} />
    </NotificationProvider>
  );
}

describe("NotificationsPage filter isolation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getNotificationsRequest.mockResolvedValue([]);
    getNotificationSummaryRequest.mockResolvedValue({ unread: 0, total: 0, read: 0 });
  });

  it("does not leak a page-selected filter into background refreshes after the page unmounts", async () => {
    let backgroundRefresh;
    const { rerender } = render(
      <MemoryRouter>
        <Harness
          showPage
          onBackgroundRefreshReady={(refresh) => { backgroundRefresh = refresh; }}
        />
      </MemoryRouter>,
    );

    await waitFor(() => expect(getNotificationsRequest).toHaveBeenCalled());
    getNotificationsRequest.mockClear();

    // Switch to the "Read" tab, like a user filtering the notification
    // center (NotificationsPage.jsx statusFilter -> "read").
    await act(async () => {
      screen.getByRole("button", { name: /Read/ }).click();
    });

    await waitFor(() => {
      expect(getNotificationsRequest).toHaveBeenCalledWith(
        expect.objectContaining({ status: "read" }),
      );
    });
    getNotificationsRequest.mockClear();

    // Navigate away: NotificationsPage unmounts (e.g. openConversation()
    // pushing a new route), while the dropdown/badge-like consumer and the
    // shared NotificationProvider stay mounted.
    await act(async () => {
      rerender(
        <MemoryRouter>
          <Harness
            showPage={false}
            onBackgroundRefreshReady={(refresh) => { backgroundRefresh = refresh; }}
          />
        </MemoryRouter>,
      );
    });

    await waitFor(() => expect(screen.queryByRole("button", { name: /Read/ })).toBeNull());
    getNotificationsRequest.mockClear();

    // Simulate a background poll / focus / visibilitychange refresh firing
    // after the page is gone.
    expect(typeof backgroundRefresh).toBe("function");
    await act(async () => {
      await backgroundRefresh({ silent: true });
    });

    expect(getNotificationsRequest).toHaveBeenCalledTimes(1);
    const appliedFilters = getNotificationsRequest.mock.calls[0][0];
    expect(appliedFilters.status).toBeUndefined();
  });
});
