import { useEffect, useState } from "react";
import {
  createKnowledgeEntryRequest,
  deleteKnowledgeEntryRequest,
  listDepartmentsRequest,
  listKnowledgeEntriesRequest,
  updateKnowledgeEntryRequest,
} from "../../api/client";
import { TagPicker, splitTags } from "./TagPicker";

export default function KnowledgePage() {
  const [entries, setEntries] = useState([]);
  const [departments, setDepartments] = useState([]);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [department, setDepartment] = useState("Unassigned");
  const [selectedDepartments, setSelectedDepartments] = useState([]);
  const [selectedChannels, setSelectedChannels] = useState([]);
  const [extraTagsInput, setExtraTagsInput] = useState("");
  const [filterDepartment, setFilterDepartment] = useState("all");

  function load() {
    listKnowledgeEntriesRequest()
      .then((result) => setEntries(result?.entries || []))
      .catch((e) => setError(e.message || "Could not load knowledge base."));
    listDepartmentsRequest()
      .then((result) => setDepartments((result?.departments || []).map((d) => d.name || d)))
      .catch(() => {});
  }

  useEffect(() => { load(); }, []);

  function startEdit(entry) {
    setEditingId(entry.id);
    setTitle(entry.title);
    setContent(entry.content);
    setDepartment(entry.department || "Unassigned");
    const { departmentTags, channelTags, extraTags } = splitTags(entry.tags || [], departments);
    setSelectedDepartments(departmentTags);
    setSelectedChannels(channelTags);
    setExtraTagsInput(extraTags.join(", "));
  }

  function startNew() {
    setEditingId("new");
    setTitle(""); setContent(""); setDepartment("Unassigned");
    setSelectedDepartments([]); setSelectedChannels([]); setExtraTagsInput("");
  }

  async function save(e) {
    e.preventDefault();
    setSaving(true);
    setError("");
    const extraTags = extraTagsInput.split(",").map((t) => t.trim()).filter(Boolean);
    const tags = [...selectedDepartments, ...selectedChannels, ...extraTags];
    try {
      if (editingId === "new") {
        await createKnowledgeEntryRequest(title, content, department, tags);
      } else {
        await updateKnowledgeEntryRequest(editingId, title, content, department, tags);
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
      await deleteKnowledgeEntryRequest(id);
      load();
    } catch (x) {
      setError(x.message);
    }
  }

  const visibleEntries = filterDepartment === "all" ? entries : entries.filter((e) => e.department === filterDepartment);

  return (
    <div className="workflow-settings-card">
      <div className="workflow-setting-row" style={{ borderBottom: "none" }}>
        <div>
          <strong>What your AI knows about your business</strong>
          <br />
          <span style={{ fontWeight: 400, color: "#6b7280" }}>
            Add questions and answers about your pricing, services, policies — anything customers ask.
            The AI uses these (not generic knowledge) when replying. Scope an entry to a department and/or channel, or leave it unscoped to apply everywhere.
          </span>
        </div>
        {editingId ? null : <button type="button" onClick={startNew}>+ New knowledge entry</button>}
      </div>

      {error ? <p style={{ color: "#c0392b" }}>{error}</p> : null}

      {editingId ? (
        <form onSubmit={save} style={{ display: "flex", flexDirection: "column", gap: 10, padding: "12px 0" }}>
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Question or topic (e.g. What internet speed do I need?)" required
            style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #d5dae5" }} />
          <textarea value={content} onChange={(e) => setContent(e.target.value)} placeholder="Answer" required rows={4}
            style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #d5dae5" }} />
          <select value={department} onChange={(e) => setDepartment(e.target.value)}
            style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #d5dae5" }}>
            <option value="Unassigned">Unassigned</option>
            {departments.filter((d) => d !== "Unassigned").map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
          <TagPicker
            departments={departments}
            selectedDepartments={selectedDepartments} setSelectedDepartments={setSelectedDepartments}
            selectedChannels={selectedChannels} setSelectedChannels={setSelectedChannels}
            extraTagsInput={extraTagsInput} setExtraTagsInput={setExtraTagsInput}
          />
          <div style={{ display: "flex", gap: 10 }}>
            <button type="submit" disabled={saving}>{saving ? "Saving..." : "Save"}</button>
            <button type="button" onClick={() => setEditingId(null)}>Cancel</button>
          </div>
        </form>
      ) : null}

      {!editingId ? (
        <div style={{ padding: "10px 0" }}>
          <select value={filterDepartment} onChange={(e) => setFilterDepartment(e.target.value)}
            style={{ padding: "6px 10px", borderRadius: 8, border: "1px solid #d5dae5", fontSize: 13 }}>
            <option value="all">All departments</option>
            {departments.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </div>
      ) : null}

      {visibleEntries.map((entry) => (
        <div className="workflow-setting-row" key={entry.id}>
          <div>
            <span style={{ fontSize: 11, fontWeight: 700, color: "#4F63F0", textTransform: "uppercase" }}>{entry.department}</span>
            {(entry.tags || []).map((tag) => (
              <span key={tag} style={{ fontSize: 10, fontWeight: 700, color: "#17A369", background: "#E7FAF1", borderRadius: 999, padding: "2px 8px", marginLeft: 6 }}>{tag}</span>
            ))}
            <br />
            <strong>{entry.title}</strong>
            <br />
            <span style={{ fontWeight: 400, color: "#6b7280" }}>{entry.content}</span>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" onClick={() => startEdit(entry)}>Edit</button>
            <button type="button" onClick={() => remove(entry.id)}>Delete</button>
          </div>
        </div>
      ))}
      {!visibleEntries.length && !editingId ? (
        <p style={{ padding: "12px 0", color: "#6b7280" }}>
          {entries.length ? "No entries in this department." : "No knowledge added yet — your AI is replying generically until you add some."}
        </p>
      ) : null}
    </div>
  );
}
