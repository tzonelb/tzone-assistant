import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BackspaceOutlined,
  CallEndOutlined,
  CallOutlined,
  LocalPhoneOutlined,
  PhoneForwardedOutlined,
  RefreshOutlined,
  SettingsPhoneOutlined,
} from "@mui/icons-material";
import { Link } from "react-router-dom";

import {
  dialerStatusRequest,
  hangupDialerCallRequest,
  listCustomersRequest,
  listDialerCallsRequest,
  placeDialerCallRequest,
  taskOptionsRequest,
  transferDialerCallRequest,
} from "../../api/client";
import { AppCard, AppTable, PageHeader, StatusBadge } from "../../components/common";
import "./DialerPage.css";

const POLL_INTERVAL_MS = 3000;

const ACTIVE_STATUSES = new Set([
  "queued",
  "initiated",
  "ringing",
  "in_progress",
  "transferring",
]);

const STATUS_TONE = {
  queued: "info",
  initiated: "info",
  ringing: "warning",
  in_progress: "success",
  transferring: "warning",
  completed: "neutral",
  failed: "danger",
  busy: "warning",
  no_answer: "warning",
  cancelled: "neutral",
};

const KEYPAD = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"];

function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  const total = Number(seconds);
  if (Number.isNaN(total)) return "—";
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return minutes ? `${minutes}m ${rest}s` : `${rest}s`;
}

