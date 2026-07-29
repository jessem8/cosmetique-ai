import {
  EnvelopeSimple,
  Eye,
  EyeSlash,
  LockKey,
} from '@phosphor-icons/react'
import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { auth } from '../api/client.js'
import { setSession } from '../auth/session.js'
import AuthFrame from '../components/AuthFrame.jsx'

const requestedDestination = (location) => {
  const destination = location.state?.from?.pathname
  return typeof destination === 'string' &&
    destination.startsWith('/') &&
    destination !== '/login'
    ? destination
    : '/dashboard'
}

function Login() {
  const location = useLocation()
  const navigate = useNavigate()
  const [form, setForm] = useState({ email: '', password: '' })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)

  const handleChange = ({ target }) => {
    setForm((current) => ({ ...current, [target.name]: target.value }))
    setError('')
  }

  const handleSubmit = async (event) => {
    event.preventDefault()
    if (!form.email.trim() || !form.password) {
      setError('Veuillez renseigner votre adresse e-mail et votre mot de passe.')
      return
    }

    setLoading(true)
    setError('')
    try {
      const { data } = await auth.login({
        email: form.email.trim(),
        password: form.password,
      })
      const token = data.access_token || data.token || data.data?.access_token
      const email = data.email || data.user?.email || form.email
      setSession({ token, email })
      navigate(requestedDestination(location), { replace: true })
    } catch (requestError) {
      setError(
        requestError.message || 'Impossible de vous connecter pour le moment.'
      )
    } finally {
      setLoading(false)
    }
  }

  return (
    <AuthFrame>
      <div className="auth-card">
        <div className="auth-card__heading">
          <p className="eyebrow">Espace privé</p>
          <h1>Accéder au studio</h1>
          <p>Retrouvez vos campagnes et démarrez un nouveau brief produit.</p>
        </div>

        <form className="auth-form" onSubmit={handleSubmit} noValidate>
          {error ? (
            <div className="alert alert--error" role="alert">
              {error}
            </div>
          ) : null}

          <div className="field">
            <label htmlFor="email">Adresse e-mail</label>
            <div className="field__control">
              <EnvelopeSimple size={19} aria-hidden="true" />
              <input
                id="email"
                name="email"
                type="email"
                autoComplete="email"
                placeholder="studio@marque.fr"
                value={form.email}
                onChange={handleChange}
                disabled={loading}
                required
              />
            </div>
          </div>

          <div className="field">
            <label htmlFor="password">Mot de passe</label>
            <div className="field__control">
              <LockKey size={19} aria-hidden="true" />
              <input
                id="password"
                name="password"
                type={showPassword ? 'text' : 'password'}
                autoComplete="current-password"
                placeholder="Votre mot de passe"
                value={form.password}
                onChange={handleChange}
                disabled={loading}
                required
              />
              <button
                className="field__reveal"
                type="button"
                onClick={() => setShowPassword((visible) => !visible)}
                aria-label={
                  showPassword
                    ? 'Masquer le mot de passe'
                    : 'Afficher le mot de passe'
                }
                aria-pressed={showPassword}
              >
                {showPassword ? (
                  <EyeSlash size={19} aria-hidden="true" />
                ) : (
                  <Eye size={19} aria-hidden="true" />
                )}
              </button>
            </div>
          </div>

          <button className="button button--primary button--wide" disabled={loading}>
            {loading ? 'Connexion en cours…' : 'Se connecter'}
          </button>
        </form>

        <p className="auth-card__switch">
          Pas encore de compte ? <Link to="/register">Créer un compte</Link>
        </p>
      </div>
    </AuthFrame>
  )
}

export default Login
