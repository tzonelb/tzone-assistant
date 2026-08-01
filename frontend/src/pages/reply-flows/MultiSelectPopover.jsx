import { useEffect, useRef, useState } from "react";
import { ArrowDropDownOutlined } from "@mui/icons-material";
import MultiSelectChips from "./MultiSelectChips";

// Compact header control: a button showing a summary ("All channels",
// "WhatsApp, Telegram") that opens a MultiSelectChips popover. Same
// multi-select data underneath, just space-efficient for a toolbar.
export default function MultiSelectPopover({ label, options, value, onChange, allLabel, emptyHint }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    function onClickOutside(event) {
      if (ref.current && !ref.current.contains(event.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  const summary = value.length === 0 ? allLabel : value.map((v) => options.find((o) => o.value === v)?.label || v).join(", ");

  return (
    <div className="reply-flow-multiselect-popover" ref={ref}>
      <button type="button" className="reply-flow-multiselect-trigger" onClick={() => setOpen((o) => !o)}>
        <span className="reply-flow-multiselect-trigger-label">{label}:</span>
        <span className="reply-flow-multiselect-trigger-value">{summary}</span>
        <ArrowDropDownOutlined fontSize="small" />
      </button>
      {open ? (
        <div className="reply-flow-multiselect-panel">
          <MultiSelectChips options={options} value={value} onChange={onChange} emptyHint={emptyHint} />
        </div>
      ) : null}
    </div>
  );
}
