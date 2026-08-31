import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AddOutlined,
  CloseOutlined,
  DeleteOutlineOutlined,
  RefreshOutlined,
} from "@mui/icons-material";

import {
  createKnowledgeCategoryRequest,
  createKnowledgeItemRequest,
  deleteKnowledgeItemRequest,
  getKnowledgeItemRequest,
  getKnowledgeItemsRequest,
  getKnowledgeOptionsRequest,
  updateKnowledgeItemRequest,
} from "../../api/client";
import {
  AppButton,
  AppCard,
  AppTable,
  ConfirmDialog,
  ErrorState,
  LoadingState,
  PageHeader,
  SearchBar,
  StatusBadge,
} from "../../components/common";
import { formatPlatformDateTime } from "../../utils/dateTime";
import "./KnowledgePage.css";

const PAGE_SIZE = 20;

const STATUS_OPTIONS = ["active", "draft", "archived"];

// The departments the router can classify a message into. They are offered as
// suggestions only: a company is free to type its own, and the filter list is
// built from what its items actually use.
const SUGGESTED_DEPARTMENTS = [
  "sales",
  "iptv",
  "maintenance",
  "accounting",
  "telecom",
  "orders",
  "information",
];

function emptyForm() {
  return {
    title: "",
    department: "",
    category_id: "",
    status: "active",
    external_id: "",
    keywords: "",
    content_ar: "",
    content_en: "",
  };
}

function formFromItem(item) {
  const form = emptyForm();

  Object.keys(form).forEach((key) => {
    const value = item?.[key];
    form[key] = value === null || value === undefined ? "" : String(value);
  });

  return form;
}

function payloadFromForm(form) {
  return {
    title: form.title.trim(),
    department: form.department.trim() || null,
    category_id: form.category_id ? Number(form.category_id) : null,
    status: form.status || "active",
    external_id: form.external_id.trim() || null,
    keywords: form.keywords.trim() || null,
    content_ar: form.content_ar.trim() || null,
    content_en: form.content_en.trim() || null,
  };
}

