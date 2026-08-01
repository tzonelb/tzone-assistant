// Real per-node-type configuration fields — every node needs to say what
// it actually DOES, not just carry a display label. Modeled on Voiceflow's
// Choice/Playbook blocks (variable/operator/value branching, exit
// conditions on AI steps) and ManyChat's per-action-type dedicated fields
// (never one generic freeform box). Stored in node.data.config.
const TASK_TYPES = [
  { value: "follow_up", label: "Follow-up" },
  { value: "complaint", label: "Complaint" },
  { value: "service_request", label: "Service request" },
  { value: "sales_inquiry", label: "Sales inquiry" },
  { value: "internal", label: "Internal" },
  { value: "other", label: "Other" },
];

const CONDITION_OPERATORS = [
  { value: "equals", label: "equals" },
  { value: "contains", label: "contains" },
  { value: "greater_than", label: "greater than" },
  { value: "less_than", label: "less than" },
  { value: "is_set", label: "is set (has any value)" },
];

export const NODE_FIELDS = {
  greeting: [
    { key: "text", label: "Greeting message", type: "textarea", placeholder: "Hi {{customer_name}}, welcome to {{company_name}}!", hint: "Variables: {{customer_name}}, {{company_name}}" },
  ],
  company_intro: [
    { key: "text", label: "Company intro message", type: "textarea", placeholder: "We're {{company_name}} — we help you with…" },
  ],
  ask_question: [
    { key: "question", label: "Question to ask", type: "textarea", placeholder: "How can we help you today?" },
    { key: "save_as", label: "Save the answer as (variable name)", type: "text", placeholder: "e.g. customer_need" },
  ],
  ai_direct: [
    { key: "instructions", label: "Instructions for the AI", type: "textarea", placeholder: "Answer naturally in your own words, stay on topic, keep it short." },
    { key: "exit_when", label: "Move to the next step when…", type: "textarea", placeholder: "The customer confirms what they want." },
  ],
  ai_knowledge_only: [
    { key: "instructions", label: "Instructions for the AI", type: "textarea", placeholder: "Only answer using the Knowledge Base — never guess." },
    { key: "exit_when", label: "Move to the next step when…", type: "textarea" },
  ],
  ai_knowledge_plus: [
    { key: "instructions", label: "Instructions for the AI", type: "textarea", placeholder: "Use the Knowledge Base first, then reason naturally to fill gaps." },
    { key: "exit_when", label: "Move to the next step when…", type: "textarea" },
  ],
  canned_reply: [
    { key: "text", label: "Reply text (sent exactly as written)", type: "textarea", placeholder: "Thanks for reaching out — our working hours are…" },
  ],
  human_handoff: [
    { key: "note", label: "Note for the employee taking over", type: "textarea", placeholder: "Why this needs a human, and what's been discussed so far." },
  ],
  appointment: [
    { key: "note", label: "Instructions / service context", type: "textarea", placeholder: "What kind of appointment, what info to collect first." },
  ],
  create_task: [
    { key: "task_type", label: "Task type", type: "select", options: TASK_TYPES },
    { key: "note", label: "Task details", type: "textarea", placeholder: "What the task should say when it's created." },
  ],
  product_suggest: [
    { key: "note", label: "What to suggest / guidance", type: "textarea", placeholder: "Suggest from the Sales category matching what the customer asked about." },
  ],
  condition: [
    { key: "variable", label: "Variable", type: "text", placeholder: "e.g. customer_need" },
    { key: "operator", label: "Operator", type: "select", options: CONDITION_OPERATORS },
    { key: "value", label: "Value to compare against", type: "text", placeholder: "Leave empty for \"is set\"" },
  ],
  timeout_followup: [
    { key: "wait_minutes", label: "Wait before following up (minutes)", type: "number", placeholder: "60" },
    { key: "text", label: "Follow-up message", type: "textarea", placeholder: "Just checking in — are you still there?" },
  ],
  close_chat: [
    { key: "ask_reschedule", label: "Ask about scheduling a follow-up", type: "checkbox" },
  ],
  end: [],
};

export function previewText(nodeType, config) {
  const fields = NODE_FIELDS[nodeType] || [];
  const firstTextField = fields.find((field) => field.type === "textarea" || field.type === "text");
  if (!firstTextField) return "";
  return (config?.[firstTextField.key] || "").trim();
}
