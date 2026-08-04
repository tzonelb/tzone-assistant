import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AutoAwesomeOutlined,
  ChatOutlined,
  GroupOutlined,
  RefreshOutlined,
  SupportAgentOutlined,
} from "@mui/icons-material";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { getAnalyticsRequest } from "../../api/client";
import {
  AppButton,
  AppCard,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "../../components/common";

// Categorical palette drawn from the --tz-* brand tokens. Distinct hues,
// readable on both the light and dark card surfaces.
const SERIES_COLORS = [
  "#39aef2", // primary blue
  "#16b892", // secondary teal
  "#49bf5c", // green
  "#d99016", // amber
  "#7c6cf0", // violet
  "#c83f4d", // red
  "#1878c8", // info blue
  "#e06fae", // pink
];

const AI_COLORS = {
  "AI-handled": "#39aef2",
  "Human-handled": "#d99016",
};

const PRESETS = [
  { key: "7", label: "Last 7 days", days: 7 },
  { key: "30", label: "Last 30 days", days: 30 },
  { key: "90", label: "Last 90 days", days: 90 },
];

function toISODate(date) {
  return date.toISOString().slice(0, 10);
}

function rangeForDays(days) {
  const to = new Date();
  const from = new Date();
  from.setDate(from.getDate() - (days - 1));
  return { from: toISODate(from), to: toISODate(to) };
}

// Resolve a handful of --tz-* tokens to concrete colors so the axes, grid and
// tooltip follow the active theme (recharts renders raw SVG and does not
// inherit CSS variables on every attribute).
function useThemeChartColors() {
  const read = useCallback(() => {
    if (typeof window === "undefined") {
      return { axis: "#68758a", grid: "#e3e8ef", surface: "#ffffff", text: "#182238" };
    }
    const styles = getComputedStyle(document.documentElement);
    const value = (name, fallback) => {
      const resolved = styles.getPropertyValue(name).trim();
      return resolved || fallback;
    };
    return {
      axis: value("--tz-text-secondary", "#68758a"),
      grid: value("--tz-border", "#e3e8ef"),
      surface: value("--tz-surface", "#ffffff"),
      text: value("--tz-text-primary", "#182238"),
    };
  }, []);

  const [colors, setColors] = useState(read);

  useEffect(() => {
    const update = () => setColors(read());
    update();

    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme", "class", "style"],
    });

    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", update);

    return () => {
      observer.disconnect();
      media.removeEventListener("change", update);
    };
  }, [read]);

  return colors;
}

