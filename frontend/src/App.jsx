import { Navigate, Route, Routes } from "react-router-dom";
import AppLayout from "./layouts/AppLayout";
import LoginPage from "./pages/auth/LoginPage";
import RolesPermissionsPage from "./pages/admin/RolesPermissionsPage";
import PlatformAdminPage from "./pages/admin/PlatformAdminPage";
import ConversationDetailPage from "./pages/conversations/ConversationDetailPage";
import ConversationsPage from "./pages/conversations/ConversationsPage";
import CommentsPage from "./pages/comments/CommentsPage";
import DashboardPage from "./pages/dashboard/DashboardPage";
import UISettingsPage from "./pages/dashboard/UISettingsPage";
import CompanySettingsPage from "./pages/company/CompanySettingsPage";
import ModulePage from "./pages/modules/ModulePage";
import CataloguePage from "./pages/catalogue/CataloguePage";
import CallsPage from "./pages/calls/CallsPage";
import CustomersPage from "./pages/customers/CustomersPage";
import CustomerDetailPage from "./pages/customers/CustomerDetailPage";
import AITeachingPage from "./pages/ai-teaching/AITeachingPage";
import AnalyticsPage from "./pages/analytics/AnalyticsPage";
import TasksPage from "./pages/tasks/TasksPage";
import BroadcastPage from "./pages/broadcast/BroadcastPage";
import BroadcastDetailPage from "./pages/broadcast/BroadcastDetailPage";
import NotificationsPage from "./pages/notifications/NotificationsPage";
import ProtectedRoute from "./routes/ProtectedRoute";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/conversations/:channel/:userId/full" element={<ProtectedRoute><ConversationDetailPage standalone /></ProtectedRoute>} />
      <Route path="/platform-admin" element={<ProtectedRoute><PlatformAdminPage /></ProtectedRoute>} />
      <Route path="/company-settings/*" element={<ProtectedRoute><CompanySettingsPage /></ProtectedRoute>} />
      <Route path="/settings" element={<ProtectedRoute><UISettingsPage /></ProtectedRoute>} />
      <Route element={<ProtectedRoute><AppLayout /></ProtectedRoute>}>
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/notifications" element={<NotificationsPage />} />
        <Route path="/conversations" element={<ConversationsPage />} />
        <Route path="/conversations/:channel/:userId" element={<ConversationsPage />} />
        <Route path="/comments" element={<CommentsPage />} />
        <Route path="/customers" element={<CustomersPage />} />
        <Route path="/customers/:customerId" element={<CustomerDetailPage />} />
        <Route path="/broadcast" element={<BroadcastPage />} />
        <Route path="/broadcast/:broadcastId" element={<BroadcastDetailPage />} />
        <Route path="/catalogue" element={<CataloguePage />} />
        <Route path="/calls" element={<CallsPage />} />
        <Route path="/ai-teaching" element={<AITeachingPage />} />
        <Route path="/tasks" element={<TasksPage />} />
        <Route path="/scheduler" element={<ModulePage title="Scheduler" description="Create, approve and schedule social posts from one place." />} />
        <Route path="/appointments" element={<ModulePage title="Appointments" description="Optional booking module connected to calendars, employees and customer profiles." />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
        <Route path="/team-chat" element={<ModulePage title="Team Chat" description="Internal messages, follow-ups, mentions, shared files and instructions without private WhatsApp groups." />} />
        <Route path="/roles" element={<RolesPermissionsPage />} />
      </Route>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
