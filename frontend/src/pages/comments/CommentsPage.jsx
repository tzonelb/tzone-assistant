import { useCallback, useEffect, useState } from "react";
import {
  ChevronLeftOutlined,
  ChevronRightOutlined,
  ForumOutlined,
  OpenInNewOutlined,
  RefreshOutlined,
} from "@mui/icons-material";

import {
  getCommentRequest,
  getCommentsRequest,
  replyToCommentRequest,
  updateCommentStatusRequest,
} from "../../api/comments";
import {
  AppButton,
  AppCard,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  SearchBar,
  StatusBadge,
} from "../../components/common";
import { formatPlatformDateTime } from "../../utils/dateTime";
import "./CommentsPage.css";

const PAGE_SIZE = 25;

const QUEUES = [
  ["open", "Open"],
  ["answered", "Answered"],
  ["ignored", "Ignored"],
  ["", "All"],
];

const CHANNELS = [
  ["all", "All channels"],
  ["messenger", "Facebook"],
  ["instagram", "Instagram"],
];

const STATUS_TONES = {
  open: "warning",
  answered: "success",
  ignored: "neutral",
};

function humanize(value) {
  return String(value ?? "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function truncate(value, maximum = 90) {
  const text = String(value || "").trim();
  return text.length <= maximum ? text : `${text.slice(0, maximum)}…`;
}

export default function CommentsPage() {
  const [status, setStatus] = useState("open");
  const [channel, setChannel] = useState("all");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [statusCounts, setStatusCounts] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [selectedId, setSelectedId] = useState(null);
  const [comment, setComment] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");

  const [replyBody, setReplyBody] = useState("");
  const [replying, setReplying] = useState(false);
  const [replyError, setReplyError] = useState("");
  const [replyStatus, setReplyStatus] = useState("");
  const [statusSaving, setStatusSaving] = useState(false);

  const loadComments = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const result = await getCommentsRequest({
        status,
        channel,
        search,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      });

      setItems(Array.isArray(result?.items) ? result.items : []);
      setTotal(Number(result?.total || 0));
      setStatusCounts(result?.status_counts || {});
    } catch (requestError) {
      setError(requestError.message || "Comments could not be loaded.");
      setItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [status, channel, search, page]);

  useEffect(() => {
    loadComments();
  }, [loadComments]);

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(1);
    }, 300);

    return () => window.clearTimeout(timeout);
  }, [searchInput]);

  const loadComment = useCallback(async (commentId) => {
    setDetailLoading(true);
    setDetailError("");

    try {
      setComment(await getCommentRequest(commentId));
    } catch (requestError) {
      setComment(null);
      setDetailError(
        requestError.message || "This comment could not be loaded.",
      );
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selectedId === null) {
      setComment(null);
      setDetailError("");
      return;
    }

    setReplyBody("");
    setReplyError("");
    setReplyStatus("");
    loadComment(selectedId);
  }, [selectedId, loadComment]);

  async function handleReply(event) {
    event.preventDefault();

    const message = replyBody.trim();

    if (!message || !comment?.id) {
      return;
    }

    setReplying(true);
    setReplyError("");
    setReplyStatus("");

    try {
      const result = await replyToCommentRequest(comment.id, message);

      setComment(result?.comment || comment);
      setReplyBody("");
      setReplyStatus("Reply published.");
      await loadComments();
    } catch (requestError) {
      /*
       * A 502 is not a lost reply. The server stored the text and left the
       * comment open because it is still public and still unanswered — so the
       * composer is cleared, the thread is reloaded to show the failed attempt,
       * and the message says exactly that.
       */
      setReplyError(
        requestError.message || "The reply could not be published.",
      );

      if (requestError.status === 502) {
        setReplyBody("");
        await loadComment(comment.id);
        await loadComments();
      }
    } finally {
      setReplying(false);
    }
  }

  async function changeStatus(nextStatus) {
    if (!comment?.id) return;

    setStatusSaving(true);
    setDetailError("");

    try {
      await updateCommentStatusRequest(comment.id, nextStatus);
      await loadComment(comment.id);
      await loadComments();
    } catch (requestError) {
      setDetailError(
        requestError.message || "The comment status could not be changed.",
      );
    } finally {
      setStatusSaving(false);
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const rangeStart = total ? (page - 1) * PAGE_SIZE + 1 : 0;
  const rangeEnd = Math.min(page * PAGE_SIZE, total);

  return (
    <div className="comments-page">
      <PageHeader
        eyebrow="PUBLIC COMMENTS"
        title="Comments"
        description="Comments left on this company's Facebook and Instagram posts. Replying here publishes the answer under the original post."
        actions={
          <AppButton
            variant="secondary"
            icon={<RefreshOutlined fontSize="small" />}
            onClick={loadComments}
          >
            Refresh
          </AppButton>
        }
      />

      <div className="comments-layout">
        <AppCard padding="none" className="comments-queue-card">
          <nav className="comments-queue-tabs" aria-label="Comment queues">
            {QUEUES.map(([value, label]) => (
              <button
                type="button"
                key={value || "all"}
                className={`comments-queue-tab ${status === value ? "is-active" : ""}`}
                aria-pressed={status === value}
                onClick={() => {
                  setStatus(value);
                  setPage(1);
                }}
              >
                <span>{label}</span>

                {value ? (
                  <strong>{Number(statusCounts[value] || 0)}</strong>
                ) : null}
              </button>
            ))}
          </nav>

          <div className="comments-queue-filters">
            <SearchBar
              value={searchInput}
              placeholder="Search comment text or author..."
              ariaLabel="Search comments"
              onChange={setSearchInput}
            />

            <select
              aria-label="Filter by channel"
              value={channel}
              onChange={(event) => {
                setChannel(event.target.value);
                setPage(1);
              }}
            >
              {CHANNELS.map(([value, label]) => (
                <option value={value} key={value}>
                  {label}
                </option>
              ))}
            </select>
          </div>

          <div className="comments-queue-list">
            {loading ? <LoadingState title="Loading comments..." /> : null}

            {!loading && error ? (
              <ErrorState
                title="Comments could not load"
                description={error}
                action={
                  <AppButton variant="primary" onClick={loadComments}>
                    Try again
                  </AppButton>
                }
              />
            ) : null}

            {!loading && !error && items.length === 0 ? (
              <EmptyState
                icon={<ForumOutlined />}
                title="Nothing in this queue"
                description={
                  search
                    ? "No comment matches this search."
                    : "Comments appear here as soon as somebody writes under one of this company's posts."
                }
              />
            ) : null}

            {!loading && !error
              ? items.map((item) => (
                  <button
                    type="button"
                    key={item.id}
                    className={`comments-queue-item ${selectedId === item.id ? "is-selected" : ""}`}
                    onClick={() => setSelectedId(item.id)}
                  >
                    <div className="comments-queue-item-top">
                      <strong>{item.author_name || "Unknown author"}</strong>
                      <time>{formatPlatformDateTime(item.created_at)}</time>
                    </div>

                    <p>{truncate(item.message) || "No comment text"}</p>

                    <div className="comments-queue-item-meta">
                      <span>{humanize(item.channel)}</span>

                      <StatusBadge
                        status={item.status}
                        tone={STATUS_TONES[item.status]}
                        label={humanize(item.status)}
                      />

                      {Number(item.reply_count || 0) > 0 ? (
                        <span>
                          {item.reply_count}{" "}
                          {Number(item.reply_count) === 1 ? "reply" : "replies"}
                        </span>
                      ) : null}
                    </div>
                  </button>
                ))
              : null}
          </div>

          <footer className="comments-queue-footer">
            <span>
              {rangeStart}–{rangeEnd} of {total}
            </span>

            <div>
              <button
                type="button"
                aria-label="Previous page"
                disabled={page <= 1}
                onClick={() => setPage((current) => Math.max(1, current - 1))}
              >
                <ChevronLeftOutlined fontSize="small" />
              </button>

              <span>
                Page {page} of {totalPages}
              </span>

              <button
                type="button"
                aria-label="Next page"
                disabled={page >= totalPages}
                onClick={() =>
                  setPage((current) => Math.min(totalPages, current + 1))
                }
              >
                <ChevronRightOutlined fontSize="small" />
              </button>
            </div>
          </footer>
        </AppCard>

        <AppCard padding="medium" className="comments-detail-card">
          {selectedId === null ? (
            <EmptyState
              icon={<ForumOutlined />}
              title="Select a comment"
              description="Pick a comment from the queue to read the thread and reply to it."
            />
          ) : null}

          {selectedId !== null && detailLoading ? (
            <LoadingState title="Loading the comment thread..." />
          ) : null}

          {selectedId !== null && !detailLoading && detailError && !comment ? (
            <ErrorState
              title="The comment could not load"
              description={detailError}
              action={
                <AppButton
                  variant="primary"
                  onClick={() => loadComment(selectedId)}
                >
                  Try again
                </AppButton>
              }
            />
          ) : null}

          {selectedId !== null && !detailLoading && comment ? (
            <>
              <header className="comments-detail-head">
                <div>
                  <span>{humanize(comment.channel)} COMMENT</span>
                  <h3>{comment.author_name || "Unknown author"}</h3>
                  <small>{formatPlatformDateTime(comment.created_at)}</small>
                </div>

                <div className="comments-detail-head-actions">
                  <StatusBadge
                    status={comment.status}
                    tone={STATUS_TONES[comment.status]}
                    label={humanize(comment.status)}
                  />

                  {comment.permalink ? (
                    <a
                      className="comments-permalink"
                      href={comment.permalink}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <OpenInNewOutlined fontSize="small" />
                      <span>Open the post</span>
                    </a>
                  ) : null}
                </div>
              </header>

              {comment.post_caption ? (
                <p className="comments-post-caption">
                  <span>On the post</span>
                  {comment.post_caption}
                </p>
              ) : null}

              <blockquote className="comments-message">
                {comment.message || "This comment has no text."}
              </blockquote>

              <section className="comments-thread">
                <h4>
                  Replies ({(comment.replies || []).length})
                </h4>

                {(comment.replies || []).length === 0 ? (
                  <p className="comments-thread-empty">
                    Nobody has answered this comment yet.
                  </p>
                ) : (
                  <ul>
                    {comment.replies.map((reply) => (
                      <li
                        key={reply.id}
                        className={
                          reply.send_status === "sent" ? "" : "is-failed"
                        }
                      >
                        <div className="comments-reply-head">
                          <strong>{reply.author_name || "Assistant"}</strong>

                          <StatusBadge
                            status={
                              reply.send_status === "sent" ? "success" : "failed"
                            }
                            label={
                              reply.send_status === "sent"
                                ? "Published"
                                : "Saved, not published"
                            }
                          />

                          <time>{formatPlatformDateTime(reply.created_at)}</time>
                        </div>

                        <p>{reply.body}</p>

                        {/*
                          The error is shown rather than swallowed: the text
                          exists on this server but never reached the public
                          post, and only the team can decide what to do.
                        */}
                        {reply.send_status !== "sent" && reply.error ? (
                          <small>{reply.error}</small>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <form className="comments-composer" onSubmit={handleReply}>
                <label htmlFor="comment-reply">
                  <span>Reply publicly</span>

                  <textarea
                    id="comment-reply"
                    rows={4}
                    maxLength={8000}
                    value={replyBody}
                    placeholder="Write the answer that will be published under this comment..."
                    onChange={(event) => {
                      setReplyStatus("");
                      setReplyError("");
                      setReplyBody(event.target.value);
                    }}
                  />
                </label>

                {replyError ? (
                  <div className="comments-reply-error" role="alert">
                    <strong>The reply was not published</strong>
                    <p>{replyError}</p>
                  </div>
                ) : null}

                {detailError ? (
                  <div className="comments-reply-error" role="alert">
                    <strong>Action failed</strong>
                    <p>{detailError}</p>
                  </div>
                ) : null}

                <footer>
                  <span className="is-success">{replyStatus}</span>

                  <div>
                    {comment.status === "ignored" ? (
                      <AppButton
                        variant="secondary"
                        disabled={statusSaving}
                        onClick={() => changeStatus("open")}
                      >
                        Reopen
                      </AppButton>
                    ) : (
                      <AppButton
                        variant="secondary"
                        disabled={statusSaving}
                        onClick={() => changeStatus("ignored")}
                      >
                        Ignore
                      </AppButton>
                    )}

                    <AppButton
                      type="submit"
                      variant="primary"
                      loading={replying}
                      disabled={!replyBody.trim()}
                    >
                      Publish reply
                    </AppButton>
                  </div>
                </footer>
              </form>
            </>
          ) : null}
        </AppCard>
      </div>
    </div>
  );
}
