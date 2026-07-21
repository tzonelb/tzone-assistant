export default function AppCard({
  children,
  padding = "medium",
  hoverable = false,
  className = "",
}) {
  const classes = [
    "tz-card",
    `tz-card-padding-${padding}`,
    hoverable ? "tz-card-hoverable" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <article className={classes}>
      {children}
    </article>
  );
}