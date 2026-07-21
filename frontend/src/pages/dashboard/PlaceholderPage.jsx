export default function PlaceholderPage({
  title,
  description,
}) {
  return (
    <section className="placeholder-page">
      <div className="placeholder-mark">T</div>

      <h2>{title}</h2>

      <p>
        {description ||
          "This module is prepared and will be connected in the next stage."}
      </p>
    </section>
  );
}