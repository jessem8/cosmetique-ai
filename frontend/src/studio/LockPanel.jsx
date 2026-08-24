import { useRef } from 'react'
import { Check, Eye, EyeSlash, PaintBrush, Scissors, ShieldCheck } from '@phosphor-icons/react'
import { STUDIO_COPY, lockStatusLabel } from './copy.js'
import { shortHash } from './normalizers.js'

const percent = (value) => `${Math.round(Number(value || 0) * 100)}%`

function LockCanvas({ sourceUrl, maskUrl, lock, maskVisible, busy, onBrushPoint }) {
  const painting = useRef(false)
  const lastPoint = useRef(null)
  const correctionEnabled = Boolean(lock.id && maskUrl && !busy)

  const emitPoint = (event) => {
    if (!correctionEnabled) return
    const rect = event.currentTarget.getBoundingClientRect()
    if (!rect.width || !rect.height) return
    const point = {
      x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
    }
    const previous = lastPoint.current
    if (previous && Math.hypot(point.x - previous.x, point.y - previous.y) < 0.012) return
    lastPoint.current = point
    onBrushPoint(point)
  }

  return (
    <div className="atelier-lock__canvas-wrap">
      <div
        className="atelier-lock__canvas"
        data-brush-enabled={correctionEnabled}
        onPointerDown={(event) => {
          if (!correctionEnabled) return
          painting.current = true
          lastPoint.current = null
          event.currentTarget.setPointerCapture(event.pointerId)
          emitPoint(event)
        }}
        onPointerMove={(event) => {
          if (painting.current) emitPoint(event)
        }}
        onPointerUp={(event) => {
          painting.current = false
          lastPoint.current = null
          if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId)
          }
        }}
        onPointerCancel={() => {
          painting.current = false
          lastPoint.current = null
        }}
        role="img"
        aria-label={correctionEnabled ? 'Passez le pinceau sur la zone à retirer' : 'Aperçu du Product Lock'}
      >
        {sourceUrl ? <img src={sourceUrl} className="atelier-lock__image" alt="Source du produit à verrouiller" draggable="false" /> : <div className="atelier-lock__missing">Source indisponible</div>}
        {maskVisible && maskUrl && <img src={maskUrl} className="atelier-lock__mask" alt="Masque réel du produit" draggable="false" />}
        {lock.bbox && (
          <span className="atelier-lock__box" style={{ left: `${lock.bbox.x * 100}%`, top: `${lock.bbox.y * 100}%`, width: `${lock.bbox.width * 100}%`, height: `${lock.bbox.height * 100}%` }}>
            <span>Produit détecté</span>
          </span>
        )}
        {lock.points?.map((point) => <span key={point.id} className="atelier-lock__point is-negative" style={{ left: `${point.x * 100}%`, top: `${point.y * 100}%` }} />)}
        <small className="atelier-lock__canvas-note">{correctionEnabled ? 'Passez le pinceau uniquement sur le défaut à retirer' : 'Extraction automatique terminée'}</small>
      </div>
      {correctionEnabled && (
        <div className="atelier-lock__brush-note" role="note">
          <PaintBrush size={18} aria-hidden="true" />
          <span>Un seul outil : peignez la zone qui contient le défaut, puis recalculez l’extraction.</span>
        </div>
      )}
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
        <div><dt>{STUDIO_COPY.lock.models}</dt><dd>{[provenance.detector_model || provenance.detector, provenance.segmenter_model || provenance.segmenter].filter(Boolean).join(', ') || 'Non communiqué'}</dd></div>
      </dl>
    </div>
  )
}

export default function LockPanel({ source, lock, sourceUrl, maskUrl, cutoutUrl, maskVisible, busy, onBrushPoint, onSave, onValidate, onToggleMask }) {
  const status = lockStatusLabel(lock.status)
  return (
    <section className="atelier-stage" aria-labelledby="lock-title">
      <header className="atelier-stage__header">
        <div>
          <p className="atelier-kicker">Extraction</p>
          <h2 id="lock-title">Vérifiez le produit extrait</h2>
          <p>Vérifiez le masque réel et le détourage transparent produits par le runtime GPU. Rien d’autre ne sera lancé depuis cet écran.</p>
        </div>
        <span className={`atelier-state ${lock.status === 'validated' ? 'is-good' : 'is-warn'}`}>{status}</span>
      </header>
      <div className="atelier-lock__layout">
        <div className="atelier-lock__workspace">
          <LockCanvas sourceUrl={sourceUrl} maskUrl={maskUrl} lock={lock} maskVisible={maskVisible} busy={busy} onBrushPoint={onBrushPoint} />
          {cutoutUrl && (
            <figure className="atelier-lock__cutout">
              <div><img src={cutoutUrl} alt="Produit extrait sur fond transparent" /></div>
              <figcaption>Produit extrait proprement</figcaption>
            </figure>
          )}
          <p className="atelier-note"><ShieldCheck size={18} aria-hidden="true" />Le masque et le détourage affichés proviennent du serveur. Aucun aperçu provisoire n’est utilisé.</p>
        </div>
        <aside className="atelier-lock__rail" aria-label="Informations du Product Lock">
          <div className="atelier-lock__metrics">
            <div><span>{STUDIO_COPY.lock.confidence}</span><strong>{lock.confidence === null ? 'Non communiquée' : percent(lock.confidence)}</strong></div>
            <div><span>{STUDIO_COPY.lock.revision}</span><strong>R{lock.revision}</strong></div>
          </div>
          <Evidence lock={lock} source={source} />
          <button type="button" className="atelier-button atelier-button--quiet atelier-button--full" onClick={onToggleMask}>{maskVisible ? <EyeSlash size={17} aria-hidden="true" /> : <Eye size={17} aria-hidden="true" />}{maskVisible ? 'Masquer le masque' : 'Afficher le masque'}</button>
          <div className="atelier-actions atelier-actions--stack">
            {lock.points?.length > 0 && <button type="button" className="atelier-button atelier-button--quiet" onClick={onSave} disabled={busy}><Scissors size={17} aria-hidden="true" />{busy ? 'Recalcul en cours…' : 'Recalculer l’extraction'}</button>}
            <button type="button" className="atelier-button atelier-button--primary" onClick={onValidate} disabled={busy || !lock.maskUrl || !lock.cutoutUrl}><Check size={17} weight="bold" aria-hidden="true" />{lock.status === 'validated' ? 'Extraction validée' : 'Valider l’extraction'}</button>
          </div>
        </aside>
      </div>
    </section>
  )
}
