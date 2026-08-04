import {
  AttachFileOutlined,
  FacebookOutlined,
  ImageOutlined,
  Instagram,
  LaunchOutlined,
  SearchOutlined,
  SendOutlined,
} from "@mui/icons-material";
import { useMemo, useState } from "react";

const CHANNELS = [
  { id: "all", label: "All comments", count: 2 },
  { id: "facebook", label: "Facebook", count: 1, icon: FacebookOutlined },
  { id: "instagram", label: "Instagram", count: 1, icon: Instagram },
];

const DEMO = [
  {
    id: 1,
    platform: "facebook",
    author: "Customer comment",
    post: "Samsung A55 offer",
    text: "Is this product still available?",
    replies: [
      { id: 11, author: "Another customer", text: "I also want the price." },
    ],
    time: "Now",
  },
  {
    id: 2,
    platform: "instagram",
    author: "Instagram customer",
    post: "New arrivals",
    text: "Please send me the price.",
    replies: [],
    time: "5 min",
  },
];

export default function CommentsPage() {
  const [channel, setChannel] = useState("all");
  const [selectedId, setSelectedId] = useState(1);
  const [query, setQuery] = useState("");
  const [reply, setReply] = useState("");

  const filtered = useMemo(
    () => DEMO.filter((item) => (
      (channel === "all" || item.platform === channel)
      && `${item.author} ${item.text} ${item.post}`
        .toLowerCase()
        .includes(query.toLowerCase())
    )),
    [channel, query],
  );

  const selected = DEMO.find((item) => item.id === selectedId) || filtered[0];

  function openInNewTab() {
    window.open("/comments", "_blank", "noopener,noreferrer");
  }

  return (
    <section className="comments-workspace-page">
      <nav className="comments-channel-tabs" aria-label="Comment channels">
        {CHANNELS.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              type="button"
              className={`comments-channel-tab channel-${item.id} ${channel === item.id ? "is-active" : ""}`}
              onClick={() => setChannel(item.id)}
            >
              {Icon ? <Icon /> : null}
              <span>{item.label}</span>
              <strong>{item.count}</strong>
            </button>
          );
        })}
      </nav>

      <div className="comments-workspace-grid comments-two-column">
        <aside className="comments-list-panel">
          <label className="comments-search">
            <SearchOutlined />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search comments..."
            />
          </label>

          <div className="comments-list-scroll">
            {filtered.map((comment) => (
              <button
                key={comment.id}
                type="button"
                className={`comment-list-item ${selectedId === comment.id ? "is-selected" : ""}`}
                onClick={() => setSelectedId(comment.id)}
              >
                <div>
                  <strong>{comment.author}</strong>
                  <span className={`comment-platform-badge ${comment.platform}`}>
                    {comment.platform}
                  </span>
                </div>
                <p>{comment.text}</p>
                <small>{comment.post} · {comment.time}</small>
              </button>
            ))}
          </div>
        </aside>

        <main className="comment-detail-panel">
          {selected ? (
            <>
              <div className="comment-thread-head">
                <span className={`comment-channel-badge ${selected.platform}`}>
                  {selected.platform}
                </span>
                <div>
                  <strong>{selected.post}</strong>
                  <small>Public comment thread</small>
                </div>
                <button
                  type="button"
                  className="comment-open-full-button"
                  title="Open comments in a separate tab"
                  onClick={openInNewTab}
                >
                  <LaunchOutlined />
                </button>
              </div>

              <div className="comment-thread-scroll">
                <article className="public-comment root-comment">
                  <strong>{selected.author}</strong>
                  <p>{selected.text}</p>
                  <time>{selected.time}</time>
                </article>

                {selected.replies.map((threadReply) => (
                  <article className="public-comment reply-comment" key={threadReply.id}>
                    <strong>{threadReply.author}</strong>
                    <p>{threadReply.text}</p>
                  </article>
                ))}
              </div>

              <form
                className="comment-reply-bar comment-reply-bar-approved"
                onSubmit={(event) => {
                  event.preventDefault();
                }}
              >
                <label className="composer-tool-button" title="Attach file">
                  <AttachFileOutlined />
                  <input type="file" hidden disabled />
                </label>
                <label className="composer-tool-button" title="Attach image">
                  <ImageOutlined />
                  <input type="file" accept="image/*" hidden disabled />
                </label>
                <textarea
                  value={reply}
                  onChange={(event) => setReply(event.target.value)}
                  placeholder="Public reply sending isn't connected yet..."
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                    }
                  }}
                />
                <button
                  type="submit"
                  className="composer-send-circle"
                  aria-label="Send reply (not yet connected)"
                  title="Public reply sending isn't connected to a channel yet"
                  disabled
                >
                  <SendOutlined />
                </button>
              </form>

              <p className="comment-composer-note">
                This is a UI preview only — public comment replies are not
                yet wired to a live channel, so sending is disabled.
              </p>
            </>
          ) : (
            <div className="comments-empty">Select a comment</div>
          )}
        </main>
      </div>
    </section>
  );
}
