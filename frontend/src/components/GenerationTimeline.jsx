const STAGES = [
  ['analysis', 'Analyse du produit'],
  ['extraction', 'Extraction du produit'],
  ['art_direction', 'Direction artistique'],
  ['background', 'Création du décor'],
  ['composition', 'Composition des formats'],
  ['copy', 'Rédaction vérifiée'],
  ['export', 'Export des visuels'],
  ['packaging', 'Préparation du dossier'],
]

function GenerationTimeline({
  status,
  stage,
  completedStages = [],
}) {
  const completed = new Set(completedStages)

  return (
    <ol className="generation-timeline" aria-label="Étapes de génération">
      {STAGES.map(([key, label]) => {
        const state = completed.has(key)
          ? 'complete'
          : status === 'processing' && stage === key
            ? 'current'
            : 'upcoming'

        return (
          <li key={key} className="generation-timeline__item" data-state={state}>
            <span
              className="generation-timeline__marker"
              aria-hidden="true"
            />
            <span
              className="generation-timeline__label"
              data-state={state}
              aria-current={state === 'current' ? 'step' : undefined}
            >
              {label}
            </span>
          </li>
        )
      })}
    </ol>
  )
}

export { STAGES }
export default GenerationTimeline
