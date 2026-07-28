const PIPELINE_STEPS = [
  { id: 'detourage', label: 'Détourage du produit...', icon: '✂️' },
  { id: 'decor', label: 'Génération du décor IA...', icon: '🎨' },
  { id: 'composition', label: "Composition de l'affiche...", icon: '🖼️' },
  { id: 'texte', label: 'Rédaction du texte marketing...', icon: '✍️' },
  { id: 'export', label: 'Export des formats...', icon: '📐' },
  { id: 'upload', label: 'Upload des assets...', icon: '☁️' },
]

function StepLoader({ currentStep, steps }) {
  const stepList = steps || PIPELINE_STEPS

  // Find the index of the current step
  const currentIndex = currentStep
    ? stepList.findIndex((s) => s.id === currentStep || s.label === currentStep)
    : -1

  const progressPercent =
    currentIndex >= 0
      ? Math.round(((currentIndex) / (stepList.length - 1)) * 100)
      : 0

  return (
    <div className="glass-card step-loader">
      {/* Progress bar */}
      <div className="step-loader-progress">
        <div
          className="step-loader-progress-fill"
          style={{ width: `${progressPercent}%` }}
        />
      </div>

      {/* Steps list */}
      <div className="step-loader-list">
        {stepList.map((step, index) => {
          let state = 'upcoming'
          if (index < currentIndex) state = 'done'
          else if (index === currentIndex) state = 'current'

          return (
            <div
              key={step.id || index}
              className={`step-loader-item ${state}`}
            >
              {/* Icon */}
              <div className={`step-loader-icon ${state}-icon`}>
                {state === 'done' ? '✓' : state === 'current' ? '⟳' : '·'}
              </div>

              {/* Step label */}
              <span className="step-loader-text">{step.label}</span>

              {/* Duration hint for completed */}
              {state === 'done' && (
                <span
                  style={{
                    marginLeft: 'auto',
                    fontSize: '0.72rem',
                    color: 'var(--success)',
                    fontWeight: 600,
                  }}
                >
                  ✓
                </span>
              )}

              {/* "En cours" pill for current */}
              {state === 'current' && (
                <span
                  style={{
                    marginLeft: 'auto',
                    fontSize: '0.7rem',
                    color: 'var(--gold)',
                    background: 'rgba(212, 160, 85, 0.12)',
                    padding: '2px 8px',
                    borderRadius: '999px',
                    fontWeight: 600,
                    whiteSpace: 'nowrap',
                  }}
                >
                  En cours…
                </span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export { PIPELINE_STEPS }
export default StepLoader
