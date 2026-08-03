import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View,
  Text,
  TextInput,
  FlatList,
  TouchableOpacity,
  StyleSheet,
  RefreshControl,
  ActivityIndicator,
  Alert,
} from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { listConversationsRequest } from "../../api/client";
import { colors, channelColors, fonts, radius } from "../../theme";
import { Kick, Tag, Avatar } from "../../components/ui";
import { SearchIcon } from "../../components/icons";

const POLL_MS = 8000;

function timeLabel(iso) {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  if (d.toDateString() === now.toDateString())
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return "Yst";
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

export default function InboxTab({ navigation, onCount }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all"); // all | mine | ai
  const [search, setSearch] = useState("");
  const [meta, setMeta] = useState({ currentUserId: null, total: 0 });
  const pollRef = useRef(null);

  const load = useCallback(async ({ silent = false } = {}) => {
    try {
      const res = await listConversationsRequest({ folder: "inbox" });
      setItems(res.items || []);
      setMeta({
        currentUserId: res.current_user_id,
        total: res.pagination?.total ?? (res.items || []).length,
      });
      if (onCount) onCount(res.pagination?.total ?? (res.items || []).length);
      setError("");
    } catch (e) {
      if (e.status === 402) {
        Alert.alert("Subscription", "Your company's subscription has expired. Contact your administrator.");
      } else if (!silent) {
        setError(e.message || "Failed to load conversations.");
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [onCount]);

  useEffect(() => {
    load();
  }, [load]);

  useFocusEffect(
    useCallback(() => {
      pollRef.current = setInterval(() => load({ silent: true }), POLL_MS);
      return () => clearInterval(pollRef.current);
    }, [load])
  );

  const mineCount = items.filter((c) => c.assigned_user_id === meta.currentUserId).length;
  const aiCount = items.filter((c) => c.ai_status !== "human").length;

  let visible = items;
  if (filter === "mine") visible = items.filter((c) => c.assigned_user_id === meta.currentUserId);
  else if (filter === "ai") visible = items.filter((c) => c.ai_status !== "human");
  if (search.trim()) {
    const q = search.trim().toLowerCase();
    visible = visible.filter((c) =>
      `${c.customer_name || ""} ${c.customer_alias || ""} ${c.external_user_id || ""} ${c.last_message || ""} ${(c.tags || []).join(" ")}`
        .toLowerCase()
        .includes(q)
    );
  }

  const renderItem = ({ item }) => {
    const name = item.customer_alias || item.customer_name || item.external_user_id;
    const tint = channelColors[item.channel] || colors.neutral600;
    const owner =
      item.ai_status !== "human"
        ? "AI assistant"
        : item.assigned_user_id === meta.currentUserId
        ? "You"
        : item.assigned_user_name || "Unassigned";
    return (
      <TouchableOpacity
        style={styles.row}
        onPress={() =>
          navigation.navigate("Chat", {
            channel: item.channel,
            userId: item.external_user_id,
            name,
            aiStatus: item.ai_status,
          })
        }
      >
        <Avatar initial={(name || "?").charAt(0).toUpperCase()} size={36} />
        <View style={styles.rowBody}>
          <View style={styles.rowTop}>
            <Text style={styles.name} numberOfLines={1}>
              {name}
            </Text>
            {item.unread_count > 0 ? <View style={styles.unreadDot} /> : null}
            <Text style={styles.time}>{timeLabel(item.updated_at)}</Text>
          </View>
          <Text style={styles.preview} numberOfLines={1}>
            {item.last_direction === "out" ? "You: " : ""}
            {item.last_message || "No messages yet"}
          </Text>
          <View style={styles.rowMeta}>
            <View style={[styles.tintSquare, { backgroundColor: tint }]} />
            <Kick style={styles.channelKick}>{item.channel}</Kick>
            <Kick style={styles.ownerKick} numberOfLines={1}>
              {owner}
            </Kick>
          </View>
        </View>
      </TouchableOpacity>
    );
  };

  return (
    <View style={styles.container}>
      <View style={styles.head}>
        <Kick color={colors.accent700}>Desk · {meta.total} open</Kick>
        <Text style={styles.h1}>Conversations</Text>
        <View style={styles.searchBox}>
          <SearchIcon size={13} color={colors.text55} />
          <TextInput
            style={styles.searchInput}
            value={search}
            onChangeText={setSearch}
            placeholder="Search name, number, tag…"
            placeholderTextColor={colors.neutral500}
            autoCapitalize="none"
          />
        </View>
        <View style={styles.tags}>
          <Tag active={filter === "all"} onPress={() => setFilter("all")}>
            All · {items.length}
          </Tag>
          <Tag active={filter === "mine"} onPress={() => setFilter("mine")}>
            Mine · {mineCount}
          </Tag>
          <Tag active={filter === "ai"} onPress={() => setFilter("ai")}>
            AI · {aiCount}
          </Tag>
        </View>
      </View>

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
          data={visible}
          keyExtractor={(item) => item.id}
          renderItem={renderItem}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => {
                setRefreshing(true);
                load();
              }}
            />
          }
          contentContainerStyle={visible.length === 0 ? styles.center : undefined}
          ListEmptyComponent={<Text style={styles.emptyText}>No conversations</Text>}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  head: { paddingHorizontal: 16, paddingTop: 16, paddingBottom: 8 },
  h1: {
    fontFamily: fonts.heading,
    fontSize: 28,
    color: colors.text,
    marginTop: 4,
    marginBottom: 10,
  },
  searchBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: 7,
    paddingHorizontal: 10,
    borderWidth: 1,
    borderColor: colors.divider,
    borderRadius: radius.md,
  },
  searchInput: {
    flex: 1,
    paddingVertical: 8,
    fontFamily: fonts.body,
    fontSize: 13,
    color: colors.text,
  },
  tags: { flexDirection: "row", gap: 6, marginTop: 10, flexWrap: "wrap" },
  row: {
    flexDirection: "row",
    gap: 11,
    paddingHorizontal: 16,
    paddingVertical: 13,
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
  },
  rowBody: { flex: 1, minWidth: 0 },
  rowTop: { flexDirection: "row", alignItems: "baseline", gap: 8 },
  name: {
    fontFamily: fonts.headingSemi,
    fontSize: 16,
    color: colors.text,
    flex: 1,
  },
  unreadDot: { width: 7, height: 7, borderRadius: 4, backgroundColor: colors.accent },
  time: { fontFamily: fonts.body, fontSize: 10.5, color: colors.text45 },
  preview: {
    marginTop: 3,
    fontFamily: fonts.body,
    fontSize: 12.5,
    lineHeight: 19,
    color: colors.text65,
  },
  rowMeta: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 6 },
  tintSquare: { width: 7, height: 7, borderRadius: 2 },
  channelKick: { opacity: 0.75 },
  ownerKick: { marginLeft: "auto", opacity: 0.5 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 30 },
  emptyText: { color: colors.text45, fontSize: 14, fontFamily: fonts.body },
  errorText: { color: "#b3372f", fontSize: 14, textAlign: "center", marginBottom: 12, fontFamily: fonts.body },
  retryText: { color: colors.accent700, fontFamily: fonts.bodyMedium, fontSize: 14 },
});
