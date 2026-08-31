import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CloseOutlined,
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
  LoadingState,
  PageHeader,
  SearchBar,
} from "../../components/common";
import { formatPlatformDateTime } from "../../utils/dateTime";
import "./CustomersPage.css";

const PAGE_SIZE = 20;

const EDITABLE_FIELDS = [
  ["display_name", "Display name", "text", "Name shown to the team"],
  ["internal_name", "Internal name", "text", "Private name used inside the company"],
  ["phone", "Phone", "tel", "+961 ..."],
  ["email", "Email", "email", "name@example.com"],
  ["language", "Language", "text", "en / ar / tr"],
  ["country", "Country", "text", "Lebanon"],
  ["timezone", "Timezone", "text", "Asia/Beirut"],
];

function emptyForm() {
  return {
    display_name: "",
    internal_name: "",
    phone: "",
    email: "",
    language: "",
    country: "",
    timezone: "",
    notes: "",
  };
}

function formFromCustomer(customer) {
  const form = emptyForm();

  Object.keys(form).forEach((key) => {
    form[key] = customer?.[key] ?? "";
  });

  return form;
}

function customerLabel(customer) {
  return (
    customer?.display_name ||
    customer?.internal_name ||
    `Customer #${customer?.id ?? "—"}`
  );
}

