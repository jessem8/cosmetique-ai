import { ArrowClockwise, FolderOpen, Play } from '@phosphor-icons/react'
import { STUDIO_COPY } from './copy.js'

export default function BatchPanel({ batches, loading, engineReady, onCreate, onRefresh }) {
  const latest = batches[0]
  return (
    <section className="atelier-batch" aria-labelledby="batch-title">
      <div className="atelier-batch__copy"><p className="atelier-kicker">Dossier</p><h2 id="batch-title">{STUDIO_COPY.batch.title}</h2><p>{STUDIO_COPY.batch.description}</p></div>
      <dl className="atelier-batch__counts"><div><dt>{STUDIO_COPY.batch.canonical}</dt><dd>213</dd></div><div><dt>{STUDIO_COPY.batch.regression}</dt><dd>600</dd></div><div><dt>{STUDIO_COPY.batch.acceptance}</dt><dd>57</dd></div></dl>
      {latest ? <div className="atelier-batch__latest"><FolderOpen size={20} aria-hidden="true" /><div><strong>{latest.name || latest.id || 'Traitement récent'}</strong><span>{latest.status || 'En préparation'}</span></div><button type="button" className="atelier-button atelier-button--text" onClick={onRefresh}><ArrowClockwise size={17} aria-hidden="true" />Actualiser</button></div> : <p className="atelier-batch__empty">{loading ? STUDIO_COPY.batch.loading : STUDIO_COPY.batch.empty}</p>}
      <button type="button" className="atelier-button atelier-button--quiet" disabled={!engineReady || loading} onClick={onCreate}><Play size={17} aria-hidden="true" />{loading ? STUDIO_COPY.batch.loading : STUDIO_COPY.batch.create}</button>
      {!engineReady && <p className="atelier-helper">{STUDIO_COPY.batch.unavailable}</p>}
    </section>
  )
}
