import { useEffect, useMemo, useState } from "react";
import {
  listSavedRepliesRequest,
  createSavedReplyRequest,
  updateSavedReplyRequest,
  deleteSavedReplyRequest,
  listDepartmentsRequest,
} from "../../api/client";
import "./SavedRepliesPage.css";

export default function SavedRepliesPage() {
  const [replies, setReplies] = useState([]);
  const [departments, setDepartments] = useState([]);
  const [canManage, setCanManage] = useState(false);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const [editingId, setEditingId] = useState(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [department, setDepartment] = useState("");
  const [saving, setSaving] = useState(false);
  const [copiedId, setCopiedId] = useState(null);

  function load() {
    setLoading(true);
    listSavedRepliesRequest()
      .then((result) => {
        setReplies(result?.replies || []);
        setCanManage(Boolean(result?.can_manage));
      })
      .catch((e) => setError(e.message || "Could not load saved replies."))
      .finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, []);

  useEffect(() => {
    listDepartmentsRequest()
      .then((result) => setDepartments(result?.departments || []))
      .catch(() => { /* departments are optional for filtering */ });
  }, []);

  const visibleReplies = useMemo(() => {
    if (!filter) return replies;
    return replies.filter((r) => (r.department || "") === filter);
  }, [replies, filter]);

  function startEdit(reply) {
    setEditingId(reply.id);
    setTitle(reply.title);
    setBody(reply.body);
    setDepartment(reply.department || "");
    setError("");
  }

  function startNew() {
    setEditingId("new");
    setTitle("");
    setBody("");
    setDepartment(filter || "");
    setError("");
  }

  async function save(e) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      if (editingId === "new") {
        await createSavedReplyRequest(title, body, department);
      } else {
        await updateSavedReplyRequest(editingId, title, body, department);
      }
      setEditingId(null);
      load();
    } catch (x) {
      setError(x.message);
    } finally {
      setSaving(false);
    }
  }

  async function remove(id) {
    setError("");
    try {
      await deleteSavedReplyRequest(id);
      load();
    } catch (x) {
      setError(x.message);
    }
  }

  async function copy(reply) {
    try {
      await navigator.clipboard.writeText(reply.body);
      setCopiedId(reply.id);
      window.setTimeout(() => setCopiedId((c) => (c === reply.id ? null : c)), 1500);
    } catch {
      /* clipboard may be unavailable — silently ignore */
    }
  }

  return (
    <section className="saved-replies-page">
      <header className="saved-replies-header">
        <div>
          <h1>Saved Replies</h1>
          <p>
            {canManage
              ? "Create and manage reusable reply snippets for your team. Scope each one to a department, or leave it general."
              : "Browse your team's reusable reply snippets and copy them into a conversation. Only admins can edit these."}
          </p>
        </div>
        {canManage && !editingId ? (
          <button type="button" className="saved-replies-primary" onClick={startNew}>+ New saved reply</button>
        ) : null}
      </header>

      <div className="saved-replies-toolbar">
        <label>
          <span>Department</span>
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">All departments</option>
            {departments.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
        </label>
      </div>

      {error ? <p className="saved-replies-error">{error}</p> : null}

      {canManage && editingId ? (
        <form className="saved-replies-form" onSubmit={save}>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Title (e.g. Greeting)"
            required
          />
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Message text"
            required
            rows={3}
          />
          <label className="saved-replies-form-dept">
            <span>Department</span>
            <select value={department} onChange={(e) => setDepartment(e.target.value)}>
              <option value="">General (all departments)</option>
              {departments.map((name) => (
                <option key={name} value={name}>{name}</option>
              ))}
            </select>
          </label>
          <div className="saved-replies-form-actions">
            <button type="submit" disabled={saving}>{saving ? "Saving..." : "Save"}</button>
            <button type="button" onClick={() => setEditingId(null)}>Cancel</button>
          </div>
        </form>
      ) : null}

      {loading ? (
        <p className="saved-replies-empty">Loading saved replies...</p>
      ) : !visibleReplies.length ? (
        <p className="saved-replies-empty">No saved replies{filter ? ` for ${filter}` : ""} yet.</p>
      ) : (
        <div className="saved-replies-list">
          {visibleReplies.map((reply) => (
            <article className="saved-reply-card" key={reply.id}>
              <div className="saved-reply-main">
                <div className="saved-reply-titlerow">
                  <strong>{reply.title}</strong>
                  <span className="saved-reply-dept">{reply.department || "General"}</span>
                </div>
                <p>{reply.body}</p>
              </div>
              <div className="saved-reply-actions">
                <button type="button" onClick={() => copy(reply)}>
                  {copiedId === reply.id ? "Copied!" : "Copy"}
                </button>
                {canManage ? (
                  <>
                    <button type="button" onClick={() => startEdit(reply)}>Edit</button>
                    <button type="button" className="saved-reply-delete" onClick={() => remove(reply.id)}>Delete</button>
                  </>
                ) : null}
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
