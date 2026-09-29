import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { AiStatusBadge } from "./AiStatusBadge";
import { BrandMark, Icon, initials } from "./ui";

export function Layout() {
  const { user, role, logout } = useAuth();
  const navigate = useNavigate();
  const name = user?.full_name || user?.username || "";
  const link = ({ isActive }: { isActive: boolean }) => (isActive ? "active" : "");

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand">
          <BrandMark />
          <span>Densito-AI<small>Контроль качества денситометрии</small></span>
        </div>
        <nav>
          <NavLink to="/studies" className={link}>Исследования</NavLink>
          <NavLink to="/batch" className={link}>Пакетная обработка</NavLink>
          {role === "admin" && (
            <>
              <NavLink to="/admin/violations" className={link}>Справочник нарушений</NavLink>
              <NavLink to="/admin/users" className={link}>Пользователи</NavLink>
              <NavLink to="/admin/stats" className={link}>Статистика</NavLink>
            </>
          )}
        </nav>
        <AiStatusBadge />
        <div className="user-box">
          <div className="avatar">{initials(name)}</div>
          <div className="user-meta">
            <b>{name}</b>
            <span>{role === "admin" ? "Администратор" : "Эксперт"}</span>
          </div>
          <button type="button" className="btn-ghost btn-sm" title="Выйти"
                  onClick={() => { logout(); navigate("/login"); }}>
            <Icon name="logout" /> Выйти
          </button>
        </div>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
