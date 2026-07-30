export default function PageHeader({
  eyebrow,
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