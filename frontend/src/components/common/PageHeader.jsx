// The Topbar already names every page — this header intentionally
// renders no title/eyebrow/description text of its own (that was tried
// and explicitly rejected as redundant). It only ever holds page-level
// actions, right-aligned.
export default function PageHeader({
  actions = null,
}) {
  if (!actions) return null;

  return (
    <header className="tz-page-header tz-page-header-actions-only">
      <div className="tz-page-header-actions">
        {actions}
      </div>
    </header>
  );
}