export default function DialerPage() {
  const [status, setStatus] = useState(null);
  const [number, setNumber] = useState("");
  const [customerId, setCustomerId] = useState("");
  const [customers, setCustomers] = useState([]);
  const [employees, setEmployees] = useState([]);

  const [calls, setCalls] = useState([]);
  const [callsTotal, setCallsTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);

  const [placing, setPlacing] = useState(false);
  const [busyCallId, setBusyCallId] = useState(null);
  const [transferTarget, setTransferTarget] = useState({});
  const [error, setError] = useState("");

  const pollRef = useRef(null);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await dialerStatusRequest());
    } catch {
      setStatus(null);
    }
  }, []);

  const loadCalls = useCallback(async () => {
    try {
      const result = await listDialerCallsRequest({
        limit: 10,
        offset: (page - 1) * 10,
      });
      setCalls(result?.items || []);
      setCallsTotal(result?.total || 0);
    } catch (requestError) {
      setError(requestError.message || "Calls could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  useEffect(() => {
    loadCalls();
  }, [loadCalls]);

  // Poll while any call is active so live statuses stay fresh.
  useEffect(() => {
    const hasActive = calls.some((call) => ACTIVE_STATUSES.has(call.status));
    if (!hasActive) return undefined;
    pollRef.current = setInterval(loadCalls, POLL_INTERVAL_MS);
    return () => clearInterval(pollRef.current);
  }, [calls, loadCalls]);

  useEffect(() => {
    listCustomersRequest({ limit: 100 })
      .then((result) => setCustomers(Array.isArray(result?.items) ? result.items : []))
      .catch(() => setCustomers([]));
    taskOptionsRequest()
      .then((result) => setEmployees(Array.isArray(result?.employees) ? result.employees : []))
      .catch(() => setEmployees([]));
  }, []);

  const configured = Boolean(status?.configured);

  const selectedCustomerPhone = useMemo(() => {
    if (!customerId) return null;
    const customer = customers.find((item) => String(item.id) === customerId);
    return customer?.phone || null;
  }, [customerId, customers]);

  useEffect(() => {
    if (selectedCustomerPhone) setNumber(selectedCustomerPhone);
  }, [selectedCustomerPhone]);

  function pressKey(key) {
    setNumber((current) => `${current}${key}`);
  }

  function backspace() {
    setNumber((current) => current.slice(0, -1));
  }

  async function handlePlaceCall() {
    const target = number.trim();
    if (!target) {
      setError("Enter a number or pick a customer first.");
      return;
    }
    setPlacing(true);
    setError("");
    try {
      await placeDialerCallRequest({
        to_number: target,
        customer_id: customerId ? Number(customerId) : null,
      });
      setPage(1);
      await loadCalls();
    } catch (err) {
      setError(
        (typeof err?.data?.detail === "string" ? err.data.detail : null) ||
          err.message ||
          "The call could not be placed.",
      );
    } finally {
      setPlacing(false);
    }
  }

  async function handleTransfer(call) {
    const employeeId = transferTarget[call.id];
    if (!employeeId) {
      setError("Pick an employee to transfer to.");
      return;
    }
    setBusyCallId(call.id);
    setError("");
    try {
      await transferDialerCallRequest(call.id, {
        employee_user_id: Number(employeeId),
      });
      await loadCalls();
    } catch (err) {
      setError(
        (typeof err?.data?.detail === "string" ? err.data.detail : null) ||
          err.message ||
          "The call could not be transferred.",
      );
    } finally {
      setBusyCallId(null);
    }
  }

  async function handleHangup(call) {
    setBusyCallId(call.id);
    setError("");
    try {
      await hangupDialerCallRequest(call.id);
      await loadCalls();
    } catch (err) {
      setError(err.message || "The call could not be ended.");
    } finally {
      setBusyCallId(null);
    }
  }

  const columns = [
    {
      key: "created_at",
      label: "When",
      render: (value) => formatDateTime(value),
    },
    {
      key: "to_number",
      label: "Call",
      render: (_value, row) => (
        <div className="dialer-call-cell">
          <strong>
            {row.customer_name ||
              (row.direction === "outbound" ? row.to_number : row.from_number) ||
              "Unknown"}
          </strong>
          <span>
            {row.direction === "outbound" ? "Outbound" : "Inbound"}
            {row.ai_answered ? " · AI answered" : ""}
            {row.transferred_to_name ? ` · → ${row.transferred_to_name}` : ""}
          </span>
        </div>
      ),
    },
    {
      key: "status",
      label: "Status",
      render: (value) => (
        <StatusBadge status={value} tone={STATUS_TONE[value] || "info"} label={String(value).replaceAll("_", " ")} />
      ),
    },
    {
      key: "duration_seconds",
      label: "Duration",
      render: (value) => formatDuration(value),
    },
    {
      key: "recording_url",
      label: "Recording",
      render: (value) =>
        value ? (
          <a href={value} target="_blank" rel="noreferrer" className="dialer-recording-link">
            Listen
          </a>
        ) : (
          "—"
        ),
    },
    {
      key: "actions",
      label: "",
      align: "right",
      render: (_value, row) =>
        ACTIVE_STATUSES.has(row.status) ? (
          <div className="dialer-row-actions">
            <select
              value={transferTarget[row.id] || ""}
              onChange={(event) =>
                setTransferTarget((current) => ({
                  ...current,
                  [row.id]: event.target.value,
                }))
              }
            >
              <option value="">Transfer to…</option>
              {employees.map((employee) => (
                <option key={employee.id} value={employee.id}>
                  {/* `display_name` is the key this platform's
                      /api/tasks/options actually sends; the other two are kept
                      for the shape the screen was written against, so neither
                      side has to change for the name to appear. Without it
                      every option read "User 12" and the dropdown was a list of
                      numbers nobody could transfer a customer to. */}
                  {employee.display_name
                    || employee.full_name
                    || employee.email
                    || `User ${employee.id}`}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn btn-secondary"
              disabled={busyCallId === row.id}
              onClick={() => handleTransfer(row)}
            >
              <PhoneForwardedOutlined fontSize="small" /> Transfer
            </button>
            <button
              type="button"
              className="btn btn-danger"
              disabled={busyCallId === row.id}
              onClick={() => handleHangup(row)}
            >
              <CallEndOutlined fontSize="small" /> End
            </button>
          </div>
        ) : null,
    },
  ];

  return (
    <section className="dialer-page">
      <PageHeader
        eyebrow="DIALER"
        title="Dialer"
        description="Place real calls, transfer them to teammates, and let the AI answer inbound calls with recording."
        actions={
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => {
              loadStatus();
              loadCalls();
            }}
          >
            <RefreshOutlined fontSize="small" /> Refresh
          </button>
        }
      />

      {!configured ? (
        <AppCard padding="medium">
          <div className="dialer-setup-notice">
            <SettingsPhoneOutlined />
            <div>
              <strong>Calling isn’t set up yet.</strong>
              <p>
                Live calling needs a telephony provider (Twilio) connected by your
                workspace owner or administrator. If that’s not you, ask them to set
                it up. Once connected, this page dials, transfers, records and
                AI-answers automatically — everything below is already wired and waiting.
              </p>
              <p className="dialer-setup-admin-hint">
                Admin setup: a Twilio account, a phone number, and these server settings —{" "}
                {(status?.missing || []).join(", ") || "TWILIO_* + PUBLIC_BASE_URL"}.
              </p>
            </div>
          </div>
        </AppCard>
      ) : null}

      <div className="dialer-layout">
        <AppCard padding="medium" className="dialer-pad-card">
          <h4 className="dialer-section-heading">
            <LocalPhoneOutlined fontSize="small" /> Place a call
          </h4>

          <label className="dialer-field">
            <span>Customer (optional)</span>
            <select value={customerId} onChange={(event) => setCustomerId(event.target.value)}>
              <option value="">Dial a number directly</option>
              {customers.map((customer) => (
                <option key={customer.id} value={customer.id}>
                  {customer.display_name || customer.full_name || `Customer ${customer.id}`}
                  {customer.phone ? ` (${customer.phone})` : ""}
                </option>
              ))}
            </select>
          </label>

          <div className="dialer-number-row">
            <input
              type="tel"
              value={number}
              placeholder="+961 ..."
              onChange={(event) => setNumber(event.target.value)}
            />
            <button
              type="button"
              className="dialer-backspace"
              aria-label="Delete last digit"
              disabled={!number}
              onClick={backspace}
            >
              <BackspaceOutlined fontSize="small" />
            </button>
          </div>

          <div className="dialer-keypad">
            {KEYPAD.map((key) => (
              <button key={key} type="button" onClick={() => pressKey(key)}>
                {key}
              </button>
            ))}
          </div>

          <button
            type="button"
            className="btn btn-primary dialer-call-button"
            disabled={placing || !configured || !number.trim()}
            onClick={handlePlaceCall}
          >
            <CallOutlined fontSize="small" /> {placing ? "Calling…" : "Call"}
          </button>

          {error ? <p className="dialer-error">{error}</p> : null}

          <p className="dialer-log-link">
            Past call history lives in the <Link to="/calls">Calls log</Link> —
            every finished dialer call is recorded there automatically.
          </p>
        </AppCard>

        <AppCard padding="medium" className="dialer-calls-card">
          <h4 className="dialer-section-heading">Live &amp; recent calls</h4>
          <AppTable
            columns={columns}
            rows={calls}
            loading={loading}
            rowKey="id"
            page={page}
            pageSize={10}
            totalRows={callsTotal}
            onPageChange={setPage}
            emptyTitle="No dialer calls yet"
            emptyDescription={
              configured
                ? "Place your first call with the dial pad."
                : "Once telephony is connected, live and recent calls will appear here."
            }
          />
        </AppCard>
      </div>
    </section>
  );
}
