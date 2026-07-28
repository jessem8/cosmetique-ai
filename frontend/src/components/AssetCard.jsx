import { useState } from 'react'

const FORMAT_INFO = {
  instagram: { emoji: '📸', label: 'Instagram' },
  facebook: { emoji: '📘', label: 'Facebook' },
  linkedin: { emoji: '💼', label: 'LinkedIn' },
}

function AssetCard({ asset }) {
  const [copied, setCopied] = useState(false)

  if (!asset) return null

  const format = asset.format || 'instagram'
  const info = FORMAT_INFO[format] || FORMAT_INFO.instagram
  const resolution =
    asset.width && asset.height ? `${asset.width} × ${asset.height}` : null

  const handleCopyUrl = () => {
    if (!asset.url) return
    navigator.clipboard
      .writeText(asset.url)
      .then(() => {
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
      })
      .catch(() => {})
  }

  return (
    <div className="asset-card">
      {/* Thumbnail */}
      <div className="asset-card-thumb">
        {asset.url ? (
          <img src={asset.url} alt={`${info.label} asset`} loading="lazy" />
        ) : (
          <div
            style={{
              width: '100%',
              height: '100%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: 'rgba(255,255,255,0.02)',
              fontSize: '2rem',
              opacity: 0.3,
            }}
          >
            🖼️
          </div>
        )}

        {/* Format badge overlay */}
        <div className="asset-card-format-badge">
          {info.emoji} {info.label}
        </div>
      </div>

      {/* Body */}
      <div className="asset-card-body">
        <div className="asset-card-meta">
          <div
            style={{
              fontFamily: 'var(--font-heading)',
              fontSize: '0.95rem',
              fontWeight: 600,
            }}
          >
            {info.label}
          </div>
          {resolution && (
            <span className="asset-card-resolution">{resolution} px</span>
          )}
        </div>

        {/* Actions */}
        <div className="asset-card-actions">
          {asset.url && (
            <a
              href={asset.url}
              download={`${format}-poster.jpg`}
              target="_blank"
              rel="noreferrer"
              className="btn btn-primary btn-sm"
              style={{ flex: 1, textDecoration: 'none' }}
            >
              <span>⬇</span>
              <span>Télécharger</span>
            </a>
          )}
          <button
            className={`copy-btn${copied ? ' copied' : ''}`}
            onClick={handleCopyUrl}
            title="Copier l'URL"
          >
            {copied ? '✓ Copié' : '🔗 URL'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default AssetCard
