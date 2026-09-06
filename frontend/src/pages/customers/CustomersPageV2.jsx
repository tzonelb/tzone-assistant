import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
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
import { EmptyState, ErrorState, LoadingState } from "../../components/common";
import { useAuth } from "../../contexts/AuthContext";
import "./CustomersPageV2.css";

// Same real data + actions as CustomersPage.jsx (v1) — this is a visual
// rebuild only, matching the mockup's kicker + segment tag row + bordered
// table + Previous/Next pagination footer.
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
    <div className="tzv2-cust-tag-editor">
      <div className="tzv2-cust-tag-list">
        {tags.map((tag) => (
          <span className="tag tag-outline tzv2-cust-tag-chip" key={tag}>
            {tag}
            <button type="button" disabled={disabled} aria-label={`Remove tag ${tag}`} onClick={() => onRemove(tag)}>
              <CloseOutlined fontSize="inherit" />
            </button>
          </span>
        ))}
      </div>
      <form onSubmit={submit} className="tzv2-cust-tag-add-form">
        <input value={draft} placeholder="+ tag" disabled={disabled} onChange={(event) => setDraft(event.target.value)} />
      </form>
    </div>
  );
}

export default function CustomersPageV2() {
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

  const [searchParams] = useSearchParams();
  // Deep-link support for the topbar quick-search ("/customers?q=...") —
  // e.g. the global search lands here with a prefilled term.
  const initialQuery = searchParams.get("q") || "";
  const [searchInput, setSearchInput] = useState(initialQuery);
  const [search, setSearch] = useState(initialQuery);
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

  function clearSegment() {
    if (segmentId === null) return;
    setSegmentId(null);
    setSearchInput("");
    setSearch("");
    setStageFilter("");
    setTagFilter("");
    setAssigneeFilter("");
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

  const activeFilters = Boolean(search || stageFilter || tagFilter || assigneeFilter);
  const allOnPageSelected = rows.length > 0 && rows.every((row) => selectedIds.has(row.id));

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const rangeFrom = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const rangeTo = Math.min(page * PAGE_SIZE, total);

  if (loading && !rows.length) {
    return (
      <div className="tz-screen tzv2-cust-page">
        <LoadingState title="Loading contacts…" description="Retrieving the customer register." />
      </div>
    );
  }

  return (
    <div className="tz-screen tzv2-cust-page">
      <div className="tzv2-cust-head">
        <div>
          <span className="tz-kick tzv2-cust-kick">Customer register · {total.toLocaleString()} records</span>
        </div>
        <div className="tzv2-cust-head-actions">
          <input
            className="input tzv2-cust-search"
            value={searchInput}
            placeholder="Search name, phone, email, username..."
            onChange={(event) => setSearchInput(event.target.value)}
            aria-label="Search contacts"
          />
          <button type="button" className="btn btn-secondary" disabled={!activeFilters} onClick={() => setSegmentDialogOpen(true)}>
            New segment
          </button>
          <button type="button" className="btn btn-primary" onClick={() => setNewContactDialogOpen(true)}>
            <AddOutlined fontSize="small" /> Add contact
          </button>
        </div>
      </div>

      <div className="tzv2-cust-segments-row">
        <span className="tz-kick tzv2-cust-segments-label">Segments</span>
        <button type="button" className={`tag ${segmentId === null ? "tag-outline" : "tag-neutral"}`} onClick={clearSegment}>
          All contacts
        </button>
        {segments.map((segment) => {
          const canDeleteSegment = canManageSettings || segment.created_by_user_id === user?.id;
          const active = segmentId === segment.id;
          return (
            <span className={`tag ${active ? "tag-outline" : "tag-neutral"} tzv2-cust-segment-tag`} key={segment.id}>
              <button type="button" onClick={() => applySegment(segment)}>{segment.name}</button>
              {canDeleteSegment ? (
                <button
                  type="button"
                  className="tzv2-cust-segment-remove"
                  aria-label={`Delete segment ${segment.name}`}
                  onClick={() => setSegmentToDelete(segment)}
                >
                  <CloseOutlined fontSize="inherit" />
                </button>
              ) : null}
            </span>
          );
        })}

        <div className="tzv2-cust-segments-filters">
          <select
            className="input tzv2-cust-filter-select"
            value={stageFilter}
            onChange={(event) => applyStageFilter(event.target.value)}
            aria-label="Filter by lifecycle stage"
          >
            <option value="">Stage: any</option>
            {lifecycleStages.map((stage) => <option value={stage} key={stage}>{humanize(stage)}</option>)}
          </select>
          <select
            className="input tzv2-cust-filter-select"
            value={assigneeFilter}
            onChange={(event) => applyAssigneeFilter(event.target.value)}
            aria-label="Filter by owner"
          >
            <option value="">Owner: any</option>
            {employees.map((employee) => <option value={employee.id} key={employee.id}>{employee.display_name}</option>)}
          </select>
          <select
            className="input tzv2-cust-filter-select"
            value={tagFilter}
            onChange={(event) => applyTagFilter(event.target.value)}
            aria-label="Filter by tag"
          >
            <option value="">Tag: any</option>
            {availableTags.map((tag) => <option value={tag} key={tag}>{tag}</option>)}
          </select>
        </div>
      </div>

      {selectedIds.size > 0 ? (
        <div className="card tzv2-cust-bulk-bar">
          <span className="tzv2-cust-bulk-count">{selectedIds.size} selected</span>
          <label className="tzv2-cust-bulk-field">
            Set lifecycle stage
            <select
              className="input"
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
          <form className="tzv2-cust-bulk-field tzv2-cust-bulk-tag-form" onSubmit={applyBulkTag}>
            Add tag
            <div className="tzv2-cust-bulk-tag-input">
              <input
                className="input"
                value={bulkTagDraft}
                placeholder="tag name"
                disabled={bulkApplying}
                onChange={(event) => setBulkTagDraft(event.target.value)}
              />
              <button type="submit" className="btn btn-secondary" disabled={bulkApplying || !bulkTagDraft.trim()}>Add</button>
            </div>
          </form>
          <button type="button" className="btn btn-ghost" disabled={bulkApplying} onClick={clearSelection}>Clear selection</button>
        </div>
      ) : null}

      {error ? (
        <ErrorState title="Could not load contacts" description={error} action={<button type="button" className="btn btn-primary" onClick={load}>Retry</button>} />
      ) : rows.length ? (
        <div className="tz-tablewrap tzv2-cust-tablewrap">
          <table className="table">
            <thead>
              <tr>
                {canManageSettings ? (
                  <th style={{ width: 34 }}>
                    <input
                      type="checkbox"
                      className="tzv2-cust-checkbox"
                      aria-label="Select all contacts on this page"
                      checked={allOnPageSelected}
                      onChange={toggleSelectAllOnPage}
                    />
                  </th>
                ) : null}
                <th>Contact</th>
                <th>Channels</th>
                <th>Stage</th>
                <th>Tags</th>
                <th>Owner</th>
                <th style={{ textAlign: "right" }}>Conversations</th>
                <th>Last activity</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  {canManageSettings ? (
                    <td>
                      <input
                        type="checkbox"
                        className="tzv2-cust-checkbox"
                        aria-label={`Select ${row.display_name || row.internal_name || "contact"}`}
                        checked={selectedIds.has(row.id)}
                        onChange={() => toggleRowSelected(row.id)}
                      />
                    </td>
                  ) : null}
                  <td>
                    <button type="button" className="tzv2-cust-name-cell" onClick={() => navigate(`/customers/${row.id}`)}>
                      <span className="tzv2-cust-avatar">{(row.display_name || row.internal_name || "?").charAt(0).toUpperCase()}</span>
                      <span>
                        <strong>{row.display_name || row.internal_name || "Unnamed contact"}</strong>
                        <span className="tz-num tzv2-cust-name-sub">{row.phone || row.email || "No phone or email on file"}</span>
                      </span>
                    </button>
                  </td>
                  <td>
                    <div className="tzv2-cust-channel-list">
                      {(row.channels || []).length
                        ? row.channels.map((channel) => <span className="tag tag-outline tzv2-cust-channel-chip" key={channel}>{humanize(channel)}</span>)
                        : <span className="tzv2-cust-muted">—</span>}
                    </div>
                  </td>
                  <td>
                    <select
                      className="input tzv2-cust-inline-select"
                      value={row.lifecycle_stage || ""}
                      disabled={savingRowId === row.id}
                      onChange={(event) => changeStage(row, event.target.value)}
                      aria-label="Lifecycle stage"
                    >
                      {lifecycleStages.map((stage) => <option value={stage} key={stage}>{humanize(stage)}</option>)}
                    </select>
                  </td>
                  <td>
                    <TagEditor
                      tags={row.tags || []}
                      disabled={savingRowId === row.id}
                      onAdd={(tag) => addTag(row, tag)}
                      onRemove={(tag) => removeTag(row, tag)}
                    />
                  </td>
                  <td>
                    <select
                      className="input tzv2-cust-inline-select"
                      value={row.assigned_user_id || ""}
                      disabled={savingRowId === row.id}
                      onChange={(event) => changeAssignee(row, event.target.value)}
                      aria-label="Assigned employee"
                    >
                      <option value="">Unassigned</option>
                      {employees.map((employee) => <option value={employee.id} key={employee.id}>{employee.display_name}</option>)}
                    </select>
                  </td>
                  <td className="tz-num" style={{ textAlign: "right" }}>{row.conversation_count ?? 0}</td>
                  <td className="tz-num tzv2-cust-last">{formatDate(row.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState title="No contacts found" description="No contact matches the current search and filters." />
      )}

      <div className="tzv2-cust-pagination">
        <span className="tz-kick tz-num tzv2-cust-pagination-summary">
          {total ? `Showing ${rangeFrom}–${rangeTo} of ${total.toLocaleString()}` : "No records"} · Page {page} of {totalPages}
        </span>
        <div className="tzv2-cust-pagination-actions">
          <button type="button" className="btn btn-secondary" disabled={page <= 1} onClick={() => setPage((current) => Math.max(1, current - 1))}>
            Previous
          </button>
          <button type="button" className="btn btn-secondary" disabled={rangeTo >= total} onClick={() => setPage((current) => current + 1)}>
            Next
          </button>
        </div>
      </div>

      {segmentDialogOpen ? (
        <div
          className="dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => { if (event.target === event.currentTarget) setSegmentDialogOpen(false); }}
        >
          <form className="dialog" role="dialog" aria-modal="true" aria-labelledby="tzv2-cust-segment-title" onSubmit={saveSegment}>
            <div className="tzv2-cust-dialog-head">
              <span className="dialog-title" id="tzv2-cust-segment-title">Save current filters as a segment</span>
              <button type="button" className="btn btn-ghost btn-icon" aria-label="Close dialog" onClick={() => setSegmentDialogOpen(false)}>
                <CloseOutlined fontSize="small" />
              </button>
            </div>
            <div className="dialog-body tzv2-cust-dialog-body">
              <p className="tzv2-cust-segment-summary">
                {search ? <span className="tag tag-neutral">Search: "{search}"</span> : null}
                {stageFilter ? <span className="tag tag-neutral">Stage: {humanize(stageFilter)}</span> : null}
                {tagFilter ? <span className="tag tag-neutral">Tag: {tagFilter}</span> : null}
                {assigneeFilter ? <span className="tag tag-neutral">Owner filter set</span> : null}
              </p>
              <div className="field">
                <label>Segment name</label>
                <input className="input" value={segmentName} onChange={(event) => setSegmentName(event.target.value)} maxLength={120} required autoFocus />
              </div>
              {segmentError ? <p className="tzv2-cust-form-error">{segmentError}</p> : null}
            </div>
            <div className="dialog-actions">
              <button type="button" className="btn btn-secondary" disabled={segmentSaving} onClick={() => setSegmentDialogOpen(false)}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={segmentSaving}>{segmentSaving ? "Saving…" : "Save segment"}</button>
            </div>
          </form>
        </div>
      ) : null}

      {newContactDialogOpen ? (
        <div
          className="dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => { if (event.target === event.currentTarget) setNewContactDialogOpen(false); }}
        >
          <form className="dialog" role="dialog" aria-modal="true" aria-labelledby="tzv2-cust-new-title" onSubmit={saveNewContact}>
            <div className="tzv2-cust-dialog-head">
              <span className="dialog-title" id="tzv2-cust-new-title">New contact</span>
              <button type="button" className="btn btn-ghost btn-icon" aria-label="Close dialog" onClick={() => setNewContactDialogOpen(false)}>
                <CloseOutlined fontSize="small" />
              </button>
            </div>
            <div className="dialog-body tzv2-cust-dialog-body">
              <div className="field">
                <label>Name</label>
                <input className="input" value={newContactName} onChange={(event) => setNewContactName(event.target.value)} maxLength={200} autoFocus />
              </div>
              <div className="field">
                <label>Phone</label>
                <input className="input" value={newContactPhone} onChange={(event) => setNewContactPhone(event.target.value)} maxLength={80} />
              </div>
              <div className="field">
                <label>Email</label>
                <input className="input" value={newContactEmail} onChange={(event) => setNewContactEmail(event.target.value)} maxLength={320} />
              </div>
              {newContactError ? <p className="tzv2-cust-form-error">{newContactError}</p> : null}
            </div>
            <div className="dialog-actions">
              <button type="button" className="btn btn-secondary" disabled={newContactSaving} onClick={() => setNewContactDialogOpen(false)}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={newContactSaving}>{newContactSaving ? "Creating…" : "Create contact"}</button>
            </div>
          </form>
        </div>
      ) : null}

      {segmentToDelete ? (
        <div
          className="dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => { if (event.target === event.currentTarget && !segmentDeleting) setSegmentToDelete(null); }}
        >
          <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="tzv2-cust-delete-title">
            <span className="dialog-title" id="tzv2-cust-delete-title">Delete segment</span>
            <div className="dialog-body">
              <p>Delete the "{segmentToDelete.name}" segment? Contacts themselves are not affected.</p>
            </div>
            <div className="dialog-actions">
              <button type="button" className="btn btn-secondary" disabled={segmentDeleting} onClick={() => setSegmentToDelete(null)}>Cancel</button>
              <button type="button" className="btn btn-primary" disabled={segmentDeleting} onClick={confirmDeleteSegment}>{segmentDeleting ? "Deleting…" : "Delete"}</button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
