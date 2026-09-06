import { useCallback, useEffect, useMemo, useState } from "react";
import { FileDownloadOutlined, RefreshOutlined } from "@mui/icons-material";
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

import {
  downloadAnalyticsReportRequest,
  getAnalyticsSummaryRequest,
} from "../../api/analytics";
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

/*
 * A channel keeps its colour across the whole screen and across reloads,
 * because it is assigned by the channel's position in the server's own list
 * rather than by whatever order a chart happened to render. Recolouring
 * WhatsApp between two visits would make the two charts uncomparable.
 */
const SERIES_COLORS = [
  "var(--tz-primary)",
  "var(--tz-secondary)",
  "var(--tz-info)",
  "var(--tz-warning)",
  "var(--tz-brand-green)",
  "var(--tz-brand-blue-deep)",
];

function seriesColor(index) {
  return SERIES_COLORS[index % SERIES_COLORS.length];
}

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
  const [exporting, setExporting] = useState("");
  const [exportError, setExportError] = useState("");

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

  /*
   * The export is a file, so there is nothing on the screen to show for it
   * afterwards. The button reports its own progress and its own failure
   * instead — a download that silently does nothing is indistinguishable from
   * a broken one.
   */
  const exportReport = useCallback(
    async (name) => {
      setExporting(name);
      setExportError("");

      try {
        await downloadAnalyticsReportRequest({ report: name, days });
      } catch (requestError) {
        setExportError(
          requestError.message || "The report could not be exported.",
        );
      } finally {
        setExporting("");
      }
    },
    [days],
  );

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
  const departments = report?.by_department || [];
  const waitBuckets = firstResponse?.buckets || [];
  const percentiles = firstResponse?.percentiles || {};

  const channelSeries = report?.channel_trend?.channels || [];

  const channelTrend = useMemo(
    () =>
      (report?.channel_trend?.days || []).map((row) => ({
        ...row,
        label: formatDayLabel(row.day),
      })),
    [report],
  );

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
        key: "conversations",
        label: "Conversations",
        align: "right",
        render: (value) => formatNumber(value),
      },
      {
        key: "replies",
        label: "Replies sent",
        align: "right",
        render: (value) => formatNumber(value),
      },
      {
        key: "average_response_seconds",
        label: "Average reply",
        align: "right",
        /*
         * A dash, never a zero. An employee whose replies never followed a
         * waiting customer has no measured response time, and printing 0s
         * would read as "answered instantly" — the opposite of the truth.
         */
        render: (value, row) =>
          row.answered ? formatSeconds(value) : "—",
      },
      {
        key: "slowest_response_seconds",
        label: "Slowest reply",
        align: "right",
        render: (value, row) =>
          row.answered ? formatSeconds(value) : "—",
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

  const departmentColumns = useMemo(
    () => [
      {
        key: "name",
        label: "Section",
        render: (value, row) => (
          <>
            <strong>{value}</strong>
            {row.defined ? null : (
              <small className="analytics-note"> not in the section list</small>
            )}
          </>
        ),
      },
      {
        key: "conversations",
        label: "Conversations",
        align: "right",
        render: (value) => formatNumber(value),
      },
      {
        key: "messages",
        label: "Messages",
        align: "right",
        render: (value) => formatNumber(value),
      },
      {
        key: "automation_rate",
        label: "Answered by assistant",
        align: "right",
        render: (value) => formatPercent(value),
      },
      {
        key: "waiting_for_human",
        label: "Waiting now",
        align: "right",
        render: (value) =>
          Number(value) ? (
            <strong className="analytics-alert">{formatNumber(value)}</strong>
          ) : (
            formatNumber(value)
          ),
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

      {exportError ? (
        <ErrorState
          title="The export did not download"
          description={exportError}
          action={
            <AppButton variant="secondary" onClick={() => setExportError("")}>
              Dismiss
            </AppButton>
          }
        />
      ) : null}

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

              <div className="analytics-head-actions">
                <AppButton
                  variant="ghost"
                  size="small"
                  onClick={() => setVolumeAsTable((current) => !current)}
                >
                  {volumeAsTable ? "Show chart" : "Show table"}
                </AppButton>

                <AppButton
                  variant="ghost"
                  size="small"
                  icon={<FileDownloadOutlined fontSize="small" />}
                  disabled={exporting === "volume"}
                  onClick={() => exportReport("volume")}
                >
                  {exporting === "volume" ? "Exporting..." : "Export CSV"}
                </AppButton>
              </div>
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

          <AppCard padding="medium" className="analytics-chart-card">
            <header className="analytics-section-head">
              <div>
                <h3>Channels over time</h3>
                <p>
                  Messages per channel, per day. The period totals below cannot
                  show a channel that went quiet three weeks ago — its total
                  still looks healthy.
                </p>
              </div>

              <AppButton
                variant="ghost"
                size="small"
                icon={<FileDownloadOutlined fontSize="small" />}
                disabled={exporting === "channels"}
                onClick={() => exportReport("channels")}
              >
                {exporting === "channels" ? "Exporting..." : "Export CSV"}
              </AppButton>
            </header>

            {channelTrend.length === 0 ? (
              <EmptyState
                title="No channel activity"
                description="No messages were exchanged on any connected channel in this period."
              />
            ) : (
              <div className="analytics-chart">
                <ResponsiveContainer width="100%" height={280}>
                  <AreaChart
                    data={channelTrend}
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
                    {channelSeries.map((channel, index) => (
                      <Area
                        key={channel}
                        type="monotone"
                        dataKey={channel}
                        name={humanize(channel)}
                        stackId="channels"
                        stroke={seriesColor(index)}
                        strokeWidth={2}
                        fill={seriesColor(index)}
                        fillOpacity={0.18}
                      />
                    ))}
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

          <AppCard padding="medium" className="analytics-chart-card">
            <header className="analytics-section-head">
              <div>
                <h3>How long customers waited</h3>
                <p>
                  Every conversation placed in the band it actually waited in.
                  An average of four minutes and a customer who waited nine
                  hours are the same average; only this is the customer.
                </p>
              </div>

              <AppButton
                variant="ghost"
                size="small"
                icon={<FileDownloadOutlined fontSize="small" />}
                disabled={exporting === "response"}
                onClick={() => exportReport("response")}
              >
                {exporting === "response" ? "Exporting..." : "Export CSV"}
              </AppButton>
            </header>

            <div className="analytics-wait-split">
              <div className="analytics-chart">
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart
                    data={waitBuckets}
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
                      interval={0}
                    />
                    <YAxis
                      tick={AXIS_TICK}
                      tickLine={false}
                      axisLine={false}
                      width={44}
                      allowDecimals={false}
                    />
                    <Tooltip {...TOOLTIP_STYLES} />
                    <Bar
                      dataKey="conversations"
                      name="Conversations"
                      fill={INBOUND_COLOR}
                      radius={[4, 4, 0, 0]}
                      maxBarSize={54}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>

              <div className="analytics-facts analytics-facts-narrow">
                {[
                  ["p50", "Half of customers waited less than this"],
                  ["p75", "Three in four waited less than this"],
                  ["p90", "Nine in ten waited less than this"],
                  ["p95", "The worst one in twenty starts here"],
                ].map(([key, note]) => (
                  <div key={key}>
                    <span>{key}</span>
                    <strong>{formatSeconds(percentiles[key])}</strong>
                    <small>{note}</small>
                  </div>
                ))}
              </div>
            </div>

            <div className="analytics-split analytics-waits">
              <div>
                <h4>Longest waits</h4>

                {(firstResponse?.slowest || []).length === 0 ? (
                  <p className="analytics-note">
                    Nothing was answered in this period.
                  </p>
                ) : (
                  <ul className="analytics-wait-list">
                    {firstResponse.slowest.map((row) => (
                      <li key={`answered-${row.conversation_id}`}>
                        <div>
                          <strong>{row.customer}</strong>
                          <span>{humanize(row.channel)}</span>
                        </div>
                        <b>{formatSeconds(row.waited_seconds)}</b>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {/*
                Never hidden and never merged into the list above. A customer
                who wrote and was never answered is the single most actionable
                row on this screen, and a wait figure would imply somebody
                eventually replied.
              */}
              <div>
                <h4>Never answered</h4>

                {(firstResponse?.never_answered || []).length === 0 ? (
                  <p className="analytics-note">
                    Everyone who wrote in got a reply.
                  </p>
                ) : (
                  <ul className="analytics-wait-list is-bad">
                    {firstResponse.never_answered.map((row) => (
                      <li key={`unanswered-${row.conversation_id}`}>
                        <div>
                          <strong>{row.customer}</strong>
                          <span>{humanize(row.channel)}</span>
                        </div>
                        <b>no reply</b>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </AppCard>

          <AppCard padding="medium">
            <header className="analytics-section-head">
              <div>
                <h3>By section</h3>
                <p>
                  Which part of the business the work landed in, how much of it
                  the assistant carried, and what is queued for a person right
                  now.
                </p>
              </div>

              <AppButton
                variant="ghost"
                size="small"
                icon={<FileDownloadOutlined fontSize="small" />}
                disabled={exporting === "departments"}
                onClick={() => exportReport("departments")}
              >
                {exporting === "departments" ? "Exporting..." : "Export CSV"}
              </AppButton>
            </header>

            <AppTable
              columns={departmentColumns}
              rows={departments}
              rowKey="code"
              emptyTitle="No section activity"
              emptyDescription="No conversation carried a section in this period."
              page={1}
              pageSize={Math.max(departments.length, 1)}
              totalRows={departments.length}
            />
          </AppCard>

          <AppCard padding="medium">
            <header className="analytics-section-head">
              <div>
                <h3>Employee performance</h3>
                <p>
                  Conversations handled, replies written, and how long a
                  customer waited for them. Reply time is measured only where a
                  reply actually answered a waiting customer.
                </p>
              </div>

              <AppButton
                variant="ghost"
                size="small"
                icon={<FileDownloadOutlined fontSize="small" />}
                disabled={exporting === "employees"}
                onClick={() => exportReport("employees")}
              >
                {exporting === "employees" ? "Exporting..." : "Export CSV"}
              </AppButton>
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
