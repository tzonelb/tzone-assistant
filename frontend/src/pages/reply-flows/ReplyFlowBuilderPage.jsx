import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  ReactFlow, ReactFlowProvider, Background, Controls, MiniMap,
  addEdge, useNodesState, useEdgesState, useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { ArrowBackOutlined, CloseOutlined, SaveOutlined, AutoAwesomeOutlined } from "@mui/icons-material";
import { getReplyFlowRequest, updateReplyFlowRequest, listDepartmentsRequest, generateReplyFlowFromTextRequest, getReplyFlowTriggerTypesRequest } from "../../api/client";
import { LoadingState, ErrorState } from "../../components/common";
import FlowStepNode from "./FlowStepNode";
import { NODE_GROUPS, NODE_TYPE_CONFIG } from "./nodeTypesConfig";
import { NODE_FIELDS, previewText } from "./nodeFieldsConfig";
import MultiSelectPopover from "../../components/common/MultiSelectPopover";
import { CHANNEL_OPTIONS, REPLY_MODE_OPTIONS } from "./ReplyFlowsListPage";
import "./ReplyFlowBuilderPage.css";

const REACT_FLOW_NODE_TYPES = { step: FlowStepNode };

// Placeholder mirror of the backend's TRIGGER_TYPES registry
// (GET /api/reply-flows/trigger-types), used only if that endpoint isn't
// reachable yet (e.g. 404 while it's still being built) so the picker still
// works. Shape: { key, label, category, description, config_fields }, where
// config_fields is shaped exactly like NODE_FIELDS entries above.
const FALLBACK_TRIGGER_TYPES = [
  { key: "new_conversation", label: "New conversation", category: "Conversation", description: "Starts when a customer opens a new chat.", config_fields: [] },
  { key: "conversation_closed", label: "Conversation closed", category: "Conversation", description: "Starts when a conversation is closed.", config_fields: [] },
  { key: "appointment_created", label: "Appointment created", category: "Appointments", description: "Starts when a new appointment is booked.", config_fields: [] },
  { key: "appointment_completed", label: "Appointment completed", category: "Appointments", description: "Starts when an appointment is marked completed.", config_fields: [] },
  {
    key: "appointment_reminder", label: "Appointment reminder", category: "Appointments",
    description: "Starts ahead of a scheduled appointment.",
    config_fields: [{ key: "minutes_before", label: "Minutes before the appointment", type: "number", placeholder: "60" }],
  },
  { key: "call_logged", label: "Call logged", category: "Calls", description: "Starts when a call is logged.", config_fields: [] },
  { key: "task_completed", label: "Task completed", category: "Tasks", description: "Starts when a task linked to this customer is marked done.", config_fields: [] },
];
const DEFAULT_TRIGGER_TYPE = "new_conversation";

// Repeatable {label, value} row editor for ask_question's "options" field
// (only relevant when its "mode" field is "buttons"/"both" — see
// nodeFieldsConfig.js). The engine (core/reply_flow_engine.py) matches a
// customer's reply against these by position, label, or value; the canvas
// node (FlowStepNode.jsx) shows them as read-only pills.
function OptionListField({ field, value, onChange }) {
  const options = Array.isArray(value) ? value : [];

  function updateOption(index, key, nextValue) {
    onChange(options.map((option, i) => (i === index ? { ...option, [key]: nextValue } : option)));
  }

  function addOption() {
    onChange([...options, { label: "", value: "" }]);
  }

  function removeOption(index) {
    onChange(options.filter((_, i) => i !== index));
  }

  return (
    <div className="reply-flow-field">
      {field.label}
      <div className="reply-flow-option-list">
        {options.map((option, index) => (
          <div className="reply-flow-option-row" key={index}>
            <input
              placeholder="Label (shown to the customer)"
              value={option?.label || ""}
              onChange={(event) => updateOption(index, "label", event.target.value)}
            />
            <input
              placeholder="Value (saved as the answer — defaults to the label)"
              value={option?.value || ""}
              onChange={(event) => updateOption(index, "value", event.target.value)}
            />
            <button type="button" className="reply-flow-option-remove" onClick={() => removeOption(index)} aria-label="Remove option">
              <CloseOutlined fontSize="small" />
            </button>
          </div>
        ))}
      </div>
      <button type="button" className="reply-flow-option-add" onClick={addOption}>+ Add option</button>
      {field.hint ? <span className="reply-flow-field-hint">{field.hint}</span> : null}
    </div>
  );
}

