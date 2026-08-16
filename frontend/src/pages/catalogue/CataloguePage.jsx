import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AddOutlined,
  CloseOutlined,
  DeleteOutlineOutlined,
  GridViewOutlined,
  Inventory2Outlined,
  RefreshOutlined,
  ViewListOutlined,
} from "@mui/icons-material";

import {
  createProductCategoryRequest,
  createProductRequest,
  deleteProductCategoryRequest,
  deleteProductRequest,
  getCatalogueOptionsRequest,
  getProductRequest,
  getProductsRequest,
  updateProductCategoryRequest,
  updateProductRequest,
} from "../../api/catalogue";
import {
  AppButton,
  AppCard,
  AppTable,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  SearchBar,
  StatusBadge,
} from "../../components/common";
import { formatPlatformDateTime } from "../../utils/dateTime";
import "./CataloguePage.css";

const PAGE_SIZE = 24;

const STATUS_OPTIONS = ["active", "draft", "archived"];

const STOCK_OPTIONS = [
  ["in_stock", "In stock"],
  ["out_of_stock", "Out of stock"],
];

const CURRENCY_SUGGESTIONS = ["USD", "LBP", "EUR", "TRY", "AED", "SAR"];

function emptyForm() {
  return {
    name: "",
    name_en: "",
    sku: "",
    brand: "",
    category_id: "",
    price: "",
    sale_price: "",
    currency: "USD",
    stock_quantity: "",
    in_stock: true,
    image_url: "",
    description: "",
    attributes: "",
    status: "active",
  };
}

function formFromProduct(product) {
  const form = emptyForm();

  Object.keys(form).forEach((key) => {
    const value = product?.[key];

    if (key === "in_stock") {
      form.in_stock = Boolean(value);
      return;
    }

    if (key === "attributes") {
      const attributes = value && typeof value === "object" ? value : {};
      form.attributes = Object.keys(attributes).length
        ? JSON.stringify(attributes, null, 2)
        : "";
      return;
    }

    form[key] = value === null || value === undefined ? "" : String(value);
  });

  return form;
}

function numberOrNull(value) {
  const text = String(value ?? "").trim();

  if (!text) {
    return null;
  }

  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : null;
}

function payloadFromForm(form) {
  return {
    name: form.name.trim(),
    name_en: form.name_en.trim() || null,
    sku: form.sku.trim() || null,
    brand: form.brand.trim() || null,
    category_id: form.category_id ? Number(form.category_id) : null,
    price: numberOrNull(form.price),
    sale_price: numberOrNull(form.sale_price),
    currency: form.currency.trim().toUpperCase() || "USD",
    stock_quantity: numberOrNull(form.stock_quantity),
    in_stock: Boolean(form.in_stock),
    image_url: form.image_url.trim() || null,
    description: form.description.trim() || null,
    attributes: form.attributes.trim() ? JSON.parse(form.attributes) : {},
    status: form.status || "active",
  };
}

function formatMoney(product) {
  const price = product?.sale_price ?? product?.price;

  if (price === null || price === undefined || price === "") {
    return "No price";
  }

  const currency = product?.currency || "USD";
  return `${Number(price).toLocaleString()} ${currency}`;
}

function productLabel(product) {
  return product?.name || product?.name_en || `Product #${product?.id ?? "—"}`;
}

function StockBadge({ product }) {
  const quantity = product?.stock_quantity;
  const inStock = Boolean(product?.in_stock);

  const label = inStock
    ? quantity === null || quantity === undefined
      ? "In stock"
      : `In stock · ${quantity}`
    : "Out of stock";

  return (
    <StatusBadge
      status={inStock ? "active" : "inactive"}
      tone={inStock ? "success" : "danger"}
      label={label}
    />
  );
}

