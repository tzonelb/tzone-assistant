import { useEffect, useMemo, useState } from "react";
import {
  AddOutlined,
  AutoAwesomeOutlined,
  CloseOutlined,
  DeleteOutlineOutlined,
  EditOutlined,
  LockOutlined,
  RefreshOutlined,
  SchoolOutlined,
  SearchOutlined,
} from "@mui/icons-material";

import {
  deleteKnowledgeFaqRequest,
  getKnowledgeFaqsRequest,
  saveKnowledgeFaqRequest,
} from "../../api/client";
import {
  AppButton,
  AppCard,
  AppTable,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  PageHeader,
  StatusBadge,
} from "../../components/common";
import { useAuth } from "../../contexts/AuthContext";
import "./AiTeachingPage.css";

// The departments the AI engine routes on (mirrors the static knowledge
// base). Free-text departments coming back from the API that are not in this
// list are still shown and preserved -- this only seeds the editor's picker.
const KNOWN_DEPARTMENTS = [
  { value: "information", label: "Information" },
  { value: "sales", label: "Sales" },
  { value: "accounting", label: "Accounting" },
  { value: "iptv", label: "IPTV" },
  { value: "maintenance", label: "Maintenance" },
  { value: "support", label: "Support" },
];

function humanizeDepartment(value) {
  const name = String(value || "").trim();
  if (!name) return "Unassigned";
  const known = KNOWN_DEPARTMENTS.find((item) => item.value === name);
  if (known) return known.label;
  return name.charAt(0).toUpperCase() + name.slice(1);
}

function newFaqId() {
  const rand =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  return `faq-${rand}`;
}

const EMPTY_FORM = {
  id: null,
  title_en: "",
  title_ar: "",
  body_en: "",
  body_ar: "",
  department: "information",
  category: "",
  enabled: true,
};

