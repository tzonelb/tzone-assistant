import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AddOutlined,
  CloseOutlined,
  RefreshOutlined,
} from "@mui/icons-material";

import {
  approveScheduledPostRequest,
  cancelScheduledPostRequest,
  createScheduledPostRequest,
  getScheduledPostsRequest,
  updateScheduledPostRequest,
} from "../../api/scheduler";
import {
  AppButton,
  AppCard,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
} from "../../components/common";
import { getUserTimezone, parsePlatformDate } from "../../utils/dateTime";
import "./SchedulerPage.css";

const CHANNELS = [
  ["messenger", "Facebook Page"],
  ["instagram", "Instagram"],
];

const STATUS_TONES = {
  draft: "neutral",
  approved: "info",
  published: "success",
  failed: "danger",
  cancelled: "warning",
};

const STATUS_HINTS = {
  draft: "Not approved. It will not be published.",
  approved: "Approved. It goes out at the scheduled time.",
  published: "Already public. It can no longer be edited.",
  failed: "Every attempt failed. Approve it again to retry.",
  cancelled: "Cancelled. It will never be published.",
};

function humanize(value) {
  return String(value ?? "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

/*
 * ---------------------------------------------------------------------------
 * Timezone
 *
 * The server stores and returns `scheduled_for` as UTC. This screen shows and
 * edits it in the platform display timezone, so both directions are converted
 * explicitly here rather than leaning on whatever timezone the browser happens
 * to be in.
 * ---------------------------------------------------------------------------
 */

function zoneOffsetMinutes(date, timeZone) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone,
      hour12: false,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    })
      .formatToParts(date)
      .map((part) => [part.type, part.value]),
  );

  const asUtc = Date.UTC(
    Number(parts.year),
    Number(parts.month) - 1,
    Number(parts.day),
    Number(parts.hour) % 24,
    Number(parts.minute),
    Number(parts.second),
  );

  return (asUtc - date.getTime()) / 60000;
}

// "2026-04-20T17:00" understood in `timeZone` -> UTC ISO for the server.
function zonedInputToUtcIso(localValue, timeZone) {
  if (!localValue) return "";

  const guess = new Date(`${localValue}:00Z`);

  if (Number.isNaN(guess.getTime())) return "";

  // Applied twice so a value that lands on a daylight-saving change is
  // resolved against the offset actually in force at that moment.
  let utc = new Date(guess.getTime() - zoneOffsetMinutes(guess, timeZone) * 60000);
  utc = new Date(guess.getTime() - zoneOffsetMinutes(utc, timeZone) * 60000);

  return utc.toISOString();
}

// UTC ISO from the server -> "2026-04-20T17:00" for a datetime-local input.
function utcIsoToZonedInput(value, timeZone) {
  const date = parsePlatformDate(value);

  if (!date) return "";

  const shifted = new Date(
    date.getTime() + zoneOffsetMinutes(date, timeZone) * 60000,
  );

  return shifted.toISOString().slice(0, 16);
}

function formatZonedTime(value, timeZone) {
  const date = parsePlatformDate(value);

  if (!date) return "—";

  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    timeZone,
  }).format(date);
}

function formatZonedDay(value, timeZone) {
  const date = parsePlatformDate(value);

  if (!date) return "Unscheduled";

  return new Intl.DateTimeFormat(undefined, {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone,
  }).format(date);
}

function dayKey(value, timeZone) {
  const date = parsePlatformDate(value);

  if (!date) return "unscheduled";

  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    })
      .formatToParts(date)
      .map((part) => [part.type, part.value]),
  );

  return `${parts.year}-${parts.month}-${parts.day}`;
}

function emptyForm() {
  return {
    channel: "messenger",
    body: "",
    scheduled_for: "",
    media_url: "",
    link_url: "",
  };
}

