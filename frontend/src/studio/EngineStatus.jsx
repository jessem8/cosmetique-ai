import { ArrowClockwise, CheckCircle, Cloud, Warning } from '@phosphor-icons/react'
import { STUDIO_COPY } from './copy.js'

export default function EngineStatus({ engine, onRefresh }) {
  const ready = engine.status === 'ready'
  const checking = engine.status === 'checking'
  const models = Array.isArray(engine.models)
    ? engine.models.filter((model) => /grounding|sam|segment|u2net/i.test(String(model)))
    : []
  return (
    <section className="atelier-engine" aria-labelledby="engine-title">
      <div className="atelier-engine__icon" data-ready={ready}>{ready ? <CheckCircle size={22} weight="fill" aria-hidden="true" /> : checking ? <Cloud size={22} aria-hidden="true" /> : <Warning size={22} aria-hidden="true" />}</div>
      <div className="atelier-engine__body"><h2 id="engine-title">{STUDIO_COPY.engine.title}</h2><strong>{checking ? STUDIO_COPY.engine.checking : ready ? STUDIO_COPY.engine.ready : engine.status === 'degraded' ? STUDIO_COPY.engine.degraded : STUDIO_COPY.engine.unavailable}</strong><p>{engine.message || (!ready && STUDIO_COPY.engine.unavailableHelp)}</p>{(engine.gpu || engine.runtime || models.length) && <dl><div><dt>{STUDIO_COPY.engine.runtime}</dt><dd>{engine.runtime || 'Non communiqué'}</dd></div><div><dt>{STUDIO_COPY.engine.gpu}</dt><dd>{engine.gpu || 'Non communiqué'}</dd></div><div><dt>{STUDIO_COPY.engine.models}</dt><dd>{models.length ? models.join(', ') : ready ? 'Confirmés par le runtime' : 'Non communiqués'}</dd></div></dl>}</div>
      <button type="button" className="atelier-button atelier-button--text" onClick={onRefresh} disabled={checking}><ArrowClockwise size={17} aria-hidden="true" />{STUDIO_COPY.engine.refresh}</button>
    </section>
  )
}
