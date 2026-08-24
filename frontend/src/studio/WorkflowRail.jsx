import { Check } from '@phosphor-icons/react'

const STEPS = [
  ['source', 'Photo source'],
  ['lock', 'Produit extrait'],
]

export default function WorkflowRail({ step, lockCreated, lockReady, onStep }) {
  const enabled = {
    source: true,
    lock: lockCreated,
  }
  const currentIndex = STEPS.findIndex(([id]) => id === step)
  return (
    <nav className="atelier-workflow" aria-label="Progression de l’extraction">
      <ol>
        {STEPS.map(([id, label], index) => {
          const complete = index < currentIndex || (id === 'lock' && lockReady)
          const active = id === step
          return <li key={id} data-active={active} data-complete={complete}><button type="button" aria-current={active ? 'step' : undefined} disabled={!enabled[id]} onClick={() => onStep(id)}><span className="atelier-workflow__number">{complete ? <Check size={14} weight="bold" aria-hidden="true" /> : index + 1}</span><span>{label}</span></button></li>
        })}
      </ol>
    </nav>
  )
}
