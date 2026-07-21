export default function LoadingState({
  title = "Loading...",
  description = "",
}) {
  return (
    <section className="tz-loading-state">
      <div className="tz-loading-spinner" />

      <strong>{title}</strong>

      {description ? (
        <p>{description}</p>
      ) : null}
    </section>
  );
}