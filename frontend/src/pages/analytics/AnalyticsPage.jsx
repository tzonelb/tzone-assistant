import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshOutlined } from "@mui/icons-material";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { getAnalyticsSummaryRequest } from "../../api/analytics";
import {
  AppButton,
  AppCard,
  AppTable,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "../../components/common";
import { formatPlatformDateTime, getUserTimezone } from "../../utils/dateTime";
import "./AnalyticsPage.css";

const RANGES = [
  [7, "7 days"],
  [30, "30 days"],
  [90, "90 days"],
  [365, "12 months"],
];

// Two series, assigned in a fixed order and never recoloured when the range
// changes: inbound is always the brand blue, outbound always the teal.
const INBOUND_COLOR = "var(--tz-primary)";
const OUTBOUND_COLOR = "var(--tz-secondary)";

const AXIS_TICK = { fill: "var(--tz-text-muted)", fontSize: 11 };
const AXIS_LINE = { stroke: "var(--tz-border)" };

const TOOLTIP_STYLES = {
  contentStyle: {
    background: "var(--tz-surface)",
    border: "1px solid var(--tz-border)",
    borderRadius: "var(--tz-radius-sm)",
    boxShadow: "var(--tz-shadow-sm)",
    fontSize: 12,
  },
  labelStyle: { color: "var(--tz-text-secondary)", fontWeight: 700 },
  itemStyle: { color: "var(--tz-text-primary)" },
  cursor: { fill: "var(--tz-surface-secondary)", stroke: "var(--tz-border)" },
};

function humanize(value) {
  return String(value ?? "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString();
}

function formatPercent(rate) {
  if (rate === null || rate === undefined) return "—";
  return `${(Number(rate) * 100).toFixed(1)}%`;
}

/*
 * Durations arrive as raw seconds. A reporting screen that prints "5426" has
 * not reported anything, so every duration is rendered in units a person reads.
 */
function formatSeconds(seconds) {
  if (seconds === null || seconds === undefined) return "—";

  const total = Math.max(0, Number(seconds));

  if (!Number.isFinite(total)) return "—";
  if (total < 1) return `${Math.round(total * 1000)} ms`;
  if (total < 60) return `${total < 10 ? total.toFixed(1) : Math.round(total)}s`;

  const minutes = Math.floor(total / 60);
  const restSeconds = Math.round(total % 60);

  if (minutes < 60) {
    return restSeconds ? `${minutes}m ${restSeconds}s` : `${minutes}m`;
  }

  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;

  if (hours < 24) {
    return restMinutes ? `${hours}h ${restMinutes}m` : `${hours}h`;
  }

  const days = Math.floor(hours / 24);
  const restHours = hours % 24;
  return restHours ? `${days}d ${restHours}h` : `${days}d`;
}

function formatMilliseconds(milliseconds) {
  if (milliseconds === null || milliseconds === undefined) return "—";
  return formatSeconds(Number(milliseconds) / 1000);
}

function formatDayLabel(day) {
  const date = new Date(`${day}T00:00:00Z`);

  if (Number.isNaN(date.getTime())) return String(day || "");

  return new Intl.DateTimeFormat(undefined, {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  }).format(date);
}

export default function AnalyticsPage() {
  const [days, setDays] = useState(30);
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [volumeAsTable, setVolumeAsTable] = useState(false);

  const loadReport = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      setReport(await getAnalyticsSummaryRequest({ days }));
    } catch (requestError) {
      setReport(null);
      setError(requestError.message || "The report could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    loadReport();
  }, [loadReport]);

  const overview = report?.overview || null;
  const assistant = report?.assistant || null;
  const firstResponse = report?.first_response || null;

  const volume = useMemo(
    () =>
      (report?.volume_by_day || []).map((row) => ({
        ...row,
        label: formatDayLabel(row.day),
      })),
    [report],
  );

  const hourly = report?.hourly_distribution || [];
  const byChannel = report?.by_channel || [];
  const employees = report?.employees || [];

  const channelPeak = useMemo(
    () =>
      byChannel.reduce(
        (highest, row) => Math.max(highest, Number(row.messages || 0)),
        0,
      ),
    [byChannel],
  );

  const volumeColumns = useMemo(
    () => [
      { key: "day", label: "Day", render: (value) => formatDayLabel(value) },
      {
        key: "inbound",
        label: "Inbound",
        align: "right",
        render: (value) => formatNumber(value),
      },
      {
        key: "outbound",
        label: "Outbound",
        align: "right",
        render: (value) => formatNumber(value),
      },
      {
        key: "total",
        label: "Total",
        align: "right",
        render: (value) => formatNumber(value),
      },
    ],
    [],
  );

  const employeeColumns = useMemo(
    () => [
      {
        key: "name",
        label: "Employee",
        render: (value, row) => (
          <strong>{value || `User ${row.user_id}`}</strong>
        ),
      },
      {
        key: "replies",
        label: "Replies sent",
        align: "right",
        render: (value) => formatNumber(value),
      },
      {
        key: "takeovers",
        label: "Takeovers",
        align: "right",
        render: (value) => formatNumber(value),
      },
    ],
    [],
  );

  const tiles = overview
    ? [
        ["Messages", formatNumber(overview.messages?.total), null],
        ["Inbound", formatNumber(overview.messages?.inbound), null],
        ["Outbound", formatNumber(overview.messages?.outbound), null],
        [
          "Answered by assistant",
          formatNumber(overview.messages?.by_assistant),
          `${formatPercent(overview.messages?.automation_rate)} of replies`,
        ],
        [
          "Answered by employees",
          formatNumber(overview.messages?.by_employee),
          null,
        ],
        [
          "New conversations",
          formatNumber(overview.conversations?.new_in_range),
          `${formatNumber(overview.conversations?.total)} in total`,
        ],
        ["New customers", formatNumber(overview.customers?.new_in_range), null],
        [
          "Waiting for a human",
          formatNumber(overview.conversations?.needs_human),
          `${formatNumber(overview.conversations?.unread)} unread`,
        ],
      ]
    : [];

  return (
    <div className="analytics-page">
      <PageHeader
        eyebrow="REPORTING"
        title="Analytics"
        description={
          overview
            ? `Conversation activity for the last ${overview.range_days} days, from ${formatPlatformDateTime(overview.from)} to ${formatPlatformDateTime(overview.to)} (${getUserTimezone()}).`
            : "Conversation volume, channel mix, assistant health and response times over the selected period."
        }
        actions={
          <AppButton
            variant="secondary"
            icon={<RefreshOutlined fontSize="small" />}
            onClick={loadReport}
          >
            Refresh
          </AppButton>
        }
      />

      <div className="analytics-range" role="group" aria-label="Reporting range">
        {RANGES.map(([value, label]) => (
          <button
            type="button"
            key={value}
            className={`analytics-range-button ${days === value ? "is-active" : ""}`}
            aria-pressed={days === value}
            onClick={() => setDays(value)}
          >
            {label}
          </button>
        ))}
      </div>

      {error ? (
        <ErrorState
          title="The report could not load"
          description={error}
          action={
            <AppButton variant="primary" onClick={loadReport}>
              Try again
            </AppButton>
          }
        />
      ) : null}

      {loading && !report ? (
        <LoadingState
          title="Building the report..."
          description="Aggregating messages, conversations and diagnostics for this period."
        />
      ) : null}

      {report ? (
        <>
          <section className="analytics-tiles">
            {tiles.map(([label, value, footnote]) => (
              <AppCard padding="medium" className="analytics-tile" key={label}>
                <span>{label}</span>
                <strong>{value}</strong>
                {footnote ? <small>{footnote}</small> : null}
              </AppCard>
            ))}
          </section>

          <AppCard padding="medium" className="analytics-chart-card">
            <header className="analytics-section-head">
              <div>
                <h3>Message volume</h3>
                <p>
                  Inbound messages from customers against outbound replies, per
                  day.
                </p>
              </div>

              <AppButton
                variant="ghost"
                size="small"
                onClick={() => setVolumeAsTable((current) => !current)}
              >
                {volumeAsTable ? "Show chart" : "Show table"}
              </AppButton>
            </header>

            {volume.length === 0 ? (
              <EmptyState
                title="No messages in this period"
                description="Nothing was sent or received in the selected range."
              />
            ) : volumeAsTable ? (
              <AppTable
                columns={volumeColumns}
                rows={volume}
                rowKey="day"
                page={1}
                pageSize={Math.max(volume.length, 1)}
                totalRows={volume.length}
              />
            ) : (
              <div className="analytics-chart">
                <ResponsiveContainer width="100%" height={300}>
                  <AreaChart
                    data={volume}
                    margin={{ top: 8, right: 12, bottom: 0, left: 0 }}
                  >
                    <CartesianGrid
                      stroke="var(--tz-border)"
                      strokeDasharray="3 3"
                      vertical={false}
                    />
                    <XAxis
                      dataKey="label"
                      tick={AXIS_TICK}
                      tickLine={false}
                      axisLine={AXIS_LINE}
                      minTickGap={24}
                    />
                    <YAxis
                      tick={AXIS_TICK}
                      tickLine={false}
                      axisLine={false}
                      width={44}
                      allowDecimals={false}
                    />
                    <Tooltip {...TOOLTIP_STYLES} />
                    <Legend
                      wrapperStyle={{ fontSize: 12, paddingTop: 8 }}
                      iconType="plainline"
                    />
                    <Area
                      type="monotone"
                      dataKey="inbound"
                      name="Inbound"
                      stroke={INBOUND_COLOR}
                      strokeWidth={2}
                      fill={INBOUND_COLOR}
                      fillOpacity={0.14}
                      activeDot={{ r: 4, strokeWidth: 2 }}
                    />
                    <Area
                      type="monotone"
                      dataKey="outbound"
                      name="Outbound"
                      stroke={OUTBOUND_COLOR}
                      strokeWidth={2}
                      fill={OUTBOUND_COLOR}
                      fillOpacity={0.14}
                      activeDot={{ r: 4, strokeWidth: 2 }}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}
          </AppCard>

          <div className="analytics-split">
            <AppCard padding="medium" className="analytics-chart-card">
              <header className="analytics-section-head">
                <div>
                  <h3>When customers write</h3>
                  <p>
                    Inbound messages by hour of day (UTC), across the whole
                    period.
                  </p>
                </div>
              </header>

              <div className="analytics-chart">
                <ResponsiveContainer width="100%" height={260}>
                  <BarChart
                    data={hourly}
                    margin={{ top: 8, right: 12, bottom: 0, left: 0 }}
                  >
                    <CartesianGrid
                      stroke="var(--tz-border)"
                      strokeDasharray="3 3"
                      vertical={false}
                    />
                    <XAxis
                      dataKey="hour"
                      tick={AXIS_TICK}
                      tickLine={false}
                      axisLine={AXIS_LINE}
                      interval={1}
                    />
                    <YAxis
                      tick={AXIS_TICK}
                      tickLine={false}
                      axisLine={false}
                      width={44}
                      allowDecimals={false}
                    />
                    <Tooltip
                      {...TOOLTIP_STYLES}
                      labelFormatter={(hour) => `${hour}:00 UTC`}
                    />
                    <Bar
                      dataKey="messages"
                      name="Inbound messages"
                      fill={INBOUND_COLOR}
                      radius={[4, 4, 0, 0]}
                      maxBarSize={22}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </AppCard>

            <AppCard padding="medium" className="analytics-channels-card">
              <header className="analytics-section-head">
                <div>
                  <h3>By channel</h3>
                  <p>Which channels the company&apos;s customers actually use.</p>
                </div>
              </header>

              {byChannel.length === 0 ? (
                <EmptyState
                  title="No channel activity"
                  description="No messages were exchanged on any connected channel in this period."
                />
              ) : (
                <ul className="analytics-channel-list">
                  {byChannel.map((row) => (
                    <li key={row.channel}>
                      <div className="analytics-channel-line">
                        <strong>{humanize(row.channel)}</strong>
                        <span>
                          {formatNumber(row.messages)} messages ·{" "}
                          {formatNumber(row.conversations)} conversations
                        </span>
                      </div>

                      <div className="analytics-channel-meter">
                        <div
                          style={{
                            width: `${channelPeak ? (Number(row.messages || 0) / channelPeak) * 100 : 0}%`,
                          }}
                        />
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </AppCard>
          </div>

          <div className="analytics-split">
            <AppCard padding="medium">
              <header className="analytics-section-head">
                <div>
                  <h3>Assistant health</h3>
                  <p>
                    How much the assistant carried, and how reliably it
                    delivered.
                  </p>
                </div>
              </header>

              <div className="analytics-facts">
                <div>
                  <span>Automation rate</span>
                  <strong>
                    {formatPercent(overview?.messages?.automation_rate)}
                  </strong>
                  <small>of all outbound replies</small>
                </div>

                <div className={Number(assistant?.replies_failed) ? "is-bad" : ""}>
                  <span>Failure rate</span>
                  <strong>{formatPercent(assistant?.failure_rate)}</strong>
                  <small>
                    {formatNumber(assistant?.replies_failed)} failed of{" "}
                    {formatNumber(
                      Number(assistant?.replies_sent || 0) +
                        Number(assistant?.replies_failed || 0),
                    )}{" "}
                    attempts
                  </small>
                </div>

                <div>
                  <span>Average reply time</span>
                  <strong>
                    {formatMilliseconds(assistant?.average_reply_ms)}
                  </strong>
                  <small>assistant replies only</small>
                </div>

                <div>
                  <span>Handed to a human</span>
                  <strong>{formatNumber(assistant?.handovers_to_human)}</strong>
                  <small>after waiting for a person</small>
                </div>

                <div>
                  <span>Queued now</span>
                  <strong>{formatNumber(assistant?.queued_now)}</strong>
                  <small>replies waiting to go out</small>
                </div>

                <div>
                  <span>Replies sent</span>
                  <strong>{formatNumber(assistant?.replies_sent)}</strong>
                  <small>published without a person</small>
                </div>
              </div>
            </AppCard>

            <AppCard padding="medium">
              <header className="analytics-section-head">
                <div>
                  <h3>First response time</h3>
                  <p>
                    How long a customer waits between their first message and
                    the first reply.
                  </p>
                </div>
              </header>

              <div className="analytics-facts">
                <div>
                  <span>Average</span>
                  <strong>
                    {formatSeconds(firstResponse?.average_seconds)}
                  </strong>
                  <small>across answered conversations</small>
                </div>

                <div>
                  <span>Median</span>
                  <strong>{formatSeconds(firstResponse?.median_seconds)}</strong>
                  <small>the typical customer&apos;s wait</small>
                </div>

                <div>
                  <span>Answered</span>
                  <strong>{formatNumber(firstResponse?.answered)}</strong>
                  <small>conversations replied to</small>
                </div>

                {/*
                  Never hidden. An unanswered conversation is a customer who
                  wrote and got nothing back, which is the number this screen
                  exists to expose.
                */}
                <div
                  className={
                    Number(firstResponse?.unanswered) ? "is-bad" : ""
                  }
                >
                  <span>Still unanswered</span>
                  <strong>{formatNumber(firstResponse?.unanswered)}</strong>
                  <small>no reply has ever been sent</small>
                </div>
              </div>
            </AppCard>
          </div>

          <AppCard padding="medium">
            <header className="analytics-section-head">
              <div>
                <h3>Employee activity</h3>
                <p>Replies written by people, and conversations taken over.</p>
              </div>
            </header>

            <AppTable
              columns={employeeColumns}
              rows={employees}
              rowKey="user_id"
              emptyTitle="No employee activity"
              emptyDescription="No employee replied or took over a conversation in this period."
              page={1}
              pageSize={Math.max(employees.length, 1)}
              totalRows={employees.length}
            />
          </AppCard>
        </>
      ) : null}
    </div>
  );
}
