import { ArrowLeft, ArrowRight, CircleNotch, Sparkle, Stop } from '@phosphor-icons/react'
import { STUDIO_COPY, generationStatusLabel } from './copy.js'

export default function GenerationPanel({ direction, generation, engine, busy, onBack, onGenerate, onCancel }) {
  const status = generationStatusLabel(generation.status)
  const engineReady = engine.status === 'ready'
  const lifecycle = [
    ['baseline', 'Baseline locale', generation.baseline],
    ['final_candidate', 'Candidat final', generation.final_candidate || generation.finalCandidate],
  ].filter(([, , artifact]) => artifact)
  return (
    <section className="atelier-stage" aria-labelledby="generation-title">
      <header className="atelier-stage__header">
        <div>
          <p className="atelier-kicker">Génération</p>
          <h2 id="generation-title">{STUDIO_COPY.generation.title}</h2>
          <p>{STUDIO_COPY.generation.description}</p>
        </div>
        <span className={`atelier-state ${engineReady ? 'is-good' : 'is-warn'}`}>{status}</span>
      </header>
      <div className="atelier-generation__layout">
        <div className="atelier-generation__main">
          <div className="atelier-generation__engine">
            <div><span>{STUDIO_COPY.generation.engine}</span><strong>{engineReady ? 'GPU confirmé' : 'GPU non confirmé'}</strong></div>
            <span className={`atelier-state ${engineReady ? 'is-good' : 'is-warn'}`}>{engineReady ? 'Prêt' : 'En attente'}</span>
          </div>
          <div className="atelier-generation__brief">
            <span>Direction {direction.mode === 'automatic' ? 'automatique' : 'personnelle'}</span>
            <p>{direction.prompt}</p>
          </div>
          {generation.error && <div className="atelier-inline-error" role="alert">{generation.error.message || 'La génération n’a pas pu aboutir.'}</div>}
          {lifecycle.length > 0 && <ol className="atelier-lifecycle" aria-label="Cycle réel du rendu">{lifecycle.map(([key, label, artifact]) => <li key={key} data-status={artifact.status || artifact.lifecycle_status || 'pending'}><span>{label}</span><strong>{artifact.status || artifact.lifecycle_status || 'pending'}</strong>{artifact.error?.message && <small>{artifact.error.message}</small>}</li>)}</ol>}
          <div className="atelier-actions atelier-actions--generation">
            <button type="button" className="atelier-button atelier-button--text" onClick={onBack}><ArrowLeft size={18} aria-hidden="true" />{STUDIO_COPY.generation.review}</button>
            {['queued', 'preparing', 'waiting_provider', 'rendering', 'qa'].includes(generation.status) ? <button type="button" className="atelier-button atelier-button--quiet" onClick={onCancel}><Stop size={18} aria-hidden="true" />{STUDIO_COPY.generation.cancel}</button> : <button type="button" className="atelier-button atelier-button--primary" onClick={onGenerate} disabled={busy || !engineReady}>{busy ? <CircleNotch className="atelier-spin" size={18} aria-hidden="true" /> : <Sparkle size={18} aria-hidden="true" />}{busy ? STUDIO_COPY.generation.generating : STUDIO_COPY.generation.generate}<ArrowRight size={18} aria-hidden="true" /></button>}
          </div>
        </div>
        <aside className="atelier-generation__rail" aria-label="Paramètres de génération">
          <div className="atelier-generation__provider"><span>Rendu</span><strong>SDXL local</strong><small>Produit recomposé à partir du détourage validé</small></div>
          <p className="atelier-helper">Une seule image est rendue puis vérifiée. Aucun service externe ne modifie le produit.</p>
        </aside>
      </div>
    </section>
  )
}
