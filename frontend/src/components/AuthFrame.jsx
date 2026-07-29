import { CheckCircle } from '@phosphor-icons/react'

function AuthFrame({ children }) {
  return (
    <main className="auth-shell">
      <section className="auth-story" aria-labelledby="auth-story-title">
        <div className="brand brand--auth">
          <span className="brand__monogram" aria-hidden="true">
            CA
          </span>
          <span className="brand__name">Cosmetique AI</span>
        </div>
        <div className="auth-story__copy">
          <p className="eyebrow">Studio de campagne produit</p>
          <h2 id="auth-story-title">
            Votre produit, intact. La scène, entièrement composée.
          </h2>
          <p>
            Préparez un dossier Instagram, Facebook et LinkedIn à partir d’un
            seul brief vérifié.
          </p>
        </div>
        <ul className="auth-story__facts">
          <li>
            <CheckCircle size={19} weight="light" aria-hidden="true" />
            Trois formats exacts et indépendants
          </li>
          <li>
            <CheckCircle size={19} weight="light" aria-hidden="true" />
            Produit source préservé pendant la composition
          </li>
          <li>
            <CheckCircle size={19} weight="light" aria-hidden="true" />
            Texte limité aux faits que vous fournissez
          </li>
        </ul>
      </section>
      <section className="auth-form-stage">{children}</section>
    </main>
  )
}

export default AuthFrame
