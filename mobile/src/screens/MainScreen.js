import React, { useCallback, useState } from "react";
import { View, Text, TouchableOpacity, StyleSheet, Image } from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useAuth } from "../context/AuthContext";
import { getNotificationsSummaryRequest } from "../api/client";
import { colors, fonts } from "../theme";
import { InboxIcon, CustomersIcon, PublishIcon, MoreIcon, BellIcon } from "../components/icons";
import { Avatar } from "../components/ui";
import InboxTab from "./tabs/InboxTab";
import CustomersTab from "./tabs/CustomersTab";
import PublishTab from "./tabs/PublishTab";
import MoreTab from "./tabs/MoreTab";

const TABS = [
  { key: "inbox", label: "Inbox", Icon: InboxIcon },
  { key: "customers", label: "Customers", Icon: CustomersIcon },
  { key: "publish", label: "Publish", Icon: PublishIcon },
  { key: "more", label: "More", Icon: MoreIcon },
];

export default function MainScreen({ navigation }) {
  const { user } = useAuth();
  const insets = useSafeAreaInsets();
  const [tab, setTab] = useState("inbox");
  const [unread, setUnread] = useState(0);
  const [inboxCount, setInboxCount] = useState(null);

  useFocusEffect(
    useCallback(() => {
      getNotificationsSummaryRequest()
        .then((s) => setUnread(s.unread || 0))
        .catch(() => {});
    }, [])
  );

  const initial = (user?.full_name || user?.email || "?").trim().charAt(0).toUpperCase();

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <Image source={require("../../assets/tzone-logo.png")} style={styles.logo} resizeMode="contain" />
        <Text style={styles.headerKick} numberOfLines={1}>
          {user?.active_company_name || ""}
        </Text>
        <View style={styles.live}>
          <View style={styles.liveDot} />
          <Text style={styles.liveText}>Live</Text>
        </View>
        <View style={styles.bellWrap}>
          <BellIcon size={17} color={colors.text} />
          {unread > 0 ? (
            <View style={styles.bellBadge}>
              <Text style={styles.bellBadgeText}>{unread > 99 ? "99+" : unread}</Text>
            </View>
          ) : null}
        </View>
        <Avatar initial={initial} size={28} accent />
      </View>

      <View style={styles.body}>
        {tab === "inbox" ? (
          <InboxTab navigation={navigation} onCount={setInboxCount} />
        ) : tab === "customers" ? (
          <CustomersTab navigation={navigation} />
        ) : tab === "publish" ? (
          <PublishTab />
        ) : (
          <MoreTab />
        )}
      </View>

      <View style={[styles.nav, { paddingBottom: Math.max(insets.bottom, 12) }]}>
        {TABS.map(({ key, label, Icon }) => {
          const on = tab === key;
          const tint = on ? colors.accent700 : colors.text55;
          const badge = key === "inbox" && inboxCount ? String(inboxCount) : null;
          return (
            <TouchableOpacity key={key} style={styles.navBtn} onPress={() => setTab(key)}>
              {on ? <View style={styles.navMark} /> : null}
              <Icon size={17} color={tint} />
              <Text style={[styles.navLabel, { color: tint }]}>{label}</Text>
              {badge ? (
                <View style={styles.navBadge}>
                  <Text style={styles.navBadgeText}>{badge}</Text>
                </View>
              ) : null}
            </TouchableOpacity>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingHorizontal: 16,
    paddingTop: 12,
    paddingBottom: 10,
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
  },
  logo: { height: 20, width: 92 },
  headerKick: {
    fontFamily: fonts.body,
    fontSize: 10,
    letterSpacing: 1.4,
    textTransform: "uppercase",
    color: colors.accent700,
    borderLeftWidth: 1,
    borderLeftColor: colors.divider,
    paddingLeft: 9,
    flexShrink: 1,
  },
  live: { flexDirection: "row", alignItems: "center", gap: 6, marginLeft: "auto" },
  liveDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: colors.accent2_500 },
  liveText: { fontFamily: fonts.body, fontSize: 11.5, color: colors.text65 },
  bellWrap: { padding: 4 },
  bellBadge: {
    position: "absolute",
    top: -1,
    right: -3,
    minWidth: 14,
    paddingHorizontal: 3,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: colors.bg,
    backgroundColor: colors.accent700,
  },
  bellBadgeText: {
    color: colors.neutral100,
    fontSize: 9,
    lineHeight: 13,
    textAlign: "center",
    fontFamily: fonts.body,
  },
  body: { flex: 1, minHeight: 0 },
  nav: {
    flexDirection: "row",
    borderTopWidth: 1,
    borderTopColor: colors.divider,
    backgroundColor: colors.bg,
  },
  navBtn: {
    flex: 1,
    alignItems: "center",
    gap: 4,
    paddingTop: 11,
  },
  navMark: {
    position: "absolute",
    top: -1,
    left: "20%",
    right: "20%",
    borderTopWidth: 2,
    borderTopColor: colors.accent,
  },
  navLabel: { fontFamily: fonts.body, fontSize: 11, letterSpacing: 0.55 },
  navBadge: {
    position: "absolute",
    top: 5,
    left: "50%",
    marginLeft: 6,
    minWidth: 15,
    paddingHorizontal: 3,
    borderRadius: 999,
    backgroundColor: colors.accent700,
  },
  navBadgeText: { color: "#fff", fontSize: 9, lineHeight: 14, textAlign: "center", fontFamily: fonts.body },
});
