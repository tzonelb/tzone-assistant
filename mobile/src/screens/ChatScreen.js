import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View,
  Text,
  FlatList,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  KeyboardAvoidingView,
  Platform,
  ActivityIndicator,
  Image,
  Alert,
  ScrollView,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  getMessagesRequest,
  sendReplyRequest,
  takeOverRequest,
  returnToAiRequest,
  getControlRequest,
  updateControlRequest,
  getSavedRepliesRequest,
} from "../api/client";
import { colors, fonts, radius } from "../theme";
import { Kick, Btn, Avatar, Sheet } from "../components/ui";
import { BackIcon, PersonIcon, SparkIcon, SendIcon, MicIcon } from "../components/icons";

const POLL_MS = 3000;

function bubbleTime(iso) {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function messageKey(msg, index) {
  return msg?.metadata?.provider_message_id || `${msg?.time || "t"}-${index}`;
}

export default function ChatScreen({ route, navigation }) {
  const { channel, userId, name, aiStatus: initialAiStatus } = route.params;
  const insets = useSafeAreaInsets();

  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [aiActive, setAiActive] = useState(initialAiStatus !== "human");
  const [control, setControl] = useState(null); // {conversation, employees, permissions, ...}
  const [sheet, setSheet] = useState(null); // null | "transfer" | "saved" | "profile"
  const [savedReplies, setSavedReplies] = useState(null);
  const pollRef = useRef(null);

  const load = useCallback(
    async ({ markRead = false, silent = false } = {}) => {
      try {
        const res = await getMessagesRequest(channel, userId, { limit: 300, markRead });
        setMessages((res.messages || []).filter((m) => m && !m.raw));
        setError("");
      } catch (e) {
        if (e.status === 404) {
          setMessages([]);
          setError("");
        } else if (!silent) {
          setError(e.message || "Failed to load messages.");
        }
      } finally {
        setLoading(false);
      }
    },
    [channel, userId]
  );

  const loadControl = useCallback(async () => {
    try {
      const res = await getControlRequest(channel, userId);
      setControl(res);
      if (res.conversation) setAiActive(res.conversation.ai_status !== "human");
    } catch (_) {
      // Non-fatal — chat still works without control metadata.
    }
  }, [channel, userId]);

  useEffect(() => {
    load({ markRead: true });
    loadControl();
    pollRef.current = setInterval(() => load({ silent: true }), POLL_MS);
    return () => clearInterval(pollRef.current);
  }, [load, loadControl]);

  const doSend = async () => {
    const body = text.trim();
    if (!body || sending) return;
    setSending(true);
    try {
      let result;
      try {
        result = await sendReplyRequest(channel, userId, body);
      } catch (e) {
        const ownedBySomeoneElse = e.status === 409 && e.data?.detail?.code === "conversation_owned";
        if (ownedBySomeoneElse) {
          const owner = e.data.detail.owner_user_name || "another teammate";
          Alert.alert("Conversation taken", `${owner} is currently handling this conversation.`);
          return;
        }
        if (e.status === 409) {
          await takeOverRequest(channel, userId);
          setAiActive(false);
          result = await sendReplyRequest(channel, userId, body);
        } else {
          throw e;
        }
      }
      setText("");
      if (result?.message) setMessages((prev) => [...prev, result.message]);
      setAiActive(false);
    } catch (e) {
      Alert.alert("Not sent", e.message || "The message could not be sent.");
    } finally {
      setSending(false);
    }
  };

  const toggleAi = async () => {
    try {
      if (aiActive) {
        await takeOverRequest(channel, userId);
        setAiActive(false);
      } else {
        await returnToAiRequest(channel, userId);
        setAiActive(true);
      }
    } catch (e) {
      const owned = e.status === 409 && e.data?.detail?.code === "conversation_owned";
      Alert.alert(
        owned ? "Conversation taken" : "Error",
        owned
          ? `${e.data.detail.owner_user_name || "Another teammate"} is currently handling this conversation.`
          : e.message || "Action failed."
      );
    }
  };

  const openSaved = async () => {
    setSheet("saved");
    if (savedReplies === null) {
      try {
        const res = await getSavedRepliesRequest();
        setSavedReplies(res.replies || []);
      } catch (e) {
        setSavedReplies([]);
      }
    }
  };

  const assignTo = async (employee) => {
    try {
      await updateControlRequest(channel, userId, { assigned_user_id: employee.id });
      setSheet(null);
      loadControl();
      Alert.alert("Transferred", `Conversation assigned to ${employee.display_name || employee.full_name}.`);
    } catch (e) {
      const owned = e.status === 409 && e.data?.detail?.code === "conversation_owned";
      Alert.alert(
        "Transfer failed",
        owned
          ? `${e.data.detail.owner_user_name || "Another teammate"} currently owns this conversation.`
          : e.message || "Could not transfer."
      );
    }
  };

  const conv = control?.conversation;
  const headKick = [channel, userId, conv?.department && conv.department !== "Unassigned" ? conv.department : null]
    .filter(Boolean)
    .join(" · ");

  const renderMessage = ({ item }) => {
    const isOut = item.direction === "out";
    const meta = item.metadata || {};
    const who = isOut
      ? meta.sender_type === "employee"
        ? meta.employee_name || "Team"
        : "AI assistant"
      : (name || "Customer").split(/\s+/)[0];
    const whoTint = isOut
      ? meta.sender_type === "employee"
        ? colors.accent700
        : colors.accent2_700
      : colors.neutral700;
    const isImage =
      meta.media_type === "image" && typeof meta.media_url === "string" && meta.media_url.startsWith("http");
    const mediaLabel =
      !isImage && meta.media_type
        ? meta.media_type === "audio"
          ? "🎤 Voice message"
          : meta.media_type === "video"
          ? "🎬 Video"
          : `📎 ${meta.media_filename || "Attachment"}`
        : null;
    const ticks = isOut && item.delivery_status ? (item.delivery_status === "read" ? " ✓✓" : " ✓") : "";

    return (
      <View style={[styles.msgWrap, isOut ? styles.msgWrapOut : styles.msgWrapIn]}>
        <View style={styles.msgHead}>
          <Kick style={{ color: whoTint }}>{who}</Kick>
          <Text style={styles.msgTime}>
            {bubbleTime(item.time)}
            {ticks}
          </Text>
        </View>
        <View style={[styles.bubble, isOut && styles.bubbleOut]}>
          {isImage ? <Image source={{ uri: meta.media_url }} style={styles.image} resizeMode="cover" /> : null}
          {mediaLabel ? <Text style={styles.msgText}>{mediaLabel}</Text> : null}
          {item.text ? <Text style={styles.msgText}>{item.text}</Text> : null}
        </View>
      </View>
    );
  };

  const inverted = [...messages].reverse();

  return (
    <KeyboardAvoidingView
      style={[styles.container, { paddingTop: insets.top }]}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity onPress={() => navigation.goBack()} style={styles.backBtn}>
          <BackIcon size={18} color={colors.text} />
        </TouchableOpacity>
        <Avatar initial={(name || "?").charAt(0).toUpperCase()} size={34} accent />
        <TouchableOpacity style={styles.headBody} onPress={() => setSheet("profile")}>
          <Text style={styles.headName} numberOfLines={1}>
            {name}
          </Text>
          <Kick style={styles.headKick} numberOfLines={1}>
            {headKick}
          </Kick>
        </TouchableOpacity>
        <TouchableOpacity style={styles.profileBtn} onPress={() => setSheet("profile")}>
          <PersonIcon size={16} color={colors.text} />
        </TouchableOpacity>
      </View>

      {/* AI ownership banner */}
      <View style={[styles.banner, aiActive ? styles.bannerAi : styles.bannerHuman]}>
        <SparkIcon size={14} color={aiActive ? colors.accent2_700 : colors.accent700} />
        <Text style={[styles.bannerText, { color: aiActive ? colors.accent2_800 : colors.accent800 }]}>
          {aiActive ? "AI is replying for you." : "You are replying — AI is paused."}
        </Text>
        <Btn kind={aiActive ? "primary" : "secondary"} small onPress={toggleAi}>
          {aiActive ? "Take over" : "Return to AI"}
        </Btn>
      </View>

      {/* Messages */}
      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.accent} size="large" />
        </View>
      ) : error ? (
        <View style={styles.center}>
          <Text style={styles.errorText}>{error}</Text>
          <TouchableOpacity onPress={() => load()}>
            <Text style={styles.retryText}>Retry</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <FlatList
          data={inverted}
          inverted
          keyExtractor={messageKey}
          renderItem={renderMessage}
          contentContainerStyle={styles.listContent}
          ListEmptyComponent={
            <View style={styles.centerInverted}>
              <Text style={styles.emptyText}>No messages yet</Text>
            </View>
          }
        />
      )}

      {/* Quick actions */}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={styles.quickRow}
        contentContainerStyle={styles.quickContent}
      >
        <Btn kind="secondary" onPress={() => setSheet("transfer")} style={styles.quickBtn}>
          Transfer
        </Btn>
        <Btn kind="secondary" onPress={openSaved} style={styles.quickBtn}>
          Saved replies
        </Btn>
      </ScrollView>

      {/* Composer */}
      <View style={[styles.composer, { paddingBottom: Math.max(insets.bottom, 12) }]}>
        <View style={styles.inputBox}>
          <TextInput
            style={styles.input}
            value={text}
            onChangeText={setText}
            placeholder="Write a reply…"
            placeholderTextColor={colors.neutral500}
            multiline
            maxLength={2000}
          />
        </View>
        <TouchableOpacity
          style={styles.roundBtn}
          onPress={() => Alert.alert("Voice notes", "Recording voice notes from the app is coming soon.")}
        >
          <MicIcon size={16} color={colors.text} />
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.roundBtn, styles.roundBtnPrimary, (!text.trim() || sending) && styles.roundBtnDisabled]}
          onPress={doSend}
          disabled={!text.trim() || sending}
        >
          {sending ? (
            <ActivityIndicator color={colors.accent} size="small" />
          ) : (
            <SendIcon size={16} color={colors.accent} />
          )}
        </TouchableOpacity>
      </View>

      {/* Transfer sheet */}
      <Sheet visible={sheet === "transfer"} title="Transfer conversation" onClose={() => setSheet(null)}>
        <ScrollView style={{ maxHeight: 380 }}>
          {(control?.employees || []).map((emp) => (
            <View key={emp.id} style={styles.teamRow}>
              <Avatar initial={(emp.display_name || "?").charAt(0).toUpperCase()} size={32} />
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={styles.teamName}>{emp.display_name || emp.full_name || emp.email}</Text>
                <Kick style={{ opacity: 0.55 }}>
                  {(emp.departments && emp.departments.length ? emp.departments.join(" · ") : emp.role_name) || ""}
                </Kick>
              </View>
              <Btn kind="secondary" small onPress={() => assignTo(emp)}>
                Assign
              </Btn>
            </View>
          ))}
          {!control?.employees?.length ? (
            <Text style={styles.sheetNote}>No teammates available.</Text>
          ) : (
            <Text style={styles.sheetNote}>One owner at a time. Transfer is recorded in the audit trail.</Text>
          )}
        </ScrollView>
      </Sheet>

      {/* Saved replies sheet */}
      <Sheet visible={sheet === "saved"} title="Saved replies" onClose={() => setSheet(null)}>
        {savedReplies === null ? (
          <ActivityIndicator color={colors.accent} style={{ marginVertical: 20 }} />
        ) : savedReplies.length === 0 ? (
          <Text style={styles.sheetNote}>No saved replies yet. Create them on the web platform.</Text>
        ) : (
          <ScrollView style={{ maxHeight: 380 }}>
            {savedReplies.map((r) => (
              <TouchableOpacity
                key={r.id}
                style={styles.savedRow}
                onPress={() => {
                  setText(r.body);
                  setSheet(null);
                }}
              >
                <Text style={styles.teamName}>{r.title}</Text>
                <Text style={styles.savedBody} numberOfLines={2}>
                  {r.body}
                </Text>
              </TouchableOpacity>
            ))}
          </ScrollView>
        )}
      </Sheet>

      {/* Customer & controls sheet */}
      <Sheet visible={sheet === "profile"} title="Customer & controls" onClose={() => setSheet(null)}>
        <View style={styles.kvBlock}>
          <View style={styles.kv}>
            <Text style={styles.kvKey}>Channel</Text>
            <Text style={styles.kvVal}>{channel}</Text>
          </View>
          <View style={styles.kv}>
            <Text style={styles.kvKey}>Contact</Text>
            <Text style={styles.kvVal}>{userId}</Text>
          </View>
          <View style={styles.kv}>
            <Text style={styles.kvKey}>Status</Text>
            <Text style={styles.kvVal}>{conv?.status || "—"}</Text>
          </View>
          <View style={styles.kv}>
            <Text style={styles.kvKey}>Department</Text>
            <Text style={styles.kvVal}>{conv?.department || "—"}</Text>
          </View>
          <View style={styles.kv}>
            <Text style={styles.kvKey}>Owner</Text>
            <Text style={styles.kvVal}>
              {aiActive ? "AI assistant" : conv?.assigned_user_name || "Unassigned"}
            </Text>
          </View>
        </View>
      </Sheet>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
  },
  backBtn: { padding: 6 },
  headBody: { flex: 1, minWidth: 0 },
  headName: { fontFamily: fonts.headingSemi, fontSize: 17, color: colors.text },
  headKick: { opacity: 0.55, marginTop: 1 },
  profileBtn: {
    padding: 8,
    borderWidth: 1,
    borderColor: "transparent",
  },
  banner: {
    flexDirection: "row",
    alignItems: "center",
    gap: 9,
    paddingHorizontal: 14,
    paddingVertical: 9,
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
  },
  bannerAi: { backgroundColor: colors.aiBannerBg },
  bannerHuman: { backgroundColor: colors.humanBannerBg },
  bannerText: { flex: 1, fontFamily: fonts.body, fontSize: 12 },
  listContent: { paddingHorizontal: 14, paddingVertical: 12 },
  msgWrap: { maxWidth: "80%", marginVertical: 6 },
  msgWrapIn: { alignSelf: "flex-start" },
  msgWrapOut: { alignSelf: "flex-end" },
  msgHead: { flexDirection: "row", alignItems: "baseline", gap: 7, marginBottom: 3 },
  msgTime: { fontFamily: fonts.body, fontSize: 10, color: colors.text45 },
  bubble: {
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderWidth: 1,
    borderColor: colors.divider,
    borderRadius: radius.md,
    backgroundColor: "transparent",
  },
  bubbleOut: { backgroundColor: colors.bubbleOut },
  msgText: { fontFamily: fonts.body, fontSize: 13.5, lineHeight: 22, color: colors.text },
  image: { width: 220, height: 220, borderRadius: radius.sm, marginBottom: 6 },
  quickRow: { flexGrow: 0, borderTopWidth: 1, borderTopColor: colors.divider },
  quickContent: { gap: 6, paddingHorizontal: 12, paddingVertical: 7 },
  quickBtn: { paddingVertical: 8 },
  composer: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 8,
    paddingHorizontal: 12,
    paddingTop: 8,
  },
  inputBox: {
    flex: 1,
    minWidth: 0,
    borderWidth: 1,
    borderColor: colors.divider,
    borderRadius: radius.md,
    paddingHorizontal: 10,
    paddingVertical: 5,
  },
  input: {
    fontFamily: fonts.body,
    fontSize: 13.5,
    color: colors.text,
    minHeight: 32,
    maxHeight: 110,
    paddingVertical: 4,
  },
  roundBtn: {
    width: 44,
    height: 44,
    borderRadius: 22,
    borderWidth: 1,
    borderColor: colors.divider,
    alignItems: "center",
    justifyContent: "center",
  },
  roundBtnPrimary: { borderColor: colors.accent },
  roundBtnDisabled: { opacity: 0.4 },
  teamRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingVertical: 11,
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
  },
  teamName: { fontFamily: fonts.headingSemi, fontSize: 15, color: colors.text },
  savedRow: { paddingVertical: 11, borderBottomWidth: 1, borderBottomColor: colors.divider },
  savedBody: { marginTop: 3, fontFamily: fonts.body, fontSize: 12.5, color: colors.text65, lineHeight: 19 },
  sheetNote: { marginTop: 10, fontFamily: fonts.body, fontSize: 11.5, color: colors.text55 },
  kvBlock: { paddingBottom: 6 },
  kv: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 9,
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
  },
  kvKey: { fontFamily: fonts.body, fontSize: 13, color: colors.text55 },
  kvVal: { fontFamily: fonts.bodyMedium, fontSize: 13, color: colors.text, textTransform: "capitalize" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 30 },
  centerInverted: { transform: [{ scaleY: -1 }], alignItems: "center", padding: 30 },
  emptyText: { color: colors.text45, fontSize: 14, fontFamily: fonts.body },
  errorText: { color: "#b3372f", fontSize: 14, textAlign: "center", marginBottom: 12, fontFamily: fonts.body },
  retryText: { color: colors.accent700, fontFamily: fonts.bodyMedium, fontSize: 14 },
});
