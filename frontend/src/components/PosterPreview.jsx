import { useState } from 'react'

const FORMAT_CONFIG = {
  instagram: {
    label: 'Instagram',
    emoji: '📸',
    ratioClass: 'poster-preview-ratio-1-1',
    width: 1080,
    height: 1080,
    dims: '1080 × 1080',
  },
  facebook: {
    label: 'Facebook',
    emoji: '📘',
    ratioClass: 'poster-preview-ratio-191-1',
    width: 1200,
    height: 630,
    dims: '1200 × 630',
  },
  linkedin: {
    label: 'LinkedIn',
    emoji: '💼',
    ratioClass: 'poster-preview-ratio-191-1',
    width: 1200,
    height: 627,
    dims: '1200 × 627',
  },
}

function PosterPreview({ url, format = 'instagram', label, isActive = false }) {
  const [imgLoaded, setImgLoaded] = useState(false)
  const [imgError, setImgError] = useState(false)
  const config = FORMAT_CONFIG[format] || FORMAT_CONFIG.instagram

  return (
    <div className="poster-preview-wrapper">
      {/* Label */}
      <div className="poster-preview-label">
        <span>{config.emoji}</span>
        {label || config.label}
      </div>

      {/* Card */}
      <div className={`poster-preview-card${isActive ? ' active-format' : ''}`}>
        <div className={`poster-preview-ratio ${config.ratioClass}`}>
          {/* Skeleton while loading */}
          {!imgLoaded && !imgError && url && (
            <div className="poster-preview-skeleton" />
          )}

          {/* Placeholder when no URL */}
          {!url && (
            <div
              style={{
                position: 'absolute',
                inset: 0,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                background: 'rgba(255,255,255,0.02)',
                gap: '8px',
              }}
            >
              <span style={{ fontSize: '2rem', opacity: 0.25 }}>🖼️</span>
              <span
                style={{
                  fontSize: '0.75rem',
                  color: 'var(--text-muted)',
                  opacity: 0.5,
                }}
              >
                {config.dims}
              </span>
            </div>
          )}

          {/* Error state */}
          {imgError && (
            <div
              style={{
                position: 'absolute',
                inset: 0,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                background: 'rgba(239,68,68,0.05)',
                gap: '8px',
              }}
            >
              <span style={{ fontSize: '1.5rem' }}>⚠️</span>
              <span
                style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}
              >
                Image indisponible
              </span>
            </div>
          )}

          {/* Actual image */}
          {url && !imgError && (
            <img
              src={url}
              alt={`${config.label} poster`}
              className="poster-preview-img"
              style={{ opacity: imgLoaded ? 1 : 0, transition: 'opacity 0.3s' }}
              onLoad={() => setImgLoaded(true)}
              onError={() => setImgError(true)}
              loading="lazy"
            />
          )}

          {/* Hover overlay */}
          {url && !imgError && (
            <div className="poster-preview-overlay">
              <span className="poster-preview-overlay-format">
                {config.emoji} {config.label}
              </span>
              <span className="poster-preview-overlay-dims">{config.dims}</span>
            </div>
          )}

          {/* Download button */}
          {url && !imgError && (
            <a
              href={url}
              download={`${format}-poster.jpg`}
              className="poster-preview-download-btn"
              onClick={(e) => e.stopPropagation()}
              target="_blank"
              rel="noreferrer"
            >
              ⬇ Télécharger
            </a>
          )}
        </div>
      </div>

      {/* Dimensions badge below */}
      <div
        style={{
          textAlign: 'center',
          fontSize: '0.72rem',
          color: 'var(--text-muted)',
          fontFamily: 'var(--font-heading)',
          letterSpacing: '0.05em',
        }}
      >
        {config.dims} px
      </div>
    </div>
  )
}

export default PosterPreview
