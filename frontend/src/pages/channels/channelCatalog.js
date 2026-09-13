// The full directory of channel types a business messaging platform is
// expected to support (benchmarked against respond.io breadth). Each entry
// declares how it connects and whether it is live today. "available" means a
// customer can genuinely connect it now through T-ZONE — the backend has a
// real routing field, webhook and sender for it (see
// backend/services/channel_account_service.py:SUPPORTED_CHANNELS, which
// tests/test_channel_catalogue.py keeps this list honest against). "soon"
// means the integration is planned but not wired yet — shown plainly, never
// as a fake-connectable button; that shortcut is exactly what got the
// previous version of this catalogue deleted as "the connect buttons for
// everything past the first four just return 'not built here'".
//
// `icon` names a real brand mark resolved in channelIcons.js (MUI's own
// brand icons, or the genuine Simple Icons glyph via react-icons for brands
// MUI doesn't ship). No hand-drawn or generic stand-ins for a real company.
//
// key: matches the `channel` value stored in channel_accounts where one exists.

export const CHANNEL_CATEGORIES = [
  {
    title: "Messaging",
    channels: [
      { key: "whatsapp", name: "WhatsApp", availability: "available", icon: "WhatsApp", color: "#25D366",
        note: "Official Meta Cloud API." },
      { key: "messenger", name: "Facebook Messenger", availability: "available", icon: "Facebook", color: "#1877F2",
        note: "One-click via Facebook login." },
      { key: "instagram", name: "Instagram", availability: "available", icon: "Instagram", color: "#E1306C",
        note: "Connected together with your Facebook Page." },
      { key: "telegram", name: "Telegram", availability: "available", icon: "Telegram", color: "#229ED9",
        note: "Paste your bot token from @BotFather." },
      { key: "whatsapp_qr", name: "WhatsApp (QR scan)", availability: "soon", icon: "WhatsApp", color: "#25D366",
        note: "Scan a QR code from your phone's Linked Devices — no Meta developer account needed." },
      { key: "instagram_direct", name: "Instagram (direct login)", availability: "soon", icon: "Instagram", color: "#E1306C",
        note: "Log in with your Instagram username and password — no Meta developer account." },
      { key: "facebook_direct", name: "Facebook (cookie download)", availability: "soon", icon: "Facebook", color: "#1877F2",
        note: "Read your Page's posts and comments without a Meta developer account." },
      { key: "viber", name: "Viber", availability: "soon", icon: "Viber", color: "#7360F2" },
      { key: "line", name: "LINE", availability: "soon", icon: "Line", color: "#06C755" },
      { key: "wechat", name: "WeChat", availability: "soon", icon: "WeChat", color: "#07C160" },
      { key: "signal", name: "Signal", availability: "soon", icon: "Signal", color: "#3A76F0" },
    ],
  },
  {
    title: "Social",
    channels: [
      { key: "tiktok", name: "TikTok", availability: "soon", icon: "TikTok", color: "#010101" },
      { key: "twitter", name: "X (Twitter)", availability: "soon", icon: "X", color: "#000000" },
      { key: "linkedin", name: "LinkedIn", availability: "soon", icon: "LinkedIn", color: "#0A66C2" },
      { key: "youtube", name: "YouTube", availability: "soon", icon: "YouTube", color: "#FF0000" },
    ],
  },
  {
    title: "Business & web",
    channels: [
      { key: "webchat", name: "Website live chat", availability: "soon", icon: "Language", color: "#0EA5A5",
        note: "An embeddable chat widget for your website." },
      { key: "email", name: "Email", availability: "soon", icon: "Email", color: "#EA4335",
        note: "Turn support emails into conversations." },
      { key: "sms", name: "SMS", availability: "soon", icon: "Sms", color: "#6B7280" },
      { key: "google_business", name: "Google Business Messages", availability: "soon", icon: "Google", color: "#4285F4" },
      { key: "apple_business", name: "Apple Messages for Business", availability: "soon", icon: "Apple", color: "#111827" },
    ],
  },
  {
    title: "Team & collaboration",
    channels: [
      { key: "slack", name: "Slack", availability: "soon", icon: "Slack", color: "#4A154B" },
      { key: "discord", name: "Discord", availability: "soon", icon: "Discord", color: "#5865F2" },
      { key: "google_chat", name: "Google Chat", availability: "soon", icon: "Google", color: "#34A853" },
    ],
  },
];

// Flat map for quick lookups by the stored channel key.
export const CHANNEL_LABELS = CHANNEL_CATEGORIES.reduce((map, category) => {
  category.channels.forEach((channel) => {
    map[channel.key] = channel.name;
  });
  return map;
}, {});
