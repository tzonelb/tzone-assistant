import { useCallback, useEffect, useState } from "react";
import { AddOutlined, CloseOutlined, DeleteOutlineOutlined } from "@mui/icons-material";
import {
  createInvoiceRequest,
  getCustomersRequest,
  getInvoiceRequest,
  listInvoicesRequest,
  recordPaymentRequest,
  updateInvoiceStatusRequest,
} from "../../api/client";
import { EmptyState, ErrorState, LoadingState } from "../../components/common";
import "./InvoicesPage.css";

const INVOICE_STATUSES = ["draft", "sent", "partially_paid", "paid", "void"];
const PAYABLE = ["sent", "partially_paid"];
const PAYMENT_METHODS = ["cash", "card", "transfer", "other"];

function humanize(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function money(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : "0.00";
}

export default function InvoicesPage() {
  const [invoices, setInvoices] = useState([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [customers, setCustomers] = useState([]);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [customerId, setCustomerId] = useState("");
  const [lines, setLines] = useState([{ description: "", quantity: 1, unit_price: 0 }]);
  const [dueDate, setDueDate] = useState("");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  const [openInvoiceId, setOpenInvoiceId] = useState(null);
  const [openInvoice, setOpenInvoice] = useState(null);
  const [openInvoiceLoading, setOpenInvoiceLoading] = useState(false);
  const [detailError, setDetailError] = useState("");

  const [paymentAmount, setPaymentAmount] = useState("");
  const [paymentMethod, setPaymentMethod] = useState("cash");
  const [recordingPayment, setRecordingPayment] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await listInvoicesRequest({ status: statusFilter || undefined });
      setInvoices(Array.isArray(result?.items) ? result.items : []);
    } catch (requestError) {
      setError(requestError.message || "Invoices could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    getCustomersRequest({ limit: 100 })
      .then((result) => setCustomers(Array.isArray(result?.items) ? result.items : []))
      .catch(() => {});
  }, []);

  function openDialog() {
    setCustomerId("");
    setLines([{ description: "", quantity: 1, unit_price: 0 }]);
    setDueDate("");
    setFormError("");
    setDialogOpen(true);
  }

  function updateLine(index, patch) {
    setLines((current) => current.map((line, i) => (i === index ? { ...line, ...patch } : line)));
  }

  function addLine() {
    setLines((current) => [...current, { description: "", quantity: 1, unit_price: 0 }]);
  }

  function removeLine(index) {
    setLines((current) => current.filter((_, i) => i !== index));
  }

  async function saveInvoice(event) {
    event.preventDefault();
    const items = lines
      .filter((line) => line.description.trim())
      .map((line) => ({
        description: line.description.trim(),
        quantity: Number(line.quantity) || 1,
        unit_price: Number(line.unit_price) || 0,
      }));

    if (!customerId || !items.length) {
      setFormError("Pick a customer and at least one line with a description.");
      return;
    }

    setSaving(true);
    setFormError("");
    try {
      await createInvoiceRequest({
        customer_id: Number(customerId),
        items,
        due_date: dueDate || undefined,
      });
      setDialogOpen(false);
      await load();
    } catch (requestError) {
      setFormError(requestError.message || "The invoice could not be created.");
    } finally {
      setSaving(false);
    }
  }

  async function openDetail(invoiceId) {
    setOpenInvoiceId(invoiceId);
    setOpenInvoiceLoading(true);
    setDetailError("");
    try {
      const invoice = await getInvoiceRequest(invoiceId);
      setOpenInvoice(invoice);
      setPaymentAmount(invoice.balance > 0 ? String(invoice.balance) : "");
      setPaymentMethod("cash");
    } catch (requestError) {
      setDetailError(requestError.message || "Could not load the invoice.");
    } finally {
      setOpenInvoiceLoading(false);
    }
  }

  function closeDetail() {
    setOpenInvoiceId(null);
    setOpenInvoice(null);
  }

  async function changeStatus(newStatus) {
    setDetailError("");
    try {
      const updated = await updateInvoiceStatusRequest(openInvoiceId, newStatus);
      setOpenInvoice(updated);
      await load();
    } catch (requestError) {
      setDetailError(requestError.message || "The status could not be changed.");
    }
  }

  async function submitPayment(event) {
    event.preventDefault();
    const amount = Number(paymentAmount);
    if (!amount || amount <= 0) {
      setDetailError("Enter an amount greater than zero.");
      return;
    }

    setRecordingPayment(true);
    setDetailError("");
    try {
      const updated = await recordPaymentRequest(openInvoiceId, {
        amount, method: paymentMethod,
      });
      setOpenInvoice(updated);
      setPaymentAmount(updated.balance > 0 ? String(updated.balance) : "");
      await load();
    } catch (requestError) {
      setDetailError(requestError.message || "The payment could not be recorded.");
    } finally {
      setRecordingPayment(false);
    }
  }

  if (loading && !invoices.length) {
    return (
      <div className="tz-screen tzv2-invoices-page">
        <LoadingState title="Loading invoices…" description="Retrieving billing and payments." />
      </div>
    );
  }

  return (
    <div className="tz-screen tzv2-invoices-page">
      <div className="tzv2-invoices-toolbar">
        <div className="seg" role="radiogroup" aria-label="Filter by status">
          <label className="seg-opt">
            <input type="radio" name="tzv2-invoices-status" checked={statusFilter === ""} onChange={() => setStatusFilter("")} />
            All
          </label>
          {INVOICE_STATUSES.map((value) => (
            <label className="seg-opt" key={value}>
              <input type="radio" name="tzv2-invoices-status" checked={statusFilter === value} onChange={() => setStatusFilter(value)} />
              {humanize(value)}
            </label>
          ))}
        </div>
        <button type="button" className="btn btn-primary" onClick={openDialog} disabled={!customers.length}>
          <AddOutlined fontSize="small" /> New invoice
        </button>
      </div>

      {error ? (
        <ErrorState title="Could not load invoices" description={error} action={<button type="button" className="btn btn-primary" onClick={load}>Retry</button>} />
      ) : invoices.length ? (
        <div className="tz-tablewrap tzv2-invoices-tablewrap">
          <table className="table">
            <thead>
              <tr>
                <th>Customer</th>
                <th>Status</th>
                <th className="tz-num">Total</th>
                <th className="tz-num">Balance</th>
                <th>Due</th>
              </tr>
            </thead>
            <tbody>
              {invoices.map((invoice) => (
                <tr key={invoice.id} className="tzv2-invoices-row" onClick={() => openDetail(invoice.id)}>
                  <td><strong>{invoice.customer_name}</strong></td>
                  <td><span className="tag tag-outline">{humanize(invoice.status)}</span></td>
                  <td className="tz-num">{money(invoice.total)}</td>
                  <td className={`tz-num${invoice.balance > 0 ? " tzv2-invoices-balance-due" : ""}`}>{money(invoice.balance)}</td>
                  <td className="tzv2-invoices-muted">{invoice.due_date || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState title="No invoices yet" description="Raise one for a customer to start billing." />
      )}

      {dialogOpen ? (
        <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setDialogOpen(false); }}>
          <form className="dialog tzv2-invoices-dialog" role="dialog" aria-modal="true" aria-labelledby="tzv2-invoice-title" onSubmit={saveInvoice}>
            <div className="tzv2-invoices-dialog-head">
              <span className="dialog-title" id="tzv2-invoice-title">New invoice</span>
              <button type="button" className="btn btn-ghost btn-icon" aria-label="Close dialog" onClick={() => setDialogOpen(false)}>
                <CloseOutlined fontSize="small" />
              </button>
            </div>
            <div className="dialog-body tzv2-invoices-dialog-body">
              <div className="tzv2-invoices-field-row">
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
                  <label>Due date (optional)</label>
                  <input type="date" className="input" value={dueDate} onChange={(event) => setDueDate(event.target.value)} />
                </div>
              </div>

              <div className="tzv2-invoices-lines">
                {lines.map((line, index) => (
                  <div className="tzv2-invoices-line" key={index}>
                    <input className="input" placeholder="Description" value={line.description} onChange={(event) => updateLine(index, { description: event.target.value })} maxLength={500} />
                    <input type="number" className="input" min="1" value={line.quantity} onChange={(event) => updateLine(index, { quantity: event.target.value })} placeholder="Qty" />
                    <input type="number" className="input" min="0" step="0.01" value={line.unit_price} onChange={(event) => updateLine(index, { unit_price: event.target.value })} placeholder="Unit price" />
                    <button type="button" className="btn btn-ghost btn-icon" aria-label="Remove line" onClick={() => removeLine(index)} disabled={lines.length === 1}>
                      <DeleteOutlineOutlined fontSize="small" />
                    </button>
                  </div>
                ))}
                <button type="button" className="btn btn-ghost" onClick={addLine}>+ Add line</button>
              </div>

              {formError ? <p className="tzv2-invoices-form-error">{formError}</p> : null}
            </div>
            <div className="dialog-actions">
              <button type="button" className="btn btn-secondary" disabled={saving} onClick={() => setDialogOpen(false)}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? "Creating…" : "Create invoice"}</button>
            </div>
          </form>
        </div>
      ) : null}

      {openInvoiceId ? (
        <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeDetail(); }}>
          <section className="dialog tzv2-invoices-dialog" role="dialog" aria-modal="true" aria-labelledby="tzv2-invoice-detail-title">
            <div className="tzv2-invoices-dialog-head">
              <span className="dialog-title" id="tzv2-invoice-detail-title">
                {openInvoice ? `Invoice for ${openInvoice.customer_name}` : "Invoice"}
              </span>
              <button type="button" className="btn btn-ghost btn-icon" aria-label="Close dialog" onClick={closeDetail}>
                <CloseOutlined fontSize="small" />
              </button>
            </div>
            <div className="dialog-body tzv2-invoices-dialog-body">
              {openInvoiceLoading || !openInvoice ? (
                <p className="tzv2-invoices-muted">Loading…</p>
              ) : (
                <>
                  <div className="tzv2-invoices-detail-head">
                    <span className="tag tag-outline">{humanize(openInvoice.status)}</span>
                    <span className="tzv2-invoices-muted">
                      Total {money(openInvoice.total)} · Balance {money(openInvoice.balance)}
                    </span>
                  </div>

                  <table className="table tzv2-invoices-items">
                    <thead>
                      <tr>
                        <th>Line</th>
                        <th className="tz-num">Qty</th>
                        <th className="tz-num">Unit price</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(openInvoice.items || []).map((item) => (
                        <tr key={item.id}>
                          <td>{item.description}</td>
                          <td className="tz-num">{item.quantity}</td>
                          <td className="tz-num">{money(item.unit_price)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>

                  {(openInvoice.payments || []).length ? (
                    <div>
                      <p className="tzv2-invoices-section-label">Payments</p>
                      <ul className="tzv2-invoices-payments">
                        {openInvoice.payments.map((payment) => (
                          <li key={payment.id}>
                            {money(payment.amount)} — {humanize(payment.method)}
                            <span className="tzv2-invoices-muted"> · {(payment.created_at || "").slice(0, 10)}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}

                  {detailError ? <p className="tzv2-invoices-form-error">{detailError}</p> : null}

                  <div className="tzv2-invoices-detail-actions">
                    {openInvoice.status === "draft" ? (
                      <>
                        <button type="button" className="btn btn-primary" onClick={() => changeStatus("sent")}>Mark as sent</button>
                        <button type="button" className="btn btn-secondary" onClick={() => changeStatus("void")}>Void</button>
                      </>
                    ) : null}
                    {openInvoice.status === "sent" ? (
                      <button type="button" className="btn btn-secondary" onClick={() => changeStatus("void")}>Void</button>
                    ) : null}
                  </div>

                  {PAYABLE.includes(openInvoice.status) ? (
                    <form className="tzv2-invoices-payment-form" onSubmit={submitPayment}>
                      <p className="tzv2-invoices-section-label">Record a payment</p>
                      <div className="tzv2-invoices-field-row">
                        <div className="field">
                          <label>Amount</label>
                          <input type="number" className="input" min="0.01" step="0.01" value={paymentAmount} onChange={(event) => setPaymentAmount(event.target.value)} required />
                        </div>
                        <div className="field">
                          <label>Method</label>
                          <select className="input" value={paymentMethod} onChange={(event) => setPaymentMethod(event.target.value)}>
                            {PAYMENT_METHODS.map((method) => <option value={method} key={method}>{humanize(method)}</option>)}
                          </select>
                        </div>
                      </div>
                      <button type="submit" className="btn btn-primary" disabled={recordingPayment}>
                        {recordingPayment ? "Recording…" : "Record payment"}
                      </button>
                    </form>
                  ) : null}
                </>
              )}
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
