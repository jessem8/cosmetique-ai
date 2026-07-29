import { Component, useSyncExternalStore } from 'react'
import {
  Navigate,
  Route,
  Routes,
  useLocation,
} from 'react-router-dom'
import Navbar from './components/Navbar.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Generation from './pages/Generation.jsx'
import Login from './pages/Login.jsx'
import Register from './pages/Register.jsx'
import Result from './pages/Result.jsx'
import Upload from './pages/Upload.jsx'
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
        <span>Studio privé de campagne produit</span>
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
        <p>Ce lien ne correspond à aucune vue du studio.</p>
        <a className="button button--primary" href="/dashboard">
          Retour aux campagnes
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
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
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
      <Route path="/new" element={protectedRoute(<Upload />)} />
      <Route
        path="/generations/:id"
        element={protectedRoute(<Generation />)}
      />
      <Route path="/result/:id" element={protectedRoute(<Result />)} />
      <Route path="*" element={protectedRoute(<NotFound />)} />
    </Routes>
  )
}

export default App