function NodeConfigField({ field, value, onChange, autoFocus }) {
  if (field.type === "checkbox") {
    return (
      <label className="reply-flow-inspector-checkbox">
        <input type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} />
        {field.label}
      </label>
    );
  }

  if (field.type === "option_list") {
    return <OptionListField field={field} value={value} onChange={onChange} />;
  }

  let control;
  if (field.type === "textarea") {
    control = <textarea rows={7} autoFocus={autoFocus} value={value || ""} placeholder={field.placeholder} onChange={(event) => onChange(event.target.value)} />;
  } else if (field.type === "select") {
    control = (
      <select className="input" autoFocus={autoFocus} value={value || ""} onChange={(event) => onChange(event.target.value)}>
        <option value="">Choose…</option>
        {field.options.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
      </select>
    );
  } else if (field.type === "number") {
    control = <input type="number" autoFocus={autoFocus} value={value ?? ""} placeholder={field.placeholder} onChange={(event) => onChange(event.target.value)} />;
  } else {
    control = <input autoFocus={autoFocus} value={value || ""} placeholder={field.placeholder} onChange={(event) => onChange(event.target.value)} />;
  }

  return (
    <label className="reply-flow-field reply-flow-primary-field">
      {field.label}
      {control}
      {field.hint ? <span className="reply-flow-field-hint">{field.hint}</span> : null}
    </label>
  );
}

let nodeIdCounter = 0;
function nextNodeId() {
  nodeIdCounter += 1;
  return `node-${Date.now()}-${nodeIdCounter}`;
}

function Palette() {
  function onDragStart(event, nodeType) {
    event.dataTransfer.setData("application/reply-flow-node", nodeType);
    event.dataTransfer.effectAllowed = "move";
  }

  return (
    <aside className="reply-flow-palette">
      <h4>Steps</h4>
      <p className="reply-flow-palette-hint">Drag a step onto the canvas to add it.</p>
      {NODE_GROUPS.map((group) => (
        <div className="reply-flow-palette-group" key={group}>
          <span className="reply-flow-palette-group-title">{group}</span>
          {Object.entries(NODE_TYPE_CONFIG)
            .filter(([, config]) => config.group === group)
            .map(([nodeType, config]) => {
              const Icon = config.icon;
              return (
                <div
                  key={nodeType}
                  className="reply-flow-palette-item"
                  draggable
                  onDragStart={(event) => onDragStart(event, nodeType)}
                  style={{ "--node-color": config.color }}
                >
                  <span className="reply-flow-palette-item-icon" style={{ background: `${config.color}1a`, color: config.color }}>
                    <Icon fontSize="small" />
                  </span>
                  <span className="reply-flow-palette-item-text">
                    <strong>{config.label}</strong>
                    <span>{config.description}</span>
                  </span>
                </div>
              );
            })}
        </div>
      ))}
    </aside>
  );
}

