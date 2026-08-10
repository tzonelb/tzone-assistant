import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  AddTaskOutlined,
  AttachFileOutlined,
  AutoAwesomeOutlined,
  BlockOutlined,
  BuildOutlined,
  CloseOutlined,
  DownloadOutlined,
  EventOutlined,
  ExpandLessOutlined,
  ExpandMoreOutlined,
  FiberManualRecordOutlined,
  FormatQuoteOutlined,
  HistoryOutlined,
  ImageOutlined,
  InsertDriveFileOutlined,
  LaunchOutlined,
  MailOutlineOutlined,
  MicNoneOutlined,
  NoteAddOutlined,
  PersonOutlined,
  ReceiptLongOutlined,
  ReportOutlined,
  RefreshOutlined,
  SendOutlined,
  ShareOutlined,
  StopCircleOutlined,
  SupportAgentOutlined,
} from "@mui/icons-material";

import { useNavigate, useParams } from "react-router-dom";

import {
  addConversationNoteRequest,
  clearConversationReminderRequest,
  createTaskRequest,
  downloadConversationExport,
  getConversationControlRequest,
  getConversationMessagesRequest,
  listSavedRepliesRequest,
  releaseConversationRequest,
  returnConversationToAiRequest,
  sendConversationMediaReplyRequest,
  sendConversationReplyRequest,
  setConversationReminderRequest,
  takeOverConversationRequest,
  updateConversationControlRequest,
  uploadMediaRequest,
  uploadVoiceNoteRequest,
} from "../../api/client";

import { useConversationLive } from "../../contexts/ConversationLiveContext";

import { ErrorState, LoadingState } from "../../components/common";
import { safeHttpUrl } from "../../utils/safeUrl";

import "./ConversationDetailPageV2.css";


// Same data/handlers as ConversationDetailPage.jsx (v1) — this is a visual
// rebuild only (CLAUDE_CODE_UI_IMPLEMENTATION.md §3). Every request call,
// every piece of state and every guard below is copied verbatim from v1;
// only the JSX/classnames changed. See the file header comment in
// ConversationsPageV2.jsx for why v1 stays untouched as the standalone
// route's component.
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


function formatMessageTime(value) {
  const date = parseServerDate(value);
  return date ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
}


// Real day separators — grouped from each message's own timestamp, not a
// fabricated schedule.
function dayLabel(value) {
  const date = parseServerDate(value);
  if (!date) return "Unknown date";
  const startOfDay = new Date(date);
  startOfDay.setHours(0, 0, 0, 0);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  if (startOfDay.getTime() === today.getTime()) return "Today";
  if (startOfDay.getTime() === yesterday.getTime()) return "Yesterday";
  return startOfDay.toLocaleDateString(undefined, {
    month: "long",
    day: "numeric",
    year: startOfDay.getFullYear() === today.getFullYear() ? undefined : "numeric",
  });
}


