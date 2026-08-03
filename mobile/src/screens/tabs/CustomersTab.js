import React, { useCallback, useEffect, useState } from "react";
import {
  View,
  Text,
  TextInput,
  FlatList,
  TouchableOpacity,
  StyleSheet,
  RefreshControl,
  ActivityIndicator,
} from "react-native";
import { listCustomersRequest } from "../../api/client";
import { colors, fonts, radius } from "../../theme";
import { Kick, Tag, Avatar } from "../../components/ui";
import { SearchIcon } from "../../components/icons";

const STAGE_LABELS = {
  lead: "Lead",
  active: "Active",
  customer: "Customer",
  vip: "VIP",
  churned: "Churned",
};

export default function CustomersTab({ navigation }) {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [stage, setStage] = useState("all");

  const load = useCallback(async (q = "") => {
    try {
      const res = await listCustomersRequest({ search: q, limit: 200 });
      setItems(res.items || []);
      setTotal(res.total || 0);
      setError("");
    } catch (e) {
      setError(e.status === 403 ? "You don't have access to customers." : e.message || "Failed to load customers.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Debounced server-side search
  useEffect(() => {
    const t = setTimeout(() => load(search.trim()), 350);
    return () => clearTimeout(t);
  }, [search, load]);

  const stages = ["all", ...Object.keys(STAGE_LABELS).filter((s) => items.some((c) => c.lifecycle_stage === s))];
  const visible = stage === "all" ? items : items.filter((c) => c.lifecycle_stage === stage);

  const renderItem = ({ item }) => {
    const name = item.display_name || item.full_name || item.internal_name || item.phone || `#${item.id}`;
    const channels = (item.channels || []).map((c) => c.slice(0, 2).toUpperCase()).join(" · ");
    return (
      <TouchableOpacity
        style={styles.row}
        onPress={() => navigation.navigate("Customer", { customerId: item.id, name })}
      >
        <Avatar initial={name.charAt(0).toUpperCase()} size={36} />
        <View style={styles.rowBody}>
          <Text style={styles.name} numberOfLines={1}>
            {name}
          </Text>
          <Text style={styles.sub} numberOfLines={1}>
            {[item.phone, channels].filter(Boolean).join(" · ") || item.email || "—"}
          </Text>
        </View>
        <View style={styles.right}>
          <Text style={styles.fig}>{item.conversation_count ?? 0}</Text>
          <Kick style={styles.figKick}>chats</Kick>
          <Tag style={styles.stageTag}>{STAGE_LABELS[item.lifecycle_stage] || item.lifecycle_stage || "—"}</Tag>
        </View>
      </TouchableOpacity>
    );
  };

  return (
    <View style={styles.container}>
      <View style={styles.head}>
        <Kick color={colors.accent700}>Register · {total.toLocaleString()} records</Kick>
        <Text style={styles.h1}>Customers</Text>
        <View style={styles.searchBox}>
          <SearchIcon size={13} color={colors.text55} />
          <TextInput
            style={styles.searchInput}
            value={search}
            onChangeText={setSearch}
            placeholder="Search contacts…"
            placeholderTextColor={colors.neutral500}
            autoCapitalize="none"
          />
        </View>
        <View style={styles.tags}>
          {stages.map((s) => (
            <Tag key={s} active={stage === s} onPress={() => setStage(s)}>
              {s === "all" ? "All" : STAGE_LABELS[s]}
            </Tag>
          ))}
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
      ) : (
        <FlatList
          data={visible}
          keyExtractor={(item) => String(item.id)}
          renderItem={renderItem}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => {
                setRefreshing(true);
                load(search.trim());
              }}
            />
          }
          contentContainerStyle={visible.length === 0 ? styles.center : undefined}
          ListEmptyComponent={<Text style={styles.emptyText}>No customers</Text>}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  head: { paddingHorizontal: 16, paddingTop: 16, paddingBottom: 8 },
  h1: { fontFamily: fonts.heading, fontSize: 28, color: colors.text, marginTop: 4, marginBottom: 10 },
  searchBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: 7,
    paddingHorizontal: 10,
    borderWidth: 1,
    borderColor: colors.divider,
    borderRadius: radius.md,
  },
  searchInput: { flex: 1, paddingVertical: 8, fontFamily: fonts.body, fontSize: 13, color: colors.text },
  tags: { flexDirection: "row", gap: 6, marginTop: 10, flexWrap: "wrap" },
  row: {
    flexDirection: "row",
    gap: 11,
    alignItems: "center",
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
  },
  rowBody: { flex: 1, minWidth: 0 },
  name: { fontFamily: fonts.headingSemi, fontSize: 16, color: colors.text },
  sub: { marginTop: 2, fontFamily: fonts.body, fontSize: 11.5, color: colors.text55 },
  right: { alignItems: "flex-end" },
  fig: { fontFamily: fonts.heading, fontSize: 17, color: colors.text },
  figKick: { opacity: 0.5, marginTop: -2 },
  stageTag: { marginTop: 3, paddingHorizontal: 8, paddingVertical: 2 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 30 },
  emptyText: { color: colors.text45, fontSize: 14, fontFamily: fonts.body },
  errorText: { color: "#b3372f", fontSize: 14, textAlign: "center", fontFamily: fonts.body },
});
