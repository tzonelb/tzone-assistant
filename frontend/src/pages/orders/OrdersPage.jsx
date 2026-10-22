import { useCallback, useEffect, useState } from "react";
import { AddOutlined, CloseOutlined, DeleteOutlineOutlined } from "@mui/icons-material";
import {
  createOrderRequest,
  fulfilOrderItemsRequest,
  getCustomersRequest,
  getOrderRequest,
  listInventoryProductsRequest,
  listOrdersRequest,
  updateOrderStatusRequest,
} from "../../api/client";
import { EmptyState, ErrorState, LoadingState } from "../../components/common";
import "./OrdersPage.css";

const ORDER_STATUSES = ["draft", "confirmed", "partially_fulfilled", "fulfilled", "cancelled"];
const FULFILLABLE = ["confirmed", "partially_fulfilled"];

function humanize(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function money(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : "0.00";
}

export default function OrdersPage() {
  const [orders, setOrders] = useState([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [customers, setCustomers] = useState([]);
  const [products, setProducts] = useState([]);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [customerId, setCustomerId] = useState("");
  const [lines, setLines] = useState([{ product_id: "", quantity: 1, unit_price: 0 }]);
  const [reference, setReference] = useState("");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  const [openOrderId, setOpenOrderId] = useState(null);
  const [openOrder, setOpenOrder] = useState(null);
  const [openOrderLoading, setOpenOrderLoading] = useState(false);
  const [fulfilQuantities, setFulfilQuantities] = useState({});
  const [fulfilling, setFulfilling] = useState(false);
  const [detailError, setDetailError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await listOrdersRequest({ status: statusFilter || undefined });
      setOrders(Array.isArray(result?.items) ? result.items : []);
    } catch (requestError) {
      setError(requestError.message || "Orders could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    getCustomersRequest({ limit: 100 })
      .then((result) => setCustomers(Array.isArray(result?.items) ? result.items : []))
      .catch(() => {});
    listInventoryProductsRequest({ limit: 200 })
      .then((result) => setProducts(Array.isArray(result?.items) ? result.items : []))
      .catch(() => {});
  }, []);

  function openDialog() {
    setCustomerId("");
    setLines([{ product_id: "", quantity: 1, unit_price: 0 }]);
    setReference("");
    setFormError("");
    setDialogOpen(true);
  }

  function updateLine(index, patch) {
    setLines((current) => current.map((line, i) => (i === index ? { ...line, ...patch } : line)));
  }

  function addLine() {
    setLines((current) => [...current, { product_id: "", quantity: 1, unit_price: 0 }]);
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
        unit_price: Number(line.unit_price) || 0,
      }));

    if (!customerId || !items.length) {
      setFormError("Pick a customer and at least one product line.");
      return;
    }

    setSaving(true);
    setFormError("");
    try {
      await createOrderRequest({
        customer_id: Number(customerId),
        items,
        reference: reference.trim() || undefined,
      });
      setDialogOpen(false);
      await load();
    } catch (requestError) {
      setFormError(requestError.message || "The order could not be created.");
    } finally {
      setSaving(false);
    }
  }

  async function openDetail(orderId) {
    setOpenOrderId(orderId);
    setOpenOrderLoading(true);
    setDetailError("");
    try {
      const order = await getOrderRequest(orderId);
      setOpenOrder(order);
      setFulfilQuantities({});
    } catch (requestError) {
      setDetailError(requestError.message || "Could not load the order.");
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
      const updated = await updateOrderStatusRequest(openOrderId, newStatus);
      setOpenOrder(updated);
      await load();
    } catch (requestError) {
      setDetailError(requestError.message || "The status could not be changed.");
    }
  }

  async function submitFulfil(event) {
    event.preventDefault();
    const items = Object.entries(fulfilQuantities)
      .map(([itemId, quantity]) => ({ item_id: Number(itemId), quantity: Number(quantity) || 0 }))
      .filter((entry) => entry.quantity > 0);

    if (!items.length) {
      setDetailError("Enter a quantity for at least one line.");
      return;
    }

    setFulfilling(true);
    setDetailError("");
    try {
      const updated = await fulfilOrderItemsRequest(openOrderId, items);
      setOpenOrder(updated);
      setFulfilQuantities({});
      await load();
    } catch (requestError) {
      setDetailError(requestError.message || "Fulfilling the order failed.");
    } finally {
      setFulfilling(false);
    }
  }

  if (loading && !orders.length) {
    return (
      <div className="tz-screen tzv2-orders-page">
        <LoadingState title="Loading orders…" description="Retrieving open and past customer orders." />
      </div>
    );
  }

  return (
    <div className="tz-screen tzv2-orders-page">
      <div className="tzv2-orders-toolbar">
        <div className="seg" role="radiogroup" aria-label="Filter by status">
          <label className="seg-opt">
            <input type="radio" name="tzv2-orders-status" checked={statusFilter === ""} onChange={() => setStatusFilter("")} />
            All
          </label>
          {ORDER_STATUSES.map((value) => (
            <label className="seg-opt" key={value}>
              <input type="radio" name="tzv2-orders-status" checked={statusFilter === value} onChange={() => setStatusFilter(value)} />
              {humanize(value)}
            </label>
          ))}
        </div>
        <button type="button" className="btn btn-primary" onClick={openDialog} disabled={!customers.length}>
          <AddOutlined fontSize="small" /> New order
        </button>
      </div>

      {error ? (
        <ErrorState title="Could not load orders" description={error} action={<button type="button" className="btn btn-primary" onClick={load}>Retry</button>} />
      ) : orders.length ? (
        <div className="tz-tablewrap tzv2-orders-tablewrap">
          <table className="table">
            <thead>
              <tr>
                <th>Customer</th>
                <th>Reference</th>
                <th>Status</th>
                <th className="tz-num">Total</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((order) => (
                <tr key={order.id} className="tzv2-orders-row" onClick={() => openDetail(order.id)}>
                  <td><strong>{order.customer_name}</strong></td>
                  <td className="tzv2-orders-muted">{order.reference || "—"}</td>
                  <td><span className="tag tag-outline">{humanize(order.status)}</span></td>
                  <td className="tz-num">{money(order.total)}</td>
                  <td className="tzv2-orders-muted">{(order.created_at || "").slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState title="No orders yet" description="Create one for a customer to sell against your catalogue." />
      )}

      {dialogOpen ? (
        <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setDialogOpen(false); }}>
          <form className="dialog tzv2-orders-dialog" role="dialog" aria-modal="true" aria-labelledby="tzv2-order-title" onSubmit={saveOrder}>
            <div className="tzv2-orders-dialog-head">
              <span className="dialog-title" id="tzv2-order-title">New order</span>
              <button type="button" className="btn btn-ghost btn-icon" aria-label="Close dialog" onClick={() => setDialogOpen(false)}>
                <CloseOutlined fontSize="small" />
              </button>
            </div>
            <div className="dialog-body tzv2-orders-dialog-body">
              <div className="tzv2-orders-field-row">
                <div className="field">
                  <label>Customer</label>
                  <select className="input" value={customerId} onChange={(event) => setCustomerId(event.target.value)} required>
                    <option value="">Select a customer…</option>
                    {customers.map((customer) => (
                      <option value={customer.id} key={customer.id}>
                        {customer.internal_name || customer.display_name || `Customer ${customer.id}`}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="field">
                  <label>Reference (optional)</label>
                  <input className="input" value={reference} onChange={(event) => setReference(event.target.value)} maxLength={100} />
                </div>
              </div>

              <div className="tzv2-orders-lines">
                {lines.map((line, index) => (
                  <div className="tzv2-orders-line" key={index}>
                    <select className="input" value={line.product_id} onChange={(event) => updateLine(index, { product_id: event.target.value })}>
                      <option value="">Select a product…</option>
                      {products.map((product) => <option value={product.id} key={product.id}>{product.name}{product.sku ? ` (${product.sku})` : ""}</option>)}
                    </select>
                    <input type="number" className="input" min="1" value={line.quantity} onChange={(event) => updateLine(index, { quantity: event.target.value })} placeholder="Qty" />
                    <input type="number" className="input" min="0" step="0.01" value={line.unit_price} onChange={(event) => updateLine(index, { unit_price: event.target.value })} placeholder="Unit price" />
                    <button type="button" className="btn btn-ghost btn-icon" aria-label="Remove line" onClick={() => removeLine(index)} disabled={lines.length === 1}>
                      <DeleteOutlineOutlined fontSize="small" />
                    </button>
                  </div>
                ))}
                <button type="button" className="btn btn-ghost" onClick={addLine}>+ Add line</button>
              </div>

              {formError ? <p className="tzv2-orders-form-error">{formError}</p> : null}
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
          <section className="dialog tzv2-orders-dialog" role="dialog" aria-modal="true" aria-labelledby="tzv2-order-detail-title">
            <div className="tzv2-orders-dialog-head">
              <span className="dialog-title" id="tzv2-order-detail-title">
                {openOrder ? `Order for ${openOrder.customer_name}` : "Order"}
              </span>
              <button type="button" className="btn btn-ghost btn-icon" aria-label="Close dialog" onClick={closeDetail}>
                <CloseOutlined fontSize="small" />
              </button>
            </div>
            <div className="dialog-body tzv2-orders-dialog-body">
              {openOrderLoading || !openOrder ? (
                <p className="tzv2-orders-muted">Loading…</p>
              ) : (
                <>
                  <div className="tzv2-orders-detail-head">
                    <span className="tag tag-outline">{humanize(openOrder.status)}</span>
                    <span className="tzv2-orders-muted">Total {money(openOrder.total)}</span>
                  </div>

                  <table className="table tzv2-orders-items">
                    <thead>
                      <tr>
                        <th>Product</th>
                        <th className="tz-num">Ordered</th>
                        <th className="tz-num">Fulfilled</th>
                        {FULFILLABLE.includes(openOrder.status) ? <th className="tz-num">Fulfil now</th> : null}
                      </tr>
                    </thead>
                    <tbody>
                      {(openOrder.items || []).map((item) => {
                        const remaining = item.quantity - item.quantity_fulfilled;
                        return (
                          <tr key={item.id}>
                            <td>{item.product_name}</td>
                            <td className="tz-num">{item.quantity}</td>
                            <td className="tz-num">{item.quantity_fulfilled}</td>
                            {FULFILLABLE.includes(openOrder.status) ? (
                              <td className="tz-num">
                                {remaining > 0 ? (
                                  <input
                                    type="number"
                                    className="input tzv2-orders-fulfil-input"
                                    min="0"
                                    max={remaining}
                                    placeholder="0"
                                    value={fulfilQuantities[item.id] ?? ""}
                                    onChange={(event) => setFulfilQuantities((current) => ({ ...current, [item.id]: event.target.value }))}
                                  />
                                ) : <span className="tzv2-orders-muted">Done</span>}
                              </td>
                            ) : null}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>

                  {detailError ? <p className="tzv2-orders-form-error">{detailError}</p> : null}

                  <div className="tzv2-orders-detail-actions">
                    {openOrder.status === "draft" ? (
                      <>
                        <button type="button" className="btn btn-primary" onClick={() => changeStatus("confirmed")}>Confirm order</button>
                        <button type="button" className="btn btn-secondary" onClick={() => changeStatus("cancelled")}>Cancel order</button>
                      </>
                    ) : null}
                    {openOrder.status === "confirmed" ? (
                      <button type="button" className="btn btn-secondary" onClick={() => changeStatus("cancelled")}>Cancel order</button>
                    ) : null}
                    {FULFILLABLE.includes(openOrder.status) ? (
                      <button type="button" className="btn btn-primary" disabled={fulfilling} onClick={submitFulfil}>
                        {fulfilling ? "Fulfilling…" : "Fulfil"}
                      </button>
                    ) : null}
                  </div>
                </>
              )}
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
