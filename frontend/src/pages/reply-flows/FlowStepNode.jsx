import { Handle, Position } from "@xyflow/react";
import { NODE_TYPE_CONFIG } from "./nodeTypesConfig";
import { previewText } from "./nodeFieldsConfig";

const MAX_OPTION_PILLS = 3;

// Read-only preview of ask_question's button options, when the (backend-owned)
// mode/options config marks the question as button-based rather than free-text.
// The actual editing UI for mode/options lives in the inspector panel.
function ButtonOptionsPreview({ config }) {
  const mode = config?.mode;
  const options = Array.isArray(config?.options) ? config.options : [];
  if (!mode || mode === "text" || options.length === 0) return null;

  const shown = options.slice(0, MAX_OPTION_PILLS);
  const remaining = options.length - shown.length;

  return (
    <div className="flow-step-node-options">
      {shown.map((option, index) => (
        <span className="flow-step-node-option-pill" key={option?.value ?? option?.label ?? index}>
          {option?.label || option?.value || "Option"}
        </span>
      ))}
      {remaining > 0 ? <span className="flow-step-node-option-pill flow-step-node-option-pill-more">+{remaining}</span> : null}
    </div>
  );
}

export default function FlowStepNode({ data, selected }) {
  const config = NODE_TYPE_CONFIG[data.nodeType] || {};
  const Icon = config.icon;
  const preview = previewText(data.nodeType, data.config);
  const isCondition = data.nodeType === "condition";

  return (
    <div className={`flow-step-node ${selected ? "is-selected" : ""}`} style={{ borderColor: config.color }}>
      <Handle type="target" position={Position.Top} />
      <div className="flow-step-node-icon" style={{ background: `${config.color}1a`, color: config.color }}>
        {Icon ? <Icon fontSize="small" /> : null}
      </div>
      <div className="flow-step-node-body">
        <span className="flow-step-node-type">{config.label}</span>
        <span className="flow-step-node-label">{data.label || config.label}</span>
        {preview ? <span className="flow-step-node-preview">{preview}</span> : null}
        {data.nodeType === "ask_question" ? <ButtonOptionsPreview config={data.config} /> : null}
      </div>
      {isCondition ? (
        <>
          <span className="flow-step-node-branch-label flow-step-node-branch-label-yes">Yes</span>
          <span className="flow-step-node-branch-label flow-step-node-branch-label-no">No</span>
          <Handle type="source" id="true" position={Position.Bottom} style={{ left: "30%" }} />
          <Handle type="source" id="false" position={Position.Bottom} style={{ left: "70%" }} />
        </>
      ) : (
        <Handle type="source" position={Position.Bottom} />
      )}
    </div>
  );
}
