import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { generations } from '../api/client.js'
import StepLoader, { PIPELINE_STEPS } from '../components/StepLoader.jsx'

const STATUS_STEP_MAP = {
  pending: 0,
  processing: 1,
  done: PIPELINE_STEPS.length - 1,
  error: -1,
}

function Generation() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [genData, setGenData] = useState(null)
  const [error, setError] = useState('')
  const [timeLeft, setTimeLeft] = useState(90)
  const [currentStepIdx, setCurrentStepIdx] = useState(0)
  const intervalRef = useRef(null)
  const timerRef = useRef(null)
  const stepCycleRef = useRef(null)

  // Countdown timer
  useEffect(() => {
    timerRef.current = setInterval(() => {
      setTimeLeft((t) => (t > 0 ? t - 1 : 0))
    }, 1000)
    return () => clearInterval(timerRef.current)
  }, [])

  // Step animation cycle (while processing)
  useEffect(() => {
    stepCycleRef.current = setInterval(() => {
      setCurrentStepIdx((idx) =>
        idx < PIPELINE_STEPS.length - 1 ? idx + 1 : idx
      )
    }, 12000)
    return () => clearInterval(stepCycleRef.current)
  }, [])

  // Polling
  useEffect(() => {
    if (!id) return

    const poll = async () => {
      try {
        const res = await generations.get(id)
        const data = res.data
        setGenData(data)

        if (data.status === 'done') {
          clearInterval(intervalRef.current)
          clearInterval(timerRef.current)
          clearInterval(stepCycleRef.current)
          // Small delay for UX
          setTimeout(() => navigate(`/result/${id}`), 800)
        } else if (data.status === 'error') {
          clearInterval(intervalRef.current)
          clearInterval(timerRef.current)
          setError(data.error_message || 'Une erreur est survenue lors de la génération.')
        }
      } catch (err) {
        const msg =
          err.response?.data?.detail ||
          err.message ||
          'Impossible de récupérer le statut.'
        setError(msg)
        clearInterval(intervalRef.current)
      }
    }

    poll() // immediate first call
    intervalRef.current = setInterval(poll, 2500)

    return () => {
      clearInterval(intervalRef.current)
    }
  }, [id, navigate])

  const formatTime = (seconds) => {
    if (seconds <= 0) return 'Finalisation...'
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    if (m > 0) return `${m}m ${String(s).padStart(2, '0')}s`
    return `${s}s`
  }

  const status = genData?.status || 'pending'
  const productName = genData?.product_name || genData?.product?.name || 'Votre produit'
  const productBrand = genData?.product?.brand || genData?.brand || ''

  const statusMessages = {
    pending: 'En file d\'attente...',
    processing: 'Génération en cours...',
    done: 'Génération terminée ! Redirection...',
    error: 'Une erreur est survenue.',
  }

  const currentStep = PIPELINE_STEPS[currentStepIdx]

  return (
    <div className="generation-page">
      {/* Background orbs */}
      <div className="generation-orb generation-orb-1" />
      <div className="generation-orb generation-orb-2" />
      <div className="generation-orb generation-orb-3" />

      <div className="generation-content animate-enter">
        {/* Product info */}
        <div
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '8px',
            background: 'var(--gold-dim)',
            border: '1px solid rgba(212,160,85,0.25)',
            borderRadius: 'var(--radius-full)',
            padding: '6px 16px',
            marginBottom: '16px',
            fontSize: '0.8rem',
            color: 'var(--gold)',
            fontWeight: 600,
          }}
        >
          ✨ {status === 'done' ? 'Terminé !' : status === 'pending' ? 'En attente' : 'En cours de génération'}
        </div>

        <h1 className="generation-product-name">{productName}</h1>
        {productBrand && (
          <p className="generation-product-sub">par {productBrand}</p>
        )}
        {!productBrand && (
          <p className="generation-product-sub">{statusMessages[status]}</p>
        )}

        {/* Error state */}
        {error ? (
          <div className="generation-error">
            <div
              style={{
                fontSize: '2rem',
                marginBottom: '12px',
              }}
            >
              ⚠️
            </div>
            <h3
              style={{
                fontFamily: 'var(--font-heading)',
                marginBottom: '8px',
                color: 'var(--error)',
              }}
            >
              Génération échouée
            </h3>
            <p
              style={{
                color: 'var(--text-secondary)',
                fontSize: '0.9rem',
                marginBottom: '20px',
              }}
            >
              {error}
            </p>
            <div
              style={{
                display: 'flex',
                gap: '10px',
                justifyContent: 'center',
                flexWrap: 'wrap',
              }}
            >
              <button
                className="btn btn-primary"
                onClick={() => window.location.reload()}
              >
                🔄 Réessayer
              </button>
              <button
                className="btn btn-ghost"
                onClick={() => navigate('/dashboard')}
              >
                ← Retour au dashboard
              </button>
            </div>
          </div>
        ) : (
          <>
            {/* Step loader */}
            <StepLoader
              currentStep={currentStep?.id}
              steps={PIPELINE_STEPS}
            />

            {/* Status line */}
            <p
              style={{
                marginTop: '16px',
                color: 'var(--text-secondary)',
                fontSize: '0.88rem',
              }}
            >
              {statusMessages[status]}
            </p>

            {/* Timer */}
            {status !== 'done' && (
              <div className="generation-timer">
                <span>⏱️ Temps estimé restant :</span>
                <span className="generation-timer-value">
                  {formatTime(timeLeft)}
                </span>
              </div>
            )}

            {/* Redirect notice when done */}
            {status === 'done' && (
              <div
                style={{
                  marginTop: '20px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '10px',
                  color: 'var(--success)',
                  fontWeight: 600,
                }}
              >
                <div
                  style={{
                    width: '16px',
                    height: '16px',
                    border: '2px solid rgba(34,197,94,0.3)',
                    borderTopColor: 'var(--success)',
                    borderRadius: '50%',
                    animation: 'spin 0.7s linear infinite',
                  }}
                />
                Redirection vers les résultats...
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

export default Generation
