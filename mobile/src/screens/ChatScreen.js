import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
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
} from "react-native";
import { useHeaderHeight } from "@react-navigation/elements";
import {
  getMessagesRequest,
  sendReplyRequest,
  takeOverRequest,
  returnToAiRequest,
} from "../api/client";
import { colors, channelColors, radius } from "../theme";

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
  const headerHeight = useHeaderHeight();

  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [aiActive, setAiActive] = useState(initialAiStatus !== "human");
  const pollRef = useRef(null);
  const listRef = useRef(null);

  useLayoutEffect(() => {
    navigation.setOptions({
      title: name,
      headerRight: () => (
        <View style={[styles.channelPill, { backgroundColor: channelColors[channel] || colors.textMuted }]}>
          <Text style={styles.channelPillText}>{channel}</Text>
        </View>
      ),
    });
  }, [navigation, name, channel]);

  const load = useCallback(
    async ({ markRead = false, silent = false } = {}) => {
      try {
        const res = await getMessagesRequest(channel, userId, { limit: 300, markRead });
        const clean = (res.messages || []).filter((m) => m && !m.raw);
        setMessages(clean);
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

  useEffect(() => {
    load({ markRead: true });
    pollRef.current = setInterval(() => load({ silent: true }), POLL_MS);
    return () => clearInterval(pollRef.current);
  }, [load]);

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
          // AI still owns the conversation — take over, then retry once.
          await takeOverRequest(channel, userId);
          setAiActive(false);
          result = await sendReplyRequest(channel, userId, body);
        } else {
          throw e;
        }
      }
      setText("");
      if (result?.message) {
        setMessages((prev) => [...prev, result.message]);
      }
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

  const renderMessage = ({ item }) => {
    const isOut = item.direction === "out";
    const meta = item.metadata || {};
    const senderLabel = isOut
      ? meta.sender_type === "employee"
        ? meta.employee_name || "Team"
        : "AI Assistant"
      : null;
    const isImage = meta.media_type === "image" && typeof meta.media_url === "string" && meta.media_url.startsWith("http");
    const mediaLabel =
      !isImage && meta.media_type
        ? meta.media_type === "audio"
          ? "🎤 Voice message"
          : meta.media_type === "video"
          ? "🎬 Video"
          : `📎 ${meta.media_filename || "Attachment"}`
        : null;

    return (
      <View style={[styles.msgRow, isOut ? styles.msgRowOut : styles.msgRowIn]}>
        <View style={[styles.bubble, isOut ? styles.bubbleOut : styles.bubbleIn]}>
          {senderLabel ? (
            <Text style={[styles.sender, isOut && styles.senderOut]}>{senderLabel}</Text>
          ) : null}
          {isImage ? <Image source={{ uri: meta.media_url }} style={styles.image} resizeMode="cover" /> : null}
          {mediaLabel ? (
            <Text style={[styles.msgText, isOut && styles.msgTextOut]}>{mediaLabel}</Text>
          ) : null}
          {item.text ? (
            <Text style={[styles.msgText, isOut && styles.msgTextOut]}>{item.text}</Text>
          ) : null}
          <Text style={[styles.msgTime, isOut && styles.msgTimeOut]}>
            {bubbleTime(item.time)}
            {isOut && item.delivery_status ? `  ${item.delivery_status === "read" ? "✓✓" : "✓"}` : ""}
          </Text>
        </View>
      </View>
    );
  };

  const inverted = [...messages].reverse();

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      keyboardVerticalOffset={headerHeight}
    >
      <View style={[styles.aiBanner, aiActive ? styles.aiBannerActive : styles.aiBannerHuman]}>
        <Text style={[styles.aiBannerText, aiActive ? styles.aiBannerTextActive : styles.aiBannerTextHuman]}>
          {aiActive ? "AI Assistant is handling this conversation" : "You are handling this conversation"}
        </Text>
        <TouchableOpacity onPress={toggleAi} style={styles.aiBannerBtn}>
          <Text style={styles.aiBannerBtnText}>{aiActive ? "Take over" : "Return to AI"}</Text>
        </TouchableOpacity>
      </View>

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.primary} size="large" />
        </View>
      ) : error ? (
        <View style={styles.center}>
          <Text style={styles.errorText}>{error}</Text>
          <TouchableOpacity style={styles.retryBtn} onPress={() => load({ silent: false })}>
            <Text style={styles.retryText}>Retry</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <FlatList
          ref={listRef}
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

      <View style={styles.composer}>
        <TextInput
          style={styles.input}
          value={text}
          onChangeText={setText}
          placeholder="Type a reply…"
          placeholderTextColor={colors.textMuted}
          multiline
          maxLength={2000}
        />
        <TouchableOpacity
          style={[styles.sendBtn, (!text.trim() || sending) && styles.sendBtnDisabled]}
          onPress={doSend}
          disabled={!text.trim() || sending}
        >
          {sending ? <ActivityIndicator color="#fff" size="small" /> : <Text style={styles.sendText}>➤</Text>}
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  channelPill: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: radius.round,
  },
  channelPillText: { color: "#fff", fontSize: 11, fontWeight: "700", textTransform: "capitalize" },
  aiBanner: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  aiBannerActive: { backgroundColor: colors.primarySoft },
  aiBannerHuman: { backgroundColor: colors.warningSoft },
  aiBannerText: { fontSize: 12, fontWeight: "600", flex: 1, marginRight: 10 },
  aiBannerTextActive: { color: colors.primaryDark },
  aiBannerTextHuman: { color: colors.warning },
  aiBannerBtn: {
    backgroundColor: colors.surface,
    borderRadius: radius.round,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderWidth: 1,
    borderColor: colors.borderStrong,
  },
  aiBannerBtnText: { fontSize: 12, fontWeight: "700", color: colors.textPrimary },
  listContent: { paddingHorizontal: 14, paddingVertical: 12 },
  msgRow: { marginVertical: 3, flexDirection: "row" },
  msgRowIn: { justifyContent: "flex-start" },
  msgRowOut: { justifyContent: "flex-end" },
  bubble: {
    maxWidth: "80%",
    borderRadius: radius.md,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  bubbleIn: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderBottomLeftRadius: 4,
  },
  bubbleOut: { backgroundColor: colors.primary, borderBottomRightRadius: 4 },
  sender: { fontSize: 11, fontWeight: "700", color: colors.primaryDark, marginBottom: 2 },
  senderOut: { color: "rgba(255,255,255,0.85)" },
  msgText: { fontSize: 15, color: colors.textPrimary, lineHeight: 21 },
  msgTextOut: { color: "#fff" },
  msgTime: { fontSize: 10, color: colors.textMuted, marginTop: 3, alignSelf: "flex-end" },
  msgTimeOut: { color: "rgba(255,255,255,0.7)" },
  image: { width: 220, height: 220, borderRadius: radius.sm, marginBottom: 6 },
  composer: {
    flexDirection: "row",
    alignItems: "flex-end",
    paddingHorizontal: 12,
    paddingVertical: 8,
    backgroundColor: colors.surface,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    gap: 8,
  },
  input: {
    flex: 1,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.lg,
    paddingHorizontal: 14,
    paddingTop: 10,
    paddingBottom: 10,
    fontSize: 15,
    color: colors.textPrimary,
    maxHeight: 110,
  },
  sendBtn: {
    width: 42,
    height: 42,
    borderRadius: 21,
    backgroundColor: colors.primary,
    alignItems: "center",
    justifyContent: "center",
  },
  sendBtnDisabled: { opacity: 0.4 },
  sendText: { color: "#fff", fontSize: 18, marginLeft: 2 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 30 },
  centerInverted: { transform: [{ scaleY: -1 }], alignItems: "center", padding: 30 },
  emptyText: { color: colors.textMuted, fontSize: 14 },
  errorText: { color: colors.danger, fontSize: 14, textAlign: "center", marginBottom: 12 },
  retryBtn: {
    backgroundColor: colors.primary,
    borderRadius: radius.sm,
    paddingHorizontal: 20,
    paddingVertical: 10,
  },
  retryText: { color: "#fff", fontWeight: "700" },
});
