import { ArrowRight, CheckCircle, ImageSquare, Plus, Scissors, ShieldCheck } from '@phosphor-icons/react'
import { Link } from 'react-router-dom'
import BrandMark from '../components/BrandMark.jsx'

const EXTRACTION_STEPS = [
  {
    icon: ImageSquare,
    title: 'Importer la source',
    body: 'Choisissez une vraie photo produit. La source originale reste privée et immuable.',
  },
  {
    icon: Scissors,
    title: 'Extraire le produit',
    body: 'Le runtime détecte le produit, produit un masque binaire et prépare le détourage transparent.',
  },
  {
    icon: CheckCircle,
    title: 'Vérifier la preuve',
    body: 'Inspectez la source, le masque, le cutout et la provenance. Le flux s’arrête ici.',
  },
]

function Dashboard() {
  return (
    <div className="workspace-page workspace-page--wide">
      <header className="library-heading">
        <div>
          <p className="eyebrow">Workspace privé</p>
          <h1>Extractions</h1>
          <p>Un seul flux actif. Importez un produit et vérifiez son détourage réel.</p>
        </div>
        <Link className="button button--primary" to="/new">
          <Plus size={18} aria-hidden="true" />
          Extraire un produit
        </Link>
      </header>

      <section className="extraction-hub" aria-labelledby="extraction-hub-title">
        <div className="extraction-hub__lead">
          <div>
            <BrandMark compact />
            <p className="eyebrow">Product Lock</p>
            <h2 id="extraction-hub-title">Votre produit, séparé sans l’inventer.</h2>
            <p>Le résultat utile est le masque vérifiable et le PNG transparent. Aucun décor, aucun fournisseur externe, aucune étape cachée.</p>
            <Link className="button button--primary" to="/new">
              Ouvrir l’extraction
              <ArrowRight size={18} aria-hidden="true" />
            </Link>
          </div>
          <p className="extraction-hub__note">
            <ShieldCheck size={17} aria-hidden="true" />
            Les pixels source restent la référence de l’évidence.
          </p>
        </div>

        <div className="extraction-hub__steps" aria-label="Workflow d’extraction">
          {EXTRACTION_STEPS.map(({ icon: Icon, title, body }) => (
            <article className="extraction-hub__step" key={title}>
              <Icon size={26} weight="light" aria-hidden="true" />
              <div>
                <strong>{title}</strong>
                <p>{body}</p>
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  )
}

export default Dashboard
