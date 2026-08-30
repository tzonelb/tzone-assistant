import {
  EmojiPeopleOutlined, BusinessOutlined, HelpOutlineOutlined, SmartToyOutlined,
  MenuBookOutlined, PsychologyOutlined, ChatBubbleOutlined, SupportAgentOutlined,
  EventAvailableOutlined, BuildOutlined, ShoppingBagOutlined, CallSplitOutlined,
  TimerOutlined, CheckCircleOutlined, FlagOutlined,
} from "@mui/icons-material";

// Every node type the builder supports — must stay in sync with
// backend/services/reply_flow_service.py's NODE_TYPES.
export const NODE_TYPE_CONFIG = {
  greeting: { label: "Greeting", description: "The opening message when a chat starts.", icon: EmojiPeopleOutlined, color: "#2563eb", group: "Opening" },
  company_intro: { label: "Company Intro", description: "A short message introducing the business.", icon: BusinessOutlined, color: "#2563eb", group: "Opening" },
  ask_question: { label: "Ask a Question", description: "Ask something and save the customer's answer.", icon: HelpOutlineOutlined, color: "#2563eb", group: "Opening" },
  ai_direct: { label: "AI — Direct", description: "AI replies freely, in its own words.", icon: SmartToyOutlined, color: "#7c3aed", group: "Reply mode" },
  ai_knowledge_only: { label: "AI — Knowledge Only", description: "AI answers only from the Knowledge Base.", icon: MenuBookOutlined, color: "#7c3aed", group: "Reply mode" },
  ai_knowledge_plus: { label: "AI + Knowledge", description: "AI uses the Knowledge Base, then reasons further.", icon: PsychologyOutlined, color: "#7c3aed", group: "Reply mode" },
  canned_reply: { label: "Canned Reply", description: "Send fixed text, exactly as written.", icon: ChatBubbleOutlined, color: "#64748b", group: "Reply mode" },
  human_handoff: { label: "Human Handoff", description: "Stop the AI and notify a team member.", icon: SupportAgentOutlined, color: "#d97706", group: "Reply mode" },
  appointment: { label: "Book Appointment", description: "Collect details to schedule an appointment.", icon: EventAvailableOutlined, color: "#16a34a", group: "Actions" },
  create_task: { label: "Create Task", description: "Create an internal task from this conversation.", icon: BuildOutlined, color: "#16a34a", group: "Actions" },
  product_suggest: { label: "Suggest Product", description: "Recommend something from the Master Catalogue.", icon: ShoppingBagOutlined, color: "#16a34a", group: "Actions" },
  condition: { label: "Condition / Branch", description: "Split the conversation based on a saved answer.", icon: CallSplitOutlined, color: "#ca8a04", group: "Logic" },
  timeout_followup: { label: "Timeout Follow-up", description: "Follow up if the customer goes quiet.", icon: TimerOutlined, color: "#dc2626", group: "Logic" },
  close_chat: { label: "Close Chat + Summary", description: "Wrap up with a summary and close the chat.", icon: CheckCircleOutlined, color: "#0d9488", group: "Logic" },
  end: { label: "End", description: "End the flow — hands back to the default AI.", icon: FlagOutlined, color: "#64748b", group: "Logic" },
};

export const NODE_GROUPS = ["Opening", "Reply mode", "Actions", "Logic"];
