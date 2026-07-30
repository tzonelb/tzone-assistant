import { useEffect, useMemo, useState } from "react";
import { CloseOutlined } from "@mui/icons-material";
import {
  createInstructionRequest,
  createKnowledgeEntryRequest,
  deleteInstructionRequest,
  deleteKnowledgeEntryRequest,
  listDepartmentsRequest,
  listInstructionsRequest,
  listKnowledgeEntriesRequest,
  updateInstructionRequest,
  updateKnowledgeEntryRequest,
} from "../../api/client";
import { AppButton, AppCard, AppTable, ConfirmDialog, ErrorState, LoadingState } from "../../components/common";
import "../customers/CustomersPage.css";
import "./AITeachingPage.css";

function TagList({ tags }) {
  if (!tags?.length) return <span className="ai-teaching-empty-hint">—</span>;
  return (
    <div className="customer-tag-list">
      {tags.map((tag) => <span className="customer-tag-chip ai-teaching-tag-chip" key={tag}>{tag}</span>)}
    </div>
  );
}

function InstructionDialog({ open, initial, saving, error, onCancel, onSave }) {
  const [text, setText] = useState(initial?.text || "");
  const [tagsInput, setTagsInput] = useState((initial?.tags || []).join(", "));

  useEffect(() => {
    setText(initial?.text || "");
    setTagsInput((initial?.tags || []).join(", "));
  }, [initial]);

  if (!open) return null;

  function submit(event) {
    event.preventDefault();
    const tags = tagsInput.split(",").map((tag) => tag.trim()).filter(Boolean);
    onSave({ text: text.trim(), tags });
  }

  return (
    <div className="tz-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) onCancel(); }}>
      <form className="tz-dialog" onSubmit={submit}>
        <header className="tz-dialog-header">
          <h3>{initial ? "Edit instruction" : "New instruction"}</h3>
          <button type="button" className="tz-dialog-close" onClick={onCancel} disabled={saving}><CloseOutlined fontSize="small" /></button>
        </header>
        <div className="tz-dialog-body">
          <label className="ai-teaching-field">
            Instruction
            <textarea rows={3} maxLength={500} value={text} disabled={saving} onChange={(event) => setText(event.target.value)} required autoFocus />
          </label>
          <label className="ai-teaching-field">
            Applies to tags (comma separated — channel/department; leave blank to apply everywhere)
            <input value={tagsInput} disabled={saving} onChange={(event) => setTagsInput(event.target.value)} placeholder="e.g. telegram, sales" />
          </label>
          {error ? <p className="customer-segment-error">{error}</p> : null}
        </div>
        <footer className="tz-dialog-actions">
          <AppButton type="button" variant="secondary" disabled={saving} onClick={onCancel}>Cancel</AppButton>
          <AppButton type="submit" variant="primary" loading={saving} disabled={!text.trim()}>Save</AppButton>
        </footer>
      </form>
    </div>
  );
}

function KnowledgeDialog({ open, initial, departments, saving, error, onCancel, onSave }) {
  const [title, setTitle] = useState(initial?.title || "");
  const [content, setContent] = useState(initial?.content || "");
  const [department, setDepartment] = useState(initial?.department || "Unassigned");
  const [tagsInput, setTagsInput] = useState((initial?.tags || []).join(", "));

  useEffect(() => {
    setTitle(initial?.title || "");
    setContent(initial?.content || "");
    setDepartment(initial?.department || "Unassigned");
    setTagsInput((initial?.tags || []).join(", "));
  }, [initial]);

  if (!open) return null;

  function submit(event) {
    event.preventDefault();
    const tags = tagsInput.split(",").map((tag) => tag.trim()).filter(Boolean);
    onSave({ title: title.trim(), content: content.trim(), department, tags });
  }

  return (
    <div className="tz-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) onCancel(); }}>
      <form className="tz-dialog ai-teaching-knowledge-dialog" onSubmit={submit}>
        <header className="tz-dialog-header">
          <h3>{initial ? "Edit knowledge entry" : "New knowledge entry"}</h3>
          <button type="button" className="tz-dialog-close" onClick={onCancel} disabled={saving}><CloseOutlined fontSize="small" /></button>
        </header>
        <div className="tz-dialog-body">
          <label className="ai-teaching-field">
            Question / title
            <input value={title} maxLength={200} disabled={saving} onChange={(event) => setTitle(event.target.value)} required autoFocus />
          </label>
          <label className="ai-teaching-field">
            Answer
            <textarea rows={4} maxLength={4000} value={content} disabled={saving} onChange={(event) => setContent(event.target.value)} required />
          </label>
          <label className="ai-teaching-field">
            Department
            <select className="tz-select" value={department} disabled={saving} onChange={(event) => setDepartment(event.target.value)}>
              <option value="Unassigned">Unassigned</option>
              {departments.filter((name) => name !== "Unassigned").map((name) => <option value={name} key={name}>{name}</option>)}
            </select>
          </label>
          <label className="ai-teaching-field">
            Tags (comma separated)
            <input value={tagsInput} disabled={saving} onChange={(event) => setTagsInput(event.target.value)} />
          </label>
          {error ? <p className="customer-segment-error">{error}</p> : null}
        </div>
        <footer className="tz-dialog-actions">
          <AppButton type="button" variant="secondary" disabled={saving} onClick={onCancel}>Cancel</AppButton>
          <AppButton type="submit" variant="primary" loading={saving} disabled={!title.trim() || !content.trim()}>Save</AppButton>
        </footer>
      </form>
    </div>
  );
}

