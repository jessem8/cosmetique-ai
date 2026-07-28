import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { products, generations } from '../api/client.js'

const CATEGORY_LABELS = {
  soin_visage: 'Soin Visage',
  soin_corps: 'Soin Corps',
  cheveux: 'Cheveux',
  maquillage: 'Maquillage',
  solaire: 'Solaire',
  hygiene_deo: 'Hygiène & Déo',
}

function formatDate(dateStr) {
  if (!dateStr) return ''
  return new Date(dateStr).toLocaleDateString('fr-FR', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  })
}

function StatusBadge({ status }) {
  const map = {
    pending:    { label: 'En attente', cls: 'badge-pending' },
    processing: { label: 'En cours',   cls: 'badge-processing' },
    done:       { label: 'Terminé',    cls: 'badge-done' },
    error:      { label: 'Erreur',     cls: 'badge-error' },
  }
  const s = map[status] || map.pending
  return <span className={`badge ${s.cls}`}>{s.label}</span>
}

function SkeletonCard() {
  return (
    <div className="skeleton-card">
      <div className="skeleton-thumb" />
      <div className="skeleton-body">
        <div className="skeleton-line long" />
        <div className="skeleton-line short" />
        <div className="skeleton-line medium" />
      </div>
    </div>
  )
}

function Dashboard() {
  const navigate = useNavigate()
  const [items, setItems]   = useState([])   // [{product, generation}]
  const [loading, setLoading] = useState(true)
  const [error, setError]   = useState('')

  useEffect(() => { loadData() }, [])

  const loadData = async () => {
    setLoading(true)
    setError('')
    try {
      // 1. Get all products (returns {items: [...], total: N})
      const productsRes = await products.list()
      const productList = productsRes.data?.items || productsRes.data || []

      // 2. For each product, get its most recent generation
      //    using the dedicated endpoint GET /products/{id}/generations?limit=1
      const enriched = await Promise.all(
        productList.map(async (product) => {
          try {
            const genRes = await generations.listForProduct(product.id, 1)
            const genList = genRes.data || []
            return { product, generation: genList[0] || null }
          } catch {
            return { product, generation: null }
          }
        })
      )

      setItems(enriched)
    } catch (err) {
      setError(
        err.response?.data?.detail ||
          err.message ||
          'Impossible de charger vos créations.'
      )
    } finally {
      setLoading(false)
    }
  }

  const handleCardClick = (item) => {
    const gen = item.generation
    if (!gen) {
      // Product exists but no generation yet — start one
      navigate('/new')
      return
    }
    if (gen.status === 'done') {
      navigate(`/result/${gen.id}`)
    } else {
      navigate(`/generations/${gen.id}`)
    }
  }

  const getFirstAssetUrl = (gen) => {
    if (!gen) return null
    if (gen.assets && gen.assets.length > 0) return gen.assets[0].url
    return null
  }

  return (
    <div className="page-container">
      <div className="page-content animate-enter">

        {/* ── Header ────────────────────────────────────────────── */}
        <div className="dashboard-header">
          <div className="dashboard-title-group">
            <h1 className="section-title">
              Mes <span>Créations</span>
            </h1>
            {!loading && items.length > 0 && (
              <div className="count-badge">{items.length}</div>
            )}
          </div>
          <button
            id="btn-new-product"
            className="btn btn-primary glow"
            onClick={() => navigate('/new')}
          >
            <span>Nouveau produit</span>
          </button>
        </div>

        {/* ── Error banner ───────────────────────────────────────── */}
        {error && (
          <div className="auth-error" style={{ marginBottom: '24px' }}>
            <span>⚠️</span>
            {error}
            <button
              onClick={loadData}
              className="btn btn-ghost btn-sm"
              style={{ marginLeft: 'auto' }}
            >
              Réessayer
            </button>
          </div>
        )}

        {/* ── Grid ──────────────────────────────────────────────── */}
        <div className="generation-grid">
          {loading ? (
            <>
              <SkeletonCard />
              <SkeletonCard />
              <SkeletonCard />
            </>
          ) : items.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon">🧴</div>
              <h2 className="empty-state-title">Aucune création pour l'instant</h2>
              <p className="empty-state-text">
                Importez votre première photo produit et laissez l'IA créer
                vos affiches premium automatiquement.
              </p>
              <button
                id="btn-first-creation"
                className="btn btn-primary btn-primary--large glow"
                onClick={() => navigate('/new')}
              >
                <span>Créer ma première affiche</span>
              </button>
            </div>
          ) : (
            items.map((item, idx) => {
              const { product, generation } = item
              const thumbUrl = getFirstAssetUrl(generation)
              const status   = generation?.status || 'none'

              return (
                <div
                  key={product.id || idx}
                  id={`card-product-${product.id}`}
                  className="generation-card"
                  onClick={() => handleCardClick(item)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => e.key === 'Enter' && handleCardClick(item)}
                  style={{ animationDelay: `${idx * 0.05}s` }}
                >
                  {/* Thumbnail */}
                  <div className="generation-card-thumbnail">
                    {thumbUrl ? (
                      <img src={thumbUrl} alt={product.name} loading="lazy" />
                    ) : (
                      <div
                        style={{
                          width: '100%', height: '100%',
                          display: 'flex', alignItems: 'center',
                          justifyContent: 'center',
                          background: 'var(--bg-beige)',
                          flexDirection: 'column', gap: '8px',
                        }}
                      >
                        {status === 'processing' || status === 'pending' ? (
                          <>
                            <div
                              style={{
                                width: '32px', height: '32px',
                                border: '3px solid rgba(244,131,155,0.2)',
                                borderTopColor: 'var(--rose)',
                                borderRadius: '50%',
                                animation: 'spin 1s linear infinite',
                              }}
                            />
                            <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                              Génération…
                            </span>
                          </>
                        ) : (
                          <span style={{ fontSize: '2.5rem', opacity: 0.35 }}>🧴</span>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Body */}
                  <div className="generation-card-body">
                    <div className="generation-card-name">
                      {product.name || 'Produit sans nom'}
                    </div>
                    {product.brand && (
                      <div className="generation-card-brand">{product.brand}</div>
                    )}
                    <div className="generation-card-meta">
                      {product.category && (
                        <span className="badge badge-category">
                          {CATEGORY_LABELS[product.category] || product.category}
                        </span>
                      )}
                      {generation && <StatusBadge status={status} />}
                    </div>
                    <div className="generation-card-date" style={{ marginTop: '8px' }}>
                      {formatDate(generation?.created_at || product.created_at)}
                    </div>
                  </div>
                </div>
              )
            })
          )}
        </div>
      </div>
    </div>
  )
}

export default Dashboard
