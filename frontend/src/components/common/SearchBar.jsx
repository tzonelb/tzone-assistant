import {
  CloseOutlined,
  SearchOutlined,
} from "@mui/icons-material";


export default function SearchBar({
  value,
  placeholder = "Search...",
  ariaLabel = "Search",
  onChange,
  onClear,
}) {
  function handleClear() {
    if (onClear) {
      onClear();
      return;
    }

    onChange?.("");
  }

  return (
    <div className="tz-search">
      <span className="tz-search-icon">
        <SearchOutlined fontSize="small" />
      </span>

      <input
        type="search"
        value={value}
        placeholder={placeholder}
        aria-label={ariaLabel}
        onChange={(event) =>
          onChange?.(event.target.value)
        }
      />

      {value ? (
        <button
          type="button"
          className="tz-search-clear"
          aria-label="Clear search"
          onClick={handleClear}
        >
          <CloseOutlined fontSize="small" />
        </button>
      ) : null}
    </div>
  );
}