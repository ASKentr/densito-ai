import { Navigate, Route, BrowserRouter, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { Layout } from "./components/Layout";
import { AdminStatsPage } from "./pages/AdminStatsPage";
import { AdminUsersPage } from "./pages/AdminUsersPage";
import { AdminViolationsPage } from "./pages/AdminViolationsPage";
import { BatchPage } from "./pages/BatchPage";
import { LoginPage } from "./pages/LoginPage";
import { StudiesListPage } from "./pages/StudiesListPage";
import { StudyCardPage } from "./pages/StudyCardPage";

function ProtectedRoute({ children, adminOnly }: { children: React.ReactNode; adminOnly?: boolean }) {
  const { role, loading } = useAuth();
  if (loading) return <p className="muted">Загрузка…</p>;
  if (!role) return <Navigate to="/login" replace />;
  if (adminOnly && role !== "admin") return <Navigate to="/studies" replace />;
  return <>{children}</>;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute><Layout /></ProtectedRoute>}>
        <Route path="/studies" element={<StudiesListPage />} />
        <Route path="/studies/:id" element={<StudyCardPage />} />
        <Route path="/batch" element={<BatchPage />} />
        <Route path="/admin/violations" element={<ProtectedRoute adminOnly><AdminViolationsPage /></ProtectedRoute>} />
        <Route path="/admin/users" element={<ProtectedRoute adminOnly><AdminUsersPage /></ProtectedRoute>} />
        <Route path="/admin/stats" element={<ProtectedRoute adminOnly><AdminStatsPage /></ProtectedRoute>} />
      </Route>
      <Route path="*" element={<Navigate to="/studies" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  );
}
