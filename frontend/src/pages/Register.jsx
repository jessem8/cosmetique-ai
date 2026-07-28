import { useState, useMemo } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { auth } from '../api/client.js'

function getPasswordStrength(password) {
  if (!password) return { level: 'none', label: '', score: 0 }
  let score = 0
  if (password.length >= 8) score++
  if (/[A-Z]/.test(password)) score++
  if (/[0-9]/.test(password)) score++
  if (/[^A-Za-z0-9]/.test(password)) score++

  if (score === 1) return { level: 'weak', label: 'Faible', score }
  if (score === 2) return { level: 'fair', label: 'Moyen', score }
  if (score === 3) return { level: 'good', label: 'Bon', score }
  if (score === 4) return { level: 'strong', label: 'Fort', score }
  return { level: 'none', label: '', score: 0 }
}

function Register() {
  const navigate = useNavigate()
  const [form, setForm] = useState({
    email: '',
    password: '',
    confirmPassword: '',
  })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)

  const strength = useMemo(
    () => getPasswordStrength(form.password),
    [form.password]
  )

  const reqs = [
    { met: form.password.length >= 8, label: 'Au moins 8 caractères' },
    { met: /[A-Z]/.test(form.password), label: 'Une lettre majuscule' },
    { met: /[0-9]/.test(form.password), label: 'Un chiffre' },
  ]

  const handleChange = (e) => {
    setForm((prev) => ({ ...prev, [e.target.name]: e.target.value }))
    setError('')
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!form.email || !form.password || !form.confirmPassword) {
      setError('Veuillez remplir tous les champs.')
      return
    }
    if (form.password !== form.confirmPassword) {
      setError('Les mots de passe ne correspondent pas.')
      return
    }
    if (form.password.length < 8) {
      setError('Le mot de passe doit contenir au moins 8 caractères.')
      return
    }

    setLoading(true)
    setError('')
    try {
      // Register
      await auth.register({ email: form.email, password: form.password })
      // Auto-login
      const loginRes = await auth.login({
        email: form.email,
        password: form.password,
      })
      const data = loginRes.data
      const token = data.access_token || data.token || data.data?.access_token
      const email = data.email || data.user?.email || form.email
      localStorage.setItem('cosmetique_ai_token', token)
      localStorage.setItem('cosmetique_ai_email', email)
      navigate('/dashboard')
    } catch (err) {
      const msg =
        err.response?.data?.detail ||
        err.response?.data?.message ||
        err.message ||
        "Une erreur est survenue lors de l'inscription."
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
          <span className="auth-logo-icon">✨</span>
          <div className="auth-logo">Cosmetique AI</div>

          <h2 className="auth-tagline">
            Rejoignez la<br />
            <span style={{ color: 'var(--gold)' }}>révolution</span> créative
          </h2>
          <p className="auth-description">
            Créez un compte gratuit et commencez à générer vos
            premières affiches cosmétiques IA en moins de 2 minutes.
          </p>

          <div className="auth-features">
            <div className="auth-feature">
              <div className="auth-feature-icon">🆓</div>
              <span>Essai gratuit sans carte bancaire</span>
            </div>
            <div className="auth-feature">
              <div className="auth-feature-icon">⚡</div>
              <span>Résultats en moins de 90 secondes</span>
            </div>
            <div className="auth-feature">
              <div className="auth-feature-icon">🌟</div>
              <span>Qualité professionnelle garantie</span>
            </div>
            <div className="auth-feature">
              <div className="auth-feature-icon">📦</div>
              <span>3 formats téléchargeables instantanément</span>
            </div>
          </div>
        </div>
      </div>

      {/* Right panel — Form */}
      <div className="auth-right">
        <div className="auth-form-container animate-enter animate-enter-delay-1">
          <h1 className="auth-form-title">Créer un compte ✦</h1>
          <p className="auth-form-subtitle">
            Rejoignez des milliers de professionnels de la beauté
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
                  autoComplete="new-password"
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
                  }}
                  tabIndex={-1}
                >
                  {showPassword ? '🙈' : '👁'}
                </button>
              </div>

              {/* Password strength indicator */}
              {form.password && (
                <div className="password-strength">
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      marginBottom: '4px',
                      fontSize: '0.75rem',
                    }}
                  >
                    <span style={{ color: 'var(--text-muted)' }}>
                      Sécurité
                    </span>
                    <span
                      style={{
                        color:
                          strength.level === 'strong'
                            ? 'var(--success)'
                            : strength.level === 'good'
                            ? 'var(--info)'
                            : strength.level === 'fair'
                            ? 'var(--warning)'
                            : 'var(--error)',
                        fontWeight: 600,
                      }}
                    >
                      {strength.label}
                    </span>
                  </div>
                  <div className="password-strength-bar">
                    <div
                      className={`password-strength-fill ${strength.level}`}
                    />
                  </div>
                  <div className="password-requirements">
                    {reqs.map((req, i) => (
                      <div
                        key={i}
                        className={`password-req${req.met ? ' met' : ''}`}
                      >
                        <span className="password-req-icon">
                          {req.met ? '✓' : '○'}
                        </span>
                        {req.label}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Confirm password */}
            <div className="form-group">
              <label className="form-label" htmlFor="confirmPassword">
                Confirmer le mot de passe
              </label>
              <div className="input-wrapper">
                <span className="input-icon">🔑</span>
                <input
                  id="confirmPassword"
                  name="confirmPassword"
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="new-password"
                  className={`input-field${
                    form.confirmPassword && form.confirmPassword !== form.password
                      ? ' error'
                      : ''
                  }`}
                  placeholder="••••••••"
                  value={form.confirmPassword}
                  onChange={handleChange}
                  disabled={loading}
                />
              </div>
              {form.confirmPassword && form.confirmPassword !== form.password && (
                <span className="form-error">
                  Les mots de passe ne correspondent pas
                </span>
              )}
            </div>

            {/* Submit */}
            <button
              type="submit"
              className={`btn btn-primary btn-primary--large${loading ? ' btn-loading' : ''}`}
              disabled={loading}
              style={{ width: '100%' }}
            >
              <span>{loading ? '' : '🚀 Créer mon compte'}</span>
            </button>
          </form>

          <div className="auth-divider">ou</div>

          <p className="auth-link-row">
            Déjà un compte ?{' '}
            <Link to="/login" style={{ fontWeight: 600 }}>
              Se connecter
            </Link>
          </p>
        </div>
      </div>
    </div>
  )
}

export default Register
