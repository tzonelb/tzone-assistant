import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ReactFlow, ReactFlowProvider, Background, Controls, MiniMap,
  addEdge, useNodesState, useEdgesState, useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { ArrowBackOutlined, CloseOutlined, SaveOutlined } from "@mui/icons-material";
import { getReplyFlowRequest, updateReplyFlowRequest, listDepartmentsRequest } from "../../api/client";
import { AppButton, LoadingState, ErrorState } from "../../components/common";
import FlowStepNode from "./FlowStepNode";
import { NODE_GROUPS, NODE_TYPE_CONFIG } from "./nodeTypesConfig";
import { NODE_FIELDS } from "./nodeFieldsConfig";
import MultiSelectPopover from "./MultiSelectPopover";
import { CHANNEL_OPTIONS } from "./ReplyFlowsListPage";
import "./ReplyFlowBuilderPage.css";

const REACT_FLOW_NODE_TYPES = { step: FlowStepNode };

function NodeConfigField({ field, value, onChange }) {
  if (field.type === "checkbox") {
    return (
      <label className="reply-flow-inspector-checkbox">
        <input type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} />
        {field.label}
      </label>
    );
  }

  let control;
  if (field.type === "textarea") {
    control = <textarea rows={3} value={value || ""} placeholder={field.placeholder} onChange={(event) => onChange(event.target.value)} />;
  } else if (field.type === "select") {
    control = (
      <select className="tz-select" value={value || ""} onChange={(event) => onChange(event.target.value)}>
        <option value="">Choose…</option>
        {field.options.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
      </select>
    );
  } else if (field.type === "number") {
    control = <input type="number" value={value ?? ""} placeholder={field.placeholder} onChange={(event) => onChange(event.target.value)} />;
  } else {
    control = <input value={value || ""} placeholder={field.placeholder} onChange={(event) => onChange(event.target.value)} />;
  }

  return (
    <label className="ai-teaching-field">
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
      <h4>Drag onto the canvas</h4>
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
                  style={{ borderColor: config.color }}
                >
                  <span className="reply-flow-palette-item-icon" style={{ background: `${config.color}1a`, color: config.color }}>
                    <Icon fontSize="small" />
                  </span>
                  {config.label}
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
          <label className="ai-teaching-field">
            Label
            <input value={selectedNode.data.label || ""} onChange={(event) => updateSelectedLabel(event.target.value)} />
          </label>
          {(NODE_FIELDS[selectedNode.data.nodeType] || []).map((field) => (
            <NodeConfigField
              key={field.key}
              field={field}
              value={selectedNode.data.config?.[field.key]}
              onChange={(value) => updateSelectedConfig(field.key, value)}
            />
          ))}
          <button type="button" className="reply-flow-inspector-delete" onClick={deleteSelected}>Delete step</button>
        </aside>
      ) : null}
    </div>
  );
}

export default function ReplyFlowBuilderPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [flow, setFlow] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [dirty, setDirty] = useState(false);
  const [name, setName] = useState("");
  const [channels, setChannels] = useState([]);
  const [departments, setDepartments] = useState([]);
  const [allDepartments, setAllDepartments] = useState([]);
  const [status, setStatus] = useState("draft");

  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  useEffect(() => {
    getReplyFlowRequest(id)
      .then((result) => {
        setFlow(result);
        setName(result.name);
        setChannels(result.channels || []);
        setDepartments(result.departments || []);
        setStatus(result.status);
        setNodes(result.nodes || []);
        setEdges(result.edges || []);
      })
      .catch((requestError) => setError(requestError.message || "Could not load this flow."))
      .finally(() => setLoading(false));
    listDepartmentsRequest().then((result) => setAllDepartments((result?.departments || []).filter((name) => name !== "Unassigned"))).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function save() {
    setSaving(true);
    setSaveError("");
    try {
      await updateReplyFlowRequest(id, { name, channels, departments, status, nodes, edges });
      setDirty(false);
    } catch (requestError) {
      setSaveError(requestError.message || "Could not save this flow.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <LoadingState label="Loading flow…" />;
  if (error) return <ErrorState title="Could not load this flow" description={error} action={<AppButton variant="primary" onClick={() => navigate("/reply-flows")}>Back to list</AppButton>} />;
  if (!flow) return null;

  return (
    <section className="reply-flow-builder">
      <header className="reply-flow-builder-header">
        <button type="button" className="reply-flow-back" onClick={() => navigate("/reply-flows")}>
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
          <select className="tz-select" value={status} onChange={(event) => { setStatus(event.target.value); setDirty(true); }}>
            <option value="draft">Draft</option>
            <option value="active">Active</option>
            <option value="archived">Archived</option>
          </select>
          {saveError ? <span className="reply-flow-save-error">{saveError}</span> : null}
          <AppButton variant="primary" icon={<SaveOutlined fontSize="small" />} loading={saving} onClick={save}>
            {dirty ? "Save changes" : "Saved"}
          </AppButton>
        </div>
      </header>

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
    </section>
  );
}
