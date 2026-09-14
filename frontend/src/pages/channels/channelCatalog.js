// The full directory of channel types a business messaging platform is
// expected to support (benchmarked against respond.io breadth). Each entry
// declares how it connects and whether it is live today. "available" means a
// customer can genuinely connect it now through T-ZONE — the backend has a
// real routing field, webhook and sender for it (see
// backend/services/channel_account_service.py:SUPPORTED_CHANNELS, which
// tests/test_channel_catalogue.py keeps this list honest against). "soon"
// means the integration is planned and technically buildable, just not wired
// yet — shown plainly, never as a fake-connectable button; that shortcut is
// exactly what got the previous version of this catalogue deleted as "the
// connect buttons for everything past the first four just return 'not built
// here'".
//
// "unavailable" is the fourth, more honest, answer for a channel this
// engineering team cannot build no matter how much time is spent on it — not
// "not built here yet", but "cannot be reached from here at all". Each one
// carries a `note` with the specific, evidence-based reason (a shut-down
// product, a closed partner program, a design with deliberately no API), so
// a company doesn't read the same "coming soon" a real in-progress channel
// gets and wait for something that is never coming. Verified by web research
// at the time each note was written; if a platform reopens a program or
// relaunches a product, the note is what needs revisiting, not the label.
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
      { key: "whatsapp_qr", name: "WhatsApp (QR scan)", availability: "unavailable", icon: "WhatsApp", color: "#25D366",
        note: "Built and researched, then cancelled: WhatsApp's own detection bans accounts using unofficial clients like this even with correct, low-volume, reply-only usage. The engineers behind the underlying library confirmed there is no fix for it, and a ban has no reliable appeal path. This isn't a risk your business could manage carefully — it's a channel that cannot stay connected." },
      { key: "instagram_direct", name: "Instagram (direct login)", availability: "available", icon: "Instagram", color: "#E1306C",
        note: "Log in with your Instagram username and password — no Meta developer account. Uses Instagram's own risk model, not Meta's Graph API; a residential/mobile proxy is strongly recommended." },
      { key: "facebook_direct", name: "Facebook (cookie download)", availability: "soon", icon: "Facebook", color: "#1877F2",
        note: "Read your Page's posts and comments without a Meta developer account." },
      { key: "viber", name: "Viber", availability: "available", icon: "Viber", color: "#7360F2",
        note: "Paste your bot's Authentication Token from the Viber Admin Panel." },
      { key: "line", name: "LINE", availability: "available", icon: "Line", color: "#06C755",
        note: "Paste your channel's Access Token and Channel Secret." },
      { key: "wechat", name: "WeChat", availability: "soon", icon: "WeChat", color: "#07C160" },
      { key: "signal", name: "Signal", availability: "unavailable", icon: "Signal", color: "#3A76F0",
        note: "Signal has no public API for a business to send or receive through, by deliberate design — its entire model is built around not exposing one. There is no credential this screen could ever ask for." },
    ],
  },
  {
    title: "Social",
    channels: [
      { key: "tiktok", name: "TikTok", availability: "soon", icon: "TikTok", color: "#010101" },
      { key: "twitter", name: "X (Twitter)", availability: "soon", icon: "X", color: "#000000" },
      { key: "linkedin", name: "LinkedIn", availability: "unavailable", icon: "LinkedIn", color: "#0A66C2",
        note: "LinkedIn's Messaging API partner program is closed to new applicants — no application form, no waitlist, no published date to reopen. Nothing to connect until LinkedIn itself changes that." },
      { key: "youtube", name: "YouTube", availability: "soon", icon: "YouTube", color: "#FF0000" },
    ],
  },
  {
    title: "Business & web",
    channels: [
      { key: "webchat", name: "Website live chat", availability: "available", icon: "Language", color: "#0EA5A5",
        note: "An embeddable chat widget for your website — no account needed." },
      { key: "email", name: "Email", availability: "available", icon: "Email", color: "#EA4335",
        note: "Connect a support mailbox over IMAP/SMTP." },
      { key: "sms", name: "SMS", availability: "available", icon: "Sms", color: "#6B7280",
        note: "Paste your Twilio Account SID and Auth Token, plus the phone number customers text." },
      { key: "google_business", name: "Google Business Messages", availability: "unavailable", icon: "Google", color: "#4285F4",
        note: "Google shut this product down permanently on July 31, 2024. Its API now returns an error for every request, for every business — this isn't a delay, the product no longer exists." },
      { key: "apple_business", name: "Apple Messages for Business", availability: "unavailable", icon: "Apple", color: "#111827",
        note: "Requires T-ZONE itself — not your business — to become an Apple-approved Messaging Service Provider: a registration, a sponsoring executive, a live demo review with Apple's own team. No customer credential can unlock this; it's a business relationship this platform doesn't hold." },
    ],
  },
  {
    title: "Team & collaboration",
    channels: [
      { key: "slack", name: "Slack", availability: "available", icon: "Slack", color: "#4A154B",
        note: "Paste your app's Bot User OAuth Token and Signing Secret." },
      { key: "discord", name: "Discord", availability: "available", icon: "Discord", color: "#5865F2",
        note: "Paste your bot's token. Direct messages only, not server channels." },
      { key: "google_chat", name: "Google Chat", availability: "available", icon: "Google", color: "#34A853",
        note: "Paste your Chat app's service account JSON key, then point Google at the URL you're shown." },
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
