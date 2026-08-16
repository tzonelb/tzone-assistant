import { Suspense, lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import AppLayout from "./layouts/AppLayout";
import LoginPage from "./pages/auth/LoginPage";
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
        <Route path="/conversations/:channel/:userId/full" element={<ProtectedRoute><ConversationDetailPage standalone /></ProtectedRoute>} />
        <Route element={<ProtectedRoute><AppLayout /></ProtectedRoute>}>
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/notifications" element={<NotificationsPage />} />
          <Route path="/conversations" element={<ConversationsPage />} />
          <Route path="/conversations/:channel/:userId" element={<ConversationsPage />} />
          <Route path="/comments" element={<CommentsPage />} />
          <Route path="/customers" element={<CustomersPage />} />
          <Route path="/tasks" element={<TasksPage />} />
          <Route path="/appointments" element={<AppointmentsPage />} />
          <Route path="/catalogue" element={<CataloguePage />} />
          <Route path="/knowledge" element={<KnowledgePage />} />
          <Route path="/ai-teaching" element={<AiTeachingPage />} />
          <Route path="/scheduler" element={<SchedulerPage />} />
          <Route path="/analytics" element={<AnalyticsPage />} />
          <Route path="/team-chat" element={<TeamChatPage />} />
          <Route path="/channels" element={<ChannelsPage />} />
          <Route path="/settings" element={<UISettingsPage />} />
          <Route path="/company-settings/*" element={<CompanySettingsPage />} />
          <Route path="/roles" element={<RolesPermissionsPage />} />
        </Route>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </Suspense>
  );
}
