import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CloseOutlined,
  ForumOutlined,
  LockOutlined,
  PersonOutlineOutlined,
  RefreshOutlined,
} from "@mui/icons-material";

import {
  getCustomerRequest,
  getCustomersRequest,
  updateCustomerRequest,
} from "../../api/client";
import {
  AppButton,
  AppCard,
  AppTable,
  ErrorState,
  PageHeader,
  SearchBar,
  StatusBadge,
} from "../../components/common";
import { useAuth } from "../../contexts/AuthContext";
import "./CustomersPage.css";

const PAGE_SIZE = 25;

// Editable fields must match backend/api/schemas/customers.py exactly.
const EDITABLE_FIELDS = [
  { key: "display_name", label: "Display name" },
  { key: "internal_name", label: "Internal alias" },
  { key: "phone", label: "Phone" },
  { key: "email", label: "Email" },
  { key: "language", label: "Language" },
  { key: "country", label: "Country" },
  { key: "timezone", label: "Timezone" },
  { key: "notes", label: "Notes", multiline: true },
];

function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function customerName(customer) {
  return (
    customer?.display_name?.trim() ||
    customer?.internal_name?.trim() ||
    "Unnamed customer"
  );
}

function toFormValues(customer) {
  const values = {};
  EDITABLE_FIELDS.forEach(({ key }) => {
    values[key] = customer?.[key] ?? "";
  });
  return values;
}

