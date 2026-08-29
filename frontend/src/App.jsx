import { Suspense, lazy } from "react";
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
import ConversationsPage from "./pages/conversations/ConversationsPage";
import ConversationDetailPage from "./pages/conversations/ConversationDetailPage";

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
const RolesPermissionsPage = lazy(() => import("./pages/admin/RolesPermissionsPage"));
const SchedulerPage = lazy(() => import("./pages/scheduler/SchedulerPage"));
const TasksPage = lazy(() => import("./pages/tasks/TasksPage"));
const TeamChatPage = lazy(() => import("./pages/team-chat/TeamChatPage"));
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
        <Route path="/conversations/:channel/:userId/full" element={<ProtectedRoute><ModuleRoute module="conversations"><ConversationDetailPage standalone /></ModuleRoute></ProtectedRoute>} />
        <Route element={<ProtectedRoute><AppLayout /></ProtectedRoute>}>
          <Route path="/dashboard" element={<ModuleRoute module="dashboard"><DashboardPage /></ModuleRoute>} />
          <Route path="/notifications" element={<ModuleRoute module="notifications"><NotificationsPage /></ModuleRoute>} />
          <Route path="/conversations" element={<ModuleRoute module="conversations"><ConversationsPage /></ModuleRoute>} />
          <Route path="/conversations/:channel/:userId" element={<ModuleRoute module="conversations"><ConversationsPage /></ModuleRoute>} />
          <Route path="/comments" element={<ModuleRoute module="comments"><CommentsPage /></ModuleRoute>} />
          <Route path="/customers" element={<ModuleRoute module="customers"><CustomersPage /></ModuleRoute>} />
          <Route path="/tasks" element={<ModuleRoute module="tasks"><TasksPage /></ModuleRoute>} />
          <Route path="/appointments" element={<ModuleRoute module="appointments"><AppointmentsPage /></ModuleRoute>} />
          <Route path="/catalogue" element={<ModuleRoute module="catalogue"><CataloguePage /></ModuleRoute>} />
          <Route path="/knowledge" element={<ModuleRoute module="knowledge"><KnowledgePage /></ModuleRoute>} />
          <Route path="/ai-teaching" element={<ModuleRoute module="ai_teaching"><AiTeachingPage /></ModuleRoute>} />
          <Route path="/scheduler" element={<ModuleRoute module="scheduler"><SchedulerPage /></ModuleRoute>} />
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
