import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AddOutlined, ContentCopyOutlined, DeleteOutlineOutlined, EditOutlined } from "@mui/icons-material";
import {
  listReplyFlowsRequest,
  createReplyFlowRequest,
  deleteReplyFlowRequest,
  duplicateReplyFlowRequest,
  listDepartmentsRequest,
} from "../../api/client";
import { AppCard, AppTable, ConfirmDialog, ErrorState, LoadingState, StatusBadge } from "../../components/common";
import MultiSelectChips from "../../components/common/MultiSelectChips";
import "./ReplyFlowsListPage.css";

// Kept in sync by hand with backend/services/reply_flow_service.py's
// CHANNEL_OPTIONS constant.
export const CHANNEL_OPTIONS = [
  { value: "whatsapp", label: "WhatsApp" },
  { value: "messenger", label: "Messenger" },
  { value: "instagram", label: "Instagram" },
  { value: "telegram", label: "Telegram" },
];
const CHANNEL_LABELS = CHANNEL_OPTIONS.reduce((map, option) => ({ ...map, [option.value]: option.label }), {});

// Kept in sync by hand with backend/services/reply_flow_service.py's
// REPLY_MODE_OPTIONS constant.
export const REPLY_MODE_OPTIONS = [
  { value: "ai_direct", label: "AI — Direct" },
  { value: "ai_knowledge_only", label: "AI — Knowledge Only" },
  { value: "ai_knowledge_plus", label: "AI + Knowledge" },
  { value: "canned_reply", label: "Canned Reply" },
  { value: "human_handoff", label: "Human Handoff" },
];
const REPLY_MODE_LABELS = REPLY_MODE_OPTIONS.reduce((map, option) => ({ ...map, [option.value]: option.label }), {});
const STATUS_TONE = { draft: "neutral", active: "success", archived: "danger" };

