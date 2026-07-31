import { useCallback, useEffect, useState } from "react";
import { AddOutlined, CloseOutlined, DeleteOutlineOutlined, UploadFileOutlined } from "@mui/icons-material";
import {
  catalogueOptionsRequest,
  createProductRequest,
  deleteProductRequest,
  importCatalogueCsvRequest,
  importWhatsAppCatalogueRequest,
  listProductsRequest,
  updateProductRequest,
} from "../../api/client";
import { AppButton, AppCard, AppTable, ConfirmDialog, ErrorState, PageHeader, SearchBar, StatusBadge } from "../../components/common";
import "./CataloguePage.css";

const STATUS_TONE = { active: "success", archived: "neutral" };
const LOW_STOCK_THRESHOLD = 5;

const EMPTY_FORM = {
  name: "",
  sku: "",
  category: "",
  price: "",
  stockQuantity: "",
  description: "",
  imageUrl: "",
};

function humanize(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatPrice(priceCents) {
  return `$${(Number(priceCents || 0) / 100).toFixed(2)}`;
}

function ImportDialog({ open, onCancel, onImported }) {
  const [mode, setMode] = useState("csv");
  const [catalogId, setCatalogId] = useState("");
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  useEffect(() => {
    if (!open) { setMode("csv"); setCatalogId(""); setError(""); setResult(null); }
  }, [open]);

  if (!open) return null;

  async function handleCsvFile(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setImporting(true);
    setError("");
    setResult(null);
    try {
      const summary = await importCatalogueCsvRequest(file);
      setResult(summary);
      onImported();
    } catch (requestError) {
      setError(requestError.message || "Could not import this file.");
    } finally {
      setImporting(false);
    }
  }

  async function handleWhatsAppImport(event) {
    event.preventDefault();
    if (!catalogId.trim()) return;
    setImporting(true);
    setError("");
    setResult(null);
    try {
      const summary = await importWhatsAppCatalogueRequest(catalogId.trim());
      setResult(summary);
      onImported();
    } catch (requestError) {
      setError(requestError.message || "Could not import from WhatsApp Catalogue.");
    } finally {
      setImporting(false);
    }
  }

  return (
    <div className="tz-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !importing) onCancel(); }}>
      <div className="tz-dialog product-import-dialog">
        <header className="tz-dialog-header">
          <h3>Import products</h3>
          <button type="button" className="tz-dialog-close" onClick={onCancel} disabled={importing}><CloseOutlined fontSize="small" /></button>
        </header>
        <div className="tz-dialog-body">
          <div className="publish-tabs">
            <button type="button" className={`publish-tab ${mode === "csv" ? "is-active" : ""}`} onClick={() => setMode("csv")}>CSV file</button>
            <button type="button" className={`publish-tab ${mode === "whatsapp" ? "is-active" : ""}`} onClick={() => setMode("whatsapp")}>WhatsApp Catalogue</button>
          </div>

          {mode === "csv" ? (
            <label className="product-field" style={{ marginTop: 16 }}>
              CSV file (columns: name, sku, description, category, price, stock_quantity — only "name" is required)
              <input type="file" accept=".csv,text/csv" disabled={importing} onChange={handleCsvFile} />
              <span className="product-import-note">Works for any product export from a POS system or website admin panel, as long as it's a CSV.</span>
            </label>
          ) : (
            <form onSubmit={handleWhatsAppImport} style={{ marginTop: 16 }}>
              <label className="product-field">
                Meta Commerce Catalog ID
                <input value={catalogId} disabled={importing} onChange={(event) => setCatalogId(event.target.value)} placeholder="e.g. 1234567890" />
                <span className="product-import-note">Uses your already-connected WhatsApp channel's access token — connect one first from Company Settings → Channels.</span>
              </label>
              <AppButton type="submit" variant="primary" loading={importing} disabled={!catalogId.trim()} style={{ marginTop: 10 }}>
                Import from WhatsApp
              </AppButton>
            </form>
          )}

          {importing && mode === "csv" ? <p className="product-import-note">Importing…</p> : null}
          {error ? <p className="product-form-error">{error}</p> : null}
          {result ? (
            <p className="product-import-result">
              {result.created} created, {result.updated} updated
              {result.errors?.length ? `, ${result.errors.length} row(s) skipped: ${result.errors.join(" ")}` : "."}
            </p>
          ) : null}
        </div>
        <footer className="tz-dialog-actions">
          <AppButton type="button" variant="secondary" onClick={onCancel}>Close</AppButton>
        </footer>
      </div>
    </div>
  );
}

export default function CataloguePage() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [statuses, setStatuses] = useState([]);
  const [categories, setCategories] = useState([]);

  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

  const [productToDelete, setProductToDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [importDialogOpen, setImportDialogOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  const loadOptions = useCallback(async () => {
    try {
      const result = await catalogueOptionsRequest();
      setStatuses(Array.isArray(result?.statuses) ? result.statuses : []);
      setCategories(Array.isArray(result?.categories) ? result.categories : []);
    } catch {
      // Options are used for filters/autocomplete only — a failure here
      // shouldn't block the main product list from loading.
    }
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await listProductsRequest({
        search: search.trim() || undefined,
        category: categoryFilter || undefined,
        status: statusFilter || undefined,
      });
      setRows(Array.isArray(result?.items) ? result.items : []);
    } catch (requestError) {
      setError(requestError.message || "Products could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [search, categoryFilter, statusFilter]);

  useEffect(() => {
    const timeout = window.setTimeout(load, search ? 300 : 0);
    return () => window.clearTimeout(timeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, categoryFilter, statusFilter]);

  useEffect(() => { loadOptions(); }, [loadOptions]);

  function openDialog() {
    setForm(EMPTY_FORM);
    setFormError("");
    setDialogOpen(true);
  }

  function closeDialog() {
    setDialogOpen(false);
  }

  async function saveNewProduct(event) {
    event.preventDefault();
    if (!form.name.trim()) return;
    setSaving(true);
    setFormError("");
    try {
      await createProductRequest({
        name: form.name.trim(),
        sku: form.sku.trim() || undefined,
        category: form.category.trim() || undefined,
        price_cents: form.price ? Math.round(parseFloat(form.price) * 100) : 0,
        stock_quantity: form.stockQuantity ? parseInt(form.stockQuantity, 10) : 0,
        description: form.description.trim() || undefined,
        image_url: form.imageUrl.trim() || undefined,
      });
      closeDialog();
      await loadOptions();
      await load();
    } catch (requestError) {
      setFormError(requestError.message || "Could not create the product.");
    } finally {
      setSaving(false);
    }
  }

  async function changeStatus(row, status) {
    try {
      await updateProductRequest(row.id, { status });
      await load();
    } catch (requestError) {
      setError(requestError.message || "Could not update product status.");
    }
  }

  async function confirmDeleteProduct() {
    if (!productToDelete) return;
    setDeleting(true);
    try {
      await deleteProductRequest(productToDelete.id);
      setProductToDelete(null);
      await load();
    } catch (requestError) {
      setError(requestError.message || "Could not delete the product.");
    } finally {
      setDeleting(false);
    }
  }

  const columns = [
    {
      key: "name",
      label: "Product",
      render: (_value, row) => (
        <div className="product-name-cell">
          <strong>{row.name}</strong>
          {row.sku ? <span>SKU: {row.sku}</span> : null}
        </div>
      ),
    },
    {
      key: "category",
      label: "Category",
      render: (value) => value || <span className="product-empty-cell">—</span>,
    },
    {
      key: "price_cents",
      label: "Price",
      render: (value) => formatPrice(value),
    },
    {
      key: "stock_quantity",
      label: "Stock",
      render: (value) => (
        <span className={Number(value) <= LOW_STOCK_THRESHOLD ? "product-stock-low" : undefined}>
          {value ?? 0}
        </span>
      ),
    },
    {
      key: "status",
      label: "Status",
      render: (value, row) => (
        <select
          className="tz-select product-status-select"
          value={value || ""}
          onChange={(event) => changeStatus(row, event.target.value)}
        >
          {(statuses.length ? statuses : ["active", "archived"]).map((status) => (
            <option value={status} key={status}>{humanize(status)}</option>
          ))}
        </select>
      ),
    },
    {
      key: "_actions",
      label: "",
      width: 44,
      render: (_value, row) => (
        <button
          type="button"
          className="product-delete-button"
          aria-label={`Delete product ${row.name}`}
          onClick={() => setProductToDelete(row)}
        >
          <DeleteOutlineOutlined fontSize="small" />
        </button>
      ),
    },
  ];

  return (
    <section className="catalogue-page">
      <PageHeader
        actions={
          <>
            <AppButton variant="secondary" icon={<UploadFileOutlined fontSize="small" />} onClick={() => setImportDialogOpen(true)}>
              Import
            </AppButton>
            <AppButton variant="primary" icon={<AddOutlined fontSize="small" />} onClick={openDialog}>
              New Product
            </AppButton>
          </>
        }
      />

      <AppCard padding="medium" className="product-filter-card">
        <div className="product-filter-bar">
          <SearchBar value={search} placeholder="Search by name, SKU, description..." onChange={setSearch} />
          <select className="tz-select" value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}>
            <option value="">All categories</option>
            {categories.map((category) => <option value={category} key={category}>{category}</option>)}
          </select>
          <select className="tz-select" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">All statuses</option>
            {statuses.map((status) => <option value={status} key={status}>{humanize(status)}</option>)}
          </select>
        </div>
      </AppCard>

      {error ? (
        <ErrorState title="Could not load products" description={error} action={<AppButton variant="primary" onClick={load}>Retry</AppButton>} />
      ) : (
        <AppTable
          columns={columns}
          rows={rows}
          loading={loading}
          emptyTitle="No products found"
          emptyDescription="No product matches the current filters."
          page={1}
          pageSize={Math.max(rows.length, 1)}
          totalRows={rows.length}
          onPageChange={() => {}}
        />
      )}

      {dialogOpen ? (
        <div
          className="tz-dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => { if (event.target === event.currentTarget) closeDialog(); }}
        >
          <form className="tz-dialog" onSubmit={saveNewProduct}>
            <header className="tz-dialog-header">
              <h3>New product</h3>
              <button type="button" className="tz-dialog-close" onClick={closeDialog}>
                <CloseOutlined fontSize="small" />
              </button>
            </header>
            <div className="tz-dialog-body product-new-fields">
              <label className="product-field">
                Name
                <input
                  value={form.name}
                  onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
                  maxLength={200}
                  autoFocus
                  required
                />
              </label>

              <div className="product-field-row">
                <label className="product-field">
                  SKU
                  <input
                    value={form.sku}
                    onChange={(event) => setForm((current) => ({ ...current, sku: event.target.value }))}
                    maxLength={80}
                  />
                </label>
                <label className="product-field">
                  Category
                  <input
                    value={form.category}
                    onChange={(event) => setForm((current) => ({ ...current, category: event.target.value }))}
                    maxLength={80}
                    list="product-category-options"
                  />
                  <datalist id="product-category-options">
                    {categories.map((category) => <option value={category} key={category} />)}
                  </datalist>
                </label>
              </div>

              <div className="product-field-row">
                <label className="product-field">
                  Price ($)
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={form.price}
                    onChange={(event) => setForm((current) => ({ ...current, price: event.target.value }))}
                  />
                </label>
                <label className="product-field">
                  Stock quantity
                  <input
                    type="number"
                    min="0"
                    step="1"
                    value={form.stockQuantity}
                    onChange={(event) => setForm((current) => ({ ...current, stockQuantity: event.target.value }))}
                  />
                </label>
              </div>

              <label className="product-field">
                Description
                <textarea
                  value={form.description}
                  onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))}
                  rows={3}
                />
              </label>

              <label className="product-field">
                Image URL
                <input
                  value={form.imageUrl}
                  onChange={(event) => setForm((current) => ({ ...current, imageUrl: event.target.value }))}
                  maxLength={2000}
                  placeholder="https://..."
                />
              </label>
              {form.imageUrl.trim() ? (
                <img className="product-image-preview" src={form.imageUrl.trim()} alt="Preview" />
              ) : null}

              {formError ? <p className="product-form-error">{formError}</p> : null}
            </div>
            <footer className="tz-dialog-actions">
              <AppButton type="button" variant="secondary" disabled={saving} onClick={closeDialog}>Cancel</AppButton>
              <AppButton type="submit" variant="primary" loading={saving}>Create product</AppButton>
            </footer>
          </form>
        </div>
      ) : null}

      <ConfirmDialog
        open={Boolean(productToDelete)}
        title="Delete product"
        message={`Delete "${productToDelete?.name}"? This cannot be undone.`}
        confirmLabel="Delete"
        confirmVariant="danger"
        loading={deleting}
        onConfirm={confirmDeleteProduct}
        onCancel={() => setProductToDelete(null)}
      />

      <ImportDialog
        open={importDialogOpen}
        onCancel={() => setImportDialogOpen(false)}
        onImported={load}
      />
    </section>
  );
}
