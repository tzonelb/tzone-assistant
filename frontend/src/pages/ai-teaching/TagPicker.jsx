export const CHANNEL_OPTIONS = ["messenger", "whatsapp", "instagram", "telegram"];

export function TagPicker({ departments, selectedDepartments, setSelectedDepartments, selectedChannels, setSelectedChannels, extraTagsInput, setExtraTagsInput }) {
  function toggle(list, setList, value) {
    setList(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);
  }
  return (
    <>
      <div>
        <span style={{ fontSize: 12, fontWeight: 600, color: "#374151" }}>Visible to departments (optional — leave all unchecked for every department)</span>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
          {departments.filter((d) => d !== "Unassigned").map((d) => {
            const value = d.toLowerCase();
            const active = selectedDepartments.includes(value);
            return (
              <button key={d} type="button" onClick={() => toggle(selectedDepartments, setSelectedDepartments, value)}
                style={{ fontSize: 12, padding: "5px 12px", borderRadius: 999, border: active ? "1px solid #4F63F0" : "1px solid #d5dae5", background: active ? "#EEF1FE" : "#fff", color: active ? "#4F63F0" : "#374151", cursor: "pointer" }}>
                {d}
              </button>
            );
          })}
        </div>
      </div>
      <div>
        <span style={{ fontSize: 12, fontWeight: 600, color: "#374151" }}>Visible to channels (optional — leave all unchecked for every channel)</span>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
          {CHANNEL_OPTIONS.map((c) => {
            const active = selectedChannels.includes(c);
            return (
              <button key={c} type="button" onClick={() => toggle(selectedChannels, setSelectedChannels, c)}
                style={{ fontSize: 12, padding: "5px 12px", borderRadius: 999, border: active ? "1px solid #17A369" : "1px solid #d5dae5", background: active ? "#E7FAF1" : "#fff", color: active ? "#17A369" : "#374151", cursor: "pointer", textTransform: "capitalize" }}>
                {c}
              </button>
            );
          })}
        </div>
      </div>
      <input value={extraTagsInput} onChange={(e) => setExtraTagsInput(e.target.value)}
        placeholder="Other custom tags, comma-separated (optional) — e.g. vip, ramadan-campaign"
        style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #d5dae5", fontSize: 13 }} />
      <span style={{ fontSize: 12, color: "#9296AC" }}>Nothing checked and no custom tags = applies everywhere.</span>
    </>
  );
}

export function splitTags(tags, departments) {
  const departmentNamesLower = departments.map((d) => d.toLowerCase());
  return {
    departmentTags: tags.filter((t) => departmentNamesLower.includes(t)),
    channelTags: tags.filter((t) => CHANNEL_OPTIONS.includes(t)),
    extraTags: tags.filter((t) => !departmentNamesLower.includes(t) && !CHANNEL_OPTIONS.includes(t)),
  };
}
