import { useCallback, useEffect, useState } from "react";
import {
  ChatOutlined,
  GroupOutlined,
  PersonAddOutlined,
  RefreshOutlined,
} from "@mui/icons-material";

import { getAnalyticsSummaryRequest } from "../../api/client";
import { AppButton, AppCard, ErrorState, LoadingState, PageHeader } from "../../components/common";
import "./AnalyticsPage.css";

function humanize(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function StatCard({ title, value, description, icon: Icon }) {
  return (
    <AppCard padding="medium" hoverable className="analytics-stat-card">
      <div className="analytics-stat-icon">
        <Icon />
      </div>
      <div>
        <span>{title}</span>
        <strong>{value ?? 0}</strong>
        <small>{description}</small>
      </div>
    </AppCard>
  );
}

function BreakdownBars({ items, labelKey, valueKey, emptyText, formatLabel }) {
  if (!items || !items.length) {
    return <p className="analytics-breakdown-empty">{emptyText}</p>;
  }
  const max = Math.max(...items.map((item) => Number(item[valueKey]) || 0), 1);
  return (
    <div className="analytics-bar-list">
      {items.map((item) => {
        const value = Number(item[valueKey]) || 0;
        const percent = Math.round((value / max) * 100);
        const label = formatLabel ? formatLabel(item[labelKey]) : humanize(item[labelKey]);
        return (
          <div className="analytics-bar-row" key={`${item[labelKey]}`}>
            <span className="analytics-bar-label">{label}</span>
            <div className="analytics-bar-track">
              <div className="analytics-bar-fill" style={{ width: `${percent}%` }} />
            </div>
            <span className="analytics-bar-value">{value}</span>
          </div>
        );
      })}
    </div>
  );
}

export default function AnalyticsPage() {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await getAnalyticsSummaryRequest();
      setSummary(result);
    } catch (requestError) {
      setError(requestError.message || "Analytics could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) {
    return (
      <AppCard padding="medium">
        <LoadingState title="Loading analytics..." description="Crunching conversations and contacts." />
      </AppCard>
    );
  }

  if (error) {
    return (
      <AppCard padding="medium">
        <ErrorState
          title="Analytics could not load"
          description={error}
          action={<AppButton variant="primary" icon={<RefreshOutlined fontSize="small" />} onClick={load}>Try again</AppButton>}
        />
      </AppCard>
    );
  }

  const aiVsHuman = summary?.ai_vs_human || { ai_enabled: 0, human: 0 };
  const aiVsHumanItems = [
    { label: "ai_enabled", count: aiVsHuman.ai_enabled || 0 },
    { label: "human", count: aiVsHuman.human || 0 },
  ];

  return (
    <section className="analytics-page">
      <PageHeader
        eyebrow="ANALYTICS & REPORTS"
        description="Operational KPIs for your company — channel mix, AI vs human handling, contact lifecycle and tags."
        actions={
          <AppButton variant="secondary" icon={<RefreshOutlined fontSize="small" />} onClick={load}>
            Refresh
          </AppButton>
        }
      />

      <section className="analytics-stats-grid">
        <StatCard
          title="Total contacts"
          value={summary?.total_contacts}
          description="Contact records on file"
          icon={GroupOutlined}
        />
        <StatCard
          title="Total conversations"
          value={summary?.total_conversations}
          description="Across all channels"
          icon={ChatOutlined}
        />
        <StatCard
          title="New contacts (30 days)"
          value={summary?.new_contacts_last_30_days}
          description="First seen in the last 30 days"
          icon={PersonAddOutlined}
        />
      </section>

      <section className="analytics-breakdown-grid">
        <AppCard padding="medium" className="analytics-breakdown-card">
          <h3>Conversations by channel</h3>
          <BreakdownBars
            items={summary?.conversations_by_channel}
            labelKey="channel"
            valueKey="count"
            emptyText="No conversations yet."
          />
        </AppCard>

        <AppCard padding="medium" className="analytics-breakdown-card">
          <h3>AI vs human</h3>
          <BreakdownBars
            items={aiVsHumanItems}
            labelKey="label"
            valueKey="count"
            emptyText="No conversations yet."
            formatLabel={(value) => (value === "ai_enabled" ? "AI enabled" : "Human handled")}
          />
        </AppCard>

        <AppCard padding="medium" className="analytics-breakdown-card">
          <h3>Contacts by lifecycle stage</h3>
          <BreakdownBars
            items={summary?.contacts_by_lifecycle_stage}
            labelKey="stage"
            valueKey="count"
            emptyText="No contacts yet."
          />
        </AppCard>

        <AppCard padding="medium" className="analytics-breakdown-card">
          <h3>Top tags</h3>
          <BreakdownBars
            items={summary?.top_tags}
            labelKey="tag"
            valueKey="count"
            emptyText="No tags used yet."
            formatLabel={(value) => value}
          />
        </AppCard>
      </section>
    </section>
  );
}
