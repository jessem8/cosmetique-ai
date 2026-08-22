import { ArrowLeft, CheckCircle, DownloadSimple, Eye, ShieldCheck, Warning } from '@phosphor-icons/react'
import { STUDIO_COPY } from './copy.js'
import { formatCost, shortHash } from './normalizers.js'

function QualitySummary({ variant }) {
  const passed = variant?.qa?.status === 'pass' || variant?.qa?.passed === true
  return <div className={`atelier-quality ${passed ? 'is-good' : 'is-warn'}`} role="status"><span>{passed ? <CheckCircle size={18} weight="fill" aria-hidden="true" /> : <Warning size={18} aria-hidden="true" />}</span><div><strong>{passed ? STUDIO_COPY.review.qaPassed : STUDIO_COPY.review.qaReview}</strong>{variant?.qa?.failures?.length > 0 && <ul>{variant.qa.failures.map((failure, index) => <li key={failure.code || index}>{failure.message || failure.code || String(failure)}</li>)}</ul>}</div></div>
}

export default function ReviewPanel({ sourceUrl, variantUrl, variant, variants, selectedId, onSelect, onBack, onExport }) {
  const passed = variant?.qa?.status === 'pass' || variant?.qa?.passed === true
  return (
    <section className="atelier-stage" aria-labelledby="review-title">
      <header className="atelier-stage__header">
        <div><p className="atelier-kicker">Vérification</p><h2 id="review-title">{STUDIO_COPY.review.title}</h2><p>{STUDIO_COPY.review.description}</p></div>
        <span className={`atelier-state ${passed ? 'is-good' : 'is-warn'}`}>{passed ? STUDIO_COPY.review.qaPassed : STUDIO_COPY.review.qaReview}</span>
      </header>
      <div className="atelier-review__layout">
        <div className="atelier-review__viewer">
          {variantUrl ? <img src={variantUrl} alt="Rendu final du produit" /> : <div className="atelier-review__empty"><Eye size={30} aria-hidden="true" /><strong>{STUDIO_COPY.review.empty}</strong></div>}
          {sourceUrl && <span className="atelier-review__source"><ShieldCheck size={15} aria-hidden="true" />Produit original restauré</span>}
        </div>
        <aside className="atelier-review__rail" aria-label="Vérification et export">
          {variants.length > 1 && <div className="atelier-variants" role="listbox" aria-label="Variantes disponibles">{variants.map((item) => <button type="button" role="option" aria-selected={item.id === selectedId} key={item.id} onClick={() => onSelect(item.id)}>{item.label}</button>)}</div>}
          <QualitySummary variant={variant} />
          <dl className="atelier-provenance">
            <div><dt>{STUDIO_COPY.review.model}</dt><dd>{variant?.provenance?.model || 'Non communiqué'}</dd></div>
            <div><dt>{STUDIO_COPY.review.sourceHash}</dt><dd>{shortHash(variant?.provenance?.sourceSha256)}</dd></div>
            <div><dt>{STUDIO_COPY.review.outputHash}</dt><dd>{shortHash(variant?.provenance?.outputSha256)}</dd></div>
            <div><dt>{STUDIO_COPY.review.cost}</dt><dd>{formatCost(variant?.provenance?.cost)}</dd></div>
          </dl>
          <div className="atelier-actions atelier-actions--stack"><button type="button" className="atelier-button atelier-button--text" onClick={onBack}><ArrowLeft size={18} aria-hidden="true" />Retour à la direction</button><button type="button" className="atelier-button atelier-button--primary" onClick={onExport} disabled={!passed}><DownloadSimple size={18} aria-hidden="true" />{passed ? STUDIO_COPY.review.export : STUDIO_COPY.review.exportBlocked}</button></div>
        </aside>
      </div>
    </section>
  )
}