export default function KnowledgePage() {
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [department, setDepartment] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);

  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [options, setOptions] = useState({ departments: [], categories: [] });

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
  const [categoryDepartment, setCategoryDepartment] = useState("");
  const [categorySaving, setCategorySaving] = useState(false);
  const [categoryError, setCategoryError] = useState("");

  const loadItems = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const result = await getKnowledgeItemsRequest({
        search,
        department,
        status,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      });

      setRows(Array.isArray(result?.items) ? result.items : []);
      setTotal(Number(result?.total || 0));
    } catch (requestError) {
      setError(requestError.message || "Knowledge could not be loaded.");
      setRows([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [search, department, status, page]);

  const loadOptions = useCallback(async () => {
    try {
      const result = await getKnowledgeOptionsRequest();

      setOptions({
        departments: Array.isArray(result?.departments) ? result.departments : [],
        categories: Array.isArray(result?.categories) ? result.categories : [],
      });
    } catch {
      // The filters are a convenience; losing them must not blank the screen.
      setOptions({ departments: [], categories: [] });
    }
  }, []);

  useEffect(() => {
    loadItems();
  }, [loadItems]);

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

  const openItem = useCallback(async (itemId) => {
    setEditorOpen(true);
    setSelectedId(itemId);
    setDetailLoading(true);
    setDetailError("");
    setSaveStatus("");

    try {
      const item = await getKnowledgeItemRequest(itemId);
      setForm(formFromItem(item));
    } catch (requestError) {
      setDetailError(requestError.message || "This item could not be loaded.");
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

    const payload = payloadFromForm(form);

    if (!payload.title) {
      setDetailError("Give this item a title.");
      return;
    }

    if (!payload.content_ar && !payload.content_en) {
      setDetailError(
        "Add Arabic or English content. An item with neither teaches the assistant nothing.",
      );
      return;
    }

    setSaving(true);
    setDetailError("");
    setSaveStatus("");

    try {
      const saved = selectedId
        ? await updateKnowledgeItemRequest(selectedId, payload)
        : await createKnowledgeItemRequest(payload);

      setSelectedId(saved.id);
      setForm(formFromItem(saved));
      setSaveStatus("Saved. The assistant uses this from its next reply.");
      await Promise.all([loadItems(), loadOptions()]);
    } catch (requestError) {
      setDetailError(requestError.message || "This item could not be saved.");
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
      await deleteKnowledgeItemRequest(pendingDelete.id);

      if (selectedId === pendingDelete.id) {
        closeEditor();
      }

      setPendingDelete(null);
      await Promise.all([loadItems(), loadOptions()]);
    } catch (requestError) {
      setError(requestError.message || "This item could not be deleted.");
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
      await createKnowledgeCategoryRequest({
        name,
        department: categoryDepartment.trim() || null,
      });

      setCategoryName("");
      setCategoryDepartment("");
      await loadOptions();
    } catch (requestError) {
      setCategoryError(requestError.message || "The category could not be created.");
    } finally {
      setCategorySaving(false);
    }
  }

  const columns = useMemo(
    () => [
      {
        key: "title",
        label: "Item",
        render: (value, row) => (
          <button
            type="button"
            className="knowledge-title-button"
            onClick={() => openItem(row.id)}
          >
            <strong>{row.title}</strong>
            {row.external_id ? <span>{row.external_id}</span> : null}
          </button>
        ),
      },
      {
        key: "department",
        label: "Department",
        render: (value) => value || "—",
      },
      {
        key: "category_name",
        label: "Category",
        render: (value) => value || "—",
      },
      {
        key: "languages",
        label: "Languages",
        valueGetter: (row) =>
          [row.content_ar ? "AR" : null, row.content_en ? "EN" : null]
            .filter(Boolean)
            .join(" · "),
        render: (value) => value || "—",
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
          <div className="knowledge-row-actions">
            <AppButton variant="ghost" size="small" onClick={() => openItem(row.id)}>
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
    [openItem],
  );

  const departmentChoices = useMemo(() => {
    const merged = new Set([...options.departments, ...SUGGESTED_DEPARTMENTS]);
    return [...merged].sort();
  }, [options.departments]);

  return (
    <div className="knowledge-page">
      <PageHeader
        eyebrow="ASSISTANT KNOWLEDGE"
        title="Knowledge Base"
        description="What the assistant is allowed to answer from. These entries belong to this company alone and are the only knowledge its replies are grounded in."
        actions={
          <>
            <AppButton
              variant="secondary"
              icon={<RefreshOutlined fontSize="small" />}
              onClick={() => {
                loadItems();
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
              New item
            </AppButton>
          </>
        }
      />

      <div className={`knowledge-layout ${editorOpen ? "has-editor" : ""}`}>
        <div className="knowledge-main">
          <AppCard padding="medium" className="knowledge-list-card">
            <div className="knowledge-toolbar">
              <SearchBar
                value={searchInput}
                placeholder="Search title, content, keywords or id..."
                ariaLabel="Search knowledge"
                onChange={setSearchInput}
              />

              <label className="knowledge-filter" htmlFor="knowledge-department-filter">
                <span>Department</span>

                <select
                  id="knowledge-department-filter"
                  value={department}
                  onChange={(event) => {
                    setDepartment(event.target.value);
                    setPage(1);
                  }}
                >
                  <option value="">All departments</option>

                  {options.departments.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>

              <label className="knowledge-filter" htmlFor="knowledge-status-filter">
                <span>Status</span>

                <select
                  id="knowledge-status-filter"
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

              <span className="knowledge-total">
                {total} {total === 1 ? "item" : "items"}
              </span>
            </div>

            {error ? (
              <ErrorState
                title="Knowledge could not load"
                description={error}
                action={
                  <AppButton variant="primary" onClick={loadItems}>
                    Try again
                  </AppButton>
                }
              />
            ) : (
              <AppTable
                columns={columns}
                rows={rows}
                loading={loading}
                emptyTitle="No knowledge items"
                emptyDescription={
                  search || department || status
                    ? "No item matches these filters."
                    : "Add the answers this company gives its customers. Until then the assistant has nothing to ground a reply in and hands every question to a human."
                }
                page={page}
                pageSize={PAGE_SIZE}
                totalRows={total}
                onPageChange={setPage}
                renderMobileCard={(row) => (
                  <button
                    type="button"
                    className="knowledge-mobile-card"
                    onClick={() => openItem(row.id)}
                  >
                    <strong>{row.title}</strong>
                    <span>{row.content_en || row.content_ar || ""}</span>
                    <small>
                      {row.department || "no department"} · {row.status}
                    </small>
                  </button>
                )}
              />
            )}
          </AppCard>

          <AppCard padding="medium" className="knowledge-categories-card">
            <header className="knowledge-section-head">
              <div>
                <span>CATEGORIES</span>
                <h3>Group related items</h3>
              </div>
            </header>

            {options.categories.length ? (
              <ul className="knowledge-category-list">
                {options.categories.map((category) => (
                  <li key={category.id}>
                    <strong>{category.name}</strong>
                    <span>{category.department || "all departments"}</span>
                    <small>
                      {Number(category.item_count || 0)}{" "}
                      {Number(category.item_count || 0) === 1 ? "item" : "items"}
                    </small>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="knowledge-empty-note">
                No categories yet. Items work without one; a category only makes a
                long list easier to keep in order.
              </p>
            )}

            <form className="knowledge-category-form" onSubmit={handleCreateCategory}>
              <label htmlFor="knowledge-category-name">
                <span>New category</span>

                <input
                  id="knowledge-category-name"
                  type="text"
                  value={categoryName}
                  maxLength={120}
                  placeholder="Warranty and returns"
                  onChange={(event) => {
                    setCategoryError("");
                    setCategoryName(event.target.value);
                  }}
                />
              </label>

              <label htmlFor="knowledge-category-department">
                <span>Department</span>

                <input
                  id="knowledge-category-department"
                  type="text"
                  list="knowledge-department-options"
                  value={categoryDepartment}
                  maxLength={60}
                  placeholder="Optional"
                  onChange={(event) => setCategoryDepartment(event.target.value)}
                />
              </label>

              <AppButton type="submit" variant="secondary" loading={categorySaving}>
                Add category
              </AppButton>
            </form>

            {categoryError ? (
              <p className="knowledge-form-error">{categoryError}</p>
            ) : null}
          </AppCard>
        </div>

        {editorOpen ? (
          <AppCard padding="medium" className="knowledge-editor-card">
            <header className="knowledge-section-head">
              <div>
                <span>{selectedId ? "EDIT ITEM" : "NEW ITEM"}</span>
                <h3>{form.title || "Untitled item"}</h3>
              </div>

              <button
                type="button"
                className="knowledge-editor-close"
                aria-label="Close editor"
                onClick={closeEditor}
              >
                <CloseOutlined fontSize="small" />
              </button>
            </header>

            {detailLoading ? <LoadingState title="Loading item..." /> : null}

            {!detailLoading ? (
              <form className="knowledge-form" onSubmit={handleSave}>
                <label htmlFor="knowledge-title">
                  <span>Title</span>

                  <input
                    id="knowledge-title"
                    type="text"
                    value={form.title}
                    maxLength={200}
                    placeholder="Do you deliver outside Beirut?"
                    onChange={(event) => updateField("title", event.target.value)}
                  />
                </label>

                <div className="knowledge-form-grid">
                  <label htmlFor="knowledge-department">
                    <span>Department</span>

                    <input
                      id="knowledge-department"
                      type="text"
                      list="knowledge-department-options"
                      value={form.department}
                      maxLength={60}
                      placeholder="sales"
                      onChange={(event) =>
                        updateField("department", event.target.value)
                      }
                    />
                  </label>

                  <label htmlFor="knowledge-category">
                    <span>Category</span>

                    <select
                      id="knowledge-category"
                      value={form.category_id}
                      onChange={(event) =>
                        updateField("category_id", event.target.value)
                      }
                    >
                      <option value="">No category</option>

                      {options.categories.map((category) => (
                        <option key={category.id} value={String(category.id)}>
                          {category.name}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label htmlFor="knowledge-status">
                    <span>Status</span>

                    <select
                      id="knowledge-status"
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

                  <label htmlFor="knowledge-external-id">
                    <span>Reference id</span>

                    <input
                      id="knowledge-external-id"
                      type="text"
                      value={form.external_id}
                      maxLength={120}
                      placeholder="delivery_policy"
                      onChange={(event) =>
                        updateField("external_id", event.target.value)
                      }
                    />
                  </label>
                </div>

                <p className="knowledge-form-hint">
                  Only <strong>active</strong> items reach the assistant. The reference
                  id is how it names this item when it reports what it answered from.
                </p>

                <label htmlFor="knowledge-keywords">
                  <span>Keywords and usage notes</span>

                  <textarea
                    id="knowledge-keywords"
                    rows={2}
                    value={form.keywords}
                    maxLength={1000}
                    placeholder="Alternative phrasings customers use, and when this answer should not be used."
                    onChange={(event) => updateField("keywords", event.target.value)}
                  />
                </label>

                <label htmlFor="knowledge-content-ar">
                  <span>Arabic answer</span>

                  <textarea
                    id="knowledge-content-ar"
                    rows={5}
                    dir="rtl"
                    value={form.content_ar}
                    maxLength={8000}
                    placeholder="الجواب بالعربية..."
                    onChange={(event) => updateField("content_ar", event.target.value)}
                  />
                </label>

                <label htmlFor="knowledge-content-en">
                  <span>English answer</span>

                  <textarea
                    id="knowledge-content-en"
                    rows={5}
                    value={form.content_en}
                    maxLength={8000}
                    placeholder="The answer in English..."
                    onChange={(event) => updateField("content_en", event.target.value)}
                  />
                </label>

                <footer className="knowledge-form-footer">
                  <span className={detailError ? "is-error" : "is-success"}>
                    {detailError || saveStatus}
                  </span>

                  <div>
                    {selectedId ? (
                      <AppButton
                        variant="danger"
                        disabled={saving}
                        onClick={() =>
                          setPendingDelete({ id: selectedId, title: form.title })
                        }
                      >
                        Delete
                      </AppButton>
                    ) : null}

                    <AppButton variant="secondary" disabled={saving} onClick={closeEditor}>
                      Cancel
                    </AppButton>

                    <AppButton type="submit" variant="primary" loading={saving}>
                      {selectedId ? "Save item" : "Create item"}
                    </AppButton>
                  </div>
                </footer>
              </form>
            ) : null}
          </AppCard>
        ) : null}
      </div>

      <datalist id="knowledge-department-options">
        {departmentChoices.map((name) => (
          <option key={name} value={name} />
        ))}
      </datalist>

      <ConfirmDialog
        open={Boolean(pendingDelete)}
        title="Delete this knowledge item?"
        message={
          <span>
            The assistant will stop answering from{" "}
            <strong>{pendingDelete?.title || "this item"}</strong> immediately. This
            cannot be undone.
          </span>
        }
        confirmLabel="Delete item"
        loading={deleting}
        onConfirm={handleDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