function renderNoteText(text, employees) {
  const names = (employees || []).map((employee) => employee.full_name).filter(Boolean).sort((a, b) => b.length - a.length);
  if (names.length === 0) return text;
  const pattern = new RegExp(`(@(?:${names.map((name) => name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")}))`, "g");
  return text.split(pattern).map((part, index) => (
    pattern.test(part) ? <strong key={index} className="tzv2-cd-mention">{part}</strong> : <span key={index}>{part}</span>
  ));
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


function MessageMedia({ metadata }) {
  const mediaUrl = safeHttpUrl(metadata?.media_url);
  const mediaType = metadata?.media_type;
  if (!mediaUrl) return null;

  if (mediaType === "image") {
    return (
      <a href={mediaUrl} target="_blank" rel="noopener noreferrer" className="tzv2-cd-media-image">
        <img src={mediaUrl} alt="" />
      </a>
    );
  }

  if (mediaType === "video") {
    return <video controls src={mediaUrl} className="tzv2-cd-media-video" />;
  }

  if (mediaType === "audio") {
    return <audio controls src={mediaUrl} className="tzv2-cd-media-audio" />;
  }

  return (
    <a href={mediaUrl} target="_blank" rel="noopener noreferrer" className="tzv2-cd-media-file">
      <AttachFileOutlined fontSize="small" />
      <span>{metadata?.media_filename || "Download file"}</span>
    </a>
  );
}


function AccordionCard({
  title,
  subtitle,
  icon,
  open,
  onToggle,
  children,
}) {
  return (
    <section className="card tzv2-cd-acc">
      <button type="button" className="tzv2-cd-acc-head" onClick={onToggle}>
        <span className="tzv2-cd-acc-icon">{icon}</span>
        <span className="tzv2-cd-acc-heading">
          <strong>{title}</strong>
          {subtitle ? <small>{subtitle}</small> : null}
        </span>
        {open ? <ExpandLessOutlined fontSize="small" /> : <ExpandMoreOutlined fontSize="small" />}
      </button>

      {open ? <div className="tzv2-cd-acc-body">{children}</div> : null}
    </section>
  );
}


export default function ConversationDetailPageV2({
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

  const [messages, setMessages] = useState([]);
  const [control, setControl] = useState(null);
  const [notes, setNotes] = useState([]);
  const [events, setEvents] = useState([]);
  const [departments, setDepartments] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [currentUserId, setCurrentUserId] = useState(null);
  const [currentUserIsAdmin, setCurrentUserIsAdmin] = useState(false);
  const [permissions, setPermissions] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [sending, setSending] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [changingMode, setChangingMode] = useState(false);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionSuccess, setActionSuccess] = useState("");
  const [draft, setDraft] = useState("");
  const [noteDraft, setNoteDraft] = useState("");
  const [noteMentionedUserIds, setNoteMentionedUserIds] = useState([]);
  const [noteMentionQuery, setNoteMentionQuery] = useState(null);
  const [aliasDraft, setAliasDraft] = useState("");
  const [reminderDraft, setReminderDraft] = useState("");
  const [reminderNoteDraft, setReminderNoteDraft] = useState("");
  const [reminderAutoSendDraft, setReminderAutoSendDraft] = useState(false);
  const [reminderMessageTextDraft, setReminderMessageTextDraft] = useState("");
  const [tagDraft, setTagDraft] = useState("");
  const [editingTag, setEditingTag] = useState("");
  const [exportScope, setExportScope] = useState("full");
  const [exportFormat, setExportFormat] = useState("json");
  // Drawer overlays the conversation column per spec — closed until the
  // customer's name is clicked, unlike v1's always-open side column.
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [timelinePanelOpen, setTimelinePanelOpen] = useState(true);
  const [customerPanelOpen, setCustomerPanelOpen] = useState(false);
  const [controlPanelOpen, setControlPanelOpen] = useState(false);
  const [createPanelOpen, setCreatePanelOpen] = useState(false);
  const [notesPanelOpen, setNotesPanelOpen] = useState(false);
  const [exportPanelOpen, setExportPanelOpen] = useState(false);
  const [moderationPanelOpen, setModerationPanelOpen] = useState(false);
  const [savedRepliesOpen, setSavedRepliesOpen] = useState(false);
  const [savedReplies, setSavedReplies] = useState([]);
  const [pendingMedia, setPendingMedia] = useState(null);
  const [mediaUploading, setMediaUploading] = useState(false);
  const [mediaError, setMediaError] = useState("");
  const [recording, setRecording] = useState(false);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const attachmentInputRef = useRef(null);
  const imageInputRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const recordedChunksRef = useRef([]);
  const recordingStreamRef = useRef(null);
  const recordingCancelledRef = useRef(false);
  const recordingIntervalRef = useRef(null);

  useEffect(() => {
    listSavedRepliesRequest()
      .then((result) => setSavedReplies(result?.replies || []))
      .catch(() => {
        // Non-critical — the composer works fine without saved replies loaded.
      });
  }, []);

  const [, setClockTick] = useState(0);

  const loadConversation = useCallback(
    async ({ silent = false } = {}) => {
      if (!channel || !userId) {
        setLoading(false);
        return;
      }

      if (!silent) {
        setLoading(true);
      }

      setError("");

      try {
        // Load messages first: the backend records the owner opening the chat
        // and clears unread before the control state is fetched. This avoids
        // a race where the unread badge remained visible until the next poll.
        // Silent background polls don't mark-read - otherwise clicking
        // "mark as unread" while still viewing the conversation would get
        // silently undone by the very next 3-second refresh tick.
        const messagesResult = await getConversationMessagesRequest(
          channel,
          userId,
          300,
          !silent,
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
        setEvents(
          Array.isArray(controlResult?.events)
            ? controlResult.events
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
        setAliasDraft(controlResult?.conversation?.customer_alias || "");
      } catch (requestError) {
        setError(
          requestError.message ||
          "Conversation could not be loaded.",
        );
      } finally {
        setLoading(false);
      }
    },
    [channel, userId],
  );

  useEffect(() => {
    setMessages([]);
    setControl(null);
    setNotes([]);
    setEvents([]);
    setDraft("");
    setActionError("");
    setActionSuccess("");
    loadConversation();
  }, [channel, userId, loadConversation]);


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
  const isAssignedToOther = assignedUserId != null && !isAssignedToMe;
  // Server permissions are authoritative.  Never infer ownership controls
  // from locally cached assignment data because another employee may have
  // acquired the lock in a different browser.
  const canReply = Boolean(permissions?.can_reply);
  const canManage = Boolean(permissions?.can_manage);
  const canMarkRead = Boolean(permissions?.can_mark_read);
  const canTakeOver = Boolean(permissions?.can_take_over) && (!isAssignedToOther || currentUserIsAdmin);

  const selectedDepartment = control?.department || "Unassigned";

  // Real audit-trail lookup for the AI-state banner ("who owns the chat,
  // when they took it") — derived from the conversation's own timeline
  // events, never fabricated. There is no confidence-score field anywhere
  // in this system's conversation/message payloads, so unlike the spec's
  // mention of "the confidence that triggered escalation" that part of the
  // banner is intentionally omitted rather than invented.
  const latestTakeoverEvent = useMemo(() => (
    events
      .filter((item) => item.event_type === "human_takeover")
      .sort((a, b) => toTimestamp(b.created_at) - toTimestamp(a.created_at))[0] || null
  ), [events]);

  const bannerText = aiIsHandling
    ? "T-ZONE AI is handling this conversation automatically."
    : assignedUserId != null
      ? `${isAssignedToMe ? "You" : control?.assigned_user_name || "An employee"} took over this conversation${latestTakeoverEvent ? ` on ${formatDateTime(latestTakeoverEvent.created_at)}` : ""}.`
      : "Waiting in the team queue — no one has taken this conversation over yet.";

  // Employees only see saved replies relevant to this conversation's department
  // (plus general ones with no department). Admins see the full library.
  const visibleSavedReplies = useMemo(() => {
    if (currentUserIsAdmin) return savedReplies;
    const dept = control?.department || "";
    return savedReplies.filter((reply) => {
      const replyDept = reply.department || "";
      return replyDept === "" || replyDept === dept;
    });
  }, [savedReplies, currentUserIsAdmin, control?.department]);
  const availableEmployees = useMemo(() => {
    if (selectedDepartment === "Unassigned") {
      return [];
    }

    // An employee with no department memberships yet hasn't been scoped
    // by an owner — keep them assignable everywhere instead of hiding
    // unassigned staff from every department's queue.
    return employees.filter((employee) => {
      const employeeDepartments = employee.departments || [];
      return employeeDepartments.length === 0 || employeeDepartments.includes(selectedDepartment);
    });
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

  // Real day separators: group the already-sorted feed by calendar day.
  const dayGroups = useMemo(() => {
    const groups = [];
    for (const item of feedItems) {
      const label = dayLabel(item.createdAt);
      const lastGroup = groups[groups.length - 1];
      if (lastGroup && lastGroup.label === label) {
        lastGroup.items.push(item);
      } else {
        groups.push({ label, items: [item] });
      }
    }
    return groups;
  }, [feedItems]);

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
    if ((isAssignedToOther && !currentUserIsAdmin) || !canTakeOver || takeoverInFlightRef.current) {
      if (isAssignedToOther && !currentUserIsAdmin) {
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

  async function handleControlUpdate(updates, successMessage) {
    setSaving(true);
    setActionError("");
    setActionSuccess("");

    try {
      const result = await updateConversationControlRequest(
        channel,
        userId,
        updates,
      );

      if (result?.conversation) {
        setControl(result.conversation);
      }

      setActionSuccess(successMessage || "Conversation updated.");
      await loadConversation({ silent: true });
      onConversationChanged?.();
    } catch (requestError) {
      if (!applyOwnershipConflict(requestError)) {
        setActionError(
          requestError.message ||
          "Conversation could not be updated.",
        );
      }
      await loadConversation({ silent: true });
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


  async function saveReminder() {
    if (!reminderDraft) return;
    if (reminderAutoSendDraft && !reminderMessageTextDraft.trim()) return;
    setSaving(true);
    setActionError("");
    setActionSuccess("");
    try {
      const isoValue = new Date(reminderDraft).toISOString();
      const result = await setConversationReminderRequest(
        channel,
        userId,
        isoValue,
        reminderNoteDraft,
        reminderAutoSendDraft,
        reminderAutoSendDraft ? reminderMessageTextDraft.trim() : "",
      );
      setControl(result);
      setActionSuccess("Reminder set.");
      setReminderDraft("");
      setReminderNoteDraft("");
      setReminderAutoSendDraft(false);
      setReminderMessageTextDraft("");
    } catch (requestError) {
      setActionError(requestError.message || "Reminder could not be set.");
    } finally {
      setSaving(false);
    }
  }

  async function clearReminder() {
    setSaving(true);
    setActionError("");
    try {
      const result = await clearConversationReminderRequest(channel, userId);
      setControl(result);
      setActionSuccess("Reminder cleared.");
    } catch (requestError) {
      setActionError(requestError.message || "Reminder could not be cleared.");
    } finally {
      setSaving(false);
    }
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
      await addConversationNoteRequest(channel, userId, note, noteMentionedUserIds);
      setNoteDraft("");
      setNoteMentionedUserIds([]);
      setNoteMentionQuery(null);
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

  function handleNoteDraftChange(event) {
    const value = event.target.value;
    setNoteDraft(value);
    const cursor = event.target.selectionStart;
    const upToCursor = value.slice(0, cursor);
    const match = upToCursor.match(/@([^\s@]*)$/);
    setNoteMentionQuery(match ? match[1] : null);
  }

  function insertNoteMention(employee) {
    const textarea = document.getElementById("conversation-note-textarea-v2");
    const cursor = textarea ? textarea.selectionStart : noteDraft.length;
    const upToCursor = noteDraft.slice(0, cursor);
    const replaced = upToCursor.replace(/@([^\s@]*)$/, `@${employee.full_name || employee.display_name} `);
    setNoteDraft(`${replaced}${noteDraft.slice(cursor)}`);
    setNoteMentionQuery(null);
    setNoteMentionedUserIds((current) => (current.includes(employee.id) ? current : [...current, employee.id]));
  }

  async function createTaskFromConversation() {
    setActionError("");
    try {
      await createTaskRequest({
        title: `Follow up: ${officialCustomerName || userId}`,
        task_type: "follow_up",
        conversation_id: control?.id,
        customer_id: control?.customer_id || undefined,
      });
      setActionSuccess("Task created — see it on the Tasks page.");
      window.setTimeout(() => setActionSuccess(""), 4000);
    } catch (requestError) {
      setActionError(requestError.message || "Could not create a task from this conversation.");
    }
  }


  async function handleSend(event) {
    event.preventDefault();
    const message = draft.trim();

    if (recording || mediaUploading) {
      return;
    }

    if (!pendingMedia && (!message || sending)) {
      return;
    }

    setSending(true);
    setActionError("");

    try {
      if (!canReply) {
        const acquired = await ensureTakeoverForReply();
        if (!acquired) return;
      }
      if (pendingMedia) {
        await sendConversationMediaReplyRequest(channel, userId, {
          mediaUrl: pendingMedia.url,
          mediaType: pendingMedia.mediaType,
          caption: message,
          filename: pendingMedia.filename,
        });
        setPendingMedia(null);
      } else {
        await sendConversationReplyRequest(channel, userId, message);
      }
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


  const DOCUMENT_ACCEPT = ".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.csv,.txt,.zip,.rar";


  async function uploadPendingFile(file) {
    setMediaError("");
    setMediaUploading(true);
    try {
      const result = await uploadMediaRequest(file);
      setPendingMedia({
        url: result.url,
        mediaType: result.media_type,
        filename: result.filename || file.name,
      });
    } catch (requestError) {
      setMediaError(requestError.message || "Could not upload this file.");
    } finally {
      setMediaUploading(false);
    }
  }


  async function handlePickerChange(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;

    if (!canReply) {
      const acquired = await ensureTakeoverForReply();
      if (!acquired) return;
    }
    await uploadPendingFile(file);
  }


  function pickAttachment() {
    attachmentInputRef.current?.click();
  }


  function pickImage() {
    imageInputRef.current?.click();
  }


  function removePendingMedia() {
    setPendingMedia(null);
    setMediaError("");
  }


  function stopRecordingTimer() {
    if (recordingIntervalRef.current) {
      window.clearInterval(recordingIntervalRef.current);
      recordingIntervalRef.current = null;
    }
  }


  function stopRecordingStream() {
    recordingStreamRef.current?.getTracks().forEach((track) => track.stop());
    recordingStreamRef.current = null;
  }


  async function startVoiceRecording() {
    setMediaError("");

    if (!canReply) {
      const acquired = await ensureTakeoverForReply();
      if (!acquired) return;
    }

    if (!navigator.mediaDevices?.getUserMedia) {
      setMediaError("This browser cannot record audio.");
      return;
    }

    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setMediaError("Microphone permission was denied.");
      return;
    }

    const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"]
      .find((candidate) => window.MediaRecorder?.isTypeSupported?.(candidate));

    let recorder;
    try {
      recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    } catch {
      stream.getTracks().forEach((track) => track.stop());
      setMediaError("This browser cannot record audio.");
      return;
    }

    recordingStreamRef.current = stream;
    recordedChunksRef.current = [];
    recordingCancelledRef.current = false;

    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        recordedChunksRef.current.push(event.data);
      }
    };

    recorder.onstop = async () => {
      stopRecordingStream();
      stopRecordingTimer();
      setRecording(false);

      if (recordingCancelledRef.current || recordedChunksRef.current.length === 0) {
        recordedChunksRef.current = [];
        return;
      }

      const blob = new Blob(recordedChunksRef.current, { type: recorder.mimeType || "audio/webm" });
      recordedChunksRef.current = [];
      const extension = recorder.mimeType?.includes("ogg") ? "ogg" : "webm";
      const file = new File([blob], `voice-note.${extension}`, { type: blob.type });

      setMediaUploading(true);
      setMediaError("");
      try {
        const result = await uploadVoiceNoteRequest(file);
        setPendingMedia({
          url: result.url,
          mediaType: result.media_type,
          filename: "Voice note",
        });
      } catch (requestError) {
        setMediaError(requestError.message || "Could not process the voice note.");
      } finally {
        setMediaUploading(false);
      }
    };

    mediaRecorderRef.current = recorder;
    recorder.start();
    setRecording(true);
    setRecordingSeconds(0);
    recordingIntervalRef.current = window.setInterval(
      () => setRecordingSeconds((current) => current + 1),
      1000,
    );
  }


  function stopVoiceRecording() {
    recordingCancelledRef.current = false;
    mediaRecorderRef.current?.stop();
  }


  function cancelVoiceRecording() {
    recordingCancelledRef.current = true;
    mediaRecorderRef.current?.stop();
  }


  useEffect(() => () => {
    stopRecordingTimer();
    stopRecordingStream();
  }, []);


  if (loading) {
    return (
      <div className="tzv2-cd-loading-shell">
        <LoadingState
          title="Loading conversation..."
          description="Retrieving messages and conversation data."
        />
      </div>
    );
  }


  if (error) {
    return (
      <div className="tzv2-cd-loading-shell">
        <ErrorState
          title="Conversation could not load"
          description={error}
          action={
            <button type="button" className="btn btn-primary" onClick={() => loadConversation()}>
              Retry
            </button>
          }
        />
      </div>
    );
  }


  return (
    <div
      className={[
        "tzv2-cd-shell",
        embedded ? "is-embedded" : "",
        standalone ? "is-standalone" : "",
      ].filter(Boolean).join(" ")}
    >
      <header className="tzv2-cd-header">
        <button type="button" className="tzv2-cd-person" onClick={() => setDetailsOpen(true)}>
          <span className="tzv2-cd-avatar">{displayCustomerName.charAt(0).toUpperCase()}</span>
          <span className="tzv2-cd-person-info">
            <strong>{displayCustomerName}</strong>
            <span className="tzv2-cd-meta">
              {humanize(channel)} · {userId}
              {control?.customer_alias ? ` · Official: ${officialCustomerName}` : ""}
            </span>
          </span>
        </button>

        <span className={`tag ${aiIsHandling ? "tag-accent-2" : isAssignedToMe ? "tag-accent" : "tag-outline"}`}>
          {aiIsHandling
            ? "AI active"
            : assignedUserId == null
              ? "Unassigned"
              : isAssignedToMe
                ? "Assigned to you"
                : `Assigned to ${control?.assigned_user_name || "another employee"}`}
        </span>

        <div className="tzv2-cd-header-actions">
          {canTakeOver && !canReply ? (
            <button type="button" className="btn btn-primary" disabled={changingMode} onClick={handleTakeOver}>
              Take over
            </button>
          ) : null}

          {canManage && !aiIsHandling && assignedUserId != null ? (
            <button type="button" className="btn btn-secondary" disabled={changingMode} onClick={handleRelease}>
              Release
            </button>
          ) : null}

          {canManage && !aiIsHandling ? (
            <button type="button" className="btn btn-secondary" disabled={changingMode} onClick={handleReturnToAi}>
              <AutoAwesomeOutlined fontSize="small" /> Return to AI
            </button>
          ) : null}

          {canMarkRead ? (
            Number(control?.unread_count || 0) > 0 ? (
              <button type="button" className="btn btn-ghost" disabled={saving} onClick={() => handleControlUpdate({ is_unread: false }, "Conversation marked as read.")}>
                Mark as read
              </button>
            ) : (
              <button type="button" className="btn btn-ghost" disabled={saving} onClick={() => handleControlUpdate({ is_unread: true }, "Conversation marked as unread.")}>
                Mark as unread
              </button>
            )
          ) : null}

          <button type="button" className="btn btn-ghost btn-icon" title="Open chat in new tab" onClick={openInNewTab}>
            <LaunchOutlined fontSize="small" />
          </button>
          <button type="button" className="btn btn-ghost btn-icon" title="Refresh" onClick={() => loadConversation({ silent: true })}>
            <RefreshOutlined fontSize="small" />
          </button>
          <button type="button" className="btn btn-ghost btn-icon" title="Close chat" onClick={exitConversation}>
            <CloseOutlined fontSize="small" />
          </button>
        </div>
      </header>

      <div className="tzv2-cd-banner">{bannerText}</div>

      {actionError ? <div className="tzv2-cd-alert tzv2-cd-alert-error">{actionError}</div> : null}
      {actionSuccess ? <div className="tzv2-cd-alert tzv2-cd-alert-success">{actionSuccess}</div> : null}

      <div className="tzv2-cd-body">
        <section className="tzv2-cd-chat">
          <div className="tzv2-cd-messages">
            {dayGroups.map((group) => (
              <div key={group.label} className="tzv2-cd-day-group">
                <div className="tzv2-cd-day-sep"><span>{group.label}</span></div>
                {group.items.map((item) => {
                  const message = item.value;
                  const direction = resolveDirection(message);
                  const senderType = message?.metadata?.sender_type;
                  const employeeName = message?.metadata?.employee_name;
                  const deliveryStatus =
                    message?.delivery_status ||
                    message?.metadata?.delivery_status ||
                    message?.metadata?.status ||
                    (direction === "out" ? "sent" : "");

                  return (
                    <div className={`tzv2-cd-msg-row tzv2-cd-msg-${direction}`} key={item.key}>
                      <article className="tzv2-cd-bubble">
                        <strong className="tzv2-cd-msg-label">
                          {direction === "in"
                            ? displayCustomerName
                            : senderType === "employee"
                              ? employeeName || "Employee"
                              : "T-ZONE AI"}
                        </strong>

                        <MessageMedia metadata={message?.metadata} />

                        {resolveMessageText(message) || !message?.metadata?.media_url ? (
                          <p>{resolveMessageText(message) || "[Unsupported message]"}</p>
                        ) : null}

                        <div className="tzv2-cd-msg-footer">
                          <time>{formatMessageTime(item.createdAt)}</time>
                          {direction === "out" ? (
                            <span
                              className={`tzv2-cd-receipt tzv2-cd-receipt-${deliveryStatus}`}
                              title={
                                deliveryStatus === "read" ? "Read"
                                  : deliveryStatus === "delivered" ? "Delivered"
                                    : deliveryStatus === "failed" ? "Failed to send"
                                      : "Sent"
                              }
                            >
                              {deliveryStatus === "failed" ? "!" : deliveryStatus === "sent" ? "✓" : "✓✓"}
                            </span>
                          ) : null}
                        </div>
                      </article>
                    </div>
                  );
                })}
              </div>
            ))}

            <div ref={messagesEndRef} />
          </div>

          {!canReply ? (
            <div className={`tzv2-cd-ownership-notice ${isAssignedToOther ? "is-locked" : ""}`}>
              {isAssignedToOther
                ? `${control?.assigned_user_name || "Another employee"} owns this conversation. You can view it, but only the owner or an administrator can control or reply.`
                : "Click the reply box to take ownership automatically. The first employee who clicks receives the exclusive reply lock."}
            </div>
          ) : null}

          <div className="tzv2-cd-composer-wrap">
            <input
              ref={attachmentInputRef}
              type="file"
              accept={DOCUMENT_ACCEPT}
              style={{ display: "none" }}
              onChange={handlePickerChange}
            />
            <input
              ref={imageInputRef}
              type="file"
              accept="image/*"
              style={{ display: "none" }}
              onChange={handlePickerChange}
            />

            {mediaError ? <div className="tzv2-cd-alert tzv2-cd-alert-error tzv2-cd-composer-alert">{mediaError}</div> : null}

            {recording ? (
              <div className="tzv2-cd-composer-status">
                <FiberManualRecordOutlined className="tzv2-cd-rec-dot" fontSize="small" />
                <span>Recording… {formatTimer(recordingSeconds)}</span>
                <div className="tzv2-cd-composer-status-spacer" />
                <button type="button" className="btn btn-secondary" onClick={cancelVoiceRecording}>Cancel</button>
                <button type="button" className="btn btn-primary" onClick={stopVoiceRecording}>
                  <StopCircleOutlined fontSize="small" /> Stop
                </button>
              </div>
            ) : pendingMedia ? (
              <div className="tzv2-cd-composer-status">
                {pendingMedia.mediaType === "image" ? (
                  <img src={pendingMedia.url} alt="" className="tzv2-cd-pending-thumb" />
                ) : pendingMedia.mediaType === "audio" ? (
                  <MicNoneOutlined fontSize="small" />
                ) : pendingMedia.mediaType === "video" ? (
                  <ImageOutlined fontSize="small" />
                ) : (
                  <InsertDriveFileOutlined fontSize="small" />
                )}
                <span>{pendingMedia.filename || humanize(pendingMedia.mediaType)}</span>
                <div className="tzv2-cd-composer-status-spacer" />
                <button type="button" className="btn btn-ghost btn-icon" title="Remove" onClick={removePendingMedia}>
                  <CloseOutlined fontSize="small" />
                </button>
              </div>
            ) : mediaUploading ? (
              <div className="tzv2-cd-composer-status"><span>Uploading…</span></div>
            ) : null}

            <form className="tzv2-cd-composer" onSubmit={handleSend}>
              <button type="button" className="btn btn-ghost btn-icon" title="Attachment" disabled={recording} onClick={pickAttachment}>
                <AttachFileOutlined fontSize="small" />
              </button>
              <button type="button" className="btn btn-ghost btn-icon" title="Image" disabled={recording} onClick={pickImage}>
                <ImageOutlined fontSize="small" />
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-icon"
                title="Saved replies"
                disabled={recording}
                onClick={() => setSavedRepliesOpen((current) => !current)}
              >
                <FormatQuoteOutlined fontSize="small" />
              </button>

              <textarea
                ref={composerRef}
                className="tzv2-cd-composer-input"
                value={draft}
                placeholder={
                  canReply
                    ? pendingMedia
                      ? "Add a caption (optional)..."
                      : "Write a reply..."
                    : isAssignedToOther
                      ? `Assigned to ${control?.assigned_user_name || "another employee"}.`
                      : "Click here to take over and reply..."
                }
                readOnly={!canReply}
                aria-readonly={!canReply}
                onClick={ensureTakeoverForReply}
                onChange={(event) => {
                  if (canReply) setDraft(event.target.value);
                }}
                onKeyDown={(event) => {
                  if ((event.key === "Enter" && (event.metaKey || event.ctrlKey)) || (event.key === "Enter" && !event.shiftKey)) {
                    event.preventDefault();
                    handleSend(event);
                  }
                }}
              />

              <button
                type="button"
                className={`btn btn-ghost btn-icon ${recording ? "is-recording" : ""}`}
                title="Record a voice note"
                disabled={recording || mediaUploading}
                onClick={startVoiceRecording}
              >
                <MicNoneOutlined fontSize="small" />
              </button>

              <button
                type="submit"
                className="btn btn-primary btn-icon tzv2-cd-send"
                aria-label="Send message"
                disabled={sending || recording || mediaUploading || (!pendingMedia && !draft.trim()) || !canReply}
              >
                <SendOutlined fontSize="small" />
              </button>
            </form>

            {savedRepliesOpen ? (
              <div className="tzv2-cd-popover tzv2-cd-saved-replies">
                {visibleSavedReplies.length ? visibleSavedReplies.map((reply) => (
                  <article
                    key={reply.id}
                    className="tzv2-cd-saved-reply"
                    onClick={async () => {
                      const acquired = await ensureTakeoverForReply();
                      if (!acquired) return;
                      setDraft((current) => (current ? `${current} ${reply.body}` : reply.body));
                      setSavedRepliesOpen(false);
                      composerRef.current?.focus();
                    }}
                  >
                    <p>{reply.title}</p>
                    <span>{reply.body}</span>
                  </article>
                )) : (
                  <span className="tzv2-cd-saved-replies-empty">No saved replies for this department yet — admins can add some from the Saved Replies page.</span>
                )}
              </div>
            ) : null}
          </div>
        </section>

        {detailsOpen ? (
          <aside className="tzv2-cd-drawer">
            <div className="tzv2-cd-drawer-head">
              <strong>Conversation details</strong>
              <button type="button" className="btn btn-ghost btn-icon" aria-label="Close details" onClick={() => setDetailsOpen(false)}>
                <CloseOutlined fontSize="small" />
              </button>
            </div>

            <div className="tzv2-cd-drawer-body">
              <AccordionCard
                title="Timeline"
                subtitle="Full history of this conversation"
                icon={<HistoryOutlined fontSize="small" />}
                open={timelinePanelOpen}
                onToggle={() => setTimelinePanelOpen((current) => !current)}
              >
                <div className="tzv2-cd-note-list">
                  {events.length ? events.map((eventItem) => (
                    <article key={eventItem.id}>
                      <p>{humanize(eventItem.event_type)}</p>
                      <span>By {eventItem.actor_name || "System"}</span>
                      <time>{formatDateTime(eventItem.created_at)}</time>
                    </article>
                  )) : (
                    <span className="tzv2-cd-muted">No timeline events yet.</span>
                  )}
                </div>
              </AccordionCard>

              <AccordionCard
                title="Customer"
                subtitle="Identity and current routing"
                icon={<PersonOutlined fontSize="small" />}
                open={customerPanelOpen}
                onToggle={() => setCustomerPanelOpen((current) => !current)}
              >
                <div className="tzv2-cd-customer-grid">
                  <span><small>Official name</small><strong>{officialCustomerName}</strong></span>
                  {control?.customer_alias ? <span><small>Internal name</small><strong>{control.customer_alias}</strong></span> : null}
                  <span><small>Department</small><strong>{control?.department || "Unassigned"}</strong></span>
                  <span><small>Assigned to</small><strong>{control?.assigned_user_name || "Unassigned"}</strong></span>
                  <span><small>Status</small><strong>{humanize(control?.status || "open")}</strong></span>
                  <span><small>Priority</small><strong>{humanize(control?.priority || "normal")}</strong></span>
                </div>
              </AccordionCard>

              {canManage ? (
                <AccordionCard
                  title="Conversation control"
                  subtitle="Transfer, department, snooze, reopen"
                  icon={<SupportAgentOutlined fontSize="small" />}
                  open={controlPanelOpen}
                  onToggle={() => setControlPanelOpen((current) => !current)}
                >
                  <div className="tzv2-cd-control-form">
                    <div className="field">
                      <label>Internal customer name</label>
                      <div className="tzv2-cd-field-action">
                        <input
                          className="input"
                          value={aliasDraft}
                          placeholder="Add an internal name..."
                          onChange={(event) => setAliasDraft(event.target.value)}
                        />
                        <button
                          type="button"
                          className="btn btn-secondary"
                          disabled={saving}
                          onClick={() => handleControlUpdate({ customer_alias: aliasDraft }, "Internal customer name updated.")}
                        >
                          Save
                        </button>
                      </div>
                    </div>

                    <div className="field">
                      <label>Transfer department</label>
                      <select
                        className="input"
                        value={selectedDepartment}
                        disabled={saving}
                        onChange={(event) => handleDepartmentChange(event.target.value)}
                      >
                        {departments.map((item) => <option value={item} key={item}>{item}</option>)}
                      </select>
                    </div>

                    <div className="field">
                      <label>Assign to</label>
                      <select
                        className="input"
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
                          {selectedDepartment === "Unassigned" ? "Choose department first" : "Department queue — no specific employee"}
                        </option>
                        {availableEmployees.map((employee) => (
                          <option value={employee.id} key={employee.id}>
                            {employee.display_name}{employee.role_name ? ` — ${employee.role_name}` : ""}
                          </option>
                        ))}
                      </select>
                    </div>

                    <div className="field">
                      <label>Status</label>
                      <select
                        className="input"
                        value={control?.status || "open"}
                        disabled={saving}
                        onChange={(event) => handleControlUpdate({ status: event.target.value }, "Conversation status updated.")}
                      >
                        {STATUS_OPTIONS.map((item) => <option value={item} key={item}>{humanize(item)}</option>)}
                      </select>
                    </div>

                    <div className="field">
                      <label>Priority</label>
                      <select
                        className="input"
                        value={control?.priority || "normal"}
                        disabled={saving}
                        onChange={(event) => handleControlUpdate({ priority: event.target.value }, "Conversation priority updated.")}
                      >
                        {PRIORITY_OPTIONS.map((item) => <option value={item} key={item}>{humanize(item)}</option>)}
                      </select>
                    </div>

                    <div className="tzv2-cd-tags-editor">
                      <span>Tags</span>
                      <div className="tzv2-cd-tag-manager-row">
                        <select
                          className="input"
                          value={editingTag}
                          disabled={saving}
                          onChange={(event) => {
                            const value = event.target.value;
                            setEditingTag(value);
                            setTagDraft(value);
                          }}
                        >
                          <option value="">Add new tag</option>
                          {(control?.tags || []).map((item) => <option value={item} key={item}>{item}</option>)}
                        </select>

                        <input
                          className="input"
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

                        <button type="button" className="btn btn-secondary" disabled={saving || !tagDraft.trim()} onClick={saveTag}>
                          {editingTag ? "Save" : "Add"}
                        </button>

                        {editingTag ? (
                          <button type="button" className="btn btn-ghost" disabled={saving} onClick={() => removeTag(editingTag)}>
                            Delete
                          </button>
                        ) : null}
                      </div>

                      <div className="tzv2-cd-tag-list" aria-label="Conversation tags">
                        {(control?.tags || []).map((item) => (
                          <button
                            type="button"
                            key={item}
                            className={`tag ${editingTag === item ? "tag-accent" : "tag-outline"}`}
                            title="Edit tag"
                            onClick={() => startEditingTag(item)}
                          >
                            {item}
                          </button>
                        ))}
                        {(control?.tags || []).length === 0 ? <small className="tzv2-cd-muted">No tags yet.</small> : null}
                      </div>
                    </div>

                    <div className="field">
                      <label>Follow-up reminder (snooze)</label>
                      <div className="tzv2-cd-field-action">
                        <input
                          type="datetime-local"
                          className="input"
                          value={reminderDraft}
                          disabled={saving}
                          onChange={(event) => setReminderDraft(event.target.value)}
                        />
                        <button
                          type="button"
                          className="btn btn-secondary"
                          disabled={saving || !reminderDraft || (reminderAutoSendDraft && !reminderMessageTextDraft.trim())}
                          onClick={saveReminder}
                        >
                          {control?.reminder_at ? "Update" : "Set"}
                        </button>
                      </div>
                      {control?.reminder_at ? (
                        <small className="tzv2-cd-muted">
                          Reminder set for {new Date(control.reminder_at).toLocaleString()}
                          {" — "}
                          <button type="button" className="tzv2-cd-inline-link" disabled={saving} onClick={clearReminder}>Clear</button>
                        </small>
                      ) : null}
                      {control?.reminder_auto_send ? (
                        <div className="tzv2-cd-reminder-armed">
                          <span className="tag tag-accent">Auto follow-up armed</span>
                          {control?.reminder_message_text ? (
                            <small className="tzv2-cd-muted">&ldquo;{control.reminder_message_text}&rdquo;</small>
                          ) : null}
                        </div>
                      ) : null}
                      <input
                        type="text"
                        className="input tzv2-cd-reminder-note"
                        placeholder="What to follow up about (optional)"
                        value={reminderNoteDraft}
                        disabled={saving}
                        onChange={(event) => setReminderNoteDraft(event.target.value)}
                      />
                      <label className="tzv2-cd-checkbox-row">
                        <input
                          type="checkbox"
                          checked={reminderAutoSendDraft}
                          disabled={saving}
                          onChange={(event) => {
                            const checked = event.target.checked;
                            setReminderAutoSendDraft(checked);
                            if (!checked) setReminderMessageTextDraft("");
                          }}
                        />
                        <span>Auto-send this as a follow-up message</span>
                      </label>
                      {reminderAutoSendDraft ? (
                        <textarea
                          className="input"
                          placeholder="Exact message to send automatically when the reminder fires..."
                          value={reminderMessageTextDraft}
                          disabled={saving}
                          onChange={(event) => setReminderMessageTextDraft(event.target.value)}
                        />
                      ) : null}
                    </div>
                  </div>
                </AccordionCard>
              ) : null}

              <AccordionCard
                title="Create from this chat"
                subtitle="Turn this conversation into follow-up work"
                icon={<AddTaskOutlined fontSize="small" />}
                open={createPanelOpen}
                onToggle={() => setCreatePanelOpen((current) => !current)}
              >
                <div className="tzv2-cd-create-grid">
                  <button type="button" className="btn btn-secondary btn-block" onClick={createTaskFromConversation}>
                    <AddTaskOutlined fontSize="small" /> Create task
                  </button>
                  <button type="button" className="btn btn-secondary btn-block" disabled title="Not available in this build yet">
                    <EventOutlined fontSize="small" /> Create appointment
                  </button>
                  <button type="button" className="btn btn-secondary btn-block" disabled title="Not available in this build yet">
                    <BuildOutlined fontSize="small" /> Create repair ticket
                  </button>
                  <button type="button" className="btn btn-secondary btn-block" disabled title="Not available in this build yet">
                    <ReceiptLongOutlined fontSize="small" /> Create quote
                  </button>
                </div>
                <small className="tzv2-cd-muted">Appointment, repair ticket and quote creation from a chat aren&rsquo;t wired up yet — coming soon.</small>
              </AccordionCard>

              <AccordionCard
                title="Internal notes"
                subtitle="Visible only to employees"
                icon={<NoteAddOutlined fontSize="small" />}
                open={notesPanelOpen}
                onToggle={() => setNotesPanelOpen((current) => !current)}
              >
                {canManage || canReply ? (
                  <form className="tzv2-cd-note-form" onSubmit={handleNoteSubmit}>
                    {noteMentionQuery !== null ? (
                      <div className="tzv2-cd-mention-menu">
                        {employees
                          .filter((employee) => (employee.display_name || "").toLowerCase().includes(noteMentionQuery.toLowerCase()))
                          .slice(0, 6)
                          .map((employee) => (
                            <button type="button" key={employee.id} onClick={() => insertNoteMention(employee)}>
                              {employee.display_name}
                            </button>
                          ))}
                      </div>
                    ) : null}
                    <textarea
                      id="conversation-note-textarea-v2"
                      className="input"
                      value={noteDraft}
                      placeholder="Write an internal note... use @ to tag a colleague"
                      onChange={handleNoteDraftChange}
                    />
                    <button type="submit" className="btn btn-primary" disabled={!noteDraft.trim()}>Add note</button>
                  </form>
                ) : null}

                <div className="tzv2-cd-note-list">
                  {notes.length ? notes.map((note) => (
                    <article key={note.id}>
                      <p>{renderNoteText(note.note, employees)}</p>
                      <span>By {note.author_name || "Unknown user"}</span>
                      <time>{formatDateTime(note.created_at)}</time>
                    </article>
                  )) : (
                    <span className="tzv2-cd-muted">No notes yet.</span>
                  )}
                </div>
              </AccordionCard>

              <AccordionCard
                title="Export & share"
                subtitle="Administrative review"
                icon={<DownloadOutlined fontSize="small" />}
                open={exportPanelOpen}
                onToggle={() => setExportPanelOpen((current) => !current)}
              >
                <div className="tzv2-cd-export-form">
                  <div className="field">
                    <label>Select what to export</label>
                    <select className="input" value={exportScope} onChange={(event) => setExportScope(event.target.value)}>
                      <option value="chat">Chat only</option>
                      <option value="full">Full report</option>
                    </select>
                  </div>
                  <div className="field">
                    <label>File format</label>
                    <select className="input" value={exportFormat} onChange={(event) => setExportFormat(event.target.value)}>
                      <option value="json">JSON</option>
                      <option value="csv">CSV</option>
                      <option value="txt">Text</option>
                      <option value="pdf">PDF</option>
                    </select>
                  </div>
                  <button type="button" className="btn btn-primary" disabled={exporting} onClick={handleExport}>
                    {exporting ? "Preparing…" : "Download export"}
                  </button>
                  <small className="tzv2-cd-muted">Full report includes the chat, internal notes and conversation metadata.</small>
                  <div className="tzv2-cd-create-grid">
                    <button type="button" className="btn btn-secondary btn-block" disabled title="Not available in this build yet">
                      <ShareOutlined fontSize="small" /> Share link
                    </button>
                    <button type="button" className="btn btn-secondary btn-block" disabled title="Not available in this build yet">
                      <MailOutlineOutlined fontSize="small" /> Email
                    </button>
                  </div>
                </div>
              </AccordionCard>

              <AccordionCard
                title="Moderation"
                subtitle="Spam and blocking controls"
                icon={<ReportOutlined fontSize="small" />}
                open={moderationPanelOpen}
                onToggle={() => setModerationPanelOpen((current) => !current)}
              >
                <div className="tzv2-cd-create-grid">
                  <button type="button" className="btn btn-secondary btn-block" disabled title="Not available in this build yet">
                    <ReportOutlined fontSize="small" /> Mark as spam
                  </button>
                  <button type="button" className="btn btn-secondary btn-block" disabled title="Not available in this build yet">
                    <BlockOutlined fontSize="small" /> Block customer
                  </button>
                </div>
                <small className="tzv2-cd-muted">Spam and block controls aren&rsquo;t available in this build yet.</small>
              </AccordionCard>
            </div>
          </aside>
        ) : null}
      </div>
    </div>
  );
}
