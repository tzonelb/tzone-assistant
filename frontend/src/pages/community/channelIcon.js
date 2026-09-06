import { createElement } from "react";

import { FacebookOutlined, Instagram, Telegram, WhatsApp } from "@mui/icons-material";

const ICONS = {
  instagram: Instagram,
  whatsapp: WhatsApp,
  telegram: Telegram,
  messenger: FacebookOutlined,
};

export function channelIcon(channel) {
  return ICONS[channel] || FacebookOutlined;
}

/* The icon for a channel, as an element.
 *
 * Built with createElement rather than `const Icon = channelIcon(...)` in a
 * component body: assigning a capitalized binding from a call during render
 * reads to the linter as building a new component type on every render, which
 * would remount its subtree. The type here is a constant from the map above,
 * so the element is made directly and that pattern never appears.
 */
export function ChannelIcon({ channel, ...props }) {
  return createElement(ICONS[channel] || FacebookOutlined, props);
}
