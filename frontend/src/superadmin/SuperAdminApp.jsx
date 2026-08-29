import { Suspense, lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { PlatformAuthProvider } from "./PlatformAuthContext";
import ConsoleShell from "./components/ConsoleShell";
import PlatformProtectedRoute from "./components/PlatformProtectedRoute";
import { ConsoleLoading } from "./components/ConsoleUI";
import "./superadmin.css";

// Split the same way the customer app splits its routes, so an employee who
// never opens the console never downloads it.
const PlatformLoginPage = lazy(() => import("./pages/PlatformLoginPage"));
const PlatformEnrolTotpPage = lazy(() => import("./pages/PlatformEnrolTotpPage"));
const CompaniesPage = lazy(() => import("./pages/CompaniesPage"));
const CompanyDetailPage = lazy(() => import("./pages/CompanyDetailPage"));
const NewCompanyPage = lazy(() => import("./pages/NewCompanyPage"));
const PlatformAdminsPage = lazy(() => import("./pages/PlatformAdminsPage"));
const AuditLogPage = lazy(() => import("./pages/AuditLogPage"));
const HealthPage = lazy(() => import("./pages/HealthPage"));


function RouteFallback() {
  return (
    <main className="sa-fullscreen">
      <ConsoleLoading label="Loading the console..." />
    </main>
  );
}

export default function SuperAdminApp() {
  return (
    <div className="superadmin-root">
      <PlatformAuthProvider>
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route path="login" element={<PlatformLoginPage />} />
            <Route path="enroll" element={<PlatformEnrolTotpPage />} />

            <Route
              element={
                <PlatformProtectedRoute>
                  <ConsoleShell />
                </PlatformProtectedRoute>
              }
            >
              <Route index element={<Navigate to="companies" replace />} />
              <Route path="companies" element={<CompaniesPage />} />
              <Route path="companies/new" element={<NewCompanyPage />} />
              <Route path="companies/:companyId" element={<CompanyDetailPage />} />
              <Route path="admins" element={<PlatformAdminsPage />} />
              <Route path="audit" element={<AuditLogPage />} />
              <Route path="health" element={<HealthPage />} />
              <Route path="*" element={<Navigate to="companies" replace />} />
            </Route>
          </Routes>
        </Suspense>
      </PlatformAuthProvider>
    </div>
  );
}
