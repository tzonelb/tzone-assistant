import React, { useEffect, useState } from "react";
import { View, Text, TextInput, StyleSheet, ScrollView, Alert } from "react-native";
import { useAuth } from "../../context/AuthContext";
import { meRequest, getServerUrl, setServerUrl } from "../../api/client";
import { colors, fonts, radius } from "../../theme";
import { Kick, Btn } from "../../components/ui";

export default function MoreTab() {
  const { user, logout } = useAuth();
  const [activeCompany, setActiveCompany] = useState(null);
  const [serverUrl, setServerUrlState] = useState("");
  const [savedNote, setSavedNote] = useState("");

  useEffect(() => {
    getServerUrl().then(setServerUrlState);
    meRequest()
      .then((me) => {
        const company = (me.companies || []).find((c) => c.id === me.user?.active_company_id);
        setActiveCompany(company || null);
      })
      .catch(() => {});
  }, []);

  const saveServer = async () => {
    if (!serverUrl.trim()) return;
    try {
      await setServerUrl(serverUrl);
      setSavedNote("Saved. It applies to all requests from now on.");
      setTimeout(() => setSavedNote(""), 3000);
    } catch (e) {
      Alert.alert("Invalid server address", e.message || "Could not save this address.");
    }
  };

  const confirmLogout = () => {
    Alert.alert("Sign out", "Sign out of T-ZONE on this phone?", [
      { text: "Cancel", style: "cancel" },
      { text: "Sign out", style: "destructive", onPress: logout },
    ]);
  };

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Kick color={colors.accent700}>Workspace</Kick>
      <Text style={styles.h1}>{user?.active_company_name || "T-ZONE"}</Text>
      <Text style={styles.sub}>
        Signed in as {user?.full_name || user?.email}
        {activeCompany?.role_name ? ` · ${activeCompany.role_name}` : ""}
      </Text>

      <View style={styles.hr} />

      <Kick style={styles.groupTitle}>Account</Kick>
      <View style={styles.kv}>
        <Text style={styles.kvKey}>Email</Text>
        <Text style={styles.kvVal}>{user?.email || "—"}</Text>
      </View>
      {user?.phone ? (
        <View style={styles.kv}>
          <Text style={styles.kvKey}>Phone</Text>
          <Text style={styles.kvVal}>{user.phone}</Text>
        </View>
      ) : null}
      {activeCompany?.role_name ? (
        <View style={styles.kv}>
          <Text style={styles.kvKey}>Role</Text>
          <Text style={styles.kvVal}>{activeCompany.role_name}</Text>
        </View>
      ) : null}

      <Kick style={styles.groupTitle}>Server</Kick>
      <TextInput
        style={styles.input}
        value={serverUrl}
        onChangeText={setServerUrlState}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="url"
        placeholder="http://192.168.1.10:8000"
        placeholderTextColor={colors.neutral500}
      />
      {savedNote ? <Text style={styles.savedNote}>{savedNote}</Text> : null}
      <Btn kind="secondary" onPress={saveServer} style={styles.saveBtn}>
        Save server address
      </Btn>

      <Kick style={styles.groupTitle}>Full platform</Kick>
      <Text style={styles.note}>
        Dashboard, Tasks, Appointments, AI Teaching, Reply Flows, Broadcast, Analytics and
        administration live on the web platform. This app focuses on conversations, customers and
        publishing on the go.
      </Text>

      <View style={styles.hr} />

      <Btn kind="primary" block onPress={confirmLogout}>
        Sign out
      </Btn>

      <Text style={styles.version}>T-ZONE Mobile · v1</Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, paddingBottom: 30 },
  h1: { fontFamily: fonts.heading, fontSize: 26, color: colors.text, marginTop: 4 },
  sub: { fontFamily: fonts.body, fontSize: 12, color: colors.text55, marginTop: 2 },
  hr: { height: 1, backgroundColor: colors.divider, marginVertical: 16 },
  groupTitle: { marginTop: 14, marginBottom: 6, color: colors.text55 },
  kv: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 9,
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
  },
  kvKey: { fontFamily: fonts.body, fontSize: 13, color: colors.text55 },
  kvVal: { fontFamily: fonts.bodyMedium, fontSize: 13, color: colors.text },
  input: {
    borderWidth: 1,
    borderColor: colors.divider,
    borderRadius: radius.md,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontFamily: fonts.body,
    fontSize: 13,
    color: colors.text,
  },
  savedNote: { fontFamily: fonts.body, fontSize: 12, color: colors.accent2_700, marginTop: 6 },
  saveBtn: { marginTop: 8, alignSelf: "flex-start" },
  note: { fontFamily: fonts.body, fontSize: 12.5, lineHeight: 19, color: colors.text55 },
  version: {
    fontFamily: fonts.body,
    fontSize: 10,
    letterSpacing: 1.4,
    textTransform: "uppercase",
    color: colors.text45,
    textAlign: "center",
    marginTop: 18,
  },
});
