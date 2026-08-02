
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
  FiberManualRecordOutlined,
  FormatQuoteOutlined,
  HistoryOutlined,
  InsertDriveFileOutlined,
  LaunchOutlined,
  NoteAddOutlined,
  PauseCircleOutlineOutlined,
  RefreshOutlined,
  SendOutlined,
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


function renderNoteText(text, employees) {
  const names = (employees || []).map((employee) => employee.full_name).filter(Boolean).sort((a, b) => b.length - a.length);
  if (names.length === 0) return text;
  const pattern = new RegExp(`(@(?:${names.map((name) => name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")}))`, "g");
  return text.split(pattern).map((part, index) => (
    pattern.test(part) ? <strong key={index} className="conversation-note-mention">{part}</strong> : <span key={index}>{part}</span>
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
  const mediaUrl = metadata?.media_url;
  const mediaType = metadata?.media_type;
  if (!mediaUrl) return null;

  if (mediaType === "image") {
    return (
      <a href={mediaUrl} target="_blank" rel="noopener noreferrer">
        <img src={mediaUrl} alt="" style={{ maxWidth: "100%", maxHeight: 260, borderRadius: 8, display: "block", marginBottom: 6 }} />
      </a>
    );
  }

  if (mediaType === "video") {
    return (
      <video controls src={mediaUrl} style={{ maxWidth: "100%", maxHeight: 260, borderRadius: 8, display: "block", marginBottom: 6 }} />
    );
  }

  if (mediaType === "audio") {
    return (
      <audio controls src={mediaUrl} style={{ display: "block", marginBottom: 6, maxWidth: "100%" }} />
    );
  }

  return (
    <a
      href={mediaUrl}
      target="_blank"
      rel="noopener noreferrer"
      style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 10px", background: "rgba(0,0,0,0.04)", borderRadius: 8, marginBottom: 6, textDecoration: "none", color: "inherit" }}
    >
      <AttachFileOutlined fontSize="small" />
      <span style={{ fontSize: 13, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {metadata?.media_filename || "Download file"}
      </span>
    </a>
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
  const [detailsOpen, setDetailsOpen] = useState(true);
  const [controlPanelOpen, setControlPanelOpen] = useState(true);
  const [notesPanelOpen, setNotesPanelOpen] = useState(false);
  const [events, setEvents] = useState([]);
  const [timelinePanelOpen, setTimelinePanelOpen] = useState(false);
  const [savedRepliesOpen, setSavedRepliesOpen] = useState(false);
  const [toolsMenuOpen, setToolsMenuOpen] = useState(false);
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
        setRefreshing(false);
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
  const isHumanQueue = !aiIsHandling && assignedUserId == null;
  const isAssignedToOther = assignedUserId != null && !isAssignedToMe;
  // Server permissions are authoritative.  Never infer ownership controls
  // from locally cached assignment data because another employee may have
  // acquired the lock in a different browser.
  const canReply = Boolean(permissions?.can_reply);
  const canManage = Boolean(permissions?.can_manage);
  const canMarkRead = Boolean(permissions?.can_mark_read);
  const canTakeOver = Boolean(permissions?.can_take_over) && (!isAssignedToOther || currentUserIsAdmin);
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
    const textarea = document.getElementById("conversation-note-textarea");
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

    setToolsMenuOpen(false);
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
    setToolsMenuOpen(false);
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
                message?.delivery_status ||
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

                    <MessageMedia metadata={message?.metadata} />

                    {resolveMessageText(message) || !message?.metadata?.media_url ? (
                      <p>{resolveMessageText(message) || "[Unsupported message]"}</p>
                    ) : null}

                    <div className="message-footer">
                      <time>{formatDateTime(item.createdAt)}</time>
                      {direction === "out" ? (
                        <span
                          className={`delivery-status delivery-status-${deliveryStatus}`}
                          title={
                            deliveryStatus === "read" ? "Read"
                              : deliveryStatus === "delivered" ? "Delivered"
                                : deliveryStatus === "failed" ? "Failed to send"
                                  : "Sent"
                          }
                          style={{
                            marginLeft: 4,
                            fontSize: 13,
                            color: deliveryStatus === "read" ? "#4F63F0" : "#9296AC",
                          }}
                        >
                          {deliveryStatus === "failed" ? "!" : deliveryStatus === "sent" ? "✓" : "✓✓"}
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

          <div style={{ position: "relative", flex: "0 0 auto" }}>
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

          {mediaError ? (
            <div className="conversation-action-error" style={{ marginBottom: 6 }}>{mediaError}</div>
          ) : null}

          {recording ? (
            <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 14px", background: "#fff", border: "1px solid #e5e7eb", borderRadius: 10, marginBottom: 6 }}>
              <FiberManualRecordOutlined style={{ color: "#e53935", fontSize: 16 }} />
              <span style={{ fontSize: 13, fontWeight: 600 }}>Recording... {formatTimer(recordingSeconds)}</span>
              <div style={{ flex: 1 }} />
              <AppButton type="button" variant="secondary" size="small" onClick={cancelVoiceRecording}>
                Cancel
              </AppButton>
              <AppButton type="button" variant="primary" size="small" icon={<StopCircleOutlined fontSize="small" />} onClick={stopVoiceRecording}>
                Stop
              </AppButton>
            </div>
          ) : pendingMedia ? (
            <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 14px", background: "#fff", border: "1px solid #e5e7eb", borderRadius: 10, marginBottom: 6 }}>
              {pendingMedia.mediaType === "image" ? (
                <img src={pendingMedia.url} alt="" style={{ width: 32, height: 32, objectFit: "cover", borderRadius: 6 }} />
              ) : pendingMedia.mediaType === "audio" ? (
                <MicNoneOutlined fontSize="small" />
              ) : pendingMedia.mediaType === "video" ? (
                <ImageOutlined fontSize="small" />
              ) : (
                <InsertDriveFileOutlined fontSize="small" />
              )}
              <span style={{ fontSize: 13, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {pendingMedia.filename || humanize(pendingMedia.mediaType)}
              </span>
              <div style={{ flex: 1 }} />
              <button type="button" className="inbox-icon-button" title="Remove" onClick={removePendingMedia}>
                <CloseOutlined fontSize="small" />
              </button>
            </div>
          ) : mediaUploading ? (
            <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 14px", background: "#fff", border: "1px solid #e5e7eb", borderRadius: 10, marginBottom: 6 }}>
              <span style={{ fontSize: 13, color: "#6b7280" }}>Uploading...</span>
            </div>
          ) : null}

          <form className="conversation-composer conversation-composer-approved" onSubmit={handleSend}>
            <div className="conversation-composer-row conversation-composer-single-row">
              <textarea
                ref={composerRef}
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
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    handleSend(event);
                  }
                }}
              />

              <button
                className="composer-tool-button"
                type="button"
                title="More options"
                disabled={recording}
                onClick={() => setToolsMenuOpen((current) => !current)}
              >
                +
              </button>

              <button
                type="submit"
                className="composer-send-circle"
                aria-label="Send message"
                disabled={sending || recording || mediaUploading || (!pendingMedia && !draft.trim()) || !canReply}
              >
                <SendOutlined />
              </button>
            </div>
          </form>

          {toolsMenuOpen ? (
            <div style={{ position: "absolute", bottom: "calc(100% + 8px)", right: 16, width: 210, background: "#fff", boxShadow: "0 6px 20px rgba(0,0,0,0.18)", borderRadius: 10, zIndex: 30, overflow: "hidden", border: "1px solid #e5e7eb" }}>
              <button
                type="button"
                title="Send a document, spreadsheet, or archive"
                style={{ display: "flex", alignItems: "center", gap: 10, padding: "11px 14px", width: "100%", fontSize: 14, background: "none", border: "none", cursor: "pointer", textAlign: "left" }}
                onClick={pickAttachment}
              >
                <AttachFileOutlined fontSize="small" /> Attachment
              </button>
              <button
                type="button"
                title="Send an image"
                style={{ display: "flex", alignItems: "center", gap: 10, padding: "11px 14px", width: "100%", fontSize: 14, background: "none", border: "none", borderTop: "1px solid #f1f3f5", cursor: "pointer", textAlign: "left" }}
                onClick={pickImage}
              >
                <ImageOutlined fontSize="small" /> Image
              </button>
              <button
                type="button"
                title="Record and send a voice note"
                style={{ display: "flex", alignItems: "center", gap: 10, padding: "11px 14px", width: "100%", fontSize: 14, background: "none", border: "none", borderTop: "1px solid #f1f3f5", cursor: "pointer", textAlign: "left" }}
                onClick={startVoiceRecording}
              >
                <MicNoneOutlined fontSize="small" /> Voice note
              </button>
              <button
                type="button"
                style={{ display: "flex", alignItems: "center", gap: 10, padding: "11px 14px", width: "100%", fontSize: 14, background: "none", border: "none", borderTop: "1px solid #f1f3f5", cursor: "pointer", textAlign: "left" }}
                onClick={() => { setToolsMenuOpen(false); setSavedRepliesOpen((current) => !current); }}
              >
                <FormatQuoteOutlined fontSize="small" /> Saved replies
              </button>
            </div>
          ) : null}

          {savedRepliesOpen ? (
            <div style={{ position: "absolute", bottom: "calc(100% + 8px)", right: 16, left: 16, maxHeight: 260, overflowY: "auto", background: "#fff", boxShadow: "0 6px 20px rgba(0,0,0,0.18)", borderRadius: 10, zIndex: 30, border: "1px solid #e5e7eb" }}>
              {visibleSavedReplies.length ? visibleSavedReplies.map((reply) => (
                <article
                  key={reply.id}
                  style={{ padding: "10px 14px", borderBottom: "1px solid #f1f3f5", cursor: canReply || !isAssignedToOther ? "pointer" : "not-allowed" }}
                  onClick={async () => {
                    const acquired = await ensureTakeoverForReply();
                    if (!acquired) return;
                    setDraft((current) => (current ? `${current} ${reply.body}` : reply.body));
                    setSavedRepliesOpen(false);
                    composerRef.current?.focus();
                  }}
                >
                  <p style={{ margin: "0 0 4px", fontWeight: 600, fontSize: 13 }}>{reply.title}</p>
                  <span style={{ fontSize: 12, color: "#6b7280" }}>{reply.body}</span>
                </article>
              )) : (
                <span style={{ display: "block", padding: "14px", fontSize: 13, color: "#6b7280" }}>No saved replies for this department yet — admins can add some from the Saved Replies page.</span>
              )}
            </div>
          ) : null}
          </div>
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
                      )}
                    >
                      Save
                    </AppButton>
                  </div>
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

                <label>
                  <span>Follow-up reminder</span>
                  <div className="inline-field-action">
                    <input
                      type="datetime-local"
                      value={reminderDraft}
                      disabled={saving}
                      onChange={(event) => setReminderDraft(event.target.value)}
                    />
                    <AppButton
                      variant="secondary"
                      size="small"
                      disabled={
                        saving ||
                        !reminderDraft ||
                        (reminderAutoSendDraft && !reminderMessageTextDraft.trim())
                      }
                      onClick={saveReminder}
                    >
                      {control?.reminder_at ? "Update" : "Set"}
                    </AppButton>
                  </div>
                  {control?.reminder_at ? (
                    <small className="conversation-muted">
                      Reminder set for {new Date(control.reminder_at).toLocaleString()}
                      {" — "}
                      <button type="button" className="tag-delete-button" disabled={saving} onClick={clearReminder}>
                        Clear
                      </button>
                    </small>
                  ) : null}
                  {control?.reminder_auto_send ? (
                    <div className="reminder-auto-send-armed">
                      <span className="reminder-auto-send-badge">Auto follow-up armed</span>
                      {control?.reminder_message_text ? (
                        <small className="conversation-muted reminder-auto-send-preview">
                          &ldquo;{control.reminder_message_text}&rdquo;
                        </small>
                      ) : null}
                    </div>
                  ) : null}
                  <input
                    type="text"
                    placeholder="What to follow up about (optional)"
                    value={reminderNoteDraft}
                    disabled={saving}
                    onChange={(event) => setReminderNoteDraft(event.target.value)}
                    style={{ marginTop: 6 }}
                  />
                  <div className="reminder-auto-send-toggle">
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
                  </div>
                  {reminderAutoSendDraft ? (
                    <textarea
                      className="reminder-auto-send-textarea"
                      placeholder="Exact message to send automatically when the reminder fires..."
                      value={reminderMessageTextDraft}
                      disabled={saving}
                      onChange={(event) => setReminderMessageTextDraft(event.target.value)}
                    />
                  ) : null}
                </label>
              </div>
              </CollapsiblePanel>
            ) : null}

            <CollapsiblePanel
              title="Timeline"
              subtitle="Full history of this conversation"
              icon={<HistoryOutlined />}
              open={timelinePanelOpen}
              onToggle={() => setTimelinePanelOpen((current) => !current)}
            >
              <div className="conversation-note-list">
                {events.length ? events.map((event) => (
                  <article key={event.id}>
                    <p>{humanize(event.event_type)}</p>
                    <span>By {event.actor_name || "System"}</span>
                    <time>{formatDateTime(event.created_at)}</time>
                  </article>
                )) : (
                  <span className="conversation-muted">No timeline events yet.</span>
                )}
              </div>
            </CollapsiblePanel>

            <CollapsiblePanel
              title="Internal notes"
              subtitle="Visible only to employees"
              icon={<NoteAddOutlined />}
              open={notesPanelOpen}
              onToggle={() => setNotesPanelOpen((current) => !current)}
            >
              <AppButton type="button" variant="secondary" size="small" onClick={createTaskFromConversation} style={{ marginBottom: 10 }}>
                + Create task from this conversation
              </AppButton>

              {canManage || canReply ? (
                <form className="conversation-note-form" onSubmit={handleNoteSubmit} style={{ position: "relative" }}>
                  {noteMentionQuery !== null ? (
                    <div className="conversation-note-mention-menu">
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
                    id="conversation-note-textarea"
                    value={noteDraft}
                    placeholder="Write an internal note... use @ to tag a colleague"
                    onChange={handleNoteDraftChange}
                  />
                  <AppButton type="submit" variant="primary" size="small" disabled={!noteDraft.trim()}>
                    Add note
                  </AppButton>
                </form>
              ) : null}

              <div className="conversation-note-list">
                {notes.length ? notes.map((note) => (
                  <article key={note.id}>
                    <p>{renderNoteText(note.note, employees)}</p>
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
                    <option value="txt">Text</option>
                    <option value="pdf">PDF</option>
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