import { FacebookOutlined, Instagram } from "@mui/icons-material";

export function channelIcon(channel) {
  return channel === "instagram" ? Instagram : FacebookOutlined;
}
