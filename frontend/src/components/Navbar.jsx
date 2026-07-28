import { useState } from 'react'
import { NavLink, useNavigate, Link } from 'react-router-dom'

function Navbar() {
  const navigate = useNavigate()
  const email = localStorage.getItem('cosmetique_ai_email') || ''
  const [menuOpen, setMenuOpen] = useState(false)

  const handleLogout = () => {
    localStorage.removeItem('cosmetique_ai_token')
    localStorage.removeItem('cosmetique_ai_email')
    navigate('/login')
  }

  const initials = email
    ? email.slice(0, 2).toUpperCase()
    : 'AI'

  return (
    <nav className="navbar">
      <div className="navbar-inner">
        {/* Logo */}
        <Link to="/dashboard" className="navbar-logo">
          <span className="navbar-logo-text">Cosmetique AI</span>
        </Link>

        {/* Navigation links */}
        <div className="navbar-nav">
          <NavLink
            to="/dashboard"
            className={({ isActive }) =>
              `navbar-link${isActive ? ' active' : ''}`
            }
          >
            Dashboard
          </NavLink>
          <NavLink
            to="/new"
            className={({ isActive }) =>
              `navbar-link${isActive ? ' active' : ''}`
            }
          >
            Nouveau produit
          </NavLink>
        </div>

        {/* Right section */}
        <div className="navbar-right">
          {email && (
            <div className="navbar-user">
              <div className="navbar-user-avatar">{initials}</div>
              <span className="navbar-user-email">{email}</span>
            </div>
          )}
          <button className="navbar-logout" onClick={handleLogout}>
            <span>⏻</span>
            Déconnexion
          </button>

          {/* Mobile hamburger */}
          <button
            className="navbar-hamburger"
            onClick={() => setMenuOpen(!menuOpen)}
            aria-label="Menu"
          >
            {menuOpen ? '✕' : '☰'}
          </button>
        </div>
      </div>

      {/* Mobile menu */}
      {menuOpen && (
        <div
          style={{
            position: 'absolute',
            top: '64px',
            left: 0,
            right: 0,
            background: 'rgba(254, 248, 243, 0.97)',
            backdropFilter: 'blur(20px)',
            borderBottom: '1px solid rgba(232, 130, 154, 0.18)',
            boxShadow: '0 8px 24px rgba(180, 100, 120, 0.10)',
            padding: '12px 24px',
            display: 'flex',
            flexDirection: 'column',
            gap: '4px',
            zIndex: 999,
          }}
        >
          <NavLink
            to="/dashboard"
            className={({ isActive }) =>
              `navbar-link${isActive ? ' active' : ''}`
            }
            onClick={() => setMenuOpen(false)}
          >
            Dashboard
          </NavLink>
          <NavLink
            to="/new"
            className={({ isActive }) =>
              `navbar-link${isActive ? ' active' : ''}`
            }
            onClick={() => setMenuOpen(false)}
          >
            Nouveau produit
          </NavLink>
          {email && (
            <div
              style={{
                padding: '8px 14px',
                fontSize: '0.82rem',
                color: 'var(--text-muted)',
                borderTop: '1px solid var(--border-subtle)',
                marginTop: '8px',
              }}
            >
              {email}
            </div>
          )}
        </div>
      )}
    </nav>
  )
}

export default Navbar