export default function AITeachingPage() {
  const [instructions, setInstructions] = useState([]);
  const [knowledge, setKnowledge] = useState([]);
  const [departments, setDepartments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [instructionDialog, setInstructionDialog] = useState(null); // null | "new" | {...instruction}
  const [instructionSaving, setInstructionSaving] = useState(false);
  const [instructionError, setInstructionError] = useState("");
  const [instructionToDelete, setInstructionToDelete] = useState(null);
  const [instructionDeleting, setInstructionDeleting] = useState(false);

  const [knowledgeDialog, setKnowledgeDialog] = useState(null);
  const [knowledgeSaving, setKnowledgeSaving] = useState(false);
  const [knowledgeError, setKnowledgeError] = useState("");
  const [knowledgeToDelete, setKnowledgeToDelete] = useState(null);
  const [knowledgeDeleting, setKnowledgeDeleting] = useState(false);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const [instructionsResult, knowledgeResult, departmentsResult] = await Promise.all([
        listInstructionsRequest(),
        listKnowledgeEntriesRequest(),
        listDepartmentsRequest(),
      ]);
      setInstructions(Array.isArray(instructionsResult?.instructions) ? instructionsResult.instructions : []);
      setKnowledge(Array.isArray(knowledgeResult?.entries) ? knowledgeResult.entries : []);
      setDepartments(Array.isArray(departmentsResult?.departments) ? departmentsResult.departments.map((d) => d.name || d) : []);
    } catch (requestError) {
      setError(requestError.message || "AI Teaching could not be loaded.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function saveInstruction(values) {
    setInstructionSaving(true);
    setInstructionError("");
    try {
      if (instructionDialog && instructionDialog !== "new") {
        await updateInstructionRequest(instructionDialog.id, values.text, values.tags);
      } else {
        await createInstructionRequest(values.text, values.tags);
      }
      setInstructionDialog(null);
      await load();
    } catch (requestError) {
      setInstructionError(requestError.message || "Could not save instruction.");
    } finally {
      setInstructionSaving(false);
    }
  }

  async function confirmDeleteInstruction() {
    if (!instructionToDelete) return;
    setInstructionDeleting(true);
    try {
      await deleteInstructionRequest(instructionToDelete.id);
      setInstructionToDelete(null);
      await load();
    } catch (requestError) {
      setError(requestError.message || "Could not delete instruction.");
    } finally {
      setInstructionDeleting(false);
    }
  }

  async function saveKnowledge(values) {
    setKnowledgeSaving(true);
    setKnowledgeError("");
    try {
      if (knowledgeDialog && knowledgeDialog !== "new") {
        await updateKnowledgeEntryRequest(knowledgeDialog.id, values.title, values.content, values.department, values.tags);
      } else {
        await createKnowledgeEntryRequest(values.title, values.content, values.department, values.tags);
      }
      setKnowledgeDialog(null);
      await load();
    } catch (requestError) {
      setKnowledgeError(requestError.message || "Could not save knowledge entry.");
    } finally {
      setKnowledgeSaving(false);
    }
  }

  async function confirmDeleteKnowledge() {
    if (!knowledgeToDelete) return;
    setKnowledgeDeleting(true);
    try {
      await deleteKnowledgeEntryRequest(knowledgeToDelete.id);
      setKnowledgeToDelete(null);
      await load();
    } catch (requestError) {
      setError(requestError.message || "Could not delete knowledge entry.");
    } finally {
      setKnowledgeDeleting(false);
    }
  }

  const instructionColumns = useMemo(() => [
    { key: "text", label: "Instruction" },
    { key: "tags", label: "Applies to", render: (value) => <TagList tags={value} /> },
    {
      key: "_actions", label: "", align: "right",
      render: (_value, row) => (
        <div className="ai-teaching-row-actions">
          <AppButton variant="secondary" size="small" onClick={() => setInstructionDialog(row)}>Edit</AppButton>
          <AppButton variant="danger" size="small" onClick={() => setInstructionToDelete(row)}>Delete</AppButton>
        </div>
      ),
    },
  ], []);

  const knowledgeColumns = useMemo(() => [
    {
      key: "title", label: "Question",
      render: (value, row) => (
        <div>
          <strong>{value}</strong>
          <p className="ai-teaching-content-preview">{row.content}</p>
        </div>
      ),
    },
    { key: "department", label: "Department" },
    { key: "tags", label: "Tags", render: (value) => <TagList tags={value} /> },
    {
      key: "_actions", label: "", align: "right",
      render: (_value, row) => (
        <div className="ai-teaching-row-actions">
          <AppButton variant="secondary" size="small" onClick={() => setKnowledgeDialog(row)}>Edit</AppButton>
          <AppButton variant="danger" size="small" onClick={() => setKnowledgeToDelete(row)}>Delete</AppButton>
        </div>
      ),
    },
  ], []);

  if (loading) return <LoadingState title="Loading AI Teaching..." />;
  if (error) return <ErrorState title="Could not load AI Teaching" description={error} action={<AppButton variant="primary" onClick={load}>Retry</AppButton>} />;

  return (
    <section className="customers-page ai-teaching-page">
      <AppCard padding="medium">
        <div className="ai-teaching-section-head">
          <h3 className="client-file-section-title">Instructions</h3>
          <AppButton variant="secondary" onClick={() => setInstructionDialog("new")}>+ New instruction</AppButton>
        </div>
        <p className="ai-teaching-section-hint">Standing rules the AI always follows — tone, boundaries, how to handle specific situations. Untagged instructions apply everywhere.</p>
        <AppTable columns={instructionColumns} rows={instructions} emptyTitle="No instructions yet" emptyDescription="Add the first rule you want the AI to always follow." />
      </AppCard>

      <AppCard padding="medium">
        <div className="ai-teaching-section-head">
          <h3 className="client-file-section-title">Knowledge base</h3>
          <AppButton variant="secondary" onClick={() => setKnowledgeDialog("new")}>+ New entry</AppButton>
        </div>
        <p className="ai-teaching-section-hint">Question/answer pairs the AI draws on to answer customers accurately — pricing, policies, product details.</p>
        <AppTable columns={knowledgeColumns} rows={knowledge} emptyTitle="No knowledge entries yet" emptyDescription="Add what the AI needs to know to answer customers correctly." />
      </AppCard>

      <InstructionDialog
        open={Boolean(instructionDialog)}
        initial={instructionDialog === "new" ? null : instructionDialog}
        saving={instructionSaving}
        error={instructionError}
        onCancel={() => setInstructionDialog(null)}
        onSave={saveInstruction}
      />
      <ConfirmDialog
        open={Boolean(instructionToDelete)}
        title="Delete instruction"
        message="Delete this instruction? The AI will no longer follow it."
        confirmLabel="Delete"
        confirmVariant="danger"
        loading={instructionDeleting}
        onConfirm={confirmDeleteInstruction}
        onCancel={() => setInstructionToDelete(null)}
      />

      <KnowledgeDialog
        open={Boolean(knowledgeDialog)}
        initial={knowledgeDialog === "new" ? null : knowledgeDialog}
        departments={departments}
        saving={knowledgeSaving}
        error={knowledgeError}
        onCancel={() => setKnowledgeDialog(null)}
        onSave={saveKnowledge}
      />
      <ConfirmDialog
        open={Boolean(knowledgeToDelete)}
        title="Delete knowledge entry"
        message="Delete this knowledge entry? The AI will no longer use it to answer customers."
        confirmLabel="Delete"
        confirmVariant="danger"
        loading={knowledgeDeleting}
        onConfirm={confirmDeleteKnowledge}
        onCancel={() => setKnowledgeToDelete(null)}
      />
    </section>
  );
}
