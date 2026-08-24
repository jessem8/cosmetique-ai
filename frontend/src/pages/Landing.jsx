import { ArrowUpRight, Check, ImageSquare, LockKey, Scissors, ShieldCheck } from '@phosphor-icons/react'
import { Link } from 'react-router-dom'
import { getSession, hasUsableSession } from '../auth/session.js'
import BrandMark from '../components/BrandMark.jsx'

function ExtractionPreview() {
  return (
    <div className="landing-extraction-preview" aria-label="Workflow d’extraction réel">
      <div className="landing-extraction-preview__row">
        <ImageSquare size={24} weight="light" aria-hidden="true" />
        <div><strong>Source originale</strong><span>Image privée, pixels immuables</span></div>
        <Check size={18} weight="bold" aria-hidden="true" />
      </div>
      <div className="landing-extraction-preview__connector" aria-hidden="true" />
      <div className="landing-extraction-preview__row">
        <Scissors size={24} weight="light" aria-hidden="true" />
        <div><strong>Masque binaire</strong><span>Produit détecté et contrôlé</span></div>
        <Check size={18} weight="bold" aria-hidden="true" />
      </div>
      <div className="landing-extraction-preview__connector" aria-hidden="true" />
      <div className="landing-extraction-preview__row is-final">
        <ShieldCheck size={24} weight="light" aria-hidden="true" />
        <div><strong>Cutout transparent</strong><span>PNG réel prêt à vérifier</span></div>
        <span className="landing-extraction-preview__status">À inspecter</span>
      </div>
    </div>
  )
}

function Landing() {
  const authenticated = hasUsableSession()
  const email = getSession().email

  return (
    <div className="landing-page">
      <header className="landing-nav" aria-label="Navigation publique">
        <Link to="/" className="landing-brand"><BrandMark /><span>Cosmetique AI</span></Link>
        <nav className="landing-nav__links">
          <a href="#method">Méthode</a>
          <a href="#proof">Contrôle</a>
        </nav>
        <div className="landing-nav__actions">
          {authenticated ? (
            <Link className="landing-nav__account" to="/dashboard">
              {email || 'Ouvrir le workspace'} <ArrowUpRight size={15} aria-hidden="true" />
            </Link>
          ) : (
            <>
              <Link className="landing-nav__login" to="/login">Se connecter</Link>
              <Link className="landing-button landing-button--small" to="/register">Créer un espace</Link>
            </>
          )}
        </div>
      </header>

      <main>
        <section className="landing-hero" aria-labelledby="landing-title">
          <div className="landing-hero__copy">
            <p className="landing-eyebrow"><span className="landing-eyebrow__line" aria-hidden="true" />Extraction produit, sous contrôle</p>
            <h1 id="landing-title">Le produit reste vrai. <em>Le détourage devient vérifiable.</em></h1>
            <p className="landing-hero__lede">Importez une photo, laissez le runtime isoler le produit, puis inspectez le masque et le PNG transparent avant de continuer.</p>
            <div className="landing-hero__actions">
              <Link className="landing-button" to={authenticated ? '/new' : '/register'}>
                {authenticated ? 'Ouvrir l’extraction' : 'Commencer une extraction'} <ArrowUpRight size={18} aria-hidden="true" />
              </Link>
              <a className="landing-text-link" href="#method">Voir la méthode <ArrowUpRight size={16} aria-hidden="true" /></a>
            </div>
            <p className="landing-hero__note"><LockKey size={16} aria-hidden="true" />Source privée, masque réel, aucune génération de décor.</p>
          </div>
          <div className="landing-hero__visual" aria-label="Aperçu du workflow d’extraction">
            <div className="landing-visual__topline"><span>Workflow actif</span><span className="landing-live"><span aria-hidden="true" />Extraction-only</span></div>
            <ExtractionPreview />
          </div>
        </section>

        <section className="landing-proof" id="proof" aria-labelledby="proof-title">
          <div className="landing-section-heading">
            <p className="landing-eyebrow">Ce qui ne bouge pas</p>
            <h2 id="proof-title">Une extraction utile laisse des preuves.</h2>
            <p>Chaque résultat reste lié à la source, au masque calculé et au détourage réellement stocké.</p>
          </div>
          <div className="landing-proof__grid">
            <article><LockKey size={23} aria-hidden="true" /><h3>Source préservée</h3><p>L’image originale est conservée et utilisée comme référence de provenance.</p><span className="landing-check"><Check size={15} weight="bold" aria-hidden="true" />Pixels inchangés</span></article>
            <article><Scissors size={23} aria-hidden="true" /><h3>Masque lisible</h3><p>Le masque binaire et sa révision sont visibles avant validation.</p><span className="landing-check"><Check size={15} weight="bold" aria-hidden="true" />Correction possible</span></article>
            <article><ShieldCheck size={23} aria-hidden="true" /><h3>Cutout réel</h3><p>Le PNG transparent vient du serveur et n’est jamais une image de fixture.</p><span className="landing-check"><Check size={15} weight="bold" aria-hidden="true" />État honnête</span></article>
          </div>
        </section>

        <section className="landing-method" id="method" aria-labelledby="method-title">
          <div className="landing-method__rail"><span>Le flux de travail</span><span>Source vers évidence</span></div>
          <div className="landing-method__content">
            <h2 id="method-title">Un parcours court, vérifiable et réversible.</h2>
            <ol>
              <li><span>Source</span><p>Importez la photo réelle et donnez-lui un nom de travail.</p></li>
              <li><span>Extraction</span><p>Le runtime détecte le produit et produit un masque binaire.</p></li>
              <li><span>Vérification</span><p>Inspectez le masque et le détourage, corrigez si nécessaire, puis arrêtez-vous.</p></li>
            </ol>
            <Link className="landing-text-link" to={authenticated ? '/new' : '/login'}>Entrer dans l’extraction <ArrowUpRight size={16} aria-hidden="true" /></Link>
          </div>
        </section>
      </main>

      <footer className="landing-footer"><Link to="/" className="landing-brand"><BrandMark /><span>Cosmetique AI</span></Link><p>Studio privé d’extraction produit.</p><div><Link to="/login">Connexion</Link><Link to="/register">Créer un espace</Link></div></footer>
    </div>
  )
}

export default Landing
