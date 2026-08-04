import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AddOutlined,
  CallMadeOutlined,
  CallReceivedOutlined,
  CloseOutlined,
  DeleteOutlineOutlined,
  EditOutlined,
  LocalPhoneOutlined,
  LockOutlined,
  RefreshOutlined,
} from "@mui/icons-material";

import {
  createCallRequest,
  deleteCallRequest,
  getCallsRequest,
  getCustomersRequest,
  updateCallRequest,
} from "../../api/client";
import {
  AppButton,
  AppCard,
  AppTable,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  PageHeader,
  SearchBar,
  StatusBadge,
} from "../../components/common";
import { useAuth } from "../../contexts/AuthContext";
import "./CallsPage.css";

const PAGE_SIZE = 20;

const DIRECTION_OPTIONS = [
  { value: "outbound", label: "Outbound" },
  { value: "inbound", label: "Inbound" },
];

const OUTCOME_OPTIONS = [
  { value: "answered", label: "Answered" },
  { value: "no_answer", label: "No answer" },
  { value: "busy", label: "Busy" },
  { value: "voicemail", label: "Voicemail" },
  { value: "wrong_number", label: "Wrong number" },
];

const OUTCOME_TONE = {
  answered: "success",
  no_answer: "warning",
  busy: "warning",
  voicemail: "info",
  wrong_number: "danger",
};

const DIRECTION_LABEL = Object.fromEntries(
  DIRECTION_OPTIONS.map((option) => [option.value, option.label]),
);
const OUTCOME_LABEL = Object.fromEntries(
  OUTCOME_OPTIONS.map((option) => [option.value, option.label]),
);

const EMPTY_FORM = {
  customer_id: "",
  phone_number: "",
  direction: "outbound",
  outcome: "answered",
  duration_minutes: "",
  notes: "",
  called_at: "",
};

function toDatetimeLocalValue(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function nowDatetimeLocalValue() {
  return toDatetimeLocalValue(new Date().toISOString());
}

function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || seconds === "") return "—";
  const total = Number(seconds);
  if (Number.isNaN(total)) return "—";
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  if (minutes === 0) return `${rest}s`;
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

function callToForm(call) {
  return {
    customer_id: call?.customer_id ? String(call.customer_id) : "",
    phone_number: call?.phone_number || "",
    direction: call?.direction || "outbound",
    outcome: call?.outcome || "answered",
    duration_minutes:
      call?.duration_seconds !== null && call?.duration_seconds !== undefined
        ? String(Math.round(call.duration_seconds / 60))
        : "",
    notes: call?.notes || "",
    called_at: toDatetimeLocalValue(call?.called_at),
  };
}

