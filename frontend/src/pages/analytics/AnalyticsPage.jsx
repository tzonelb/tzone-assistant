import { useCallback, useEffect, useState } from "react";
import {
  ChatOutlined,
  GroupOutlined,
  InsightsOutlined,
  PersonAddOutlined,
  RefreshOutlined,
} from "@mui/icons-material";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Funnel,
  FunnelChart,
  LabelList,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { getAnalyticsSummaryRequest } from "../../api/client";
import { AppCard, ErrorState, LoadingState, PageHeader } from "../../components/common";
import "./AnalyticsPage.css";

// Canonical lifecycle pipeline order (matches backend/services/customer_service.py
// LIFECYCLE_STAGES) — used only to order the funnel visually, never to
// invent counts for stages that aren't present in the real data.
const LIFECYCLE_STAGE_ORDER = ["lead", "active", "customer", "vip", "churned"];

const CHART_TOOLTIP_STYLE = {
  background: "var(--tz-surface)",
  border: "1px solid var(--tz-border)",
  borderRadius: 10,
  fontSize: 12,
  color: "var(--tz-text-primary)",
};

const CHART_AXIS_TICK = { fill: "var(--tz-text-muted)", fontSize: 11 };

function humanize(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatShortDate(isoDate) {
  if (!isoDate) return "";
  const parsed = new Date(`${isoDate}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return isoDate;
  return parsed.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
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

function ConversationVolumeChart({ series }) {
  if (!series || !series.length) {
    return <p className="analytics-breakdown-empty">No conversations in this period.</p>;
  }
  const data = series.map((point) => ({ ...point, label: formatShortDate(point.date) }));
  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={data} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
        <defs>
          <linearGradient id="analyticsVolumeFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--tz-primary)" stopOpacity={0.35} />
            <stop offset="100%" stopColor="var(--tz-primary)" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="var(--tz-border)" vertical={false} />
        <XAxis dataKey="label" tick={CHART_AXIS_TICK} axisLine={{ stroke: "var(--tz-border)" }} tickLine={false} />
        <YAxis allowDecimals={false} tick={CHART_AXIS_TICK} axisLine={false} tickLine={false} width={30} />
        <Tooltip contentStyle={CHART_TOOLTIP_STYLE} labelStyle={{ color: "var(--tz-text-secondary)" }} />
        <Area
          type="monotone"
          dataKey="count"
          name="Conversations"
          stroke="var(--tz-primary)"
          strokeWidth={2}
          fill="url(#analyticsVolumeFill)"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

function AiVsHumanTrendChart({ series }) {
  if (!series || !series.length) {
    return <p className="analytics-breakdown-empty">No conversations in this period.</p>;
  }
  const data = series.map((point) => ({ ...point, label: formatShortDate(point.date) }));
  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={data} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
        <CartesianGrid stroke="var(--tz-border)" vertical={false} />
        <XAxis dataKey="label" tick={CHART_AXIS_TICK} axisLine={{ stroke: "var(--tz-border)" }} tickLine={false} />
        <YAxis allowDecimals={false} tick={CHART_AXIS_TICK} axisLine={false} tickLine={false} width={30} />
        <Tooltip contentStyle={CHART_TOOLTIP_STYLE} labelStyle={{ color: "var(--tz-text-secondary)" }} />
        <Legend wrapperStyle={{ fontSize: 12, color: "var(--tz-text-secondary)" }} />
        <Area
          type="monotone"
          dataKey="ai_enabled_count"
          name="Started AI-enabled"
          stackId="1"
          stroke="var(--tz-primary)"
          fill="var(--tz-primary)"
          fillOpacity={0.35}
        />
        <Area
          type="monotone"
          dataKey="human_count"
          name="Started human-handled"
          stackId="1"
          stroke="var(--tz-secondary)"
          fill="var(--tz-secondary)"
          fillOpacity={0.35}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

function LifecycleFunnelChart({ items }) {
  if (!items || !items.length) {
    return <p className="analytics-breakdown-empty">No contacts yet.</p>;
  }
  const countByStage = new Map(items.map((item) => [item.stage, Number(item.count) || 0]));
  const orderedStages = [
    ...LIFECYCLE_STAGE_ORDER.filter((stage) => countByStage.has(stage)),
    ...items.map((item) => item.stage).filter((stage) => !LIFECYCLE_STAGE_ORDER.includes(stage)),
  ];
  const data = orderedStages
    .map((stage) => ({ stage, name: humanize(stage), value: countByStage.get(stage) || 0 }));
  const funnelColors = ["var(--tz-primary)", "var(--tz-secondary)", "var(--tz-success)", "var(--tz-warning)", "var(--tz-danger)"];

  return (
    <ResponsiveContainer width="100%" height={240}>
      <FunnelChart>
        <Tooltip contentStyle={CHART_TOOLTIP_STYLE} labelStyle={{ color: "var(--tz-text-secondary)" }} />
        <Funnel dataKey="value" data={data} isAnimationActive nameKey="name">
          {data.map((entry, index) => (
            <Cell key={entry.stage} fill={funnelColors[index % funnelColors.length]} />
          ))}
          <LabelList
            dataKey="name"
            position="right"
            fill="var(--tz-text-primary)"
            stroke="none"
            fontSize={12}
          />
          <LabelList
            dataKey="value"
            position="center"
            fill="#fff"
            stroke="none"
            fontSize={12}
            fontWeight={800}
          />
        </Funnel>
      </FunnelChart>
    </ResponsiveContainer>
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
          action={<button type="button" className="btn btn-primary" onClick={load}><RefreshOutlined fontSize="small" /> Try again</button>}
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
        actions={
          <button type="button" className="btn btn-secondary" onClick={load}>
            <RefreshOutlined fontSize="small" /> Refresh
          </button>
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

      <section className="analytics-charts-grid">
        <AppCard padding="medium" className="analytics-chart-card">
          <h3>Conversation volume ({summary?.conversation_volume_trend?.days ?? 30} days)</h3>
          <ConversationVolumeChart series={summary?.conversation_volume_trend?.series} />
        </AppCard>

        <AppCard padding="medium" className="analytics-chart-card">
          <h3>AI vs human, over time</h3>
          <AiVsHumanTrendChart series={summary?.ai_vs_human_trend?.series} />
          {summary?.ai_vs_human_trend?.note ? (
            <p className="analytics-chart-note">{summary.ai_vs_human_trend.note}</p>
          ) : null}
        </AppCard>
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
          <h3>Contact lifecycle funnel</h3>
          <LifecycleFunnelChart items={summary?.contacts_by_lifecycle_stage} />
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

        <AppCard padding="medium" className="analytics-breakdown-card analytics-insights-card">
          <h3><InsightsOutlined fontSize="small" /> Insights</h3>
          <p className="analytics-breakdown-empty">Engagement stats per post and per channel will land here next.</p>
        </AppCard>
      </section>
    </section>
  );
}
