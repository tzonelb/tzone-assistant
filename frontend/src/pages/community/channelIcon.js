import { FacebookOutlined, Instagram, Language, Telegram, WhatsApp } from "@mui/icons-material";

const ICONS = {
  instagram: Instagram,
  whatsapp: WhatsApp,
  telegram: Telegram,
  website: Language,
  messenger: FacebookOutlined,
};

export function channelIcon(channel) {
  return ICONS[channel] || FacebookOutlined;
}
