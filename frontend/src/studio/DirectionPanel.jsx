import { ArrowRight, ShieldCheck } from '@phosphor-icons/react'
import { STUDIO_COPY } from './copy.js'

export default function DirectionPanel({ direction, lockReady, onChange, onContinue }) {
  const prompt = direction.prompt || ''
  const promptReady = prompt.trim().length >= 12
  return (
    <section className="atelier-stage" aria-labelledby="direction-title">
      <header className="atelier-stage__header">
        <div>
          <p className="atelier-kicker">Direction</p>
          <h2 id="direction-title">{STUDIO_COPY.direction.title}</h2>
          <p>{STUDIO_COPY.direction.description}</p>
        </div>
        <span className={`atelier-state ${lockReady ? 'is-good' : 'is-warn'}`}>{lockReady ? 'Product Lock validé' : 'Product Lock requis'}</span>
      </header>
      <div className="atelier-direction__layout">
        <div className="atelier-direction__main">
          <div className="atelier-direction__copy" aria-live="polite">
            <h3>Votre prompt de décor</h3>
            <p>Cette direction est obligatoire. Le serveur ne peut pas inventer de scène à votre place.</p>
          </div>
          <label className="atelier-field">
            <span>{STUDIO_COPY.direction.prompt}</span>
            <textarea value={prompt} onChange={(event) => onChange({ mode: 'custom', prompt: event.target.value })} placeholder={STUDIO_COPY.direction.promptPlaceholder} rows="8" aria-describedby="direction-guardrail direction-prompt-help" required minLength="12" />
            <small id="direction-prompt-help">{prompt.length}/12 caractères minimum. Décrivez la lumière, les matières et l’espace de votre scène.</small>
          </label>
          <p id="direction-guardrail" className="atelier-note"><ShieldCheck size={18} aria-hidden="true" />{STUDIO_COPY.direction.guardrailText}</p>
          <div className="atelier-actions atelier-actions--direction">
            <button type="button" className="atelier-button atelier-button--primary" onClick={onContinue} disabled={!lockReady || !promptReady}>{STUDIO_COPY.generation.review}<ArrowRight size={18} aria-hidden="true" /></button>
          </div>
        </div>
        <aside className="atelier-direction__rail" aria-label="Paramètres de la scène">
          <label className="atelier-field"><span>{STUDIO_COPY.direction.audience}</span><input value={direction.audience} onChange={(event) => onChange({ audience: event.target.value })} placeholder={STUDIO_COPY.direction.audiencePlaceholder} /></label>
          <label className="atelier-field"><span>{STUDIO_COPY.direction.placement}</span><select value={direction.placement} onChange={(event) => onChange({ placement: event.target.value })}>{STUDIO_COPY.placements.map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
          <label className="atelier-field"><span>{STUDIO_COPY.direction.seed}</span><input type="number" value={direction.seed} onChange={(event) => onChange({ seed: Number(event.target.value) || 0 })} /></label>
          <div className="atelier-direction__guardrail"><span>{STUDIO_COPY.direction.guardrail}</span><strong>Produit intact</strong><p>Le rendu local remplace uniquement le décor, puis remet le détourage validé en place.</p></div>
        </aside>
      </div>
    </section>
  )
}
