import { Check } from '@phosphor-icons/react'

const STEPS = [
  ['source', 'Source'],
  ['lock', 'Product Lock'],
  ['direction', 'Direction'],
  ['generation', 'Génération'],
  ['review', 'Vérification'],
]

export default function WorkflowRail({ step, sourceReady, lockReady, directionReady, generationReady, onStep }) {
  const enabled = { source: true, lock: sourceReady, direction: lockReady, generation: directionReady, review: generationReady }
  const currentIndex = STEPS.findIndex(([id]) => id === step)
  return (
    <nav className="atelier-workflow" aria-label="Progression de la campagne">
      <ol>
        {STEPS.map(([id, label], index) => {
          const complete = index < currentIndex || (id === 'review' && generationReady)
          const active = id === step
          return <li key={id} data-active={active} data-complete={complete}><button type="button" aria-current={active ? 'step' : undefined} disabled={!enabled[id]} onClick={() => onStep(id)}><span className="atelier-workflow__number">{complete ? <Check size={14} weight="bold" aria-hidden="true" /> : index + 1}</span><span>{label}</span></button></li>
        })}
      </ol>
    </nav>
  )
}
