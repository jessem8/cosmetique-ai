import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowRight,
  ImageSquare,
  Plus,
  WarningCircle,
} from '@phosphor-icons/react'
import { generations } from '../api/client.js'

const STATUS_LABELS = {
  pending: 'En attente',
  processing: 'En création',
  done: 'Terminée',
  error: 'Arrêtée',
}

const ERROR_LABELS = {
  AI_RUNTIME_LOST: 'Session IA interrompue',
  AI_SERVICE_UNAVAILABLE: 'Studio IA indisponible',
  TARGET_AMBIGUOUS: 'Produit à confirmer',
  TARGET_NOT_FOUND: 'Produit introuvable',
  EXTRACTION_FAILED: 'Extraction impossible',
  MASK_QUALITY_FAILED: 'Contour insuffisant',
  CLAIM_SAFETY_FAILED: 'Texte non conforme',
}

const campaignHref = (generation) =>
  generation.status === 'done'
    ? `/result/${generation.id}`
    : `/generations/${generation.id}`

const formatDate = (value) => {
  if (!value) return 'Date indisponible'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Date indisponible'
  return new Intl.DateTimeFormat('fr-FR', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function CampaignThumbnail({ generation }) {
  const [url, setUrl] = useState(null)

  useEffect(() => {
    if (generation.status !== 'done') return undefined
    const controller = new AbortController()
    let objectUrl = null

    generations
      .getArtifact(generation.id, 'instagram.jpg', {
        signal: controller.signal,
      })
      .then((response) => {
        objectUrl = URL.createObjectURL(response.data)
        setUrl(objectUrl)
      })
      .catch(() => {})

    return () => {
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [generation.id, generation.status])

  return (
    <div className="campaign-row__media">
      {url ? (
        <img src={url} alt="" loading="lazy" />
      ) : (
        <ImageSquare size={28} weight="light" aria-hidden="true" />
      )}
    </div>
  )
}

function Dashboard() {
  const [items, setItems] = useState([])
  const [cursor, setCursor] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState(null)
  const loadMoreControllerRef = useRef(null)

  const load = useCallback(
    async ({ nextCursor = null, append = false, signal } = {}) => {
      const response = await generations.list({
        cursor: nextCursor,
        limit: 20,
        signal,
      })
      const body = response.data
      const nextItems = Array.isArray(body) ? body : body.items || []
      setItems((current) => (append ? [...current, ...nextItems] : nextItems))
      setCursor(Array.isArray(body) ? null : body.next_cursor || null)
    },
    []
  )

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    load({ signal: controller.signal })
      .catch((requestError) => {
        if (requestError.code !== 'REQUEST_CANCELLED') setError(requestError)
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => {
      controller.abort()
      loadMoreControllerRef.current?.abort()
    }
  }, [load])

  const loadMore = async () => {
    if (!cursor) return
    loadMoreControllerRef.current?.abort()
    const controller = new AbortController()
    loadMoreControllerRef.current = controller
    setLoadingMore(true)
    setError(null)
    try {
      await load({
        nextCursor: cursor,
        append: true,
        signal: controller.signal,
      })
    } catch (requestError) {
      if (
        !controller.signal.aborted &&
        requestError.code !== 'REQUEST_CANCELLED'
      ) {
        setError(requestError)
      }
    } finally {
      if (loadMoreControllerRef.current === controller) {
        loadMoreControllerRef.current = null
        if (!controller.signal.aborted) setLoadingMore(false)
      }
    }
  }

  return (
    <div className="workspace-page workspace-page--wide">
      <header className="library-heading">
        <div>
          <p className="eyebrow">Bibliothèque</p>
          <h1>Campagnes</h1>
          <p>Chaque génération conserve son brief, sa langue et son résultat.</p>
        </div>
        {(loading || items.length > 0) && (
          <Link className="button button--primary" to="/new">
            <Plus size={18} aria-hidden="true" />
            Créer une campagne
          </Link>
        )}
      </header>

      {error && (
        <div className="inline-alert inline-alert--error" role="alert">
          <WarningCircle size={20} aria-hidden="true" />
          <div>
            <strong>Historique indisponible</strong>
            <p>{error.message}</p>
          </div>
        </div>
      )}

      {loading ? (
        <div className="campaign-list" aria-label="Chargement de l’historique">
          {[0, 1, 2].map((item) => (
            <div className="campaign-row campaign-row--skeleton" key={item}>
              <div className="skeleton skeleton--thumb" />
              <div className="skeleton skeleton--body" />
            </div>
          ))}
        </div>
      ) : items.length === 0 ? (
        <section className="empty-state">
          <div className="empty-state__monogram" aria-hidden="true">
            CA
          </div>
          <h2>Composez votre première campagne.</h2>
          <p>
            Importez un produit, donnez uniquement les faits vérifiés, puis
            suivez chaque étape réelle de création.
          </p>
          <Link className="button button--primary" to="/new">
            Créer une campagne
          </Link>
        </section>
      ) : (
        <>
          <section className="campaign-list" aria-label="Historique des campagnes">
            {items.map((generation) => {
              const product = generation.product || {}
              const name =
                product.name || generation.product_name || 'Produit sans nom'
              const brand = product.brand || generation.product_brand
              return (
                <article className="campaign-row" key={generation.id}>
                  <CampaignThumbnail generation={generation} />
                  <div className="campaign-row__main">
                    <div>
                      <h2>{name}</h2>
                      <p>{brand || 'Marque non renseignée'}</p>
                    </div>
                    <dl className="campaign-row__meta">
                      <div>
                        <dt>Langue</dt>
                        <dd>
                          {generation.language === 'en' ? 'Anglais' : 'Français'}
                        </dd>
                      </div>
                      <div>
                        <dt>Création</dt>
                        <dd>{formatDate(generation.created_at)}</dd>
                      </div>
                    </dl>
                  </div>
                  <div className="campaign-row__status">
                    <span
                      className="status-label"
                      data-status={generation.status}
                    >
                      {STATUS_LABELS[generation.status] || generation.status}
                    </span>
                    {generation.status === 'error' && (
                      <span className="campaign-row__error">
                        {ERROR_LABELS[generation.error?.code] ||
                          generation.error?.message ||
                          'Échec de la génération'}
                      </span>
                    )}
                  </div>
                  <Link
                    className="icon-button campaign-row__link"
                    to={campaignHref(generation)}
                    aria-label={`Ouvrir la campagne ${name}`}
                  >
                    <ArrowRight size={19} aria-hidden="true" />
                  </Link>
                </article>
              )
            })}
          </section>
          {cursor && (
            <div className="library-more">
              <button
                type="button"
                className="button button--secondary"
                onClick={loadMore}
                disabled={loadingMore}
              >
                {loadingMore ? 'Chargement' : 'Afficher plus'}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

export default Dashboard
