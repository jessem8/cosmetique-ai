import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { generations } from '../api/client.js'
import PosterPreview from '../components/PosterPreview.jsx'
import AssetCard from '../components/AssetCard.jsx'

const TONES = [
  { value: 'luxe', label: '💎 Luxe' },
  { value: 'naturel', label: '🌿 Naturel' },
  { value: 'dynamique', label: '⚡ Dynamique' },
  { value: 'frais', label: '❄️ Frais' },
  { value: 'scientifique', label: '🔬 Scientifique' },
]

const FORMATS = ['instagram', 'facebook', 'linkedin']

const FORMAT_CONFIG = {
  instagram: { emoji: '📸', label: 'Instagram' },
  facebook: { emoji: '📘', label: 'Facebook' },
  linkedin: { emoji: '💼', label: 'LinkedIn' },
}

function formatDate(dateStr) {
  if (!dateStr) return ''
  return new Date(dateStr).toLocaleDateString('fr-FR', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const CATEGORY_LABELS = {
  soin_visage: 'Soin Visage',
  soin_corps: 'Soin Corps',
  cheveux: 'Cheveux',
  maquillage: 'Maquillage',
  solaire: 'Solaire',
  hygiene: 'Hygiène & Déo',
}

function ConfirmModal({ title, text, onConfirm, onCancel }) {
  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <h2 className="modal-title">{title}</h2>
        <p className="modal-text">{text}</p>
        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onCancel}>
            Annuler
          </button>
          <button className="btn btn-warning" onClick={onConfirm}>
            Confirmer
          </button>
        </div>
      </div>
    </div>
  )
}

