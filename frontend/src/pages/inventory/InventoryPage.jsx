import { useCallback, useEffect, useState } from "react";
import { CloseOutlined, TuneOutlined } from "@mui/icons-material";
import {
  getInventorySummaryRequest,
  listInventoryProductsRequest,
  listLowStockProductsRequest,
  listStockMovementsRequest,
  recordStockMovementRequest,
  updateReorderPointRequest,
} from "../../api/client";
import { EmptyState, ErrorState, LoadingState } from "../../components/common";
import "./InventoryPage.css";

const MOVEMENT_TYPES = ["receipt", "sale", "return", "adjustment", "write_off"];

// Which direction each type nudges the quantity delta toward when a person
// opens the form fresh -- a receipt is almost always a gain and a sale is
// almost always a loss, so the sign field defaults to match instead of
// silently accepting whatever sign was left over from the last movement typed.
const DEFAULT_SIGN = {
  receipt: 1,
  sale: -1,
  return: 1,
  adjustment: 1,
  write_off: -1,
};

function humanize(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

export default function InventoryPage() {
  const [summary, setSummary] = useState(null);
  const [lowStock, setLowStock] = useState([]);
  const [products, setProducts] = useState([]);
  const [movements, setMovements] = useState([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [movementProduct, setMovementProduct] = useState(null);
  const [movementType, setMovementType] = useState("receipt");
  const [movementQuantity, setMovementQuantity] = useState("");
  const [movementReason, setMovementReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  const [reorderProduct, setReorderProduct] = useState(null);
  const [reorderValue, setReorderValue] = useState("");
  const [reorderSaving, setReorderSaving] = useState(false);
  const [reorderError, setReorderError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [summaryResult, lowStockResult, productsResult, movementsResult] = await Promise.all([
        getInventorySummaryRequest(),
        listLowStockProductsRequest(),
        listInventoryProductsRequest({ search }),
        listStockMovementsRequest({ limit: 20 }),
      ]);
      setSummary(summaryResult);
      setLowStock(Array.isArray(lowStockResult?.items) ? lowStockResult.items : []);
      setProducts(Array.isArray(productsResult?.items) ? productsResult.items : []);
      setMovements(Array.isArray(movementsResult?.items) ? movementsResult.items : []);
    } catch (requestError) {
      setError(requestError.message || "Inventory could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [search]);

  useEffect(() => { load(); }, [load]);

  function openMovementForm(product) {
    setMovementProduct(product);
    setMovementType("receipt");
    setMovementQuantity("");
    setMovementReason("");
    setFormError("");
  }

  function closeMovementForm() {
    setMovementProduct(null);
  }

  async function saveMovement(event) {
    event.preventDefault();
    const magnitude = Math.abs(Number(movementQuantity));
    if (!magnitude) {
      setFormError("Enter a quantity greater than zero.");
      return;
    }
    setSaving(true);
    setFormError("");
    try {
      await recordStockMovementRequest({
        product_id: movementProduct.id,
        movement_type: movementType,
        quantity_delta: magnitude * (DEFAULT_SIGN[movementType] || 1),
        reason: movementReason.trim() || undefined,
      });
      closeMovementForm();
      await load();
    } catch (requestError) {
      setFormError(requestError.message || "The movement could not be recorded.");
    } finally {
      setSaving(false);
    }
  }

  function openReorderForm(product) {
    setReorderProduct(product);
    setReorderValue(product.reorder_point ?? "");
    setReorderError("");
  }

  async function saveReorderPoint(event) {
    event.preventDefault();
    setReorderSaving(true);
    setReorderError("");
    try {
      await updateReorderPointRequest(
        reorderProduct.id,
        reorderValue === "" ? null : Number(reorderValue),
      );
      setReorderProduct(null);
      await load();
    } catch (requestError) {
      setReorderError(requestError.message || "The reorder point could not be saved.");
    } finally {
      setReorderSaving(false);
    }
  }

  if (loading && !summary) {
    return (
      <div className="tz-screen tzv2-inventory-page">
        <LoadingState title="Loading inventory…" description="Retrieving stock levels and recent movements." />
      </div>
    );
  }

  if (error && !summary) {
    return (
      <div className="tz-screen tzv2-inventory-page">
        <ErrorState title="Could not load inventory" description={error} action={<button type="button" className="btn btn-primary" onClick={load}>Retry</button>} />
      </div>
    );
  }

  return (
    <div className="tz-screen tzv2-inventory-page">
      <div className="tzv2-inv-head">
        <span className="tz-kick tzv2-inv-kick">
          {summary?.total_products ?? 0} products · {summary?.total_units ?? 0} units on hand
        </span>
        <input
          className="input tzv2-inv-search"
          placeholder="Search products…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>

      <div className="tzv2-inv-summary">
        <div className="tzv2-inv-card">
          <span className="tzv2-inv-card-label">Total products</span>
          <strong className="tzv2-inv-card-value">{summary?.total_products ?? 0}</strong>
        </div>
        <div className="tzv2-inv-card">
          <span className="tzv2-inv-card-label">Units on hand</span>
          <strong className="tzv2-inv-card-value">{summary?.total_units ?? 0}</strong>
        </div>
        <div className="tzv2-inv-card tzv2-inv-card-warning">
          <span className="tzv2-inv-card-label">Low stock</span>
          <strong className="tzv2-inv-card-value">{summary?.low_stock_count ?? 0}</strong>
        </div>
        <div className="tzv2-inv-card tzv2-inv-card-danger">
          <span className="tzv2-inv-card-label">Out of stock</span>
          <strong className="tzv2-inv-card-value">{summary?.out_of_stock_count ?? 0}</strong>
        </div>
      </div>

      {lowStock.length ? (
        <div className="tzv2-inv-section">
          <h2 className="tzv2-inv-section-title">Needs reordering</h2>
          <ul className="tzv2-inv-lowstock-list">
            {lowStock.map((product) => (
              <li className="tzv2-inv-lowstock-row" key={product.id}>
                <span className="tzv2-inv-lowstock-name">{product.name}</span>
                <span className="tzv2-inv-lowstock-qty">
                  {product.stock_quantity ?? 0} on hand · reorder at {product.reorder_point}
                </span>
                <button type="button" className="btn btn-ghost" onClick={() => openMovementForm(product)}>
                  Record movement
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="tzv2-inv-section">
        <h2 className="tzv2-inv-section-title">Products</h2>
        {products.length ? (
          <div className="tz-tablewrap tzv2-inv-tablewrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Product</th>
                  <th>SKU</th>
                  <th className="tz-num">On hand</th>
                  <th className="tz-num">Reorder at</th>
                  <th>Status</th>
                  <th style={{ width: 200 }} />
                </tr>
              </thead>
              <tbody>
                {products.map((product) => (
                  <tr key={product.id}>
                    <td><strong>{product.name}</strong></td>
                    <td className="tzv2-inv-muted">{product.sku || "—"}</td>
                    <td className="tz-num">{product.stock_quantity ?? 0}</td>
                    <td className="tz-num">{product.reorder_point ?? "—"}</td>
                    <td>
                      {!product.in_stock ? (
                        <span className="tag tzv2-inv-tag-danger">Out of stock</span>
                      ) : product.reorder_point !== null && product.reorder_point !== undefined
                        && (product.stock_quantity ?? 0) <= product.reorder_point ? (
                        <span className="tag tzv2-inv-tag-warning">Low</span>
                      ) : (
                        <span className="tag tag-outline">In stock</span>
                      )}
                    </td>
                    <td>
                      <div className="tzv2-inv-row-actions">
                        <button type="button" className="btn btn-ghost" onClick={() => openMovementForm(product)}>
                          Record movement
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost btn-icon"
                          aria-label={`Set reorder point for ${product.name}`}
                          title="Set reorder point"
                          onClick={() => openReorderForm(product)}
                        >
                          <TuneOutlined fontSize="small" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="No products found" description="No active product matches this search." />
        )}
      </div>

      <div className="tzv2-inv-section">
        <h2 className="tzv2-inv-section-title">Recent movements</h2>
        {movements.length ? (
          <div className="tz-tablewrap tzv2-inv-tablewrap">
            <table className="table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Product</th>
                  <th>Type</th>
                  <th className="tz-num">Change</th>
                  <th className="tz-num">Balance after</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {movements.map((movement) => (
                  <tr key={movement.id}>
                    <td className="tzv2-inv-muted">{formatDateTime(movement.created_at)}</td>
                    <td>{movement.product_name}</td>
                    <td>{humanize(movement.movement_type)}</td>
                    <td className={`tz-num ${movement.quantity_delta < 0 ? "tzv2-inv-negative" : "tzv2-inv-positive"}`}>
                      {movement.quantity_delta > 0 ? `+${movement.quantity_delta}` : movement.quantity_delta}
                    </td>
                    <td className="tz-num">{movement.quantity_after}</td>
                    <td className="tzv2-inv-muted">{movement.reason || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="No movements yet" description="Stock receipts, sales and adjustments will appear here." />
        )}
      </div>

      {movementProduct ? (
        <div
          className="dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => { if (event.target === event.currentTarget) closeMovementForm(); }}
        >
          <form className="dialog" role="dialog" aria-modal="true" aria-labelledby="tzv2-inv-movement-title" onSubmit={saveMovement}>
            <div className="tzv2-inv-dialog-head">
              <span className="dialog-title" id="tzv2-inv-movement-title">
                Record a movement — {movementProduct.name}
              </span>
              <button type="button" className="btn btn-ghost btn-icon" aria-label="Close dialog" onClick={closeMovementForm}>
                <CloseOutlined fontSize="small" />
              </button>
            </div>
            <div className="dialog-body tzv2-inv-dialog-body">
              <p className="tzv2-inv-dialog-current">{movementProduct.stock_quantity ?? 0} currently on hand</p>
              <div className="tzv2-inv-field-row">
                <div className="field">
                  <label>Type</label>
                  <select
                    className="input"
                    value={movementType}
                    onChange={(event) => setMovementType(event.target.value)}
                  >
                    {MOVEMENT_TYPES.map((type) => (
                      <option value={type} key={type}>{humanize(type)}</option>
                    ))}
                  </select>
                </div>
                <div className="field">
                  <label>Quantity</label>
                  <input
                    type="number"
                    min="1"
                    className="input"
                    value={movementQuantity}
                    onChange={(event) => setMovementQuantity(event.target.value)}
                    autoFocus
                    required
                  />
                </div>
              </div>
              <p className="tzv2-inv-muted">
                {(DEFAULT_SIGN[movementType] || 1) > 0
                  ? "This adds to the quantity on hand."
                  : "This takes from the quantity on hand."}
              </p>
              <div className="field">
                <label>Reason (optional)</label>
                <input
                  className="input"
                  value={movementReason}
                  onChange={(event) => setMovementReason(event.target.value)}
                  maxLength={500}
                />
              </div>
              {formError ? <p className="tzv2-inv-form-error">{formError}</p> : null}
            </div>
            <div className="dialog-actions">
              <button type="button" className="btn btn-secondary" disabled={saving} onClick={closeMovementForm}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? "Saving…" : "Record movement"}</button>
            </div>
          </form>
        </div>
      ) : null}

      {reorderProduct ? (
        <div
          className="dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => { if (event.target === event.currentTarget && !reorderSaving) setReorderProduct(null); }}
        >
          <form className="dialog" role="dialog" aria-modal="true" aria-labelledby="tzv2-inv-reorder-title" onSubmit={saveReorderPoint}>
            <div className="tzv2-inv-dialog-head">
              <span className="dialog-title" id="tzv2-inv-reorder-title">
                Reorder point — {reorderProduct.name}
              </span>
              <button type="button" className="btn btn-ghost btn-icon" aria-label="Close dialog" onClick={() => setReorderProduct(null)}>
                <CloseOutlined fontSize="small" />
              </button>
            </div>
            <div className="dialog-body">
              <div className="field">
                <label>Alert when stock falls to or below</label>
                <input
                  type="number"
                  min="0"
                  className="input"
                  value={reorderValue}
                  placeholder="No alert set"
                  onChange={(event) => setReorderValue(event.target.value)}
                  autoFocus
                />
              </div>
              {reorderError ? <p className="tzv2-inv-form-error">{reorderError}</p> : null}
            </div>
            <div className="dialog-actions">
              <button type="button" className="btn btn-secondary" disabled={reorderSaving} onClick={() => setReorderProduct(null)}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={reorderSaving}>{reorderSaving ? "Saving…" : "Save"}</button>
            </div>
          </form>
        </div>
      ) : null}
    </div>
  );
}
