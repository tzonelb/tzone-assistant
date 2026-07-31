import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ChatBubbleOutlined, SendOutlined, GridViewOutlined } from "@mui/icons-material";
import { listCommentPostsRequest, listPostCommentsRequest, replyToCommentRequest } from "../../api/client";
import { channelIcon } from "./channelIcon";
import "./InboxPage.css";

function timeAgo(value) {
  if (!value) return "";
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const then = new Date(normalized).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

export default function InboxPage() {
  const [searchParams] = useSearchParams();
  const channelFilter = searchParams.get("channel");

  const [posts, setPosts] = useState([]);
  const [unansweredTotal, setUnansweredTotal] = useState(0);
  const [selectedPost, setSelectedPost] = useState(null);
  const [comments, setComments] = useState([]);
  const [loadingPosts, setLoadingPosts] = useState(true);
  const [loadingComments, setLoadingComments] = useState(false);
  const [draft, setDraft] = useState("");
  const [replyingTo, setReplyingTo] = useState(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");

  async function loadPosts() {
    setLoadingPosts(true);
    try {
      const result = await listCommentPostsRequest({ channelAccountId: channelFilter || undefined });
      setPosts(Array.isArray(result?.posts) ? result.posts : []);
      setUnansweredTotal(result?.unanswered_total || 0);
    } catch {
      setPosts([]);
    } finally {
      setLoadingPosts(false);
    }
  }

  useEffect(() => { loadPosts(); /* eslint-disable-next-line */ }, [channelFilter]);

  async function openPost(post) {
    setSelectedPost(post);
    setComments([]);
    setReplyingTo(null);
    setDraft("");
    setLoadingComments(true);
    try {
      const result = await listPostCommentsRequest(post.post_external_id);
      setComments(Array.isArray(result?.comments) ? result.comments : []);
    } catch {
      setComments([]);
    } finally {
      setLoadingComments(false);
    }
  }

  async function sendReply(comment) {
    const text = draft.trim();
    if (!text) return;
    setSending(true);
    setError("");
    try {
      await replyToCommentRequest(comment.id, text);
      setDraft("");
      setReplyingTo(null);
      await Promise.all([openPost(selectedPost), loadPosts()]);
    } catch (requestError) {
      setError(requestError.message || "Could not send this reply.");
    } finally {
      setSending(false);
    }
  }

  const topLevel = useMemo(
    () => comments.filter((c) => !c.parent_comment_external_id),
    [comments],
  );
  const repliesByParent = useMemo(() => {
    const map = {};
    comments.forEach((c) => {
      if (c.parent_comment_external_id) {
        (map[c.parent_comment_external_id] ||= []).push(c);
      }
    });
    return map;
  }, [comments]);

  return (
    <section className="community-inbox">
      <header className="community-inbox-head">
        <div className="community-inbox-title">
          <GridViewOutlined fontSize="small" />
          <h2>All Channels</h2>
          {unansweredTotal ? <span className="community-inbox-badge">{unansweredTotal}</span> : null}
        </div>
      </header>

      <div className="community-inbox-body">
        <aside className="community-inbox-posts">
          <div className="community-inbox-posts-head">Posts</div>
          {loadingPosts ? (
            <p className="community-inbox-hint">Loading…</p>
          ) : posts.length === 0 ? (
            <div className="community-inbox-empty">
              <ChatBubbleOutlined fontSize="large" />
              <p>No comments yet</p>
              <span>
                Comments on your Facebook and Instagram posts appear here. This activates once the
                platform is deployed on your domain and the Meta comment webhook is connected — a
                one-time setup, then comments flow in automatically.
              </span>
            </div>
          ) : (
            posts.map((post) => {
              const Icon = channelIcon(post.channel);
              const active = selectedPost?.post_external_id === post.post_external_id;
              return (
                <button
                  key={post.post_external_id}
                  type="button"
                  className={`community-inbox-post ${active ? "is-active" : ""}`}
                  onClick={() => openPost(post)}
                >
                  {post.media_url ? (
                    <img src={post.media_url} alt="" className="community-inbox-post-thumb" />
                  ) : (
                    <span className="community-inbox-post-thumb community-inbox-post-thumb-empty">
                      <Icon fontSize="small" />
                    </span>
                  )}
                  <span className="community-inbox-post-main">
                    <span className="community-inbox-post-name">
                      <Icon fontSize="inherit" /> {post.channel_account_name || "Channel"}
                    </span>
                    <span className="community-inbox-post-caption">{post.caption || "(no caption)"}</span>
                  </span>
                  {post.comment_count ? (
                    <span className={`community-inbox-count ${post.unanswered_count ? "is-unanswered" : ""}`}>
                      {post.comment_count}
                    </span>
                  ) : null}
                </button>
              );
            })
          )}
        </aside>

        <main className="community-inbox-thread">
          {!selectedPost ? (
            <div className="community-inbox-empty community-inbox-empty-center">
              <ChatBubbleOutlined fontSize="large" />
              <p>Select a post to view its comments</p>
            </div>
          ) : (
            <>
              <div className="community-inbox-post-preview">
                {selectedPost.media_url ? (
                  <img src={selectedPost.media_url} alt="" />
                ) : null}
                <div>
                  <strong>{selectedPost.channel_account_name}</strong>
                  <p>{selectedPost.caption || "(no caption)"}</p>
                  <span>{selectedPost.comment_count || 0} comments · {selectedPost.unanswered_count || 0} unanswered</span>
                </div>
              </div>

              {error ? <p className="community-inbox-error">{error}</p> : null}

              {loadingComments ? (
                <p className="community-inbox-hint">Loading comments…</p>
              ) : (
                <div className="community-inbox-comments">
                  {topLevel.map((comment) => (
                    <div key={comment.id} className="community-inbox-comment">
                      <div className="community-inbox-comment-avatar">
                        {(comment.author_name || "?").charAt(0).toUpperCase()}
                      </div>
                      <div className="community-inbox-comment-body">
                        <div className="community-inbox-comment-meta">
                          <strong>{comment.author_name || "Unknown"}</strong>
                          <time>{timeAgo(comment.platform_created_at || comment.received_at)}</time>
                          {comment.status === "answered" ? <em className="is-answered">Answered</em> : null}
                        </div>
                        <p>{comment.text}</p>

                        {(repliesByParent[comment.comment_external_id] || []).map((reply) => (
                          <div key={reply.id} className={`community-inbox-reply ${reply.is_from_business ? "is-business" : ""}`}>
                            <strong>{reply.is_from_business ? "You" : reply.author_name}</strong>
                            <span>{reply.text}</span>
                          </div>
                        ))}

                        {replyingTo === comment.id ? (
                          <div className="community-inbox-reply-box">
                            <textarea
                              rows={2}
                              value={draft}
                              autoFocus
                              placeholder="Write a reply…"
                              disabled={sending}
                              onChange={(event) => setDraft(event.target.value)}
                            />
                            <div className="community-inbox-reply-actions">
                              <button type="button" onClick={() => { setReplyingTo(null); setDraft(""); }} disabled={sending}>Cancel</button>
                              <button type="button" className="is-primary" onClick={() => sendReply(comment)} disabled={sending || !draft.trim()}>
                                <SendOutlined fontSize="inherit" /> {sending ? "Sending…" : "Reply"}
                              </button>
                            </div>
                          </div>
                        ) : (
                          <button type="button" className="community-inbox-reply-trigger" onClick={() => { setReplyingTo(comment.id); setDraft(""); }}>
                            Reply
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                  {topLevel.length === 0 ? <p className="community-inbox-hint">No comments on this post yet.</p> : null}
                </div>
              )}
            </>
          )}
        </main>
      </div>
    </section>
  );
}
