import { Navigate, Route, Routes } from "react-router-dom";
import AppLayout from "./layouts/AppLayout";
import LoginPage from "./pages/auth/LoginPage";
import RolesPermissionsPage from "./pages/admin/RolesPermissionsPage";
import ConversationDetailPage from "./pages/conversations/ConversationDetailPage";
import ConversationsPage from "./pages/conversations/ConversationsPage";
import CommentsPage from "./pages/comments/CommentsPage";
import BroadcastPage from "./pages/broadcast/BroadcastPage";
import CustomersPage from "./pages/customers/CustomersPage";
import CatalogueMasterPage from "./pages/catalogue/CatalogueMasterPage";
import AppointmentsPage from "./pages/appointments/AppointmentsPage";
import SchedulerPage from "./pages/scheduler/SchedulerPage";
import TeamChatPage from "./pages/team-chat/TeamChatPage";
import CallsPage from "./pages/calls/CallsPage";
import TriggersPage from "./pages/triggers/TriggersPage";
import DialerPage from "./pages/dialer/DialerPage";
import DashboardPage from "./pages/dashboard/DashboardPage";
import UISettingsPage from "./pages/dashboard/UISettingsPage";
import CompanySettingsPage from "./pages/company/CompanySettingsPage";
import AnalyticsPage from "./pages/analytics/AnalyticsPage";
import AiTeachingPage from "./pages/ai-teaching/AiTeachingPage";
import NotificationsPage from "./pages/notifications/NotificationsPage";
import TasksPage from "./pages/tasks/TasksPage";
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
        <Route path="/catalogue" element={<CatalogueMasterPage />} />
        <Route path="/ai-teaching" element={<AiTeachingPage />} />
        <Route path="/tasks" element={<TasksPage />} />
        <Route path="/scheduler" element={<SchedulerPage />} />
        <Route path="/appointments" element={<AppointmentsPage />} />
        <Route path="/calls" element={<CallsPage />} />
        <Route path="/triggers" element={<TriggersPage />} />
        <Route path="/dialer" element={<DialerPage />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
        <Route path="/team-chat" element={<TeamChatPage />} />
        <Route path="/settings" element={<UISettingsPage />} />
        <Route path="/company-settings/*" element={<CompanySettingsPage />} />
        <Route path="/roles" element={<RolesPermissionsPage />} />
      </Route>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
