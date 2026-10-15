import { useCallback, useEffect, useState } from "react";
import { AddOutlined, CloseOutlined, DeleteOutlineOutlined } from "@mui/icons-material";
import {
  archiveSupplierRequest,
  createPurchaseOrderRequest,
  createSupplierRequest,
  getPurchaseOrderRequest,
  listInventoryProductsRequest,
  listPurchaseOrdersRequest,
  listSuppliersRequest,
  receivePurchaseOrderItemsRequest,
  updatePurchaseOrderStatusRequest,
  updateSupplierRequest,
} from "../../api/client";
import { EmptyState, ErrorState, LoadingState } from "../../components/common";
import "./PurchasingPage.css";

const ORDER_STATUSES = ["draft", "sent", "partially_received", "received", "cancelled"];

function humanize(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function money(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : "0.00";
}

export default function PurchasingPage() {
  const [tab, setTab] = useState("orders");

  return (
    <div className="tz-screen tzv2-purch-page">
      <div className="tzv2-purch-head">
        <div className="seg" role="radiogroup" aria-label="Section">
          <label className="seg-opt">
            <input type="radio" name="tzv2-purch-tab" checked={tab === "orders"} onChange={() => setTab("orders")} />
            Purchase orders
          </label>
          <label className="seg-opt">
            <input type="radio" name="tzv2-purch-tab" checked={tab === "suppliers"} onChange={() => setTab("suppliers")} />
            Suppliers
          </label>
        </div>
      </div>

      {tab === "orders" ? <OrdersSection /> : <SuppliersSection />}
    </div>
  );
}

// ------------------------------------------------------------------ orders

function OrdersSection() {
  const [orders, setOrders] = useState([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [suppliers, setSuppliers] = useState([]);
  const [products, setProducts] = useState([]);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [supplierId, setSupplierId] = useState("");
  const [lines, setLines] = useState([{ product_id: "", quantity: 1, unit_cost: 0 }]);
  const [reference, setReference] = useState("");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  const [openOrderId, setOpenOrderId] = useState(null);
  const [openOrder, setOpenOrder] = useState(null);
  const [openOrderLoading, setOpenOrderLoading] = useState(false);
  const [receiveQuantities, setReceiveQuantities] = useState({});
  const [receiving, setReceiving] = useState(false);
  const [detailError, setDetailError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await listPurchaseOrdersRequest({ status: statusFilter || undefined });
      setOrders(Array.isArray(result?.items) ? result.items : []);
    } catch (requestError) {
      setError(requestError.message || "Purchase orders could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    listSuppliersRequest({ status: "active", limit: 200 })
      .then((result) => setSuppliers(Array.isArray(result?.items) ? result.items : []))
      .catch(() => {});
    listInventoryProductsRequest({ limit: 200 })
      .then((result) => setProducts(Array.isArray(result?.items) ? result.items : []))
      .catch(() => {});
  }, []);

  function openDialog() {
    setSupplierId(suppliers[0]?.id ? String(suppliers[0].id) : "");
    setLines([{ product_id: "", quantity: 1, unit_cost: 0 }]);
    setReference("");
    setFormError("");
    setDialogOpen(true);
  }

  function updateLine(index, patch) {
    setLines((current) => current.map((line, i) => (i === index ? { ...line, ...patch } : line)));
  }

  function addLine() {
    setLines((current) => [...current, { product_id: "", quantity: 1, unit_cost: 0 }]);
  }

  function removeLine(index) {
    setLines((current) => current.filter((_, i) => i !== index));
  }

  async function saveOrder(event) {
    event.preventDefault();
    const items = lines
      .filter((line) => line.product_id)
      .map((line) => ({
        product_id: Number(line.product_id),
        quantity: Number(line.quantity) || 0,
        unit_cost: Number(line.unit_cost) || 0,
      }));

    if (!supplierId || !items.length) {
      setFormError("Pick a supplier and at least one product line.");
      return;
    }

    setSaving(true);
    setFormError("");
    try {
      await createPurchaseOrderRequest({
        supplier_id: Number(supplierId),
        items,
        reference: reference.trim() || undefined,
      });
      setDialogOpen(false);
      await load();
    } catch (requestError) {
      setFormError(requestError.message || "The purchase order could not be created.");
    } finally {
      setSaving(false);
    }
  }

  async function openDetail(orderId) {
    setOpenOrderId(orderId);
    setOpenOrderLoading(true);
    setDetailError("");
    try {
      const order = await getPurchaseOrderRequest(orderId);
      setOpenOrder(order);
      setReceiveQuantities({});
    } catch (requestError) {
      setDetailError(requestError.message || "Could not load the purchase order.");
    } finally {
      setOpenOrderLoading(false);
    }
  }

  function closeDetail() {
    setOpenOrderId(null);
    setOpenOrder(null);
  }

  async function changeStatus(newStatus) {
    setDetailError("");
    try {
      const updated = await updatePurchaseOrderStatusRequest(openOrderId, newStatus);
      setOpenOrder(updated);
      await load();
    } catch (requestError) {
      setDetailError(requestError.message || "The status could not be changed.");
    }
  }

  async function submitReceive(event) {
    event.preventDefault();
    const items = Object.entries(receiveQuantities)
      .map(([itemId, quantity]) => ({ item_id: Number(itemId), quantity: Number(quantity) || 0 }))
      .filter((entry) => entry.quantity > 0);

    if (!items.length) {
      setDetailError("Enter a quantity for at least one line.");
      return;
    }

    setReceiving(true);
    setDetailError("");
    try {
      const updated = await receivePurchaseOrderItemsRequest(openOrderId, items);
      setOpenOrder(updated);
      setReceiveQuantities({});
      await load();
    } catch (requestError) {
      setDetailError(requestError.message || "Receiving failed.");
    } finally {
      setReceiving(false);
    }
  }

  if (loading && !orders.length) {
    return <LoadingState title="Loading purchase orders…" description="Retrieving open and past orders." />;
  }

  return (
    <>
      <div className="tzv2-purch-toolbar">
        <div className="seg" role="radiogroup" aria-label="Filter by status">
          <label className="seg-opt">
            <input type="radio" name="tzv2-purch-status" checked={statusFilter === ""} onChange={() => setStatusFilter("")} />
            All
          </label>
          {ORDER_STATUSES.map((value) => (
            <label className="seg-opt" key={value}>
              <input type="radio" name="tzv2-purch-status" checked={statusFilter === value} onChange={() => setStatusFilter(value)} />
              {humanize(value)}
            </label>
          ))}
        </div>
        <button type="button" className="btn btn-primary" onClick={openDialog} disabled={!suppliers.length}>
          <AddOutlined fontSize="small" /> New order
        </button>
      </div>

      {error ? (
        <ErrorState title="Could not load purchase orders" description={error} action={<button type="button" className="btn btn-primary" onClick={load}>Retry</button>} />
      ) : orders.length ? (
        <div className="tz-tablewrap tzv2-purch-tablewrap">
          <table className="table">
            <thead>
              <tr>
                <th>Supplier</th>
                <th>Reference</th>
                <th>Status</th>
                <th className="tz-num">Total</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((order) => (
                <tr key={order.id} className="tzv2-purch-row" onClick={() => openDetail(order.id)}>
                  <td><strong>{order.supplier_name}</strong></td>
                  <td className="tzv2-purch-muted">{order.reference || "—"}</td>
                  <td><span className="tag tag-outline">{humanize(order.status)}</span></td>
                  <td className="tz-num">{money(order.total)}</td>
                  <td className="tzv2-purch-muted">{(order.created_at || "").slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState title="No purchase orders yet" description="Raise one against a supplier to restock a product." />
      )}

      {dialogOpen ? (
        <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setDialogOpen(false); }}>
          <form className="dialog tzv2-purch-dialog" role="dialog" aria-modal="true" aria-labelledby="tzv2-po-title" onSubmit={saveOrder}>
            <div className="tzv2-purch-dialog-head">
              <span className="dialog-title" id="tzv2-po-title">New purchase order</span>
              <button type="button" className="btn btn-ghost btn-icon" aria-label="Close dialog" onClick={() => setDialogOpen(false)}>
                <CloseOutlined fontSize="small" />
              </button>
            </div>
            <div className="dialog-body tzv2-purch-dialog-body">
              <div className="tzv2-purch-field-row">
                <div className="field">
                  <label>Supplier</label>
                  <select className="input" value={supplierId} onChange={(event) => setSupplierId(event.target.value)} required>
                    <option value="">Select a supplier…</option>
                    {suppliers.map((supplier) => <option value={supplier.id} key={supplier.id}>{supplier.name}</option>)}
                  </select>
                </div>
                <div className="field">
                  <label>Reference (optional)</label>
                  <input className="input" value={reference} onChange={(event) => setReference(event.target.value)} maxLength={100} />
                </div>
              </div>

              <div className="tzv2-purch-lines">
                {lines.map((line, index) => (
                  <div className="tzv2-purch-line" key={index}>
                    <select className="input" value={line.product_id} onChange={(event) => updateLine(index, { product_id: event.target.value })}>
                      <option value="">Select a product…</option>
                      {products.map((product) => <option value={product.id} key={product.id}>{product.name}{product.sku ? ` (${product.sku})` : ""}</option>)}
                    </select>
                    <input type="number" className="input" min="1" value={line.quantity} onChange={(event) => updateLine(index, { quantity: event.target.value })} placeholder="Qty" />
                    <input type="number" className="input" min="0" step="0.01" value={line.unit_cost} onChange={(event) => updateLine(index, { unit_cost: event.target.value })} placeholder="Unit cost" />
                    <button type="button" className="btn btn-ghost btn-icon" aria-label="Remove line" onClick={() => removeLine(index)} disabled={lines.length === 1}>
                      <DeleteOutlineOutlined fontSize="small" />
                    </button>
                  </div>
                ))}
                <button type="button" className="btn btn-ghost" onClick={addLine}>+ Add line</button>
              </div>

              {formError ? <p className="tzv2-purch-form-error">{formError}</p> : null}
            </div>
            <div className="dialog-actions">
              <button type="button" className="btn btn-secondary" disabled={saving} onClick={() => setDialogOpen(false)}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? "Creating…" : "Create order"}</button>
            </div>
          </form>
        </div>
      ) : null}

      {openOrderId ? (
        <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeDetail(); }}>
          <section className="dialog tzv2-purch-dialog" role="dialog" aria-modal="true" aria-labelledby="tzv2-po-detail-title">
            <div className="tzv2-purch-dialog-head">
              <span className="dialog-title" id="tzv2-po-detail-title">
                {openOrder ? `Order with ${openOrder.supplier_name}` : "Purchase order"}
              </span>
              <button type="button" className="btn btn-ghost btn-icon" aria-label="Close dialog" onClick={closeDetail}>
                <CloseOutlined fontSize="small" />
              </button>
            </div>
            <div className="dialog-body tzv2-purch-dialog-body">
              {openOrderLoading || !openOrder ? (
                <p className="tzv2-purch-muted">Loading…</p>
              ) : (
                <>
                  <div className="tzv2-purch-detail-head">
                    <span className="tag tag-outline">{humanize(openOrder.status)}</span>
                    <span className="tzv2-purch-muted">Total {money(openOrder.total)}</span>
                  </div>

                  <table className="table tzv2-purch-items">
                    <thead>
                      <tr>
                        <th>Product</th>
                        <th className="tz-num">Ordered</th>
                        <th className="tz-num">Received</th>
                        {["sent", "partially_received"].includes(openOrder.status) ? <th className="tz-num">Receive now</th> : null}
                      </tr>
                    </thead>
                    <tbody>
                      {(openOrder.items || []).map((item) => {
                        const remaining = item.quantity_ordered - item.quantity_received;
                        return (
                          <tr key={item.id}>
                            <td>{item.product_name}</td>
                            <td className="tz-num">{item.quantity_ordered}</td>
                            <td className="tz-num">{item.quantity_received}</td>
                            {["sent", "partially_received"].includes(openOrder.status) ? (
                              <td className="tz-num">
                                {remaining > 0 ? (
                                  <input
                                    type="number"
                                    className="input tzv2-purch-receive-input"
                                    min="0"
                                    max={remaining}
                                    placeholder="0"
                                    value={receiveQuantities[item.id] ?? ""}
                                    onChange={(event) => setReceiveQuantities((current) => ({ ...current, [item.id]: event.target.value }))}
                                  />
                                ) : <span className="tzv2-purch-muted">Done</span>}
                              </td>
                            ) : null}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>

                  {detailError ? <p className="tzv2-purch-form-error">{detailError}</p> : null}

                  <div className="tzv2-purch-detail-actions">
                    {openOrder.status === "draft" ? (
                      <>
                        <button type="button" className="btn btn-primary" onClick={() => changeStatus("sent")}>Mark as sent</button>
                        <button type="button" className="btn btn-secondary" onClick={() => changeStatus("cancelled")}>Cancel order</button>
                      </>
                    ) : null}
                    {openOrder.status === "sent" ? (
                      <button type="button" className="btn btn-secondary" onClick={() => changeStatus("cancelled")}>Cancel order</button>
                    ) : null}
                    {["sent", "partially_received"].includes(openOrder.status) ? (
                      <button type="button" className="btn btn-primary" disabled={receiving} onClick={submitReceive}>
                        {receiving ? "Receiving…" : "Receive stock"}
                      </button>
                    ) : null}
                  </div>
                </>
              )}
            </div>
          </section>
        </div>
      ) : null}
    </>
  );
}

// --------------------------------------------------------------- suppliers

function SuppliersSection() {
  const [suppliers, setSuppliers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState({ name: "", contact_name: "", email: "", phone: "" });
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  function load() {
    setLoading(true);
    setError("");
    listSuppliersRequest({ limit: 200 })
      .then((result) => setSuppliers(Array.isArray(result?.items) ? result.items : []))
      .catch((requestError) => setError(requestError.message || "Suppliers could not be loaded."))
      .finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, []);

  function startNew() {
    setEditingId("new");
    setForm({ name: "", contact_name: "", email: "", phone: "" });
    setFormError("");
  }

  function startEdit(supplier) {
    setEditingId(supplier.id);
    setForm({
      name: supplier.name || "",
      contact_name: supplier.contact_name || "",
      email: supplier.email || "",
      phone: supplier.phone || "",
    });
    setFormError("");
  }

  async function save(event) {
    event.preventDefault();
    if (!form.name.trim()) return;
    setSaving(true);
    setFormError("");
    try {
      if (editingId === "new") {
        await createSupplierRequest(form);
      } else {
        await updateSupplierRequest(editingId, form);
      }
      setEditingId(null);
      load();
    } catch (requestError) {
      setFormError(requestError.message || "The supplier could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  async function archive(supplierId) {
    setError("");
    try {
      await archiveSupplierRequest(supplierId);
      load();
    } catch (requestError) {
      setError(requestError.message || "The supplier could not be archived.");
    }
  }

  if (loading && !suppliers.length) {
    return <LoadingState title="Loading suppliers…" description="Retrieving the vendor list." />;
  }

  return (
    <>
      {!editingId ? (
        <div className="tzv2-purch-toolbar">
          <button type="button" className="btn btn-primary" onClick={startNew}>
            <AddOutlined fontSize="small" /> New supplier
          </button>
        </div>
      ) : null}

      {error ? <ErrorState title="Could not load suppliers" description={error} action={<button type="button" className="btn btn-primary" onClick={load}>Retry</button>} /> : null}

      {editingId ? (
        <form className="tzv2-purch-supplier-form" onSubmit={save}>
          <div className="tzv2-purch-field-row">
            <div className="field">
              <label>Name</label>
              <input className="input" value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} required autoFocus maxLength={200} />
            </div>
            <div className="field">
              <label>Contact name</label>
              <input className="input" value={form.contact_name} onChange={(event) => setForm((current) => ({ ...current, contact_name: event.target.value }))} maxLength={200} />
            </div>
          </div>
          <div className="tzv2-purch-field-row">
            <div className="field">
              <label>Email</label>
              <input type="email" className="input" value={form.email} onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))} maxLength={200} />
            </div>
            <div className="field">
              <label>Phone</label>
              <input className="input" value={form.phone} onChange={(event) => setForm((current) => ({ ...current, phone: event.target.value }))} maxLength={40} />
            </div>
          </div>
          {formError ? <p className="tzv2-purch-form-error">{formError}</p> : null}
          <div className="tzv2-purch-form-actions">
            <button type="submit" className="btn btn-primary" disabled={saving || !form.name.trim()}>{saving ? "Saving…" : "Save"}</button>
            <button type="button" className="btn btn-secondary" onClick={() => setEditingId(null)}>Cancel</button>
          </div>
        </form>
      ) : null}

      {!error && suppliers.length ? (
        <div className="tz-tablewrap tzv2-purch-tablewrap">
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Contact</th>
                <th>Email</th>
                <th>Phone</th>
                <th>Status</th>
                <th style={{ width: 120 }} />
              </tr>
            </thead>
            <tbody>
              {suppliers.map((supplier) => (
                <tr key={supplier.id}>
                  <td><strong>{supplier.name}</strong></td>
                  <td className="tzv2-purch-muted">{supplier.contact_name || "—"}</td>
                  <td className="tzv2-purch-muted">{supplier.email || "—"}</td>
                  <td className="tzv2-purch-muted">{supplier.phone || "—"}</td>
                  <td>{supplier.status === "archived" ? <span className="tag">Archived</span> : <span className="tag tag-outline">Active</span>}</td>
                  <td>
                    <div className="tzv2-purch-row-actions">
                      <button type="button" className="btn btn-ghost" onClick={() => startEdit(supplier)}>Edit</button>
                      {supplier.status !== "archived" ? (
                        <button type="button" className="btn btn-ghost" onClick={() => archive(supplier.id)}>Archive</button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : !error && !loading ? (
        <EmptyState title="No suppliers yet" description="Add a vendor before raising a purchase order." />
      ) : null}
    </>
  );
}