export default function CallsPage() {
  const { hasPermission } = useAuth();
  const canView = hasPermission("calls.view");
  const canManage = hasPermission("calls.manage");

  const [directionFilter, setDirectionFilter] = useState("all");
  const [outcomeFilter, setOutcomeFilter] = useState("all");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [customers, setCustomers] = useState([]);
  const [customerSearch, setCustomerSearch] = useState("");

  const [editorOpen, setEditorOpen] = useState(false);
  const [isEdit, setIsEdit] = useState(false);
  const [activeCallId, setActiveCallId] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [baseUpdatedAt, setBaseUpdatedAt] = useState(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const requestSeq = useRef(0);

  const load = useCallback(async () => {
    if (!canView) {
      setLoading(false);
      return;
    }
    const seq = ++requestSeq.current;
    setLoading(true);
    setError("");
    try {
      const result = await getCallsRequest({
        direction: directionFilter,
        outcome: outcomeFilter,
        search,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      });
      if (seq !== requestSeq.current) return;
      setRows(result?.items || []);
      setTotal(result?.total || 0);
    } catch (requestError) {
      if (seq !== requestSeq.current) return;
      setError(requestError.message || "Calls could not be loaded.");
      setRows([]);
      setTotal(0);
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, [canView, directionFilter, outcomeFilter, search, page]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const handle = setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(1);
    }, 350);
    return () => clearTimeout(handle);
  }, [searchInput]);

  useEffect(() => {
    setPage(1);
  }, [directionFilter, outcomeFilter]);

  function loadCustomersIfNeeded() {
    if (customers.length || !canManage) return;
    getCustomersRequest({ limit: 100 })
      .then((result) => setCustomers(Array.isArray(result?.items) ? result.items : []))
      .catch(() => setCustomers([]));
  }

  function openCreate() {
    setForm({ ...EMPTY_FORM, called_at: nowDatetimeLocalValue() });
    setBaseUpdatedAt(null);
    setActiveCallId(null);
    setIsEdit(false);
    setFormError("");
    setCustomerSearch("");
    loadCustomersIfNeeded();
    setEditorOpen(true);
  }

  function openEdit(call) {
    setForm(callToForm(call));
    setBaseUpdatedAt(call?.updated_at ?? null);
    setActiveCallId(call.id);
    setIsEdit(true);
    setFormError("");
    setCustomerSearch("");
    loadCustomersIfNeeded();
    setEditorOpen(true);
  }

  function closeEditor() {
    if (saving) return;
    setEditorOpen(false);
  }

  function updateForm(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function handleSave() {
    if (!form.called_at) {
      setFormError("The call time is required.");
      return;
    }
    if (!form.customer_id && !form.phone_number.trim()) {
      setFormError("Pick a customer or enter a phone number.");
      return;
    }

    const minutes = form.duration_minutes === "" ? null : Number(form.duration_minutes);
    if (minutes !== null && (Number.isNaN(minutes) || minutes < 0)) {
      setFormError("Duration must be a positive number of minutes.");
      return;
    }

    const payload = {
      customer_id: form.customer_id ? Number(form.customer_id) : null,
      phone_number: form.phone_number.trim() || null,
      direction: form.direction,
      outcome: form.outcome,
      duration_seconds: minutes === null ? null : Math.round(minutes * 60),
      notes: form.notes.trim() || null,
      called_at: new Date(form.called_at).toISOString(),
    };

    setSaving(true);
    setFormError("");
    try {
      if (isEdit) {
        await updateCallRequest(activeCallId, {
          ...payload,
          expected_updated_at: baseUpdatedAt,
        });
      } else {
        await createCallRequest(payload);
      }
      setEditorOpen(false);
      await load();
    } catch (err) {
      if (err?.status === 409) {
        const detail = err?.data?.detail;
        const current = detail && typeof detail === "object" ? detail.current : null;
        if (current) {
          setForm(callToForm(current));
          setBaseUpdatedAt(current?.updated_at ?? null);
        }
        setFormError(
          (detail && typeof detail === "object" ? detail.message : null) ||
            "This call was changed elsewhere. It has been reloaded — review and save again.",
        );
      } else {
        setFormError(err.message || "The call could not be saved.");
      }
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteCallRequest(deleteTarget.id);
      setDeleteTarget(null);
      await load();
    } catch (err) {
      setError(err.message || "The call could not be deleted.");
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  }

  const filteredCustomerOptions = useMemo(() => {
    const needle = customerSearch.trim().toLowerCase();
    if (!needle) return customers;
    return customers.filter((customer) => {
      const name = (customer.display_name || customer.internal_name || "").toLowerCase();
      return name.includes(needle);
    });
  }, [customers, customerSearch]);

  function buildColumns() {
    const cols = [
      {
        key: "called_at",
        label: "When",
        render: (value) => formatDateTime(value),
      },
      {
        key: "customer_name",
        label: "Customer / Number",
        render: (_value, row) => (
          <div className="calls-customer-cell">
            <strong>{row.customer_name || row.phone_number || "Unknown"}</strong>
            {row.customer_name && row.phone_number ? <span>{row.phone_number}</span> : null}
          </div>
        ),
      },
      {
        key: "direction",
        label: "Direction",
        render: (value) => (
          <span className="calls-direction">
            {value === "inbound" ? (
              <CallReceivedOutlined fontSize="small" />
            ) : (
              <CallMadeOutlined fontSize="small" />
            )}
            {DIRECTION_LABEL[value] || value}
          </span>
        ),
      },
      {
        key: "outcome",
        label: "Outcome",
        render: (value) => (
          <StatusBadge status={value} tone={OUTCOME_TONE[value]} label={OUTCOME_LABEL[value] || value} />
        ),
      },
      {
        key: "duration_seconds",
        label: "Duration",
        render: (value) => formatDuration(value),
      },
      {
        key: "logged_by_name",
        label: "Logged by",
        render: (value) => value || "—",
      },
    ];

    if (canManage) {
      cols.push({
        key: "actions",
        label: "",
        align: "right",
        render: (_value, row) => (
          <div className="calls-row-actions">
            <AppButton
              size="small"
              variant="secondary"
              icon={<EditOutlined fontSize="small" />}
              onClick={() => openEdit(row)}
            >
              Edit
            </AppButton>
            <AppButton
              size="small"
              variant="danger"
              icon={<DeleteOutlineOutlined fontSize="small" />}
              onClick={() => setDeleteTarget(row)}
            >
              Delete
            </AppButton>
          </div>
        ),
      });
    }

    return cols;
  }

  if (!canView) {
    return (
      <section className="calls-page">
        <PageHeader
          eyebrow="CALLS"
          title="Calls"
          description="A log of phone calls with customers, linked to their profiles."
        />
        <AppCard padding="large">
          <EmptyState
            icon={<LockOutlined />}
            title="You don't have access to Calls"
            description="Ask a company administrator to grant you the “View Calls” permission."
          />
        </AppCard>
      </section>
    );
  }

  const columns = buildColumns();

  return (
    <section className="calls-page">
      <PageHeader
        eyebrow="CALLS"
        title="Calls"
        description="Log and track phone calls with customers. In-platform dialing requires a telephony provider and is a separate future step."
        actions={
          <div className="calls-row-actions">
            <AppButton
              variant="secondary"
              icon={<RefreshOutlined fontSize="small" />}
              onClick={load}
            >
              Refresh
            </AppButton>
            {canManage ? (
              <AppButton
                variant="primary"
                icon={<AddOutlined fontSize="small" />}
                onClick={openCreate}
              >
                Log call
              </AppButton>
            ) : null}
          </div>
        }
      />

      {!canManage ? (
        <p className="calls-inline-note">
          <LockOutlined fontSize="small" /> You have read-only access. Ask an
          administrator for the &quot;Manage Calls&quot; permission to log,
          edit or delete calls.
        </p>
      ) : null}

      <AppCard padding="medium">
        <div className="calls-toolbar">
          <SearchBar
            value={searchInput}
            placeholder="Search by customer, number or notes..."
            ariaLabel="Search calls"
            onChange={setSearchInput}
            onClear={() => setSearchInput("")}
          />

          <label className="calls-filter">
            <span>Direction</span>
            <select value={directionFilter} onChange={(event) => setDirectionFilter(event.target.value)}>
              <option value="all">All directions</option>
              {DIRECTION_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <label className="calls-filter">
            <span>Outcome</span>
            <select value={outcomeFilter} onChange={(event) => setOutcomeFilter(event.target.value)}>
              <option value="all">All outcomes</option>
              {OUTCOME_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <StatusBadge
            status="info"
            tone="info"
            showDot={false}
            label={`${total} call${total === 1 ? "" : "s"}`}
          />
        </div>

        {error ? (
          <ErrorState
            title="Calls could not load"
            description={error}
            action={
              <AppButton
                variant="primary"
                icon={<RefreshOutlined fontSize="small" />}
                onClick={load}
              >
                Try again
              </AppButton>
            }
          />
        ) : (
          <AppTable
            columns={columns}
            rows={rows}
            loading={loading}
            rowKey="id"
            page={page}
            pageSize={PAGE_SIZE}
            totalRows={total}
            onPageChange={setPage}
            emptyTitle="No calls logged"
            emptyDescription={
              search || directionFilter !== "all" || outcomeFilter !== "all"
                ? "No calls match your filters. Try widening your search."
                : canManage
                  ? "Log your first call to start building customer call history."
                  : "Calls logged by the team will appear here."
            }
            renderMobileCard={(row) => (
              <div className="tz-mobile-record-fields">
                <div className="calls-customer-cell">
                  <strong>{row.customer_name || row.phone_number || "Unknown"}</strong>
                  <span>{formatDateTime(row.called_at)}</span>
                </div>
                <div className="calls-mobile-meta">
                  <StatusBadge status={row.outcome} tone={OUTCOME_TONE[row.outcome]} label={OUTCOME_LABEL[row.outcome] || row.outcome} />
                  <span>{DIRECTION_LABEL[row.direction] || row.direction} · {formatDuration(row.duration_seconds)}</span>
                </div>
                {canManage ? (
                  <div className="calls-row-actions">
                    <AppButton size="small" variant="secondary" onClick={() => openEdit(row)}>
                      Edit
                    </AppButton>
                    <AppButton size="small" variant="danger" onClick={() => setDeleteTarget(row)}>
                      Delete
                    </AppButton>
                  </div>
                ) : null}
              </div>
            )}
          />
        )}
      </AppCard>

      {editorOpen ? (
        <div
          className="tz-dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeEditor();
          }}
        >
          <section
            className="tz-dialog calls-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="calls-editor-title"
          >
            <header className="tz-dialog-header">
              <h3 id="calls-editor-title">
                <LocalPhoneOutlined fontSize="small" /> {isEdit ? "Edit call" : "Log call"}
              </h3>
              <button
                type="button"
                className="tz-dialog-close"
                aria-label="Close editor"
                onClick={closeEditor}
              >
                <CloseOutlined fontSize="small" />
              </button>
            </header>

            <div className="tz-dialog-body">
              <div className="calls-form">
                <div className="calls-grid-2">
                  <label className="calls-field">
                    <span>Customer (optional)</span>
                    <select
                      value={form.customer_id}
                      disabled={saving}
                      onChange={(event) => updateForm("customer_id", event.target.value)}
                    >
                      <option value="">None — use phone number</option>
                      {filteredCustomerOptions.map((customer) => (
                        <option key={customer.id} value={customer.id}>
                          {customer.display_name || customer.internal_name || `Customer ${customer.id}`}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="calls-field">
                    <span>Phone number</span>
                    <input
                      type="tel"
                      value={form.phone_number}
                      disabled={saving}
                      placeholder="+961 ..."
                      onChange={(event) => updateForm("phone_number", event.target.value)}
                    />
                  </label>
                </div>

                {customers.length > 8 ? (
                  <label className="calls-field">
                    <span>Filter customers</span>
                    <input
                      type="text"
                      value={customerSearch}
                      disabled={saving}
                      placeholder="Type to narrow the customer list above"
                      onChange={(event) => setCustomerSearch(event.target.value)}
                    />
                  </label>
                ) : null}

                <div className="calls-grid-3">
                  <label className="calls-field">
                    <span>Direction</span>
                    <select
                      value={form.direction}
                      disabled={saving}
                      onChange={(event) => updateForm("direction", event.target.value)}
                    >
                      {DIRECTION_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="calls-field">
                    <span>Outcome</span>
                    <select
                      value={form.outcome}
                      disabled={saving}
                      onChange={(event) => updateForm("outcome", event.target.value)}
                    >
                      {OUTCOME_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="calls-field">
                    <span>Duration (minutes)</span>
                    <input
                      type="number"
                      min="0"
                      step="1"
                      value={form.duration_minutes}
                      disabled={saving}
                      onChange={(event) => updateForm("duration_minutes", event.target.value)}
                    />
                  </label>
                </div>

                <label className="calls-field">
                  <span>Call time</span>
                  <input
                    type="datetime-local"
                    value={form.called_at}
                    disabled={saving}
                    onChange={(event) => updateForm("called_at", event.target.value)}
                  />
                </label>

                <label className="calls-field">
                  <span>Notes</span>
                  <textarea
                    value={form.notes}
                    disabled={saving}
                    placeholder="What was discussed, agreed or promised"
                    onChange={(event) => updateForm("notes", event.target.value)}
                  />
                </label>

                {formError ? <p className="calls-form-error">{formError}</p> : null}
              </div>
            </div>

            <footer className="tz-dialog-actions">
              <AppButton variant="secondary" disabled={saving} onClick={closeEditor}>
                Cancel
              </AppButton>
              <AppButton variant="primary" loading={saving} onClick={handleSave}>
                {isEdit ? "Save changes" : "Log call"}
              </AppButton>
            </footer>
          </section>
        </div>
      ) : null}

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="Delete call"
        confirmLabel="Delete"
        cancelLabel="Cancel"
        confirmVariant="danger"
        loading={deleting}
        onConfirm={handleDelete}
        onCancel={() => (deleting ? null : setDeleteTarget(null))}
        message={
          deleteTarget ? (
            <p>
              Delete this call record
              {deleteTarget.customer_name ? (
                <>
                  {" "}with <strong>{deleteTarget.customer_name}</strong>
                </>
              ) : null}
              ? This cannot be undone.
            </p>
          ) : null
        }
      />
    </section>
  );
}
