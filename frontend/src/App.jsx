import { Navigate, Route, Routes } from "react-router-dom";
import AppLayout from "./layouts/AppLayout";
import LoginPage from "./pages/auth/LoginPage";
import SignupPage from "./pages/auth/SignupPage";
import RolesPermissionsPage from "./pages/admin/RolesPermissionsPage";
import PlatformAdminPage from "./pages/admin/PlatformAdminPage";
import ThemeStudioPage from "./pages/admin/ThemeStudioPage";
import ConversationDetailPage from "./pages/conversations/ConversationDetailPage";
import ConversationsPage from "./pages/conversations/ConversationsPage";
import ConversationsPageV2 from "./pages/conversations/ConversationsPageV2";
import { useEffect, useState } from "react";
import { isUiV2Enabled } from "./config/featureFlags";
import CommunityHubPage from "./pages/community/CommunityHubPage";
import DashboardPage from "./pages/dashboard/DashboardPage";
import DashboardPageV2 from "./pages/dashboard/DashboardPageV2";
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
import ReplyFlowsListPage from "./pages/reply-flows/ReplyFlowsListPage";
import ReplyFlowBuilderPage from "./pages/reply-flows/ReplyFlowBuilderPage";
import BroadcastPage from "./pages/broadcast/BroadcastPage";
import BroadcastDetailPage from "./pages/broadcast/BroadcastDetailPage";
import NotificationsPage from "./pages/notifications/NotificationsPage";
import ProtectedRoute from "./routes/ProtectedRoute";

// Renders V2Component when the ui_v2 flag is on, V1Component otherwise —
// same switch AppLayout.jsx uses for Sidebar/Topbar, generalized here
// for any route that has gotten its own v2 rebuild.
function v2Route(V1Component, V2Component) {
  return function V2Route() {
    const [uiV2, setUiV2] = useState(isUiV2Enabled);
    useEffect(() => {
      function handleFlagChange() { setUiV2(isUiV2Enabled()); }
      window.addEventListener("tzone:ui-v2-changed", handleFlagChange);
      return () => window.removeEventListener("tzone:ui-v2-changed", handleFlagChange);
    }, []);
    return uiV2 ? <V2Component /> : <V1Component />;
  };
}

const ConversationsRoute = v2Route(ConversationsPage, ConversationsPageV2);
const DashboardRoute = v2Route(DashboardPage, DashboardPageV2);

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/signup" element={<SignupPage />} />
      <Route path="/conversations/:channel/:userId/full" element={<ProtectedRoute><ConversationDetailPage standalone /></ProtectedRoute>} />
      <Route path="/platform-admin" element={<ProtectedRoute requireSuperAdmin><PlatformAdminPage /></ProtectedRoute>} />
      <Route path="/platform-admin/theme-studio" element={<ProtectedRoute requireSuperAdmin><ThemeStudioPage /></ProtectedRoute>} />
      <Route path="/company-settings/*" element={<ProtectedRoute><CompanySettingsPage /></ProtectedRoute>} />
      <Route path="/community/*" element={<ProtectedRoute><CommunityHubPage /></ProtectedRoute>} />
      <Route path="/ai-teaching/*" element={<ProtectedRoute><AITeachingHubPage /></ProtectedRoute>} />
      <Route path="/settings" element={<ProtectedRoute><UISettingsPage /></ProtectedRoute>} />
      <Route element={<ProtectedRoute><AppLayout /></ProtectedRoute>}>
        <Route path="/dashboard" element={<DashboardRoute />} />
        <Route path="/notifications" element={<NotificationsPage />} />
        <Route path="/conversations" element={<ConversationsRoute />} />
        <Route path="/conversations/:channel/:userId" element={<ConversationsRoute />} />
        <Route path="/customers" element={<CustomersPage />} />
        <Route path="/customers/:customerId" element={<CustomerDetailPage />} />
        <Route path="/broadcast" element={<BroadcastPage />} />
        <Route path="/broadcast/:broadcastId" element={<BroadcastDetailPage />} />
        <Route path="/catalogue" element={<CataloguePage />} />
        <Route path="/calls" element={<CallsPage />} />
        <Route path="/tasks" element={<TasksPage />} />
        <Route path="/saved-replies" element={<SavedRepliesPage />} />
        <Route path="/reply-flows" element={<ReplyFlowsListPage />} />
        <Route path="/reply-flows/:id" element={<ReplyFlowBuilderPage />} />
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
