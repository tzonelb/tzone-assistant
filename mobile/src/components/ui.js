import React from "react";
import { View, Text, TouchableOpacity, StyleSheet, Modal, Pressable } from "react-native";
import { colors, fonts, radius, kick } from "../theme";

// .tz-kick — 10px letterspaced uppercase label
export function Kick({ children, color, style, ...rest }) {
  return (
    <Text style={[styles.kick, color ? { color } : null, style]} {...rest}>
      {children}
    </Text>
  );
}

// .tag — outlined / neutral pills
export function Tag({ children, active, onPress, style }) {
  const inner = (
    <View style={[styles.tag, active ? styles.tagActive : null, style]}>
      <Text style={[styles.tagText, active ? styles.tagTextActive : null]}>{children}</Text>
    </View>
  );
  if (!onPress) return inner;
  return <TouchableOpacity onPress={onPress}>{inner}</TouchableOpacity>;
}

// .btn — Classical buttons are OUTLINED, never filled.
export function Btn({ children, kind = "primary", onPress, disabled, block, small, style }) {
  return (
    <TouchableOpacity
      onPress={onPress}
      disabled={disabled}
      style={[
        styles.btn,
        kind === "primary" && styles.btnPrimary,
        kind === "secondary" && styles.btnSecondary,
        kind === "ghost" && styles.btnGhost,
        block && styles.btnBlock,
        small && styles.btnSmall,
        disabled && styles.btnDisabled,
        style,
      ]}
    >
      {typeof children === "string" ? (
        <Text
          style={[
            styles.btnText,
            kind === "primary" && styles.btnTextPrimary,
            small && styles.btnTextSmall,
          ]}
        >
          {children}
        </Text>
      ) : (
        children
      )}
    </TouchableOpacity>
  );
}

// Round outlined avatar with serif initial (mockup's 36px circles)
export function Avatar({ initial, size = 36, accent, style }) {
  return (
    <View
      style={[
        styles.avatar,
        {
          width: size,
          height: size,
          borderRadius: size / 2,
          borderColor: accent ? colors.accent : colors.divider,
        },
        style,
      ]}
    >
      <Text
        style={[
          styles.avatarText,
          { fontSize: size * 0.4, color: accent ? colors.accent700 : colors.text },
        ]}
      >
        {initial || "?"}
      </Text>
    </View>
  );
}

// Bottom sheet (mockup: scrim + slide-up panel, kick title, ✕ close)
export function Sheet({ visible, title, onClose, children }) {
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.scrim} onPress={onClose} />
      <View style={styles.sheet}>
        <View style={styles.sheetHead}>
          <Kick color={colors.accent700} style={{ flex: 1 }}>
            {title}
          </Kick>
          <TouchableOpacity onPress={onClose} style={styles.sheetClose}>
            <Text style={styles.sheetCloseText}>✕</Text>
          </TouchableOpacity>
        </View>
        <View style={styles.sheetBody}>{children}</View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  kick: { ...kick, color: colors.text },
  tag: {
    paddingHorizontal: 14,
    paddingVertical: 6,
    borderRadius: radius.round,
    borderWidth: 1,
    borderColor: colors.divider,
  },
  tagActive: { borderColor: colors.accent },
  tagText: { fontFamily: fonts.body, fontSize: 12.5, color: colors.text },
  tagTextActive: { color: colors.accent700 },
  btn: {
    borderWidth: 1,
    borderRadius: radius.md,
    paddingHorizontal: 14,
    paddingVertical: 9,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "transparent",
  },
  btnPrimary: { borderColor: colors.accent },
  btnSecondary: { borderColor: colors.divider },
  btnGhost: { borderColor: "transparent" },
  btnBlock: { alignSelf: "stretch" },
  btnSmall: { paddingHorizontal: 10, paddingVertical: 5 },
  btnDisabled: { opacity: 0.4 },
  btnText: { fontFamily: fonts.body, fontSize: 14, color: colors.text },
  btnTextPrimary: { color: colors.accent },
  btnTextSmall: { fontSize: 12 },
  avatar: {
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  avatarText: { fontFamily: fonts.headingSemi },
  scrim: { flex: 1, backgroundColor: colors.scrim },
  sheet: {
    backgroundColor: colors.bg,
    borderTopWidth: 1,
    borderTopColor: colors.divider,
    borderTopLeftRadius: radius.lg,
    borderTopRightRadius: radius.lg,
    maxHeight: "70%",
    paddingBottom: 24,
  },
  sheetHead: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 16,
    paddingTop: 12,
    paddingBottom: 8,
  },
  sheetClose: { paddingHorizontal: 7, paddingVertical: 2 },
  sheetCloseText: { fontSize: 15, color: colors.text },
  sheetBody: { paddingHorizontal: 16 },
});
