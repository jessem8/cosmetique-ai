import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, useNavigate } from 'react-router-dom'
import { List, Plus, SignOut, X } from '@phosphor-icons/react'
import { clearSession, getSession } from '../auth/session.js'
import BrandMark from './BrandMark.jsx'

function Navbar() {
  const navigate = useNavigate()
  const { email } = getSession()
  const [open, setOpen] = useState(false)
  const triggerRef = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    const handleEscape = (event) => {
      if (event.key === 'Escape') {
        setOpen(false)
        triggerRef.current?.focus()
      }
    }
    document.addEventListener('keydown', handleEscape)
    return () => document.removeEventListener('keydown', handleEscape)
  }, [open])

  const close = () => setOpen(false)
  const logout = () => {
    clearSession()
    navigate('/login', { replace: true })
  }

  return (
    <header className="site-header">
      <nav className="navbar" aria-label="Navigation principale">
        <Link className="brand" to="/" onClick={close} aria-label="Cosmetique AI, accueil public">
          <BrandMark />
        </Link>

        <div className="navbar__links">
          <NavLink
            to="/dashboard"
            className={({ isActive }) => (isActive ? 'is-active' : undefined)}
          >
            Extractions
          </NavLink>
        </div>

        <div className="navbar__account">
          <span className="navbar__email" title={email || undefined}>{email || 'Espace privé'}</span>
          <Link className="button button--primary button--small" to="/new">
            <Plus size={16} aria-hidden="true" />
            Extraire un produit
          </Link>
          <button
            type="button"
            className="icon-button navbar__signout"
            aria-label="Se déconnecter"
            onClick={logout}
          >
            <SignOut size={19} aria-hidden="true" />
          </button>
          <button
            ref={triggerRef}
            type="button"
            className="icon-button navbar__menu-trigger"
            aria-expanded={open}
            aria-controls="mobile-navigation"
            aria-label={open ? 'Fermer le menu' : 'Ouvrir le menu'}
            onClick={() => setOpen((current) => !current)}
          >
            {open ? (
              <X size={20} aria-hidden="true" />
            ) : (
              <List size={20} aria-hidden="true" />
            )}
          </button>
        </div>
      </nav>

      {open && (
        <div id="mobile-navigation" className="mobile-navigation">
          <NavLink to="/dashboard" onClick={close}>
            Extractions
          </NavLink>
          <NavLink to="/new" onClick={close}>
            Extraire un produit
          </NavLink>
          <button type="button" onClick={logout}>
            <SignOut size={18} aria-hidden="true" />
            Se déconnecter
          </button>
        </div>
      )}
    </header>
  )
}

export default Navbar
