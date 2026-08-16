import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AddOutlined,
  ChevronLeftOutlined,
  ChevronRightOutlined,
  CloseOutlined,
  DeleteOutlineOutlined,
  RefreshOutlined,
  TodayOutlined,
} from "@mui/icons-material";

import {
  cancelAppointmentRequest,
  createAppointmentRequest,
  createAvailabilityRuleRequest,
  deleteAvailabilityRuleRequest,
  getAppointmentOptionsRequest,
  getAppointmentsRequest,
  getAvailabilityRulesRequest,
  getAvailableSlotsRequest,
  rescheduleAppointmentRequest,
  updateAppointmentStatusRequest,
  updateAvailabilityRuleRequest,
} from "../../api/appointments";
import {
  AppButton,
  AppCard,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
} from "../../components/common";
import "./AppointmentsPage.css";

/*
 * The API stores and returns every instant as UTC in one fixed format
 * (YYYY-MM-DDTHH:MM:SS+00:00), and availability rules are UTC wall-clock
 * times. This screen therefore draws the calendar in UTC as well, and says so
 * in the header. Rendering the grid in the browser's zone while the rules that
 * generate its slots are in another is how a calendar ends up offering times
 * nobody works.
 */

const WEEKDAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];

const STATUS_LABELS = {
  scheduled: "Scheduled",
  confirmed: "Confirmed",
  completed: "Completed",
  no_show: "No show",
  cancelled: "Cancelled",
};

const SETTABLE_STATUSES = ["scheduled", "confirmed", "completed", "no_show"];

const DURATIONS = [15, 30, 45, 60, 90, 120];

const DEFAULT_DAY_START_HOUR = 8;
const DEFAULT_DAY_END_HOUR = 20;
const HOUR_HEIGHT = 56;

// ----------------------------------------------------------------------
// UTC date helpers
// ----------------------------------------------------------------------

function todayKey() {
  return new Date().toISOString().slice(0, 10);
}

function dateKeyOf(instant) {
  return String(instant || "").slice(0, 10);
}

function clockOf(instant) {
  return String(instant || "").slice(11, 16);
}

function toInstant(dateKey, clock) {
  return `${dateKey}T${clock}:00+00:00`;
}

function shiftDays(dateKey, days) {
  const date = new Date(`${dateKey}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function weekdayOf(dateKey) {
  // 0 = Monday, to match the availability rules.
  return (new Date(`${dateKey}T00:00:00Z`).getUTCDay() + 6) % 7;
}

function startOfWeek(dateKey) {
  return shiftDays(dateKey, -weekdayOf(dateKey));
}

function minutesOf(instant) {
  const clock = clockOf(instant);
  const [hours, minutes] = clock.split(":");
  return Number(hours) * 60 + Number(minutes);
}

function durationMinutes(appointment) {
  const start = new Date(appointment.starts_at).getTime();
  const end = new Date(appointment.ends_at).getTime();
  return Math.max(0, Math.round((end - start) / 60000));
}

function addMinutesToClock(clock, minutes) {
  const [hours, mins] = clock.split(":").map(Number);
  const total = hours * 60 + mins + minutes;
  const wrapped = ((total % 1440) + 1440) % 1440;
  return `${String(Math.floor(wrapped / 60)).padStart(2, "0")}:${String(
    wrapped % 60,
  ).padStart(2, "0")}`;
}

function formatDayLabel(dateKey) {
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  }).format(new Date(`${dateKey}T00:00:00Z`));
}

function formatRangeLabel(days) {
  if (days.length === 1) {
    return new Intl.DateTimeFormat(undefined, {
      weekday: "long",
      day: "numeric",
      month: "long",
      year: "numeric",
      timeZone: "UTC",
    }).format(new Date(`${days[0]}T00:00:00Z`));
  }

  return `${formatDayLabel(days[0])} – ${formatDayLabel(days[days.length - 1])}`;
}

function appointmentLabel(appointment) {
  return (
    appointment?.customer_name ||
    appointment?.title ||
    `Appointment #${appointment?.id ?? "—"}`
  );
}

// ----------------------------------------------------------------------
// Column layout: appointments that overlap on screen share the day's width.
// ----------------------------------------------------------------------

function layoutDay(appointments) {
  const sorted = [...appointments].sort(
    (a, b) =>
      minutesOf(a.starts_at) - minutesOf(b.starts_at) ||
      Number(a.id) - Number(b.id),
  );

  const placed = [];
  let cluster = [];
  let clusterEnd = -1;

  const flush = () => {
    const lanes = Math.max(1, ...cluster.map((entry) => entry.lane + 1));

    cluster.forEach((entry) => {
      placed.push({ ...entry, lanes });
    });

    cluster = [];
    clusterEnd = -1;
  };

  sorted.forEach((appointment) => {
    const start = minutesOf(appointment.starts_at);
    const end = start + Math.max(15, durationMinutes(appointment));

    if (cluster.length && start >= clusterEnd) {
      flush();
    }

    const usedLanes = new Set(
      cluster.filter((entry) => entry.end > start).map((entry) => entry.lane),
    );

    let lane = 0;
    while (usedLanes.has(lane)) {
      lane += 1;
    }

    cluster.push({ appointment, start, end, lane });
    clusterEnd = Math.max(clusterEnd, end);
  });

  if (cluster.length) {
    flush();
  }

  return placed;
}

