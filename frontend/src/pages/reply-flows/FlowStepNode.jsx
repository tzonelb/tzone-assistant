import { Handle, Position } from "@xyflow/react";
import { NODE_TYPE_CONFIG } from "./nodeTypesConfig";

export default function FlowStepNode({ data, selected }) {
  const config = NODE_TYPE_CONFIG[data.nodeType] || {};
  const Icon = config.icon;

  return (
    <div className={`flow-step-node ${selected ? "is-selected" : ""}`} style={{ borderColor: config.color }}>
      <Handle type="target" position={Position.Top} />
      <div className="flow-step-node-icon" style={{ background: `${config.color}1a`, color: config.color }}>
        {Icon ? <Icon fontSize="small" /> : null}
      </div>
      <div className="flow-step-node-body">
        <span className="flow-step-node-type">{config.label}</span>
        <span className="flow-step-node-label">{data.label || config.label}</span>
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
