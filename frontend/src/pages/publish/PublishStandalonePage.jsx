import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import HomePage from "../community/HomePage";
import PublishPage from "../community/PublishPage";
// Both HomePage and PublishPage use AppCard (which renders
// .tz-card) and expect the Buffer-style theme overrides that
// CommunityHubPage.css scopes to ".community-hub-shell", plus the
// --buffer-* CSS custom properties it declares on :root. This page used to
// only be reachable nested inside CommunityHubPage's own shell — now that
// it's a standalone top-level route, it has to bring that stylesheet and
// wrapper class along itself so the real, working Buffer look is preserved
// exactly as it was.
import "../community/CommunityHubPage.css";
// Reuses the existing .publish-tabs / .publish-tab pill-tab styles that
// PublishPage.css already defines for its own Queue/Drafts/Sent/Failed
// tabs — same visual language, one level up.
import "../community/PublishPage.css";
import "./PublishStandalonePage.css";

const TOP_TABS = [
  { key: "overview", label: "Overview" },
  { key: "posts", label: "Posts" },
];

function wantsPostsTab(searchParams) {
  return searchParams.get("new") === "1" || searchParams.get("tab") === "posts" || Boolean(searchParams.get("channel"));
}

export default function PublishStandalonePage() {
  const [searchParams] = useSearchParams();
  const [activeTab, setActiveTab] = useState(() => (wantsPostsTab(searchParams) ? "posts" : "overview"));

  // If the user arrives (or is navigated, e.g. from the Overview tab's own
  // "Create Post" button) with ?new=1 / ?tab=posts / ?channel=..., jump to
  // the Posts tab so PublishPage's own existing "auto-open create dialog"
  // and channel-filter behavior still fires exactly as it did before.
  useEffect(() => {
    if (wantsPostsTab(searchParams)) setActiveTab("posts");
  }, [searchParams]);

  return (
    <section className="community-hub-shell publish-standalone-page">
      <div className="publish-standalone-tabs publish-tabs">
        {TOP_TABS.map((tab) => (
          <button
            type="button"
            key={tab.key}
            className={`publish-tab ${activeTab === tab.key ? "is-active" : ""}`}
            onClick={() => setActiveTab(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "overview" ? <HomePage /> : <PublishPage />}
    </section>
  );
}