export default function AiTeachingPage() {
  const { hasPermission } = useAuth();
  const canView = hasPermission("knowledge.view");
  const canManage = hasPermission("knowledge.manage");

  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");

  const [editorOpen, setEditorOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [isEdit, setIsEdit] = useState(false);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);

  async function loadItems() {
    setLoading(true);
    setError("");
    try {
      const result = await getKnowledgeFaqsRequest();
      setItems(Array.isArray(result) ? result : []);
    } catch (err) {
      setError(err.message || "Knowledge items could not be loaded.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!canView) {
      setLoading(false);
      return;
    }
    loadItems();
  }, [canView]);

  const knownCategories = useMemo(() => {
    const set = new Set();
    items.forEach((item) => {
      const name = String(item.category || "").trim();
      if (name) set.add(name);
    });
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [items]);

  const filteredItems = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return items;
    return items.filter((item) => {
      const haystack = [
        item.title_en,
        item.title_ar,
        item.body_en,
        item.body_ar,
        item.category,
        humanizeDepartment(item.department),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    });
  }, [items, query]);

  const groups = useMemo(() => {
    const map = new Map();
    filteredItems.forEach((item) => {
      const key = item.department || "";
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(item);
    });
    return Array.from(map.entries()).sort((a, b) =>
      humanizeDepartment(a[0]).localeCompare(humanizeDepartment(b[0])),
    );
  }, [filteredItems]);

  function openCreate() {
    setForm({ ...EMPTY_FORM, id: newFaqId() });
    setIsEdit(false);
    setFormError("");
    setEditorOpen(true);
  }

  function openEdit(item) {
    setForm({
      id: item.id,
      title_en: item.title_en || "",
      title_ar: item.title_ar || "",
      body_en: item.body_en || "",
      body_ar: item.body_ar || "",
      department: item.department || "information",
      category: item.category || "",
      enabled: item.enabled !== false,
    });
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

  // The editor's department picker: known departments plus, when editing an
  // item stored under a custom/legacy department, that value too so it is
  // never silently lost.
  const departmentOptions = useMemo(() => {
    const options = [...KNOWN_DEPARTMENTS];
    const current = String(form.department || "").trim();
    if (current && !options.some((item) => item.value === current)) {
      options.push({ value: current, label: humanizeDepartment(current) });
    }
    return options;
  }, [form.department]);

  async function handleSave() {
    const titleEn = form.title_en.trim();
    const department = String(form.department || "").trim();

    if (!titleEn) {
      setFormError("An English title is required.");
      return;
    }
    if (!department) {
      setFormError("A department is required.");
      return;
    }

    setSaving(true);
    setFormError("");
    try {
      await saveKnowledgeFaqRequest(department, {
        id: form.id,
        title_en: titleEn,
        title_ar: form.title_ar.trim() || null,
        body_en: form.body_en.trim() || null,
        body_ar: form.body_ar.trim() || null,
        category: form.category.trim() || null,
        enabled: Boolean(form.enabled),
      });
      setEditorOpen(false);
      await loadItems();
    } catch (err) {
      setFormError(err.message || "The knowledge item could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteKnowledgeFaqRequest(
        deleteTarget.department || "information",
        deleteTarget.id,
      );
      setDeleteTarget(null);
      await loadItems();
    } catch (err) {
      setError(err.message || "The knowledge item could not be deleted.");
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  }

  function buildColumns() {
    const columns = [
      {
        key: "title_en",
        label: "Title",
        render: (_value, row) => (
          <div className="ai-teaching-title-cell">
            <strong>{row.title_en || "Untitled"}</strong>
            {row.title_ar ? (
              <span className="ai-teaching-title-ar">{row.title_ar}</span>
            ) : null}
          </div>
        ),
      },
      {
        key: "category",
        label: "Category",
        render: (value) => value || "General",
      },
      {
        key: "enabled",
        label: "Status",
        render: (value) => (
          <StatusBadge
            status={value ? "active" : "inactive"}
            label={value ? "Enabled" : "Disabled"}
          />
        ),
      },
    ];

    if (canManage) {
      columns.push({
        key: "actions",
        label: "",
        align: "right",
        render: (_value, row) => (
          <div className="ai-teaching-row-actions">
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

    return columns;
  }

  if (!canView) {
    return (
      <section className="ai-teaching-page">
        <PageHeader
          eyebrow="AI TEACHING"
          title="AI Teaching"
          description="The bilingual knowledge the company AI uses to answer customers."
        />
        <AppCard padding="large">
          <EmptyState
            icon={<LockOutlined />}
            title="You don't have access to AI Teaching"
            description="Ask a company administrator to grant you the “View Knowledge” permission."
          />
        </AppCard>
      </section>
    );
  }

  const columns = buildColumns();

  return (
    <section className="ai-teaching-page">
      <PageHeader
        eyebrow="AI TEACHING"
        title="AI Teaching"
        description="Manage the bilingual (Arabic & English) knowledge base your company AI uses to answer customers, grouped by department."
        actions={
          <div className="ai-teaching-row-actions">
            <AppButton
              variant="secondary"
              icon={<RefreshOutlined fontSize="small" />}
              onClick={loadItems}
            >
              Refresh
            </AppButton>
            {canManage ? (
              <AppButton
                variant="primary"
                icon={<AddOutlined fontSize="small" />}
                onClick={openCreate}
              >
                New knowledge item
              </AppButton>
            ) : null}
          </div>
        }
      />

      <div className="ai-teaching-toolbar">
        <label className="ai-teaching-search">
          <SearchOutlined fontSize="small" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search titles, answers, categories or departments..."
          />
        </label>
      </div>

      {!canManage ? (
        <p className="ai-teaching-inline-note">
          <LockOutlined fontSize="small" /> You have read-only access. Ask an
          administrator for the “Manage Knowledge” permission to add, edit or
          delete knowledge items.
        </p>
      ) : null}

      {error ? (
        <AppCard padding="medium">
          <ErrorState
            title="Knowledge could not load"
            description={error}
            action={
              <AppButton
                variant="primary"
                icon={<RefreshOutlined fontSize="small" />}
                onClick={loadItems}
              >
                Try again
              </AppButton>
            }
          />
        </AppCard>
      ) : loading ? (
        <AppCard padding="medium">
          <AppTable columns={columns} rows={[]} loading />
        </AppCard>
      ) : groups.length === 0 ? (
        <AppCard padding="large">
          <EmptyState
            icon={<SchoolOutlined />}
            title={query ? "No matching knowledge items" : "No knowledge yet"}
            description={
              query
                ? "No knowledge item matches your search. Try a different term."
                : "Add the questions, answers and instructions your AI should use to reply to customers."
            }
            action={
              canManage && !query ? (
                <AppButton
                  variant="primary"
                  icon={<AddOutlined fontSize="small" />}
                  onClick={openCreate}
                >
                  New knowledge item
                </AppButton>
              ) : null
            }
          />
        </AppCard>
      ) : (
        <div className="ai-teaching-groups">
          {groups.map(([department, rows]) => (
            <AppCard key={department || "unassigned"} padding="medium">
              <div className="ai-teaching-department-head">
                <h3>
                  <AutoAwesomeOutlined fontSize="small" />
                  {humanizeDepartment(department)}
                </h3>
                <span className="ai-teaching-department-count">
                  {rows.length} {rows.length === 1 ? "item" : "items"}
                </span>
              </div>
              <AppTable
                columns={columns}
                rows={rows}
                rowKey="id"
                pageSize={1000}
              />
            </AppCard>
          ))}
        </div>
      )}

      {editorOpen ? (
        <div
          className="tz-dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeEditor();
          }}
        >
          <section
            className="tz-dialog ai-teaching-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="ai-teaching-editor-title"
          >
            <header className="tz-dialog-header">
              <h3 id="ai-teaching-editor-title">
                {isEdit ? "Edit knowledge item" : "New knowledge item"}
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
              <div className="ai-teaching-form">
                <div className="ai-teaching-grid-2">
                  <label className="ai-teaching-field">
                    <span>Department</span>
                    <select
                      value={form.department}
                      disabled={saving}
                      onChange={(event) =>
                        updateForm("department", event.target.value)
                      }
                    >
                      {departmentOptions.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="ai-teaching-field">
                    <span>Category (optional)</span>
                    <input
                      type="text"
                      list="ai-teaching-categories"
                      value={form.category}
                      disabled={saving}
                      placeholder="e.g. Pricing, Warranty"
                      onChange={(event) =>
                        updateForm("category", event.target.value)
                      }
                    />
                    <datalist id="ai-teaching-categories">
                      {knownCategories.map((name) => (
                        <option key={name} value={name} />
                      ))}
                    </datalist>
                  </label>
                </div>

                <div className="ai-teaching-grid-2">
                  <div className="ai-teaching-lang">
                    <span className="ai-teaching-lang-title">English</span>
                    <label className="ai-teaching-field">
                      <span>Title</span>
                      <input
                        type="text"
                        value={form.title_en}
                        disabled={saving}
                        placeholder="Question or topic in English"
                        onChange={(event) =>
                          updateForm("title_en", event.target.value)
                        }
                      />
                    </label>
                    <label className="ai-teaching-field">
                      <span>Answer / content</span>
                      <textarea
                        value={form.body_en}
                        disabled={saving}
                        placeholder="The answer the AI should give in English"
                        onChange={(event) =>
                          updateForm("body_en", event.target.value)
                        }
                      />
                    </label>
                  </div>

                  <div className="ai-teaching-lang is-rtl">
                    <span className="ai-teaching-lang-title">العربية</span>
                    <label className="ai-teaching-field">
                      <span>العنوان</span>
                      <input
                        type="text"
                        value={form.title_ar}
                        disabled={saving}
                        placeholder="السؤال أو الموضوع بالعربية"
                        onChange={(event) =>
                          updateForm("title_ar", event.target.value)
                        }
                      />
                    </label>
                    <label className="ai-teaching-field">
                      <span>الإجابة / المحتوى</span>
                      <textarea
                        value={form.body_ar}
                        disabled={saving}
                        placeholder="الإجابة التي يقدمها الذكاء الاصطناعي بالعربية"
                        onChange={(event) =>
                          updateForm("body_ar", event.target.value)
                        }
                      />
                    </label>
                  </div>
                </div>

                <label className="ai-teaching-enabled-row">
                  <input
                    type="checkbox"
                    checked={Boolean(form.enabled)}
                    disabled={saving}
                    onChange={(event) =>
                      updateForm("enabled", event.target.checked)
                    }
                  />
                  <div>
                    <span>Enabled</span>
                    <span>
                      Disabled items stay saved but are hidden from the live AI
                      knowledge set.
                    </span>
                  </div>
                </label>

                {formError ? (
                  <p className="ai-teaching-form-error">{formError}</p>
                ) : null}
              </div>
            </div>

            <footer className="tz-dialog-actions">
              <AppButton
                variant="secondary"
                disabled={saving}
                onClick={closeEditor}
              >
                Cancel
              </AppButton>
              <AppButton variant="primary" loading={saving} onClick={handleSave}>
                {isEdit ? "Save changes" : "Create item"}
              </AppButton>
            </footer>
          </section>
        </div>
      ) : null}

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="Delete knowledge item"
        confirmLabel="Delete"
        cancelLabel="Cancel"
        confirmVariant="danger"
        loading={deleting}
        onConfirm={handleDelete}
        onCancel={() => (deleting ? null : setDeleteTarget(null))}
        message={
          deleteTarget ? (
            <p>
              Delete <strong>{deleteTarget.title_en || "this item"}</strong> from
              the <strong>{humanizeDepartment(deleteTarget.department)}</strong>{" "}
              knowledge? This cannot be undone.
            </p>
          ) : null
        }
      />
    </section>
  );
}
