import "./MultiSelect.css";

// Reusable multi-select for a small, fixed list of options — chips you
// toggle on/off. Used wherever more than one value can legitimately
// apply at once (Reply Flow channels/departments, employee department
// membership, etc.) per the standing rule: never a single-select when
// multi-select is the honest model.
export default function MultiSelectChips({ options, value, onChange, disabled, emptyHint }) {
  function toggle(optionValue) {
    if (disabled) return;
    const next = value.includes(optionValue)
      ? value.filter((item) => item !== optionValue)
      : [...value, optionValue];
    onChange(next);
  }

  if (!options.length) {
    return <p className="reply-flow-multiselect-empty">{emptyHint}</p>;
  }

  return (
    <div className="reply-flow-multiselect">
      {options.map((option) => {
        const selected = value.includes(option.value);
        return (
          <button
            type="button"
            key={option.value}
            className={`reply-flow-multiselect-chip ${selected ? "is-selected" : ""}`}
            disabled={disabled}
            onClick={() => toggle(option.value)}
            aria-pressed={selected}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
