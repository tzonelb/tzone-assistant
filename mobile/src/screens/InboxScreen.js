import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View,
  Text,
  FlatList,
  TouchableOpacity,
  StyleSheet,
  RefreshControl,
  ActivityIndicator,
  TextInput,
  ScrollView,
  Alert,
} from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { listConversationsRequest } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { colors, channelColors, radius } from "../theme";

const POLL_MS = 8000;

function timeLabel(iso) {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

function initials(name) {
  if (!name) return "?";
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((p) => p[0])
    .join("")
    .toUpperCase();
}

export default function InboxScreen({ navigation }) {
  const { logout, user } = useAuth();
  const insets = useSafeAreaInsets();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [channel, setChannel] = useState("all");
  const [channelCounts, setChannelCounts] = useState({});
  const [search, setSearch] = useState("");
  const pollRef = useRef(null);
  const channelRef = useRef(channel);
  channelRef.current = channel;

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setError("");
    try {
      const res = await listConversationsRequest({ channel: channelRef.current, folder: "inbox" });
      setItems(res.items || []);
      setChannelCounts(res.channel_counts || {});
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
  }, []);

  useEffect(() => {
    setLoading(true);
    load();
  }, [channel, load]);

  useFocusEffect(
    useCallback(() => {
      load({ silent: true });
      pollRef.current = setInterval(() => load({ silent: true }), POLL_MS);
      return () => clearInterval(pollRef.current);
    }, [load])
  );

  const onRefresh = () => {
    setRefreshing(true);
    load();
  };

  const filtered = search.trim()
    ? items.filter((c) =>
        `${c.customer_name || ""} ${c.customer_alias || ""} ${c.last_message || ""}`
          .toLowerCase()
          .includes(search.trim().toLowerCase())
      )
    : items;

  const channels = ["all", ...Object.keys(channelCounts)];

  const renderItem = ({ item }) => {
    const cColor = channelColors[item.channel] || colors.textMuted;
    const unread = item.unread_count > 0;
    return (
      <TouchableOpacity
        style={styles.row}
        onPress={() =>
          navigation.navigate("Chat", {
            channel: item.channel,
            userId: item.external_user_id,
            name: item.customer_alias || item.customer_name || item.external_user_id,
            aiStatus: item.ai_status,
          })
        }
      >
        <View style={[styles.avatar, { borderColor: cColor }]}>
          <Text style={styles.avatarText}>{initials(item.customer_alias || item.customer_name)}</Text>
          <View style={[styles.channelDot, { backgroundColor: cColor }]} />
        </View>
        <View style={styles.rowBody}>
          <View style={styles.rowTop}>
            <Text style={[styles.name, unread && styles.nameUnread]} numberOfLines={1}>
              {item.customer_alias || item.customer_name || item.external_user_id}
            </Text>
            <Text style={styles.time}>{timeLabel(item.updated_at)}</Text>
          </View>
          <View style={styles.rowBottom}>
            <Text style={[styles.preview, unread && styles.previewUnread]} numberOfLines={1}>
              {item.last_direction === "out" ? "You: " : ""}
              {item.last_message || "No messages yet"}
            </Text>
            {unread ? (
              <View style={styles.badge}>
                <Text style={styles.badgeText}>{item.unread_count}</Text>
              </View>
            ) : item.ai_status === "human" ? (
              <View style={styles.humanPill}>
                <Text style={styles.humanPillText}>Human</Text>
              </View>
            ) : null}
          </View>
        </View>
      </TouchableOpacity>
    );
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <View>
          <Text style={styles.headerTitle}>Inbox</Text>
          {user ? <Text style={styles.headerSub}>{user.active_company_name}</Text> : null}
        </View>
        <TouchableOpacity style={styles.logoutBtn} onPress={logout}>
          <Text style={styles.logoutText}>Log out</Text>
        </TouchableOpacity>
      </View>

      <View style={styles.searchWrap}>
        <TextInput
          style={styles.search}
          value={search}
          onChangeText={setSearch}
          placeholder="Search conversations"
          placeholderTextColor={colors.textMuted}
          autoCapitalize="none"
        />
      </View>

      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={styles.chipsRow}
        contentContainerStyle={styles.chipsContent}
      >
        {channels.map((ch) => {
          const active = channel === ch;
          return (
            <TouchableOpacity
              key={ch}
              style={[styles.chip, active && styles.chipActive]}
              onPress={() => setChannel(ch)}
            >
              <Text style={[styles.chipText, active && styles.chipTextActive]}>
                {ch === "all" ? "All" : ch.charAt(0).toUpperCase() + ch.slice(1)}
                {ch !== "all" && channelCounts[ch] ? ` ${channelCounts[ch]}` : ""}
              </Text>
            </TouchableOpacity>
          );
        })}
      </ScrollView>

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.primary} size="large" />
        </View>
      ) : error ? (
        <View style={styles.center}>
          <Text style={styles.errorText}>{error}</Text>
          <TouchableOpacity style={styles.retryBtn} onPress={() => load()}>
            <Text style={styles.retryText}>Retry</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <FlatList
          data={filtered}
          keyExtractor={(item) => item.id}
          renderItem={renderItem}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
          contentContainerStyle={filtered.length === 0 ? styles.center : undefined}
          ListEmptyComponent={<Text style={styles.emptyText}>No conversations</Text>}
          ItemSeparatorComponent={() => <View style={styles.separator} />}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: 18,
    paddingTop: 10,
    paddingBottom: 6,
  },
  headerTitle: { fontSize: 24, fontWeight: "800", color: colors.textPrimary },
  headerSub: { fontSize: 12, color: colors.textMuted, marginTop: 1 },
  logoutBtn: {
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: radius.round,
    backgroundColor: colors.primarySoft,
  },
  logoutText: { color: colors.primaryDark, fontSize: 12, fontWeight: "700" },
  searchWrap: { paddingHorizontal: 18, paddingVertical: 8 },
  search: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.sm,
    paddingHorizontal: 14,
    paddingVertical: 10,
    fontSize: 14,
    color: colors.textPrimary,
  },
  chipsRow: { flexGrow: 0, marginBottom: 4 },
  chipsContent: { paddingHorizontal: 18, gap: 8 },
  chip: {
    paddingHorizontal: 14,
    paddingVertical: 7,
    borderRadius: radius.round,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
  },
  chipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText: { fontSize: 13, color: colors.textSecondary, fontWeight: "600" },
  chipTextActive: { color: "#fff" },
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 18,
    paddingVertical: 12,
    backgroundColor: colors.surface,
  },
  avatar: {
    width: 48,
    height: 48,
    borderRadius: 24,
    borderWidth: 2,
    backgroundColor: colors.primarySoft,
    alignItems: "center",
    justifyContent: "center",
    marginRight: 12,
  },
  avatarText: { fontSize: 16, fontWeight: "700", color: colors.primaryDark },
  channelDot: {
    position: "absolute",
    bottom: -2,
    right: -2,
    width: 14,
    height: 14,
    borderRadius: 7,
    borderWidth: 2,
    borderColor: "#fff",
  },
  rowBody: { flex: 1 },
  rowTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  name: { fontSize: 15, fontWeight: "600", color: colors.textPrimary, flex: 1, marginRight: 8 },
  nameUnread: { fontWeight: "800" },
  time: { fontSize: 11, color: colors.textMuted },
  rowBottom: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginTop: 3 },
  preview: { fontSize: 13, color: colors.textMuted, flex: 1, marginRight: 8 },
  previewUnread: { color: colors.textPrimary, fontWeight: "600" },
  badge: {
    minWidth: 20,
    height: 20,
    borderRadius: 10,
    backgroundColor: colors.primary,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 6,
  },
  badgeText: { color: "#fff", fontSize: 11, fontWeight: "700" },
  humanPill: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: radius.round,
    backgroundColor: colors.warningSoft,
  },
  humanPillText: { fontSize: 10, fontWeight: "700", color: colors.warning },
  separator: { height: 1, backgroundColor: colors.border, marginLeft: 78 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 30 },
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
