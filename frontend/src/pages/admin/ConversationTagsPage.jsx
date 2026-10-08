import { useEffect, useState } from "react";
import {
  createConversationTagRequest,
  deleteConversationTagRequest,
  listConversationTagsRequest,
  updateConversationTagRequest,
} from "../../api/client";
import "./ConversationTagsPage.css";

const DEFAULT_COLOR = "#6b7280";

// The company's own named, coloured vocabulary for sorting conversations --
// distinct from the free-form per-conversation labels the inbox keeps in a
// conversation's own `tags_json` (visible in the conversation detail
// screen). This is the taxonomy itself: what the company's own tags are
// called and what colour each one shows as, not where a specific
// conversation is tagged with one.
export default function ConversationTagsPage() {
  const [tags, setTags] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [editingId, setEditingId] = useState(null);
  const [name, setName] = useState("");
  const [color, setColor] = useState(DEFAULT_COLOR);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  function load() {
    setLoading(true);
    setError("");
    listConversationTagsRequest()
      .then((result) => setTags(Array.isArray(result?.items) ? result.items : []))
      .catch((requestError) => setError(requestError.message || "Could not load tags."))
      .finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, []);

  function startNew() {
    setEditingId("new");
    setName("");
    setColor(DEFAULT_COLOR);
    setFormError("");
  }

  function startEdit(tag) {
    setEditingId(tag.id);
    setName(tag.name);
    setColor(tag.color || DEFAULT_COLOR);
    setFormError("");
  }

  async function save(event) {
    event.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    setFormError("");
    try {
      if (editingId === "new") {
        await createConversationTagRequest(name.trim(), color);
      } else {
        await updateConversationTagRequest(editingId, name.trim(), color);
      }
      setEditingId(null);
      load();
    } catch (requestError) {
      setFormError(requestError.message || "The tag could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  async function remove(tagId) {
    setError("");
    try {
      await deleteConversationTagRequest(tagId);
      setTags((current) => current.filter((tag) => tag.id !== tagId));
    } catch (requestError) {
      setError(requestError.message || "The tag could not be deleted.");
    }
  }

  return (
    <section className="tz-conv-tags-page">
      <p className="tz-conv-tags-hint">
        Named, coloured tags your team can use to sort conversations in the inbox.
      </p>

      {!editingId ? (
        <div className="tz-conv-tags-actions">
          <button type="button" className="btn btn-primary" onClick={startNew}>+ New tag</button>
        </div>
      ) : null}

      {error ? <p className="tz-conv-tags-error">{error}</p> : null}

      {editingId ? (
        <form className="tz-conv-tags-form" onSubmit={save}>
          <label>
            <span>Name</span>
            <input
              className="input"
              value={name}
              maxLength={50}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. VIP"
              required
            />
          </label>
          <label>
            <span>Colour</span>
            <input
              type="color"
              value={color}
              onChange={(event) => setColor(event.target.value)}
            />
          </label>
          <div className="tz-conv-tags-form-actions">
            <button type="submit" className="btn btn-primary" disabled={saving || !name.trim()}>
              {saving ? "Saving…" : "Save"}
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => setEditingId(null)}>Cancel</button>
          </div>
          {formError ? <p className="tz-conv-tags-error">{formError}</p> : null}
        </form>
      ) : null}

      {loading ? (
        <p className="tz-conv-tags-empty">Loading tags…</p>
      ) : tags.length === 0 ? (
        <p className="tz-conv-tags-empty">No tags yet.</p>
      ) : (
        <ul className="tz-conv-tags-list">
          {tags.map((tag) => (
            <li className="tz-conv-tags-row" key={tag.id}>
              <span className="tz-conv-tags-swatch" style={{ background: tag.color || DEFAULT_COLOR }} />
              <strong>{tag.name}</strong>
              <div className="tz-conv-tags-row-actions">
                <button type="button" className="btn btn-ghost" onClick={() => startEdit(tag)}>Edit</button>
                <button type="button" className="btn btn-ghost" onClick={() => remove(tag.id)}>Delete</button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