function emptyBookingForm() {
  return {
    customer_id: "",
    staff_user_id: "",
    title: "Appointment",
    date: todayKey(),
    start_time: "09:00",
    duration: 60,
    notes: "",
  };
}

// ----------------------------------------------------------------------

export default function AppointmentsPage() {
  const [tab, setTab] = useState("calendar");
  const [view, setView] = useState("week");
  const [anchorDate, setAnchorDate] = useState(todayKey);
  const [staffFilter, setStaffFilter] = useState("");
  const [includeCancelled, setIncludeCancelled] = useState(false);

  const [options, setOptions] = useState({ staff: [], customers: [] });
  const [optionsError, setOptionsError] = useState("");

  const [appointments, setAppointments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [selectedId, setSelectedId] = useState(null);
  const [detailError, setDetailError] = useState("");
  const [detailStatus, setDetailStatus] = useState("");
  const [detailBusy, setDetailBusy] = useState(false);
  const [rescheduleForm, setRescheduleForm] = useState(null);

  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelReason, setCancelReason] = useState("");
  const [cancelBusy, setCancelBusy] = useState(false);

  const [bookingOpen, setBookingOpen] = useState(false);
  const [bookingForm, setBookingForm] = useState(emptyBookingForm);
  const [bookingError, setBookingError] = useState("");
  const [bookingBusy, setBookingBusy] = useState(false);
  const [slots, setSlots] = useState([]);
  const [slotsLoading, setSlotsLoading] = useState(false);
  const [slotsError, setSlotsError] = useState("");

  const [ruleStaffId, setRuleStaffId] = useState("");
  const [rules, setRules] = useState([]);
  const [rulesLoading, setRulesLoading] = useState(false);
  const [rulesError, setRulesError] = useState("");
  const [ruleForm, setRuleForm] = useState({
    id: null,
    weekday: 0,
    start_time: "09:00",
    end_time: "17:00",
    slot_minutes: 30,
    status: "active",
  });
  const [ruleBusy, setRuleBusy] = useState(false);
  const [ruleStatus, setRuleStatus] = useState("");
  const [ruleToDelete, setRuleToDelete] = useState(null);

  const days = useMemo(() => {
    if (view === "day") {
      return [anchorDate];
    }

    const first = startOfWeek(anchorDate);
    return Array.from({ length: 7 }, (unused, index) => shiftDays(first, index));
  }, [view, anchorDate]);

  const staffNames = useMemo(() => {
    const map = new Map();
    options.staff.forEach((member) => map.set(Number(member.id), member.name));
    return map;
  }, [options.staff]);

  // ------------------------------------------------------------------
  // Loading
  // ------------------------------------------------------------------

  const loadOptions = useCallback(async () => {
    setOptionsError("");

    try {
      const result = await getAppointmentOptionsRequest();

      setOptions({
        staff: Array.isArray(result?.staff) ? result.staff : [],
        customers: Array.isArray(result?.customers) ? result.customers : [],
      });

      const firstStaffId = result?.staff?.[0]?.id;

      if (firstStaffId) {
        setRuleStaffId((current) => current || String(firstStaffId));
        setBookingForm((current) =>
          current.staff_user_id
            ? current
            : { ...current, staff_user_id: String(firstStaffId) },
        );
      }
    } catch (requestError) {
      setOptionsError(
        requestError.message || "Staff and customers could not be loaded.",
      );
    }
  }, []);

  const loadAppointments = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const result = await getAppointmentsRequest({
        startDate: days[0],
        endDate: days[days.length - 1],
        staffUserId: staffFilter,
        includeCancelled,
      });

      setAppointments(Array.isArray(result?.items) ? result.items : []);
    } catch (requestError) {
      setError(requestError.message || "The calendar could not be loaded.");
      setAppointments([]);
    } finally {
      setLoading(false);
    }
  }, [days, staffFilter, includeCancelled]);

  const loadRules = useCallback(async () => {
    if (!ruleStaffId) {
      setRules([]);
      return;
    }

    setRulesLoading(true);
    setRulesError("");

    try {
      const result = await getAvailabilityRulesRequest({
        staffUserId: ruleStaffId,
      });

      setRules(Array.isArray(result?.items) ? result.items : []);
    } catch (requestError) {
      setRulesError(
        requestError.message || "Working hours could not be loaded.",
      );
      setRules([]);
    } finally {
      setRulesLoading(false);
    }
  }, [ruleStaffId]);

  useEffect(() => {
    loadOptions();
  }, [loadOptions]);

  useEffect(() => {
    loadAppointments();
  }, [loadAppointments]);

  useEffect(() => {
    if (tab === "availability") {
      loadRules();
    }
  }, [tab, loadRules]);

  // Free slots follow the booking form's staff member, day and length.
  useEffect(() => {
    if (!bookingOpen || !bookingForm.staff_user_id || !bookingForm.date) {
      setSlots([]);
      return undefined;
    }

    let cancelled = false;
    setSlotsLoading(true);
    setSlotsError("");

    getAvailableSlotsRequest({
      staffUserId: bookingForm.staff_user_id,
      date: bookingForm.date,
      durationMinutes: bookingForm.duration,
    })
      .then((result) => {
        if (cancelled) return;
        setSlots(Array.isArray(result?.slots) ? result.slots : []);
      })
      .catch((requestError) => {
        if (cancelled) return;
        setSlots([]);
        setSlotsError(
          requestError.message || "Free slots could not be loaded.",
        );
      })
      .finally(() => {
        if (!cancelled) setSlotsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [
    bookingOpen,
    bookingForm.staff_user_id,
    bookingForm.date,
    bookingForm.duration,
  ]);

  const selected = useMemo(
    () => appointments.find((row) => row.id === selectedId) || null,
    [appointments, selectedId],
  );

  useEffect(() => {
    if (!selected) {
      setRescheduleForm(null);
      return;
    }

    setRescheduleForm({
      date: dateKeyOf(selected.starts_at),
      start_time: clockOf(selected.starts_at),
      duration: durationMinutes(selected) || 60,
      staff_user_id: String(selected.staff_user_id || ""),
    });
    setDetailError("");
    setDetailStatus("");
  }, [selected]);

  // ------------------------------------------------------------------
  // Grid geometry
  // ------------------------------------------------------------------

  const { startHour, endHour } = useMemo(() => {
    let first = DEFAULT_DAY_START_HOUR;
    let last = DEFAULT_DAY_END_HOUR;

    appointments.forEach((appointment) => {
      first = Math.min(first, Math.floor(minutesOf(appointment.starts_at) / 60));
      last = Math.max(
        last,
        Math.ceil(
          (minutesOf(appointment.starts_at) + durationMinutes(appointment)) / 60,
        ),
      );
    });

    return {
      startHour: Math.max(0, first),
      endHour: Math.min(24, Math.max(last, first + 1)),
    };
  }, [appointments]);

  const hours = useMemo(
    () =>
      Array.from({ length: endHour - startHour }, (unused, index) => startHour + index),
    [startHour, endHour],
  );

  const gridHeight = (endHour - startHour) * HOUR_HEIGHT;

  const byDay = useMemo(() => {
    const map = new Map();

    days.forEach((day) => map.set(day, []));

    appointments.forEach((appointment) => {
      const key = dateKeyOf(appointment.starts_at);
      if (map.has(key)) {
        map.get(key).push(appointment);
      }
    });

    const laidOut = new Map();
    map.forEach((value, key) => laidOut.set(key, layoutDay(value)));
    return laidOut;
  }, [days, appointments]);

  // ------------------------------------------------------------------
  // Actions
  // ------------------------------------------------------------------

  function openBookingAt(dateKey, clock) {
    setBookingError("");
    setBookingOpen(true);
    setSelectedId(null);
    setBookingForm((current) => ({
      ...current,
      date: dateKey,
      start_time: clock,
      staff_user_id:
        current.staff_user_id ||
        staffFilter ||
        String(options.staff[0]?.id || ""),
    }));
  }

  async function handleBook(event) {
    event.preventDefault();

    if (!bookingForm.staff_user_id) {
      setBookingError("Choose the staff member this appointment is for.");
      return;
    }

    setBookingBusy(true);
    setBookingError("");

    try {
      await createAppointmentRequest({
        staff_user_id: Number(bookingForm.staff_user_id),
        customer_id: bookingForm.customer_id
          ? Number(bookingForm.customer_id)
          : null,
        title: bookingForm.title.trim() || "Appointment",
        notes: bookingForm.notes.trim() || null,
        starts_at: toInstant(bookingForm.date, bookingForm.start_time),
        ends_at: toInstant(
          bookingForm.date,
          addMinutesToClock(bookingForm.start_time, Number(bookingForm.duration)),
        ),
      });

      setBookingOpen(false);
      setBookingForm((current) => ({ ...current, notes: "" }));
      setAnchorDate(bookingForm.date);
      await loadAppointments();
    } catch (requestError) {
      setBookingError(
        requestError.message || "The appointment could not be booked.",
      );
    } finally {
      setBookingBusy(false);
    }
  }

  async function handleReschedule(event) {
    event.preventDefault();

    if (!selected || !rescheduleForm) {
      return;
    }

    setDetailBusy(true);
    setDetailError("");
    setDetailStatus("");

    try {
      await rescheduleAppointmentRequest(selected.id, {
        starts_at: toInstant(rescheduleForm.date, rescheduleForm.start_time),
        ends_at: toInstant(
          rescheduleForm.date,
          addMinutesToClock(
            rescheduleForm.start_time,
            Number(rescheduleForm.duration),
          ),
        ),
        staff_user_id: rescheduleForm.staff_user_id
          ? Number(rescheduleForm.staff_user_id)
          : null,
      });

      setDetailStatus("Appointment moved.");
      setAnchorDate(rescheduleForm.date);
      await loadAppointments();
    } catch (requestError) {
      setDetailError(
        requestError.message || "The appointment could not be moved.",
      );
    } finally {
      setDetailBusy(false);
    }
  }

  async function handleStatusChange(nextStatus) {
    if (!selected) return;

    setDetailBusy(true);
    setDetailError("");
    setDetailStatus("");

    try {
      await updateAppointmentStatusRequest(selected.id, nextStatus);
      setDetailStatus(`Marked as ${STATUS_LABELS[nextStatus] || nextStatus}.`);
      await loadAppointments();
    } catch (requestError) {
      setDetailError(
        requestError.message || "The status could not be changed.",
      );
    } finally {
      setDetailBusy(false);
    }
  }

  async function handleCancelConfirmed() {
    if (!selected) return;

    setCancelBusy(true);
    setDetailError("");

    try {
      await cancelAppointmentRequest(selected.id, cancelReason.trim() || null);
      setCancelOpen(false);
      setCancelReason("");
      setDetailStatus("Appointment cancelled. The slot is free again.");
      await loadAppointments();
    } catch (requestError) {
      setCancelOpen(false);
      setDetailError(
        requestError.message || "The appointment could not be cancelled.",
      );
    } finally {
      setCancelBusy(false);
    }
  }

  async function handleRuleSubmit(event) {
    event.preventDefault();

    if (!ruleStaffId) {
      setRulesError("Choose a staff member first.");
      return;
    }

    setRuleBusy(true);
    setRulesError("");
    setRuleStatus("");

    const values = {
      weekday: Number(ruleForm.weekday),
      start_time: ruleForm.start_time,
      end_time: ruleForm.end_time,
      slot_minutes: Number(ruleForm.slot_minutes),
      status: ruleForm.status,
    };

    try {
      if (ruleForm.id) {
        await updateAvailabilityRuleRequest(ruleForm.id, {
          ...values,
          staff_user_id: Number(ruleStaffId),
        });
        setRuleStatus("Working hours updated.");
      } else {
        await createAvailabilityRuleRequest({
          ...values,
          staff_user_id: Number(ruleStaffId),
        });
        setRuleStatus("Working hours added.");
      }

      setRuleForm({
        id: null,
        weekday: values.weekday,
        start_time: values.start_time,
        end_time: values.end_time,
        slot_minutes: values.slot_minutes,
        status: "active",
      });
      await loadRules();
    } catch (requestError) {
      setRulesError(
        requestError.message || "The working hours could not be saved.",
      );
    } finally {
      setRuleBusy(false);
    }
  }

  async function handleRuleDeleteConfirmed() {
    if (!ruleToDelete) return;

    setRuleBusy(true);
    setRulesError("");

    try {
      await deleteAvailabilityRuleRequest(ruleToDelete.id);
      setRuleStatus("Working hours removed.");

      if (ruleForm.id === ruleToDelete.id) {
        setRuleForm((current) => ({ ...current, id: null }));
      }

      await loadRules();
    } catch (requestError) {
      setRulesError(
        requestError.message || "The working hours could not be removed.",
      );
    } finally {
      setRuleToDelete(null);
      setRuleBusy(false);
    }
  }

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------

  const staffOptions = options.staff;

  return (
    <div className="appointments-page">
      <PageHeader
        eyebrow="SCHEDULING"
        title="Appointments"
        description="The booking calendar for every staff member. Times are shown and entered in UTC, the same zone the working-hour rules use."
        actions={
          <>
            <AppButton
              variant="secondary"
              icon={<RefreshOutlined fontSize="small" />}
              onClick={() => {
                loadAppointments();
                if (tab === "availability") loadRules();
              }}
            >
              Refresh
            </AppButton>

            <AppButton
              variant="primary"
              icon={<AddOutlined fontSize="small" />}
              onClick={() => openBookingAt(anchorDate, "09:00")}
            >
              Book appointment
            </AppButton>
          </>
        }
      />

      {optionsError ? (
        <AppCard padding="medium">
          <ErrorState
            title="Staff list unavailable"
            description={optionsError}
            action={
              <AppButton variant="primary" onClick={loadOptions}>
                Try again
              </AppButton>
            }
          />
        </AppCard>
      ) : null}

      <nav className="appointments-tabs" aria-label="Appointment sections">
        <button
          type="button"
          className={tab === "calendar" ? "is-active" : ""}
          onClick={() => setTab("calendar")}
        >
          Calendar
        </button>

        <button
          type="button"
          className={tab === "availability" ? "is-active" : ""}
          onClick={() => setTab("availability")}
        >
          Working hours
        </button>
      </nav>

      {tab === "calendar" ? (
        <div
          className={`appointments-layout ${
            selected || bookingOpen ? "has-panel" : ""
          }`}
        >
          <AppCard padding="medium" className="appointments-calendar-card">
            <div className="appointments-toolbar">
              <div className="appointments-nav">
                <button
                  type="button"
                  aria-label="Previous period"
                  onClick={() =>
                    setAnchorDate((current) =>
                      shiftDays(current, view === "day" ? -1 : -7),
                    )
                  }
                >
                  <ChevronLeftOutlined fontSize="small" />
                </button>

                <button
                  type="button"
                  className="appointments-today"
                  onClick={() => setAnchorDate(todayKey())}
                >
                  <TodayOutlined fontSize="small" />
                  <span>Today</span>
                </button>

                <button
                  type="button"
                  aria-label="Next period"
                  onClick={() =>
                    setAnchorDate((current) =>
                      shiftDays(current, view === "day" ? 1 : 7),
                    )
                  }
                >
                  <ChevronRightOutlined fontSize="small" />
                </button>

                <strong className="appointments-range">
                  {formatRangeLabel(days)}
                </strong>
              </div>

              <div className="appointments-filters">
                <div className="appointments-view-switch" role="group" aria-label="Calendar view">
                  <button
                    type="button"
                    className={view === "week" ? "is-active" : ""}
                    onClick={() => setView("week")}
                  >
                    Week
                  </button>

                  <button
                    type="button"
                    className={view === "day" ? "is-active" : ""}
                    onClick={() => setView("day")}
                  >
                    Day
                  </button>
                </div>

                <label htmlFor="appointments-staff-filter">
                  <span>Staff</span>

                  <select
                    id="appointments-staff-filter"
                    value={staffFilter}
                    onChange={(event) => setStaffFilter(event.target.value)}
                  >
                    <option value="">All staff</option>

                    {staffOptions.map((member) => (
                      <option key={member.id} value={member.id}>
                        {member.name}
                      </option>
                    ))}
                  </select>
                </label>

                <label
                  htmlFor="appointments-show-cancelled"
                  className="appointments-checkbox"
                >
                  <input
                    id="appointments-show-cancelled"
                    type="checkbox"
                    checked={includeCancelled}
                    onChange={(event) =>
                      setIncludeCancelled(event.target.checked)
                    }
                  />

                  <span>Show cancelled</span>
                </label>
              </div>
            </div>

            {error ? (
              <ErrorState
                title="Calendar could not load"
                description={error}
                action={
                  <AppButton variant="primary" onClick={loadAppointments}>
                    Try again
                  </AppButton>
                }
              />
            ) : null}

            {!error && loading ? (
              <LoadingState
                title="Loading calendar..."
                description="Fetching the appointments for this period."
              />
            ) : null}

            {!error && !loading ? (
              <div className="appointments-grid-scroll">
                <div
                  className="appointments-grid"
                  style={{
                    gridTemplateColumns: `64px repeat(${days.length}, minmax(120px, 1fr))`,
                  }}
                >
                  <div className="appointments-corner" />

                  {days.map((day) => (
                    <div
                      key={`head-${day}`}
                      className={`appointments-day-head ${
                        day === todayKey() ? "is-today" : ""
                      }`}
                    >
                      <strong>{formatDayLabel(day)}</strong>

                      <span>
                        {(byDay.get(day) || []).length} booked
                      </span>
                    </div>
                  ))}

                  <div
                    className="appointments-hours"
                    style={{ height: `${gridHeight}px` }}
                  >
                    {hours.map((hour) => (
                      <div
                        key={`hour-${hour}`}
                        className="appointments-hour-label"
                        style={{ height: `${HOUR_HEIGHT}px` }}
                      >
                        {String(hour).padStart(2, "0")}:00
                      </div>
                    ))}
                  </div>

                  {days.map((day) => (
                    <div
                      key={`col-${day}`}
                      className="appointments-day-column"
                      style={{ height: `${gridHeight}px` }}
                    >
                      {hours.map((hour) => (
                        <button
                          key={`slot-${day}-${hour}`}
                          type="button"
                          className="appointments-hour-cell"
                          style={{ height: `${HOUR_HEIGHT}px` }}
                          title={`Book ${formatDayLabel(day)} at ${String(
                            hour,
                          ).padStart(2, "0")}:00 UTC`}
                          onClick={() =>
                            openBookingAt(
                              day,
                              `${String(hour).padStart(2, "0")}:00`,
                            )
                          }
                        />
                      ))}

                      {(byDay.get(day) || []).map(
                        ({ appointment, start, end, lane, lanes }) => {
                          const top =
                            ((start - startHour * 60) / 60) * HOUR_HEIGHT;
                          const height = Math.max(
                            22,
                            ((end - start) / 60) * HOUR_HEIGHT - 2,
                          );

                          return (
                            <button
                              key={appointment.id}
                              type="button"
                              className={`appointments-event status-${appointment.status} ${
                                appointment.id === selectedId ? "is-selected" : ""
                              }`}
                              style={{
                                top: `${top}px`,
                                height: `${height}px`,
                                left: `${(lane / lanes) * 100}%`,
                                width: `calc(${100 / lanes}% - 4px)`,
                              }}
                              onClick={() => {
                                setBookingOpen(false);
                                setSelectedId(appointment.id);
                              }}
                            >
                              <strong>{clockOf(appointment.starts_at)}</strong>
                              <span>{appointmentLabel(appointment)}</span>
                              <small>
                                {appointment.staff_name ||
                                  staffNames.get(
                                    Number(appointment.staff_user_id),
                                  ) ||
                                  "Unassigned"}
                              </small>
                            </button>
                          );
                        },
                      )}
                    </div>
                  ))}
                </div>

                {!appointments.length ? (
                  <EmptyState
                    title="Nothing booked in this period"
                    description="Click any hour in the calendar, or use Book appointment, to add one."
                  />
                ) : null}
              </div>
            ) : null}
          </AppCard>

          {bookingOpen ? (
            <AppCard padding="medium" className="appointments-panel">
              <header className="appointments-panel-head">
                <div>
                  <span>NEW BOOKING</span>
                  <h3>Book an appointment</h3>
                </div>

                <button
                  type="button"
                  aria-label="Close booking form"
                  className="appointments-panel-close"
                  onClick={() => setBookingOpen(false)}
                >
                  <CloseOutlined fontSize="small" />
                </button>
              </header>

              <form className="appointments-form" onSubmit={handleBook}>
                <label htmlFor="booking-customer">
                  <span>Customer</span>

                  <select
                    id="booking-customer"
                    value={bookingForm.customer_id}
                    onChange={(event) =>
                      setBookingForm((current) => ({
                        ...current,
                        customer_id: event.target.value,
                      }))
                    }
                  >
                    <option value="">No customer record</option>

                    {options.customers.map((customer) => (
                      <option key={customer.id} value={customer.id}>
                        {customer.label}
                        {customer.phone ? ` · ${customer.phone}` : ""}
                      </option>
                    ))}
                  </select>
                </label>

                <label htmlFor="booking-staff">
                  <span>Staff member</span>

                  <select
                    id="booking-staff"
                    value={bookingForm.staff_user_id}
                    onChange={(event) =>
                      setBookingForm((current) => ({
                        ...current,
                        staff_user_id: event.target.value,
                      }))
                    }
                  >
                    <option value="">Choose a staff member</option>

                    {staffOptions.map((member) => (
                      <option key={member.id} value={member.id}>
                        {member.name}
                      </option>
                    ))}
                  </select>
                </label>

                <label htmlFor="booking-title">
                  <span>Subject</span>

                  <input
                    id="booking-title"
                    type="text"
                    value={bookingForm.title}
                    onChange={(event) =>
                      setBookingForm((current) => ({
                        ...current,
                        title: event.target.value,
                      }))
                    }
                  />
                </label>

                <div className="appointments-form-row">
                  <label htmlFor="booking-date">
                    <span>Date (UTC)</span>

                    <input
                      id="booking-date"
                      type="date"
                      value={bookingForm.date}
                      onChange={(event) =>
                        setBookingForm((current) => ({
                          ...current,
                          date: event.target.value,
                        }))
                      }
                    />
                  </label>

                  <label htmlFor="booking-time">
                    <span>Start (UTC)</span>

                    <input
                      id="booking-time"
                      type="time"
                      value={bookingForm.start_time}
                      onChange={(event) =>
                        setBookingForm((current) => ({
                          ...current,
                          start_time: event.target.value,
                        }))
                      }
                    />
                  </label>

                  <label htmlFor="booking-duration">
                    <span>Length</span>

                    <select
                      id="booking-duration"
                      value={bookingForm.duration}
                      onChange={(event) =>
                        setBookingForm((current) => ({
                          ...current,
                          duration: Number(event.target.value),
                        }))
                      }
                    >
                      {DURATIONS.map((minutes) => (
                        <option key={minutes} value={minutes}>
                          {minutes} min
                        </option>
                      ))}
                    </select>
                  </label>
                </div>

                <section className="appointments-slots">
                  <h4>Free slots on this day</h4>

                  {slotsLoading ? (
                    <p className="appointments-hint">Checking availability...</p>
                  ) : null}

                  {!slotsLoading && slotsError ? (
                    <p className="appointments-hint is-error">{slotsError}</p>
                  ) : null}

                  {!slotsLoading && !slotsError && !slots.length ? (
                    <p className="appointments-hint">
                      No free slot on this day. Either no working hours are set
                      for this staff member, or the day is fully booked.
                    </p>
                  ) : null}

                  {!slotsLoading && slots.length ? (
                    <div className="appointments-slot-chips">
                      {slots.map((slot) => (
                        <button
                          key={slot.starts_at}
                          type="button"
                          className={
                            clockOf(slot.starts_at) === bookingForm.start_time
                              ? "is-active"
                              : ""
                          }
                          onClick={() =>
                            setBookingForm((current) => ({
                              ...current,
                              start_time: clockOf(slot.starts_at),
                            }))
                          }
                        >
                          {clockOf(slot.starts_at)}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </section>

                <label htmlFor="booking-notes">
                  <span>Notes</span>

                  <textarea
                    id="booking-notes"
                    rows={3}
                    value={bookingForm.notes}
                    onChange={(event) =>
                      setBookingForm((current) => ({
                        ...current,
                        notes: event.target.value,
                      }))
                    }
                  />
                </label>

                <footer className="appointments-form-footer">
                  <span className="is-error">{bookingError}</span>

                  <div>
                    <AppButton
                      variant="secondary"
                      disabled={bookingBusy}
                      onClick={() => setBookingOpen(false)}
                    >
                      Cancel
                    </AppButton>

                    <AppButton
                      type="submit"
                      variant="primary"
                      loading={bookingBusy}
                    >
                      Book
                    </AppButton>
                  </div>
                </footer>
              </form>
            </AppCard>
          ) : null}

          {!bookingOpen && selected && rescheduleForm ? (
            <AppCard padding="medium" className="appointments-panel">
              <header className="appointments-panel-head">
                <div>
                  <span>APPOINTMENT</span>
                  <h3>{appointmentLabel(selected)}</h3>
                </div>

                <button
                  type="button"
                  aria-label="Close appointment"
                  className="appointments-panel-close"
                  onClick={() => setSelectedId(null)}
                >
                  <CloseOutlined fontSize="small" />
                </button>
              </header>

              <div className="appointments-detail-facts">
                <div>
                  <span>Status</span>
                  <StatusBadge
                    status={selected.status}
                    label={STATUS_LABELS[selected.status] || selected.status}
                  />
                </div>

                <div>
                  <span>Staff</span>
                  <strong>{selected.staff_name || "Unassigned"}</strong>
                </div>

                <div>
                  <span>When (UTC)</span>
                  <strong>
                    {dateKeyOf(selected.starts_at)}{" "}
                    {clockOf(selected.starts_at)}–{clockOf(selected.ends_at)}
                  </strong>
                </div>

                <div>
                  <span>Subject</span>
                  <strong>{selected.title}</strong>
                </div>

                {selected.customer_phone ? (
                  <div>
                    <span>Phone</span>
                    <strong>{selected.customer_phone}</strong>
                  </div>
                ) : null}

                {selected.cancelled_reason ? (
                  <div>
                    <span>Cancellation reason</span>
                    <strong>{selected.cancelled_reason}</strong>
                  </div>
                ) : null}
              </div>

              {selected.notes ? (
                <p className="appointments-detail-notes">{selected.notes}</p>
              ) : null}

              {selected.status === "cancelled" ? (
                <p className="appointments-hint">
                  This appointment is cancelled and its slot is free again. Book
                  a new appointment to use the time.
                </p>
              ) : (
                <>
                  <form
                    className="appointments-form"
                    onSubmit={handleReschedule}
                  >
                    <h4>Reschedule</h4>

                    <div className="appointments-form-row">
                      <label htmlFor="reschedule-date">
                        <span>Date (UTC)</span>

                        <input
                          id="reschedule-date"
                          type="date"
                          value={rescheduleForm.date}
                          onChange={(event) =>
                            setRescheduleForm((current) => ({
                              ...current,
                              date: event.target.value,
                            }))
                          }
                        />
                      </label>

                      <label htmlFor="reschedule-time">
                        <span>Start (UTC)</span>

                        <input
                          id="reschedule-time"
                          type="time"
                          value={rescheduleForm.start_time}
                          onChange={(event) =>
                            setRescheduleForm((current) => ({
                              ...current,
                              start_time: event.target.value,
                            }))
                          }
                        />
                      </label>

                      <label htmlFor="reschedule-duration">
                        <span>Length</span>

                        <select
                          id="reschedule-duration"
                          value={rescheduleForm.duration}
                          onChange={(event) =>
                            setRescheduleForm((current) => ({
                              ...current,
                              duration: Number(event.target.value),
                            }))
                          }
                        >
                          {[
                            ...new Set([
                              ...DURATIONS,
                              rescheduleForm.duration,
                            ]),
                          ]
                            .sort((a, b) => a - b)
                            .map((minutes) => (
                              <option key={minutes} value={minutes}>
                                {minutes} min
                              </option>
                            ))}
                        </select>
                      </label>
                    </div>

                    <label htmlFor="reschedule-staff">
                      <span>Staff member</span>

                      <select
                        id="reschedule-staff"
                        value={rescheduleForm.staff_user_id}
                        onChange={(event) =>
                          setRescheduleForm((current) => ({
                            ...current,
                            staff_user_id: event.target.value,
                          }))
                        }
                      >
                        {!staffNames.has(Number(rescheduleForm.staff_user_id)) &&
                        rescheduleForm.staff_user_id ? (
                          <option value={rescheduleForm.staff_user_id}>
                            {selected.staff_name ||
                              `User ${rescheduleForm.staff_user_id}`}
                          </option>
                        ) : null}

                        {staffOptions.map((member) => (
                          <option key={member.id} value={member.id}>
                            {member.name}
                          </option>
                        ))}
                      </select>
                    </label>

                    <footer className="appointments-form-footer">
                      <span className={detailError ? "is-error" : "is-success"}>
                        {detailError || detailStatus}
                      </span>

                      <div>
                        <AppButton
                          type="submit"
                          variant="primary"
                          loading={detailBusy}
                        >
                          Move appointment
                        </AppButton>
                      </div>
                    </footer>
                  </form>

                  <section className="appointments-detail-actions">
                    <h4>Mark as</h4>

                    <div className="appointments-status-buttons">
                      {SETTABLE_STATUSES.map((value) => (
                        <AppButton
                          key={value}
                          size="small"
                          variant={
                            selected.status === value ? "primary" : "secondary"
                          }
                          disabled={detailBusy || selected.status === value}
                          onClick={() => handleStatusChange(value)}
                        >
                          {STATUS_LABELS[value]}
                        </AppButton>
                      ))}
                    </div>

                    <AppButton
                      variant="danger"
                      fullWidth
                      disabled={detailBusy}
                      onClick={() => {
                        setCancelReason("");
                        setCancelOpen(true);
                      }}
                    >
                      Cancel appointment
                    </AppButton>
                  </section>
                </>
              )}
            </AppCard>
          ) : null}
        </div>
      ) : null}

      {tab === "availability" ? (
        <AppCard padding="medium" className="appointments-availability">
          <div className="appointments-toolbar">
            <label htmlFor="rules-staff">
              <span>Working hours for</span>

              <select
                id="rules-staff"
                value={ruleStaffId}
                onChange={(event) => {
                  setRuleStaffId(event.target.value);
                  setRuleForm((current) => ({ ...current, id: null }));
                  setRuleStatus("");
                }}
              >
                <option value="">Choose a staff member</option>

                {staffOptions.map((member) => (
                  <option key={member.id} value={member.id}>
                    {member.name}
                  </option>
                ))}
              </select>
            </label>

            <span className="appointments-hint">
              Slots are generated from these windows, in UTC, and every booked
              appointment is subtracted from them.
            </span>
          </div>

          {rulesError ? (
            <ErrorState
              title="Working hours could not load"
              description={rulesError}
              action={
                <AppButton variant="primary" onClick={loadRules}>
                  Try again
                </AppButton>
              }
            />
          ) : null}

          {rulesLoading ? <LoadingState title="Loading working hours..." /> : null}

          {!rulesLoading && ruleStaffId ? (
            <div className="appointments-rules-layout">
              <div className="appointments-rules-list">
                {rules.length ? (
                  <table className="appointments-rules-table">
                    <thead>
                      <tr>
                        <th>Day</th>
                        <th>Window (UTC)</th>
                        <th>Slot</th>
                        <th>Status</th>
                        <th aria-label="Actions" />
                      </tr>
                    </thead>

                    <tbody>
                      {rules.map((rule) => (
                        <tr key={rule.id}>
                          <td>{WEEKDAYS[rule.weekday]}</td>
                          <td>
                            {rule.start_time} – {rule.end_time}
                          </td>
                          <td>{rule.slot_minutes} min</td>
                          <td>
                            <StatusBadge status={rule.status} />
                          </td>
                          <td className="appointments-rules-actions">
                            <AppButton
                              size="small"
                              variant="ghost"
                              onClick={() =>
                                setRuleForm({
                                  id: rule.id,
                                  weekday: rule.weekday,
                                  start_time: rule.start_time,
                                  end_time: rule.end_time,
                                  slot_minutes: rule.slot_minutes,
                                  status: rule.status,
                                })
                              }
                            >
                              Edit
                            </AppButton>

                            <AppButton
                              size="small"
                              variant="ghost"
                              icon={<DeleteOutlineOutlined fontSize="small" />}
                              onClick={() => setRuleToDelete(rule)}
                            >
                              Delete
                            </AppButton>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <EmptyState
                    title="No working hours yet"
                    description="Add a window below so this staff member can be offered to customers."
                  />
                )}
              </div>

              <form className="appointments-form" onSubmit={handleRuleSubmit}>
                <h4>{ruleForm.id ? "Edit window" : "Add a window"}</h4>

                <label htmlFor="rule-weekday">
                  <span>Day of week</span>

                  <select
                    id="rule-weekday"
                    value={ruleForm.weekday}
                    onChange={(event) =>
                      setRuleForm((current) => ({
                        ...current,
                        weekday: Number(event.target.value),
                      }))
                    }
                  >
                    {WEEKDAYS.map((name, index) => (
                      <option key={name} value={index}>
                        {name}
                      </option>
                    ))}
                  </select>
                </label>

                <div className="appointments-form-row">
                  <label htmlFor="rule-start">
                    <span>From (UTC)</span>

                    <input
                      id="rule-start"
                      type="time"
                      value={ruleForm.start_time}
                      onChange={(event) =>
                        setRuleForm((current) => ({
                          ...current,
                          start_time: event.target.value,
                        }))
                      }
                    />
                  </label>

                  <label htmlFor="rule-end">
                    <span>To (UTC)</span>

                    <input
                      id="rule-end"
                      type="time"
                      value={ruleForm.end_time}
                      onChange={(event) =>
                        setRuleForm((current) => ({
                          ...current,
                          end_time: event.target.value,
                        }))
                      }
                    />
                  </label>

                  <label htmlFor="rule-slot">
                    <span>Slot length</span>

                    <select
                      id="rule-slot"
                      value={ruleForm.slot_minutes}
                      onChange={(event) =>
                        setRuleForm((current) => ({
                          ...current,
                          slot_minutes: Number(event.target.value),
                        }))
                      }
                    >
                      {DURATIONS.map((minutes) => (
                        <option key={minutes} value={minutes}>
                          {minutes} min
                        </option>
                      ))}
                    </select>
                  </label>
                </div>

                <label
                  htmlFor="rule-active"
                  className="appointments-checkbox"
                >
                  <input
                    id="rule-active"
                    type="checkbox"
                    checked={ruleForm.status === "active"}
                    onChange={(event) =>
                      setRuleForm((current) => ({
                        ...current,
                        status: event.target.checked ? "active" : "inactive",
                      }))
                    }
                  />

                  <span>Active — generate slots from this window</span>
                </label>

                <footer className="appointments-form-footer">
                  <span className={rulesError ? "is-error" : "is-success"}>
                    {rulesError || ruleStatus}
                  </span>

                  <div>
                    {ruleForm.id ? (
                      <AppButton
                        variant="secondary"
                        disabled={ruleBusy}
                        onClick={() =>
                          setRuleForm((current) => ({ ...current, id: null }))
                        }
                      >
                        New window
                      </AppButton>
                    ) : null}

                    <AppButton
                      type="submit"
                      variant="primary"
                      loading={ruleBusy}
                    >
                      {ruleForm.id ? "Save window" : "Add window"}
                    </AppButton>
                  </div>
                </footer>
              </form>
            </div>
          ) : null}

          {!rulesLoading && !ruleStaffId ? (
            <EmptyState
              title="Choose a staff member"
              description="Working hours are set per staff member, because the calendar books their time individually."
            />
          ) : null}
        </AppCard>
      ) : null}

      <ConfirmDialog
        open={cancelOpen}
        title="Cancel this appointment?"
        confirmLabel="Cancel appointment"
        cancelLabel="Keep it"
        loading={cancelBusy}
        onCancel={() => setCancelOpen(false)}
        onConfirm={handleCancelConfirmed}
        message={
          <div className="appointments-cancel-dialog">
            <p>
              The slot becomes free for another booking straight away. This
              cannot be undone — a cancelled appointment cannot be reactivated.
            </p>

            <label htmlFor="cancel-reason">
              <span>Reason (optional)</span>

              <input
                id="cancel-reason"
                type="text"
                value={cancelReason}
                onChange={(event) => setCancelReason(event.target.value)}
              />
            </label>
          </div>
        }
      />

      <ConfirmDialog
        open={Boolean(ruleToDelete)}
        title="Remove this working window?"
        confirmLabel="Remove"
        loading={ruleBusy}
        onCancel={() => setRuleToDelete(null)}
        onConfirm={handleRuleDeleteConfirmed}
        message={
          ruleToDelete
            ? `${WEEKDAYS[ruleToDelete.weekday]} ${ruleToDelete.start_time}–${
                ruleToDelete.end_time
              } will no longer generate slots. Appointments already booked in it are kept.`
            : ""
        }
      />
    </div>
  );
}
