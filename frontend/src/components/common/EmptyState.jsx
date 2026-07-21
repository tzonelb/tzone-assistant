import { InboxOutlined } from "@mui/icons-material";


export default function EmptyState({
  icon = <InboxOutlined />,
  title = "No results found",
  description = "There is currently nothing to display.",
  action = null,
}) {
  return (
    <section className="tz-empty-state">
      <div className="tz-empty-icon">
        {icon}
      </div>

      <strong>{title}</strong>

      <p>{description}</p>

      {action}
    </section>
  );
}