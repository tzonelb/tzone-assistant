import { Navigate, Route, Routes } from "react-router-dom";
import AppLayout from "./layouts/AppLayout";
import LoginPage from "./pages/auth/LoginPage";
import SignupPage from "./pages/auth/SignupPage";
import PlatformAdminPage from "./pages/admin/PlatformAdminPage";
import ThemeStudioPage from "./pages/admin/ThemeStudioPage";
import ConversationDetailPage from "./pages/conversations/ConversationDetailPage";
import ConversationsPage from "./pages/conversations/ConversationsPage";
import ConversationsPageV2 from "./pages/conversations/ConversationsPageV2";
import { useEffect, useState } from "react";
import { isUiV2Enabled } from "./config/featureFlags";
import PublishStandalonePage from "./pages/publish/PublishStandalonePage";
import CommentsPage from "./pages/community/InboxPage";
import DashboardPage from "./pages/dashboard/DashboardPage";
import DashboardPageV2 from "./pages/dashboard/DashboardPageV2";
import UISettingsPage from "./pages/dashboard/UISettingsPage";
import CompanySettingsPage from "./pages/company/CompanySettingsPage";
import CataloguePage from "./pages/catalogue/CataloguePage";
import CallsPage from "./pages/calls/CallsPage";
import DialerPage from "./pages/dialer/DialerPage";
import TeamChatPage from "./pages/team-chat/TeamChatPage";
import TeamChatPageV2 from "./pages/team-chat/TeamChatPageV2";
import AppointmentsPage from "./pages/appointments/AppointmentsPage";
import AppointmentsPageV2 from "./pages/appointments/AppointmentsPageV2";
import CustomersPage from "./pages/customers/CustomersPage";
import CustomersPageV2 from "./pages/customers/CustomersPageV2";
import CustomerDetailPage from "./pages/customers/CustomerDetailPage";
import CustomerDetailPageV2 from "./pages/customers/CustomerDetailPageV2";
import TrainAndTestPage from "./pages/ai-teaching/TrainAndTestPage";
import AnalyticsPage from "./pages/analytics/AnalyticsPage";
import TasksPage from "./pages/tasks/TasksPage";
import TasksPageV2 from "./pages/tasks/TasksPageV2";
import SavedRepliesPage from "./pages/saved-replies/SavedRepliesPage";
import ReplyFlowBuilderPage from "./pages/reply-flows/ReplyFlowBuilderPage";
import BroadcastPage from "./pages/broadcast/BroadcastPage";
import BroadcastPageV2 from "./pages/broadcast/BroadcastPageV2";
import BroadcastDetailPage from "./pages/broadcast/BroadcastDetailPage";
import BroadcastDetailPageV2 from "./pages/broadcast/BroadcastDetailPageV2";
import NotificationsPage from "./pages/notifications/NotificationsPage";
import NotificationsPageV2 from "./pages/notifications/NotificationsPageV2";
import ThemePreviewPage from "./pages/theme-preview/ThemePreviewPage";
import ProtectedRoute from "./routes/ProtectedRoute";
import RequireAccess from "./routes/RequireAccess";

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
const NotificationsRoute = v2Route(NotificationsPage, NotificationsPageV2);
const TasksRoute = v2Route(TasksPage, TasksPageV2);
const AppointmentsRoute = v2Route(AppointmentsPage, AppointmentsPageV2);
const TeamChatRoute = v2Route(TeamChatPage, TeamChatPageV2);
const CustomersRoute = v2Route(CustomersPage, CustomersPageV2);
const CustomerDetailRoute = v2Route(CustomerDetailPage, CustomerDetailPageV2);
const BroadcastRoute = v2Route(BroadcastPage, BroadcastPageV2);
const BroadcastDetailRoute = v2Route(BroadcastDetailPage, BroadcastDetailPageV2);

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/signup" element={<SignupPage />} />
      <Route path="/theme-preview" element={<ThemePreviewPage />} />
      <Route path="/conversations/:channel/:userId/full" element={<ProtectedRoute><ConversationDetailPage standalone /></ProtectedRoute>} />
      <Route path="/platform-admin" element={<ProtectedRoute requireSuperAdmin><PlatformAdminPage /></ProtectedRoute>} />
      <Route path="/platform-admin/theme-studio" element={<ProtectedRoute requireSuperAdmin><ThemeStudioPage /></ProtectedRoute>} />
      <Route path="/company-settings/*" element={<ProtectedRoute><CompanySettingsPage /></ProtectedRoute>} />
      <Route path="/settings" element={<ProtectedRoute><UISettingsPage /></ProtectedRoute>} />
      <Route element={<ProtectedRoute><AppLayout /></ProtectedRoute>}>
        <Route path="/dashboard" element={<DashboardRoute />} />
        <Route path="/notifications" element={<NotificationsRoute />} />
        <Route path="/conversations" element={<ConversationsRoute />} />
        <Route path="/conversations/:channel/:userId" element={<ConversationsRoute />} />
        <Route path="/customers" element={<CustomersRoute />} />
        <Route path="/customers/:customerId" element={<CustomerDetailRoute />} />
        <Route path="/broadcast" element={<RequireAccess permissions={["channels.view"]} moduleKey="broadcast"><BroadcastRoute /></RequireAccess>} />
        <Route path="/broadcast/:broadcastId" element={<RequireAccess permissions={["channels.view"]} moduleKey="broadcast"><BroadcastDetailRoute /></RequireAccess>} />
        <Route path="/catalogue" element={<RequireAccess moduleKey="catalogue"><CataloguePage /></RequireAccess>} />
        <Route path="/calls" element={<CallsPage />} />
        <Route path="/dialer" element={<RequireAccess permissions={["dialer.use"]} moduleKey="dialer"><DialerPage /></RequireAccess>} />
        <Route path="/tasks" element={<TasksRoute />} />
        <Route path="/saved-replies" element={<SavedRepliesPage />} />
        <Route path="/reply-flows/:id" element={<ReplyFlowBuilderPage />} />
        <Route path="/appointments" element={<RequireAccess permissions={["modules.appointments"]} moduleKey="appointments"><AppointmentsRoute /></RequireAccess>} />
        <Route path="/analytics" element={<RequireAccess permissions={["analytics.view"]} moduleKey="analytics"><AnalyticsPage /></RequireAccess>} />
        <Route path="/team-chat" element={<RequireAccess permissions={["modules.team_chat"]} moduleKey="team_chat"><TeamChatRoute /></RequireAccess>} />
        <Route path="/publish" element={<RequireAccess permissions={["channels.view"]} moduleKey="publish"><PublishStandalonePage /></RequireAccess>} />
        <Route path="/comments" element={<RequireAccess permissions={["modules.comments"]} moduleKey="comments"><CommentsPage /></RequireAccess>} />
        <Route path="/test-ai" element={<TrainAndTestPage />} />
      </Route>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
