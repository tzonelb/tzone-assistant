import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AddOutlined,
  AddTaskOutlined,
  AttachFileOutlined,
  CloseOutlined,
  DeleteOutlineOutlined,
  GroupOutlined,
  ImageOutlined,
  MicNoneOutlined,
  SendOutlined,
} from "@mui/icons-material";
import {
  createTaskRequest,
  createTeamDmRequest,
  createTeamGroupRequest,
  deleteTeamMessageRequest,
  deleteTeamRoomMessageRequest,
  getCurrentUserRequest,
  listDepartmentsRequest,
  listTeamMessagesRequest,
  listTeamRoomMessagesRequest,
  listTeamRoomsRequest,
  sendTeamMessageRequest,
  sendTeamRoomMessageRequest,
  taskOptionsRequest,
  teamChatOptionsRequest,
  uploadMediaRequest,
  uploadVoiceNoteRequest,
} from "../../api/client";
import { EmptyState, ErrorState, LoadingState } from "../../components/common";
import "./TeamChatPageV2.css";

// Real data + APIs throughout — no fabricated presence/rooms. The single
// flat, company-wide stream (team_messages, unchanged) is joined here by a
// genuinely additive rooms feature (team_chat_rooms/_room_members/
// team_room_messages — see backend/services/team_chat_rooms_service.py):
// 1:1 DMs and named groups (explicit member picks or a department
// snapshot). Attachments/voice notes reuse the exact same /api/media
// upload endpoints Conversations' composer uses.
const POLL_INTERVAL_MS = 5000;

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const today = new Date();
  const isToday = date.toDateString() === today.toDateString();
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return isToday ? time : `${date.toLocaleDateString()} ${time}`;
}

function renderMessageText(text, employees) {
  const names = employees.map((employee) => employee.full_name || employee.display_name).filter(Boolean).sort((a, b) => b.length - a.length);
  if (!text || names.length === 0) return text;
  const pattern = new RegExp(`(@(?:${names.map((name) => name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")}))`, "g");
  return text.split(pattern).map((part, index) => (
    pattern.test(part) ? <strong key={index} className="tzv2-chat-mention">{part}</strong> : <span key={index}>{part}</span>
  ));
}

