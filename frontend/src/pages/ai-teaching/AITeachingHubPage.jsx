import { ArrowBackOutlined, ChatOutlined, LibraryBooksOutlined, RuleOutlined } from "@mui/icons-material";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import InstructionsPage from "./InstructionsPage";
import KnowledgePage from "./KnowledgePage";
import TrainAndTestPage from "./TrainAndTestPage";
import "../customers/CustomersPage.css";
import "./AITeachingPage.css";
import "../community/CommunityHubPage.css";

const NAV_ITEMS = [
  { to: "/ai-teaching/instructions", label: "Instructions", icon: RuleOutlined },
  { to: "/ai-teaching/knowledge", label: "Knowledge", icon: LibraryBooksOutlined },
  { to: "/ai-teaching/train-and-test", label: "Train & Chat / Test", icon: ChatOutlined },
];

export default function AITeachingHubPage() {
  const navigate = useNavigate();

  return (
    <section className="company-settings-shell community-hub-shell">
      <aside className="company-settings-nav">
        <button className="company-settings-back" type="button" onClick={() => navigate("/dashboard")}>
          <ArrowBackOutlined /> Back to platform
        </button>
        <div className="company-settings-nav-heading">
          <span>AI TEACHING</span>
          <h1>AI Teaching</h1>
        </div>
        <nav className="company-settings-nav-scroll">
          {NAV_ITEMS.map((item) => (
            <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? "is-active" : "")}>
              <item.icon fontSize="small" /> {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      <main className="company-settings-content">
        <div className="company-settings-content-scroll">
          <Routes>
            <Route path="/" element={<Navigate to="/ai-teaching/instructions" replace />} />
            <Route path="/instructions" element={<InstructionsPage />} />
            <Route path="/knowledge" element={<KnowledgePage />} />
            <Route path="/train-and-test" element={<TrainAndTestPage />} />
            <Route path="*" element={<Navigate to="/ai-teaching/instructions" replace />} />
          </Routes>
        </div>
      </main>
    </section>
  );
}
