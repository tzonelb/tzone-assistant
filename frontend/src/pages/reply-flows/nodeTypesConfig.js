import {
  EmojiPeopleOutlined, BusinessOutlined, HelpOutlineOutlined, SmartToyOutlined,
  MenuBookOutlined, PsychologyOutlined, ChatBubbleOutlined, SupportAgentOutlined,
  EventAvailableOutlined, BuildOutlined, ShoppingBagOutlined, CallSplitOutlined,
  TimerOutlined, CheckCircleOutlined, FlagOutlined,
} from "@mui/icons-material";

// Every node type the builder supports — must stay in sync with
// backend/services/reply_flow_service.py's NODE_TYPES.
export const NODE_TYPE_CONFIG = {
  greeting: { label: "Greeting", icon: EmojiPeopleOutlined, color: "#2563eb", group: "Opening" },
  company_intro: { label: "Company Intro", icon: BusinessOutlined, color: "#2563eb", group: "Opening" },
  ask_question: { label: "Ask a Question", icon: HelpOutlineOutlined, color: "#2563eb", group: "Opening" },
  ai_direct: { label: "AI — Direct", icon: SmartToyOutlined, color: "#7c3aed", group: "Reply mode" },
  ai_knowledge_only: { label: "AI — Knowledge Only", icon: MenuBookOutlined, color: "#7c3aed", group: "Reply mode" },
  ai_knowledge_plus: { label: "AI + Knowledge", icon: PsychologyOutlined, color: "#7c3aed", group: "Reply mode" },
  canned_reply: { label: "Canned Reply", icon: ChatBubbleOutlined, color: "#64748b", group: "Reply mode" },
  human_handoff: { label: "Human Handoff", icon: SupportAgentOutlined, color: "#d97706", group: "Reply mode" },
  appointment: { label: "Book Appointment", icon: EventAvailableOutlined, color: "#16a34a", group: "Actions" },
  create_task: { label: "Create Task", icon: BuildOutlined, color: "#16a34a", group: "Actions" },
  product_suggest: { label: "Suggest Product", icon: ShoppingBagOutlined, color: "#16a34a", group: "Actions" },
  condition: { label: "Condition / Branch", icon: CallSplitOutlined, color: "#ca8a04", group: "Logic" },
  timeout_followup: { label: "Timeout Follow-up", icon: TimerOutlined, color: "#dc2626", group: "Logic" },
  close_chat: { label: "Close Chat + Summary", icon: CheckCircleOutlined, color: "#0d9488", group: "Logic" },
  end: { label: "End", icon: FlagOutlined, color: "#64748b", group: "Logic" },
};

export const NODE_GROUPS = ["Opening", "Reply mode", "Actions", "Logic"];
