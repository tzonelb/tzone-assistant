import { useCallback, useEffect, useState } from "react";
import {
  AddOutlined,
  DeleteOutlineOutlined,
  PlayArrowOutlined,
  RefreshOutlined,
} from "@mui/icons-material";

import {
  getAiTeachingProfileRequest,
  getAiTeachingPromptRequest,
  runAiTeachingDryRunRequest,
  updateAiTeachingProfileRequest,
} from "../../api/aiTeaching";
import {
  AppButton,
  AppCard,
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
  "website_chat",
];

const MAX_EXAMPLES = 20;

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

  useEffect(() => {
    loadProfile();
  }, [loadProfile]);

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
      setPromptError(
        requestError.message || "The prompt could not be loaded.",
      );
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
        <form className="ai-teaching-main" onSubmit={handleSave}>
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
                  onChange={(event) => updateField("name", event.target.value)}
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
                  onChange={(event) => updateField("tone", event.target.value)}
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
              generic guidance, but never the safety rules: the assistant still
              refuses to invent prices, stock or policies.
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
                    <p className="ai-teaching-alert is-error">{promptError}</p>
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

            <label className="ai-teaching-toggle" htmlFor="ai-teaching-welcome">
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
    </div>
  );
}
