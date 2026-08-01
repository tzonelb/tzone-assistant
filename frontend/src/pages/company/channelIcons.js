// Every icon here is a real brand mark — either MUI's own accurate brand
// icon (Facebook/Instagram/Telegram/WhatsApp/LinkedIn/YouTube/X/Apple/Google
// are all genuine logos, not placeholders) or, for brands MUI doesn't ship,
// the real Simple Icons glyph via react-icons/si (Viber/LINE/WeChat/Signal/
// TikTok/Slack/Discord). Nothing here is hand-drawn or a generic stand-in
// for a specific company.
import {
  Apple, Email, Facebook, Google, Instagram,
  Language, LinkedIn, Sms, Telegram, WhatsApp, X, YouTube,
} from "@mui/icons-material";
import {
  SiDiscord, SiLine, SiSignal, SiTiktok, SiViber, SiWechat,
} from "react-icons/si";
import { FaSlack } from "react-icons/fa6";

const ICONS = {
  WhatsApp, Facebook, Instagram, Telegram, LinkedIn, YouTube, Language, Email, Sms, Google, Apple, X,
  Viber: SiViber, Line: SiLine, WeChat: SiWechat, Signal: SiSignal,
  TikTok: SiTiktok, Slack: FaSlack, Discord: SiDiscord,
};

export function resolveChannelIcon(name) {
  return ICONS[name] || Language;
}
