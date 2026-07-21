export default function AppButton({
  children,
  type = "button",
  variant = "primary",
  size = "medium",
  fullWidth = false,
  disabled = false,
  loading = false,
  icon = null,
  className = "",
  onClick,
}) {
  const classes = [
    "tz-button",
    `tz-button-${variant}`,
    size === "small" ? "tz-button-small" : "",
    size === "large" ? "tz-button-large" : "",
    fullWidth ? "tz-button-full" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      type={type}
      className={classes}
      disabled={disabled || loading}
      onClick={onClick}
    >
      {loading ? (
        <span>Loading...</span>
      ) : (
        <>
          {icon}
          <span>{children}</span>
        </>
      )}
    </button>
  );
}