import { useEffect, useRef, useState } from "react";
import { ArrowDropDownOutlined } from "@mui/icons-material";
import MultiSelectChips from "./MultiSelectChips";

// Compact header/table-cell control: a button showing a summary ("All
// channels", "WhatsApp, Telegram") that opens a MultiSelectChips
// popover. Same multi-select data underneath, just space-efficient.
export default function MultiSelectPopover({ label, options, value, onChange, allLabel, emptyHint, disabled }) {
  const [open, setOpen] = useState(false);
  const [panelStyle, setPanelStyle] = useState(null);
  const ref = useRef(null);
  const triggerRef = useRef(null);

  useEffect(() => {
    function onClickOutside(event) {
      if (ref.current && !ref.current.contains(event.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  // The panel uses position:fixed (computed from the trigger's own
  // viewport rect) instead of a plain absolute child, since this
  // control is also used inside table cells whose scroll wrapper has
  // overflow:auto — an absolutely positioned child there gets clipped
  // by the table wrapper before it can render below the row.
  useEffect(() => {
    if (!open || !triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    setPanelStyle({ position: "fixed", top: rect.bottom + 6, left: rect.left });
  }, [open]);

  const summary = value.length === 0 ? allLabel : value.map((v) => options.find((o) => o.value === v)?.label || v).join(", ");

  return (
    <div className="reply-flow-multiselect-popover" ref={ref}>
      <button type="button" ref={triggerRef} className="reply-flow-multiselect-trigger" disabled={disabled} onClick={() => setOpen((o) => !o)}>
        {label ? <span className="reply-flow-multiselect-trigger-label">{label}:</span> : null}
        <span className="reply-flow-multiselect-trigger-value">{summary}</span>
        <ArrowDropDownOutlined fontSize="small" />
      </button>
      {open ? (
        <div className="reply-flow-multiselect-panel" style={panelStyle || undefined}>
          <MultiSelectChips options={options} value={value} onChange={onChange} disabled={disabled} emptyHint={emptyHint} />
        </div>
      ) : null}
    </div>
  );
}
