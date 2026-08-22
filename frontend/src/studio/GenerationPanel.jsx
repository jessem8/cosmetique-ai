import { ArrowLeft, ArrowRight, CircleNotch, Sparkle, Stop } from '@phosphor-icons/react'
import { STUDIO_COPY, generationStatusLabel } from './copy.js'

export default function GenerationPanel({ direction, generation, provider, engine, busy, onBack, onGenerate, onCancel, onChange }) {
  const status = generationStatusLabel(generation.status)
  const providerAvailable = Boolean(provider)
  const engineReady = engine.status === 'ready'
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
            <div><span>{STUDIO_COPY.generation.engine}</span><strong>{engineReady ? 'Colab GPU confirmé' : 'Colab non confirmé'}</strong></div>
            <span className={`atelier-state ${engineReady ? 'is-good' : 'is-warn'}`}>{engineReady ? 'Prêt' : 'En attente'}</span>
          </div>
          <div className="atelier-generation__brief">
            <span>Direction {direction.mode === 'automatic' ? 'automatique' : 'personnelle'}</span>
            <p>{direction.prompt}</p>
          </div>
          {generation.error && <div className="atelier-inline-error" role="alert">{generation.error.message || 'La génération n’a pas pu aboutir.'}</div>}
          <div className="atelier-actions atelier-actions--generation">
            <button type="button" className="atelier-button atelier-button--text" onClick={onBack}><ArrowLeft size={18} aria-hidden="true" />{STUDIO_COPY.generation.review}</button>
            {['queued', 'preparing', 'waiting_provider', 'rendering', 'qa'].includes(generation.status) ? <button type="button" className="atelier-button atelier-button--quiet" onClick={onCancel}><Stop size={18} aria-hidden="true" />{STUDIO_COPY.generation.cancel}</button> : <button type="button" className="atelier-button atelier-button--primary" onClick={onGenerate} disabled={busy || !engineReady || !providerAvailable}>{busy ? <CircleNotch className="atelier-spin" size={18} aria-hidden="true" /> : <Sparkle size={18} aria-hidden="true" />}{busy ? STUDIO_COPY.generation.generating : STUDIO_COPY.generation.generate}<ArrowRight size={18} aria-hidden="true" /></button>}
          </div>
        </div>
        <aside className="atelier-generation__rail" aria-label="Paramètres de génération">
          <div className="atelier-generation__provider"><span>{STUDIO_COPY.generation.provider}</span><strong>{provider?.name || STUDIO_COPY.generation.noProvider}</strong>{provider && <small>{provider.model}</small>}</div>
          <label className="atelier-field"><span>{STUDIO_COPY.generation.variants}</span><input type="number" min="1" max={provider?.maxVariants || 1} value={direction.variantCount} onChange={(event) => onChange({ variantCount: Math.max(1, Number(event.target.value) || 1) })} /></label>
          <label className="atelier-field"><span>{STUDIO_COPY.generation.budget}</span><input type="number" min="0" step="0.25" value={direction.costCeiling} onChange={(event) => onChange({ costCeiling: Math.max(0, Number(event.target.value) || 0) })} /></label>
          <p className="atelier-helper">Colab reste le moteur principal. Le profil affiché vient uniquement des capacités renvoyées par le serveur.</p>
        </aside>
      </div>
    </section>
  )
}
