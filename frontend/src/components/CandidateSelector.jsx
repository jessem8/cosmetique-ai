import { useId, useRef, useState } from 'react'

function CandidateSelector({
  imageUrl,
  candidates = [],
  onConfirm,
  isSubmitting = false,
}) {
  const labelId = useId()
  const [selectedId, setSelectedId] = useState(null)
  const candidateRefs = useRef([])
  const selected = candidates.find((candidate) => candidate.id === selectedId)

  const confirm = () => {
    if (!selected) return
    onConfirm({
      type: 'box',
      x: selected.x,
      y: selected.y,
      width: selected.width,
      height: selected.height,
    })
  }

  const handleCandidateKeyDown = (event, index) => {
    const directions = {
      ArrowRight: 1,
      ArrowDown: 1,
      ArrowLeft: -1,
      ArrowUp: -1,
    }
    let nextIndex = index

    if (event.key in directions) {
      nextIndex =
        (index + directions[event.key] + candidates.length) % candidates.length
    } else if (event.key === 'Home') {
      nextIndex = 0
    } else if (event.key === 'End') {
      nextIndex = candidates.length - 1
    } else {
      return
    }

    event.preventDefault()
    setSelectedId(candidates[nextIndex].id)
    candidateRefs.current[nextIndex]?.focus()
  }

  return (
    <section className="candidate-selector" aria-labelledby={labelId}>
      <div className="candidate-selector__copy">
        <h2 id={labelId}>Quel produit doit être utilisé ?</h2>
        <p>
          Plusieurs produits ont été détectés. Sélectionnez le bon cadre pour
          lancer une nouvelle campagne complète.
        </p>
      </div>

      <div
        className="candidate-canvas"
        role="radiogroup"
        aria-label="Produits détectés"
      >
        <img src={imageUrl} alt="Photo originale avec les produits détectés" />
        {candidates.map((candidate, index) => (
          <button
            key={candidate.id}
            ref={(element) => {
              candidateRefs.current[index] = element
            }}
            type="button"
            role="radio"
            aria-checked={selectedId === candidate.id}
            aria-label={`Produit possible ${index + 1}`}
            tabIndex={
              selectedId ? (selectedId === candidate.id ? 0 : -1) : index === 0 ? 0 : -1
            }
            className="candidate-box"
            data-selected={selectedId === candidate.id}
            style={{
              '--candidate-x': `${candidate.x * 100}%`,
              '--candidate-y': `${candidate.y * 100}%`,
              '--candidate-width': `${candidate.width * 100}%`,
              '--candidate-height': `${candidate.height * 100}%`,
            }}
            onClick={() => setSelectedId(candidate.id)}
            onKeyDown={(event) => handleCandidateKeyDown(event, index)}
          >
            <span>{index + 1}</span>
          </button>
        ))}
      </div>

      <ol className="candidate-list" aria-label="Liste des produits détectés">
        {candidates.map((candidate, index) => (
          <li key={candidate.id}>
            <button
              type="button"
              className="candidate-list__option"
              data-selected={selectedId === candidate.id}
              onClick={() => setSelectedId(candidate.id)}
            >
              <span>Produit {index + 1}</span>
              <span>Cadre détecté</span>
            </button>
          </li>
        ))}
      </ol>

      <button
        type="button"
        className="button button--primary"
        disabled={!selected || isSubmitting}
        onClick={confirm}
      >
        {isSubmitting ? 'Lancement en cours' : 'Utiliser ce produit'}
      </button>
    </section>
  )
}

export default CandidateSelector
