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
  Image,
} from "react-native";
import { useAuth } from "../context/AuthContext";
import { getServerUrl, setServerUrl } from "../api/client";
import { colors, fonts, radius } from "../theme";
import { Kick, Btn } from "../components/ui";

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
    <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
        <View style={styles.logoWrap}>
          <Image source={require("../../assets/tzone-logo.png")} style={styles.logo} resizeMode="contain" />
          <Kick color={colors.accent700} style={styles.subtitle}>
            {pendingToken ? "Two-factor verification" : "Sign in to your workspace"}
          </Kick>
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
                placeholderTextColor={colors.neutral500}
                keyboardType="number-pad"
                maxLength={6}
                autoFocus
              />
              {error ? <Text style={styles.error}>{error}</Text> : null}
              <Btn kind="primary" block onPress={handleVerify} disabled={busy} style={styles.submit}>
                {busy ? <ActivityIndicator color={colors.accent} /> : "Verify"}
              </Btn>
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
                placeholderTextColor={colors.neutral500}
                autoCapitalize="none"
                autoCorrect={false}
              />
              <Text style={styles.label}>Email</Text>
              <TextInput
                style={styles.input}
                value={email}
                onChangeText={setEmail}
                placeholder="you@company.com"
                placeholderTextColor={colors.neutral500}
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
                placeholderTextColor={colors.neutral500}
                secureTextEntry
              />
              {error ? <Text style={styles.error}>{error}</Text> : null}
              <Btn kind="primary" block onPress={handleLogin} disabled={busy} style={styles.submit}>
                {busy ? <ActivityIndicator color={colors.accent} /> : "Sign in"}
              </Btn>

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
                    placeholderTextColor={colors.neutral500}
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
  flex: { flex: 1, backgroundColor: colors.bg },
  container: { flexGrow: 1, justifyContent: "center", padding: 24 },
  logoWrap: { alignItems: "center", marginBottom: 26 },
  logo: { height: 40, width: 200 },
  subtitle: { marginTop: 10 },
  card: {
    borderWidth: 1,
    borderColor: colors.divider,
    borderRadius: radius.lg,
    padding: 20,
    backgroundColor: "transparent",
  },
  label: {
    fontFamily: fonts.bodySemi,
    fontSize: 12,
    color: colors.text65,
    marginBottom: 6,
    marginTop: 12,
  },
  input: {
    borderWidth: 1,
    borderColor: colors.divider,
    borderRadius: radius.md,
    paddingHorizontal: 12,
    paddingVertical: 11,
    fontFamily: fonts.body,
    fontSize: 14,
    color: colors.text,
    backgroundColor: "transparent",
  },
  codeInput: {
    textAlign: "center",
    fontSize: 24,
    letterSpacing: 8,
    fontFamily: fonts.bodySemi,
  },
  error: { color: "#b3372f", marginTop: 12, fontSize: 13, fontFamily: fonts.body },
  submit: { marginTop: 20 },
  linkButton: { alignItems: "center", marginTop: 14 },
  linkText: { color: colors.accent700, fontSize: 13, fontFamily: fonts.bodyMedium },
});
