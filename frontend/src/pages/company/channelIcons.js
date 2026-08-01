import {
  Apple, ChatBubble, Email, Facebook, Forum, Google, Instagram,
  Language, LinkedIn, MusicNote, Sms, Tag, Telegram, Twitter, WhatsApp, YouTube,
} from "@mui/icons-material";

const ICONS = {
  WhatsApp, Facebook, Instagram, Telegram, ChatBubble, MusicNote,
  Twitter, LinkedIn, YouTube, Language, Email, Sms, Google, Apple, Tag, Forum,
};

export function resolveChannelIcon(name) {
  return ICONS[name] || ChatBubble;
}
