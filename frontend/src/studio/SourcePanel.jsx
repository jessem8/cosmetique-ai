import { CheckCircle, ImageSquare, Scissors, UploadSimple, X } from '@phosphor-icons/react'
import { STUDIO_COPY } from './copy.js'

function SourceFigure({ image, stored }) {
  return (
    <figure className="atelier-source__figure" data-empty={!image}>
      {image ? (
        <img src={image} alt="Photo source du produit" />
      ) : (
        <div className="atelier-source__empty">
          <ImageSquare size={34} weight="light" aria-hidden="true" />
          <strong>{STUDIO_COPY.source.emptyTitle}</strong>
          <span>{STUDIO_COPY.source.emptyHelp}</span>
        </div>
      )}
      {stored && (
        <figcaption className="atelier-source__stamp">
          <CheckCircle size={15} weight="fill" aria-hidden="true" />
          {STUDIO_COPY.source.stored}
        </figcaption>
      )}
    </figure>
  )
}

function CategorySelect({ value, onChange }) {
  return (
    <select value={value} onChange={(event) => onChange(event.target.value)}>
      {STUDIO_COPY.categories.map(([key, label]) => <option value={key} key={key}>{label}</option>)}
    </select>
  )
}

export default function SourcePanel({ source, file, preview, busy, onChange, onFile, onCreate, onRemove }) {
  const image = preview || source.imageUrl
  const stored = Boolean(source.productId)
  return (
    <section className="atelier-stage" aria-labelledby="source-title">
      <header className="atelier-stage__header">
        <div>
          <p className="atelier-kicker">Source</p>
          <h2 id="source-title">{STUDIO_COPY.source.title}</h2>
          <p>{STUDIO_COPY.source.description}</p>
        </div>
        <span className={`atelier-state ${stored ? 'is-good' : ''}`}>
          {stored ? STUDIO_COPY.source.stored : image ? STUDIO_COPY.source.ready : STUDIO_COPY.source.waiting}
        </span>
      </header>

      <div className="atelier-source__layout">
        <SourceFigure image={image} stored={stored} />
        <form className="atelier-form" onSubmit={(event) => { event.preventDefault(); onCreate() }}>
          <label className="atelier-field">
            <span>{STUDIO_COPY.source.name}</span>
            <input value={source.name} onChange={(event) => onChange({ name: event.target.value })} required />
          </label>
          <label className="atelier-field">
            <span>{STUDIO_COPY.source.brand} <small>{STUDIO_COPY.source.optional}</small></span>
            <input value={source.brand} onChange={(event) => onChange({ brand: event.target.value })} />
          </label>
          <label className="atelier-field">
            <span>{STUDIO_COPY.source.category}</span>
            <CategorySelect value={source.category} onChange={(category) => onChange({ category })} />
          </label>

          <dl className="atelier-facts">
            <div><dt>{STUDIO_COPY.source.file}</dt><dd title={source.fileName || file?.name}>{source.fileName || file?.name || 'Aucune photo choisie'}</dd></div>
            <div><dt>{STUDIO_COPY.source.storage}</dt><dd>{stored ? STUDIO_COPY.source.privateStorage : STUDIO_COPY.source.notStored}</dd></div>
          </dl>

          <div className="atelier-actions atelier-actions--source">
            <label className="atelier-button atelier-button--quiet" htmlFor="atelier-product-image">
              <UploadSimple size={18} aria-hidden="true" />
              {image ? STUDIO_COPY.source.change : STUDIO_COPY.source.upload}
            </label>
            <input id="atelier-product-image" className="visually-hidden" type="file" accept="image/jpeg,image/png,image/webp" aria-label={STUDIO_COPY.source.upload} onChange={(event) => onFile(event.target.files?.[0])} />
            {image && !stored && <button type="button" className="atelier-button atelier-button--text" onClick={onRemove}><X size={17} aria-hidden="true" />{STUDIO_COPY.source.remove}</button>}
            <button type="submit" className="atelier-button atelier-button--primary" disabled={busy || (!stored && !file)}>
              <Scissors size={18} aria-hidden="true" />
              {busy ? STUDIO_COPY.source.creatingLock : stored ? STUDIO_COPY.source.refreshLock : STUDIO_COPY.source.createLock}
            </button>
          </div>
          <p className="atelier-helper">{STUDIO_COPY.source.helper}</p>
        </form>
      </div>
    </section>
  )
}
