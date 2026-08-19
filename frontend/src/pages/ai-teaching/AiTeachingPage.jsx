import { useCallback, useEffect, useState } from "react";
import {
  AddOutlined,
  ArrowDownwardOutlined,
  ArrowUpwardOutlined,
  DeleteOutlineOutlined,
  PlayArrowOutlined,
  RefreshOutlined,
} from "@mui/icons-material";

import {
  clearReplyPolicyChannelRequest,
  createBusinessDepartmentRequest,
  deleteBusinessDepartmentRequest,
  getAiTeachingProfileRequest,
  getAiTeachingPromptRequest,
  getReplyPolicyRequest,
  listBusinessDepartmentsRequest,
  reorderBusinessDepartmentsRequest,
  runAiTeachingDryRunRequest,
  updateAiTeachingProfileRequest,
  updateBusinessDepartmentRequest,
  updateReplyPolicyChannelRequest,
  updateReplyPolicyDefaultRequest,
} from "../../api/aiTeaching";
import {
  createBranchRequest,
  deleteBranchRequest,
  listBranchesRequest,
  updateBranchRequest,
} from "../../api/client";
import {
  AppButton,
  AppCard,
  ConfirmDialog,
  ErrorState,
  LoadingState,
  PageHeader,
} from "../../components/common";
import "./AiTeachingPage.css";

const FALLBACK_TONES = [
  "friendly",
  "professional",
  "formal",
  "casual",
  "concise",
  "warm",
  "technical",
];

const FALLBACK_CHANNELS = [
  "messenger",
  "instagram",
  "whatsapp",
  "telegram",
];

const MAX_EXAMPLES = 20;

const MAX_DEPARTMENTS = 40;

function emptyDepartment() {
  return {
    code: "",
    name_ar: "",
    name_en: "",
    button_ar: "",
    button_en: "",
    enabled: true,
  };
}

function departmentRow(department) {
  return {
    id: department.id,
    code: department.code || "",
    name_ar: department.name_ar || "",
    name_en: department.name_en || "",
    button_ar: department.button_ar || "",
    button_en: department.button_en || "",
    enabled: Boolean(department.enabled),
  };
}

function departmentPayload(row) {
  return {
    code: (row.code || "").trim(),
    name_ar: (row.name_ar || "").trim() || null,
    name_en: (row.name_en || "").trim() || null,
    button_ar: (row.button_ar || "").trim() || null,
    button_en: (row.button_en || "").trim() || null,
    enabled: Boolean(row.enabled),
  };
}

// ----------------------------------------------------------------------
// The reply policy — how this company answers, per channel
//
// Two levels: the company's own default, and an optional override per channel.
// Underneath both sit the platform's shipped values. A setting that is not
// overridden *inherits* — it does not hold a copy — so every control here can
// be put back to inheriting, and the screen has to show which of the two it is
// looking at. Showing only the effective value is exactly how a control starts
// looking like a decision somebody made when nobody made it.
// ----------------------------------------------------------------------

const POLICY_DEFAULT_SCOPE = "default";

const CHANNEL_LABELS = {
  messenger: "Messenger",
  instagram: "Instagram",
  whatsapp: "WhatsApp",
  telegram: "Telegram",
};

const POLICY_CHOICE_LABELS = {
  always: "Every message",
  once_per_conversation: "Once per conversation",
  never: "Never",
  grounded_ai: "Grounded AI",
  knowledge_then_ai: "Knowledge, then AI",
  flow_only: "Flow only",
};

function channelLabel(channel) {
  return CHANNEL_LABELS[channel] || channel;
}

function choiceLabel(value) {
  return POLICY_CHOICE_LABELS[value] || String(value);
}

function formatPolicyValue(field, value) {
  if (value === undefined || value === null || value === "") {
    return "not set";
  }

  if (field.type === "boolean") {
    return value ? "on" : "off";
  }

  if (field.type === "choice") {
    return choiceLabel(value);
  }

  return String(value);
}

/** The overrides each scope has actually stored, as the drafts the form edits. */
function policyDraftsFrom(policy) {
  const drafts = {
    [POLICY_DEFAULT_SCOPE]: { ...(policy?.default?.overrides || {}) },
  };

  (policy?.channels || []).forEach((row) => {
    drafts[row.channel] = { ...(row.overrides || {}) };
  });

  return drafts;
}

function policyScopes(policy) {
  if (!policy) {
    return [];
  }

  return [
    {
      key: POLICY_DEFAULT_SCOPE,
      channel: null,
      title: "Your default",
      subtitle: "Applies on every channel you have not overridden below.",
      inheritLabel: "the platform default",
      inherited: policy.default?.inherited || {},
      overrides: policy.default?.overrides || {},
      values: policy.default?.values || {},
    },
    ...(policy.channels || []).map((row) => ({
      key: row.channel,
      channel: row.channel,
      title: channelLabel(row.channel),
      subtitle: null,
      inheritLabel: "your default",
      inherited: row.inherited || {},
      overrides: row.overrides || {},
      values: row.values || {},
    })),
  ];
}

function policyScopeDirty(scope, draft) {
  const stored = scope.overrides || {};
  const draftKeys = Object.keys(draft || {});
  const storedKeys = Object.keys(stored);

  if (draftKeys.length !== storedKeys.length) {
    return true;
  }

  return draftKeys.some((key) => String(draft[key]) !== String(stored[key]));
}

/** Numbers arrive from the inputs as strings; the API refuses those, rightly. */
function policyValuesFromDraft(draft, fields) {
  const values = {};

  fields.forEach((field) => {
    if (!(field.key in draft)) {
      return;
    }

    const raw = draft[field.key];

    if (field.type === "boolean") {
      values[field.key] = Boolean(raw);
      return;
    }

    if (field.type === "fraction" || field.type === "integer") {
      const number =
        field.type === "integer"
          ? Number.parseInt(raw, 10)
          : Number.parseFloat(raw);

      if (Number.isNaN(number)) {
        throw new Error(`${field.label} needs a number.`);
      }

      values[field.key] = number;
      return;
    }

    values[field.key] = String(raw);
  });

  return values;
}

