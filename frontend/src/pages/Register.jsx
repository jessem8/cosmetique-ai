import {
  Check,
  EnvelopeSimple,
  Eye,
  EyeSlash,
  LockKey,
} from '@phosphor-icons/react'
import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { auth } from '../api/client.js'
import { setSession } from '../auth/session.js'
import AuthFrame from '../components/AuthFrame.jsx'

const passwordRequirements = (password) => [
  { label: '8 caractères minimum', met: password.length >= 8 },
  { label: 'Une lettre majuscule', met: /[A-Z]/.test(password) },
  { label: 'Un chiffre', met: /\d/.test(password) },
]

function Register() {
  const navigate = useNavigate()
  const [form, setForm] = useState({
    email: '',
    password: '',
    confirmPassword: '',
  })
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [loading, setLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const requirements = useMemo(
    () => passwordRequirements(form.password),
    [form.password]
  )

  const handleChange = ({ target }) => {
    setForm((current) => ({ ...current, [target.name]: target.value }))
    setError('')
  }

  const handleSubmit = async (event) => {
    event.preventDefault()
    setError('')
    setSuccess('')

    if (!form.email.trim() || !form.password || !form.confirmPassword) {
      setError('Veuillez remplir tous les champs.')
      return
    }
    if (!requirements.every(({ met }) => met)) {
      setError('Le mot de passe ne respecte pas encore les trois critères.')
      return
    }
    if (form.password !== form.confirmPassword) {
      setError('Les mots de passe ne correspondent pas.')
      return
    }

    setLoading(true)
    try {
      const credentials = {
        email: form.email.trim(),
        password: form.password,
      }
      await auth.register(credentials)

      try {
        const { data } = await auth.login(credentials)
        const token = data.access_token || data.token || data.data?.access_token
        const email = data.email || data.user?.email || credentials.email
        setSession({ token, email })
        navigate('/dashboard', { replace: true })
      } catch {
        setSuccess(
          'Votre compte est créé. Vous pouvez maintenant vous connecter.'
        )
      }
    } catch (requestError) {
      setError(
        requestError.message ||
          'Impossible de créer votre compte pour le moment.'
      )
    } finally {
      setLoading(false)
    }
  }

  const confirmationMismatch =
    Boolean(form.confirmPassword) && form.confirmPassword !== form.password

  return (
    <AuthFrame>
      <div className="auth-card">
        <div className="auth-card__heading">
          <p className="eyebrow">Nouveau studio</p>
          <h1>Créer votre accès</h1>
          <p>Un compte suffit pour conserver vos briefs et vos campagnes.</p>
        </div>

        <form className="auth-form" onSubmit={handleSubmit} noValidate>
          {error ? (
            <div className="alert alert--error" role="alert">
              {error}
            </div>
          ) : null}
          {success ? (
            <div className="alert alert--success" role="status">
              {success} <Link to="/login">Accéder à la connexion</Link>
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
                autoComplete="new-password"
                placeholder="Choisissez un mot de passe"
                value={form.password}
                onChange={handleChange}
                disabled={loading}
                aria-describedby="password-rules"
                required
              />
              <button
                className="field__reveal"
                type="button"
                onClick={() => setShowPassword((visible) => !visible)}
                aria-label={
                  showPassword
                    ? 'Masquer les mots de passe'
                    : 'Afficher les mots de passe'
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
            <ul className="password-rules" id="password-rules">
              {requirements.map(({ label, met }) => (
                <li className={met ? 'is-met' : ''} key={label}>
                  <Check size={14} weight="bold" aria-hidden="true" />
                  {label}
                </li>
              ))}
            </ul>
          </div>

          <div className="field">
            <label htmlFor="confirmPassword">Confirmer le mot de passe</label>
            <div
              className={`field__control${confirmationMismatch ? ' is-invalid' : ''}`}
            >
              <LockKey size={19} aria-hidden="true" />
              <input
                id="confirmPassword"
                name="confirmPassword"
                type={showPassword ? 'text' : 'password'}
                autoComplete="new-password"
                placeholder="Saisissez-le à nouveau"
                value={form.confirmPassword}
                onChange={handleChange}
                disabled={loading}
                aria-invalid={confirmationMismatch}
                aria-describedby={
                  confirmationMismatch ? 'password-mismatch' : undefined
                }
                required
              />
            </div>
            {confirmationMismatch ? (
              <p className="field__error" id="password-mismatch">
                Les mots de passe ne correspondent pas.
              </p>
            ) : null}
          </div>

          <button className="button button--primary button--wide" disabled={loading}>
            {loading ? 'Création en cours…' : 'Créer mon compte'}
          </button>
        </form>

        <p className="auth-card__switch">
          Déjà un compte ? <Link to="/login">Se connecter</Link>
        </p>
      </div>
    </AuthFrame>
  )
}

export default Register