function BuilderCanvas({ nodes, edges, setNodes, setEdges, onNodesChange, onEdgesChange, onDirty }) {
  const { screenToFlowPosition } = useReactFlow();
  const [selectedNodeId, setSelectedNodeId] = useState(null);

  const handleNodesChange = useCallback((changes) => { onDirty(); onNodesChange(changes); }, [onNodesChange, onDirty]);
  const handleEdgesChange = useCallback((changes) => { onDirty(); onEdgesChange(changes); }, [onEdgesChange, onDirty]);
  const onConnect = useCallback((connection) => { onDirty(); setEdges((eds) => addEdge(connection, eds)); }, [setEdges, onDirty]);

  const onDrop = useCallback((event) => {
    event.preventDefault();
    const nodeType = event.dataTransfer.getData("application/reply-flow-node");
    if (!nodeType || !NODE_TYPE_CONFIG[nodeType]) return;
    const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
    const newNode = {
      id: nextNodeId(),
      type: "step",
      position,
      data: { nodeType, label: NODE_TYPE_CONFIG[nodeType].label, config: {} },
    };
    setNodes((nds) => nds.concat(newNode));
    onDirty();
  }, [screenToFlowPosition, setNodes, onDirty]);

  const onDragOver = useCallback((event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }, []);

  const selectedNode = nodes.find((node) => node.id === selectedNodeId) || null;

  function updateSelectedLabel(value) {
    setNodes((nds) => nds.map((node) => (node.id === selectedNodeId ? { ...node, data: { ...node.data, label: value } } : node)));
    onDirty();
  }

  function updateSelectedConfig(key, value) {
    setNodes((nds) => nds.map((node) => (
      node.id === selectedNodeId
        ? { ...node, data: { ...node.data, config: { ...node.data.config, [key]: value } } }
        : node
    )));
    onDirty();
  }

  function deleteSelected() {
    setNodes((nds) => nds.filter((node) => node.id !== selectedNodeId));
    setEdges((eds) => eds.filter((edge) => edge.source !== selectedNodeId && edge.target !== selectedNodeId));
    setSelectedNodeId(null);
    onDirty();
  }

  return (
    <div className="reply-flow-canvas-row">
      <Palette />
      <div className="reply-flow-canvas-wrap">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={handleNodesChange}
          onEdgesChange={handleEdgesChange}
          onConnect={onConnect}
          onDrop={onDrop}
          onDragOver={onDragOver}
          onNodeClick={(_event, node) => setSelectedNodeId(node.id)}
          onPaneClick={() => setSelectedNodeId(null)}
          nodeTypes={REACT_FLOW_NODE_TYPES}
          fitView
        >
          <Background gap={16} />
          <Controls />
          <MiniMap pannable zoomable />
        </ReactFlow>
        {nodes.length === 0 ? (
          <div className="reply-flow-canvas-hint">Drag a step from the left onto the canvas to start building.</div>
        ) : null}
      </div>
      {selectedNode ? (
        <aside className="reply-flow-inspector">
          <div className="reply-flow-inspector-head">
            <span>{NODE_TYPE_CONFIG[selectedNode.data.nodeType]?.label}</span>
            <button type="button" onClick={() => setSelectedNodeId(null)}><CloseOutlined fontSize="small" /></button>
          </div>
          {(NODE_FIELDS[selectedNode.data.nodeType] || []).length === 0 ? (
            <p className="reply-flow-inspector-no-fields">This step has no content to configure — it just moves the conversation to the next step.</p>
          ) : null}
          {(NODE_FIELDS[selectedNode.data.nodeType] || []).map((field, index) => (
            <NodeConfigField
              key={field.key}
              field={field}
              value={selectedNode.data.config?.[field.key]}
              onChange={(value) => updateSelectedConfig(field.key, value)}
              autoFocus={index === 0}
            />
          ))}
          <label className="reply-flow-field reply-flow-inspector-name">
            Internal step name <span className="reply-flow-field-hint">(for your own reference on the canvas — not sent to the customer)</span>
            <input value={selectedNode.data.label || ""} onChange={(event) => updateSelectedLabel(event.target.value)} />
          </label>
          <button type="button" className="reply-flow-inspector-delete" onClick={deleteSelected}>Delete step</button>
        </aside>
      ) : null}
    </div>
  );
}

function buildOutlineText(nodes) {
  if (!nodes.length) return "";
  return nodes
    .map((node, index) => {
      const config = NODE_TYPE_CONFIG[node.data.nodeType];
      const preview = previewText(node.data.nodeType, node.data.config);
      const title = `${config?.label || node.data.nodeType}${node.data.label && node.data.label !== config?.label ? ` — ${node.data.label}` : ""}`;
      return preview ? `${index + 1}. ${title}: ${preview}` : `${index + 1}. ${title}`;
    })
    .join("\n");
}