function StatTile({ title, value, description, icon: Icon }) {
  return (
    <AppCard padding="medium" hoverable className="stat-card">
      <div className="stat-icon">
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

function ChartCard({ title, subtitle, children, isEmpty, emptyLabel }) {
  return (
    <AppCard padding="medium" className="dashboard-panel analytics-chart-card">
      <div className="panel-title">
        <div>
          <span>{subtitle}</span>
          <h3>{title}</h3>
        </div>
      </div>
      {isEmpty ? (
        <EmptyState
          icon={<ChatOutlined />}
          title="No data in this range"
          description={emptyLabel || "There is nothing to chart for the selected dates yet."}
        />
      ) : (
        <div className="analytics-chart-body">{children}</div>
      )}
    </AppCard>
  );
}

export default function AnalyticsPage() {
  const themeColors = useThemeChartColors();

  const [range, setRange] = useState(() => rangeForDays(30));
  const [activePreset, setActivePreset] = useState("30");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadAnalytics = useCallback(async (selectedRange) => {
    setLoading(true);
    setError("");

    try {
      const result = await getAnalyticsRequest(selectedRange);
      setData(result);
    } catch (requestError) {
      setError(requestError.message || "Analytics could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAnalytics(range);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function applyPreset(preset) {
    const nextRange = rangeForDays(preset.days);
    setActivePreset(preset.key);
    setRange(nextRange);
    loadAnalytics(nextRange);
  }

  function updateRangeField(field, value) {
    const nextRange = { ...range, [field]: value };
    setActivePreset("custom");
    setRange(nextRange);
  }

  function applyCustomRange() {
    if (!range.from || !range.to) return;
    loadAnalytics(range);
  }

  const totals = data?.totals || {};

  const tooltipStyle = useMemo(
    () => ({
      background: themeColors.surface,
      border: `1px solid ${themeColors.grid}`,
      borderRadius: 10,
      color: themeColors.text,
    }),
    [themeColors],
  );

  const axisProps = useMemo(
    () => ({
      stroke: themeColors.axis,
      tick: { fill: themeColors.axis, fontSize: 12 },
    }),
    [themeColors],
  );

  function hasValues(series) {
    return Array.isArray(series) && series.some((point) => (point.value || 0) > 0);
  }

  const timeSeries = useMemo(() => {
    const conversations = data?.conversations_over_time || [];
    const customers = data?.customers_over_time || [];
    const byDate = new Map();
    conversations.forEach((point) => {
      byDate.set(point.date, { date: point.date, conversations: point.value, customers: 0 });
    });
    customers.forEach((point) => {
      const existing = byDate.get(point.date) || { date: point.date, conversations: 0, customers: 0 };
      existing.customers = point.value;
      byDate.set(point.date, existing);
    });
    return Array.from(byDate.values()).sort((a, b) => a.date.localeCompare(b.date));
  }, [data]);

  const timeSeriesHasData = timeSeries.some(
    (point) => (point.conversations || 0) > 0 || (point.customers || 0) > 0,
  );

  if (loading && !data) {
    return (
      <AppCard padding="medium">
        <LoadingState
          title="Loading analytics..."
          description="Aggregating conversations, customers and tickets for your company."
        />
      </AppCard>
    );
  }

  if (error && !data) {
    return (
      <AppCard padding="medium">
        <ErrorState
          title="Analytics could not load"
          description={error}
          action={
            <AppButton
              variant="primary"
              icon={<RefreshOutlined fontSize="small" />}
              onClick={() => loadAnalytics(range)}
            >
              Try again
            </AppButton>
          }
        />
      </AppCard>
    );
  }

  const shortDate = (value) => value.slice(5); // MM-DD for compact axis labels

  return (
    <div className="analytics-page">
      <PageHeader
        eyebrow="BUSINESS INTELLIGENCE"
        title="Analytics"
        description="Channel, employee, AI and customer performance across your company's conversations, customers and tickets."
        actions={
          <AppButton
            variant="secondary"
            icon={<RefreshOutlined fontSize="small" />}
            loading={loading}
            onClick={() => loadAnalytics(range)}
          >
            Refresh
          </AppButton>
        }
      />

      <AppCard padding="medium" className="analytics-controls">
        <div className="analytics-presets">
          {PRESETS.map((preset) => (
            <AppButton
              key={preset.key}
              size="small"
              variant={activePreset === preset.key ? "primary" : "ghost"}
              onClick={() => applyPreset(preset)}
            >
              {preset.label}
            </AppButton>
          ))}
        </div>

        <div className="analytics-range-fields">
          <label className="analytics-field">
            <span>From</span>
            <input
              type="date"
              className="tz-input"
              value={range.from}
              max={range.to}
              onChange={(event) => updateRangeField("from", event.target.value)}
            />
          </label>
          <label className="analytics-field">
            <span>To</span>
            <input
              type="date"
              className="tz-input"
              value={range.to}
              min={range.from}
              onChange={(event) => updateRangeField("to", event.target.value)}
            />
          </label>
          <AppButton
            size="small"
            variant="secondary"
            disabled={!range.from || !range.to}
            onClick={applyCustomRange}
          >
            Apply
          </AppButton>
        </div>

        {data?.range ? (
          <p className="analytics-range-note">
            Showing {data.range.from} to {data.range.to}
          </p>
        ) : null}
      </AppCard>

      {error ? <p className="analytics-inline-error">{error}</p> : null}

      <section className="statistics-grid">
        <StatTile
          title="Conversations"
          value={totals.conversations}
          description={`${totals.open_conversations || 0} still open`}
          icon={ChatOutlined}
        />
        <StatTile
          title="AI-handled"
          value={totals.ai_handled_conversations}
          description={`${totals.human_handled_conversations || 0} handled by a person`}
          icon={AutoAwesomeOutlined}
        />
        <StatTile
          title="New customers"
          value={totals.new_customers}
          description="First seen in this range"
          icon={GroupOutlined}
        />
        <StatTile
          title="Open tickets"
          value={totals.open_tickets}
          description={`${totals.tickets || 0} tickets created`}
          icon={SupportAgentOutlined}
        />
      </section>

      <ChartCard
        title="Conversations & new customers over time"
        subtitle="ACTIVITY TREND"
        isEmpty={!timeSeriesHasData}
      >
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={timeSeries} margin={{ top: 8, right: 16, bottom: 8, left: -12 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={themeColors.grid} />
            <XAxis dataKey="date" tickFormatter={shortDate} {...axisProps} />
            <YAxis allowDecimals={false} {...axisProps} />
            <Tooltip contentStyle={tooltipStyle} />
            <Legend />
            <Line
              type="monotone"
              dataKey="conversations"
              name="Conversations"
              stroke={SERIES_COLORS[0]}
              strokeWidth={2}
              dot={false}
            />
            <Line
              type="monotone"
              dataKey="customers"
              name="New customers"
              stroke={SERIES_COLORS[1]}
              strokeWidth={2}
              dot={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </ChartCard>

      <section className="analytics-grid">
        <ChartCard
          title="Conversations by channel"
          subtitle="REACH"
          isEmpty={!hasValues(data?.conversations_by_channel)}
        >
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={data?.conversations_by_channel || []} margin={{ top: 8, right: 16, bottom: 8, left: -12 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={themeColors.grid} />
              <XAxis dataKey="label" {...axisProps} />
              <YAxis allowDecimals={false} {...axisProps} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "rgba(57,174,242,0.08)" }} />
              <Bar dataKey="value" name="Conversations" radius={[6, 6, 0, 0]}>
                {(data?.conversations_by_channel || []).map((entry, index) => (
                  <Cell key={entry.label} fill={SERIES_COLORS[index % SERIES_COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard
          title="AI vs human handling"
          subtitle="AUTOMATION"
          isEmpty={!hasValues(data?.ai_vs_human)}
        >
          <ResponsiveContainer width="100%" height={280}>
            <PieChart>
              <Pie
                data={(data?.ai_vs_human || []).filter((entry) => entry.value > 0)}
                dataKey="value"
                nameKey="label"
                innerRadius={55}
                outerRadius={95}
                paddingAngle={2}
              >
                {(data?.ai_vs_human || []).map((entry) => (
                  <Cell key={entry.label} fill={AI_COLORS[entry.label] || SERIES_COLORS[0]} />
                ))}
              </Pie>
              <Tooltip contentStyle={tooltipStyle} />
              <Legend />
            </PieChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard
          title="Conversations by status"
          subtitle="PIPELINE"
          isEmpty={!hasValues(data?.conversations_by_status)}
        >
          <ResponsiveContainer width="100%" height={280}>
            <PieChart>
              <Pie
                data={(data?.conversations_by_status || []).filter((entry) => entry.value > 0)}
                dataKey="value"
                nameKey="label"
                outerRadius={95}
                paddingAngle={2}
              >
                {(data?.conversations_by_status || []).map((entry, index) => (
                  <Cell key={entry.label} fill={SERIES_COLORS[index % SERIES_COLORS.length]} />
                ))}
              </Pie>
              <Tooltip contentStyle={tooltipStyle} />
              <Legend />
            </PieChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard
          title="Conversations by department"
          subtitle="ROUTING"
          isEmpty={!hasValues(data?.conversations_by_department)}
        >
          <ResponsiveContainer width="100%" height={280}>
            <BarChart
              data={data?.conversations_by_department || []}
              layout="vertical"
              margin={{ top: 8, right: 16, bottom: 8, left: 8 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke={themeColors.grid} />
              <XAxis type="number" allowDecimals={false} {...axisProps} />
              <YAxis type="category" dataKey="label" width={110} {...axisProps} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "rgba(22,184,146,0.08)" }} />
              <Bar dataKey="value" name="Conversations" radius={[0, 6, 6, 0]}>
                {(data?.conversations_by_department || []).map((entry, index) => (
                  <Cell key={entry.label} fill={SERIES_COLORS[(index + 1) % SERIES_COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard
          title="Tickets by status"
          subtitle="SUPPORT"
          isEmpty={!hasValues(data?.tickets_by_status)}
          emptyLabel="No tickets were created in the selected date range."
        >
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={data?.tickets_by_status || []} margin={{ top: 8, right: 16, bottom: 8, left: -12 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={themeColors.grid} />
              <XAxis dataKey="label" {...axisProps} />
              <YAxis allowDecimals={false} {...axisProps} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "rgba(217,144,22,0.08)" }} />
              <Bar dataKey="value" name="Tickets" radius={[6, 6, 0, 0]}>
                {(data?.tickets_by_status || []).map((entry, index) => (
                  <Cell key={entry.label} fill={SERIES_COLORS[(index + 3) % SERIES_COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard
          title="Employee activity"
          subtitle="TEAM"
          isEmpty={!hasValues(data?.employee_activity)}
          emptyLabel="No conversations have been assigned to a team member in this range."
        >
          <ResponsiveContainer width="100%" height={280}>
            <BarChart
              data={data?.employee_activity || []}
              layout="vertical"
              margin={{ top: 8, right: 16, bottom: 8, left: 8 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke={themeColors.grid} />
              <XAxis type="number" allowDecimals={false} {...axisProps} />
              <YAxis type="category" dataKey="label" width={130} {...axisProps} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "rgba(124,108,240,0.08)" }} />
              <Bar dataKey="value" name="Conversations handled" radius={[0, 6, 6, 0]}>
                {(data?.employee_activity || []).map((entry, index) => (
                  <Cell key={entry.label} fill={SERIES_COLORS[(index + 4) % SERIES_COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </section>
    </div>
  );
}