function CustomerDetailModal({ customerId, canManage, onClose, onSaved }) {
  const [customer, setCustomer] = useState(null);
  const [values, setValues] = useState({});
  const [baseUpdatedAt, setBaseUpdatedAt] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState(null); // { tone, text }

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    setMessage(null);
    try {
      const result = await getCustomerRequest(customerId);
      setCustomer(result);
      setValues(toFormValues(result));
      setBaseUpdatedAt(result?.updated_at ?? null);
    } catch (error) {
      setLoadError(error.message || "This customer could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [customerId]);

  useEffect(() => {
    load();
  }, [load]);

  function updateField(key, value) {
    setValues((current) => ({ ...current, [key]: value }));
  }

  const isDirty = useMemo(() => {
    if (!customer) return false;
    return EDITABLE_FIELDS.some(
      ({ key }) => (values[key] ?? "") !== (customer[key] ?? ""),
    );
  }, [values, customer]);

  async function save() {
    setSaving(true);
    setMessage(null);
    try {
      // Only send changed fields, plus the optimistic-concurrency token the
      // record carried when we loaded it (same idea as the conversation
      // control version). If it is stale, the backend answers 409.
      const payload = { expected_updated_at: baseUpdatedAt };
      EDITABLE_FIELDS.forEach(({ key }) => {
        if ((values[key] ?? "") !== (customer[key] ?? "")) {
          payload[key] = values[key] ?? "";
        }
      });
      const updated = await updateCustomerRequest(customerId, payload);
      setCustomer(updated);
      setValues(toFormValues(updated));
      setBaseUpdatedAt(updated?.updated_at ?? null);
      setMessage({ tone: "is-success", text: "Customer saved." });
      onSaved?.(updated);
    } catch (error) {
      if (error?.status === 409) {
        // Someone else changed this record. Refresh the form with the server's
        // current copy so the user can re-apply their edit knowingly, rather
        // than silently overwriting the other change.
        const current = error?.data?.detail?.current;
        if (current) {
          setCustomer(current);
          setValues(toFormValues(current));
          setBaseUpdatedAt(current?.updated_at ?? null);
        }
        setMessage({
          tone: "is-conflict",
          text:
            error?.data?.detail?.message ||
            "This customer was changed elsewhere. It has been reloaded — review and save again.",
        });
      } else {
        setMessage({
          tone: "is-error",
          text: error.message || "The customer could not be saved.",
        });
      }
    } finally {
      setSaving(false);
    }
  }

  const identities = customer?.identities || [];

  return (
    <div
      className="tz-dialog-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose?.();
      }}
    >
      <section
        className="tz-dialog customers-detail"
        role="dialog"
        aria-modal="true"
        aria-labelledby="customer-detail-title"
      >
        <header className="tz-dialog-header">
          <h3 id="customer-detail-title">
            {customer ? customerName(customer) : "Customer"}
          </h3>
          <button
            type="button"
            className="tz-dialog-close"
            aria-label="Close"
            onClick={onClose}
          >
            <CloseOutlined fontSize="small" />
          </button>
        </header>

        <div className="tz-dialog-body">
          {loading ? (
            <p>Loading customer...</p>
          ) : loadError ? (
            <ErrorState
              title="Customer could not load"
              description={loadError}
              action={
                <AppButton variant="primary" onClick={load}>
                  Try again
                </AppButton>
              }
            />
          ) : (
            <>
              {!canManage ? (
                <p className="customers-readonly-note">
                  <LockOutlined fontSize="small" />
                  You have read-only access. Ask an administrator for the
                  &quot;Manage Users&quot; permission to edit customer details.
                </p>
              ) : null}

              <div className="customer-detail-section">
                <h4>Activity</h4>
                <div className="customer-tag-row">
                  <span className="customer-chip">
                    <ForumOutlined fontSize="inherit" />
                    {customer.conversation_count || 0} conversation
                    {(customer.conversation_count || 0) === 1 ? "" : "s"}
                  </span>
                  <span className="customer-chip">
                    First seen {formatDateTime(customer.first_seen_at)}
                  </span>
                  <span className="customer-chip">
                    Last seen {formatDateTime(customer.last_seen_at)}
                  </span>
                </div>
              </div>

              <div className="customer-detail-section">
                <h4>Channel identities</h4>
                {identities.length ? (
                  <div className="customer-identity-list">
                    {identities.map((identity) => (
                      <div
                        className="customer-identity-item"
                        key={`${identity.channel}:${identity.external_user_id}`}
                      >
                        <div className="identity-main">
                          <strong>
                            {identity.display_name ||
                              identity.username ||
                              "Unknown handle"}
                          </strong>
                          <span>{identity.external_user_id}</span>
                        </div>
                        <StatusBadge
                          status={identity.channel}
                          label={identity.channel}
                          tone="info"
                          showDot={false}
                        />
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="customer-contact-cell">
                    No channel identities linked yet.
                  </p>
                )}
              </div>

              <div className="customer-detail-section">
                <h4>Details</h4>
                <div className="customer-form-grid">
                  {EDITABLE_FIELDS.map(({ key, label, multiline }) => (
                    <div
                      className={`customer-field${multiline ? " customer-field-full" : ""}`}
                      key={key}
                    >
                      <label htmlFor={`customer-field-${key}`}>{label}</label>
                      {multiline ? (
                        <textarea
                          id={`customer-field-${key}`}
                          value={values[key] ?? ""}
                          disabled={!canManage || saving}
                          onChange={(event) =>
                            updateField(key, event.target.value)
                          }
                          placeholder={label}
                        />
                      ) : (
                        <input
                          id={`customer-field-${key}`}
                          type="text"
                          value={values[key] ?? ""}
                          disabled={!canManage || saving}
                          onChange={(event) =>
                            updateField(key, event.target.value)
                          }
                          placeholder={label}
                        />
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>

        <footer className="tz-dialog-actions">
          {message ? (
            <span className={`customers-form-message ${message.tone}`}>
              {message.text}
            </span>
          ) : (
            <span className="customers-form-message" />
          )}
          <AppButton variant="secondary" onClick={onClose} disabled={saving}>
            Close
          </AppButton>
          {canManage && !loadError ? (
            <AppButton
              variant="primary"
              onClick={save}
              loading={saving}
              disabled={loading || !isDirty}
            >
              Save changes
            </AppButton>
          ) : null}
        </footer>
      </section>
    </div>
  );
}

export default function CustomersPage() {
  const { hasPermission } = useAuth();
  const canManage = hasPermission("users.manage");

  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [activeCustomerId, setActiveCustomerId] = useState(null);

  const requestSeq = useRef(0);

  const load = useCallback(async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    setError("");
    try {
      const result = await getCustomersRequest({
        search,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      });
      // Ignore responses from superseded requests (search races).
      if (seq !== requestSeq.current) return;
      setRows(result?.items || []);
      setTotal(result?.total || 0);
    } catch (requestError) {
      if (seq !== requestSeq.current) return;
      setError(requestError.message || "Customers could not be loaded.");
      setRows([]);
      setTotal(0);
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, [search, page]);

  useEffect(() => {
    load();
  }, [load]);

  // Debounce the search box and reset to the first page on a new query.
  useEffect(() => {
    const handle = setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(1);
    }, 350);
    return () => clearTimeout(handle);
  }, [searchInput]);

  const columns = useMemo(
    () => [
      {
        key: "name",
        label: "Customer",
        render: (_value, row) => (
          <div className="customer-name-cell">
            <strong>{customerName(row)}</strong>
            <span>
              {row.identity_count || 0} channel
              {(row.identity_count || 0) === 1 ? "" : "s"}
            </span>
          </div>
        ),
      },
      {
        key: "contact",
        label: "Contact",
        render: (_value, row) => (
          <div className="customer-contact-cell">
            <span>{row.phone || "No phone"}</span>
            <span>{row.email || "No email"}</span>
          </div>
        ),
      },
      {
        key: "country",
        label: "Locale",
        render: (_value, row) => (
          <div className="customer-contact-cell">
            <span>{row.country || "—"}</span>
            <span>{row.language || row.timezone || ""}</span>
          </div>
        ),
      },
      {
        key: "conversation_count",
        label: "Chats",
        align: "center",
        render: (value) => value || 0,
      },
      {
        key: "last_seen_at",
        label: "Last activity",
        render: (value) => formatDateTime(value),
      },
      {
        key: "actions",
        label: "",
        align: "right",
        render: (_value, row) => (
          <AppButton
            variant="secondary"
            size="small"
            icon={<PersonOutlineOutlined fontSize="small" />}
            onClick={() => setActiveCustomerId(row.id)}
          >
            {canManage ? "View / Edit" : "View"}
          </AppButton>
        ),
      },
    ],
    [canManage],
  );

  if (error) {
    return (
      <div className="customers-page">
        <PageHeader
          eyebrow="CUSTOMER DATABASE"
          title="Customers"
          description="Unified customer records across every connected channel."
        />
        <AppCard padding="medium">
          <ErrorState
            title="Customers could not load"
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
        </AppCard>
      </div>
    );
  }

  return (
    <div className="customers-page">
      <PageHeader
        eyebrow="CUSTOMER DATABASE"
        title="Customers"
        description="Unified customer records, channel identities and contact details across Messenger, WhatsApp and every connected source."
        actions={
          <AppButton
            variant="secondary"
            icon={<RefreshOutlined fontSize="small" />}
            onClick={load}
          >
            Refresh
          </AppButton>
        }
      />

      <AppCard padding="medium">
        <div className="customers-toolbar">
          <SearchBar
            value={searchInput}
            placeholder="Search by name, phone, email or handle..."
            ariaLabel="Search customers"
            onChange={setSearchInput}
            onClear={() => setSearchInput("")}
          />
          <StatusBadge
            status="info"
            tone="info"
            showDot={false}
            label={`${total} customer${total === 1 ? "" : "s"}`}
          />
        </div>

        <AppTable
          columns={columns}
          rows={rows}
          loading={loading}
          rowKey="id"
          page={page}
          pageSize={PAGE_SIZE}
          totalRows={total}
          onPageChange={setPage}
          emptyTitle="No customers found"
          emptyDescription={
            search
              ? "No customers match your search. Try a different name, phone or handle."
              : "Customers appear here automatically as they message your connected channels."
          }
          renderMobileCard={(row) => (
            <button
              type="button"
              className="tz-mobile-record-fields"
              style={{ width: "100%", textAlign: "left", background: "none", border: "none", cursor: "pointer" }}
              onClick={() => setActiveCustomerId(row.id)}
            >
              <div className="customer-name-cell">
                <strong>{customerName(row)}</strong>
                <span>{row.phone || row.email || `${row.identity_count || 0} channels`}</span>
              </div>
              <div className="customer-contact-cell">
                <span>{row.conversation_count || 0} chats</span>
                <span>Last seen {formatDateTime(row.last_seen_at)}</span>
              </div>
            </button>
          )}
        />
      </AppCard>

      {activeCustomerId != null ? (
        <CustomerDetailModal
          customerId={activeCustomerId}
          canManage={canManage}
          onClose={() => setActiveCustomerId(null)}
          onSaved={() => load()}
        />
      ) : null}
    </div>
  );
}
