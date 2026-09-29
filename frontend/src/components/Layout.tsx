import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export function Layout() {
  const { user, role, logout } = useAuth();
  const navigate = useNavigate();

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand">Densito-AI</div>
        <nav>
          <NavLink to="/studies" className={({ isActive }) => (isActive ? "active" : "")}>Исследования</NavLink>
          {role === "admin" && (
            <>
              <NavLink to="/admin/violations" className={({ isActive }) => (isActive ? "active" : "")}>Справочник нарушений</NavLink>
              <NavLink to="/admin/users" className={({ isActive }) => (isActive ? "active" : "")}>Пользователи</NavLink>
              <NavLink to="/admin/stats" className={({ isActive }) => (isActive ? "active" : "")}>Статистика</NavLink>
            </>
          )}
        </nav>
        <div className="user-box">
          <span>{user?.full_name || user?.username} <em>({role === "admin" ? "Администратор" : "Эксперт"})</em></span>
          <button type="button" onClick={() => { logout(); navigate("/login"); }}>Выйти</button>
        </div>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
