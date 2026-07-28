import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { auth } from '../api/client.js'

function Login() {
  const navigate = useNavigate()
  const [form, setForm] = useState({ email: '', password: '' })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)

  const handleChange = (e) => {
    setForm((prev) => ({ ...prev, [e.target.name]: e.target.value }))
    setError('')
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!form.email || !form.password) {
      setError('Veuillez remplir tous les champs.')
      return
    }
    setLoading(true)
    setError('')
    try {
      const res = await auth.login(form)
      const data = res.data
      const token = data.access_token || data.token || data.data?.access_token
      const email = data.email || data.user?.email || form.email
      if (!token) throw new Error('Token non reçu')
      localStorage.setItem('cosmetique_ai_token', token)
      localStorage.setItem('cosmetique_ai_email', email)
      navigate('/dashboard')
    } catch (err) {
      const msg =
        err.response?.data?.detail ||
        err.response?.data?.message ||
        err.message ||
        'Identifiants incorrects. Veuillez réessayer.'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="auth-page">
      {/* Left panel */}
      <div className="auth-left">
        <div className="auth-left-orb auth-left-orb-1" />
        <div className="auth-left-orb auth-left-orb-2" />

        <div className="auth-left-content animate-enter">
          <div className="auth-logo">Cosmetique AI</div>

          <h2 className="auth-tagline">
            Créez des affiches<br />
            <span style={{ color: 'var(--rose)' }}>premium</span> en secondes
          </h2>
          <p className="auth-description">
            Transformez vos photos produits en contenu marketing
            professionnel pour Instagram, Facebook et LinkedIn.
          </p>

          <div className="auth-features">
            <div className="auth-feature">
              <div className="auth-feature-icon">🤖</div>
              <span>Génération IA ultra-réaliste</span>
            </div>
            <div className="auth-feature">
              <div className="auth-feature-icon">🎨</div>
              <span>Décors et compositions automatiques</span>
            </div>
            <div className="auth-feature">
              <div className="auth-feature-icon">✍️</div>
              <span>Textes marketing sur mesure</span>
            </div>
            <div className="auth-feature">
              <div className="auth-feature-icon">📲</div>
              <span>3 formats exportés en un clic</span>
            </div>
          </div>
        </div>
      </div>

      {/* Right panel — Form */}
      <div className="auth-right">
        <div className="auth-form-container animate-enter animate-enter-delay-1">
          <h1 className="auth-form-title">Bienvenue</h1>
          <p className="auth-form-subtitle">
            Connectez-vous à votre espace créatif
          </p>

          <form className="auth-form" onSubmit={handleSubmit} noValidate>
            {/* Error */}
            {error && (
              <div className="auth-error">
                <span>⚠️</span>
                {error}
              </div>
            )}

            {/* Email */}
            <div className="form-group">
              <label className="form-label" htmlFor="email">
                Adresse e-mail
              </label>
              <div className="input-wrapper">
                <span className="input-icon">✉</span>
                <input
                  id="email"
                  name="email"
                  type="email"
                  autoComplete="email"
                  className="input-field"
                  placeholder="vous@exemple.com"
                  value={form.email}
                  onChange={handleChange}
                  disabled={loading}
                />
              </div>
            </div>

            {/* Password */}
            <div className="form-group">
              <label className="form-label" htmlFor="password">
                Mot de passe
              </label>
              <div className="input-wrapper" style={{ position: 'relative' }}>
                <span className="input-icon">🔒</span>
                <input
                  id="password"
                  name="password"
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="current-password"
                  className="input-field"
                  placeholder="••••••••"
                  value={form.password}
                  onChange={handleChange}
                  disabled={loading}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  style={{
                    position: 'absolute',
                    right: '14px',
                    top: '50%',
                    transform: 'translateY(-50%)',
                    background: 'none',
                    border: 'none',
                    color: 'var(--text-muted)',
                    cursor: 'pointer',
                    fontSize: '0.85rem',
                    padding: '2px',
                    transition: 'color 0.2s',
                  }}
                  tabIndex={-1}
                >
                  {showPassword ? '🙈' : '👁'}
                </button>
              </div>
            </div>

            {/* Submit */}
            <button
              type="submit"
              className={`btn btn-primary btn-primary--large${loading ? ' btn-loading' : ''}`}
              disabled={loading}
              style={{ width: '100%' }}
            >
              <span>
                {loading ? '' : 'Se connecter'}
              </span>
            </button>
          </form>

          <div className="auth-divider">ou</div>

          <p className="auth-link-row">
            Pas encore de compte ?{' '}
            <Link to="/register" style={{ fontWeight: 600 }}>
              Créer un compte
            </Link>
          </p>
        </div>
      </div>
    </div>
  )
}

export default Login