function PolicyField({ field, scope, draft, onOverride, onChange, disabled }) {
  const overridden = field.key in draft;
  const inherited = scope.inherited?.[field.key];
  const value = overridden ? draft[field.key] : inherited;
  const controlId = `ai-teaching-policy-${scope.key}-${field.key}`;

  return (
    <li className={overridden ? "" : "is-inherited"}>
      <div className="ai-teaching-policy-field-head">
        <span className="ai-teaching-policy-label">{field.label}</span>

        <label htmlFor={`${controlId}-override`}>
          <input
            id={`${controlId}-override`}
            type="checkbox"
            checked={overridden}
            disabled={disabled}
            onChange={(event) =>
              onOverride(field, event.target.checked, inherited)
            }
          />

          <span>{overridden ? "Set here" : "Inheriting"}</span>
        </label>
      </div>

      {field.type === "boolean" ? (
        <label className="ai-teaching-policy-switch" htmlFor={controlId}>
          <input
            id={controlId}
            type="checkbox"
            checked={Boolean(value)}
            disabled={disabled || !overridden}
            onChange={(event) => onChange(field, event.target.checked)}
          />

          <span>{value ? "On" : "Off"}</span>
        </label>
      ) : field.type === "choice" ? (
        <select
          id={controlId}
          value={value ?? ""}
          disabled={disabled || !overridden}
          onChange={(event) => onChange(field, event.target.value)}
        >
          {(field.choices || []).map((choice) => (
            <option key={choice} value={choice}>
              {choiceLabel(choice)}
            </option>
          ))}
        </select>
      ) : (
        <input
          id={controlId}
          type="number"
          value={value ?? ""}
          min={field.minimum}
          max={field.maximum}
          step={field.step}
          disabled={disabled || !overridden}
          onChange={(event) => onChange(field, event.target.value)}
        />
      )}

      <small className="ai-teaching-note">
        {overridden
          ? field.help
          : `Inherits ${scope.inheritLabel}: ${formatPolicyValue(
              field,
              inherited,
            )}.`}
      </small>
    </li>
  );
}

function emptyForm() {
  return {
    name: "",
    tone: "friendly",
    default_language: "ar",
    system_prompt: "",
    welcome_enabled: true,
    welcome_message_ar: "",
    welcome_message_en: "",
    examples: [],
  };
}

function formFromProfile(profile) {
  const form = emptyForm();

  if (!profile) {
    return form;
  }

  return {
    name: profile.name || "",
    tone: profile.tone || "friendly",
    default_language: profile.default_language || "ar",
    system_prompt: profile.system_prompt || "",
    welcome_enabled: Boolean(profile.welcome_enabled),
    welcome_message_ar: profile.welcome_message_ar || "",
    welcome_message_en: profile.welcome_message_en || "",
    examples: Array.isArray(profile.examples)
      ? profile.examples.map((item) => ({
          customer: item?.customer || "",
          reply: item?.reply || "",
        }))
      : [],
  };
}

function payloadFromForm(form) {
  return {
    name: form.name.trim() || "Default Assistant",
    tone: form.tone.trim() || null,
    default_language: form.default_language,
    system_prompt: form.system_prompt.trim() || null,
    welcome_enabled: form.welcome_enabled,
    welcome_message_ar: form.welcome_message_ar.trim() || null,
    welcome_message_en: form.welcome_message_en.trim() || null,
    examples: form.examples
      .map((item) => ({
        customer: (item.customer || "").trim(),
        reply: (item.reply || "").trim(),
      }))
      .filter((item) => item.customer && item.reply),
  };
}

