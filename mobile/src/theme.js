// T-ZONE Classical theme — mirrors frontend/src/styles/classical-styles.css +
// tzone-theme.css and the approved "T-ZONE Mobile" mockup. Values are final —
// do not invent new colors; add here only if the design system gains a token.
export const colors = {
  bg: "#f4f5f7",
  surface: "#e9ebef",
  text: "#18202a",
  accent: "#1b9be0",
  accent2: "#3fb552",
  divider: "rgba(24,32,42,0.15)",

  neutral100: "#f6f7f9",
  neutral200: "#e8eaee",
  neutral300: "#d5d9df",
  neutral400: "#b8bec8",
  neutral500: "#99a1ad",
  neutral600: "#7b8492",
  neutral700: "#5f6875",
  neutral800: "#434b56",
  neutral900: "#2b323b",

  accent100: "#e8f6fe",
  accent200: "#c7e9fc",
  accent700: "#0b6c9f",
  accent800: "#08506f",

  accent2_100: "#e9f9eb",
  accent2_500: "#3fb552",
  accent2_700: "#1d7830",
  accent2_800: "#145826",

  // color-mix(text 60%) equivalents used all over the mockup
  text65: "rgba(24,32,42,0.65)",
  text55: "rgba(24,32,42,0.55)",
  text45: "rgba(24,32,42,0.45)",

  // Message bubble backgrounds
  bubbleOut: "rgba(27,155,224,0.07)", // accent 7%
  aiBannerBg: "rgba(63,181,82,0.08)", // accent-2 8%
  humanBannerBg: "rgba(27,155,224,0.07)", // accent 7%
  scrim: "rgba(15,23,32,0.35)",
};

// Channel tint squares (from the mockup's thread rows)
export const channelColors = {
  whatsapp: "#3fb552",
  messenger: "#1b9be0",
  instagram: "#b06ab3",
  telegram: "#2ca7e6",
  website: "#7b8492",
};

export const radius = {
  sm: 2,
  md: 4,
  lg: 7,
  round: 999,
};

export const fonts = {
  heading: "CormorantGaramond_400Regular",
  headingSemi: "CormorantGaramond_600SemiBold",
  body: "Lora_400Regular",
  bodyMedium: "Lora_500Medium",
  bodySemi: "Lora_600SemiBold",
};

// The .tz-kick style: 10px, letterspaced, uppercase
export const kick = {
  fontFamily: fonts.body,
  fontSize: 10,
  letterSpacing: 1.4,
  textTransform: "uppercase",
};