function humanize(value) {
  return String(value ?? "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export default function CustomersPage() {
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [form, setForm] = useState(emptyForm);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState("");

  const loadCustomers = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const result = await getCustomersRequest({
        search,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      });

      setRows(Array.isArray(result?.items) ? result.items : []);
      setTotal(Number(result?.total || 0));
    } catch (requestError) {
      setError(
        requestError.message ||
        "Customers could not be loaded.",
      );
      setRows([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [search, page]);

  useEffect(() => {
    loadCustomers();
  }, [loadCustomers]);

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(1);
    }, 300);

    return () => window.clearTimeout(timeout);
  }, [searchInput]);

  const loadCustomer = useCallback(async (customerId) => {
    setDetailLoading(true);
    setDetailError("");
    setSaveStatus("");

    try {
      const result = await getCustomerRequest(customerId);

      setDetail(result);
      setForm(formFromCustomer(result));
    } catch (requestError) {
      setDetail(null);
      setDetailError(
        requestError.message ||
        "Customer profile could not be loaded.",
      );
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selectedId === null) {
      setDetail(null);
      setDetailError("");
      setForm(emptyForm());
      return;
    }

    loadCustomer(selectedId);
  }, [selectedId, loadCustomer]);

  async function handleSave(event) {
    event.preventDefault();

    if (!detail?.id) {
      return;
    }

    setSaving(true);
    setSaveStatus("");
    setDetailError("");

    try {
      const result = await updateCustomerRequest(detail.id, {
        display_name: form.display_name.trim() || null,
        internal_name: form.internal_name.trim() || null,
        phone: form.phone.trim() || null,
        email: form.email.trim() || null,
        language: form.language.trim() || null,
        country: form.country.trim() || null,
        timezone: form.timezone.trim() || null,
        notes: form.notes.trim() || null,
      });

      setDetail(result);
      setForm(formFromCustomer(result));
      setSaveStatus("Customer saved.");
      setRows((current) =>
        current.map((row) =>
          row.id === result.id ? { ...row, ...result } : row,
        ),
      );
    } catch (requestError) {
      setDetailError(
        requestError.message ||
        "Customer could not be saved.",
      );
    } finally {
      setSaving(false);
    }
  }

  const columns = useMemo(
    () => [
      {
        key: "display_name",
        label: "Customer",
        render: (value, row) => (
          <button
            type="button"
            className="customer-name-button"
            onClick={() => setSelectedId(row.id)}
          >
            <strong>{customerLabel(row)}</strong>
            {row.internal_name && row.display_name ? (
              <span>{row.internal_name}</span>
            ) : null}
          </button>
        ),
      },
      {
        key: "contact",
        label: "Contact",
        valueGetter: (row) => row.phone || row.email || "",
        render: (value, row) => (
          <div className="customer-contact-cell">
            <span>{row.phone || "—"}</span>
            <span>{row.email || "—"}</span>
          </div>
        ),
      },
      {
        key: "country",
        label: "Country / Language",
        render: (value, row) =>
          [row.country, row.language].filter(Boolean).join(" · ") || "—",
      },
      {
        key: "identity_count",
        label: "Channels",
        align: "center",
        render: (value) => Number(value || 0),
      },
      {
        key: "conversation_count",
        label: "Conversations",
        align: "center",
        render: (value) => Number(value || 0),
      },
      {
        key: "last_seen_at",
        label: "Last seen",
        render: (value) => formatPlatformDateTime(value),
      },
      {
        key: "actions",
        label: "",
        align: "right",
        render: (value, row) => (
          <AppButton
            variant="ghost"
            size="small"
            onClick={() => setSelectedId(row.id)}
          >
            Edit
          </AppButton>
        ),
      },
    ],
    [],
  );

  return (
    <div className="customers-page">
      <PageHeader
        eyebrow="CUSTOMER DATABASE"
        title="Customers"
        description="Every customer profile collected from the connected messaging channels, with the identities and conversations linked to it."
        actions={
          <AppButton
            variant="secondary"
            icon={<RefreshOutlined fontSize="small" />}
            onClick={loadCustomers}
          >
            Refresh
          </AppButton>
        }
      />

      <div
        className={`customers-layout ${selectedId !== null ? "has-detail" : ""}`}
      >
        <AppCard padding="medium" className="customers-list-card">
          <div className="customers-list-toolbar">
            <SearchBar
              value={searchInput}
              placeholder="Search name, phone, email or channel id..."
              ariaLabel="Search customers"
              onChange={setSearchInput}
            />

            <span className="customers-total">
              {total} {total === 1 ? "customer" : "customers"}
            </span>
          </div>

          {error ? (
            <ErrorState
              title="Customers could not load"
              description={error}
              action={
                <AppButton variant="primary" onClick={loadCustomers}>
                  Try again
                </AppButton>
              }
            />
          ) : (
            <AppTable
              columns={columns}
              rows={rows}
              loading={loading}
              emptyTitle="No customers found"
              emptyDescription={
                search
                  ? "No customer matches this search."
                  : "Customer profiles are created automatically when messages arrive from a connected channel."
              }
              page={page}
              pageSize={PAGE_SIZE}
              totalRows={total}
              onPageChange={setPage}
              renderMobileCard={(row) => (
                <button
                  type="button"
                  className="customer-mobile-card"
                  onClick={() => setSelectedId(row.id)}
                >
                  <strong>{customerLabel(row)}</strong>
                  <span>{row.phone || row.email || "No contact details"}</span>
                  <small>
                    {Number(row.identity_count || 0)} channels ·{" "}
                    {Number(row.conversation_count || 0)} conversations
                  </small>
                </button>
              )}
            />
          )}
        </AppCard>

        {selectedId !== null ? (
          <AppCard padding="medium" className="customer-detail-card">
            <header className="customer-detail-head">
              <div>
                <span>CUSTOMER PROFILE</span>
                <h3>{detail ? customerLabel(detail) : "Loading..."}</h3>
              </div>

              <button
                type="button"
                className="customer-detail-close"
                aria-label="Close customer profile"
                onClick={() => setSelectedId(null)}
              >
                <CloseOutlined fontSize="small" />
              </button>
            </header>

            {detailLoading ? (
              <LoadingState title="Loading customer profile..." />
            ) : null}

            {!detailLoading && detailError && !detail ? (
              <ErrorState
                title="Profile could not load"
                description={detailError}
                action={
                  <AppButton
                    variant="primary"
                    onClick={() => loadCustomer(selectedId)}
                  >
                    Try again
                  </AppButton>
                }
              />
            ) : null}

            {!detailLoading && detail ? (
              <>
                <div className="customer-facts">
                  <div>
                    <span>Conversations</span>
                    <strong>{Number(detail.conversation_count || 0)}</strong>
                  </div>

                  <div>
                    <span>First seen</span>
                    <strong>{formatPlatformDateTime(detail.first_seen_at)}</strong>
                  </div>

                  <div>
                    <span>Last seen</span>
                    <strong>{formatPlatformDateTime(detail.last_seen_at)}</strong>
                  </div>
                </div>

                <section className="customer-identities">
                  <h4>Channel identities</h4>

                  {(detail.identities || []).length ? (
                    <ul>
                      {detail.identities.map((identity) => (
                        <li
                          key={`${identity.channel}-${identity.external_user_id}`}
                        >
                          <strong>{humanize(identity.channel)}</strong>
                          <span>
                            {identity.display_name ||
                              identity.username ||
                              identity.external_user_id}
                          </span>
                          <small>{identity.external_user_id}</small>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="customer-identities-empty">
                      This profile has no channel identity linked yet.
                    </p>
                  )}
                </section>

                <form className="customer-edit-form" onSubmit={handleSave}>
                  <h4>Profile details</h4>

                  <div className="customer-form-grid">
                    {EDITABLE_FIELDS.map(([key, label, type, placeholder]) => (
                      <label key={key} htmlFor={`customer-${key}`}>
                        <span>{label}</span>

                        <input
                          id={`customer-${key}`}
                          type={type}
                          value={form[key]}
                          placeholder={placeholder}
                          onChange={(event) => {
                            setSaveStatus("");
                            setForm((current) => ({
                              ...current,
                              [key]: event.target.value,
                            }));
                          }}
                        />
                      </label>
                    ))}
                  </div>

                  <label htmlFor="customer-notes" className="customer-notes-field">
                    <span>Notes</span>

                    <textarea
                      id="customer-notes"
                      rows={4}
                      value={form.notes}
                      placeholder="Internal notes about this customer..."
                      onChange={(event) => {
                        setSaveStatus("");
                        setForm((current) => ({
                          ...current,
                          notes: event.target.value,
                        }));
                      }}
                    />
                  </label>

                  <footer className="customer-form-footer">
                    <span
                      className={detailError ? "is-error" : "is-success"}
                    >
                      {detailError || saveStatus}
                    </span>

                    <div>
                      <AppButton
                        variant="secondary"
                        disabled={saving}
                        onClick={() => {
                          setForm(formFromCustomer(detail));
                          setSaveStatus("");
                          setDetailError("");
                        }}
                      >
                        Reset
                      </AppButton>

                      <AppButton
                        type="submit"
                        variant="primary"
                        loading={saving}
                      >
                        Save customer
                      </AppButton>
                    </div>
                  </footer>
                </form>
              </>
            ) : null}
          </AppCard>
        ) : null}
      </div>
    </div>
  );
}
