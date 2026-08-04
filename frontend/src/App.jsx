import { Navigate, Route, Routes } from "react-router-dom";
import AppLayout from "./layouts/AppLayout";
import LoginPage from "./pages/auth/LoginPage";
import RolesPermissionsPage from "./pages/admin/RolesPermissionsPage";
import ConversationDetailPage from "./pages/conversations/ConversationDetailPage";
import ConversationsPage from "./pages/conversations/ConversationsPage";
import CommentsPage from "./pages/comments/CommentsPage";
import BroadcastPage from "./pages/broadcast/BroadcastPage";
import CustomersPage from "./pages/customers/CustomersPage";
import DashboardPage from "./pages/dashboard/DashboardPage";
import UISettingsPage from "./pages/dashboard/UISettingsPage";
import CompanySettingsPage from "./pages/company/CompanySettingsPage";
import AnalyticsPage from "./pages/analytics/AnalyticsPage";
import AiTeachingPage from "./pages/ai-teaching/AiTeachingPage";
import ModulePage from "./pages/modules/ModulePage";
import NotificationsPage from "./pages/notifications/NotificationsPage";
import ProtectedRoute from "./routes/ProtectedRoute";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/conversations/:channel/:userId/full" element={<ProtectedRoute><ConversationDetailPage standalone /></ProtectedRoute>} />
      <Route element={<ProtectedRoute><AppLayout /></ProtectedRoute>}>
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/notifications" element={<NotificationsPage />} />
        <Route path="/conversations" element={<ConversationsPage />} />
        <Route path="/conversations/:channel/:userId" element={<ConversationsPage />} />
        <Route path="/comments" element={<CommentsPage />} />
        <Route path="/broadcast" element={<BroadcastPage />} />
        <Route path="/customers" element={<CustomersPage />} />
        <Route path="/catalogue" element={<ModulePage title="Master Catalogue" description="One product catalogue synchronized with WhatsApp, websites, accounting systems and future sales channels." />} />
        <Route path="/ai-teaching" element={<AiTeachingPage />} />
        <Route path="/tasks" element={<ModulePage title="Tasks" description="Tasks, follow-ups, payments, services and internal cases assigned to the team." />} />
        <Route path="/scheduler" element={<ModulePage title="Scheduler" description="Create, approve and schedule social posts from one place." />} />
        <Route path="/appointments" element={<ModulePage title="Appointments" description="Optional booking module connected to calendars, employees and customer profiles." />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
        <Route path="/team-chat" element={<ModulePage title="Team Chat" description="Internal messages, follow-ups, mentions, shared files and instructions without private WhatsApp groups." />} />
        <Route path="/settings" element={<UISettingsPage />} />
        <Route path="/company-settings/*" element={<CompanySettingsPage />} />
        <Route path="/roles" element={<RolesPermissionsPage />} />
      </Route>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
