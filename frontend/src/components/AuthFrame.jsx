import { CheckCircle, LockKey, Sparkle } from '@phosphor-icons/react'
import { Link } from 'react-router-dom'
import BrandMark from './BrandMark.jsx'

function AuthFrame({ children }) {
  return (
    <main className="auth-shell">
      <section className="auth-story" aria-labelledby="auth-story-title">
        <Link to="/" className="brand brand--auth" aria-label="Retour à l’accueil public">
          <BrandMark />
        </Link>
        <div className="auth-story__copy">
          <p className="eyebrow">Extraction produit</p>
          <h2 id="auth-story-title">Votre produit, isolé avec preuve.</h2>
          <p>Un espace privé pour importer la source, vérifier le masque et récupérer un détourage transparent exploitable.</p>
        </div>
        <ul className="auth-story__facts">
          <li><CheckCircle size={19} weight="light" aria-hidden="true" />Product Lock avant toute composition</li>
          <li><Sparkle size={19} weight="light" aria-hidden="true" />Masque réel et détourage transparent</li>
          <li><LockKey size={19} weight="light" aria-hidden="true" />Corrections et provenance conservées</li>
        </ul>
        <p className="auth-story__signature">Pour les équipes qui veulent un rendu précis, pas une promesse vague.</p>
      </section>
      <section className="auth-form-stage">{children}</section>
    </main>
  )
}

export default AuthFrame
