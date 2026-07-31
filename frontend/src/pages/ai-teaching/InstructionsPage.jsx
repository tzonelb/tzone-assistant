import { useEffect, useState } from "react";
import {
  createInstructionRequest,
  deleteInstructionRequest,
  listDepartmentsRequest,
  listInstructionsRequest,
  reorderInstructionsRequest,
  updateInstructionRequest,
} from "../../api/client";
import { TagPicker, splitTags } from "./TagPicker";

export default function InstructionsPage() {
  const [instructions, setInstructions] = useState([]);
  const [departments, setDepartments] = useState([]);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [newText, setNewText] = useState("");
  const [newDepartments, setNewDepartments] = useState([]);
  const [newChannels, setNewChannels] = useState([]);
  const [newExtraTags, setNewExtraTags] = useState("");
  const [editingId, setEditingId] = useState(null);
  const [editingText, setEditingText] = useState("");
  const [editingDepartments, setEditingDepartments] = useState([]);
  const [editingChannels, setEditingChannels] = useState([]);
  const [editingExtraTags, setEditingExtraTags] = useState("");

  function load() {
    listInstructionsRequest()
      .then((result) => setInstructions(result?.instructions || []))
      .catch((e) => setError(e.message || "Could not load instructions."));
    listDepartmentsRequest()
      .then((result) => setDepartments((result?.departments || []).map((d) => d.name || d)))
      .catch(() => {});
  }

  useEffect(() => { load(); }, []);

  async function addInstruction(e) {
    e.preventDefault();
    const value = newText.trim();
    if (!value) return;
    setSaving(true);
    setError("");
    const extraTags = newExtraTags.split(",").map((t) => t.trim()).filter(Boolean);
    const tags = [...newDepartments, ...newChannels, ...extraTags];
    try {
      await createInstructionRequest(value, tags);
      setNewText(""); setNewDepartments([]); setNewChannels([]); setNewExtraTags("");
      load();
    } catch (x) {
      setError(x.message);
    } finally {
      setSaving(false);
    }
  }

  function startEditing(instruction) {
    setEditingId(instruction.id);
    setEditingText(instruction.text);
    const { departmentTags, channelTags, extraTags } = splitTags(instruction.tags || [], departments);
    setEditingDepartments(departmentTags);
    setEditingChannels(channelTags);
    setEditingExtraTags(extraTags.join(", "));
  }

  async function saveEdit(id) {
    setSaving(true);
    setError("");
    const extraTags = editingExtraTags.split(",").map((t) => t.trim()).filter(Boolean);
    const tags = [...editingDepartments, ...editingChannels, ...extraTags];
    try {
      await updateInstructionRequest(id, editingText, tags);
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
      await deleteInstructionRequest(id);
      load();
    } catch (x) {
      setError(x.message);
    }
  }

  async function move(index, direction) {
    const nextIndex = index + direction;
    if (nextIndex < 0 || nextIndex >= instructions.length) return;
    const reordered = [...instructions];
    [reordered[index], reordered[nextIndex]] = [reordered[nextIndex], reordered[index]];
    setInstructions(reordered);
    try {
      await reorderInstructionsRequest(reordered.map((i) => i.id));
    } catch (x) {
      setError(x.message);
      load();
    }
  }

  return (
    <div className="workflow-settings-card">
      <div className="workflow-setting-row" style={{ borderBottom: "none" }}>
        <div>
          <strong>Instructions — how your AI should behave</strong>
          <br />
          <span style={{ fontWeight: 400, color: "#6b7280" }}>
            Behavior rules, not facts — e.g. "Don't share prices", "Use emojis when appropriate", "Don't send follow-up messages".
            Earlier rules take priority when they conflict. Scope a rule to specific departments/channels below, or leave it unscoped to apply everywhere.
          </span>
        </div>
      </div>

      {error ? <p style={{ color: "#c0392b" }}>{error}</p> : null}

      <form onSubmit={addInstruction} style={{ display: "flex", flexDirection: "column", gap: 8, padding: "12px 0" }}>
        <input
          value={newText}
          onChange={(e) => setNewText(e.target.value)}
          placeholder="e.g. Don't share prices in the first message"
          style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #d5dae5" }}
        />
        <TagPicker
          departments={departments}
          selectedDepartments={newDepartments} setSelectedDepartments={setNewDepartments}
          selectedChannels={newChannels} setSelectedChannels={setNewChannels}
          extraTagsInput={newExtraTags} setExtraTagsInput={setNewExtraTags}
        />
        <button type="submit" disabled={saving || !newText.trim()} style={{ alignSelf: "flex-start" }}>{saving ? "Adding..." : "+ Add instruction"}</button>
      </form>

      {instructions.map((instruction, index) => (
        <div className="workflow-setting-row" key={instruction.id} style={{ alignItems: editingId === instruction.id ? "flex-start" : "center" }}>
          {editingId === instruction.id ? (
            <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 8 }}>
              <input
                value={editingText}
                onChange={(e) => setEditingText(e.target.value)}
                style={{ padding: "6px 8px", borderRadius: 6, border: "1px solid #d5dae5" }}
              />
              <TagPicker
                departments={departments}
                selectedDepartments={editingDepartments} setSelectedDepartments={setEditingDepartments}
                selectedChannels={editingChannels} setSelectedChannels={setEditingChannels}
                extraTagsInput={editingExtraTags} setExtraTagsInput={setEditingExtraTags}
              />
              <div style={{ display: "flex", gap: 8 }}>
                <button type="button" onClick={() => saveEdit(instruction.id)} disabled={saving}>Save</button>
                <button type="button" onClick={() => setEditingId(null)}>Cancel</button>
              </div>
            </div>
          ) : (
            <>
              <div>
                <span style={{ color: "#9296AC", marginRight: 8 }}>{index + 1}.</span>{instruction.text}
                {(instruction.tags || []).map((tag) => (
                  <span key={tag} style={{ fontSize: 10, fontWeight: 700, color: "#17A369", background: "#E7FAF1", borderRadius: 999, padding: "2px 8px", marginLeft: 6 }}>{tag}</span>
                ))}
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <button type="button" onClick={() => move(index, -1)} disabled={index === 0}>↑</button>
                <button type="button" onClick={() => move(index, 1)} disabled={index === instructions.length - 1}>↓</button>
                <button type="button" onClick={() => startEditing(instruction)}>Edit</button>
                <button type="button" onClick={() => remove(instruction.id)}>Delete</button>
              </div>
            </>
          )}
        </div>
      ))}
      {!instructions.length ? <p style={{ padding: "12px 0", color: "#6b7280" }}>No instructions yet.</p> : null}
    </div>
  );
}
