import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AddOutlined, CloseOutlined } from "@mui/icons-material";
import {
  bulkUpdateCustomersRequest,
  createCustomerRequest,
  createCustomerSegmentRequest,
  customerOptionsRequest,
  deleteCustomerSegmentRequest,
  listCustomerSegmentsRequest,
  listCustomersRequest,
  updateCustomerRequest,
} from "../../api/client";
import { AppButton, AppCard, AppTable, ConfirmDialog, ErrorState, SearchBar } from "../../components/common";
import { useAuth } from "../../contexts/AuthContext";
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
  const { user, companies } = useAuth();
  const canManageSettings = useMemo(() => {
    if (user?.is_super_admin) return true;
    const activeCompany = companies.find((company) => company.id === user?.active_company_id) || companies[0];
    return activeCompany?.role_code === "owner" || (activeCompany?.permission_codes || []).includes("settings.manage");
  }, [user, companies]);
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
  const [assigneeFilter, setAssigneeFilter] = useState("");
  const [segmentId, setSegmentId] = useState(null);
  const [page, setPage] = useState(1);

  const [savingRowId, setSavingRowId] = useState(null);
  const [segmentDialogOpen, setSegmentDialogOpen] = useState(false);
  const [segmentName, setSegmentName] = useState("");
  const [segmentSaving, setSegmentSaving] = useState(false);
  const [segmentError, setSegmentError] = useState("");
  const [segmentToDelete, setSegmentToDelete] = useState(null);
  const [segmentDeleting, setSegmentDeleting] = useState(false);

  const [newContactDialogOpen, setNewContactDialogOpen] = useState(false);
  const [newContactName, setNewContactName] = useState("");
  const [newContactPhone, setNewContactPhone] = useState("");
  const [newContactEmail, setNewContactEmail] = useState("");
  const [newContactSaving, setNewContactSaving] = useState(false);
  const [newContactError, setNewContactError] = useState("");

  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [bulkStage, setBulkStage] = useState("");
  const [bulkTagDraft, setBulkTagDraft] = useState("");
  const [bulkApplying, setBulkApplying] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await listCustomersRequest({
        search: search || undefined,
        lifecycleStage: stageFilter || undefined,
        tag: tagFilter || undefined,
        assignedUserId: assigneeFilter || undefined,
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
  }, [search, stageFilter, tagFilter, assigneeFilter, segmentId, page]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => { setSelectedIds(new Set()); }, [page, search, stageFilter, tagFilter, assigneeFilter, segmentId]);

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

  // Accumulates every tag ever seen across loads instead of being
  // recomputed from just the current (possibly tag-filtered) page - a
  // plain useMemo off `rows` would make the dropdown's own option list
  // collapse to just the active tag the moment it's used, since `rows`
  // only ever contains matching results once a tag filter is applied.
  const seenTagsRef = useRef(new Set());
  const [availableTags, setAvailableTags] = useState([]);
  useEffect(() => {
    let changed = false;
    rows.forEach((row) => (row.tags || []).forEach((item) => {
      if (!seenTagsRef.current.has(item)) { seenTagsRef.current.add(item); changed = true; }
    }));
    if (changed) setAvailableTags([...seenTagsRef.current].sort());
  }, [rows]);

  function applyStageFilter(value) {
    setStageFilter(value);
    setPage(1);
  }

  function applyTagFilter(value) {
    setTagFilter(value);
    setPage(1);
  }

  function applyAssigneeFilter(value) {
    setAssigneeFilter(value);
    setPage(1);
  }

  function applySegment(segment) {
    const next = segmentId === segment.id ? null : segment.id;
    const filters = next ? segment.filters || {} : {};
    setSegmentId(next);
    setSearchInput(filters.search || "");
    setSearch(filters.search || "");
    setStageFilter(filters.lifecycle_stage || "");
    setTagFilter(filters.tag || "");
    setAssigneeFilter(filters.assigned_user_id ? String(filters.assigned_user_id) : "");
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

  const activeFilters = Boolean(search || stageFilter || tagFilter || assigneeFilter);

  function toggleRowSelected(rowId) {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(rowId)) next.delete(rowId);
      else next.add(rowId);
      return next;
    });
  }

  function toggleSelectAllOnPage() {
    setSelectedIds((current) => {
      const allSelected = rows.length > 0 && rows.every((row) => current.has(row.id));
      if (allSelected) return new Set();
      return new Set(rows.map((row) => row.id));
    });
  }

  function clearSelection() {
    setSelectedIds(new Set());
  }

  async function applyBulkStage(stage) {
    if (!stage || !selectedIds.size) return;
    setBulkApplying(true);
    try {
      await bulkUpdateCustomersRequest({ customer_ids: [...selectedIds], lifecycle_stage: stage });
      setBulkStage("");
      clearSelection();
      await load();
    } catch (requestError) {
      setError(requestError.message || "Could not update lifecycle stage for the selected contacts.");
    } finally {
      setBulkApplying(false);
    }
  }

  async function applyBulkTag(event) {
    event.preventDefault();
    const tag = bulkTagDraft.trim();
    if (!tag || !selectedIds.size) return;
    setBulkApplying(true);
    try {
      await bulkUpdateCustomersRequest({ customer_ids: [...selectedIds], add_tag: tag });
      setBulkTagDraft("");
      clearSelection();
      await load();
    } catch (requestError) {
      setError(requestError.message || "Could not add the tag to the selected contacts.");
    } finally {
      setBulkApplying(false);
    }
  }

  async function saveNewContact(event) {
    event.preventDefault();
    setNewContactSaving(true);
    setNewContactError("");
    try {
      const created = await createCustomerRequest({
        display_name: newContactName.trim() || undefined,
        phone: newContactPhone.trim() || undefined,
        email: newContactEmail.trim() || undefined,
      });
      setNewContactDialogOpen(false);
      setNewContactName("");
      setNewContactPhone("");
      setNewContactEmail("");
      await load();
      navigate(`/customers/${created.id}`);
    } catch (requestError) {
      setNewContactError(requestError.message || "Could not create the contact.");
    } finally {
      setNewContactSaving(false);
    }
  }

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
        assigned_user_id: assigneeFilter ? Number(assigneeFilter) : undefined,
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

  const allOnPageSelected = rows.length > 0 && rows.every((row) => selectedIds.has(row.id));

  const columns = [
    // Bulk actions require settings.manage server-side - selection
    // checkboxes are pointless (and misleading) to show otherwise.
    ...(canManageSettings ? [{
      key: "_select",
      label: (
        <input
          type="checkbox"
          className="customer-row-checkbox"
          aria-label="Select all contacts on this page"
          checked={allOnPageSelected}
          onChange={toggleSelectAllOnPage}
        />
      ),
      width: 40,
      render: (_value, row) => (
        <input
          type="checkbox"
          className="customer-row-checkbox"
          aria-label={`Select ${row.display_name || row.internal_name || "contact"}`}
          checked={selectedIds.has(row.id)}
          onChange={() => toggleRowSelected(row.id)}
        />
      ),
    }] : []),
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
          {segments.map((segment) => {
            const canDeleteSegment = canManageSettings || segment.created_by_user_id === user?.id;
            return (
              <span className={`customer-segment-chip ${segmentId === segment.id ? "is-active" : ""}`} key={segment.id}>
                <button type="button" onClick={() => applySegment(segment)}>{segment.name}</button>
                {canDeleteSegment ? (
                  <button
                    type="button"
                    className="customer-segment-chip-remove"
                    aria-label={`Delete segment ${segment.name}`}
                    onClick={() => setSegmentToDelete(segment)}
                  >
                    <CloseOutlined fontSize="inherit" />
                  </button>
                ) : null}
              </span>
            );
          })}
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
          <select className="tz-select" value={assigneeFilter} onChange={(event) => applyAssigneeFilter(event.target.value)}>
            <option value="">All employees</option>
            {employees.map((employee) => <option value={employee.id} key={employee.id}>{employee.display_name}</option>)}
          </select>
          <div className="customer-filter-actions">
            <AppButton
              variant="secondary"
              icon={<AddOutlined fontSize="small" />}
              disabled={!activeFilters}
              onClick={() => setSegmentDialogOpen(true)}
            >
              Save as segment
            </AppButton>
            <AppButton
              variant="primary"
              icon={<AddOutlined fontSize="small" />}
              onClick={() => setNewContactDialogOpen(true)}
            >
              New contact
            </AppButton>
          </div>
        </div>
      </AppCard>

      {selectedIds.size > 0 ? (
        <AppCard padding="medium" className="customer-bulk-bar">
          <span className="customer-bulk-count">{selectedIds.size} selected</span>
          <label className="customer-bulk-field">
            Set lifecycle stage
            <select
              className="tz-select"
              value={bulkStage}
              disabled={bulkApplying}
              onChange={(event) => {
                setBulkStage(event.target.value);
                applyBulkStage(event.target.value);
              }}
            >
              <option value="">Choose stage…</option>
              {lifecycleStages.map((stage) => <option value={stage} key={stage}>{humanize(stage)}</option>)}
            </select>
          </label>
          <form className="customer-bulk-field customer-bulk-tag-form" onSubmit={applyBulkTag}>
            Add tag
            <div className="customer-bulk-tag-input">
              <input
                value={bulkTagDraft}
                placeholder="tag name"
                disabled={bulkApplying}
                onChange={(event) => setBulkTagDraft(event.target.value)}
              />
              <AppButton type="submit" variant="secondary" size="small" disabled={bulkApplying || !bulkTagDraft.trim()}>
                Add
              </AppButton>
            </div>
          </form>
          <AppButton type="button" variant="secondary" size="small" disabled={bulkApplying} onClick={clearSelection}>
            Clear selection
          </AppButton>
        </AppCard>
      ) : null}

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

      {newContactDialogOpen ? (
        <div
          className="tz-dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => { if (event.target === event.currentTarget) setNewContactDialogOpen(false); }}
        >
          <form className="tz-dialog" onSubmit={saveNewContact}>
            <header className="tz-dialog-header">
              <h3>New contact</h3>
              <button type="button" className="tz-dialog-close" onClick={() => setNewContactDialogOpen(false)}>
                <CloseOutlined fontSize="small" />
              </button>
            </header>
            <div className="tz-dialog-body customer-new-contact-fields">
              <label className="customer-segment-name-field">
                Name
                <input value={newContactName} onChange={(event) => setNewContactName(event.target.value)} maxLength={200} autoFocus />
              </label>
              <label className="customer-segment-name-field">
                Phone
                <input value={newContactPhone} onChange={(event) => setNewContactPhone(event.target.value)} maxLength={80} />
              </label>
              <label className="customer-segment-name-field">
                Email
                <input value={newContactEmail} onChange={(event) => setNewContactEmail(event.target.value)} maxLength={320} />
              </label>
              {newContactError ? <p className="customer-segment-error">{newContactError}</p> : null}
            </div>
            <footer className="tz-dialog-actions">
              <AppButton type="button" variant="secondary" disabled={newContactSaving} onClick={() => setNewContactDialogOpen(false)}>Cancel</AppButton>
              <AppButton type="submit" variant="primary" loading={newContactSaving}>Create contact</AppButton>
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