export default function TeamChatPageV2() {
  const [currentUserId, setCurrentUserId] = useState(null);
  const [employees, setEmployees] = useState([]);
  const [departments, setDepartments] = useState([]);
  const [rooms, setRooms] = useState([]);
  const [activeRoomId, setActiveRoomId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState("");
  const [mentionedUserIds, setMentionedUserIds] = useState([]);
  const [sending, setSending] = useState(false);
  const [mentionQuery, setMentionQuery] = useState(null);
  const [showMembers, setShowMembers] = useState(true);
  const listRef = useRef(null);
  const textareaRef = useRef(null);
  const loadRequestIdRef = useRef(0);

  const [pendingMedia, setPendingMedia] = useState(null);
  const [mediaUploading, setMediaUploading] = useState(false);
  const [mediaError, setMediaError] = useState("");
  const [recording, setRecording] = useState(false);
  const attachmentInputRef = useRef(null);
  const imageInputRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const recordingStreamRef = useRef(null);
  const recordedChunksRef = useRef([]);

  const [newChatOpen, setNewChatOpen] = useState(false);
  const [newChatMode, setNewChatMode] = useState("dm");
  const [contactQuery, setContactQuery] = useState("");
  const [groupName, setGroupName] = useState("");
  const [groupBy, setGroupBy] = useState("manual");
  const [groupDepartment, setGroupDepartment] = useState("");
  const [selectedMemberIds, setSelectedMemberIds] = useState([]);
  const [newChatError, setNewChatError] = useState("");
  const [creatingChat, setCreatingChat] = useState(false);

  const [taskDialogOpen, setTaskDialogOpen] = useState(false);
  const [taskOptions, setTaskOptions] = useState({ priorities: [], task_types: [] });
  const [taskTitle, setTaskTitle] = useState("");
  const [taskType, setTaskType] = useState("follow_up");
  const [taskPriority, setTaskPriority] = useState("normal");
  const [taskDueAt, setTaskDueAt] = useState("");
  const [taskTargetIds, setTaskTargetIds] = useState([]);
  const [taskError, setTaskError] = useState("");
  const [creatingTask, setCreatingTask] = useState(false);
  const [taskCreated, setTaskCreated] = useState(false);

  const activeRoom = useMemo(() => rooms.find((room) => room.id === activeRoomId) || null, [rooms, activeRoomId]);
  const panelMembers = activeRoom ? activeRoom.members : employees.map((employee) => ({ id: employee.id, display_name: employee.display_name, role_name: employee.role_name }));

  const load = useCallback(async () => {
    // A room switch can fire a new request while an older one for the
    // previous room is still in flight — without this guard, whichever
    // response lands LAST wins the setMessages call, which can attach an
    // old room's messages to the newly-selected room if the old request
    // happens to resolve after the new one.
    const requestId = ++loadRequestIdRef.current;
    try {
      const result = activeRoomId
        ? await listTeamRoomMessagesRequest(activeRoomId, { limit: 100 })
        : await listTeamMessagesRequest({ limit: 100 });
      if (requestId !== loadRequestIdRef.current) return;
      setMessages(Array.isArray(result?.items) ? result.items : []);
      setError("");
    } catch (requestError) {
      if (requestId !== loadRequestIdRef.current) return;
      setError(requestError.message || "Team chat could not be loaded.");
    } finally {
      if (requestId === loadRequestIdRef.current) setLoading(false);
    }
  }, [activeRoomId]);

  const loadRooms = useCallback(async () => {
    try {
      const result = await listTeamRoomsRequest();
      setRooms(Array.isArray(result?.rooms) ? result.rooms : []);
    } catch {
      // Rooms are additive — a failed fetch still leaves the real flat stream usable.
    }
  }, []);

  useEffect(() => {
    getCurrentUserRequest()
      .then((result) => setCurrentUserId(result?.user?.id ?? null))
      .catch(() => setError("Could not confirm who you are — try reloading the page."));
    teamChatOptionsRequest()
      .then((result) => setEmployees(Array.isArray(result?.employees) ? result.employees : []))
      .catch(() => setError("Could not load your company's employee list."));
    listDepartmentsRequest()
      .then((result) => setDepartments((result?.departments || []).filter((name) => name !== "Unassigned")))
      .catch(() => {
        // Non-critical — only affects the "by department" group option, not falsely shown as empty (no departments render).
      });
    loadRooms();
  }, [loadRooms]);

  useEffect(() => {
    setLoading(true);
    load();
    const interval = window.setInterval(load, POLL_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [load]);

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [messages]);

  useEffect(() => {
    // Navigating away mid-recording (an SPA route change unmounts this
    // page) must not leave the microphone live — the browser's mic
    // indicator would stay lit until the tab itself closes otherwise.
    return () => {
      recordingStreamRef.current?.getTracks().forEach((track) => track.stop());
      recordingStreamRef.current = null;
    };
  }, []);

  const mentionSuggestions = useMemo(() => {
    if (mentionQuery === null) return [];
    const query = mentionQuery.toLowerCase();
    const pool = activeRoom ? activeRoom.members : employees;
    return pool.filter((person) => (person.display_name || "").toLowerCase().includes(query)).slice(0, 6);
  }, [mentionQuery, employees, activeRoom]);

  const memberFirstNames = useMemo(
    () => employees.map((employee) => (employee.display_name || "").split(" ")[0]).filter(Boolean),
    [employees]
  );

  function switchRoom(roomId) {
    setActiveRoomId(roomId);
    setDraft("");
    setMentionedUserIds([]);
    setMentionQuery(null);
    removePendingMedia();
  }

  function handleDraftChange(event) {
    const value = event.target.value;
    setDraft(value);
    const cursor = event.target.selectionStart;
    const upToCursor = value.slice(0, cursor);
    const match = upToCursor.match(/@([^\s@]*)$/);
    setMentionQuery(match ? match[1] : null);
  }

  function insertMention(person) {
    const cursor = textareaRef.current ? textareaRef.current.selectionStart : draft.length;
    const upToCursor = draft.slice(0, cursor);
    const replaced = upToCursor.replace(/@([^\s@]*)$/, `@${person.display_name} `);
    const nextDraft = `${replaced}${draft.slice(cursor)}`;
    setDraft(nextDraft);
    setMentionQuery(null);
    setMentionedUserIds((current) => (current.includes(person.id) ? current : [...current, person.id]));
    window.requestAnimationFrame(() => textareaRef.current?.focus());
  }

  async function handleSend(event) {
    event.preventDefault();
    const text = draft.trim();
    if ((!text && !pendingMedia) || sending || recording || mediaUploading) return;
    setSending(true);
    try {
      const payload = {
        text,
        mentioned_user_ids: mentionedUserIds,
        attachment_url: pendingMedia?.url || null,
        attachment_type: pendingMedia?.mediaType || null,
        attachment_filename: pendingMedia?.filename || null,
      };
      if (activeRoomId) await sendTeamRoomMessageRequest(activeRoomId, payload);
      else await sendTeamMessageRequest(payload);
      setDraft("");
      setMentionedUserIds([]);
      setMentionQuery(null);
      removePendingMedia();
      await load();
    } catch (requestError) {
      setError(requestError.message || "Message could not be sent.");
    } finally {
      setSending(false);
    }
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey && mentionQuery === null) {
      event.preventDefault();
      handleSend(event);
    }
  }

  async function handleDelete(messageId) {
    try {
      if (activeRoomId) await deleteTeamRoomMessageRequest(activeRoomId, messageId);
      else await deleteTeamMessageRequest(messageId);
      setMessages((current) => current.filter((message) => message.id !== messageId));
    } catch (requestError) {
      setError(requestError.message || "Message could not be deleted.");
    }
  }

  async function uploadPendingFile(file) {
    setMediaError("");
    setMediaUploading(true);
    try {
      const result = await uploadMediaRequest(file);
      setPendingMedia({ url: result.url, mediaType: result.media_type, filename: result.filename || file.name });
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
    await uploadPendingFile(file);
  }

  function pickAttachment() { attachmentInputRef.current?.click(); }
  function pickImage() { imageInputRef.current?.click(); }
  function removePendingMedia() { setPendingMedia(null); setMediaError(""); }

  function stopRecordingStream() {
    recordingStreamRef.current?.getTracks().forEach((track) => track.stop());
    recordingStreamRef.current = null;
  }

  async function startVoiceRecording() {
    setMediaError("");
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
    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) recordedChunksRef.current.push(event.data);
    };
    recorder.onstop = async () => {
      stopRecordingStream();
      setRecording(false);
      if (recordedChunksRef.current.length === 0) return;
      const blob = new Blob(recordedChunksRef.current, { type: recorder.mimeType || "audio/webm" });
      recordedChunksRef.current = [];
      const extension = recorder.mimeType?.includes("ogg") ? "ogg" : "webm";
      const file = new File([blob], `voice-note.${extension}`, { type: blob.type });
      setMediaUploading(true);
      setMediaError("");
      try {
        const result = await uploadVoiceNoteRequest(file);
        setPendingMedia({ url: result.url, mediaType: result.media_type, filename: "Voice note" });
      } catch (requestError) {
        setMediaError(requestError.message || "Could not process the voice note.");
      } finally {
        setMediaUploading(false);
      }
    };
    mediaRecorderRef.current = recorder;
    recorder.start();
    setRecording(true);
  }

  function stopVoiceRecording() {
    mediaRecorderRef.current?.stop();
  }

  const filteredContacts = useMemo(() => {
    const query = contactQuery.trim().toLowerCase();
    return employees.filter((employee) => employee.id !== currentUserId && (!query || (employee.display_name || "").toLowerCase().includes(query)));
  }, [employees, contactQuery, currentUserId]);

  function openNewChat() {
    setNewChatOpen(true);
    setNewChatMode("dm");
    setContactQuery("");
    setNewChatError("");
    setGroupName("");
    setGroupBy("manual");
    setGroupDepartment("");
    setSelectedMemberIds([]);
  }

  async function startDm(employee) {
    setCreatingChat(true);
    setNewChatError("");
    try {
      const room = await createTeamDmRequest(employee.id);
      await loadRooms();
      switchRoom(room.id);
      setNewChatOpen(false);
    } catch (requestError) {
      setNewChatError(requestError.message || "Could not start this conversation.");
    } finally {
      setCreatingChat(false);
    }
  }

  function toggleMember(id) {
    setSelectedMemberIds((current) => (current.includes(id) ? current.filter((existing) => existing !== id) : [...current, id]));
  }

  async function submitGroup(event) {
    event.preventDefault();
    if (!groupName.trim()) return;
    setCreatingChat(true);
    setNewChatError("");
    try {
      const room = await createTeamGroupRequest({
        name: groupName,
        memberUserIds: groupBy === "manual" ? selectedMemberIds : [],
        department: groupBy === "department" ? groupDepartment : null,
      });
      await loadRooms();
      switchRoom(room.id);
      setNewChatOpen(false);
    } catch (requestError) {
      setNewChatError(requestError.message || "Could not create this group.");
    } finally {
      setCreatingChat(false);
    }
  }

  function openTaskDialog() {
    setTaskDialogOpen(true);
    setTaskError("");
    setTaskCreated(false);
    setTaskTitle("");
    setTaskType("follow_up");
    setTaskPriority("normal");
    setTaskDueAt("");
    setTaskTargetIds(panelMembers.filter((person) => person.id !== currentUserId).map((person) => person.id));
    if (!taskOptions.task_types.length) {
      taskOptionsRequest()
        .then((result) => setTaskOptions({ priorities: result?.priorities || [], task_types: result?.task_types || [] }))
        .catch(() => {});
    }
  }

  function toggleTaskTarget(id) {
    setTaskTargetIds((current) => (current.includes(id) ? current.filter((existing) => existing !== id) : [...current, id]));
  }

  async function submitTask(event) {
    event.preventDefault();
    if (!taskTitle.trim() || taskTargetIds.length === 0) return;
    setCreatingTask(true);
    setTaskError("");
    // allSettled, not all — a Promise.all rejection would still leave every
    // already-succeeded task created server-side with no way to tell which
    // ones. Settle everything, then only leave the genuinely-failed targets
    // selected, so retrying never re-creates a duplicate for someone who
    // already got their task.
    const results = await Promise.allSettled(taskTargetIds.map((userId) => createTaskRequest({
      title: taskTitle.trim(),
      task_type: taskType,
      priority: taskPriority,
      due_at: taskDueAt || null,
      assigned_user_id: userId,
    }).then(() => userId)));

    const failedIds = results
      .map((result, index) => (result.status === "rejected" ? taskTargetIds[index] : null))
      .filter((id) => id !== null);

    if (failedIds.length === 0) {
      setTaskCreated(true);
    } else if (failedIds.length === taskTargetIds.length) {
      setTaskError(results[0].reason?.message || "Could not create the task.");
    } else {
      setTaskTargetIds(failedIds);
      setTaskError(`Created for ${taskTargetIds.length - failedIds.length} of ${taskTargetIds.length} — retry to create the rest.`);
    }
    setCreatingTask(false);
  }

  if (loading) {
    return (
      <div className="tzv2-chat-page tz-screen">
        <LoadingState title="Loading team chat…" description="Retrieving your company's internal message stream." />
      </div>
    );
  }

  const memberCountLabel = `${employees.length} member${employees.length === 1 ? "" : "s"}`;
  const memberPreview = memberFirstNames.length
    ? ` · ${memberFirstNames.slice(0, 5).join(", ")}${memberFirstNames.length > 5 ? "…" : ""}`
    : "";

  const headerTitle = activeRoom ? activeRoom.display_name : "Team Chat";
  const headerKick = activeRoom
    ? `${activeRoom.kind === "dm" ? "Direct message" : `${activeRoom.members.length} members`}`
    : `${memberCountLabel}${memberPreview}`;

  return (
    <div className="tzv2-chat-page tz-screen">
      <div className="tzv2-chat-sidebar">
        <div className="tzv2-chat-sidebar-head tzv2-chat-sidebar-head-row">
          <div>
            <span className="tz-kick">Internal only</span>
            <h4>Team Chat</h4>
          </div>
          <button type="button" className="btn btn-secondary btn-icon" title="New chat" onClick={openNewChat}>
            <AddOutlined fontSize="small" />
          </button>
        </div>
        <div className="tzv2-chat-rooms">
          <button type="button" className={`tzv2-chat-room ${!activeRoomId ? "is-active" : ""}`} onClick={() => switchRoom(null)}>
            <div className="tzv2-chat-room-main">
              <strong>Team Chat</strong>
              <span>{memberCountLabel}</span>
            </div>
          </button>
          {rooms.map((room) => (
            <button type="button" key={room.id} className={`tzv2-chat-room ${activeRoomId === room.id ? "is-active" : ""}`} onClick={() => switchRoom(room.id)}>
              <div className="tzv2-chat-room-main">
                <strong>{room.display_name}</strong>
                <span>{room.kind === "dm" ? "Direct message" : `${room.members.length} members`}</span>
              </div>
            </button>
          ))}
        </div>
      </div>

      <div className="tzv2-chat-main">
        <div className="tzv2-chat-header">
          <div className="tzv2-chat-header-main">
            <h4>{headerTitle}</h4>
            <span className="tz-kick">{headerKick}</span>
          </div>
          <button type="button" className="btn btn-secondary" onClick={openTaskDialog}>
            <AddTaskOutlined fontSize="small" /> Create task
          </button>
          <button type="button" className="btn btn-secondary" onClick={() => setShowMembers((current) => !current)}>
            <GroupOutlined fontSize="small" /> {showMembers ? "Hide members" : "Members"}
          </button>
        </div>

        {error ? <ErrorState title="Team chat error" description={error} /> : null}

        <div className="tzv2-chat-messages" ref={listRef}>
          {messages.length === 0 ? (
            <EmptyState title="No messages yet" description="Say hello — this stream is internal and separate from customer conversations." />
          ) : (
            messages.map((message) => {
              const isOwn = message.sender_user_id === currentUserId;
              return (
                <div key={message.id} className={`tzv2-chat-msg ${isOwn ? "is-own" : ""}`}>
                  <div className="tzv2-chat-msg-head">
                    <span className="tz-kick">{message.sender_name}</span>
                    <span className="tz-num">{formatTime(message.created_at)}</span>
                  </div>
                  <div className="tzv2-chat-bubble">
                    {message.attachment_url ? (
                      message.attachment_type === "image" ? (
                        <img src={message.attachment_url} alt="" className="tzv2-chat-attachment-img" />
                      ) : message.attachment_type === "audio" ? (
                        <audio controls src={message.attachment_url} className="tzv2-chat-attachment-audio" />
                      ) : (
                        <a href={message.attachment_url} target="_blank" rel="noreferrer" className="tzv2-chat-attachment-file">
                          {message.attachment_filename || "Attachment"}
                        </a>
                      )
                    ) : null}
                    {message.text ? <p>{renderMessageText(message.text, employees)}</p> : null}
                    {isOwn ? (
                      <button type="button" className="tzv2-chat-delete" title="Delete message" onClick={() => handleDelete(message.id)}>
                        <DeleteOutlineOutlined fontSize="inherit" />
                      </button>
                    ) : null}
                  </div>
                </div>
              );
            })
          )}
        </div>

        <form className="tzv2-chat-composer" onSubmit={handleSend}>
          {mentionSuggestions.length > 0 ? (
            <div className="tzv2-chat-mention-menu">
              {mentionSuggestions.map((person) => (
                <button type="button" key={person.id} onClick={() => insertMention(person)}>
                  {person.display_name}
                </button>
              ))}
            </div>
          ) : null}

          <input ref={attachmentInputRef} type="file" hidden onChange={handlePickerChange} />
          <input ref={imageInputRef} type="file" accept="image/*" hidden onChange={handlePickerChange} />

          <button type="button" className="btn btn-ghost btn-icon" title="Attachment" disabled={recording} onClick={pickAttachment}>
            <AttachFileOutlined fontSize="small" />
          </button>
          <button type="button" className="btn btn-ghost btn-icon" title="Image" disabled={recording} onClick={pickImage}>
            <ImageOutlined fontSize="small" />
          </button>

          <div className="tzv2-chat-composer-input-wrap">
            {mediaError ? <div className="tzv2-chat-media-error">{mediaError}</div> : null}
            {pendingMedia ? (
              <div className="tzv2-chat-pending-media">
                {pendingMedia.mediaType === "image" ? (
                  <img src={pendingMedia.url} alt="" className="tzv2-chat-pending-thumb" />
                ) : (
                  <span>{pendingMedia.filename || "Attachment"}</span>
                )}
                <button type="button" className="btn btn-ghost btn-icon" title="Remove" onClick={removePendingMedia}>
                  <CloseOutlined fontSize="small" />
                </button>
              </div>
            ) : null}
            <textarea
              ref={textareaRef}
              className="input"
              value={draft}
              placeholder={pendingMedia ? "Add a caption (optional)..." : "Message — @mention a colleague"}
              onChange={handleDraftChange}
              onKeyDown={handleKeyDown}
              rows={2}
            />
          </div>

          <button
            type="button"
            className={`btn btn-ghost btn-icon ${recording ? "is-recording" : ""}`}
            title={recording ? "Stop recording" : "Record a voice note"}
            disabled={mediaUploading}
            onClick={recording ? stopVoiceRecording : startVoiceRecording}
          >
            <MicNoneOutlined fontSize="small" />
          </button>

          <button type="submit" className="btn btn-primary" disabled={sending || recording || mediaUploading || (!pendingMedia && !draft.trim())}>
            <SendOutlined fontSize="small" /> Send
          </button>
        </form>
      </div>

      {showMembers ? (
        <div className="tz-pane-aux tzv2-chat-aux">
          <span className="tz-kick">{activeRoom ? "Members" : "Team"}</span>
          {panelMembers.length ? (
            <div className="tzv2-chat-aux-list">
              {panelMembers.map((person) => (
                <div className="tzv2-chat-aux-row" key={person.id}>
                  <strong>{person.display_name}</strong>
                  {person.role_name ? <span>{person.role_name}</span> : null}
                </div>
              ))}
            </div>
          ) : (
            <p className="tzv2-chat-aux-empty">No other team members yet.</p>
          )}
          <div className="hr" />
          <p className="tzv2-chat-aux-note">
            Team chat is separate from customer conversations — nothing written here reaches a customer.
          </p>
        </div>
      ) : null}

      {newChatOpen ? (
        <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setNewChatOpen(false); }}>
          <div className="dialog tzv2-chat-new-dialog" role="dialog" aria-modal="true">
            <div className="tzv2-chat-dialog-head">
              <span className="dialog-title">New chat</span>
              <button type="button" className="btn btn-ghost btn-icon" aria-label="Close dialog" onClick={() => setNewChatOpen(false)}>
                <CloseOutlined fontSize="small" />
              </button>
            </div>

            <div className="seg" role="radiogroup" aria-label="Chat type">
              <label className="seg-opt">
                <input type="radio" name="tzv2-new-chat-mode" checked={newChatMode === "dm"} onChange={() => setNewChatMode("dm")} />
                Direct message
              </label>
              <label className="seg-opt">
                <input type="radio" name="tzv2-new-chat-mode" checked={newChatMode === "group"} onChange={() => setNewChatMode("group")} />
                Group
              </label>
            </div>

            {newChatError ? <p className="customer-segment-error">{newChatError}</p> : null}

            {newChatMode === "dm" ? (
              <div className="dialog-body">
                <input className="input" placeholder="Search employees..." value={contactQuery} onChange={(event) => setContactQuery(event.target.value)} />
                <div className="tzv2-chat-contact-list">
                  {filteredContacts.map((employee) => (
                    <button type="button" key={employee.id} className="tzv2-chat-contact-row" disabled={creatingChat} onClick={() => startDm(employee)}>
                      <strong>{employee.display_name}</strong>
                      {employee.role_name ? <span>{employee.role_name}</span> : null}
                    </button>
                  ))}
                  {filteredContacts.length === 0 ? <p className="tzv2-chat-aux-empty">No employees match.</p> : null}
                </div>
              </div>
            ) : (
              <form className="dialog-body" onSubmit={submitGroup}>
                <div className="field">
                  <label>Group name</label>
                  <input className="input" value={groupName} onChange={(event) => setGroupName(event.target.value)} placeholder="e.g. Sales Team" required />
                </div>

                <div className="seg" role="radiogroup" aria-label="Choose members by">
                  <label className="seg-opt">
                    <input type="radio" name="tzv2-group-by" checked={groupBy === "manual"} onChange={() => setGroupBy("manual")} />
                    Pick employees
                  </label>
                  <label className="seg-opt">
                    <input type="radio" name="tzv2-group-by" checked={groupBy === "department"} onChange={() => setGroupBy("department")} />
                    By department
                  </label>
                </div>

                {groupBy === "department" ? (
                  <div className="field">
                    <label>Department</label>
                    <select className="input" value={groupDepartment} onChange={(event) => setGroupDepartment(event.target.value)} required>
                      <option value="">Select a department...</option>
                      {departments.map((department) => <option value={department} key={department}>{department}</option>)}
                    </select>
                  </div>
                ) : (
                  <div className="tzv2-chat-contact-list">
                    {employees.filter((employee) => employee.id !== currentUserId).map((employee) => (
                      <label className="tzv2-chat-contact-checkbox" key={employee.id}>
                        <input type="checkbox" checked={selectedMemberIds.includes(employee.id)} onChange={() => toggleMember(employee.id)} />
                        <span>{employee.display_name}</span>
                      </label>
                    ))}
                  </div>
                )}

                <div className="dialog-actions">
                  <button type="button" className="btn btn-secondary" onClick={() => setNewChatOpen(false)}>Cancel</button>
                  <button type="submit" className="btn btn-primary" disabled={creatingChat || !groupName.trim() || (groupBy === "department" ? !groupDepartment : selectedMemberIds.length === 0)}>
                    {creatingChat ? "Creating…" : "Create group"}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      ) : null}

      {taskDialogOpen ? (
        <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setTaskDialogOpen(false); }}>
          <form className="dialog tzv2-chat-task-dialog" role="dialog" aria-modal="true" onSubmit={submitTask}>
            <div className="tzv2-chat-dialog-head">
              <span className="dialog-title">Create task</span>
              <button type="button" className="btn btn-ghost btn-icon" aria-label="Close dialog" onClick={() => setTaskDialogOpen(false)}>
                <CloseOutlined fontSize="small" />
              </button>
            </div>

            {taskCreated ? (
              <div className="dialog-body">
                <p>Task created for {taskTargetIds.length} employee{taskTargetIds.length === 1 ? "" : "s"}.</p>
                <div className="dialog-actions">
                  <button type="button" className="btn btn-primary" onClick={() => setTaskDialogOpen(false)}>Done</button>
                </div>
              </div>
            ) : (
              <div className="dialog-body">
                <div className="field">
                  <label>Title</label>
                  <input className="input" value={taskTitle} onChange={(event) => setTaskTitle(event.target.value)} placeholder="e.g. Follow up on IPTV renewal" required />
                </div>
                <div className="field">
                  <label>Type</label>
                  <select className="input" value={taskType} onChange={(event) => setTaskType(event.target.value)}>
                    {(taskOptions.task_types.length ? taskOptions.task_types : ["follow_up", "internal", "other"]).map((type) => (
                      <option value={type} key={type}>{type.replace(/_/g, " ")}</option>
                    ))}
                  </select>
                </div>
                <div className="field">
                  <label>Priority</label>
                  <select className="input" value={taskPriority} onChange={(event) => setTaskPriority(event.target.value)}>
                    {(taskOptions.priorities.length ? taskOptions.priorities : ["low", "normal", "high", "urgent"]).map((priority) => (
                      <option value={priority} key={priority}>{priority}</option>
                    ))}
                  </select>
                </div>
                <div className="field">
                  <label>Due date (optional)</label>
                  <input className="input" type="datetime-local" value={taskDueAt} onChange={(event) => setTaskDueAt(event.target.value)} />
                </div>
                <div className="field">
                  <label>Assign to</label>
                  <div className="tzv2-chat-contact-list">
                    {panelMembers.filter((person) => person.id !== currentUserId).map((person) => (
                      <label className="tzv2-chat-contact-checkbox" key={person.id}>
                        <input type="checkbox" checked={taskTargetIds.includes(person.id)} onChange={() => toggleTaskTarget(person.id)} />
                        <span>{person.display_name}</span>
                      </label>
                    ))}
                    {panelMembers.filter((person) => person.id !== currentUserId).length === 0 ? (
                      <p className="tzv2-chat-aux-empty">No other members here to assign to.</p>
                    ) : null}
                  </div>
                </div>

                {taskError ? <p className="customer-segment-error">{taskError}</p> : null}

                <div className="dialog-actions">
                  <button type="button" className="btn btn-secondary" onClick={() => setTaskDialogOpen(false)}>Cancel</button>
                  <button type="submit" className="btn btn-primary" disabled={creatingTask || !taskTitle.trim() || taskTargetIds.length === 0}>
                    {creatingTask ? "Creating…" : `Create task (${taskTargetIds.length})`}
                  </button>
                </div>
              </div>
            )}
          </form>
        </div>
      ) : null}
    </div>
  );
}
