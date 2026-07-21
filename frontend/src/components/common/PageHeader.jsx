export default function PageHeader({
  eyebrow,
  title,
  description,
  actions = null,
}) {
  return (
    <header className="tz-page-header">
      <div className="tz-page-header-content">
        {eyebrow ? (
          <span className="tz-page-header-eyebrow">
            {eyebrow}
          </span>
        ) : null}

        <h2>{title}</h2>

        {description ? (
          <p>{description}</p>
        ) : null}
      </div>

      {actions ? (
        <div className="tz-page-header-actions">
          {actions}
        </div>
      ) : null}
    </header>
  );
}