function OutlinePanel({ id, nodes, onGenerated }) {
  const [text, setText] = useState(() => buildOutlineText(nodes));
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState("");

  async function generate() {
    if (!text.trim()) return;
    setGenerating(true);
    setGenerateError("");
    try {
      const updated = await generateReplyFlowFromTextRequest(id, text);
      onGenerated(updated);
    } catch (requestError) {
      setGenerateError(requestError.message || "Could not generate a flow from this text.");
    } finally {
      setGenerating(false);
    }
  }

  return (
    <div className="reply-flow-outline">
      <p className="reply-flow-outline-hint">
        Describe both what should trigger this flow and what it should do — e.g. "when an appointment finishes,
        ask the customer to rate 1 to 5" — one step per line works well. The AI sets the trigger and the steps
        together (existing steps drawn on the canvas are replaced when you generate).
      </p>
      <textarea
        className="reply-flow-outline-textarea"
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder={"1. Greet the customer\n2. Ask what they need\n3. Let the AI answer using our knowledge base\n4. If they ask for a human, hand off"}
        rows={16}
      />
      {generateError ? <p className="customer-segment-error">{generateError}</p> : null}
      <button type="button" className="btn btn-primary" disabled={generating || !text.trim()} onClick={generate}>
        <AutoAwesomeOutlined fontSize="small" /> {generating ? "Generating…" : "Generate flow with AI"}
      </button>
    </div>
  );
}

