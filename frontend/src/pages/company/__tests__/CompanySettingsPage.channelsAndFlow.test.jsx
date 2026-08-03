import { act, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock the API layer so we can assert exactly what CompanySettingsPage sends
// (and control what it receives) without a real backend.
vi.mock("../../../api/client", () => ({
  facebookConnectRequest: vi.fn(),
  getCompanySettingSectionRequest: vi.fn(),
  updateCompanySettingSectionRequest: vi.fn(),
}));

vi.mock("../../../contexts/AuthContext", () => ({
  useAuth: () => globalThis.__mockUseAuth(),
}));

import {
  facebookConnectRequest,
  getCompanySettingSectionRequest,
  updateCompanySettingSectionRequest,
} from "../../../api/client";
import CompanySettingsPage from "../CompanySettingsPage";

// Renders the page plus a tiny probe that exposes the current router
// location's search string, so tests can assert the ?connected=... params
// actually get stripped from the URL (not just that a banner appeared).
function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-search">{location.search}</div>;
}

function renderPage(initialPath, { canManage = true } = {}) {
  globalThis.__mockUseAuth = () => ({
    hasPermission: (code) => {
      if (code === "settings.view") return true;
      if (code === "settings.manage") return canManage;
      return false;
    },
  });

  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <LocationProbe />
      <Routes>
        <Route path="/company-settings/*" element={<CompanySettingsPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

// Which section tab is shown is purely internal component state driven by
// the sidebar nav buttons -- the URL path suffix (e.g. "/channels") is not
// read to select a tab (only the connected=facebook query param drives an
// automatic tab switch). So tests that need a non-default tab select it the
// same way a user would: by clicking its nav button.
async function selectTab(name) {
  await act(async () => {
    screen.getByRole("button", { name }).click();
  });
}

describe("CompanySettingsPage Facebook OAuth redirect handling", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getCompanySettingSectionRequest.mockResolvedValue({ values: {}, locked_keys: [] });
  });

  it("switches to the Channels tab and shows a success banner for ?connected=facebook&status=ok, then strips the query params", async () => {
    renderPage("/company-settings/profile?connected=facebook&status=ok");

    expect(await screen.findByText("Facebook connected successfully.")).toBeInTheDocument();

    // The Channels nav button should now be the active tab.
    const channelsNavButton = screen.getByRole("button", { name: "Channels" });
    expect(channelsNavButton.className).toContain("is-active");

    // Query params used for the redirect must be cleaned out of the URL so
    // a refresh does not re-show the banner.
    await waitFor(() => {
      const search = screen.getByTestId("location-search").textContent;
      expect(search).not.toContain("connected");
      expect(search).not.toContain("status");
    });
  });

  it("shows a labeled error banner including the failure reason for ?status=error&reason=...", async () => {
    renderPage("/company-settings/profile?connected=facebook&status=error&reason=permission_denied");

    expect(
      await screen.findByText("Facebook connection failed: permission denied."),
    ).toBeInTheDocument();
  });

  it("is dismissible", async () => {
    renderPage("/company-settings/profile?connected=facebook&status=ok");

    const banner = await screen.findByRole("status");
    expect(within(banner).getByText("Facebook connected successfully.")).toBeInTheDocument();

    await act(async () => {
      within(banner).getByRole("button", { name: /dismiss/i }).click();
    });

    expect(screen.queryByText("Facebook connected successfully.")).not.toBeInTheDocument();
  });

  it("does not show a banner or switch tabs when there is no connected=facebook param", async () => {
    renderPage("/company-settings/profile");

    await waitFor(() => expect(getCompanySettingSectionRequest).toHaveBeenCalled());
    expect(screen.queryByText(/Facebook connect/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Company Profile" }).className).toContain("is-active");
  });
});

describe("CompanySettingsPage Connect Facebook action", () => {
  const originalLocation = window.location;

  beforeEach(() => {
    vi.clearAllMocks();
    getCompanySettingSectionRequest.mockResolvedValue({ values: {}, locked_keys: [] });
    delete window.location;
    window.location = { ...originalLocation, href: "" };
  });

  afterEach(() => {
    window.location = originalLocation;
  });

  it("navigates the browser to the returned authorize_url on click (not fetched/XHR'd)", async () => {
    facebookConnectRequest.mockResolvedValue({ authorize_url: "https://facebook.com/oauth/authorize?x=1" });
    renderPage("/company-settings/profile");
    await selectTab("Channels");

    const button = await screen.findByRole("button", { name: "Connect Facebook" });
    await act(async () => {
      button.click();
    });

    await waitFor(() => {
      expect(window.location.href).toBe("https://facebook.com/oauth/authorize?x=1");
    });
  });

  it("shows a visible error instead of failing silently when the request fails", async () => {
    facebookConnectRequest.mockRejectedValue(new Error("Facebook is not configured for this company."));
    renderPage("/company-settings/profile");
    await selectTab("Channels");

    const button = await screen.findByRole("button", { name: "Connect Facebook" });
    await act(async () => {
      button.click();
    });

    expect(await screen.findByText("Facebook is not configured for this company.")).toBeInTheDocument();
    expect(window.location.href).toBe("");
  });

  it("disables the Connect button when the user lacks settings.manage", async () => {
    renderPage("/company-settings/profile", { canManage: false });
    await selectTab("Channels");

    const button = await screen.findByRole("button", { name: "Connect Facebook" });
    expect(button).toBeDisabled();
  });
});

describe("CompanySettingsPage Reply Flow editor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders all six stages as toggles, in saved order, with escalation visibly conditional", async () => {
    getCompanySettingSectionRequest.mockResolvedValue({
      values: { steps: ["welcome", "language_detection", "intent_detection", "knowledge_lookup", "answer", "escalation"] },
      locked_keys: [],
    });
    renderPage("/company-settings/profile");
    await selectTab("Reply Flow");

    expect(await screen.findByText("Welcome message")).toBeInTheDocument();
    expect(screen.getByText("Language detection")).toBeInTheDocument();
    expect(screen.getByText("Intent / department detection")).toBeInTheDocument();
    expect(screen.getByText("Knowledge base lookup")).toBeInTheDocument();
    expect(screen.getByText("AI answer generation")).toBeInTheDocument();
    expect(screen.getByText("Escalation to human")).toBeInTheDocument();

    // The escalation card must spell out the condition and both labeled
    // outcomes unambiguously (the bug this feature fixes).
    expect(screen.getByText(/the AI decides during the conversation whether the customer needs a human/)).toBeInTheDocument();
    expect(screen.getByText(/hand off to a human employee \(shown to the customer\)/)).toBeInTheDocument();
    expect(screen.getByText(/the AI keeps handling the conversation/)).toBeInTheDocument();

    expect(screen.getByRole("button", { name: "Escalation to human on" })).toBeInTheDocument();
  });

  it("omits ids not present in the saved steps array by turning their toggle off", async () => {
    getCompanySettingSectionRequest.mockResolvedValue({
      values: { steps: ["welcome", "answer"] },
      locked_keys: [],
    });
    renderPage("/company-settings/profile");
    await selectTab("Reply Flow");

    await screen.findByText("Welcome message");
    expect(screen.getByRole("button", { name: "Escalation to human off" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Language detection off" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Welcome message on" })).toBeInTheDocument();
  });

  it("reorders a stage with the move buttons and toggles escalation off, then saves the resulting steps array", async () => {
    getCompanySettingSectionRequest.mockResolvedValue({
      values: { steps: ["welcome", "language_detection", "intent_detection", "knowledge_lookup", "answer", "escalation"] },
      locked_keys: [],
    });
    updateCompanySettingSectionRequest.mockResolvedValue({
      values: { steps: ["language_detection", "welcome", "intent_detection", "knowledge_lookup", "answer"] },
    });

    renderPage("/company-settings/profile");
    await selectTab("Reply Flow");
    await screen.findByText("Welcome message");

    // Move "Language detection" up above "Welcome message".
    await act(async () => {
      screen.getByRole("button", { name: "Move Language detection earlier" }).click();
    });

    // Turn escalation off (off = never show escalation to the customer).
    await act(async () => {
      screen.getByRole("button", { name: "Escalation to human on" }).click();
    });

    await act(async () => {
      screen.getByRole("button", { name: "Save reply flow" }).click();
    });

    await waitFor(() => {
      expect(updateCompanySettingSectionRequest).toHaveBeenCalledWith("reply_flow", {
        steps: ["language_detection", "welcome", "intent_detection", "knowledge_lookup", "answer"],
      });
    });
    expect(await screen.findByText("Reply flow saved.")).toBeInTheDocument();
  });

  it("is read-only without settings.manage: no save button, toggles and move buttons disabled", async () => {
    getCompanySettingSectionRequest.mockResolvedValue({
      values: { steps: ["welcome", "language_detection", "intent_detection", "knowledge_lookup", "answer", "escalation"] },
      locked_keys: [],
    });
    renderPage("/company-settings/profile", { canManage: false });
    await selectTab("Reply Flow");

    await screen.findByText("Welcome message");
    expect(screen.queryByRole("button", { name: "Save reply flow" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Escalation to human on" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move Language detection earlier" })).toBeDisabled();
    expect(screen.getByText(/read-only access to this section/)).toBeInTheDocument();
  });
});
