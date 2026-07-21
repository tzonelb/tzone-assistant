import { ErrorOutlineOutlined } from "@mui/icons-material";


export default function ErrorState({
  title = "Something went wrong",
  description = "The requested information could not be loaded.",
  action = null,
}) {
  return (
    <section className="tz-error-state">
      <div className="tz-error-icon">
        <ErrorOutlineOutlined />
      </div>

      <strong>{title}</strong>

      <p>{description}</p>

      {action}
    </section>
  );
}