import React, { useEffect, useState } from "react";
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  ActivityIndicator,
} from "react-native";
import { useAuth } from "../context/AuthContext";
import { getServerUrl, setServerUrl } from "../api/client";
import { colors, radius } from "../theme";

export default function LoginScreen() {
  const { login, verify2fa } = useAuth();

  const [company, setCompany] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [serverUrl, setServerUrlState] = useState("");
  const [showServer, setShowServer] = useState(false);

  const [pendingToken, setPendingToken] = useState(null);
  const [code, setCode] = useState("");

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    getServerUrl().then(setServerUrlState);
  }, []);

  const handleLogin = async () => {
    setError("");
    if (!company.trim() || !email.trim() || !password) {
      setError("Please fill in company, email and password.");
      return;
    }
    setBusy(true);
    try {
      if (serverUrl.trim()) await setServerUrl(serverUrl);
      const res = await login({ company: company.trim(), email: email.trim(), password });
      if (res.twofaRequired) setPendingToken(res.pendingToken);
    } catch (e) {
      setError(e.message || "Login failed.");
    } finally {
      setBusy(false);
    }
  };

  const handleVerify = async () => {
    setError("");
    if (code.trim().length !== 6) {
      setError("Enter the 6-digit code from your authenticator app.");
      return;
    }
    setBusy(true);
    try {
      await verify2fa({ pendingToken, code: code.trim() });
    } catch (e) {
      setError(e.message || "Verification failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
        <View style={styles.logoWrap}>
          <View style={styles.logoBadge}>
            <Text style={styles.logoText}>T</Text>
          </View>
          <Text style={styles.title}>T-ZONE</Text>
          <Text style={styles.subtitle}>
            {pendingToken ? "Two-factor verification" : "Sign in to your workspace"}
          </Text>
        </View>

        <View style={styles.card}>
          {pendingToken ? (
            <>
              <Text style={styles.label}>Authentication code</Text>
              <TextInput
                style={[styles.input, styles.codeInput]}
                value={code}
                onChangeText={setCode}
                placeholder="123456"
                placeholderTextColor={colors.textMuted}
                keyboardType="number-pad"
                maxLength={6}
                autoFocus
              />
              {error ? <Text style={styles.error}>{error}</Text> : null}
              <TouchableOpacity style={styles.button} onPress={handleVerify} disabled={busy}>
                {busy ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <Text style={styles.buttonText}>Verify</Text>
                )}
              </TouchableOpacity>
              <TouchableOpacity
                style={styles.linkButton}
                onPress={() => {
                  setPendingToken(null);
                  setCode("");
                  setError("");
                }}
              >
                <Text style={styles.linkText}>Back to login</Text>
              </TouchableOpacity>
            </>
          ) : (
            <>
              <Text style={styles.label}>Company</Text>
              <TextInput
                style={styles.input}
                value={company}
                onChangeText={setCompany}
                placeholder="Company name or slug"
                placeholderTextColor={colors.textMuted}
                autoCapitalize="none"
                autoCorrect={false}
              />
              <Text style={styles.label}>Email</Text>
              <TextInput
                style={styles.input}
                value={email}
                onChangeText={setEmail}
                placeholder="you@company.com"
                placeholderTextColor={colors.textMuted}
                autoCapitalize="none"
                autoCorrect={false}
                keyboardType="email-address"
              />
              <Text style={styles.label}>Password</Text>
              <TextInput
                style={styles.input}
                value={password}
                onChangeText={setPassword}
                placeholder="Password"
                placeholderTextColor={colors.textMuted}
                secureTextEntry
              />
              {error ? <Text style={styles.error}>{error}</Text> : null}
              <TouchableOpacity style={styles.button} onPress={handleLogin} disabled={busy}>
                {busy ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <Text style={styles.buttonText}>Sign in</Text>
                )}
              </TouchableOpacity>

              <TouchableOpacity style={styles.linkButton} onPress={() => setShowServer((v) => !v)}>
                <Text style={styles.linkText}>{showServer ? "Hide server settings" : "Server settings"}</Text>
              </TouchableOpacity>
              {showServer ? (
                <>
                  <Text style={styles.label}>Server address</Text>
                  <TextInput
                    style={styles.input}
                    value={serverUrl}
                    onChangeText={setServerUrlState}
                    placeholder="http://192.168.1.10:8000"
                    placeholderTextColor={colors.textMuted}
                    autoCapitalize="none"
                    autoCorrect={false}
                    keyboardType="url"
                  />
                </>
              ) : null}
            </>
          )}
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  container: { flexGrow: 1, justifyContent: "center", padding: 24 },
  logoWrap: { alignItems: "center", marginBottom: 28 },
  logoBadge: {
    width: 64,
    height: 64,
    borderRadius: radius.lg,
    backgroundColor: colors.primary,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 12,
  },
  logoText: { color: "#fff", fontSize: 34, fontWeight: "800" },
  title: { fontSize: 26, fontWeight: "800", color: colors.textPrimary, letterSpacing: 1 },
  subtitle: { fontSize: 14, color: colors.textSecondary, marginTop: 4 },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    padding: 20,
    borderWidth: 1,
    borderColor: colors.border,
    shadowColor: "#1C1A42",
    shadowOpacity: 0.07,
    shadowRadius: 18,
    shadowOffset: { width: 0, height: 6 },
    elevation: 3,
  },
  label: { fontSize: 13, fontWeight: "600", color: colors.textSecondary, marginBottom: 6, marginTop: 10 },
  input: {
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.sm,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 15,
    color: colors.textPrimary,
  },
  codeInput: { textAlign: "center", fontSize: 24, letterSpacing: 8, fontWeight: "700" },
  error: { color: colors.danger, marginTop: 12, fontSize: 13 },
  button: {
    backgroundColor: colors.primary,
    borderRadius: radius.sm,
    paddingVertical: 14,
    alignItems: "center",
    marginTop: 18,
  },
  buttonText: { color: "#fff", fontSize: 16, fontWeight: "700" },
  linkButton: { alignItems: "center", marginTop: 14 },
  linkText: { color: colors.primary, fontSize: 13, fontWeight: "600" },
});
