import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AddOutlined, CloseOutlined } from "@mui/icons-material";
import {
  createCustomerSegmentRequest,
  customerOptionsRequest,
  deleteCustomerSegmentRequest,
  listCustomerSegmentsRequest,
  listCustomersRequest,
  updateCustomerRequest,
} from "../../api/client";
import { AppButton, AppCard, AppTable, ConfirmDialog, ErrorState, SearchBar } from "../../components/common";
import "./CustomersPage.css";

const PAGE_SIZE = 25;

function humanize(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatDate(value) {
  if (!value) return "—";
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleDateString();
}

function TagEditor({ tags, disabled, onAdd, onRemove }) {
  const [draft, setDraft] = useState("");

  function submit(event) {
    event.preventDefault();
    const cleaned = draft.trim();
    if (!cleaned) return;
    onAdd(cleaned);
    setDraft("");
  }

  return (
    <div className="customer-tag-editor">
      <div className="customer-tag-list">
        {tags.map((tag) => (
          <span className="customer-tag-chip" key={tag}>
            {tag}
            <button type="button" disabled={disabled} aria-label={`Remove tag ${tag}`} onClick={() => onRemove(tag)}>
              <CloseOutlined fontSize="inherit" />
            </button>
          </span>
        ))}
      </div>
      <form onSubmit={submit} className="customer-tag-add-form">
        <input value={draft} placeholder="+ tag" disabled={disabled} onChange={(event) => setDraft(event.target.value)} />
      </form>
    </div>
  );
}

export default function CustomersPage() {
  const navigate = useNavigate();
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [lifecycleStages, setLifecycleStages] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [segments, setSegments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [stageFilter, setStageFilter] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const [segmentId, setSegmentId] = useState(null);
  const [page, setPage] = useState(1);

  const [savingRowId, setSavingRowId] = useState(null);
  const [segmentDialogOpen, setSegmentDialogOpen] = useState(false);
  const [segmentName, setSegmentName] = useState("");
  const [segmentSaving, setSegmentSaving] = useState(false);
  const [segmentError, setSegmentError] = useState("");
  const [segmentToDelete, setSegmentToDelete] = useState(null);
  const [segmentDeleting, setSegmentDeleting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await listCustomersRequest({
        search: search || undefined,
        lifecycleStage: stageFilter || undefined,
        tag: tagFilter || undefined,
        segmentId: segmentId || undefined,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      });
      setRows(Array.isArray(result?.items) ? result.items : []);
      setTotal(Number(result?.total || 0));
    } catch (requestError) {
      setError(requestError.message || "Contacts could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [search, stageFilter, tagFilter, segmentId, page]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    customerOptionsRequest()
      .then((result) => {
        setLifecycleStages(Array.isArray(result?.lifecycle_stages) ? result.lifecycle_stages : []);
        setEmployees(Array.isArray(result?.employees) ? result.employees : []);
      })
      .catch(() => {});
  }, []);

  const loadSegments = useCallback(() => {
    listCustomerSegmentsRequest()
      .then((result) => setSegments(Array.isArray(result?.items) ? result.items : []))
      .catch(() => {});
  }, []);

  useEffect(() => { loadSegments(); }, [loadSegments]);

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timeout);
  }, [searchInput]);

  const availableTags = useMemo(() => {
    const values = new Set();
    rows.forEach((row) => (row.tags || []).forEach((item) => values.add(item)));
    return [...values].sort();
  }, [rows]);

  function applyStageFilter(value) {
    setStageFilter(value);
    setPage(1);
  }

  function applyTagFilter(value) {
    setTagFilter(value);
    setPage(1);
  }

  function applySegment(segment) {
    setSegmentId((current) => (current === segment.id ? null : segment.id));
    setPage(1);
  }

  async function changeStage(row, stage) {
    setSavingRowId(row.id);
    try {
      await updateCustomerRequest(row.id, { lifecycle_stage: stage });
      await load();
    } catch (requestError) {
      setError(requestError.message || "Could not update lifecycle stage.");
    } finally {
      setSavingRowId(null);
    }
  }

  async function removeTag(row, tag) {
    setSavingRowId(row.id);
    try {
      await updateCustomerRequest(row.id, { tags: (row.tags || []).filter((item) => item !== tag) });
      await load();
    } catch (requestError) {
      setError(requestError.message || "Could not update tags.");
    } finally {
      setSavingRowId(null);
    }
  }

  async function addTag(row, tag) {
    if ((row.tags || []).includes(tag)) return;
    setSavingRowId(row.id);
    try {
      await updateCustomerRequest(row.id, { tags: [...(row.tags || []), tag] });
      await load();
    } catch (requestError) {
      setError(requestError.message || "Could not update tags.");
    } finally {
      setSavingRowId(null);
    }
  }

  async function changeAssignee(row, value) {
    setSavingRowId(row.id);
    try {
      await updateCustomerRequest(row.id, { assigned_user_id: value ? Number(value) : null });
      await load();
    } catch (requestError) {
      setError(requestError.message || "Could not update the assigned employee.");
    } finally {
      setSavingRowId(null);
    }
  }

  const activeFilters = Boolean(search || stageFilter || tagFilter);

  async function saveSegment(event) {
    event.preventDefault();
    if (!segmentName.trim()) return;
    setSegmentSaving(true);
    setSegmentError("");
    try {
      await createCustomerSegmentRequest(segmentName.trim(), {
        search: search || undefined,
        lifecycle_stage: stageFilter || undefined,
        tag: tagFilter || undefined,
      });
      setSegmentDialogOpen(false);
      setSegmentName("");
      loadSegments();
    } catch (requestError) {
      setSegmentError(requestError.message || "Could not save segment.");
    } finally {
      setSegmentSaving(false);
    }
  }

  async function confirmDeleteSegment() {
    if (!segmentToDelete) return;
    setSegmentDeleting(true);
    try {
      await deleteCustomerSegmentRequest(segmentToDelete.id);
      if (segmentId === segmentToDelete.id) setSegmentId(null);
      setSegmentToDelete(null);
      loadSegments();
    } catch (requestError) {
      setError(requestError.message || "Could not delete segment.");
    } finally {
      setSegmentDeleting(false);
    }
  }

  const columns = [
    {
      key: "display_name",
      label: "Contact",
      render: (_value, row) => (
        <button type="button" className="customer-name-cell customer-name-cell-link" onClick={() => navigate(`/customers/${row.id}`)}>
          <div className="customer-avatar">{(row.display_name || row.internal_name || "?").charAt(0).toUpperCase()}</div>
          <div>
            <strong>{row.display_name || row.internal_name || "Unnamed contact"}</strong>
            <span>{row.phone || row.email || "No phone or email on file"}</span>
          </div>
        </button>
      ),
    },
    {
      key: "channels",
      label: "Channels",
      render: (value) => (
        <div className="customer-channel-list">
          {(value || []).length
            ? value.map((channel) => (
              <span className="customer-channel-chip" style={{ "--channel-color": `var(--tz-channel-${channel}, var(--tz-text-muted))` }} key={channel}>
                {humanize(channel)}
              </span>
            ))
            : <span className="customer-channel-empty">—</span>}
        </div>
      ),
    },
    {
      key: "assigned_user_id",
      label: "Assigned to",
      render: (value, row) => (
        <select
          className="tz-select customer-stage-select"
          value={value || ""}
          disabled={savingRowId === row.id}
          onChange={(event) => changeAssignee(row, event.target.value)}
        >
          <option value="">Unassigned</option>
          {employees.map((employee) => <option value={employee.id} key={employee.id}>{employee.display_name}</option>)}
        </select>
      ),
    },
    {
      key: "lifecycle_stage",
      label: "Lifecycle stage",
      render: (value, row) => (
        <select
          className="tz-select customer-stage-select"
          value={value || ""}
          disabled={savingRowId === row.id}
          onChange={(event) => changeStage(row, event.target.value)}
        >
          {lifecycleStages.map((stage) => <option value={stage} key={stage}>{humanize(stage)}</option>)}
        </select>
      ),
    },
    {
      key: "tags",
      label: "Tags",
      render: (value, row) => (
        <TagEditor
          tags={value || []}
          disabled={savingRowId === row.id}
          onAdd={(tag) => addTag(row, tag)}
          onRemove={(tag) => removeTag(row, tag)}
        />
      ),
    },
    {
      key: "conversation_count",
      label: "Conversations",
      align: "right",
    },
    {
      key: "updated_at",
      label: "Last updated",
      render: (value) => formatDate(value),
    },
  ];

  return (
    <section className="customers-page">
      {segments.length ? (
        <div className="customer-segment-chips">
          {segments.map((segment) => (
            <span className={`customer-segment-chip ${segmentId === segment.id ? "is-active" : ""}`} key={segment.id}>
              <button type="button" onClick={() => applySegment(segment)}>{segment.name}</button>
              <button
                type="button"
                className="customer-segment-chip-remove"
                aria-label={`Delete segment ${segment.name}`}
                onClick={() => setSegmentToDelete(segment)}
              >
                <CloseOutlined fontSize="inherit" />
              </button>
            </span>
          ))}
        </div>
      ) : null}

      <AppCard padding="medium" className="customer-filter-card">
        <div className="customer-filter-bar">
          <SearchBar value={searchInput} placeholder="Search name, phone, email, username..." onChange={setSearchInput} />
          <select className="tz-select" value={stageFilter} onChange={(event) => applyStageFilter(event.target.value)}>
            <option value="">All lifecycle stages</option>
            {lifecycleStages.map((stage) => <option value={stage} key={stage}>{humanize(stage)}</option>)}
          </select>
          <select className="tz-select" value={tagFilter} onChange={(event) => applyTagFilter(event.target.value)}>
            <option value="">All tags</option>
            {availableTags.map((tag) => <option value={tag} key={tag}>{tag}</option>)}
          </select>
          <AppButton
            variant="secondary"
            icon={<AddOutlined fontSize="small" />}
            disabled={!activeFilters}
            onClick={() => setSegmentDialogOpen(true)}
          >
            Save as segment
          </AppButton>
        </div>
      </AppCard>

      {error ? (
        <ErrorState title="Could not load contacts" description={error} action={<AppButton variant="primary" onClick={load}>Retry</AppButton>} />
      ) : (
        <AppTable
          columns={columns}
          rows={rows}
          loading={loading}
          emptyTitle="No contacts found"
          emptyDescription="No contact matches the current search and filters."
          page={page}
          pageSize={PAGE_SIZE}
          totalRows={total}
          onPageChange={setPage}
        />
      )}

      {segmentDialogOpen ? (
        <div
          className="tz-dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => { if (event.target === event.currentTarget) setSegmentDialogOpen(false); }}
        >
          <form className="tz-dialog" onSubmit={saveSegment}>
            <header className="tz-dialog-header">
              <h3>Save current filters as a segment</h3>
              <button type="button" className="tz-dialog-close" onClick={() => setSegmentDialogOpen(false)}>
                <CloseOutlined fontSize="small" />
              </button>
            </header>
            <div className="tz-dialog-body">
              <p className="customer-segment-summary">
                {search ? <span>Search: “{search}”</span> : null}
                {stageFilter ? <span>Stage: {humanize(stageFilter)}</span> : null}
                {tagFilter ? <span>Tag: {tagFilter}</span> : null}
              </p>
              <label className="customer-segment-name-field">
                Segment name
                <input value={segmentName} onChange={(event) => setSegmentName(event.target.value)} maxLength={120} required autoFocus />
              </label>
              {segmentError ? <p className="customer-segment-error">{segmentError}</p> : null}
            </div>
            <footer className="tz-dialog-actions">
              <AppButton type="button" variant="secondary" disabled={segmentSaving} onClick={() => setSegmentDialogOpen(false)}>Cancel</AppButton>
              <AppButton type="submit" variant="primary" loading={segmentSaving}>Save segment</AppButton>
            </footer>
          </form>
        </div>
      ) : null}

      <ConfirmDialog
        open={Boolean(segmentToDelete)}
        title="Delete segment"
        message={`Delete the "${segmentToDelete?.name}" segment? Contacts themselves are not affected.`}
        confirmLabel="Delete"
        confirmVariant="danger"
        loading={segmentDeleting}
        onConfirm={confirmDeleteSegment}
        onCancel={() => setSegmentToDelete(null)}
      />
    </section>
  );
}