export default function ReplyFlowBuilderPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [flow, setFlow] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [dirty, setDirty] = useState(false);
  const [name, setName] = useState("");
  const [channels, setChannels] = useState([]);
  const [departments, setDepartments] = useState([]);
  const [replyModes, setReplyModes] = useState([]);
  const [allDepartments, setAllDepartments] = useState([]);
  const [status, setStatus] = useState("draft");
  const [triggerTypes, setTriggerTypes] = useState(FALLBACK_TRIGGER_TYPES);
  const [triggerType, setTriggerType] = useState(DEFAULT_TRIGGER_TYPE);
  const [triggerConfig, setTriggerConfig] = useState({});
  const [view, setView] = useState(searchParams.get("view") === "outline" ? "outline" : "canvas");
  const [graphVersion, setGraphVersion] = useState(0);

  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  useEffect(() => {
    getReplyFlowRequest(id)
      .then((result) => {
        setFlow(result);
        setName(result.name);
        setChannels(result.channels || []);
        setDepartments(result.departments || []);
        setReplyModes(result.reply_modes || []);
        setStatus(result.status);
        setTriggerType(result.trigger_type || DEFAULT_TRIGGER_TYPE);
        setTriggerConfig(result.trigger_config || {});
        setNodes(result.nodes || []);
        setEdges(result.edges || []);
        setGraphVersion((v) => v + 1);
      })
      .catch((requestError) => setError(requestError.message || "Could not load this flow."))
      .finally(() => setLoading(false));
    listDepartmentsRequest().then((result) => setAllDepartments((result?.departments || []).filter((name) => name !== "Unassigned"))).catch(() => {});
    // The trigger-types registry endpoint is being built concurrently on the
    // backend — fall back to the mirrored placeholder list above if it 404s
    // or the request otherwise fails, so the picker still renders.
    getReplyFlowTriggerTypesRequest()
      .then((result) => {
        const types = result?.trigger_types || result?.types || (Array.isArray(result) ? result : null);
        if (Array.isArray(types) && types.length) setTriggerTypes(types);
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  function switchView(next) {
    setView(next);
    setSearchParams((params) => { params.set("view", next); return params; }, { replace: true });
  }

  function onOutlineGenerated(updatedFlow) {
    // The AI-generate call only persists nodes/edges server-side — any
    // pending edits to name/channels/departments/reply_modes/status made
    // before switching to the Outline tab are still unsaved, so this must
    // not clear a dirty flag it didn't set.
    setNodes(updatedFlow.nodes || []);
    setEdges(updatedFlow.edges || []);
    // generate_from_text is being extended to also infer a trigger from the
    // same plain-language description. Accept either a nested `trigger`
    // object or flat `trigger_type`/`trigger_config` fields, since the exact
    // response shape is still landing on the backend concurrently. Applying
    // it here only updates local state — it still requires Save, same as
    // the generated nodes/edges did before this.
    const trigger = updatedFlow.trigger || updatedFlow;
    if (trigger.trigger_type) {
      setTriggerType(trigger.trigger_type);
      setTriggerConfig(trigger.trigger_config || {});
      setDirty(true);
    }
    setGraphVersion((v) => v + 1);
    switchView("canvas");
  }

  async function save() {
    setSaving(true);
    setSaveError("");
    try {
      await updateReplyFlowRequest(id, {
        name, channels, departments, reply_modes: replyModes, status,
        trigger_type: triggerType, trigger_config: triggerConfig,
        nodes, edges,
      });
      setDirty(false);
    } catch (requestError) {
      setSaveError(requestError.message || "Could not save this flow.");
    } finally {
      setSaving(false);
    }
  }

  function updateTriggerConfig(key, value) {
    setTriggerConfig((prev) => ({ ...prev, [key]: value }));
    setDirty(true);
  }

  if (loading) return <LoadingState label="Loading flow…" />;
  if (error) return <ErrorState title="Could not load this flow" description={error} action={<button type="button" className="btn btn-primary" onClick={() => navigate("/company-settings?section=flow")}>Back to list</button>} />;
  if (!flow) return null;

  const selectedTriggerType = triggerTypes.find((t) => t.key === triggerType);
  const triggerCategories = triggerTypes.reduce((groups, t) => {
    const category = t.category || "Trigger";
    if (!groups[category]) groups[category] = [];
    groups[category].push(t);
    return groups;
  }, {});

  return (
    <section className="reply-flow-builder">
      <header className="reply-flow-builder-header">
        <button type="button" className="reply-flow-back" onClick={() => navigate("/company-settings?section=flow")}>
          <ArrowBackOutlined fontSize="small" /> Reply Flows
        </button>
        <input
          className="reply-flow-name-input"
          value={name}
          onChange={(event) => { setName(event.target.value); setDirty(true); }}
        />
        <div className="reply-flow-builder-controls">
          <MultiSelectPopover
            label="Channels"
            options={CHANNEL_OPTIONS}
            value={channels}
            onChange={(next) => { setChannels(next); setDirty(true); }}
            allLabel="All channels"
          />
          <MultiSelectPopover
            label="Departments"
            options={allDepartments.map((name) => ({ value: name, label: name }))}
            value={departments}
            onChange={(next) => { setDepartments(next); setDirty(true); }}
            allLabel="All departments"
            emptyHint="No departments set up yet — add some in Company Settings → Departments first."
          />
          <MultiSelectPopover
            label="Reply mode"
            options={REPLY_MODE_OPTIONS}
            value={replyModes}
            onChange={(next) => { setReplyModes(next); setDirty(true); }}
            allLabel="Per-step"
          />
          <div className="reply-flow-trigger-control">
            <span className="reply-flow-trigger-label">Trigger</span>
            <select
              className="input"
              value={triggerType}
              onChange={(event) => {
                setTriggerType(event.target.value);
                setTriggerConfig({});
                setDirty(true);
              }}
            >
              {Object.entries(triggerCategories).map(([category, items]) => (
                <optgroup label={category} key={category}>
                  {items.map((t) => <option value={t.key} key={t.key}>{t.label}</option>)}
                </optgroup>
              ))}
            </select>
            {(selectedTriggerType?.config_fields || []).map((field, index) => (
              <NodeConfigField
                key={field.key}
                field={field}
                value={triggerConfig?.[field.key]}
                onChange={(value) => updateTriggerConfig(field.key, value)}
                autoFocus={index === 0}
              />
            ))}
          </div>
          <select className="input" value={status} onChange={(event) => { setStatus(event.target.value); setDirty(true); }}>
            <option value="draft">Draft</option>
            <option value="active">Active</option>
            <option value="archived">Archived</option>
          </select>
          {saveError ? <span className="reply-flow-save-error">{saveError}</span> : null}
          <button type="button" className="btn btn-primary" disabled={saving} onClick={save}>
            <SaveOutlined fontSize="small" /> {saving ? "Saving…" : (dirty ? "Save changes" : "Saved")}
          </button>
        </div>
      </header>

      <div className="reply-flow-view-tabs">
        <button type="button" className={view === "canvas" ? "is-active" : ""} onClick={() => switchView("canvas")}>Canvas</button>
        <button type="button" className={view === "outline" ? "is-active" : ""} onClick={() => switchView("outline")}>Outline</button>
      </div>

      {view === "canvas" ? (
        <ReactFlowProvider>
          <BuilderCanvas
            nodes={nodes}
            edges={edges}
            setNodes={setNodes}
            setEdges={setEdges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onDirty={() => setDirty(true)}
          />
        </ReactFlowProvider>
      ) : (
        <OutlinePanel key={graphVersion} id={id} nodes={nodes} onGenerated={onOutlineGenerated} />
      )}
    </section>
  );
}