function formFromPost(post, timeZone) {
  return {
    channel: post.channel || "messenger",
    body: post.body || "",
    scheduled_for: utcIsoToZonedInput(post.scheduled_for, timeZone),
    media_url: post.media_url || "",
    link_url: post.link_url || "",
  };
}

export default function SchedulerPage() {
  const timeZone = getUserTimezone();

  const [status, setStatus] = useState("");
  const [channel, setChannel] = useState("all");
  const [startsAfter, setStartsAfter] = useState("");
  const [endsBefore, setEndsBefore] = useState("");

  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [statusCounts, setStatusCounts] = useState({});
  const [statuses, setStatuses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [composerOpen, setComposerOpen] = useState(false);
  const [editingPost, setEditingPost] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [saveStatus, setSaveStatus] = useState("");

  const [actionError, setActionError] = useState("");
  const [busyPostId, setBusyPostId] = useState(null);
  const [pendingCancel, setPendingCancel] = useState(null);
  const [cancelling, setCancelling] = useState(false);

  const loadPosts = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const result = await getScheduledPostsRequest({
        status,
        channel,
        // A day picked in the display timezone has to become the real UTC
        // instant that day starts and ends, or the filter silently drops posts.
        startsAfter: startsAfter
          ? zonedInputToUtcIso(`${startsAfter}T00:00`, timeZone)
          : "",
        endsBefore: endsBefore
          ? zonedInputToUtcIso(`${endsBefore}T23:59`, timeZone)
          : "",
        limit: 200,
      });

      setItems(Array.isArray(result?.items) ? result.items : []);
      setTotal(Number(result?.total || 0));
      setStatusCounts(result?.status_counts || {});
      setStatuses(Array.isArray(result?.statuses) ? result.statuses : []);
    } catch (requestError) {
      setError(
        requestError.message || "The publishing calendar could not be loaded.",
      );
      setItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [status, channel, startsAfter, endsBefore, timeZone]);

  useEffect(() => {
    loadPosts();
  }, [loadPosts]);

  const grouped = useMemo(() => {
    const days = new Map();

    items.forEach((item) => {
      const key = dayKey(item.scheduled_for, timeZone);

      if (!days.has(key)) {
        days.set(key, { key, scheduledFor: item.scheduled_for, posts: [] });
      }

      days.get(key).posts.push(item);
    });

    return [...days.values()];
  }, [items, timeZone]);

  function openComposer() {
    setEditingPost(null);
    setForm(emptyForm());
    setFormError("");
    setSaveStatus("");
    setComposerOpen(true);
  }

  function openEditor(post) {
    setEditingPost(post);
    setForm(formFromPost(post, timeZone));
    setFormError("");
    setSaveStatus("");
    setComposerOpen(true);
  }

  function closeComposer() {
    setComposerOpen(false);
    setEditingPost(null);
    setFormError("");
    setSaveStatus("");
  }

  function updateField(key, value) {
    setSaveStatus("");
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function handleSubmit(event) {
    event.preventDefault();

    const scheduledForUtc = zonedInputToUtcIso(form.scheduled_for, timeZone);

    if (!scheduledForUtc) {
      setFormError("Choose the date and time this post should go out.");
      return;
    }

    setSaving(true);
    setFormError("");
    setSaveStatus("");

    try {
      if (editingPost) {
        await updateScheduledPostRequest(editingPost.id, {
          body: form.body.trim(),
          media_url: form.media_url.trim() || null,
          link_url: form.link_url.trim() || null,
          scheduled_for: scheduledForUtc,
        });
        setSaveStatus("Post updated.");
      } else {
        await createScheduledPostRequest({
          channel: form.channel,
          body: form.body.trim(),
          media_url: form.media_url.trim() || null,
          link_url: form.link_url.trim() || null,
          scheduled_for: scheduledForUtc,
        });
        setSaveStatus("Post saved as a draft. Approve it so it goes out.");
      }

      await loadPosts();

      if (!editingPost) {
        setForm(emptyForm());
        setComposerOpen(false);
      }
    } catch (requestError) {
      setFormError(requestError.message || "The post could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  async function handleApprove(post) {
    setBusyPostId(post.id);
    setActionError("");

    try {
      await approveScheduledPostRequest(post.id);
      await loadPosts();
    } catch (requestError) {
      setActionError(
        requestError.message || "The post could not be approved.",
      );
    } finally {
      setBusyPostId(null);
    }
  }

  async function handleCancel() {
    if (!pendingCancel) return;

    setCancelling(true);
    setActionError("");

    try {
      await cancelScheduledPostRequest(pendingCancel.id);
      setPendingCancel(null);

      if (editingPost?.id === pendingCancel.id) {
        closeComposer();
      }

      await loadPosts();
    } catch (requestError) {
      setActionError(
        requestError.message || "The post could not be cancelled.",
      );
      setPendingCancel(null);
    } finally {
      setCancelling(false);
    }
  }

  return (
    <div className="scheduler-page">
      <PageHeader
        eyebrow="PUBLISHING CALENDAR"
        title="Scheduler"
        description={`Posts queued for this company's connected pages. Times are shown in ${timeZone}; the server stores every schedule in UTC.`}
        actions={
          <>
            <AppButton
              variant="secondary"
              icon={<RefreshOutlined fontSize="small" />}
              onClick={loadPosts}
            >
              Refresh
            </AppButton>

            <AppButton
              variant="primary"
              icon={<AddOutlined fontSize="small" />}
              onClick={openComposer}
            >
              Schedule a post
            </AppButton>
          </>
        }
      />

      <section className="scheduler-counts">
        <button
          type="button"
          className={`scheduler-count ${status === "" ? "is-active" : ""}`}
          aria-pressed={status === ""}
          onClick={() => setStatus("")}
        >
          <span>All</span>
          {/*
            Summed from the per-status counts, which the server reports across
            the whole calendar. `total` below counts only the current filter, so
            using it here would make the tile shrink as soon as one is applied.
          */}
          <strong>
            {Object.values(statusCounts).reduce(
              (sum, value) => sum + Number(value || 0),
              0,
            )}
          </strong>
        </button>

        {(statuses.length ? statuses : Object.keys(statusCounts)).map(
          (value) => (
            <button
              type="button"
              key={value}
              className={`scheduler-count ${status === value ? "is-active" : ""}`}
              aria-pressed={status === value}
              onClick={() => setStatus(value)}
            >
              <span>{humanize(value)}</span>
              <strong>{Number(statusCounts[value] || 0)}</strong>
            </button>
          ),
        )}
      </section>

      <div className={`scheduler-layout ${composerOpen ? "has-composer" : ""}`}>
        <AppCard padding="medium" className="scheduler-calendar-card">
          <div className="scheduler-filters">
            <label>
              <span>Channel</span>

              <select
                value={channel}
                onChange={(event) => setChannel(event.target.value)}
              >
                <option value="all">All channels</option>
                {CHANNELS.map(([value, label]) => (
                  <option value={value} key={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>

            <label>
              <span>From ({timeZone})</span>

              <input
                type="date"
                value={startsAfter}
                onChange={(event) => setStartsAfter(event.target.value)}
              />
            </label>

            <label>
              <span>To ({timeZone})</span>

              <input
                type="date"
                value={endsBefore}
                onChange={(event) => setEndsBefore(event.target.value)}
              />
            </label>

            {startsAfter || endsBefore || status || channel !== "all" ? (
              <AppButton
                variant="ghost"
                size="small"
                onClick={() => {
                  setStartsAfter("");
                  setEndsBefore("");
                  setStatus("");
                  setChannel("all");
                }}
              >
                Clear filters
              </AppButton>
            ) : null}
          </div>

          {!loading && !error && total ? (
            <p className="scheduler-result-count">
              Showing {items.length} of {total}{" "}
              {total === 1 ? "post" : "posts"} matching these filters.
            </p>
          ) : null}

          {actionError ? (
            <div className="scheduler-action-error" role="alert">
              {actionError}
            </div>
          ) : null}

          {loading ? (
            <LoadingState title="Loading the calendar..." />
          ) : null}

          {!loading && error ? (
            <ErrorState
              title="The calendar could not load"
              description={error}
              action={
                <AppButton variant="primary" onClick={loadPosts}>
                  Try again
                </AppButton>
              }
            />
          ) : null}

          {!loading && !error && grouped.length === 0 ? (
            <EmptyState
              title="Nothing is scheduled"
              description="Schedule a post and approve it, and it will be published automatically at the time you choose."
            />
          ) : null}

          {!loading && !error
            ? grouped.map((group) => (
                <section className="scheduler-day" key={group.key}>
                  <header>
                    <h3>{formatZonedDay(group.scheduledFor, timeZone)}</h3>
                    <span>
                      {group.posts.length}{" "}
                      {group.posts.length === 1 ? "post" : "posts"}
                    </span>
                  </header>

                  <ul>
                    {group.posts.map((post) => (
                      <li
                        key={post.id}
                        className={`scheduler-post status-${post.status}`}
                      >
                        <div className="scheduler-post-time">
                          <strong>
                            {formatZonedTime(post.scheduled_for, timeZone)}
                          </strong>
                          <small>{timeZone}</small>
                        </div>

                        <div className="scheduler-post-body">
                          <div className="scheduler-post-head">
                            <StatusBadge
                              status={post.status}
                              tone={STATUS_TONES[post.status]}
                              label={humanize(post.status)}
                            />

                            <span className="scheduler-post-channel">
                              {CHANNELS.find(
                                ([value]) => value === post.channel,
                              )?.[1] || humanize(post.channel)}
                            </span>

                            {post.created_by_name ? (
                              <span className="scheduler-post-author">
                                by {post.created_by_name}
                              </span>
                            ) : null}
                          </div>

                          <p>{post.body}</p>

                          <div className="scheduler-post-links">
                            {post.media_url ? (
                              <a
                                href={post.media_url}
                                target="_blank"
                                rel="noreferrer"
                              >
                                Media
                              </a>
                            ) : null}

                            {post.link_url ? (
                              <a
                                href={post.link_url}
                                target="_blank"
                                rel="noreferrer"
                              >
                                Link
                              </a>
                            ) : null}
                          </div>

                          <small className="scheduler-post-hint">
                            {STATUS_HINTS[post.status] || ""}
                            {post.approved_by_name
                              ? ` Approved by ${post.approved_by_name}.`
                              : ""}
                            {Number(post.attempts || 0) > 0
                              ? ` ${post.attempts} publish ${Number(post.attempts) === 1 ? "attempt" : "attempts"}.`
                              : ""}
                          </small>

                          {post.last_error ? (
                            <small className="scheduler-post-error">
                              {post.last_error}
                            </small>
                          ) : null}
                        </div>

                        <div className="scheduler-post-actions">
                          {post.status === "draft" || post.status === "failed" ? (
                            <AppButton
                              variant="success"
                              size="small"
                              loading={busyPostId === post.id}
                              onClick={() => handleApprove(post)}
                            >
                              Approve
                            </AppButton>
                          ) : null}

                          {post.status !== "published" ? (
                            <AppButton
                              variant="ghost"
                              size="small"
                              onClick={() => openEditor(post)}
                            >
                              Edit
                            </AppButton>
                          ) : null}

                          {post.status !== "published" &&
                          post.status !== "cancelled" ? (
                            <AppButton
                              variant="danger"
                              size="small"
                              onClick={() => setPendingCancel(post)}
                            >
                              Cancel
                            </AppButton>
                          ) : null}
                        </div>
                      </li>
                    ))}
                  </ul>
                </section>
              ))
            : null}
        </AppCard>

        {composerOpen ? (
          <AppCard padding="medium" className="scheduler-composer-card">
            <header className="scheduler-composer-head">
              <div>
                <span>{editingPost ? "EDIT POST" : "NEW POST"}</span>
                <h3>
                  {editingPost
                    ? `Post #${editingPost.id}`
                    : "Schedule a publication"}
                </h3>
              </div>

              <button
                type="button"
                className="scheduler-composer-close"
                aria-label="Close composer"
                onClick={closeComposer}
              >
                <CloseOutlined fontSize="small" />
              </button>
            </header>

            <form className="scheduler-form" onSubmit={handleSubmit}>
              <label htmlFor="scheduler-channel">
                <span>Channel</span>

                <select
                  id="scheduler-channel"
                  value={form.channel}
                  // The publishing target is fixed once the post exists: the
                  // server only lets the copy and the timing be edited.
                  disabled={Boolean(editingPost)}
                  onChange={(event) => updateField("channel", event.target.value)}
                >
                  {CHANNELS.map(([value, label]) => (
                    <option value={value} key={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>

              <label htmlFor="scheduler-body">
                <span>Post text</span>

                <textarea
                  id="scheduler-body"
                  rows={7}
                  required
                  maxLength={5000}
                  value={form.body}
                  placeholder="What should this post say?"
                  onChange={(event) => updateField("body", event.target.value)}
                />

                <small>{form.body.length} / 5000 characters</small>
              </label>

              <label htmlFor="scheduler-media">
                <span>Media URL (optional)</span>

                <input
                  id="scheduler-media"
                  type="url"
                  maxLength={1000}
                  value={form.media_url}
                  placeholder="https://..."
                  onChange={(event) =>
                    updateField("media_url", event.target.value)
                  }
                />
              </label>

              <label htmlFor="scheduler-link">
                <span>Link URL (optional)</span>

                <input
                  id="scheduler-link"
                  type="url"
                  maxLength={1000}
                  value={form.link_url}
                  placeholder="https://..."
                  onChange={(event) =>
                    updateField("link_url", event.target.value)
                  }
                />
              </label>

              <label htmlFor="scheduler-when">
                <span>Publish at</span>

                <input
                  id="scheduler-when"
                  type="datetime-local"
                  required
                  value={form.scheduled_for}
                  onChange={(event) =>
                    updateField("scheduled_for", event.target.value)
                  }
                />

                <small>
                  Entered in {timeZone}
                  {form.scheduled_for
                    ? ` · sent to the server as ${zonedInputToUtcIso(form.scheduled_for, timeZone)}`
                    : " · stored in UTC"}
                </small>
              </label>

              {formError ? (
                <div className="scheduler-form-error" role="alert">
                  {formError}
                </div>
              ) : null}

              <footer className="scheduler-form-footer">
                <span className="is-success">{saveStatus}</span>

                <div>
                  <AppButton
                    variant="secondary"
                    disabled={saving}
                    onClick={closeComposer}
                  >
                    Close
                  </AppButton>

                  <AppButton type="submit" variant="primary" loading={saving}>
                    {editingPost ? "Save changes" : "Save as draft"}
                  </AppButton>
                </div>
              </footer>
            </form>
          </AppCard>
        ) : null}
      </div>

      <ConfirmDialog
        open={Boolean(pendingCancel)}
        title="Cancel this post?"
        message={
          pendingCancel
            ? `The post scheduled for ${formatZonedDay(pendingCancel.scheduled_for, timeZone)} at ${formatZonedTime(pendingCancel.scheduled_for, timeZone)} (${timeZone}) will never be published.`
            : ""
        }
        confirmLabel="Cancel the post"
        cancelLabel="Keep it"
        loading={cancelling}
        onConfirm={handleCancel}
        onCancel={() => setPendingCancel(null)}
      />
    </div>
  );
}