export default function AiTeachingPage() {
  const [form, setForm] = useState(emptyForm);
  const [tones, setTones] = useState(FALLBACK_TONES);
  const [channels, setChannels] = useState(FALLBACK_CHANNELS);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState("");
  const [saveError, setSaveError] = useState("");

  const [testChannel, setTestChannel] = useState("messenger");
  const [testMessage, setTestMessage] = useState("");
  const [testRunning, setTestRunning] = useState(false);
  const [testError, setTestError] = useState("");
  const [testResult, setTestResult] = useState(null);

  const [promptOpen, setPromptOpen] = useState(false);
  const [promptText, setPromptText] = useState("");
  const [promptLoading, setPromptLoading] = useState(false);
  const [promptError, setPromptError] = useState("");

  // Branches sit next to sections because they are the same kind of thing: a
  // list the company defines about itself. They differ in what they do —
  // a section is offered to a customer, a branch is only ever a label for
  // filtering the inbox and the channel list.
  const [branches, setBranches] = useState([]);
  const [branchesError, setBranchesError] = useState("");
  const [branchesStatus, setBranchesStatus] = useState("");
  const [branchBusyId, setBranchBusyId] = useState(null);
  const [newBranch, setNewBranch] = useState(null);
  const [savingBranch, setSavingBranch] = useState(false);

  const [departments, setDepartments] = useState([]);
  const [departmentsLoading, setDepartmentsLoading] = useState(true);
  const [departmentsError, setDepartmentsError] = useState("");
  const [departmentsStatus, setDepartmentsStatus] = useState("");
  const [departmentBusyId, setDepartmentBusyId] = useState(null);
  const [dirtyDepartments, setDirtyDepartments] = useState([]);
  const [newDepartment, setNewDepartment] = useState(null);
  const [creatingDepartment, setCreatingDepartment] = useState(false);
  const [pendingDepartmentDelete, setPendingDepartmentDelete] = useState(null);
  const [deletingDepartment, setDeletingDepartment] = useState(false);

  const [policy, setPolicy] = useState(null);
  const [policyDrafts, setPolicyDrafts] = useState({});
  const [policyLoading, setPolicyLoading] = useState(true);
  const [policyError, setPolicyError] = useState("");
  const [policyStatus, setPolicyStatus] = useState("");
  const [policyBusyScope, setPolicyBusyScope] = useState(null);

  const loadProfile = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const result = await getAiTeachingProfileRequest();

      setForm(formFromProfile(result?.profile));

      if (Array.isArray(result?.tones) && result.tones.length) {
        setTones(result.tones);
      }

      if (Array.isArray(result?.channels) && result.channels.length) {
        setChannels(result.channels);
      }
    } catch (requestError) {
      setError(
        requestError.message || "The assistant profile could not be loaded.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  const loadBranches = useCallback(async () => {
    setBranchesError("");

    try {
      const result = await listBranchesRequest();
      setBranches(Array.isArray(result?.branches) ? result.branches : []);
    } catch (requestError) {
      setBranchesError(requestError.message || "Branches could not be loaded.");
    }
  }, []);

  const handleCreateBranch = useCallback(async () => {
    if (!newBranch?.name?.trim()) {
      setBranchesError("A branch needs a name.");
      return;
    }

    setSavingBranch(true);
    setBranchesError("");
    setBranchesStatus("");

    try {
      await createBranchRequest({
        name: newBranch.name.trim(),
        code: newBranch.code?.trim() || null,
        address: newBranch.address?.trim() || null,
        phone: newBranch.phone?.trim() || null,
      });
      setNewBranch(null);
      setBranchesStatus("Branch added.");
      await loadBranches();
    } catch (requestError) {
      setBranchesError(requestError.message || "The branch could not be saved.");
    } finally {
      setSavingBranch(false);
    }
  }, [newBranch, loadBranches]);

  const handleRenameBranch = useCallback(
    async (branch, name) => {
      const trimmed = String(name || "").trim();

      if (!trimmed || trimmed === branch.name) {
        return;
      }

      setBranchBusyId(branch.id);
      setBranchesError("");

      try {
        await updateBranchRequest(branch.id, { name: trimmed });
        setBranchesStatus("Branch renamed.");
        await loadBranches();
      } catch (requestError) {
        setBranchesError(requestError.message || "The branch could not be renamed.");
        await loadBranches();
      } finally {
        setBranchBusyId(null);
      }
    },
    [loadBranches],
  );

  const handleDeleteBranch = useCallback(
    async (branch) => {
      setBranchBusyId(branch.id);
      setBranchesError("");

      try {
        await deleteBranchRequest(branch.id);
        setBranchesStatus("Branch removed.");
        await loadBranches();
      } catch (requestError) {
        setBranchesError(requestError.message || "The branch could not be removed.");
      } finally {
        setBranchBusyId(null);
      }
    },
    [loadBranches],
  );

  const loadDepartments = useCallback(async () => {
    setDepartmentsLoading(true);
    setDepartmentsError("");

    try {
      const result = await listBusinessDepartmentsRequest();
      const items = Array.isArray(result?.items) ? result.items : [];

      setDepartments(items.map(departmentRow));
      setDirtyDepartments([]);
    } catch (requestError) {
      setDepartmentsError(
        requestError.message || "The sections could not be loaded.",
      );
    } finally {
      setDepartmentsLoading(false);
    }
  }, []);

  const applyPolicy = useCallback((result) => {
    setPolicy(result);
    setPolicyDrafts(policyDraftsFrom(result));
  }, []);

  const loadPolicy = useCallback(async () => {
    setPolicyLoading(true);
    setPolicyError("");

    try {
      applyPolicy(await getReplyPolicyRequest());
    } catch (requestError) {
      setPolicyError(
        requestError.message || "The reply policy could not be loaded.",
      );
    } finally {
      setPolicyLoading(false);
    }
  }, [applyPolicy]);

  useEffect(() => {
    loadProfile();
    loadDepartments();
    loadBranches();
    loadPolicy();
  }, [loadProfile, loadDepartments, loadBranches, loadPolicy]);

  const overridePolicyField = useCallback(
    (scopeKey, field, overridden, inherited) => {
      setPolicyStatus("");
      setPolicyError("");
      setPolicyDrafts((current) => {
        const draft = { ...(current[scopeKey] || {}) };

        if (overridden) {
          // Starts from what it was inheriting, so switching a setting to
          // "set here" changes nothing until the value itself is changed.
          draft[field.key] = inherited;
        } else {
          delete draft[field.key];
        }

        return { ...current, [scopeKey]: draft };
      });
    },
    [],
  );

  const changePolicyField = useCallback((scopeKey, field, value) => {
    setPolicyStatus("");
    setPolicyError("");
    setPolicyDrafts((current) => ({
      ...current,
      [scopeKey]: { ...(current[scopeKey] || {}), [field.key]: value },
    }));
  }, []);

  const savePolicyScope = useCallback(
    async (scope) => {
      const draft = policyDrafts[scope.key] || {};
      const fields = policy?.fields || [];

      let values;

      try {
        values = policyValuesFromDraft(draft, fields);
      } catch (localError) {
        setPolicyError(localError.message);
        return;
      }

      // Anything this scope had stored and the form no longer sets goes back
      // to inheriting. Without this a cleared row would keep its old value.
      const clear = Object.keys(scope.overrides || {}).filter(
        (key) => !(key in draft),
      );

      setPolicyBusyScope(scope.key);
      setPolicyError("");
      setPolicyStatus("");

      try {
        const result = scope.channel
          ? await updateReplyPolicyChannelRequest(scope.channel, {
              values,
              clear,
            })
          : await updateReplyPolicyDefaultRequest({ values, clear });

        applyPolicy(result);
        setPolicyStatus(
          `Saved. ${scope.title} applies from the next customer message.`,
        );
      } catch (requestError) {
        setPolicyError(
          requestError.message || "The reply policy could not be saved.",
        );
      } finally {
        setPolicyBusyScope(null);
      }
    },
    [applyPolicy, policy, policyDrafts],
  );

  const clearPolicyChannel = useCallback(
    async (scope) => {
      if (!scope.channel) {
        return;
      }

      setPolicyBusyScope(scope.key);
      setPolicyError("");
      setPolicyStatus("");

      try {
        applyPolicy(await clearReplyPolicyChannelRequest(scope.channel));
        setPolicyStatus(
          `${scope.title} inherits your default again — nothing is kept behind.`,
        );
      } catch (requestError) {
        setPolicyError(
          requestError.message || "The override could not be cleared.",
        );
      } finally {
        setPolicyBusyScope(null);
      }
    },
    [applyPolicy],
  );

  const markDepartmentDirty = useCallback((departmentId) => {
    setDepartmentsStatus("");
    setDirtyDepartments((current) =>
      current.includes(departmentId) ? current : [...current, departmentId],
    );
  }, []);

  const updateDepartmentField = useCallback(
    (departmentId, key, value) => {
      markDepartmentDirty(departmentId);
      setDepartments((current) =>
        current.map((row) =>
          row.id === departmentId ? { ...row, [key]: value } : row,
        ),
      );
    },
    [markDepartmentDirty],
  );

  const saveDepartment = useCallback(async (row) => {
    if (!(row.code || "").trim()) {
      setDepartmentsError(
        "A section needs a code — it is what the assistant routes on.",
      );
      return;
    }

    setDepartmentBusyId(row.id);
    setDepartmentsError("");
    setDepartmentsStatus("");

    try {
      const result = await updateBusinessDepartmentRequest(
        row.id,
        departmentPayload(row),
      );

      const saved = departmentRow(result.department);

      setDepartments((current) =>
        current.map((item) => (item.id === saved.id ? saved : item)),
      );
      setDirtyDepartments((current) => current.filter((id) => id !== row.id));
      setDepartmentsStatus("Saved. Customers see this from the next message.");
    } catch (requestError) {
      setDepartmentsError(
        requestError.message || "The section could not be saved.",
      );
    } finally {
      setDepartmentBusyId(null);
    }
  }, []);

  const toggleDepartmentEnabled = useCallback(async (row) => {
    const enabled = !row.enabled;

    setDepartmentBusyId(row.id);
    setDepartmentsError("");
    setDepartmentsStatus("");

    try {
      const result = await updateBusinessDepartmentRequest(row.id, {
        enabled,
      });

      const saved = departmentRow(result.department);

      setDepartments((current) =>
        current.map((item) =>
          item.id === saved.id ? { ...item, enabled: saved.enabled } : item,
        ),
      );
      setDepartmentsStatus(
        enabled
          ? "Section switched on. It is offered again from the next message."
          : "Section switched off. It is no longer offered to customers.",
      );
    } catch (requestError) {
      setDepartmentsError(
        requestError.message || "The section could not be updated.",
      );
    } finally {
      setDepartmentBusyId(null);
    }
  }, []);

  const moveDepartment = useCallback(
    async (index, offset) => {
      const target = index + offset;

      if (target < 0 || target >= departments.length) {
        return;
      }

      const reordered = [...departments];
      const [moved] = reordered.splice(index, 1);
      reordered.splice(target, 0, moved);

      // Shown in the new order straight away; the server call below is what
      // makes it stick, and a failure reloads the real order rather than
      // leaving the screen claiming an order the customer will not see.
      setDepartments(reordered);
      setDepartmentsError("");
      setDepartmentsStatus("");

      try {
        await reorderBusinessDepartmentsRequest(reordered.map((row) => row.id));
      } catch (requestError) {
        setDepartmentsError(
          requestError.message || "The order could not be saved.",
        );
        loadDepartments();
      }
    },
    [departments, loadDepartments],
  );

  const handleCreateDepartment = useCallback(async () => {
    if (!newDepartment) {
      return;
    }

    if (!(newDepartment.code || "").trim()) {
      setDepartmentsError(
        "A section needs a code — it is what the assistant routes on.",
      );
      return;
    }

    setCreatingDepartment(true);
    setDepartmentsError("");
    setDepartmentsStatus("");

    try {
      const result = await createBusinessDepartmentRequest(
        departmentPayload(newDepartment),
      );

      setDepartments((current) => [
        ...current,
        departmentRow(result.department),
      ]);
      setNewDepartment(null);
      setDepartmentsStatus("Section added.");
    } catch (requestError) {
      setDepartmentsError(
        requestError.message || "The section could not be added.",
      );
    } finally {
      setCreatingDepartment(false);
    }
  }, [newDepartment]);

  const handleDeleteDepartment = useCallback(async () => {
    if (!pendingDepartmentDelete) {
      return;
    }

    setDeletingDepartment(true);
    setDepartmentsError("");

    try {
      await deleteBusinessDepartmentRequest(pendingDepartmentDelete.id);

      setDepartments((current) =>
        current.filter((row) => row.id !== pendingDepartmentDelete.id),
      );
      setPendingDepartmentDelete(null);
      setDepartmentsStatus("Section removed.");
    } catch (requestError) {
      setDepartmentsError(
        requestError.message || "The section could not be removed.",
      );
    } finally {
      setDeletingDepartment(false);
    }
  }, [pendingDepartmentDelete]);

  const updateField = useCallback((key, value) => {
    setSaveStatus("");
    setSaveError("");
    setForm((current) => ({ ...current, [key]: value }));
  }, []);

  const updateExample = useCallback((index, key, value) => {
    setSaveStatus("");
    setSaveError("");
    setForm((current) => ({
      ...current,
      examples: current.examples.map((item, position) =>
        position === index ? { ...item, [key]: value } : item,
      ),
    }));
  }, []);

  const addExample = useCallback(() => {
    setSaveStatus("");
    setForm((current) =>
      current.examples.length >= MAX_EXAMPLES
        ? current
        : {
            ...current,
            examples: [...current.examples, { customer: "", reply: "" }],
          },
    );
  }, []);

  const removeExample = useCallback((index) => {
    setSaveStatus("");
    setForm((current) => ({
      ...current,
      examples: current.examples.filter((_, position) => position !== index),
    }));
  }, []);

  const handleSave = useCallback(
    async (event) => {
      event?.preventDefault?.();

      setSaving(true);
      setSaveStatus("");
      setSaveError("");

      try {
        const result = await updateAiTeachingProfileRequest(
          payloadFromForm(form),
        );

        setForm(formFromProfile(result?.profile));
        setSaveStatus("Saved. The assistant uses this from the next message.");

        if (promptOpen) {
          setPromptText("");
          setPromptOpen(false);
        }
      } catch (requestError) {
        setSaveError(requestError.message || "The profile could not be saved.");
      } finally {
        setSaving(false);
      }
    },
    [form, promptOpen],
  );

  const handleDryRun = useCallback(
    async (event) => {
      event?.preventDefault?.();

      const message = testMessage.trim();

      if (!message) {
        setTestError("Type the message a customer would send.");
        return;
      }

      setTestRunning(true);
      setTestError("");

      try {
        const result = await runAiTeachingDryRunRequest({
          message,
          channel: testChannel,
        });

        setTestResult(result);
      } catch (requestError) {
        setTestResult(null);
        setTestError(requestError.message || "The test could not be run.");
      } finally {
        setTestRunning(false);
      }
    },
    [testMessage, testChannel],
  );

  const handleTogglePrompt = useCallback(async () => {
    if (promptOpen) {
      setPromptOpen(false);
      return;
    }

    setPromptOpen(true);
    setPromptLoading(true);
    setPromptError("");

    try {
      const result = await getAiTeachingPromptRequest(testChannel);
      setPromptText(result?.prompt || "");
    } catch (requestError) {
      setPromptError(requestError.message || "The prompt could not be loaded.");
    } finally {
      setPromptLoading(false);
    }
  }, [promptOpen, testChannel]);

  if (loading) {
    return (
      <div className="ai-teaching-page">
        <LoadingState
          title="Loading the assistant profile..."
          description="Reading how this company's bot is taught to speak."
        />
      </div>
    );
  }

  if (error) {
    return (
      <div className="ai-teaching-page">
        <ErrorState
          title="AI teaching could not load"
          description={error}
          action={
            <AppButton variant="primary" onClick={loadProfile}>
              Try again
            </AppButton>
          }
        />
      </div>
    );
  }

  return (
    <div className="ai-teaching-page">
      <PageHeader
        eyebrow="AI TEACHING"
        title="Teach your assistant"
        description="Tone, instructions and examples are sent to the model on every customer message. Test a message here before a customer ever sees the answer."
        actions={
          <>
            <AppButton
              variant="ghost"
              icon={<RefreshOutlined />}
              onClick={loadProfile}
              disabled={saving}
            >
              Reload
            </AppButton>

            <AppButton variant="primary" onClick={handleSave} loading={saving}>
              Save changes
            </AppButton>
          </>
        }
      />

      {saveError ? (
        <p className="ai-teaching-alert is-error">{saveError}</p>
      ) : null}

      {saveStatus ? (
        <p className="ai-teaching-alert is-ok">{saveStatus}</p>
      ) : null}

      <div className="ai-teaching-layout">
        <div className="ai-teaching-main">
          <form className="ai-teaching-profile-form" onSubmit={handleSave}>
            <AppCard padding="medium">
              <header className="ai-teaching-section-head">
                <span>VOICE</span>
                <h3>How the assistant sounds</h3>
              </header>

              <div className="ai-teaching-grid">
                <label htmlFor="ai-teaching-name">
                  <span>Profile name</span>

                  <input
                    id="ai-teaching-name"
                    type="text"
                    value={form.name}
                    maxLength={120}
                    placeholder="Default Assistant"
                    onChange={(event) =>
                      updateField("name", event.target.value)
                    }
                  />
                </label>

                <label htmlFor="ai-teaching-tone">
                  <span>Tone</span>

                  <input
                    id="ai-teaching-tone"
                    type="text"
                    list="ai-teaching-tone-options"
                    value={form.tone}
                    maxLength={60}
                    placeholder="friendly"
                    onChange={(event) =>
                      updateField("tone", event.target.value)
                    }
                  />

                  <datalist id="ai-teaching-tone-options">
                    {tones.map((tone) => (
                      <option key={tone} value={tone} />
                    ))}
                  </datalist>
                </label>

                <label htmlFor="ai-teaching-language">
                  <span>Default language</span>

                  <select
                    id="ai-teaching-language"
                    value={form.default_language}
                    onChange={(event) =>
                      updateField("default_language", event.target.value)
                    }
                  >
                    <option value="ar">Arabic</option>
                    <option value="en">English</option>
                  </select>
                </label>
              </div>
            </AppCard>

            <AppCard padding="medium">
              <header className="ai-teaching-section-head">
                <span>INSTRUCTIONS</span>
                <h3>What the assistant must always do</h3>
              </header>

              <p className="ai-teaching-note">
                Written in your own words. These outrank the platform&apos;s
                generic guidance, but never the safety rules: the assistant
                still refuses to invent prices, stock or policies.
              </p>

              <label className="ai-teaching-block" htmlFor="ai-teaching-prompt">
                <span>System instructions</span>

                <textarea
                  id="ai-teaching-prompt"
                  rows={8}
                  maxLength={6000}
                  value={form.system_prompt}
                  placeholder={
                    "Always confirm the branch before quoting delivery.\n" +
                    "Ask for the order number when a customer mentions a repair."
                  }
                  onChange={(event) =>
                    updateField("system_prompt", event.target.value)
                  }
                />
              </label>

              <div className="ai-teaching-prompt-preview">
                <AppButton
                  variant="ghost"
                  size="small"
                  onClick={handleTogglePrompt}
                >
                  {promptOpen ? "Hide the full prompt" : "Show the full prompt"}
                </AppButton>

                {promptOpen ? (
                  <div className="ai-teaching-prompt-box">
                    {promptLoading ? (
                      <p className="ai-teaching-note">Loading the prompt...</p>
                    ) : promptError ? (
                      <p className="ai-teaching-alert is-error">
                        {promptError}
                      </p>
                    ) : (
                      <pre>{promptText}</pre>
                    )}
                  </div>
                ) : null}
              </div>
            </AppCard>

            <AppCard padding="medium">
              <header className="ai-teaching-section-head">
                <span>WELCOME</span>
                <h3>The first thing a new customer reads</h3>
              </header>

              <label
                className="ai-teaching-toggle"
                htmlFor="ai-teaching-welcome"
              >
                <input
                  id="ai-teaching-welcome"
                  type="checkbox"
                  checked={form.welcome_enabled}
                  onChange={(event) =>
                    updateField("welcome_enabled", event.target.checked)
                  }
                />

                <span>Send a welcome message</span>
              </label>

              <div className="ai-teaching-grid">
                <label htmlFor="ai-teaching-welcome-ar">
                  <span>Arabic</span>

                  <textarea
                    id="ai-teaching-welcome-ar"
                    rows={3}
                    maxLength={1000}
                    value={form.welcome_message_ar}
                    disabled={!form.welcome_enabled}
                    onChange={(event) =>
                      updateField("welcome_message_ar", event.target.value)
                    }
                  />
                </label>

                <label htmlFor="ai-teaching-welcome-en">
                  <span>English</span>

                  <textarea
                    id="ai-teaching-welcome-en"
                    rows={3}
                    maxLength={1000}
                    value={form.welcome_message_en}
                    disabled={!form.welcome_enabled}
                    onChange={(event) =>
                      updateField("welcome_message_en", event.target.value)
                    }
                  />
                </label>
              </div>
            </AppCard>

            <AppCard padding="medium">
              <header className="ai-teaching-section-head">
                <div>
                  <span>EXAMPLES</span>
                  <h3>Answer questions like these</h3>
                </div>

                <AppButton
                  variant="secondary"
                  size="small"
                  icon={<AddOutlined />}
                  onClick={addExample}
                  disabled={form.examples.length >= MAX_EXAMPLES}
                >
                  Add example
                </AppButton>
              </header>

              {form.examples.length ? (
                <ul className="ai-teaching-examples">
                  {form.examples.map((example, index) => (
                    <li key={`example-${index}`}>
                      <label htmlFor={`ai-teaching-example-customer-${index}`}>
                        <span>Customer says</span>

                        <input
                          id={`ai-teaching-example-customer-${index}`}
                          type="text"
                          maxLength={1000}
                          value={example.customer}
                          placeholder="Do you deliver to Tripoli?"
                          onChange={(event) =>
                            updateExample(index, "customer", event.target.value)
                          }
                        />
                      </label>

                      <label htmlFor={`ai-teaching-example-reply-${index}`}>
                        <span>Assistant answers</span>

                        <input
                          id={`ai-teaching-example-reply-${index}`}
                          type="text"
                          maxLength={1000}
                          value={example.reply}
                          placeholder="Yes, delivery takes two days."
                          onChange={(event) =>
                            updateExample(index, "reply", event.target.value)
                          }
                        />
                      </label>

                      <AppButton
                        variant="ghost"
                        size="small"
                        icon={<DeleteOutlineOutlined />}
                        onClick={() => removeExample(index)}
                      >
                        Remove
                      </AppButton>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="ai-teaching-note">
                  No examples yet. One or two real conversations teach the
                  assistant more about your voice than a page of instructions.
                </p>
              )}

              <p className="ai-teaching-note">
                Empty rows are dropped when you save. Up to {MAX_EXAMPLES}{" "}
                examples: every one of them is sent with each customer message.
              </p>
            </AppCard>
          </form>

          <AppCard padding="medium">
            <header className="ai-teaching-section-head">
              <div>
                <span>SECTIONS</span>
                <h3>The parts of your business customers can ask for</h3>
              </div>

              <AppButton
                variant="secondary"
                size="small"
                icon={<AddOutlined />}
                onClick={() => {
                  setDepartmentsStatus("");
                  setDepartmentsError("");
                  setNewDepartment(emptyDepartment());
                }}
                disabled={
                  Boolean(newDepartment) ||
                  departments.length >= MAX_DEPARTMENTS
                }
              >
                Add section
              </AppButton>
            </header>

            <p className="ai-teaching-note">
              These are yours alone. They become the menu the assistant offers,
              the quick-reply buttons a customer taps, and the list of
              departments the model is told about. Define none and no menu is
              shown — nothing is filled in from any other business.
            </p>

            {departmentsError ? (
              <p className="ai-teaching-alert is-error">{departmentsError}</p>
            ) : null}

            {departmentsStatus ? (
              <p className="ai-teaching-alert is-ok">{departmentsStatus}</p>
            ) : null}

            {departmentsLoading ? (
              <p className="ai-teaching-note">Loading your sections...</p>
            ) : (
              <ul className="ai-teaching-departments">
                {departments.map((row, index) => (
                  <li key={row.id} className={row.enabled ? "" : "is-off"}>
                    <div className="ai-teaching-department-fields">
                      <label htmlFor={`ai-teaching-department-code-${row.id}`}>
                        <span>Code</span>

                        <input
                          id={`ai-teaching-department-code-${row.id}`}
                          type="text"
                          maxLength={60}
                          value={row.code}
                          placeholder="sales"
                          onChange={(event) =>
                            updateDepartmentField(
                              row.id,
                              "code",
                              event.target.value,
                            )
                          }
                        />
                      </label>

                      <label
                        htmlFor={`ai-teaching-department-name-ar-${row.id}`}
                      >
                        <span>Name (Arabic)</span>

                        <input
                          id={`ai-teaching-department-name-ar-${row.id}`}
                          type="text"
                          maxLength={120}
                          value={row.name_ar}
                          onChange={(event) =>
                            updateDepartmentField(
                              row.id,
                              "name_ar",
                              event.target.value,
                            )
                          }
                        />
                      </label>

                      <label
                        htmlFor={`ai-teaching-department-name-en-${row.id}`}
                      >
                        <span>Name (English)</span>

                        <input
                          id={`ai-teaching-department-name-en-${row.id}`}
                          type="text"
                          maxLength={120}
                          value={row.name_en}
                          onChange={(event) =>
                            updateDepartmentField(
                              row.id,
                              "name_en",
                              event.target.value,
                            )
                          }
                        />
                      </label>

                      <label
                        htmlFor={`ai-teaching-department-button-ar-${row.id}`}
                      >
                        <span>Button (Arabic)</span>

                        <input
                          id={`ai-teaching-department-button-ar-${row.id}`}
                          type="text"
                          maxLength={60}
                          value={row.button_ar}
                          onChange={(event) =>
                            updateDepartmentField(
                              row.id,
                              "button_ar",
                              event.target.value,
                            )
                          }
                        />
                      </label>

                      <label
                        htmlFor={`ai-teaching-department-button-en-${row.id}`}
                      >
                        <span>Button (English)</span>

                        <input
                          id={`ai-teaching-department-button-en-${row.id}`}
                          type="text"
                          maxLength={60}
                          value={row.button_en}
                          onChange={(event) =>
                            updateDepartmentField(
                              row.id,
                              "button_en",
                              event.target.value,
                            )
                          }
                        />
                      </label>
                    </div>

                    <div className="ai-teaching-department-actions">
                      <AppButton
                        variant="ghost"
                        size="small"
                        icon={<ArrowUpwardOutlined />}
                        disabled={index === 0}
                        onClick={() => moveDepartment(index, -1)}
                      >
                        Up
                      </AppButton>

                      <AppButton
                        variant="ghost"
                        size="small"
                        icon={<ArrowDownwardOutlined />}
                        disabled={index === departments.length - 1}
                        onClick={() => moveDepartment(index, 1)}
                      >
                        Down
                      </AppButton>

                      <AppButton
                        variant="secondary"
                        size="small"
                        onClick={() => toggleDepartmentEnabled(row)}
                        loading={departmentBusyId === row.id}
                      >
                        {row.enabled ? "Switch off" : "Switch on"}
                      </AppButton>

                      <AppButton
                        variant="primary"
                        size="small"
                        onClick={() => saveDepartment(row)}
                        disabled={!dirtyDepartments.includes(row.id)}
                        loading={departmentBusyId === row.id}
                      >
                        Save
                      </AppButton>

                      <AppButton
                        variant="ghost"
                        size="small"
                        icon={<DeleteOutlineOutlined />}
                        onClick={() => setPendingDepartmentDelete(row)}
                      >
                        Remove
                      </AppButton>
                    </div>
                  </li>
                ))}

                {newDepartment ? (
                  <li className="is-new">
                    <div className="ai-teaching-department-fields">
                      <label htmlFor="ai-teaching-new-department-code">
                        <span>Code</span>

                        <input
                          id="ai-teaching-new-department-code"
                          type="text"
                          maxLength={60}
                          value={newDepartment.code}
                          placeholder="sales"
                          onChange={(event) =>
                            setNewDepartment((current) => ({
                              ...current,
                              code: event.target.value,
                            }))
                          }
                        />
                      </label>

                      <label htmlFor="ai-teaching-new-department-name-ar">
                        <span>Name (Arabic)</span>

                        <input
                          id="ai-teaching-new-department-name-ar"
                          type="text"
                          maxLength={120}
                          value={newDepartment.name_ar}
                          onChange={(event) =>
                            setNewDepartment((current) => ({
                              ...current,
                              name_ar: event.target.value,
                            }))
                          }
                        />
                      </label>

                      <label htmlFor="ai-teaching-new-department-name-en">
                        <span>Name (English)</span>

                        <input
                          id="ai-teaching-new-department-name-en"
                          type="text"
                          maxLength={120}
                          value={newDepartment.name_en}
                          onChange={(event) =>
                            setNewDepartment((current) => ({
                              ...current,
                              name_en: event.target.value,
                            }))
                          }
                        />
                      </label>

                      <label htmlFor="ai-teaching-new-department-button-ar">
                        <span>Button (Arabic)</span>

                        <input
                          id="ai-teaching-new-department-button-ar"
                          type="text"
                          maxLength={60}
                          value={newDepartment.button_ar}
                          onChange={(event) =>
                            setNewDepartment((current) => ({
                              ...current,
                              button_ar: event.target.value,
                            }))
                          }
                        />
                      </label>

                      <label htmlFor="ai-teaching-new-department-button-en">
                        <span>Button (English)</span>

                        <input
                          id="ai-teaching-new-department-button-en"
                          type="text"
                          maxLength={60}
                          value={newDepartment.button_en}
                          onChange={(event) =>
                            setNewDepartment((current) => ({
                              ...current,
                              button_en: event.target.value,
                            }))
                          }
                        />
                      </label>
                    </div>

                    <div className="ai-teaching-department-actions">
                      <AppButton
                        variant="primary"
                        size="small"
                        onClick={handleCreateDepartment}
                        loading={creatingDepartment}
                      >
                        Add section
                      </AppButton>

                      <AppButton
                        variant="ghost"
                        size="small"
                        onClick={() => setNewDepartment(null)}
                      >
                        Cancel
                      </AppButton>
                    </div>
                  </li>
                ) : null}
              </ul>
            )}

            {!departmentsLoading && !departments.length && !newDepartment ? (
              <p className="ai-teaching-note">
                No sections yet, so the assistant shows no menu at all. Add the
                parts of your business a customer would ask for — sales,
                support, deliveries — and they appear as buttons on the next
                message.
              </p>
            ) : null}

            <p className="ai-teaching-note">
              The code is what the assistant routes on, so keep it short and
              unchanged once conversations use it. A section with no button
              label is still listed by name but is not offered as a button. Up
              to {MAX_DEPARTMENTS} sections.
            </p>
          </AppCard>

          {/* Next to sections, because both are lists a company defines about
              itself. What they do is different and deliberately so: a section
              is offered to a customer and routes a conversation, a branch is
              only ever a label — it filters the inbox and the channel list and
              decides nothing the customer sees. */}
          <AppCard padding="medium">
            <header className="ai-teaching-section-head">
              <div>
                <span>BRANCHES</span>
                <h3>Your locations, for filtering</h3>
              </div>

              <AppButton
                variant="secondary"
                size="small"
                icon={<AddOutlined />}
                onClick={() => {
                  setBranchesStatus("");
                  setBranchesError("");
                  setNewBranch({ name: "", code: "", address: "", phone: "" });
                }}
                disabled={Boolean(newBranch)}
              >
                Add branch
              </AppButton>
            </header>

            <p className="ai-teaching-note">
              A shop, an office, a warehouse. Naming one lets you say which
              location an employee works at and which one a connected channel
              belongs to, and filter both lists by it. Nothing a customer sees
              changes. A company with a single location needs none of this.
            </p>

            {branchesError ? (
              <p className="ai-teaching-alert is-error">{branchesError}</p>
            ) : null}

            {branchesStatus ? (
              <p className="ai-teaching-alert is-ok">{branchesStatus}</p>
            ) : null}

            <ul className="ai-teaching-departments">
              {branches.map((row) => (
                <li key={row.id}>
                  <div className="ai-teaching-department-fields">
                    <label htmlFor={`ai-teaching-branch-name-${row.id}`}>
                      <span>Name</span>

                      <input
                        id={`ai-teaching-branch-name-${row.id}`}
                        type="text"
                        maxLength={120}
                        defaultValue={row.name}
                        disabled={branchBusyId === row.id}
                        onBlur={(event) =>
                          handleRenameBranch(row, event.target.value)
                        }
                      />
                    </label>

                    <label htmlFor={`ai-teaching-branch-code-${row.id}`}>
                      <span>Code</span>

                      <input
                        id={`ai-teaching-branch-code-${row.id}`}
                        type="text"
                        value={row.code || ""}
                        readOnly
                      />
                    </label>
                  </div>

                  <div className="ai-teaching-department-actions">
                    <AppButton
                      variant="ghost"
                      size="small"
                      icon={<DeleteOutlineOutlined />}
                      loading={branchBusyId === row.id}
                      onClick={() => handleDeleteBranch(row)}
                    >
                      Remove
                    </AppButton>
                  </div>
                </li>
              ))}

              {newBranch ? (
                <li className="is-new">
                  <div className="ai-teaching-department-fields">
                    <label htmlFor="ai-teaching-new-branch-name">
                      <span>Name</span>

                      <input
                        id="ai-teaching-new-branch-name"
                        type="text"
                        maxLength={120}
                        value={newBranch.name}
                        placeholder="Downtown"
                        onChange={(event) =>
                          setNewBranch((current) => ({
                            ...current,
                            name: event.target.value,
                          }))
                        }
                      />
                    </label>

                    <label htmlFor="ai-teaching-new-branch-code">
                      <span>Code</span>

                      <input
                        id="ai-teaching-new-branch-code"
                        type="text"
                        maxLength={40}
                        value={newBranch.code}
                        placeholder="DT"
                        onChange={(event) =>
                          setNewBranch((current) => ({
                            ...current,
                            code: event.target.value,
                          }))
                        }
                      />
                    </label>

                    <label htmlFor="ai-teaching-new-branch-address">
                      <span>Address</span>

                      <input
                        id="ai-teaching-new-branch-address"
                        type="text"
                        maxLength={300}
                        value={newBranch.address}
                        onChange={(event) =>
                          setNewBranch((current) => ({
                            ...current,
                            address: event.target.value,
                          }))
                        }
                      />
                    </label>

                    <label htmlFor="ai-teaching-new-branch-phone">
                      <span>Phone</span>

                      <input
                        id="ai-teaching-new-branch-phone"
                        type="text"
                        maxLength={40}
                        value={newBranch.phone}
                        onChange={(event) =>
                          setNewBranch((current) => ({
                            ...current,
                            phone: event.target.value,
                          }))
                        }
                      />
                    </label>
                  </div>

                  <div className="ai-teaching-department-actions">
                    <AppButton
                      variant="primary"
                      size="small"
                      loading={savingBranch}
                      onClick={handleCreateBranch}
                    >
                      Save
                    </AppButton>

                    <AppButton
                      variant="ghost"
                      size="small"
                      onClick={() => setNewBranch(null)}
                    >
                      Cancel
                    </AppButton>
                  </div>
                </li>
              ) : null}
            </ul>
          </AppCard>

          <AppCard padding="medium">
            <header className="ai-teaching-section-head">
              <div>
                <span>REPLY POLICY</span>
                <h3>How your assistant answers</h3>
              </div>

              <AppButton
                variant="ghost"
                size="small"
                icon={<RefreshOutlined />}
                onClick={loadPolicy}
                disabled={Boolean(policyBusyScope)}
              >
                Reload
              </AppButton>
            </header>

            <p className="ai-teaching-note">
              Yours alone. Set your own default and, where a channel needs to
              behave differently, override just that channel. Anything you do
              not set inherits — your default from the platform&apos;s, a
              channel from your default — and can be put back to inheriting at
              any time.
            </p>

            {policyError ? (
              <p className="ai-teaching-alert is-error">{policyError}</p>
            ) : null}

            {policyStatus ? (
              <p className="ai-teaching-alert is-ok">{policyStatus}</p>
            ) : null}

            {policyLoading ? (
              <p className="ai-teaching-note">Loading your reply policy...</p>
            ) : (
              policyScopes(policy).map((scope) => {
                const draft = policyDrafts[scope.key] || {};
                const locked = (policy?.locked_keys || []).includes(
                  scope.channel ? "channels" : "default",
                );
                const overrideCount = Object.keys(scope.overrides || {}).length;
                const busy = policyBusyScope === scope.key;

                const fields = (
                  <>
                    <ul className="ai-teaching-policy-fields">
                      {(policy?.fields || []).map((field) => (
                        <PolicyField
                          key={`${scope.key}-${field.key}`}
                          field={field}
                          scope={scope}
                          draft={draft}
                          disabled={locked || busy}
                          onOverride={(policyField, overridden, inherited) =>
                            overridePolicyField(
                              scope.key,
                              policyField,
                              overridden,
                              inherited,
                            )
                          }
                          onChange={(policyField, value) =>
                            changePolicyField(scope.key, policyField, value)
                          }
                        />
                      ))}
                    </ul>

                    {locked ? (
                      <p className="ai-teaching-note">
                        Locked by the platform administrator, so it is shown
                        here but cannot be changed from this screen.
                      </p>
                    ) : (
                      <div className="ai-teaching-policy-actions">
                        {scope.channel ? (
                          <AppButton
                            variant="ghost"
                            size="small"
                            onClick={() => clearPolicyChannel(scope)}
                            disabled={!overrideCount}
                            loading={busy}
                          >
                            Inherit everything
                          </AppButton>
                        ) : null}

                        <AppButton
                          variant="primary"
                          size="small"
                          onClick={() => savePolicyScope(scope)}
                          disabled={!policyScopeDirty(scope, draft)}
                          loading={busy}
                        >
                          Save
                        </AppButton>
                      </div>
                    )}
                  </>
                );

                if (!scope.channel) {
                  return (
                    <section
                      key={scope.key}
                      className="ai-teaching-policy-scope"
                    >
                      <h4>{scope.title}</h4>

                      <p className="ai-teaching-note">{scope.subtitle}</p>

                      {fields}
                    </section>
                  );
                }

                return (
                  <details
                    key={scope.key}
                    className="ai-teaching-policy-scope is-channel"
                  >
                    <summary>
                      <span>{scope.title}</span>

                      <small>
                        {overrideCount
                          ? `${overrideCount} setting${
                              overrideCount === 1 ? "" : "s"
                            } set here`
                          : "Inherits your default"}
                      </small>
                    </summary>

                    {fields}
                  </details>
                );
              })
            )}
          </AppCard>
        </div>

        <aside className="ai-teaching-side">
          <AppCard padding="medium" className="ai-teaching-test-card">
            <header className="ai-teaching-section-head">
              <span>TEST</span>
              <h3>Try it before a customer does</h3>
            </header>

            <p className="ai-teaching-note">
              Runs the real assistant for this company. Nothing is sent to a
              channel, saved to the inbox, queued, or added to any conversation.
            </p>

            <form className="ai-teaching-test-form" onSubmit={handleDryRun}>
              <label htmlFor="ai-teaching-test-channel">
                <span>Channel</span>

                <select
                  id="ai-teaching-test-channel"
                  value={testChannel}
                  onChange={(event) => setTestChannel(event.target.value)}
                >
                  {channels.map((channel) => (
                    <option key={channel} value={channel}>
                      {channel}
                    </option>
                  ))}
                </select>
              </label>

              <label htmlFor="ai-teaching-test-message">
                <span>Customer message</span>

                <textarea
                  id="ai-teaching-test-message"
                  rows={4}
                  maxLength={2000}
                  value={testMessage}
                  placeholder="مرحبا، بدي أعرف إذا عندكم توصيل"
                  onChange={(event) => {
                    setTestError("");
                    setTestMessage(event.target.value);
                  }}
                />
              </label>

              <AppButton
                type="submit"
                variant="primary"
                fullWidth
                icon={<PlayArrowOutlined />}
                loading={testRunning}
              >
                Run test
              </AppButton>
            </form>

            {testError ? (
              <p className="ai-teaching-alert is-error">{testError}</p>
            ) : null}

            {testResult ? (
              <div className="ai-teaching-result">
                <strong>Assistant reply</strong>

                <p className="ai-teaching-reply">{testResult.reply}</p>

                {Array.isArray(testResult.buttons) &&
                testResult.buttons.length ? (
                  <ul className="ai-teaching-buttons">
                    {testResult.buttons.map((label) => (
                      <li key={label}>{label}</li>
                    ))}
                  </ul>
                ) : null}

                <small className="ai-teaching-note">{testResult.note}</small>

                <small className="ai-teaching-note">
                  Profile: {testResult.profile_name || "default"} · not sent ·
                  not saved
                </small>
              </div>
            ) : null}
          </AppCard>
        </aside>
      </div>

      <ConfirmDialog
        open={Boolean(pendingDepartmentDelete)}
        title="Remove this section?"
        message={
          <span>
            Customers will stop being offered{" "}
            <strong>
              {pendingDepartmentDelete?.name_en ||
                pendingDepartmentDelete?.name_ar ||
                pendingDepartmentDelete?.code ||
                "this section"}
            </strong>{" "}
            immediately. Switch it off instead if you only want to hide it for
            now.
          </span>
        }
        confirmLabel="Remove section"
        loading={deletingDepartment}
        onConfirm={handleDeleteDepartment}
        onCancel={() => setPendingDepartmentDelete(null)}
      />
    </div>
  );
}