function Result() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [genData, setGenData] = useState(null)
  const [assets, setAssets] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [activeFormat, setActiveFormat] = useState('instagram')
  const [zipLoading, setZipLoading] = useState(false)

  // Text regeneration state
  const [regenTone, setRegenTone] = useState('luxe')
  const [regenLoading, setRegenLoading] = useState(false)
  const [regenError, setRegenError] = useState('')

  // Decor regeneration state
  const [decorModal, setDecorModal] = useState(false)
  const [decorLoading, setDecorLoading] = useState(false)

  useEffect(() => {
    loadResult()
  }, [id])

  const loadResult = async () => {
    setLoading(true)
    setError('')
    try {
      const [genRes, assetsRes] = await Promise.all([
        generations.get(id),
        generations.getAssets(id),
      ])
      setGenData(genRes.data)
      const assetList = assetsRes.data
      setAssets(Array.isArray(assetList) ? assetList : assetList?.assets || [])
    } catch (err) {
      setError(
        err.response?.data?.detail ||
          err.message ||
          'Impossible de charger les résultats.'
      )
    } finally {
      setLoading(false)
    }
  }

  const handleRegenerateText = async () => {
    setRegenLoading(true)
    setRegenError('')
    try {
      const res = await generations.regenerateText(id, regenTone)
      // Update marketing text in place
      setGenData((prev) => ({
        ...prev,
        marketing_text: res.data.marketing_text || res.data,
        marketing_content: res.data.marketing_content || prev?.marketing_content,
      }))
    } catch (err) {
      setRegenError(
        err.response?.data?.detail ||
          err.message ||
          'Impossible de régénérer le texte.'
      )
    } finally {
      setRegenLoading(false)
    }
  }

  const handleRegenerateDecor = async () => {
    setDecorModal(false)
    setDecorLoading(true)
    try {
      const res = await generations.regenerateDecor(id)
      const newGenId = res.data.generation_id || res.data.id || id
      navigate(`/generations/${newGenId}`)
    } catch (err) {
      setDecorLoading(false)
      alert(
        err.response?.data?.detail ||
          err.message ||
          'Impossible de régénérer le décor.'
      )
    }
  }

  const getAssetByFormat = (format) =>
    assets.find(
      (a) =>
        a.format === format ||
        (a.format || '').toLowerCase() === format.toLowerCase()
    )

  const downloadBlob = (blob, filename) => {
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    window.URL.revokeObjectURL(url)
  }

  const downloadAll = async () => {
    setZipLoading(true)
    try {
      const res = await generations.downloadZip(id)
      downloadBlob(res.data, `affiches_${id}.zip`)
    } catch (err) {
      alert(
        err.response?.data?.detail ||
          err.message ||
          'Impossible de telecharger les affiches.'
      )
    } finally {
      setZipLoading(false)
    }
  }

  // Extract marketing content
  const marketing =
    genData?.marketing_text ||
    genData?.marketing_content ||
    genData?.content ||
    null
  const marketingTitle = marketing?.titre || marketing?.title
  const marketingSubtitle = marketing?.sous_titre || marketing?.subtitle

  const product = genData?.product || {}
  const productName =
    genData?.product_name || product.name || 'Produit cosmétique'
  const brand = genData?.brand || product.brand || ''
  const category = genData?.category || product.category || ''

  if (loading) {
    return (
      <div className="page-container">
        <div className="page-content" style={{ textAlign: 'center', paddingTop: '80px' }}>
          <div
            style={{
              width: '48px',
              height: '48px',
              border: '3px solid rgba(212,160,85,0.2)',
              borderTopColor: 'var(--gold)',
              borderRadius: '50%',
              animation: 'spin 0.8s linear infinite',
              margin: '0 auto 20px',
            }}
          />
          <p style={{ color: 'var(--text-secondary)' }}>
            Chargement des résultats...
          </p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="page-container">
        <div className="page-content" style={{ textAlign: 'center', paddingTop: '60px' }}>
          <div style={{ fontSize: '3rem', marginBottom: '16px' }}>⚠️</div>
          <p style={{ color: 'var(--error)', marginBottom: '20px' }}>
            {error}
          </p>
          <button className="btn btn-primary" onClick={() => navigate('/dashboard')}>
            ← Retour au dashboard
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="page-container">
      <div className="page-content animate-enter">
        {/* ---- HEADER ---- */}
        <div className="result-header">
          <div>
            <div className="result-meta" style={{ marginBottom: '10px' }}>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => navigate('/dashboard')}
                style={{ padding: '4px 8px' }}
              >
                ← Dashboard
              </button>
              {category && (
                <span className="badge badge-category">
                  {CATEGORY_LABELS[category] || category}
                </span>
              )}
              <span className="badge badge-done">✓ Généré</span>
            </div>
            <h1 className="result-title">{productName}</h1>
            {brand && (
              <p style={{ color: 'var(--text-secondary)', marginTop: '4px' }}>
                par {brand}
              </p>
            )}
            <div className="result-meta" style={{ marginTop: '10px' }}>
              {genData?.created_at && (
                <span className="result-meta-item">
                  <span>🕐</span>
                  Généré le {formatDate(genData.created_at)}
                </span>
              )}
            </div>
          </div>

          {/* Download all */}
          <div className="download-section">
            <button
              className="btn btn-primary"
              onClick={downloadAll}
              disabled={assets.length === 0 || zipLoading}
            >
              <span>⬇️</span>
              <span>Télécharger tout</span>
            </button>
          </div>
        </div>

        <div className="section-divider" />

        {/* ---- POSTER PREVIEWS ---- */}
        <section style={{ marginBottom: '40px' }}>
          <h2
            style={{
              fontFamily: 'var(--font-heading)',
              fontSize: '1.2rem',
              fontWeight: 700,
              marginBottom: '16px',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
            }}
          >
            🖼️ Affiches générées
          </h2>

          {/* Format tabs */}
          <div className="format-tabs">
            {FORMATS.map((fmt) => (
              <button
                key={fmt}
                className={`format-tab${activeFormat === fmt ? ' active' : ''}`}
                onClick={() => setActiveFormat(fmt)}
              >
                {FORMAT_CONFIG[fmt].emoji} {FORMAT_CONFIG[fmt].label}
              </button>
            ))}
          </div>

          {/* All 3 posters */}
          <div className="posters-grid">
            {FORMATS.map((fmt) => {
              const asset = getAssetByFormat(fmt)
              return (
                <PosterPreview
                  key={fmt}
                  url={asset?.url}
                  format={fmt}
                  label={FORMAT_CONFIG[fmt].label}
                  isActive={activeFormat === fmt}
                />
              )
            })}
          </div>
        </section>

        {/* ---- ASSET CARDS ---- */}
        {assets.length > 0 && (
          <section style={{ marginBottom: '40px' }}>
            <h2
              style={{
                fontFamily: 'var(--font-heading)',
                fontSize: '1.2rem',
                fontWeight: 700,
                marginBottom: '16px',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
              }}
            >
              📦 Fichiers exportés
            </h2>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
                gap: '16px',
              }}
            >
              {assets.map((asset, idx) => (
                <AssetCard key={asset.id || idx} asset={asset} />
              ))}
            </div>
          </section>
        )}

        <div className="section-divider" />

        {/* ---- MARKETING TEXT ---- */}
        <section style={{ marginBottom: '40px' }}>
          <h2
            style={{
              fontFamily: 'var(--font-heading)',
              fontSize: '1.2rem',
              fontWeight: 700,
              marginBottom: '16px',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
            }}
          >
            ✍️ Texte marketing
          </h2>

          <div className="glass-card marketing-card">
            <div className="marketing-card-header">
              <div>
                <div className="marketing-card-title-label">Contenu généré par IA</div>
              </div>
              {/* Regenerate text controls */}
              <div className="regenerate-section">
                <select
                  className="select-field"
                  value={regenTone}
                  onChange={(e) => setRegenTone(e.target.value)}
                  style={{ width: 'auto', fontSize: '0.82rem', padding: '7px 36px 7px 12px' }}
                  disabled={regenLoading}
                >
                  {TONES.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </select>
                <button
                  className={`btn btn-secondary btn-sm${regenLoading ? ' btn-loading' : ''}`}
                  onClick={handleRegenerateText}
                  disabled={regenLoading}
                >
                  <span>{regenLoading ? '' : '🔄 Régénérer le texte'}</span>
                </button>
              </div>
            </div>

            {regenError && (
              <div
                className="auth-error"
                style={{ marginBottom: '16px', fontSize: '0.82rem' }}
              >
                <span>⚠️</span> {regenError}
              </div>
            )}

            {/* Marketing content display */}
            {marketing ? (
              <div style={{ position: 'relative' }}>
                {regenLoading && (
                  <div
                    style={{
                      position: 'absolute',
                      inset: 0,
                      background: 'rgba(8,8,15,0.7)',
                      backdropFilter: 'blur(4px)',
                      borderRadius: 'var(--radius-md)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      zIndex: 10,
                      gap: '12px',
                      color: 'var(--gold)',
                      fontWeight: 600,
                    }}
                  >
                    <span className="inline-spinner" />
                    Régénération en cours...
                  </div>
                )}

                {marketingTitle && (
                  <div className="marketing-title">{marketingTitle}</div>
                )}
                {marketingSubtitle && (
                  <div className="marketing-subtitle">{marketingSubtitle}</div>
                )}
                {marketing.bullets && marketing.bullets.length > 0 && (
                  <div className="marketing-bullets">
                    {marketing.bullets.map((bullet, i) => (
                      <div key={i} className="marketing-bullet">
                        <div className="marketing-bullet-dot" />
                        <span>{bullet}</span>
                      </div>
                    ))}
                  </div>
                )}
                {marketing.cta && (
                  <div>
                    <span className="marketing-cta-preview">{marketing.cta}</span>
                  </div>
                )}

                {/* If marketing is a raw string */}
                {typeof marketing === 'string' && (
                  <p
                    style={{
                      color: 'var(--text-secondary)',
                      fontSize: '0.9rem',
                      lineHeight: 1.7,
                      whiteSpace: 'pre-line',
                    }}
                  >
                    {marketing}
                  </p>
                )}
              </div>
            ) : (
              <div
                style={{
                  color: 'var(--text-muted)',
                  fontStyle: 'italic',
                  fontSize: '0.88rem',
                  textAlign: 'center',
                  padding: '20px',
                }}
              >
                Aucun texte marketing disponible pour le moment.
              </div>
            )}
          </div>
        </section>

        <div className="section-divider" />

        {/* ---- DECOR REGENERATION ---- */}
        <section style={{ marginBottom: '40px' }}>
          <h2
            style={{
              fontFamily: 'var(--font-heading)',
              fontSize: '1.2rem',
              fontWeight: 700,
              marginBottom: '16px',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
            }}
          >
            🎨 Décor IA
          </h2>

          <div className="glass-card decor-section">
            <div className="decor-section-info">
              <div className="decor-section-title">Régénérer le décor</div>
              <div className="decor-section-sub">
                Générez un nouveau décor pour votre affiche avec une composition différente.
                Attention, cette action relancera une nouvelle génération complète.
              </div>
            </div>
            <button
              className={`btn btn-warning${decorLoading ? ' btn-loading' : ''}`}
              onClick={() => setDecorModal(true)}
              disabled={decorLoading}
            >
              <span>
                {decorLoading ? '' : '🔄 Régénérer le décor'}
              </span>
            </button>
          </div>
        </section>

        {/* Confirmation modal */}
        {decorModal && (
          <ConfirmModal
            title="🎨 Régénérer le décor ?"
            text="Cette action va relancer une génération complète avec un nouveau décor. Le processus prendra environ 60 à 90 secondes. Voulez-vous continuer ?"
            onConfirm={handleRegenerateDecor}
            onCancel={() => setDecorModal(false)}
          />
        )}
      </div>
    </div>
  )
}

export default Result
