import { ArrowRight, MagicWand, PencilSimple, ShieldCheck } from '@phosphor-icons/react'
import { STUDIO_COPY } from './copy.js'

export default function DirectionPanel({ direction, source, lockReady, busy, onChange, onPreview, onContinue }) {
  const autoPrompt = direction.prompt || `Décor ${source.category || 'beauté'} contemporain, lumière naturelle douce, composition aérée, matières minérales, espace calme autour de ${source.name || 'la référence produit'}.`
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
          <div className="atelier-tabs" role="tablist" aria-label="Mode de direction">
            <button type="button" role="tab" aria-selected={direction.mode === 'automatic'} className={direction.mode === 'automatic' ? 'is-active' : ''} onClick={() => onChange({ mode: 'automatic', prompt: '' })}><MagicWand size={18} aria-hidden="true" />{STUDIO_COPY.direction.automatic}</button>
            <button type="button" role="tab" aria-selected={direction.mode === 'custom'} className={direction.mode === 'custom' ? 'is-active' : ''} onClick={() => onChange({ mode: 'custom' })}><PencilSimple size={18} aria-hidden="true" />{STUDIO_COPY.direction.custom}</button>
          </div>
          <div className="atelier-direction__copy" aria-live="polite">
            <h3>{direction.mode === 'automatic' ? STUDIO_COPY.direction.automatic : STUDIO_COPY.direction.custom}</h3>
            <p>{direction.mode === 'automatic' ? STUDIO_COPY.direction.automaticHelp : STUDIO_COPY.direction.customHelp}</p>
          </div>
          <label className="atelier-field">
            <span>{STUDIO_COPY.direction.prompt}</span>
            <textarea value={direction.mode === 'automatic' ? autoPrompt : direction.prompt} onChange={(event) => onChange({ prompt: event.target.value })} placeholder={STUDIO_COPY.direction.promptPlaceholder} rows="8" aria-describedby="direction-guardrail" />
          </label>
          <p id="direction-guardrail" className="atelier-note"><ShieldCheck size={18} aria-hidden="true" />{STUDIO_COPY.direction.guardrailText}</p>
          <div className="atelier-actions atelier-actions--direction">
            <button type="button" className="atelier-button atelier-button--quiet" onClick={() => onPreview()} disabled={busy || !lockReady}><MagicWand size={18} aria-hidden="true" />{busy ? STUDIO_COPY.direction.previewing : STUDIO_COPY.direction.preview}</button>
            <button type="button" className="atelier-button atelier-button--primary" onClick={onContinue} disabled={!lockReady || !direction.prompt.trim()}>{STUDIO_COPY.generation.review}<ArrowRight size={18} aria-hidden="true" /></button>
          </div>
        </div>
        <aside className="atelier-direction__rail" aria-label="Paramètres de la scène">
          <label className="atelier-field"><span>{STUDIO_COPY.direction.audience}</span><input value={direction.audience} onChange={(event) => onChange({ audience: event.target.value })} placeholder={STUDIO_COPY.direction.audiencePlaceholder} /></label>
          <label className="atelier-field"><span>{STUDIO_COPY.direction.placement}</span><select value={direction.placement} onChange={(event) => onChange({ placement: event.target.value })}>{STUDIO_COPY.placements.map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
          <label className="atelier-field"><span>{STUDIO_COPY.direction.seed}</span><input type="number" value={direction.seed} onChange={(event) => onChange({ seed: Number(event.target.value) || 0 })} /></label>
          <div className="atelier-direction__guardrail"><span>{STUDIO_COPY.direction.guardrail}</span><strong>Produit intact</strong><p>Les pixels verrouillés sont remis en place après la génération du décor.</p></div>
        </aside>
      </div>
    </section>
  )
}
