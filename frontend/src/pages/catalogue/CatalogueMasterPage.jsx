import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AddOutlined,
  CloseOutlined,
  DeleteOutlineOutlined,
  EditOutlined,
  Inventory2Outlined,
  LockOutlined,
  RefreshOutlined,
} from "@mui/icons-material";

import {
  createCatalogueProductRequest,
  deleteCatalogueProductRequest,
  getCatalogueCategoriesRequest,
  getCatalogueProductsRequest,
  updateCatalogueProductRequest,
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
import "./CatalogueMasterPage.css";

const PAGE_SIZE = 20;

const STATUS_OPTIONS = [
  { value: "active", label: "Active" },
  { value: "out_of_stock", label: "Out of stock" },
  { value: "archived", label: "Archived" },
];

const AVAILABILITY_OPTIONS = [
  { value: "in_stock", label: "In stock" },
  { value: "out_of_stock", label: "Out of stock" },
  { value: "preorder", label: "Preorder" },
  { value: "discontinued", label: "Discontinued" },
];

const STATUS_TONE = {
  active: "success",
  out_of_stock: "warning",
  archived: "neutral",
};

const AVAILABILITY_TONE = {
  in_stock: "success",
  out_of_stock: "warning",
  preorder: "info",
  discontinued: "danger",
};

const STATUS_LABEL = Object.fromEntries(
  STATUS_OPTIONS.map((option) => [option.value, option.label]),
);
const AVAILABILITY_LABEL = Object.fromEntries(
  AVAILABILITY_OPTIONS.map((option) => [option.value, option.label]),
);

const EMPTY_FORM = {
  sku: "",
  name: "",
  description: "",
  category: "",
  brand: "",
  price: "",
  currency: "USD",
  quantity: "",
  availability_status: "in_stock",
  status: "active",
};

function formatMoney(price, currency) {
  if (price === null || price === undefined || price === "") return "—";
  const amount = Number(price);
  if (Number.isNaN(amount)) return "—";
  return `${amount.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${currency || "USD"}`;
}

function productToForm(product) {
  return {
    sku: product?.sku || "",
    name: product?.name || "",
    description: product?.description || "",
    category: product?.category || "",
    brand: product?.brand || "",
    price: product?.price ?? "",
    currency: product?.currency || "USD",
    quantity: product?.quantity ?? "",
    availability_status: product?.availability_status || "in_stock",
    status: product?.status || "active",
  };
}

export default function CatalogueMasterPage() {
  const { hasPermission } = useAuth();
  const canView = hasPermission("catalogue.view");
  const canManage = hasPermission("catalogue.manage");

  const [statusFilter, setStatusFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [categories, setCategories] = useState([]);

  const [editorOpen, setEditorOpen] = useState(false);
  const [isEdit, setIsEdit] = useState(false);
  const [activeProductId, setActiveProductId] = useState(null);
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
      const result = await getCatalogueProductsRequest({
        status: statusFilter,
        category: categoryFilter,
        search,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      });
      if (seq !== requestSeq.current) return;
      setRows(result?.items || []);
      setTotal(result?.total || 0);
    } catch (requestError) {
      if (seq !== requestSeq.current) return;
      setError(requestError.message || "Catalogue could not be loaded.");
      setRows([]);
      setTotal(0);
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, [canView, statusFilter, categoryFilter, search, page]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!canView) return;
    getCatalogueCategoriesRequest()
      .then((result) => setCategories(Array.isArray(result?.items) ? result.items : []))
      .catch(() => setCategories([]));
  }, [canView]);

  // Debounce the search box and reset to the first page on a new query.
  useEffect(() => {
    const handle = setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(1);
    }, 350);
    return () => clearTimeout(handle);
  }, [searchInput]);

  useEffect(() => {
    setPage(1);
  }, [statusFilter, categoryFilter]);

  function openCreate() {
    setForm(EMPTY_FORM);
    setBaseUpdatedAt(null);
    setActiveProductId(null);
    setIsEdit(false);
    setFormError("");
    setEditorOpen(true);
  }

  function openEdit(product) {
    setForm(productToForm(product));
    setBaseUpdatedAt(product?.updated_at ?? null);
    setActiveProductId(product.id);
    setIsEdit(true);
    setFormError("");
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
    const name = form.name.trim();
    if (!name) {
      setFormError("A product name is required.");
      return;
    }

    const payload = {
      sku: form.sku.trim() || null,
      name,
      description: form.description.trim() || null,
      category: form.category.trim() || null,
      brand: form.brand.trim() || null,
      price: form.price === "" ? null : Number(form.price),
      currency: form.currency.trim() || "USD",
      quantity: form.quantity === "" ? null : Number(form.quantity),
      availability_status: form.availability_status || null,
      status: form.status,
    };

    setSaving(true);
    setFormError("");
    try {
      if (isEdit) {
        await updateCatalogueProductRequest(activeProductId, {
          ...payload,
          expected_updated_at: baseUpdatedAt,
        });
      } else {
        await createCatalogueProductRequest(payload);
      }
      setEditorOpen(false);
      await load();
    } catch (err) {
      if (err?.status === 409) {
        const current = err?.data?.detail?.current;
        if (current) {
          setForm(productToForm(current));
          setBaseUpdatedAt(current?.updated_at ?? null);
        }
        setFormError(
          err?.data?.detail?.message ||
            "This product was changed elsewhere. It has been reloaded — review and save again.",
        );
      } else {
        setFormError(err.message || "The product could not be saved.");
      }
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteCatalogueProductRequest(deleteTarget.id);
      setDeleteTarget(null);
      await load();
    } catch (err) {
      setError(err.message || "The product could not be deleted.");
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  }

  const columns = useMemo(() => {
    const cols = [
      {
        key: "name",
        label: "Product",
        render: (_value, row) => (
          <div className="catalogue-title-cell">
            <strong>{row.name}</strong>
            {row.sku ? <span className="catalogue-sku-chip">SKU {row.sku}</span> : null}
            {row.category ? (
              <span className="catalogue-category-chip">{row.category}</span>
            ) : null}
          </div>
        ),
      },
      {
        key: "price",
        label: "Price",
        render: (_value, row) => formatMoney(row.price, row.currency),
      },
      {
        key: "quantity",
        label: "Qty",
        render: (value) => (value === null || value === undefined ? "—" : value),
      },
      {
        key: "availability_status",
        label: "Availability",
        render: (value) =>
          value ? (
            <StatusBadge
              status={value}
              tone={AVAILABILITY_TONE[value]}
              label={AVAILABILITY_LABEL[value] || value}
            />
          ) : (
            "—"
          ),
      },
      {
        key: "status",
        label: "Status",
        render: (value) => (
          <StatusBadge status={value} tone={STATUS_TONE[value]} label={STATUS_LABEL[value] || value} />
        ),
      },
    ];

    if (canManage) {
      cols.push({
        key: "actions",
        label: "",
        align: "right",
        render: (_value, row) => (
          <div className="catalogue-row-actions">
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
  }, [canManage]);

  if (!canView) {
    return (
      <section className="catalogue-page">
        <PageHeader
          eyebrow="CATALOGUE"
          title="Master Catalogue"
          description="One product catalogue synchronized with WhatsApp, websites, accounting systems and future sales channels."
        />
        <AppCard padding="large">
          <EmptyState
            icon={<LockOutlined />}
            title="You don't have access to the Catalogue"
            description="Ask a company administrator to grant you the “View Catalogue” permission."
          />
        </AppCard>
      </section>
    );
  }

  return (
    <section className="catalogue-page">
      <PageHeader
        eyebrow="CATALOGUE"
        title="Master Catalogue"
        description="Manage the product catalogue the AI bot references when answering product questions."
        actions={
          <div className="catalogue-row-actions">
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
                New product
              </AppButton>
            ) : null}
          </div>
        }
      />

      {!canManage ? (
        <p className="catalogue-inline-note">
          <LockOutlined fontSize="small" /> You have read-only access. Ask an
          administrator for the &quot;Manage Catalogue&quot; permission to
          create, edit or delete products.
        </p>
      ) : null}

      <AppCard padding="medium">
        <div className="catalogue-toolbar">
          <SearchBar
            value={searchInput}
            placeholder="Search products by name, SKU or description..."
            ariaLabel="Search catalogue"
            onChange={setSearchInput}
            onClear={() => setSearchInput("")}
          />

          <label className="catalogue-filter">
            <span>Status</span>
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
              <option value="all">All statuses</option>
              {STATUS_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <label className="catalogue-filter">
            <span>Category</span>
            <select value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}>
              <option value="all">All categories</option>
              {categories.map((category) => (
                <option key={category} value={category}>
                  {category}
                </option>
              ))}
            </select>
          </label>

          <StatusBadge
            status="info"
            tone="info"
            showDot={false}
            label={`${total} product${total === 1 ? "" : "s"}`}
          />
        </div>

        {error ? (
          <ErrorState
            title="Catalogue could not load"
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
            emptyTitle="No products found"
            emptyDescription={
              search || statusFilter !== "all" || categoryFilter !== "all"
                ? "No products match your filters. Try widening your search."
                : canManage
                  ? "Add your first product to start building the catalogue."
                  : "Products will appear here once the catalogue is set up."
            }
            renderMobileCard={(row) => (
              <div className="tz-mobile-record-fields">
                <div className="catalogue-title-cell">
                  <strong>{row.name}</strong>
                  {row.sku ? <span className="catalogue-sku-chip">SKU {row.sku}</span> : null}
                </div>
                <div className="catalogue-mobile-meta">
                  <StatusBadge status={row.status} tone={STATUS_TONE[row.status]} label={STATUS_LABEL[row.status] || row.status} />
                  {row.availability_status ? (
                    <StatusBadge
                      status={row.availability_status}
                      tone={AVAILABILITY_TONE[row.availability_status]}
                      label={AVAILABILITY_LABEL[row.availability_status] || row.availability_status}
                    />
                  ) : null}
                </div>
                <span>{formatMoney(row.price, row.currency)} · Qty {row.quantity ?? "—"}</span>
                {canManage ? (
                  <div className="catalogue-row-actions">
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
            className="tz-dialog catalogue-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="catalogue-editor-title"
          >
            <header className="tz-dialog-header">
              <h3 id="catalogue-editor-title">
                <Inventory2Outlined fontSize="small" /> {isEdit ? "Edit product" : "New product"}
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
              <div className="catalogue-form">
                <div className="catalogue-grid-2">
                  <label className="catalogue-field">
                    <span>Name</span>
                    <input
                      type="text"
                      value={form.name}
                      disabled={saving}
                      placeholder="e.g. iPhone 15 Pro 256GB"
                      onChange={(event) => updateForm("name", event.target.value)}
                    />
                  </label>

                  <label className="catalogue-field">
                    <span>SKU</span>
                    <input
                      type="text"
                      value={form.sku}
                      disabled={saving}
                      placeholder="e.g. IP15P-256"
                      onChange={(event) => updateForm("sku", event.target.value)}
                    />
                  </label>
                </div>

                <label className="catalogue-field">
                  <span>Description</span>
                  <textarea
                    value={form.description}
                    disabled={saving}
                    placeholder="Details customers or the AI bot might need"
                    onChange={(event) => updateForm("description", event.target.value)}
                  />
                </label>

                <div className="catalogue-grid-2">
                  <label className="catalogue-field">
                    <span>Category</span>
                    <input
                      type="text"
                      value={form.category}
                      disabled={saving}
                      placeholder="e.g. Phones"
                      onChange={(event) => updateForm("category", event.target.value)}
                    />
                  </label>

                  <label className="catalogue-field">
                    <span>Brand</span>
                    <input
                      type="text"
                      value={form.brand}
                      disabled={saving}
                      placeholder="e.g. Apple"
                      onChange={(event) => updateForm("brand", event.target.value)}
                    />
                  </label>
                </div>

                <div className="catalogue-grid-3">
                  <label className="catalogue-field">
                    <span>Price</span>
                    <input
                      type="number"
                      step="0.01"
                      value={form.price}
                      disabled={saving}
                      onChange={(event) => updateForm("price", event.target.value)}
                    />
                  </label>

                  <label className="catalogue-field">
                    <span>Currency</span>
                    <input
                      type="text"
                      value={form.currency}
                      disabled={saving}
                      maxLength={8}
                      onChange={(event) => updateForm("currency", event.target.value)}
                    />
                  </label>

                  <label className="catalogue-field">
                    <span>Quantity</span>
                    <input
                      type="number"
                      step="1"
                      value={form.quantity}
                      disabled={saving}
                      onChange={(event) => updateForm("quantity", event.target.value)}
                    />
                  </label>
                </div>

                <div className="catalogue-grid-2">
                  <label className="catalogue-field">
                    <span>Availability</span>
                    <select
                      value={form.availability_status}
                      disabled={saving}
                      onChange={(event) => updateForm("availability_status", event.target.value)}
                    >
                      {AVAILABILITY_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="catalogue-field">
                    <span>Status</span>
                    <select
                      value={form.status}
                      disabled={saving}
                      onChange={(event) => updateForm("status", event.target.value)}
                    >
                      {STATUS_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>

                {formError ? <p className="catalogue-form-error">{formError}</p> : null}
              </div>
            </div>

            <footer className="tz-dialog-actions">
              <AppButton variant="secondary" disabled={saving} onClick={closeEditor}>
                Cancel
              </AppButton>
              <AppButton variant="primary" loading={saving} onClick={handleSave}>
                {isEdit ? "Save changes" : "Create product"}
              </AppButton>
            </footer>
          </section>
        </div>
      ) : null}

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="Delete product"
        confirmLabel="Delete"
        cancelLabel="Cancel"
        confirmVariant="danger"
        loading={deleting}
        onConfirm={handleDelete}
        onCancel={() => (deleting ? null : setDeleteTarget(null))}
        message={
          deleteTarget ? (
            <p>
              Delete <strong>{deleteTarget.name}</strong>? This cannot be undone.
            </p>
          ) : null
        }
      />
    </section>
  );
}
