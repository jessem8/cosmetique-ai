import { Check, Cursor, Eye, EyeSlash, Hand, Plus, Scissors, ShieldCheck, Warning, X } from '@phosphor-icons/react'
import { STUDIO_COPY, lockStatusLabel } from './copy.js'
import { shortHash } from './normalizers.js'

const percent = (value) => `${Math.round(Number(value || 0) * 100)}%`

function LockCanvas({ sourceUrl, maskUrl, lock, tool, maskVisible, onCanvasClick, onCandidate }) {
  const box = lock.bbox || { x: 0.28, y: 0.18, width: 0.44, height: 0.64 }
  return (
    <div className="atelier-lock__canvas-wrap">
      <div className="atelier-lock__canvas" data-tool={tool} onClick={onCanvasClick} role="img" aria-label="Zone de correction du Product Lock">
        {sourceUrl ? <img src={sourceUrl} className="atelier-lock__image" alt="Source du produit à verrouiller" /> : <div className="atelier-lock__missing">Source indisponible</div>}
        {maskVisible && maskUrl && <img src={maskUrl} className="atelier-lock__mask" alt="Masque du produit" />}
        <span className="atelier-lock__box" style={{ left: `${box.x * 100}%`, top: `${box.y * 100}%`, width: `${box.width * 100}%`, height: `${box.height * 100}%` }}>
          <span>Produit protégé</span>
        </span>
        {lock.points?.map((point) => <span key={point.id} className={`atelier-lock__point ${point.kind === 'negative' ? 'is-negative' : ''}`} style={{ left: `${point.x * 100}%`, top: `${point.y * 100}%` }}>{point.kind === 'negative' ? '−' : '+'}</span>)}
        <small className="atelier-lock__canvas-note">{tool === 'positive' ? 'Cliquez pour garder' : tool === 'negative' ? 'Cliquez pour retirer' : 'Vérifiez les contours'}</small>
      </div>
      <div className="atelier-lock__toolbar" role="toolbar" aria-label="Outils de correction">
        <button type="button" className={tool === 'select' ? 'is-active' : ''} onClick={() => onCandidate('select')}><Cursor size={17} aria-hidden="true" />{STUDIO_COPY.lock.select}</button>
        <button type="button" className={tool === 'positive' ? 'is-active' : ''} onClick={() => onCandidate('positive')}><Plus size={17} aria-hidden="true" />{STUDIO_COPY.lock.positive}</button>
        <button type="button" className={tool === 'negative' ? 'is-active' : ''} onClick={() => onCandidate('negative')}><X size={17} aria-hidden="true" />{STUDIO_COPY.lock.negative}</button>
        <button type="button" className={tool === 'pan' ? 'is-active' : ''} onClick={() => onCandidate('pan')}><Hand size={17} aria-hidden="true" />{STUDIO_COPY.lock.pan}</button>
      </div>
    </div>
  )
}

function Evidence({ lock, source }) {
  const provenance = lock.modelProvenance || {}
  return (
    <div className="atelier-lock__evidence">
      <h3>{STUDIO_COPY.lock.evidence}</h3>
      <dl>
        <div><dt>{STUDIO_COPY.lock.source}</dt><dd>{shortHash(source.sourceSha256)}</dd></div>
        <div><dt>{STUDIO_COPY.lock.maskArtifact}</dt><dd>{lock.maskUrl ? 'Disponible' : STUDIO_COPY.lock.noArtifact}</dd></div>
        <div><dt>{STUDIO_COPY.lock.cutout}</dt><dd>{lock.cutoutUrl ? 'Disponible' : STUDIO_COPY.lock.noArtifact}</dd></div>
        <div><dt>{STUDIO_COPY.lock.models}</dt><dd>{[provenance.detector_model || provenance.detector, provenance.segmenter_model || provenance.segmenter].filter(Boolean).join(', ') || 'En attente du serveur'}</dd></div>
      </dl>
    </div>
  )
}

export default function LockPanel({ source, lock, sourceUrl, maskUrl, tool, maskVisible, busy, onTool, onCanvasClick, onSave, onValidate, onReject, onReason, onToggleMask, onCandidate }) {
  const status = lockStatusLabel(lock.status)
  return (
    <section className="atelier-stage" aria-labelledby="lock-title">
      <header className="atelier-stage__header">
        <div>
          <p className="atelier-kicker">Product Lock</p>
          <h2 id="lock-title">{STUDIO_COPY.lock.title}</h2>
          <p>{STUDIO_COPY.lock.description}</p>
        </div>
        <span className={`atelier-state ${lock.status === 'validated' ? 'is-good' : lock.status === 'rejected' ? 'is-error' : 'is-warn'}`}>{status}</span>
      </header>
      <div className="atelier-lock__layout">
        <div className="atelier-lock__workspace">
          <LockCanvas sourceUrl={sourceUrl} maskUrl={maskUrl} lock={lock} tool={tool} maskVisible={maskVisible} onCanvasClick={onCanvasClick} onCandidate={onTool} />
          <p className="atelier-note"><ShieldCheck size={18} aria-hidden="true" />{STUDIO_COPY.lock.reviewNote}</p>
        </div>
        <aside className="atelier-lock__rail" aria-label="Informations du Product Lock">
          <div className="atelier-lock__metrics">
            <div><span>{STUDIO_COPY.lock.confidence}</span><strong>{lock.confidence === null ? 'En attente' : percent(lock.confidence)}</strong></div>
            <div><span>{STUDIO_COPY.lock.revision}</span><strong>R{lock.revision || 0}</strong></div>
          </div>
          <div className="atelier-lock__candidates">
            <h3>{STUDIO_COPY.lock.candidate}</h3>
            {lock.candidates?.length ? lock.candidates.map((candidate, index) => <button type="button" key={candidate.id || index} className={candidate.id === lock.candidateId ? 'is-selected' : ''} onClick={() => onCandidate(candidate)}><span>{candidate.label || `Candidat ${index + 1}`}</span><small>{percent(candidate.confidence ?? candidate.score)}</small></button>) : <p>{STUDIO_COPY.lock.noCandidates}</p>}
          </div>
          <Evidence lock={lock} source={source} />
          <button type="button" className="atelier-button atelier-button--quiet atelier-button--full" onClick={onToggleMask}>{maskVisible ? <EyeSlash size={17} aria-hidden="true" /> : <Eye size={17} aria-hidden="true" />}{STUDIO_COPY.lock.mask}</button>
          <div className="atelier-actions atelier-actions--stack">
            <button type="button" className="atelier-button atelier-button--quiet" onClick={onSave} disabled={busy}><Scissors size={17} aria-hidden="true" />{busy ? STUDIO_COPY.lock.saving : STUDIO_COPY.lock.save}</button>
            <button type="button" className="atelier-button atelier-button--primary" onClick={onValidate} disabled={busy || !lock.maskUrl || !lock.cutoutUrl}><Check size={17} weight="bold" aria-hidden="true" />{STUDIO_COPY.lock.validate}</button>
          </div>
          <label className="atelier-field atelier-field--review">
            <span>{STUDIO_COPY.lock.correctionReason}</span>
            <textarea value={lock.abstentionReason || ''} onChange={(event) => onReason(event.target.value)} placeholder={STUDIO_COPY.lock.correctionPlaceholder} rows="3" />
          </label>
          <button type="button" className="atelier-button atelier-button--text atelier-button--full" onClick={onReject}><Warning size={17} aria-hidden="true" />{STUDIO_COPY.lock.reject}</button>
        </aside>
      </div>
    </section>
  )
}