function formatDateTime(value) {
  if (!value) return "—";
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

function summarizeList(values, labelMap, allLabel) {
  if (!values || values.length === 0) return allLabel;
  return values.map((value) => labelMap?.[value] || value).join(", ");
}

function NewFlowDialog({ open, departments, saving, error, onCancel, onCreate }) {
  const [name, setName] = useState("");
  const [channels, setChannels] = useState([]);
  const [selectedDepartments, setSelectedDepartments] = useState([]);
  const [replyModes, setReplyModes] = useState([]);
  const [buildMode, setBuildMode] = useState("draw");

  if (!open) return null;

  function submit(event) {
    event.preventDefault();
    if (!name.trim()) return;
    onCreate({ name: name.trim(), channels, departments: selectedDepartments, reply_modes: replyModes, buildMode });
  }

  const departmentOptions = departments.filter((name) => name !== "Unassigned").map((name) => ({ value: name, label: name }));

  return (
    <div className="tz-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) onCancel(); }}>
      <form className="tz-dialog" onSubmit={submit}>
        <header className="tz-dialog-header"><h3>Create Reply Flow</h3></header>
        <div className="tz-dialog-body">
          <label className="ai-teaching-field">
            Flow name
            <input value={name} disabled={saving} onChange={(event) => setName(event.target.value)} placeholder="e.g. WhatsApp Sales" required autoFocus />
          </label>
          <label className="ai-teaching-field">
            Channels <span className="reply-flow-field-hint">(leave empty for all channels)</span>
            <MultiSelectChips options={CHANNEL_OPTIONS} value={channels} onChange={setChannels} disabled={saving} />
          </label>
          <label className="ai-teaching-field">
            Departments <span className="reply-flow-field-hint">(leave empty for all departments)</span>
            <MultiSelectChips
              options={departmentOptions}
              value={selectedDepartments}
              onChange={setSelectedDepartments}
              disabled={saving}
              emptyHint="No departments set up yet — add some in Company Settings → Departments first."
            />
          </label>
          <label className="ai-teaching-field">
            Reply mode <span className="reply-flow-field-hint">(leave empty to decide per-step inside the flow)</span>
            <MultiSelectChips options={REPLY_MODE_OPTIONS} value={replyModes} onChange={setReplyModes} disabled={saving} />
          </label>
          <label className="ai-teaching-field">
            How do you want to build it?
            <div className="reply-flow-build-mode-choice">
              <button
                type="button"
                className={`reply-flow-build-mode-card ${buildMode === "draw" ? "is-selected" : ""}`}
                disabled={saving}
                onClick={() => setBuildMode("draw")}
              >
                <strong>Draw it</strong>
                <span>Drag steps onto a visual canvas and connect them.</span>
              </button>
              <button
                type="button"
                className={`reply-flow-build-mode-card ${buildMode === "write" ? "is-selected" : ""}`}
                disabled={saving}
                onClick={() => setBuildMode("write")}
              >
                <strong>Write it</strong>
                <span>Describe the flow in your own words — the AI builds the steps for you.</span>
              </button>
            </div>
          </label>
          {error ? <p className="customer-segment-error">{error}</p> : null}
        </div>
        <footer className="tz-dialog-actions">
          <button type="button" className="btn btn-secondary" disabled={saving} onClick={onCancel}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={saving || !name.trim()}>{saving ? "Creating…" : "Create"}</button>
        </footer>
      </form>
    </div>
  );
}

export default function ReplyFlowsListPage() {
  const navigate = useNavigate();
  const [flows, setFlows] = useState([]);
  const [departments, setDepartments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [dialogOpen, setDialogOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [toDelete, setToDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const result = await listReplyFlowsRequest();
      setFlows(Array.isArray(result?.flows) ? result.flows : []);
    } catch (requestError) {
      setError(requestError.message || "Reply flows could not be loaded.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    listDepartmentsRequest().then((result) => setDepartments(result?.departments || [])).catch(() => {});
  }, []);

  async function createFlow({ buildMode, ...values }) {
    setSaving(true);
    setSaveError("");
    try {
      const flow = await createReplyFlowRequest(values);
      setDialogOpen(false);
      navigate(`/reply-flows/${flow.id}?view=${buildMode === "write" ? "outline" : "canvas"}`);
    } catch (requestError) {
      setSaveError(requestError.message || "Could not create this flow.");
    } finally {
      setSaving(false);
    }
  }

  async function duplicateFlow(flow) {
    try {
      await duplicateReplyFlowRequest(flow.id);
      await load();
    } catch (requestError) {
      setError(requestError.message || "Could not duplicate this flow.");
    }
  }

  async function confirmDelete() {
    if (!toDelete) return;
    setDeleting(true);
    try {
      await deleteReplyFlowRequest(toDelete.id);
      setToDelete(null);
      await load();
    } catch (requestError) {
      setError(requestError.message || "Could not delete this flow.");
    } finally {
      setDeleting(false);
    }
  }

  const columns = [
    {
      key: "name", label: "Flow",
      render: (value, row) => (
        <button type="button" className="reply-flows-name-link" onClick={() => navigate(`/reply-flows/${row.id}`)}>
          <strong>{value}</strong>
          <span>{row.node_count} step{row.node_count === 1 ? "" : "s"}</span>
        </button>
      ),
    },
    { key: "channels", label: "Channels", render: (value) => summarizeList(value, CHANNEL_LABELS, "All channels") },
    { key: "departments", label: "Departments", render: (value) => summarizeList(value, null, "All departments") },
    { key: "reply_modes", label: "Reply mode", render: (value) => summarizeList(value, REPLY_MODE_LABELS, "Per-step") },
    { key: "status", label: "Status", render: (value) => <StatusBadge status={value} tone={STATUS_TONE[value]} label={value} /> },
    { key: "updated_at", label: "Updated", render: (value) => formatDateTime(value) },
    {
      key: "_actions", label: "", align: "right",
      render: (_value, row) => (
        <div className="reply-flows-row-actions">
          <button type="button" title="Edit" onClick={() => navigate(`/reply-flows/${row.id}`)}><EditOutlined fontSize="small" /></button>
          <button type="button" title="Duplicate" onClick={() => duplicateFlow(row)}><ContentCopyOutlined fontSize="small" /></button>
          <button type="button" title="Delete" onClick={() => setToDelete(row)}><DeleteOutlineOutlined fontSize="small" /></button>
        </div>
      ),
    },
  ];

  if (loading) return <LoadingState label="Loading reply flows…" />;
  if (error) return <ErrorState title="Could not load reply flows" description={error} action={<button type="button" className="btn btn-primary" onClick={load}>Retry</button>} />;

  return (
    <section className="reply-flows-page">
      {/* No repeated "Reply Flows" heading here — this list now renders
          embedded inside Company Settings' "Reply Flow & Saved Replies"
          section, which already provides that title and description right
          above. Only the action button belongs in this local header. */}
      <header className="reply-flows-header reply-flows-header-embedded">
        <button type="button" className="btn btn-primary" onClick={() => setDialogOpen(true)}>
          <AddOutlined fontSize="small" /> Create Reply Flow
        </button>
      </header>

      {flows.length === 0 ? (
        <AppCard padding="large" className="reply-flows-empty">
          <p>No reply flows yet.</p>
          <span>Create one to design a step-by-step conversation for a channel or department.</span>
        </AppCard>
      ) : (
        <AppTable columns={columns} rows={flows} emptyTitle="No reply flows" emptyDescription="Create your first flow." />
      )}

      <NewFlowDialog open={dialogOpen} departments={departments} saving={saving} error={saveError} onCancel={() => setDialogOpen(false)} onCreate={createFlow} />
      <ConfirmDialog
        open={Boolean(toDelete)}
        title="Delete reply flow"
        message={`Delete "${toDelete?.name}"? This can't be undone.`}
        confirmLabel="Delete"
        confirmVariant="danger"
        loading={deleting}
        onConfirm={confirmDelete}
        onCancel={() => setToDelete(null)}
      />
    </section>
  );
}
