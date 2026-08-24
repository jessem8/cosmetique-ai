import { Component, useSyncExternalStore } from 'react'
import {
  Navigate,
  Route,
  Routes,
  useLocation,
} from 'react-router-dom'
import Navbar from './components/Navbar.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Landing from './pages/Landing.jsx'
import Login from './pages/Login.jsx'
import Register from './pages/Register.jsx'
import StudioWorkspace from './studio/StudioWorkspace.jsx'
import {
  hasUsableSession,
  subscribeToSession,
} from './auth/session.js'

const useAuthenticated = () =>
  useSyncExternalStore(subscribeToSession, hasUsableSession, () => false)

function RequireAuth({ children }) {
  const location = useLocation()
  const authenticated = useAuthenticated()
  if (!authenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }
  return children
}

function PublicOnly({ children }) {
  const authenticated = useAuthenticated()
  return authenticated ? <Navigate to="/dashboard" replace /> : children
}

function AppShell({ children }) {
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Aller au contenu
      </a>
      <Navbar />
      <main id="main-content" tabIndex="-1">
        {children}
      </main>
      <footer className="site-footer">
        <span>Cosmetique AI</span>
        <span>Studio privé d’extraction produit</span>
      </footer>
    </div>
  )
}

function NotFound() {
  return (
    <div className="workspace-page">
      <section className="empty-state">
        <p className="eyebrow">404</p>
        <h1>Page introuvable</h1>
        <p>Ce lien ne correspond à aucune vue de l’extraction.</p>
        <a className="button button--primary" href="/new">
          Retour à l’extraction
        </a>
      </section>
    </div>
  )
}

class RouteErrorBoundary extends Component {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    if (this.state.failed) {
      return (
        <div className="workspace-page">
          <section className="empty-state empty-state--error">
            <h1>Cette vue ne peut pas être affichée.</h1>
            <p>Rechargez le studio. Aucun travail serveur n’a été modifié.</p>
            <button
              type="button"
              className="button button--primary"
              onClick={() => window.location.reload()}
            >
              Recharger
            </button>
          </section>
        </div>
      )
    }
    return this.props.children
  }
}

const protectedRoute = (node) => (
  <RequireAuth>
    <AppShell>
      <RouteErrorBoundary>{node}</RouteErrorBoundary>
    </AppShell>
  </RequireAuth>
)

function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route
        path="/login"
        element={
          <PublicOnly>
            <Login />
          </PublicOnly>
        }
      />
      <Route
        path="/register"
        element={
          <PublicOnly>
            <Register />
          </PublicOnly>
        }
      />
      <Route path="/dashboard" element={protectedRoute(<Dashboard />)} />
      <Route path="/new" element={protectedRoute(<StudioWorkspace />)} />
      {/* Legacy deep links are retained as redirects, not as a second product. */}
      <Route path="/studio-v2" element={<Navigate to="/new" replace />} />
      <Route path="/studio/v2" element={<Navigate to="/new" replace />} />
      <Route path="/campaigns/:id/studio" element={protectedRoute(<StudioWorkspace />)} />
      {/* Legacy generation links return to the only supported extraction workflow. */}
      <Route path="/generations/:id" element={<Navigate to="/new" replace />} />
      <Route path="/result/:id" element={<Navigate to="/new" replace />} />
      <Route path="*" element={protectedRoute(<NotFound />)} />
    </Routes>
  )
}

export default App