export default function CataloguePage() {
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [stock, setStock] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [view, setView] = useState("grid");

  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [categories, setCategories] = useState([]);

  const [editorOpen, setEditorOpen] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState("");

  const [pendingDelete, setPendingDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const [categoryName, setCategoryName] = useState("");
  const [categorySort, setCategorySort] = useState("");
  const [categorySaving, setCategorySaving] = useState(false);
  const [categoryError, setCategoryError] = useState("");
  const [editingCategoryId, setEditingCategoryId] = useState(null);
  const [editingCategoryName, setEditingCategoryName] = useState("");
  const [pendingCategoryDelete, setPendingCategoryDelete] = useState(null);
  const [deletingCategory, setDeletingCategory] = useState(false);

  const loadProducts = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const result = await getProductsRequest({
        search,
        categoryId,
        stock,
        status,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      });

      setRows(Array.isArray(result?.items) ? result.items : []);
      setTotal(Number(result?.total || 0));
    } catch (requestError) {
      setError(requestError.message || "The catalogue could not be loaded.");
      setRows([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [search, categoryId, stock, status, page]);

  const loadOptions = useCallback(async () => {
    try {
      const result = await getCatalogueOptionsRequest();
      setCategories(Array.isArray(result?.categories) ? result.categories : []);
    } catch {
      // Filters are a convenience; losing them must not blank the screen.
      setCategories([]);
    }
  }, []);

  useEffect(() => {
    loadProducts();
  }, [loadProducts]);

  useEffect(() => {
    loadOptions();
  }, [loadOptions]);

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(1);
    }, 300);

    return () => window.clearTimeout(timeout);
  }, [searchInput]);

  const openCreate = useCallback(() => {
    setSelectedId(null);
    setForm(emptyForm());
    setDetailError("");
    setSaveStatus("");
    setEditorOpen(true);
  }, []);

  const openProduct = useCallback(async (productId) => {
    setEditorOpen(true);
    setSelectedId(productId);
    setDetailLoading(true);
    setDetailError("");
    setSaveStatus("");

    try {
      const product = await getProductRequest(productId);
      setForm(formFromProduct(product));
    } catch (requestError) {
      setDetailError(requestError.message || "This product could not be loaded.");
    } finally {
      setDetailLoading(false);
    }
  }, []);

  function closeEditor() {
    setEditorOpen(false);
    setSelectedId(null);
    setForm(emptyForm());
    setDetailError("");
    setSaveStatus("");
  }

  function updateField(key, value) {
    setSaveStatus("");
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function handleSave(event) {
    event.preventDefault();

    if (!form.name.trim()) {
      setDetailError("Give this product a name.");
      return;
    }

    let payload;

    try {
      payload = payloadFromForm(form);
    } catch {
      setDetailError(
        "Attributes must be valid JSON, for example {\"colour\": \"black\"}.",
      );
      return;
    }

    if (payload.price !== null && payload.price < 0) {
      setDetailError("A price cannot be negative.");
      return;
    }

    setSaving(true);
    setDetailError("");
    setSaveStatus("");

    try {
      const saved = selectedId
        ? await updateProductRequest(selectedId, payload)
        : await createProductRequest(payload);

      setSelectedId(saved.id);
      setForm(formFromProduct(saved));
      setSaveStatus(
        "Saved. The assistant quotes this price and stock from its next reply.",
      );
      await Promise.all([loadProducts(), loadOptions()]);
    } catch (requestError) {
      setDetailError(requestError.message || "This product could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!pendingDelete) {
      return;
    }

    setDeleting(true);

    try {
      await deleteProductRequest(pendingDelete.id);

      if (selectedId === pendingDelete.id) {
        closeEditor();
      }

      setPendingDelete(null);
      await Promise.all([loadProducts(), loadOptions()]);
    } catch (requestError) {
      setError(requestError.message || "This product could not be deleted.");
      setPendingDelete(null);
    } finally {
      setDeleting(false);
    }
  }

  async function handleCreateCategory(event) {
    event.preventDefault();

    const name = categoryName.trim();

    if (!name) {
      setCategoryError("Give the category a name.");
      return;
    }

    setCategorySaving(true);
    setCategoryError("");

    try {
      await createProductCategoryRequest({
        name,
        sort_order: Number(categorySort) || 0,
        status: "active",
      });

      setCategoryName("");
      setCategorySort("");
      await loadOptions();
    } catch (requestError) {
      setCategoryError(
        requestError.message || "The category could not be created.",
      );
    } finally {
      setCategorySaving(false);
    }
  }

  async function handleRenameCategory(event) {
    event.preventDefault();

    const name = editingCategoryName.trim();

    if (!editingCategoryId || !name) {
      setCategoryError("Give the category a name.");
      return;
    }

    setCategorySaving(true);
    setCategoryError("");

    try {
      await updateProductCategoryRequest(editingCategoryId, { name });
      setEditingCategoryId(null);
      setEditingCategoryName("");
      await Promise.all([loadOptions(), loadProducts()]);
    } catch (requestError) {
      setCategoryError(
        requestError.message || "The category could not be renamed.",
      );
    } finally {
      setCategorySaving(false);
    }
  }

  async function handleDeleteCategory() {
    if (!pendingCategoryDelete) {
      return;
    }

    setDeletingCategory(true);

    try {
      await deleteProductCategoryRequest(pendingCategoryDelete.id);

      if (String(categoryId) === String(pendingCategoryDelete.id)) {
        setCategoryId("");
      }

      setPendingCategoryDelete(null);
      await Promise.all([loadOptions(), loadProducts()]);
    } catch (requestError) {
      setCategoryError(
        requestError.message || "The category could not be deleted.",
      );
      setPendingCategoryDelete(null);
    } finally {
      setDeletingCategory(false);
    }
  }

  const columns = useMemo(
    () => [
      {
        key: "name",
        label: "Product",
        render: (value, row) => (
          <button
            type="button"
            className="catalogue-name-button"
            onClick={() => openProduct(row.id)}
          >
            <strong>{productLabel(row)}</strong>
            {row.sku ? <span>{row.sku}</span> : null}
          </button>
        ),
      },
      {
        key: "brand",
        label: "Brand",
        render: (value) => value || "—",
      },
      {
        key: "category_name",
        label: "Category",
        render: (value) => value || "Unfiled",
      },
      {
        key: "price",
        label: "Price",
        render: (value, row) => (
          <div className="catalogue-price-cell">
            <strong>{formatMoney(row)}</strong>
            {row.sale_price !== null &&
            row.sale_price !== undefined &&
            row.price !== null &&
            row.price !== undefined ? (
              <span>was {Number(row.price).toLocaleString()}</span>
            ) : null}
          </div>
        ),
      },
      {
        key: "in_stock",
        label: "Stock",
        render: (value, row) => <StockBadge product={row} />,
      },
      {
        key: "status",
        label: "Status",
        render: (value) => <StatusBadge status={value} />,
      },
      {
        key: "updated_at",
        label: "Updated",
        render: (value) => formatPlatformDateTime(value),
      },
      {
        key: "actions",
        label: "",
        align: "right",
        render: (value, row) => (
          <div className="catalogue-row-actions">
            <AppButton
              variant="ghost"
              size="small"
              onClick={() => openProduct(row.id)}
            >
              Edit
            </AppButton>

            <AppButton
              variant="ghost"
              size="small"
              icon={<DeleteOutlineOutlined fontSize="small" />}
              onClick={() => setPendingDelete(row)}
            >
              Delete
            </AppButton>
          </div>
        ),
      },
    ],
    [openProduct],
  );

  const hasFilters = Boolean(search || categoryId || stock || status);
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="catalogue-page">
      <PageHeader
        eyebrow="PRODUCT CATALOGUE"
        title="Catalogue"
        description="What this company sells. These rows are the assistant's only source of confirmed price and stock: a product listed here is answered with real numbers, anything else is handed to a human."
        actions={
          <>
            <AppButton
              variant="secondary"
              icon={<RefreshOutlined fontSize="small" />}
              onClick={() => {
                loadProducts();
                loadOptions();
              }}
            >
              Refresh
            </AppButton>

            <AppButton
              variant="primary"
              icon={<AddOutlined fontSize="small" />}
              onClick={openCreate}
            >
              New product
            </AppButton>
          </>
        }
      />

      <div className={`catalogue-layout ${editorOpen ? "has-editor" : ""}`}>
        <div className="catalogue-main">
          <AppCard padding="medium" className="catalogue-list-card">
            <div className="catalogue-toolbar">
              <SearchBar
                value={searchInput}
                placeholder="Search name, SKU or brand..."
                ariaLabel="Search products"
                onChange={setSearchInput}
              />

              <label className="catalogue-filter" htmlFor="catalogue-category-filter">
                <span>Category</span>

                <select
                  id="catalogue-category-filter"
                  value={categoryId}
                  onChange={(event) => {
                    setCategoryId(event.target.value);
                    setPage(1);
                  }}
                >
                  <option value="">All categories</option>

                  {categories.map((category) => (
                    <option key={category.id} value={category.id}>
                      {category.name}
                    </option>
                  ))}
                </select>
              </label>

              <label className="catalogue-filter" htmlFor="catalogue-stock-filter">
                <span>Stock</span>

                <select
                  id="catalogue-stock-filter"
                  value={stock}
                  onChange={(event) => {
                    setStock(event.target.value);
                    setPage(1);
                  }}
                >
                  <option value="">Any stock</option>

                  {STOCK_OPTIONS.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="catalogue-filter" htmlFor="catalogue-status-filter">
                <span>Status</span>

                <select
                  id="catalogue-status-filter"
                  value={status}
                  onChange={(event) => {
                    setStatus(event.target.value);
                    setPage(1);
                  }}
                >
                  <option value="">All statuses</option>

                  {STATUS_OPTIONS.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>

              <div className="catalogue-view-toggle" role="group" aria-label="View">
                <button
                  type="button"
                  className={view === "grid" ? "is-active" : ""}
                  aria-pressed={view === "grid"}
                  aria-label="Grid view"
                  onClick={() => setView("grid")}
                >
                  <GridViewOutlined fontSize="small" />
                </button>

                <button
                  type="button"
                  className={view === "list" ? "is-active" : ""}
                  aria-pressed={view === "list"}
                  aria-label="List view"
                  onClick={() => setView("list")}
                >
                  <ViewListOutlined fontSize="small" />
                </button>
              </div>

              <span className="catalogue-total">
                {total} {total === 1 ? "product" : "products"}
              </span>
            </div>

            {error ? (
              <ErrorState
                title="Catalogue could not load"
                description={error}
                action={
                  <AppButton variant="primary" onClick={loadProducts}>
                    Try again
                  </AppButton>
                }
              />
            ) : null}

            {!error && view === "list" ? (
              <AppTable
                columns={columns}
                rows={rows}
                loading={loading}
                emptyTitle="No products"
                emptyDescription={
                  hasFilters
                    ? "No product matches these filters."
                    : "Add what this company sells. Until then the assistant cannot confirm a price or availability to anyone."
                }
                page={page}
                pageSize={PAGE_SIZE}
                totalRows={total}
                onPageChange={setPage}
                renderMobileCard={(row) => (
                  <button
                    type="button"
                    className="catalogue-mobile-card"
                    onClick={() => openProduct(row.id)}
                  >
                    <strong>{productLabel(row)}</strong>
                    <span>{formatMoney(row)}</span>
                    <small>
                      {row.brand || "no brand"} · {row.in_stock ? "in stock" : "out of stock"}
                    </small>
                  </button>
                )}
              />
            ) : null}

            {!error && view === "grid" ? (
              <>
                {loading ? <LoadingState title="Loading products..." /> : null}

                {!loading && !rows.length ? (
                  <EmptyState
                    icon={<Inventory2Outlined />}
                    title="No products"
                    description={
                      hasFilters
                        ? "No product matches these filters."
                        : "Add what this company sells. Until then the assistant cannot confirm a price or availability to anyone."
                    }
                    action={
                      <AppButton variant="primary" onClick={openCreate}>
                        Add the first product
                      </AppButton>
                    }
                  />
                ) : null}

                {!loading && rows.length ? (
                  <>
                    <ul className="catalogue-grid">
                      {rows.map((product) => (
                        <li key={product.id}>
                          <article className="catalogue-card">
                            <button
                              type="button"
                              className="catalogue-card-media"
                              aria-label={`Edit ${productLabel(product)}`}
                              onClick={() => openProduct(product.id)}
                            >
                              {product.image_url ? (
                                <img
                                  src={product.image_url}
                                  alt=""
                                  loading="lazy"
                                />
                              ) : (
                                <Inventory2Outlined />
                              )}
                            </button>

                            <div className="catalogue-card-body">
                              <header>
                                <strong>{productLabel(product)}</strong>
                                <span>
                                  {[product.brand, product.category_name || "Unfiled"]
                                    .filter(Boolean)
                                    .join(" · ")}
                                </span>
                              </header>

                              <div className="catalogue-card-price">
                                <strong>{formatMoney(product)}</strong>

                                {product.sale_price !== null &&
                                product.sale_price !== undefined &&
                                product.price !== null &&
                                product.price !== undefined ? (
                                  <s>{Number(product.price).toLocaleString()}</s>
                                ) : null}
                              </div>

                              <div className="catalogue-card-badges">
                                <StockBadge product={product} />
                                <StatusBadge status={product.status} />
                              </div>

                              <footer>
                                <small>{product.sku || "No SKU"}</small>

                                <div>
                                  <AppButton
                                    variant="ghost"
                                    size="small"
                                    onClick={() => openProduct(product.id)}
                                  >
                                    Edit
                                  </AppButton>

                                  <AppButton
                                    variant="ghost"
                                    size="small"
                                    icon={<DeleteOutlineOutlined fontSize="small" />}
                                    onClick={() => setPendingDelete(product)}
                                  >
                                    Delete
                                  </AppButton>
                                </div>
                              </footer>
                            </div>
                          </article>
                        </li>
                      ))}
                    </ul>

                    <footer className="catalogue-grid-footer">
                      <span>
                        Showing {(page - 1) * PAGE_SIZE + 1} –{" "}
                        {Math.min(page * PAGE_SIZE, total)} of {total}
                      </span>

                      <div className="catalogue-pagination">
                        <AppButton
                          variant="secondary"
                          size="small"
                          disabled={page <= 1}
                          onClick={() => setPage(page - 1)}
                        >
                          Previous
                        </AppButton>

                        <span>
                          Page {page} of {totalPages}
                        </span>

                        <AppButton
                          variant="secondary"
                          size="small"
                          disabled={page >= totalPages}
                          onClick={() => setPage(page + 1)}
                        >
                          Next
                        </AppButton>
                      </div>
                    </footer>
                  </>
                ) : null}
              </>
            ) : null}
          </AppCard>

          <AppCard padding="medium" className="catalogue-categories-card">
            <header className="catalogue-section-head">
              <div>
                <span>CATEGORIES</span>
                <h3>Group what you sell</h3>
              </div>
            </header>

            {categories.length ? (
              <ul className="catalogue-category-list">
                {categories.map((category) => (
                  <li key={category.id}>
                    {editingCategoryId === category.id ? (
                      <form
                        className="catalogue-category-rename"
                        onSubmit={handleRenameCategory}
                      >
                        <input
                          type="text"
                          value={editingCategoryName}
                          maxLength={200}
                          aria-label={`Rename ${category.name}`}
                          onChange={(event) =>
                            setEditingCategoryName(event.target.value)
                          }
                        />

                        <AppButton
                          type="submit"
                          variant="primary"
                          size="small"
                          loading={categorySaving}
                        >
                          Save
                        </AppButton>

                        <AppButton
                          variant="ghost"
                          size="small"
                          onClick={() => {
                            setEditingCategoryId(null);
                            setEditingCategoryName("");
                            setCategoryError("");
                          }}
                        >
                          Cancel
                        </AppButton>
                      </form>
                    ) : (
                      <>
                        <div className="catalogue-category-name">
                          <strong>{category.name}</strong>
                          <small>
                            {Number(category.product_count || 0)}{" "}
                            {Number(category.product_count || 0) === 1
                              ? "product"
                              : "products"}
                          </small>
                        </div>

                        <div className="catalogue-category-actions">
                          <AppButton
                            variant="ghost"
                            size="small"
                            onClick={() => {
                              setCategoryError("");
                              setEditingCategoryId(category.id);
                              setEditingCategoryName(category.name);
                            }}
                          >
                            Rename
                          </AppButton>

                          <AppButton
                            variant="ghost"
                            size="small"
                            onClick={() => setPendingCategoryDelete(category)}
                          >
                            Delete
                          </AppButton>
                        </div>
                      </>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="catalogue-empty-note">
                No categories yet. Products work without one; a category only makes
                a long catalogue easier to keep in order.
              </p>
            )}

            <form className="catalogue-category-form" onSubmit={handleCreateCategory}>
              <label htmlFor="catalogue-category-name">
                <span>New category</span>

                <input
                  id="catalogue-category-name"
                  type="text"
                  value={categoryName}
                  maxLength={200}
                  placeholder="Phones"
                  onChange={(event) => {
                    setCategoryError("");
                    setCategoryName(event.target.value);
                  }}
                />
              </label>

              <label htmlFor="catalogue-category-sort">
                <span>Sort order</span>

                <input
                  id="catalogue-category-sort"
                  type="number"
                  min="0"
                  value={categorySort}
                  placeholder="0"
                  onChange={(event) => setCategorySort(event.target.value)}
                />
              </label>

              <AppButton type="submit" variant="secondary" loading={categorySaving}>
                Add category
              </AppButton>
            </form>

            {categoryError ? (
              <p className="catalogue-form-error">{categoryError}</p>
            ) : null}
          </AppCard>
        </div>

        {editorOpen ? (
          <AppCard padding="medium" className="catalogue-editor-card">
            <header className="catalogue-section-head">
              <div>
                <span>{selectedId ? "EDIT PRODUCT" : "NEW PRODUCT"}</span>
                <h3>{form.name || "Untitled product"}</h3>
              </div>

              <button
                type="button"
                className="catalogue-editor-close"
                aria-label="Close editor"
                onClick={closeEditor}
              >
                <CloseOutlined fontSize="small" />
              </button>
            </header>

            {detailLoading ? <LoadingState title="Loading product..." /> : null}

            {!detailLoading ? (
              <form className="catalogue-form" onSubmit={handleSave}>
                <div className="catalogue-form-grid">
                  <label htmlFor="catalogue-name" className="catalogue-field-wide">
                    <span>Name</span>

                    <input
                      id="catalogue-name"
                      type="text"
                      value={form.name}
                      maxLength={200}
                      placeholder="iPhone 15 Pro 256GB"
                      onChange={(event) => updateField("name", event.target.value)}
                    />
                  </label>

                  <label htmlFor="catalogue-name-en" className="catalogue-field-wide">
                    <span>Name (other language)</span>

                    <input
                      id="catalogue-name-en"
                      type="text"
                      value={form.name_en}
                      maxLength={200}
                      placeholder="Used when the customer writes in the other language"
                      onChange={(event) => updateField("name_en", event.target.value)}
                    />
                  </label>

                  <label htmlFor="catalogue-sku">
                    <span>SKU</span>

                    <input
                      id="catalogue-sku"
                      type="text"
                      value={form.sku}
                      maxLength={80}
                      placeholder="IP15P-256"
                      onChange={(event) => updateField("sku", event.target.value)}
                    />
                  </label>

                  <label htmlFor="catalogue-brand">
                    <span>Brand</span>

                    <input
                      id="catalogue-brand"
                      type="text"
                      value={form.brand}
                      maxLength={120}
                      placeholder="Apple"
                      onChange={(event) => updateField("brand", event.target.value)}
                    />
                  </label>

                  <label htmlFor="catalogue-category">
                    <span>Category</span>

                    <select
                      id="catalogue-category"
                      value={form.category_id}
                      onChange={(event) =>
                        updateField("category_id", event.target.value)
                      }
                    >
                      <option value="">Unfiled</option>

                      {categories.map((category) => (
                        <option key={category.id} value={category.id}>
                          {category.name}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label htmlFor="catalogue-status">
                    <span>Status</span>

                    <select
                      id="catalogue-status"
                      value={form.status}
                      onChange={(event) => updateField("status", event.target.value)}
                    >
                      {STATUS_OPTIONS.map((name) => (
                        <option key={name} value={name}>
                          {name}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label htmlFor="catalogue-price">
                    <span>Price</span>

                    <input
                      id="catalogue-price"
                      type="number"
                      min="0"
                      step="0.01"
                      value={form.price}
                      placeholder="1099"
                      onChange={(event) => updateField("price", event.target.value)}
                    />
                  </label>

                  <label htmlFor="catalogue-sale-price">
                    <span>Sale price</span>

                    <input
                      id="catalogue-sale-price"
                      type="number"
                      min="0"
                      step="0.01"
                      value={form.sale_price}
                      placeholder="Leave empty when not on offer"
                      onChange={(event) =>
                        updateField("sale_price", event.target.value)
                      }
                    />
                  </label>

                  <label htmlFor="catalogue-currency">
                    <span>Currency</span>

                    <input
                      id="catalogue-currency"
                      type="text"
                      list="catalogue-currency-options"
                      value={form.currency}
                      maxLength={8}
                      onChange={(event) =>
                        updateField("currency", event.target.value)
                      }
                    />

                    <datalist id="catalogue-currency-options">
                      {CURRENCY_SUGGESTIONS.map((code) => (
                        <option key={code} value={code} />
                      ))}
                    </datalist>
                  </label>

                  <label htmlFor="catalogue-stock-quantity">
                    <span>Stock quantity</span>

                    <input
                      id="catalogue-stock-quantity"
                      type="number"
                      min="0"
                      step="1"
                      value={form.stock_quantity}
                      placeholder="Leave empty if you do not track units"
                      onChange={(event) =>
                        updateField("stock_quantity", event.target.value)
                      }
                    />
                  </label>

                  <label
                    htmlFor="catalogue-in-stock"
                    className="catalogue-checkbox-field"
                  >
                    <input
                      id="catalogue-in-stock"
                      type="checkbox"
                      checked={form.in_stock}
                      onChange={(event) =>
                        updateField("in_stock", event.target.checked)
                      }
                    />

                    <span>
                      Available to sell — this is what the assistant tells a
                      customer asking if it is in stock.
                    </span>
                  </label>

                  <label htmlFor="catalogue-image" className="catalogue-field-wide">
                    <span>Image URL</span>

                    <input
                      id="catalogue-image"
                      type="url"
                      value={form.image_url}
                      maxLength={1000}
                      placeholder="https://..."
                      onChange={(event) =>
                        updateField("image_url", event.target.value)
                      }
                    />
                  </label>
                </div>

                <label htmlFor="catalogue-description">
                  <span>Description</span>

                  <textarea
                    id="catalogue-description"
                    rows={4}
                    value={form.description}
                    maxLength={4000}
                    placeholder="What the assistant may say about this product..."
                    onChange={(event) =>
                      updateField("description", event.target.value)
                    }
                  />
                </label>

                <label htmlFor="catalogue-attributes">
                  <span>Attributes (JSON)</span>

                  <textarea
                    id="catalogue-attributes"
                    rows={3}
                    value={form.attributes}
                    placeholder={'{"colour": "black", "warranty": "1 year"}'}
                    onChange={(event) =>
                      updateField("attributes", event.target.value)
                    }
                  />
                </label>

                <footer className="catalogue-form-footer">
                  <span className={detailError ? "is-error" : "is-success"}>
                    {detailError || saveStatus}
                  </span>

                  <div>
                    {selectedId ? (
                      <AppButton
                        variant="danger"
                        disabled={saving}
                        onClick={() =>
                          setPendingDelete({ id: selectedId, name: form.name })
                        }
                      >
                        Delete
                      </AppButton>
                    ) : null}

                    <AppButton
                      variant="secondary"
                      disabled={saving}
                      onClick={closeEditor}
                    >
                      Cancel
                    </AppButton>

                    <AppButton type="submit" variant="primary" loading={saving}>
                      {selectedId ? "Save product" : "Create product"}
                    </AppButton>
                  </div>
                </footer>
              </form>
            ) : null}
          </AppCard>
        ) : null}
      </div>

      <ConfirmDialog
        open={Boolean(pendingDelete)}
        title="Delete product"
        message={
          <>
            <strong>{productLabel(pendingDelete)}</strong> will be removed from the
            catalogue. The assistant will stop confirming its price and stock, and
            will hand those questions to a human instead.
          </>
        }
        confirmLabel="Delete product"
        loading={deleting}
        onConfirm={handleDelete}
        onCancel={() => setPendingDelete(null)}
      />

      <ConfirmDialog
        open={Boolean(pendingCategoryDelete)}
        title="Delete category"
        message={
          <>
            <strong>{pendingCategoryDelete?.name}</strong> will be removed. Its{" "}
            {Number(pendingCategoryDelete?.product_count || 0)} product(s) stay in
            the catalogue, unfiled.
          </>
        }
        confirmLabel="Delete category"
        loading={deletingCategory}
        onConfirm={handleDeleteCategory}
        onCancel={() => setPendingCategoryDelete(null)}
      />
    </div>
  );
}
