import React, { useCallback, useEffect, useState } from "react";
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
  ActivityIndicator,
  TextInput,
  Alert,
} from "react-native";
import {
  listScheduledPostsRequest,
  getScheduledPostOptionsRequest,
  listCommentPostsRequest,
  listPostCommentsRequest,
  replyToCommentRequest,
} from "../../api/client";
import { colors, fonts, radius } from "../../theme";
import { Kick, Tag, Btn, Sheet } from "../../components/ui";

function scheduleLabel(iso) {
  if (!iso) return "Draft";
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString([], { weekday: "short" }) + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function PublishTab() {
  const [view, setView] = useState("queue"); // queue | comments
  const [posts, setPosts] = useState([]);
  const [accounts, setAccounts] = useState({});
  const [commentPosts, setCommentPosts] = useState([]);
  const [unansweredTotal, setUnansweredTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Comment thread sheet
  const [openPost, setOpenPost] = useState(null);
  const [comments, setComments] = useState([]);
  const [commentsLoading, setCommentsLoading] = useState(false);
  const [replyTarget, setReplyTarget] = useState(null);
  const [replyText, setReplyText] = useState("");
  const [replying, setReplying] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [postsRes, optionsRes] = await Promise.all([
        listScheduledPostsRequest("scheduled"),
        getScheduledPostOptionsRequest().catch(() => null),
      ]);
      setPosts(postsRes.items || []);
      if (optionsRes) {
        const byId = {};
        (optionsRes.channel_accounts || []).forEach((a) => {
          byId[a.id] = a;
        });
        setAccounts(byId);
      }
      setError("");
    } catch (e) {
      setError(e.status === 403 ? "You don't have access to publishing." : e.message || "Failed to load.");
    } finally {
      setLoading(false);
    }

    try {
      const c = await listCommentPostsRequest();
      setCommentPosts(c.posts || []);
      setUnansweredTotal(c.unanswered_total || 0);
    } catch (_) {
      // Comments module may be disabled or not permitted — hide gracefully.
      setCommentPosts([]);
      setUnansweredTotal(0);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openComments = async (post) => {
    setOpenPost(post);
    setCommentsLoading(true);
    setReplyTarget(null);
    setReplyText("");
    try {
      const res = await listPostCommentsRequest(post.post_external_id);
      setComments(res.comments || []);
    } catch (e) {
      Alert.alert("Error", e.message || "Failed to load comments.");
      setOpenPost(null);
    } finally {
      setCommentsLoading(false);
    }
  };

  const sendReply = async () => {
    const text = replyText.trim();
    if (!text || !replyTarget || replying) return;
    setReplying(true);
    try {
      await replyToCommentRequest(replyTarget.id, text);
      setComments((prev) =>
        prev.map((c) => (c.id === replyTarget.id ? { ...c, status: "answered" } : c))
      );
      setReplyTarget(null);
      setReplyText("");
    } catch (e) {
      Alert.alert("Not sent", e.message || "The reply could not be sent.");
    } finally {
      setReplying(false);
    }
  };

  const postNetworks = (p) =>
    (p.channel_account_ids || [])
      .map((id) => accounts[id]?.name || accounts[id]?.channel || "")
      .filter(Boolean)
      .join(" · ");

  return (
    <View style={styles.container}>
      <View style={styles.head}>
        <Kick color={colors.accent700}>Community</Kick>
        <Text style={styles.h1}>Publish</Text>
        <View style={styles.seg}>
          <TouchableOpacity
            style={[styles.segOpt, view === "queue" && styles.segOn]}
            onPress={() => setView("queue")}
          >
            <Text style={[styles.segText, view === "queue" && styles.segTextOn]}>Queue</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.segOpt, view === "comments" && styles.segOn]}
            onPress={() => setView("comments")}
          >
            <Text style={[styles.segText, view === "comments" && styles.segTextOn]}>
              Comments{unansweredTotal ? ` · ${unansweredTotal}` : ""}
            </Text>
          </TouchableOpacity>
        </View>
      </View>

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.accent} size="large" />
        </View>
      ) : error ? (
        <View style={styles.center}>
          <Text style={styles.errorText}>{error}</Text>
        </View>
      ) : view === "queue" ? (
        <ScrollView contentContainerStyle={styles.list}>
          {posts.length === 0 ? (
            <Text style={styles.emptyText}>No scheduled posts</Text>
          ) : (
            posts.map((p) => (
              <View key={p.id} style={styles.card}>
                <View style={styles.cardTop}>
                  <Kick color={colors.accent700}>{scheduleLabel(p.scheduled_at)}</Kick>
                  <Kick style={{ opacity: 0.5 }}>{postNetworks(p)}</Kick>
                  {p.media_type ? <Tag style={styles.kindTag}>{p.media_type}</Tag> : null}
                </View>
                <Text style={styles.cardText}>{p.text || "(no text)"}</Text>
              </View>
            ))
          )}
          <Text style={styles.footNote}>New posts are created from the web platform.</Text>
        </ScrollView>
      ) : (
        <ScrollView>
          {commentPosts.length === 0 ? (
            <View style={styles.center}>
              <Text style={styles.emptyText}>No comments to review</Text>
            </View>
          ) : (
            commentPosts.map((p) => (
              <TouchableOpacity key={p.id} style={styles.commentRow} onPress={() => openComments(p)}>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <View style={{ flexDirection: "row", alignItems: "baseline", gap: 8 }}>
                    <Text style={styles.commentWho} numberOfLines={1}>
                      {p.channel_account_name || p.channel}
                    </Text>
                    <Kick color={colors.accent700}>{p.channel}</Kick>
                  </View>
                  <Text style={styles.commentText} numberOfLines={2}>
                    {p.caption || "(no caption)"}
                  </Text>
                </View>
                <View style={styles.commentRight}>
                  <Text style={styles.fig}>{p.unanswered_count}</Text>
                  <Kick style={{ opacity: 0.5 }}>waiting</Kick>
                </View>
              </TouchableOpacity>
            ))
          )}
        </ScrollView>
      )}

      <Sheet
        visible={!!openPost}
        title={openPost ? `Comments · ${openPost.channel}` : ""}
        onClose={() => setOpenPost(null)}
      >
        {commentsLoading ? (
          <ActivityIndicator color={colors.accent} style={{ marginVertical: 20 }} />
        ) : (
          <ScrollView style={{ maxHeight: 380 }}>
            {comments.map((c) => (
              <View key={c.id} style={styles.sheetComment}>
                <View style={{ flexDirection: "row", alignItems: "baseline", gap: 8 }}>
                  <Text style={styles.commentWho}>{c.author_name || "User"}</Text>
                  {c.is_from_business ? (
                    <Kick color={colors.accent2_700}>You</Kick>
                  ) : c.status === "unanswered" ? (
                    <Kick color={colors.accent700}>Waiting</Kick>
                  ) : null}
                </View>
                <Text style={styles.commentText}>{c.text}</Text>
                {!c.is_from_business ? (
                  <Btn
                    kind="secondary"
                    small
                    style={styles.replyBtn}
                    onPress={() => setReplyTarget(c)}
                  >
                    Reply
                  </Btn>
                ) : null}
              </View>
            ))}
          </ScrollView>
        )}
        {replyTarget ? (
          <View style={styles.replyBox}>
            <Kick color={colors.accent700} style={{ marginBottom: 6 }}>
              Reply to {replyTarget.author_name || "user"}
            </Kick>
            <TextInput
              style={styles.replyInput}
              value={replyText}
              onChangeText={setReplyText}
              placeholder="Write a public reply…"
              placeholderTextColor={colors.neutral500}
              multiline
            />
            <View style={{ flexDirection: "row", gap: 8, marginTop: 8 }}>
              <Btn kind="secondary" small onPress={() => setReplyTarget(null)}>
                Cancel
              </Btn>
              <Btn kind="primary" small onPress={sendReply} disabled={replying || !replyText.trim()}>
                {replying ? "Sending…" : "Send reply"}
              </Btn>
            </View>
          </View>
        ) : null}
      </Sheet>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  head: {
    paddingHorizontal: 16,
    paddingTop: 16,
    paddingBottom: 10,
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
  },
  h1: { fontFamily: fonts.heading, fontSize: 28, color: colors.text, marginTop: 4, marginBottom: 10 },
  seg: {
    flexDirection: "row",
    borderWidth: 1,
    borderColor: colors.divider,
    borderRadius: radius.md,
    overflow: "hidden",
  },
  segOpt: { flex: 1, alignItems: "center", paddingVertical: 8 },
  segOn: { borderColor: colors.accent, borderWidth: 1, borderRadius: radius.md, margin: -1 },
  segText: { fontFamily: fonts.body, fontSize: 13, color: colors.text65 },
  segTextOn: { color: colors.accent },
  list: { padding: 16, gap: 10 },
  card: {
    borderWidth: 1,
    borderColor: colors.divider,
    borderRadius: radius.md,
    padding: 12,
  },
  cardTop: { flexDirection: "row", alignItems: "center", gap: 6 },
  kindTag: { marginLeft: "auto", paddingHorizontal: 8, paddingVertical: 2 },
  cardText: { marginTop: 7, fontFamily: fonts.body, fontSize: 13, lineHeight: 21, color: colors.text },
  footNote: { fontFamily: fonts.body, fontSize: 11.5, color: colors.text45, textAlign: "center", marginTop: 6 },
  commentRow: {
    flexDirection: "row",
    gap: 12,
    alignItems: "center",
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
  },
  commentWho: { fontFamily: fonts.headingSemi, fontSize: 15, color: colors.text },
  commentText: { marginTop: 3, fontFamily: fonts.body, fontSize: 12.5, color: colors.text65, lineHeight: 19 },
  commentRight: { alignItems: "flex-end" },
  fig: { fontFamily: fonts.heading, fontSize: 20, color: colors.text },
  sheetComment: { paddingVertical: 11, borderBottomWidth: 1, borderBottomColor: colors.divider },
  replyBtn: { alignSelf: "flex-start", marginTop: 8 },
  replyBox: { paddingTop: 12 },
  replyInput: {
    borderWidth: 1,
    borderColor: colors.divider,
    borderRadius: radius.md,
    padding: 10,
    minHeight: 60,
    fontFamily: fonts.body,
    fontSize: 13,
    color: colors.text,
    textAlignVertical: "top",
  },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 30 },
  emptyText: { color: colors.text45, fontSize: 14, fontFamily: fonts.body, textAlign: "center", padding: 20 },
  errorText: { color: "#b3372f", fontSize: 14, textAlign: "center", fontFamily: fonts.body },
});
