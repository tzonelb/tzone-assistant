
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  AttachFileOutlined,
  AutoAwesomeOutlined,
  ImageOutlined,
  MicNoneOutlined,
  CloseOutlined,
  ChevronLeftOutlined,
  ChevronRightOutlined,
  DownloadOutlined,
  ExpandLessOutlined,
  ExpandMoreOutlined,
  LaunchOutlined,
  NoteAddOutlined,
  PauseCircleOutlineOutlined,
  RefreshOutlined,
  SendOutlined,
  SupportAgentOutlined,
} from "@mui/icons-material";

import { useNavigate, useParams } from "react-router-dom";

import {
  addConversationNoteRequest,
  downloadConversationExport,
  getConversationControlRequest,
  getConversationMessagesRequest,
  releaseConversationRequest,
  returnConversationToAiRequest,
  sendConversationReplyRequest,
  takeOverConversationRequest,
  updateConversationControlRequest,
} from "../../api/client";

import { useConversationLive } from "../../contexts/ConversationLiveContext";

import {
  AppButton,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../../components/common";

import "./ConversationControl.css";
import "./ConversationInbox.css";


const STATUS_OPTIONS = [
  "new",
  "open",
  "ai_handling",
  "human_handling",
  "waiting_customer",
  "waiting_agent",
  "pending",
  "resolved",
  "closed",
  "archived",
];

const PRIORITY_OPTIONS = [
  "low",
  "normal",
  "high",
  "urgent",
];


function humanize(value) {
  return String(value ?? "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}


function parseServerDate(value) {
  if (!value) {
    return null;
  }

  const rawValue = String(value).trim();
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(rawValue);
  const normalizedValue = hasTimezone ? rawValue : `${rawValue}Z`;
  const date = new Date(normalizedValue);

  return Number.isNaN(date.getTime()) ? null : date;
}


function formatDateTime(value) {
  const date = parseServerDate(value);
  return date ? date.toLocaleString() : String(value || "");
}


function toTimestamp(value) {
  return parseServerDate(value)?.getTime() || 0;
}


function resolveMessageText(message) {
  return (
    message?.text ||
    message?.message ||
    message?.content ||
    ""
  );
}


function resolveDirection(message) {
  const direction = String(
    message?.direction || "",
  ).toLowerCase();

  return [
    "out",
    "outgoing",
    "assistant",
  ].includes(direction)
    ? "out"
    : "in";
}


function formatTimer(totalSeconds) {
  const safeSeconds = Math.max(
    0,
    Number(totalSeconds) || 0,
  );
  const minutes = Math.floor(safeSeconds / 60);
  const seconds = safeSeconds % 60;

  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}


function getCustomerName(metadata, control) {
  return (
    control?.official_customer_name ||
    control?.customer_name ||
    control?.platform_customer_name ||
    metadata?.customer_name ||
    metadata?.sender_name ||
    metadata?.display_name ||
    metadata?.name ||
    "Unknown Customer"
  );
}


function CollapsiblePanel({
  title,
  subtitle,
  icon,
  open,
  onToggle,
  children,
}) {
  return (
    <section className="detail-card">
      <button
        type="button"
        className="detail-accordion-header"
        onClick={onToggle}
      >
        <span className="detail-accordion-icon">{icon}</span>
        <span className="detail-accordion-heading">
          <strong>{title}</strong>
          <small>{subtitle}</small>
        </span>
        {open ? <ExpandLessOutlined /> : <ExpandMoreOutlined />}
      </button>

      {open ? (
        <div className="detail-accordion-body">{children}</div>
      ) : null}
    </section>
  );
}


export default function ConversationDetailPage({
  embedded = false,
  standalone = false,
  channelOverride = "",
  userIdOverride = "",
  onConversationChanged,
  onExit,
}) {
  const params = useParams();
  const navigate = useNavigate();
  const channel = channelOverride || params.channel || "";
  const userId = userIdOverride || params.userId || "";
  const messagesEndRef = useRef(null);
  const composerRef = useRef(null);
  const takeoverInFlightRef = useRef(false);
  const liveSignatureRef = useRef("");
  const live = useConversationLive();

  // Tracks the alias draft's dirty state without recreating loadConversation
  // on every keystroke: aliasDraftRef always mirrors the latest aliasDraft,
  // and lastSyncedAliasRef.current is the {alias, controlVersion} pair last
  // confirmed to match the server. When they diverge the user has an
  // unsaved edit in flight, so silent polling must not overwrite it — and
  // saving must use the version the edit was actually based on, not
  // whatever the most recent poll happens to show.
  //
  // `controlVersion` is the dedicated conversations.control_version counter
  // (see conversation_control_service.update_state/update_workspace_state),
  // NOT the row's general `updated_at` — updated_at is also bumped by
  // unrelated activity (inbound customer messages, AI/employee replies,
  // takeover-timeout expiry, ...) and would cause spurious conflicts here.
  const aliasDraftRef = useRef("");
  const lastSyncedAliasRef = useRef({ alias: "", controlVersion: null });

  const [messages, setMessages] = useState([]);
  const [control, setControl] = useState(null);
  const [notes, setNotes] = useState([]);
  const [departments, setDepartments] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [currentUserId, setCurrentUserId] = useState(null);
  const [currentUserIsAdmin, setCurrentUserIsAdmin] = useState(false);
  const [permissions, setPermissions] = useState({});
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [sending, setSending] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [changingMode, setChangingMode] = useState(false);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionSuccess, setActionSuccess] = useState("");
  // True when the last alias save was rejected as stale: the alias draft
  // and its version anchor are pinned to what the user was editing (see
  // aliasDraftRef/lastSyncedAliasRef above), so the ordinary silent poll
  // can never clear this on its own — an explicit user action must discard
  // the edit and resync before another save can succeed.
  const [aliasVersionConflict, setAliasVersionConflict] = useState(false);
  const [draft, setDraft] = useState("");
  const [noteDraft, setNoteDraft] = useState("");
  const [aliasDraft, setAliasDraft] = useState("");
  const [tagDraft, setTagDraft] = useState("");
  const [editingTag, setEditingTag] = useState("");
  const [exportScope, setExportScope] = useState("full");
  const [exportFormat, setExportFormat] = useState("json");
  const [detailsOpen, setDetailsOpen] = useState(true);
  const [controlPanelOpen, setControlPanelOpen] = useState(true);
  const [notesPanelOpen, setNotesPanelOpen] = useState(false);
  const [exportPanelOpen, setExportPanelOpen] = useState(false);
  const [, setClockTick] = useState(0);


  const loadConversation = useCallback(
    async ({ silent = false } = {}) => {
      if (!channel || !userId) {
        setLoading(false);
        return;
      }

      if (silent) {
        setRefreshing(true);
      } else {
        setLoading(true);
      }

      setError("");

      try {
        // Load messages first: the backend records the owner opening the chat
        // and clears unread before the control state is fetched. This avoids
        // a race where the unread badge remained visible until the next poll.
        const messagesResult = await getConversationMessagesRequest(
          channel,
          userId,
          300,
        );
        const controlResult = await getConversationControlRequest(
          channel,
          userId,
        );

        window.dispatchEvent(new CustomEvent("tzone:conversation-refresh", {
          detail: { channel, userId },
        }));

        setMessages(
          Array.isArray(messagesResult?.messages)
            ? messagesResult.messages
            : [],
        );
        setControl(controlResult?.conversation || null);
        setNotes(
          Array.isArray(controlResult?.notes)
            ? controlResult.notes
            : [],
        );
        setEmployees(
          Array.isArray(controlResult?.employees)
            ? controlResult.employees
            : [],
        );
        setDepartments(
          Array.isArray(controlResult?.departments)
            ? controlResult.departments
            : [],
        );
        setCurrentUserId(
          controlResult?.current_user_id != null
            ? Number(controlResult.current_user_id)
            : null,
        );
        setCurrentUserIsAdmin(Boolean(controlResult?.current_user_is_admin));
        setPermissions(controlResult?.permissions || {});

        const serverAlias = controlResult?.conversation?.customer_alias || "";
        const serverControlVersion = controlResult?.conversation?.control_version ?? null;
        const hasUnsavedAliasEdit =
          aliasDraftRef.current !== lastSyncedAliasRef.current.alias;

        if (!hasUnsavedAliasEdit) {
          // Safe to sync: the user has no in-progress edit on this field,
          // so the server snapshot can't be clobbering anything.
          aliasDraftRef.current = serverAlias;
          setAliasDraft(serverAlias);
          lastSyncedAliasRef.current = {
            alias: serverAlias,
            controlVersion: serverControlVersion,
          };
        }
        // If dirty, leave aliasDraft and lastSyncedAliasRef alone — the
        // user's edit keeps the version it was based on, so saving it will
        // correctly be rejected with 409 if the record has actually moved
        // on underneath them.
      } catch (requestError) {
        setError(
          requestError.message ||
          "Conversation could not be loaded.",
        );
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [channel, userId],
  );


  useEffect(() => {
    setMessages([]);
    setControl(null);
    setNotes([]);
    setDraft("");
    setActionError("");
    setActionSuccess("");
    setAliasDraft("");
    aliasDraftRef.current = "";
    lastSyncedAliasRef.current = { alias: "", controlVersion: null };
    setAliasVersionConflict(false);
    loadConversation();
  }, [channel, userId, loadConversation]);


  // Keep aliasDraftRef in sync with every aliasDraft change (typing,
  // programmatic resets) so loadConversation's dirty check always sees the
  // latest value without needing aliasDraft itself in its dependency array.
  useEffect(() => {
    aliasDraftRef.current = aliasDraft;
  }, [aliasDraft]);


  useEffect(() => {
    const interval = window.setInterval(
      () => loadConversation({ silent: true }),
      3000,
    );

    return () => window.clearInterval(interval);
  }, [loadConversation]);


  useEffect(() => live.subscribe((event) => {
    if (event?.type !== "snapshot") return;
    const matching = (event.items || []).find((item) => (
      String(item?.channel) === String(channel)
      && String(item?.external_user_id) === String(userId)
    ));
    if (!matching) return;
    const signature = [
      matching.updated_at,
      matching.assigned_user_id,
      matching.handled_by_ai,
      matching.unread_count,
      matching.takeover_expires_at,
    ].join("::");
    if (signature === liveSignatureRef.current) return;
    liveSignatureRef.current = signature;

    // Apply the ownership part of the snapshot immediately so a second
    // employee never keeps seeing Take over while the detail request reloads.
    const nextOwnerId = matching.assigned_user_id == null
      ? null
      : Number(matching.assigned_user_id);
    const nextIsOwner = nextOwnerId != null
      && currentUserId != null
      && nextOwnerId === Number(currentUserId);
    const nextAiHandling = Boolean(
      matching.handled_by_ai && matching.ai_enabled,
    );
    setControl((current) => ({
      ...(current || {}),
      assigned_user_id: nextOwnerId,
      assigned_user_name: matching.assigned_user_name || null,
      handled_by_ai: Boolean(matching.handled_by_ai),
      ai_enabled: Boolean(matching.ai_enabled),
      unread_count: Number(matching.unread_count || 0),
      takeover_expires_at: matching.takeover_expires_at || null,
    }));
    setPermissions({
      can_reply: Boolean(nextIsOwner && !nextAiHandling),
      can_manage: Boolean(currentUserIsAdmin || nextIsOwner),
      can_mark_read: Boolean(currentUserIsAdmin || nextIsOwner),
      can_take_over: Boolean(nextOwnerId == null || nextIsOwner),
    });
    loadConversation({ silent: true });
  }), [
    live,
    channel,
    userId,
    loadConversation,
    currentUserId,
    currentUserIsAdmin,
  ]);


  useEffect(() => {
    const interval = window.setInterval(
      () => setClockTick((current) => current + 1),
      1000,
    );

    return () => window.clearInterval(interval);
  }, []);


  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({
      behavior: "smooth",
    });
  }, [messages]);


  useEffect(() => {
    if (!actionSuccess) {
      return undefined;
    }

    const timeout = window.setTimeout(
      () => setActionSuccess(""),
      3500,
    );

    return () => window.clearTimeout(timeout);
  }, [actionSuccess]);


  const latestMetadata = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const metadata = messages[index]?.metadata;

      if (metadata && typeof metadata === "object") {
        return metadata;
      }
    }

    return {};
  }, [messages]);


  const officialCustomerName = getCustomerName(
    latestMetadata,
    control,
  );
  const displayCustomerName =
    control?.customer_alias || officialCustomerName;
  const aiIsHandling = Boolean(
    control?.handled_by_ai && control?.ai_enabled,
  );
  const assignedUserId = control?.assigned_user_id == null
    ? null
    : Number(control.assigned_user_id);
  const isAssignedToMe =
    assignedUserId != null &&
    currentUserId != null &&
    assignedUserId === currentUserId;
  const isHumanQueue = !aiIsHandling && assignedUserId == null;
  const isAssignedToOther = assignedUserId != null && !isAssignedToMe;
  // Server permissions are authoritative.  Never infer ownership controls
  // from locally cached assignment data because another employee may have
  // acquired the lock in a different browser.
  const canReply = Boolean(permissions?.can_reply);
  const canManage = Boolean(permissions?.can_manage);
  const canMarkRead = Boolean(permissions?.can_mark_read);
  const canTakeOver = Boolean(permissions?.can_take_over) && !isAssignedToOther;
  const expiryDate = control?.takeover_expires_at
    ? new Date(control.takeover_expires_at)
    : null;
  const takeoverSecondsLeft =
    expiryDate && !Number.isNaN(expiryDate.getTime())
      ? Math.max(
          0,
          Math.floor((expiryDate.getTime() - Date.now()) / 1000),
        )
      : 0;


  const selectedDepartment = control?.department || "Unassigned";
  const availableEmployees = useMemo(() => {
    if (selectedDepartment === "Unassigned") {
      return [];
    }

    return employees;
  }, [employees, selectedDepartment]);


  const feedItems = useMemo(() => (
    messages
      .map((message, index) => ({
        key: `message-${message.id || index}`,
        createdAt: message.time || message.created_at,
        value: message,
      }))
      .sort(
        (left, right) =>
          toTimestamp(left.createdAt) - toTimestamp(right.createdAt),
      )
  ), [messages]);

  function openInNewTab() {
    window.open(
      `/conversations/${encodeURIComponent(channel)}/${encodeURIComponent(userId)}/full`,
      "_blank",
      "noopener,noreferrer",
    );
  }


  function exitConversation() {
    if (typeof onExit === "function") {
      onExit();
      return;
    }
    navigate("/conversations");
  }


  function applyOwnershipConflict(requestError) {
    const detail = requestError?.data?.detail;
    if (requestError?.status !== 409 || !detail || typeof detail !== "object") {
      return false;
    }

    const ownerUserId = detail.owner_user_id == null
      ? null
      : Number(detail.owner_user_id);
    const ownerUserName = detail.owner_user_name || "another employee";

    if (ownerUserId != null) {
      setControl((current) => ({
        ...(current || {}),
        assigned_user_id: ownerUserId,
        assigned_user_name: ownerUserName,
        handled_by_ai: false,
        ai_enabled: false,
        status: "human_handling",
      }));
      setPermissions({
        can_reply: false,
        can_manage: Boolean(currentUserIsAdmin),
        can_mark_read: Boolean(currentUserIsAdmin),
        can_take_over: false,
      });
      setActionError(`${ownerUserName} owns this conversation. You have read-only access.`);
    } else {
      setActionError(detail.message || requestError.message || "Conversation ownership changed.");
    }

    return true;
  }


  // A 409 with code "stale_version" means someone else changed one of this
  // conversation's control fields after we last loaded it. We must not
  // silently keep either side's data: leave both the local draft and the
  // last-rendered `control` snapshot untouched, and make the conflict
  // visible so the user reloads and reconciles before trying again.
  //
  // `touchedAlias` marks conflicts from a save that included the alias
  // draft: unlike the other control fields (which always mirror the
  // latest polled `control` snapshot straight through), the alias draft
  // is tracked separately (aliasDraftRef/lastSyncedAliasRef) specifically
  // so the 3s silent poll won't clobber an unsaved edit — which also means
  // the poll can never clear a stale-version conflict on its own. Flag it
  // so the UI can offer an explicit "discard edit and reload" recovery.
  function applyVersionConflict(requestError, { touchedAlias = false } = {}) {
    const detail = requestError?.data?.detail;
    if (
      requestError?.status !== 409
      || !detail
      || typeof detail !== "object"
      || detail.code !== "stale_version"
    ) {
      return false;
    }

    setActionError(
      detail.message
      || "Someone else changed this conversation since you loaded it. "
        + "Reload the conversation before saving again.",
    );

    if (touchedAlias) {
      setAliasVersionConflict(true);
    }

    return true;
  }


  // Explicit recovery for a stuck alias stale-version conflict: discards
  // the user's unsaved alias edit (and its pinned version anchor) so the
  // next load can resync from the server, instead of leaving the user
  // stuck re-submitting the same stale edit indefinitely.
  async function discardAliasEditAndReload() {
    aliasDraftRef.current = "";
    setAliasDraft("");
    lastSyncedAliasRef.current = { alias: "", controlVersion: null };
    setAliasVersionConflict(false);
    setActionError("");
    await loadConversation({ silent: true });
  }


  async function runModeAction(request, successMessage) {
    setChangingMode(true);
    setActionError("");
    setActionSuccess("");
    try {
      const result = await request(channel, userId);
      if (result?.conversation) setControl(result.conversation);
      setActionSuccess(successMessage);
      await loadConversation({ silent: true });
      onConversationChanged?.();
    } catch (requestError) {
      if (!applyOwnershipConflict(requestError)) {
        setActionError(requestError.message || "Conversation mode could not be changed.");
      }
      await loadConversation({ silent: true });
    } finally {
      setChangingMode(false);
    }
  }

  function handleTakeOver() {
    return runModeAction(
      takeOverConversationRequest,
      "Conversation assigned to you.",
    );
  }

  async function ensureTakeoverForReply() {
    if (canReply) return true;
    if (isAssignedToOther || !canTakeOver || takeoverInFlightRef.current) {
      if (isAssignedToOther) {
        setActionError(`Assigned to ${control?.assigned_user_name || "another employee"}. You have read-only access.`);
      }
      return false;
    }
    takeoverInFlightRef.current = true;
    setChangingMode(true);
    setActionError("");
    try {
      const result = await takeOverConversationRequest(channel, userId);
      if (result?.conversation) setControl(result.conversation);
      await loadConversation({ silent: true });
      onConversationChanged?.();
      window.setTimeout(() => composerRef.current?.focus(), 0);
      return true;
    } catch (requestError) {
      if (!applyOwnershipConflict(requestError)) {
        setActionError(requestError.message || "Conversation could not be assigned to you.");
      }
      await loadConversation({ silent: true });
      return false;
    } finally {
      takeoverInFlightRef.current = false;
      setChangingMode(false);
    }
  }


  function handleRelease() {
    return runModeAction(
      releaseConversationRequest,
      "Conversation released to the team queue.",
    );
  }

  function handleReturnToAi() {
    return runModeAction(
      returnConversationToAiRequest,
      "Conversation returned to AI.",
    );
  }

  // `expectedControlVersion` lets a caller pin the version its edit was
  // based on (the alias draft does this, since it may have diverged from
  // the continuously-polled `control` snapshot). Callers without an
  // in-flight draft — star/pin/department/assignment/tags/status/priority,
  // which always mirror the latest `control` straight through to the UI —
  // just use whatever `control.control_version` currently is. This is the
  // dedicated per-conversation control_version counter, not the row's
  // general `updated_at` (which is also bumped by unrelated activity like
  // inbound customer messages and would cause spurious conflicts).
  async function handleControlUpdate(updates, successMessage, options = {}) {
    const { expectedControlVersion } = options;
    const versionToSend = Object.prototype.hasOwnProperty.call(options, "expectedControlVersion")
      ? expectedControlVersion
      : control?.control_version ?? null;
    const touchesAlias = Object.prototype.hasOwnProperty.call(updates, "customer_alias");
    // Snapshot exactly what we're sending for the alias field so the
    // success handler can detect whether the user kept typing while the
    // request was in flight, instead of blindly overwriting newer input
    // with the older value that was actually saved.
    const submittedAlias = touchesAlias ? updates.customer_alias : null;

    setSaving(true);
    setActionError("");
    setActionSuccess("");
    if (touchesAlias) {
      setAliasVersionConflict(false);
    }

    try {
      const result = await updateConversationControlRequest(
        channel,
        userId,
        { ...updates, expected_control_version: versionToSend ?? null },
      );

      if (result?.conversation) {
        setControl(result.conversation);
      }

      if (touchesAlias) {
        const savedAlias = result?.conversation?.customer_alias || "";
        const savedControlVersion = result?.conversation?.control_version ?? null;
        // The user's edit anchor must always advance to the version we
        // just saved, whether or not we also update the visible draft —
        // otherwise a later save would incorrectly conflict with this one.
        lastSyncedAliasRef.current = { alias: savedAlias, controlVersion: savedControlVersion };
        // Only overwrite the visible draft (and its ref) if nothing has
        // changed it since we sent this request. If the user kept typing
        // in the meantime, leave their newer text alone — it is now
        // correctly flagged dirty against the version we just recorded
        // above, so the next Save will be checked against it.
        if (aliasDraftRef.current === submittedAlias) {
          aliasDraftRef.current = savedAlias;
          setAliasDraft(savedAlias);
        }
      }

      setActionSuccess(successMessage || "Conversation updated.");
      await loadConversation({ silent: true });
      onConversationChanged?.();
    } catch (requestError) {
      if (applyVersionConflict(requestError, { touchedAlias: touchesAlias })) {
        // Stale version: leave the draft and the rendered control snapshot
        // exactly as they are — no silent overwrite in either direction.
        // The user has to explicitly reload (or use the "discard edit and
        // reload" recovery action for the alias field) to pick up the
        // newer version before they can save again.
      } else {
        if (!applyOwnershipConflict(requestError)) {
          setActionError(
            requestError.message ||
            "Conversation could not be updated.",
          );
        }
        await loadConversation({ silent: true });
      }
    } finally {
      setSaving(false);
    }
  }


  async function handleDepartmentChange(value) {
    await handleControlUpdate(
      {
        department: value,
        clear_assignment: true,
      },
      "Department updated and assignment cleared.",
    );
  }


  async function saveTag() {
    const value = tagDraft.trim();

    if (!value) {
      return;
    }

    const currentTags = control?.tags || [];
    const nextTags = editingTag
      ? currentTags.map((item) => item === editingTag ? value : item)
      : [...currentTags, value];

    setTagDraft("");
    setEditingTag("");
    await handleControlUpdate(
      { tags: Array.from(new Set(nextTags)) },
      editingTag ? "Tag updated." : "Tag added.",
    );
  }


  function startEditingTag(value) {
    setEditingTag(value);
    setTagDraft(value);
  }


  async function removeTag(value) {
    if (editingTag === value) {
      setEditingTag("");
      setTagDraft("");
    }

    await handleControlUpdate(
      {
        tags: (control?.tags || []).filter((item) => item !== value),
      },
      "Tag removed.",
    );
  }


  async function handleExport() {
    setExporting(true);
    setActionError("");

    try {
      await downloadConversationExport(channel, userId, {
        scope: exportScope,
        format: exportFormat,
      });
      setActionSuccess("Export downloaded successfully.");
    } catch (requestError) {
      setActionError(
        requestError.message ||
        "Export could not be generated.",
      );
    } finally {
      setExporting(false);
    }
  }


  async function handleNoteSubmit(event) {
    event.preventDefault();
    const note = noteDraft.trim();

    if (!note || saving) {
      return;
    }

    setSaving(true);
    setActionError("");

    try {
      await addConversationNoteRequest(channel, userId, note);
      setNoteDraft("");
      await loadConversation({ silent: true });
    } catch (requestError) {
      setActionError(
        requestError.message ||
        "Internal note could not be added.",
      );
    } finally {
      setSaving(false);
    }
  }


  async function handleSend(event) {
    event.preventDefault();
    const message = draft.trim();

    if (!message || sending) {
      return;
    }

    setSending(true);
    setActionError("");

    try {
      if (!canReply) {
        const acquired = await ensureTakeoverForReply();
        if (!acquired) return;
      }
      await sendConversationReplyRequest(channel, userId, message);
      setDraft("");
      await loadConversation({ silent: true });
      onConversationChanged?.();
    } catch (requestError) {
      if (!applyOwnershipConflict(requestError)) {
        setActionError(
          requestError.message ||
          "Message could not be sent.",
        );
      }
      await loadConversation({ silent: true });
    } finally {
      setSending(false);
    }
  }


  if (loading) {
    return (
      <div className="conversation-loading-shell">
        <LoadingState
          title="Loading conversation..."
          description="Retrieving messages and conversation data."
        />
      </div>
    );
  }


  if (error) {
    return (
      <div className="conversation-loading-shell">
        <ErrorState
          title="Conversation could not load"
          description={error}
          action={
            <AppButton variant="primary" onClick={() => loadConversation()}>
              Retry
            </AppButton>
          }
        />
      </div>
    );
  }


  return (
    <div
      className={[
        "conversation-detail-shell",
        embedded ? "embedded" : "",
        standalone ? "standalone" : "",
        detailsOpen ? "" : "details-collapsed",
      ].filter(Boolean).join(" ")}
    >
      <header className="conversation-detail-topbar">
        <div className="conversation-person">
          <div className="conversation-detail-avatar">
            {displayCustomerName.charAt(0).toUpperCase()}
          </div>

          <div>
            <strong>{displayCustomerName}</strong>
            {control?.customer_alias ? (
              <span>Official: {officialCustomerName}</span>
            ) : null}
            <span>{humanize(channel)} · {userId}</span>
          </div>
        </div>

        <div className="conversation-topbar-actions">
          <StatusBadge
            status={aiIsHandling ? "active" : "handoff"}
            label={aiIsHandling ? "AI active" : "Human takeover"}
          />

          <button
            type="button"
            className="inbox-icon-button"
            title="Open chat in new tab"
            onClick={openInNewTab}
          >
            <LaunchOutlined />
          </button>
          <button
            type="button"
            className="inbox-icon-button"
            title="Exit chat"
            onClick={exitConversation}
          >
            <CloseOutlined />
          </button>

          <button
            type="button"
            className="inbox-icon-button"
            title={detailsOpen ? "Hide details" : "Show details"}
            onClick={() => setDetailsOpen((current) => !current)}
          >
            {detailsOpen ? <ChevronRightOutlined /> : <ChevronLeftOutlined />}
          </button>

          <button
            type="button"
            className="inbox-icon-button"
            title="Refresh"
            onClick={() => loadConversation({ silent: true })}
          >
            <RefreshOutlined />
          </button>

          {canTakeOver && !canReply ? (
            <AppButton
              variant="danger"
              size="small"
              loading={changingMode}
              icon={<PauseCircleOutlineOutlined />}
              onClick={handleTakeOver}
            >
              Take over
            </AppButton>
          ) : null}

          {canManage && !aiIsHandling && assignedUserId != null ? (
            <AppButton
              variant="secondary"
              size="small"
              loading={changingMode}
              onClick={handleRelease}
            >
              Release
            </AppButton>
          ) : null}

          {canManage && !aiIsHandling ? (
            <AppButton
              variant="success"
              size="small"
              loading={changingMode}
              icon={<AutoAwesomeOutlined />}
              onClick={handleReturnToAi}
            >
              Return to AI
            </AppButton>
          ) : null}

          {canMarkRead ? (
            Number(control?.unread_count || 0) > 0 ? (
              <AppButton
                variant="secondary"
                size="small"
                disabled={saving}
                onClick={() => handleControlUpdate(
                  { is_unread: false },
                  "Conversation marked as read.",
                )}
              >
                Mark as read
              </AppButton>
            ) : (
              <AppButton
                variant="secondary"
                size="small"
                disabled={saving}
                onClick={() => handleControlUpdate(
                  { is_unread: true },
                  "Conversation marked as unread.",
                )}
              >
                Mark as unread
              </AppButton>
            )
          ) : null}
        </div>
      </header>

      {actionError ? (
        <div className="conversation-action-error">{actionError}</div>
      ) : null}

      {actionSuccess ? (
        <div className="conversation-action-success">{actionSuccess}</div>
      ) : null}

      <div className="conversation-detail-grid">
        <section className="conversation-chat-column">
          <div className="conversation-messages">
            {feedItems.map((item) => {
              const message = item.value;
              const direction = resolveDirection(message);
              const senderType = message?.metadata?.sender_type;
              const employeeName = message?.metadata?.employee_name;
              const deliveryStatus =
                message?.metadata?.delivery_status ||
                message?.metadata?.status ||
                (direction === "out" ? "sent" : "");

              return (
                <div
                  className={`conversation-message-row conversation-message-${direction}`}
                  key={item.key}
                >
                  <article className="conversation-message-bubble">
                    <strong className="conversation-message-label">
                      {direction === "in"
                        ? displayCustomerName
                        : senderType === "employee"
                          ? employeeName || "Employee"
                          : "T-ZONE AI"}
                    </strong>

                    <p>{resolveMessageText(message) || "[Unsupported message]"}</p>

                    <div className="message-footer">
                      <time>{formatDateTime(item.createdAt)}</time>
                      {direction === "out" ? (
                        <span className={`delivery-status ${deliveryStatus}`}>
                          {deliveryStatus === "read"
                            ? "✓✓ Read"
                            : deliveryStatus === "delivered"
                              ? "✓✓ Delivered"
                              : deliveryStatus === "failed"
                                ? "Failed"
                                : "✓ Sent"}
                        </span>
                      ) : null}
                    </div>
                  </article>
                </div>
              );
            })}

            <div ref={messagesEndRef} />
          </div>

          {!canReply ? (
            <div className={`conversation-ownership-notice ${isAssignedToOther ? "is-locked" : ""}`}>
              {isAssignedToOther
                ? `${control?.assigned_user_name || "Another employee"} owns this conversation. You can view it, but only the owner or an administrator can control or reply.`
                : "Click the reply box to take ownership automatically. The first employee who clicks receives the exclusive reply lock."}
            </div>
          ) : null}

          <form className="conversation-composer conversation-composer-approved" onSubmit={handleSend}>
            <div className="conversation-composer-row conversation-composer-single-row">
              <label className="composer-tool-button" title="Attach file">
                <AttachFileOutlined />
                <input type="file" hidden />
              </label>

              <label className="composer-tool-button" title="Attach image">
                <ImageOutlined />
                <input type="file" accept="image/*" hidden />
              </label>

              <button className="composer-tool-button" type="button" title="Voice note">
                <MicNoneOutlined />
              </button>

              <textarea
                ref={composerRef}
                value={draft}
                placeholder={
                  canReply
                    ? "Write a reply..."
                    : isAssignedToOther
                      ? `Assigned to ${control?.assigned_user_name || "another employee"}.`
                      : "Click here to take over and reply..."
                }
                readOnly={!canReply}
                aria-readonly={!canReply}
                onFocus={ensureTakeoverForReply}
                onClick={ensureTakeoverForReply}
                onChange={(event) => {
                  if (canReply) setDraft(event.target.value);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    handleSend(event);
                  }
                }}
              />

              <button
                type="submit"
                className="composer-send-circle"
                aria-label="Send message"
                disabled={sending || !draft.trim() || !canReply}
              >
                <SendOutlined />
              </button>
            </div>
          </form>
        </section>

        {detailsOpen ? (
          <aside className="conversation-details-column">
            <section className="detail-card customer-profile-card">
              <div className="customer-profile-head">
                <div className="conversation-detail-avatar">
                  {displayCustomerName.charAt(0).toUpperCase()}
                </div>
                <div>
                  <strong>{officialCustomerName}</strong>
                  {control?.customer_alias ? <span>{control.customer_alias}</span> : null}
                  <small>{humanize(channel)} · {userId}</small>
                </div>
              </div>
              <div className="customer-profile-grid">
                <span><small>Department</small><strong>{control?.department || "Unassigned"}</strong></span>
                <span><small>Assigned to</small><strong>{control?.assigned_user_name || "Unassigned"}</strong></span>
                <span><small>Status</small><strong>{humanize(control?.status || "open")}</strong></span>
                <span><small>Priority</small><strong>{humanize(control?.priority || "normal")}</strong></span>
              </div>
            </section>

            {canManage ? (
              <CollapsiblePanel
              title="Conversation control"
              subtitle="Routing, assignment and customer identity"
              icon={<SupportAgentOutlined />}
              open={controlPanelOpen}
              onToggle={() => setControlPanelOpen((current) => !current)}
            >
              <div className="conversation-control-form">
                <label>
                  <span>Internal customer name</span>
                  <div className="inline-field-action">
                    <input
                      value={aliasDraft}
                      placeholder="Add an internal name..."
                      onChange={(event) => setAliasDraft(event.target.value)}
                    />
                    <AppButton
                      variant="secondary"
                      size="small"
                      disabled={saving}
                      onClick={() => handleControlUpdate(
                        { customer_alias: aliasDraft },
                        "Internal customer name updated.",
                        { expectedControlVersion: lastSyncedAliasRef.current.controlVersion },
                      )}
                    >
                      Save
                    </AppButton>
                  </div>
                  {aliasVersionConflict ? (
                    <div className="inline-field-conflict">
                      <span>
                        This name was changed elsewhere, so your edit can&apos;t be
                        saved as-is.
                      </span>
                      <button
                        type="button"
                        className="inline-text-button"
                        onClick={discardAliasEditAndReload}
                      >
                        Discard my edit &amp; reload
                      </button>
                    </div>
                  ) : null}
                </label>

                <label>
                  <span>Transfer department</span>
                  <select
                    value={selectedDepartment}
                    disabled={saving}
                    onChange={(event) => handleDepartmentChange(event.target.value)}
                  >
                    {departments.map((item) => (
                      <option value={item} key={item}>{item}</option>
                    ))}
                  </select>
                </label>

                <label>
                  <span>Assign to</span>
                  <select
                    value={control?.assigned_user_id ?? ""}
                    disabled={saving || selectedDepartment === "Unassigned"}
                    onChange={(event) => handleControlUpdate(
                      event.target.value
                        ? { assigned_user_id: Number(event.target.value) }
                        : { clear_assignment: true },
                      "Employee assignment updated.",
                    )}
                  >
                    <option value="">
                      {selectedDepartment === "Unassigned"
                        ? "Choose department first"
                        : "Department queue — no specific employee"}
                    </option>
                    {availableEmployees.map((employee) => (
                      <option value={employee.id} key={employee.id}>
                        {employee.display_name}
                        {employee.role_name ? ` — ${employee.role_name}` : ""}
                      </option>
                    ))}
                  </select>
                </label>

                <label>
                  <span>Status</span>
                  <select
                    value={control?.status || "open"}
                    disabled={saving}
                    onChange={(event) => handleControlUpdate(
                      { status: event.target.value },
                      "Conversation status updated.",
                    )}
                  >
                    {STATUS_OPTIONS.map((item) => (
                      <option value={item} key={item}>{humanize(item)}</option>
                    ))}
                  </select>
                </label>

                <label>
                  <span>Priority</span>
                  <select
                    value={control?.priority || "normal"}
                    disabled={saving}
                    onChange={(event) => handleControlUpdate(
                      { priority: event.target.value },
                      "Conversation priority updated.",
                    )}
                  >
                    {PRIORITY_OPTIONS.map((item) => (
                      <option value={item} key={item}>{humanize(item)}</option>
                    ))}
                  </select>
                </label>

                <div className="tags-editor">
                  <span>Tags</span>

                  <div className="tag-manager-row">
                    <select
                      value={editingTag}
                      disabled={saving}
                      onChange={(event) => {
                        const value = event.target.value;
                        setEditingTag(value);
                        setTagDraft(value);
                      }}
                    >
                      <option value="">Add new tag</option>
                      {(control?.tags || []).map((item) => (
                        <option value={item} key={item}>{item}</option>
                      ))}
                    </select>

                    <input
                      value={tagDraft}
                      placeholder={editingTag ? "Edit selected tag..." : "New tag name..."}
                      onChange={(event) => setTagDraft(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          saveTag();
                        }
                      }}
                    />

                    <AppButton
                      variant="secondary"
                      size="small"
                      disabled={saving || !tagDraft.trim()}
                      onClick={saveTag}
                    >
                      {editingTag ? "Save" : "Add"}
                    </AppButton>

                    {editingTag ? (
                      <button
                        type="button"
                        className="tag-delete-button"
                        disabled={saving}
                        onClick={() => removeTag(editingTag)}
                      >
                        Delete
                      </button>
                    ) : null}
                  </div>

                  <div className="tags-list" aria-label="Conversation tags">
                    {(control?.tags || []).map((item) => (
                      <button
                        type="button"
                        key={item}
                        className={editingTag === item ? "is-selected" : ""}
                        title="Edit tag"
                        onClick={() => startEditingTag(item)}
                      >
                        {item}
                      </button>
                    ))}
                    {(control?.tags || []).length === 0 ? (
                      <small className="conversation-muted">No tags yet.</small>
                    ) : null}
                  </div>
                </div>
              </div>
              </CollapsiblePanel>
            ) : null}

            <CollapsiblePanel
              title="Internal notes"
              subtitle="Visible only to employees"
              icon={<NoteAddOutlined />}
              open={notesPanelOpen}
              onToggle={() => setNotesPanelOpen((current) => !current)}
            >
              {canManage ? (
                <form className="conversation-note-form" onSubmit={handleNoteSubmit}>
                  <textarea
                    value={noteDraft}
                    placeholder="Write an internal note..."
                    onChange={(event) => setNoteDraft(event.target.value)}
                  />
                  <AppButton type="submit" variant="primary" size="small" disabled={!noteDraft.trim()}>
                    Add note
                  </AppButton>
                </form>
              ) : null}

              <div className="conversation-note-list">
                {notes.length ? notes.map((note) => (
                  <article key={note.id}>
                    <p>{note.note}</p>
                    <span>By {note.author_name || "Unknown user"}</span>
                    <time>{formatDateTime(note.created_at)}</time>
                  </article>
                )) : (
                  <span className="conversation-muted">No notes yet.</span>
                )}
              </div>
            </CollapsiblePanel>

            <CollapsiblePanel
              title="Export"
              subtitle="Administrative review"
              icon={<DownloadOutlined />}
              open={exportPanelOpen}
              onToggle={() => setExportPanelOpen((current) => !current)}
            >
              <div className="export-form">
                <label>
                  <span>Select what to export</span>
                  <select value={exportScope} onChange={(event) => setExportScope(event.target.value)}>
                    <option value="chat">Chat only</option>
                    <option value="full">Full report</option>
                  </select>
                </label>

                <label>
                  <span>File format</span>
                  <select value={exportFormat} onChange={(event) => setExportFormat(event.target.value)}>
                    <option value="json">JSON</option>
                    <option value="csv">CSV</option>
                  </select>
                </label>

                <AppButton
                  variant="primary"
                  size="small"
                  loading={exporting}
                  onClick={handleExport}
                >
                  Download export
                </AppButton>

                <small>
                  Full report includes the chat, internal notes and conversation metadata.
                </small>
              </div>
            </CollapsiblePanel>
          </aside>
        ) : null}
      </div>
    </div>
  );
}