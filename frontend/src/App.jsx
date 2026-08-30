import { Suspense, lazy, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import AppLayout from "./layouts/AppLayout";
import LoginPage from "./pages/auth/LoginPage";
import ResetPasswordPage from "./pages/auth/ResetPasswordPage";
import ForgotPasswordPage from "./pages/auth/ForgotPasswordPage";
import ModuleRoute from "./routes/ModuleRoute";
import ProtectedRoute from "./routes/ProtectedRoute";

// Loaded eagerly: the login screen is the first thing an unauthenticated
// visitor needs, and the dashboard and inbox are where employees spend the day.
import DashboardPage from "./pages/dashboard/DashboardPage";
import DashboardPageV2 from "./pages/dashboard/DashboardPageV2";
import ConversationsPageV2 from "./pages/conversations/ConversationsPageV2";
import TasksPageV2 from "./pages/tasks/TasksPageV2";
import AppointmentsPageV2 from "./pages/appointments/AppointmentsPageV2";
import NotificationsPageV2 from "./pages/notifications/NotificationsPageV2";
import { isUiV2Enabled } from "./config/featureFlags";
import ConversationsPage from "./pages/conversations/ConversationsPage";
import ConversationDetailPage from "./pages/conversations/ConversationDetailPage";
import ConversationDetailPageV2 from "./pages/conversations/ConversationDetailPageV2";

// Everything else is split per route. Bundling all seventeen screens into one
// file pushed the initial download past 950 KB, which an employee on a phone
// pays for on every first load even though they open two or three screens.
const AiTeachingPage = lazy(() => import("./pages/ai-teaching/AiTeachingPage"));
const AnalyticsPage = lazy(() => import("./pages/analytics/AnalyticsPage"));
const AppointmentsPage = lazy(() => import("./pages/appointments/AppointmentsPage"));
const CataloguePage = lazy(() => import("./pages/catalogue/CataloguePage"));
const ChannelsPage = lazy(() => import("./pages/channels/ChannelsPage"));
const CommentsPage = lazy(() => import("./pages/comments/CommentsPage"));
const CompanySettingsPage = lazy(() => import("./pages/company/CompanySettingsPage"));
const CustomersPage = lazy(() => import("./pages/customers/CustomersPage"));
const KnowledgePage = lazy(() => import("./pages/knowledge/KnowledgePage"));
const NotificationsPage = lazy(() => import("./pages/notifications/NotificationsPage"));
const PublishStandalonePage = lazy(() => import("./pages/publish/PublishStandalonePage"));
const RolesPermissionsPage = lazy(() => import("./pages/admin/RolesPermissionsPage"));
const SavedRepliesPage = lazy(() => import("./pages/saved-replies/SavedRepliesPage"));
const SchedulerPage = lazy(() => import("./pages/scheduler/SchedulerPage"));
const TasksPage = lazy(() => import("./pages/tasks/TasksPage"));
const TeamChatPage = lazy(() => import("./pages/team-chat/TeamChatPage"));
const TrainAndTestPage = lazy(() => import("./pages/ai-teaching/TrainAndTestPage"));
const UISettingsPage = lazy(() => import("./pages/dashboard/UISettingsPage"));

// The operator's console. A separate credential, a separate shell and a
// separate visual identity — it shares this router and nothing else.
const SuperAdminApp = lazy(() => import("./superadmin/SuperAdminApp"));

function RouteFallback() {
  return (
    <main className="full-screen-state">
      <div className="loading-spinner" />
    </main>
  );
}

// Renders the redesigned screen when the UI v2 flag is on and the previous one
// otherwise — the same switch AppLayout uses for the shell, so a user who turns
// the new interface off gets the old shell AND the old screens, not a mix.
function v2Route(V1Component, V2Component) {
  return function V2Route(props) {
    const [uiV2, setUiV2] = useState(isUiV2Enabled);

    useEffect(() => {
      function handleFlagChange() {
        setUiV2(isUiV2Enabled());
      }

      window.addEventListener("tzone:ui-v2-changed", handleFlagChange);
      window.addEventListener("storage", handleFlagChange);

      return () => {
        window.removeEventListener("tzone:ui-v2-changed", handleFlagChange);
        window.removeEventListener("storage", handleFlagChange);
      };
    }, []);

    const Component = uiV2 ? V2Component : V1Component;

    return <Component {...props} />;
  };
}

const DashboardScreen = v2Route(DashboardPage, DashboardPageV2);
const ConversationsScreen = v2Route(ConversationsPage, ConversationsPageV2);
const TasksScreen = v2Route(TasksPage, TasksPageV2);
const AppointmentsScreen = v2Route(AppointmentsPage, AppointmentsPageV2);
const NotificationsScreen = v2Route(NotificationsPage, NotificationsPageV2);
// The standalone full-page chat ("Open chat in new tab"). v2Route forwards
// props, so `standalone` reaches whichever component the flag selects —
// both versions accept it and drop their embedded chrome accordingly.
const ConversationDetailScreen = v2Route(ConversationDetailPage, ConversationDetailPageV2);


export default function App() {
  return (
    <Suspense fallback={<RouteFallback />}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        {/* Public by necessity: the person following this link cannot sign in,
            which is the whole reason the link exists. */}
        <Route path="/reset-password/:token" element={<ResetPasswordPage />} />
        <Route path="/superadmin/*" element={<SuperAdminApp />} />
        <Route path="/conversations/:channel/:userId/full" element={<ProtectedRoute><ModuleRoute module="conversations"><ConversationDetailScreen standalone /></ModuleRoute></ProtectedRoute>} />
        <Route element={<ProtectedRoute><AppLayout /></ProtectedRoute>}>
          <Route path="/dashboard" element={<ModuleRoute module="dashboard"><DashboardScreen /></ModuleRoute>} />
          <Route path="/notifications" element={<ModuleRoute module="notifications"><NotificationsScreen /></ModuleRoute>} />
          <Route path="/conversations" element={<ModuleRoute module="conversations"><ConversationsScreen /></ModuleRoute>} />
          <Route path="/conversations/:channel/:userId" element={<ModuleRoute module="conversations"><ConversationsScreen /></ModuleRoute>} />
          <Route path="/comments" element={<ModuleRoute module="comments"><CommentsPage /></ModuleRoute>} />
          <Route path="/customers" element={<ModuleRoute module="customers"><CustomersPage /></ModuleRoute>} />
          <Route path="/tasks" element={<ModuleRoute module="tasks"><TasksScreen /></ModuleRoute>} />
          {/* Gated on `conversations`, matching main.py: the API registers
              saved_replies under that module because the library exists to be
              dropped into the inbox composer. */}
          <Route path="/saved-replies" element={<ModuleRoute module="conversations"><SavedRepliesPage /></ModuleRoute>} />
          <Route path="/appointments" element={<ModuleRoute module="appointments"><AppointmentsScreen /></ModuleRoute>} />
          <Route path="/catalogue" element={<ModuleRoute module="catalogue"><CataloguePage /></ModuleRoute>} />
          <Route path="/knowledge" element={<ModuleRoute module="knowledge"><KnowledgePage /></ModuleRoute>} />
          <Route path="/ai-teaching" element={<ModuleRoute module="ai_teaching"><AiTeachingPage /></ModuleRoute>} />
          {/* The sidebar's "Test & Train AI" entry. Gated on `ai_teaching`,
              matching main.py: both halves of the screen — the teaching chat
              and the dry run — are routes on that module's router. */}
          <Route path="/test-ai" element={<ModuleRoute module="ai_teaching"><TrainAndTestPage /></ModuleRoute>} />
          <Route path="/scheduler" element={<ModuleRoute module="scheduler"><SchedulerPage /></ModuleRoute>} />
          {/* The sidebar's "Publish" entry: the same publishing calendar as
              /scheduler, in the redesigned Buffer-style shell. Gated on
              `scheduler` because that is the module its posts come from; the
              connected-page list it also reads rides on `channels.view`, which
              is the permission the sidebar already checks before showing the
              link. */}
          <Route path="/publish" element={<ModuleRoute module="scheduler"><PublishStandalonePage /></ModuleRoute>} />
          <Route path="/analytics" element={<ModuleRoute module="analytics"><AnalyticsPage /></ModuleRoute>} />
          <Route path="/team-chat" element={<ModuleRoute module="team_chat"><TeamChatPage /></ModuleRoute>} />
          <Route path="/channels" element={<ModuleRoute module="channels"><ChannelsPage /></ModuleRoute>} />
          <Route path="/settings" element={<ModuleRoute module="preferences"><UISettingsPage /></ModuleRoute>} />
          <Route path="/company-settings/*" element={<ModuleRoute module="company_settings"><CompanySettingsPage /></ModuleRoute>} />
          <Route path="/roles" element={<ModuleRoute module="roles"><RolesPermissionsPage /></ModuleRoute>} />
        </Route>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </Suspense>
  );
}
