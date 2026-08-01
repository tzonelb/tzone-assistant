import { Navigate, Route, Routes } from "react-router-dom";
import AppLayout from "./layouts/AppLayout";
import LoginPage from "./pages/auth/LoginPage";
import SignupPage from "./pages/auth/SignupPage";
import RolesPermissionsPage from "./pages/admin/RolesPermissionsPage";
import PlatformAdminPage from "./pages/admin/PlatformAdminPage";
import ConversationDetailPage from "./pages/conversations/ConversationDetailPage";
import ConversationsPage from "./pages/conversations/ConversationsPage";
import CommunityHubPage from "./pages/community/CommunityHubPage";
import DashboardPage from "./pages/dashboard/DashboardPage";
import UISettingsPage from "./pages/dashboard/UISettingsPage";
import CompanySettingsPage from "./pages/company/CompanySettingsPage";
import CataloguePage from "./pages/catalogue/CataloguePage";
import CallsPage from "./pages/calls/CallsPage";
import TeamChatPage from "./pages/team-chat/TeamChatPage";
import AppointmentsPage from "./pages/appointments/AppointmentsPage";
import CustomersPage from "./pages/customers/CustomersPage";
import CustomerDetailPage from "./pages/customers/CustomerDetailPage";
import AITeachingHubPage from "./pages/ai-teaching/AITeachingHubPage";
import AnalyticsPage from "./pages/analytics/AnalyticsPage";
import TasksPage from "./pages/tasks/TasksPage";
import SavedRepliesPage from "./pages/saved-replies/SavedRepliesPage";
import BroadcastPage from "./pages/broadcast/BroadcastPage";
import BroadcastDetailPage from "./pages/broadcast/BroadcastDetailPage";
import NotificationsPage from "./pages/notifications/NotificationsPage";
import ProtectedRoute from "./routes/ProtectedRoute";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/signup" element={<SignupPage />} />
      <Route path="/conversations/:channel/:userId/full" element={<ProtectedRoute><ConversationDetailPage standalone /></ProtectedRoute>} />
      <Route path="/platform-admin" element={<ProtectedRoute><PlatformAdminPage /></ProtectedRoute>} />
      <Route path="/company-settings/*" element={<ProtectedRoute><CompanySettingsPage /></ProtectedRoute>} />
      <Route path="/community/*" element={<ProtectedRoute><CommunityHubPage /></ProtectedRoute>} />
      <Route path="/ai-teaching/*" element={<ProtectedRoute><AITeachingHubPage /></ProtectedRoute>} />
      <Route path="/settings" element={<ProtectedRoute><UISettingsPage /></ProtectedRoute>} />
      <Route element={<ProtectedRoute><AppLayout /></ProtectedRoute>}>
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/notifications" element={<NotificationsPage />} />
        <Route path="/conversations" element={<ConversationsPage />} />
        <Route path="/conversations/:channel/:userId" element={<ConversationsPage />} />
        <Route path="/customers" element={<CustomersPage />} />
        <Route path="/customers/:customerId" element={<CustomerDetailPage />} />
        <Route path="/broadcast" element={<BroadcastPage />} />
        <Route path="/broadcast/:broadcastId" element={<BroadcastDetailPage />} />
        <Route path="/catalogue" element={<CataloguePage />} />
        <Route path="/calls" element={<CallsPage />} />
        <Route path="/tasks" element={<TasksPage />} />
        <Route path="/saved-replies" element={<SavedRepliesPage />} />
        <Route path="/appointments" element={<AppointmentsPage />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
        <Route path="/team-chat" element={<TeamChatPage />} />
        <Route path="/roles" element={<RolesPermissionsPage />} />
      </Route>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
