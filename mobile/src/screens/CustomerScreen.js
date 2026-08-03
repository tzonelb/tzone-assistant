import React, { useCallback, useEffect, useState } from "react";
import {
  View,
  Text,
  ScrollView,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { getCustomerRequest, getCustomerTimelineRequest } from "../api/client";
import { colors, fonts, radius } from "../theme";
import { Kick, Tag, Btn, Avatar } from "../components/ui";
import { BackIcon } from "../components/icons";

const STAGE_LABELS = {
  lead: "Lead",
  active: "Active",
  customer: "Customer",
  vip: "VIP",
  churned: "Churned",
};

function eventText(e) {
  if (e.type === "conversation_started") {
    return `${e.channel} conversation started${e.department && e.department !== "Unassigned" ? ` · ${e.department}` : ""}${
      e.handled_by_name ? ` · ${e.handled_by_name}` : ""
    }`;
  }
  if (e.type === "profile_updated") {
    const fields = Object.keys(e.changes || {}).join(", ");
    return `Profile updated${fields ? ` (${fields})` : ""}${e.actor_name ? ` by ${e.actor_name}` : ""}`;
  }
  return e.type || "Event";
}

function eventDate(iso) {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString([], { day: "2-digit", month: "short", year: "numeric" });
}

export default function CustomerScreen({ route, navigation }) {
  const { customerId } = route.params;
  const insets = useSafeAreaInsets();
  const [customer, setCustomer] = useState(null);
  const [timeline, setTimeline] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [c, t] = await Promise.all([
        getCustomerRequest(customerId),
        getCustomerTimelineRequest(customerId).catch(() => ({ items: [] })),
      ]);
      setCustomer(c);
      setTimeline(t.items || []);
      setError("");
    } catch (e) {
      setError(e.message || "Failed to load customer.");
    } finally {
      setLoading(false);
    }
  }, [customerId]);

  useEffect(() => {
    load();
  }, [load]);

  const name =
    customer?.display_name || customer?.full_name || customer?.internal_name || customer?.phone || "Customer";

  const openConversation = () => {
    const identity = (customer?.identities || [])[0];
    if (!identity) return;
    navigation.navigate("Chat", {
      channel: identity.channel,
      userId: identity.external_user_id,
      name,
    });
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => navigation.goBack()} style={styles.backBtn}>
          <BackIcon size={18} color={colors.text} />
        </TouchableOpacity>
        <Kick color={colors.accent700}>Customer profile</Kick>
        <Kick style={styles.headerId}>#C-{customerId}</Kick>
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
        <ScrollView contentContainerStyle={styles.body}>
          <View style={styles.identityRow}>
            <Avatar initial={name.charAt(0).toUpperCase()} size={48} accent />
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={styles.h2}>{name}</Text>
              <Text style={styles.sub}>
                {[customer?.phone, (customer?.channels || []).map((c) => c.slice(0, 2).toUpperCase()).join(" · ")]
                  .filter(Boolean)
                  .join(" · ") || customer?.email || "—"}
              </Text>
            </View>
          </View>

          <View style={styles.statGrid}>
            <View style={styles.statCell}>
              <Kick style={styles.statKick}>Conversations</Kick>
              <Text style={styles.statFig}>{customer?.conversation_count ?? 0}</Text>
            </View>
            <View style={styles.statCell}>
              <Kick style={styles.statKick}>Stage</Kick>
              <Text style={styles.statVal}>
                {STAGE_LABELS[customer?.lifecycle_stage] || customer?.lifecycle_stage || "—"}
              </Text>
            </View>
            <View style={styles.statCell}>
              <Kick style={styles.statKick}>Owner</Kick>
              <Text style={styles.statVal}>{customer?.assigned_user_name || "Unassigned"}</Text>
            </View>
            <View style={styles.statCell}>
              <Kick style={styles.statKick}>Last seen</Kick>
              <Text style={styles.statVal}>{eventDate(customer?.last_seen_at) || "—"}</Text>
            </View>
          </View>

          {(customer?.tags || []).length ? (
            <View style={styles.tagRow}>
              {customer.tags.map((t) => (
                <Tag key={t}>{t}</Tag>
              ))}
            </View>
          ) : null}

          <Kick color={colors.accent700} style={styles.sectionTitle}>
            Timeline
          </Kick>
          {timeline.length === 0 ? (
            <Text style={styles.emptyText}>No activity yet</Text>
          ) : (
            timeline.map((e, i) => (
              <View key={i} style={styles.timelineRow}>
                <View style={styles.timelineRail}>
                  <View style={styles.timelineDot} />
                  {i < timeline.length - 1 ? <View style={styles.timelineLine} /> : null}
                </View>
                <View style={{ flex: 1, minWidth: 0, paddingBottom: 10 }}>
                  <Text style={styles.timelineText}>{eventText(e)}</Text>
                  <Kick style={styles.timelineMeta}>{eventDate(e.created_at)}</Kick>
                </View>
              </View>
            ))
          )}

          {(customer?.identities || []).length ? (
            <Btn kind="primary" block onPress={openConversation} style={styles.cta}>
              Open conversation
            </Btn>
          ) : null}
        </ScrollView>
      )}
    </View>
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
  headerId: { marginLeft: "auto", opacity: 0.5 },
  body: { padding: 16, paddingBottom: 30 },
  identityRow: { flexDirection: "row", alignItems: "center", gap: 12 },
  h2: { fontFamily: fonts.heading, fontSize: 24, color: colors.text },
  sub: { fontFamily: fonts.body, fontSize: 12, color: colors.text55, marginTop: 2 },
  statGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    marginTop: 14,
    borderWidth: 1,
    borderColor: colors.divider,
    borderRadius: radius.md,
    overflow: "hidden",
  },
  statCell: {
    width: "50%",
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderTopWidth: 1,
    borderLeftWidth: 1,
    borderColor: colors.divider,
    marginTop: -1,
    marginLeft: -1,
  },
  statKick: { opacity: 0.55 },
  statFig: { fontFamily: fonts.heading, fontSize: 24, color: colors.text },
  statVal: { fontFamily: fonts.body, fontSize: 14, color: colors.text, marginTop: 4 },
  tagRow: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 12 },
  sectionTitle: { marginTop: 16, marginBottom: 6 },
  timelineRow: { flexDirection: "row", gap: 9 },
  timelineRail: { alignItems: "center", paddingTop: 4, width: 9 },
  timelineDot: {
    width: 7,
    height: 7,
    borderRadius: 4,
    borderWidth: 1,
    borderColor: colors.accent,
  },
  timelineLine: { flex: 1, width: 1, backgroundColor: colors.divider, marginTop: 3 },
  timelineText: { fontFamily: fonts.body, fontSize: 12.5, lineHeight: 19, color: colors.text },
  timelineMeta: { opacity: 0.45, marginTop: 1 },
  cta: { marginTop: 16 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 30 },
  emptyText: { color: colors.text45, fontSize: 13, fontFamily: fonts.body },
  errorText: { color: "#b3372f", fontSize: 14, textAlign: "center", fontFamily: fonts.body },
});
