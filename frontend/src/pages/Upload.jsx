import { useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { products, generations } from '../api/client.js'

const STEPS = [
  { id: 1, label: "Image produit" },
  { id: 2, label: "Informations" },
  { id: 3, label: "Confirmation" },
]

const CATEGORIES = [
  { value: 'soin_visage', label: 'Soin Visage' },
  { value: 'soin_corps', label: 'Soin Corps' },
  { value: 'cheveux', label: 'Cheveux' },
  { value: 'maquillage', label: 'Maquillage' },
  { value: 'solaire', label: 'Solaire' },
  { value: 'hygiene', label: 'Hygiène & Déo' },
]

const TONES = [
  { value: 'luxe', label: '💎 Luxe' },
  { value: 'naturel', label: '🌿 Naturel' },
  { value: 'dynamique', label: '⚡ Dynamique' },
  { value: 'frais', label: '❄️ Frais' },
  { value: 'scientifique', label: '🔬 Scientifique' },
]

const TEMPLATES = [
  { value: 'classique', label: '🎭 Classique' },
  { value: 'minimaliste', label: '◻️ Minimaliste' },
  { value: 'bold', label: '🔥 Bold' },
]

function StepIndicator({ current }) {
  return (
    <div className="upload-steps-indicator">
      {STEPS.map((step, idx) => (
        <div className="upload-step-item" key={step.id}>
          <div className="upload-step-wrapper">
            <div
              className={`upload-step-dot${
                current === step.id
                  ? ' active'
                  : current > step.id
                  ? ' completed'
                  : ''
              }`}
            >
              {current > step.id ? '✓' : step.id}
            </div>
            <span className="upload-step-label">{step.label}</span>
          </div>
          {idx < STEPS.length - 1 && (
            <div
              className={`upload-step-connector${
                current > step.id ? ' completed' : ''
              }`}
            />
          )}
        </div>
      ))}
    </div>
  )
}

function Upload() {
  const navigate = useNavigate()
  const fileInputRef = useRef(null)
  const [step, setStep] = useState(1)
  const [dragging, setDragging] = useState(false)
  const [imageFile, setImageFile] = useState(null)
  const [imagePreview, setImagePreview] = useState(null)
  const [fileError, setFileError] = useState('')
  const [form, setForm] = useState({
    name: '',
    brand: '',
    category: 'soin_visage',
    tone: 'luxe',
    template: 'classique',
  })
  const [formErrors, setFormErrors] = useState({})
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')

  // --- Step 1: Image handling ---
  const ACCEPTED = ['image/jpeg', 'image/png', 'image/webp']
  const MAX_SIZE = 10 * 1024 * 1024 // 10 MB

  const handleFile = (file) => {
    setFileError('')
    if (!file) return
    if (!ACCEPTED.includes(file.type)) {
      setFileError('Format non supporté. Utilisez JPG, PNG ou WebP.')
      return
    }
    if (file.size > MAX_SIZE) {
      setFileError('Le fichier dépasse 10 Mo.')
      return
    }
    setImageFile(file)
    const reader = new FileReader()
    reader.onload = (e) => setImagePreview(e.target.result)
    reader.readAsDataURL(file)
  }

  const handleDrop = (e) => {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files?.[0]
    if (file) handleFile(file)
  }

  const handleDragOver = (e) => {
    e.preventDefault()
    setDragging(true)
  }

  const handleDragLeave = () => setDragging(false)

  const handleFileInput = (e) => {
    handleFile(e.target.files?.[0])
  }

  // --- Step 2: Form handling ---
  const handleFormChange = (e) => {
    const { name, value } = e.target
    setForm((prev) => ({ ...prev, [name]: value }))
    setFormErrors((prev) => ({ ...prev, [name]: '' }))
  }

  const validateForm = () => {
    const errors = {}
    if (!form.name.trim()) errors.name = 'Le nom du produit est requis.'
    return errors
  }

  // --- Step navigation ---
  const goToStep2 = () => {
    if (!imageFile) {
      setFileError('Veuillez ajouter une image produit.')
      return
    }
    setStep(2)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const goToStep3 = () => {
    const errors = validateForm()
    if (Object.keys(errors).length) {
      setFormErrors(errors)
      return
    }
    setStep(3)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  // --- Final submit ---
  const handleSubmit = async () => {
    setSubmitting(true)
    setSubmitError('')
    try {
      // 1. Create product with multipart form data
      const formData = new FormData()
      formData.append('image', imageFile)
      formData.append('name', form.name)
      if (form.brand) formData.append('brand', form.brand)
      formData.append('category', form.category)

      const productRes = await products.create(formData)
      const productData = productRes.data
      const productId = productData.id || productData.product_id

      // 2. Create generation
      const genRes = await generations.create(productId, {
        tone: form.tone,
        template: form.template,
      })
      const genData = genRes.data
      const genId = genData.id || genData.generation_id

      // 3. Navigate to polling page
      navigate(`/generations/${genId}`)
    } catch (err) {
      const msg =
        err.response?.data?.detail ||
        err.response?.data?.message ||
        err.message ||
        "Une erreur est survenue lors du lancement de la génération."
      setSubmitError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="page-container">
      <div className="page-content animate-enter">
        <div className="upload-container">
          {/* Page title */}
          <div style={{ textAlign: 'center', marginBottom: '32px' }}>
            <h1 className="section-title">
              Nouveau <span>Produit</span>
            </h1>
            <p className="section-subtitle">
              Importez votre photo et configurez votre affiche en 3 étapes
            </p>
          </div>

          {/* Step indicator */}
          <StepIndicator current={step} />

          {/* ==================== STEP 1 ==================== */}
          {step === 1 && (
            <div className="glass-card" style={{ padding: '32px' }}>
              <h2 className="upload-section-header">
                📷 Image du produit
              </h2>

              {!imagePreview ? (
                <div
                  className={`dropzone${dragging ? ' dragging' : ''}`}
                  onDrop={handleDrop}
                  onDragOver={handleDragOver}
                  onDragLeave={handleDragLeave}
                  onClick={() => fileInputRef.current?.click()}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) =>
                    e.key === 'Enter' && fileInputRef.current?.click()
                  }
                >
                  <span className="dropzone-icon">📤</span>
                  <div className="dropzone-title">
                    Glissez votre image ici
                  </div>
                  <div className="dropzone-subtitle">
                    ou cliquez pour parcourir vos fichiers
                  </div>
                  <div className="dropzone-formats">
                    Formats acceptés : JPG, PNG, WebP — Max 10 Mo
                  </div>
                </div>
              ) : (
                <div>
                  <div className="dropzone-preview">
                    <img
                      src={imagePreview}
                      alt="Aperçu produit"
                      style={{ maxHeight: '320px', objectFit: 'contain', width: '100%' }}
                    />
                    <div className="dropzone-preview-overlay">
                      <button
                        className="btn btn-secondary"
                        onClick={() => fileInputRef.current?.click()}
                      >
                        🔄 Changer l'image
                      </button>
                    </div>
                  </div>
                  <div
                    style={{
                      marginTop: '12px',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '10px',
                      color: 'var(--success)',
                      fontSize: '0.85rem',
                    }}
                  >
                    <span>✅</span>
                    <span>
                      {imageFile.name} ({(imageFile.size / 1024 / 1024).toFixed(1)} Mo)
                    </span>
                    <button
                      className="btn btn-ghost btn-sm"
                      onClick={() => {
                        setImageFile(null)
                        setImagePreview(null)
                      }}
                      style={{ marginLeft: 'auto', color: 'var(--error)' }}
                    >
                      ✕ Supprimer
                    </button>
                  </div>
                </div>
              )}

              <input
                ref={fileInputRef}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                style={{ display: 'none' }}
                onChange={handleFileInput}
              />

              {fileError && (
                <div className="form-error" style={{ marginTop: '12px' }}>
                  ⚠️ {fileError}
                </div>
              )}

              <div className="upload-nav">
                <button
                  className="btn btn-ghost"
                  onClick={() => navigate('/dashboard')}
                >
                  ← Annuler
                </button>
                <button className="btn btn-primary" onClick={goToStep2}>
                  <span>Continuer →</span>
                </button>
              </div>
            </div>
          )}

          {/* ==================== STEP 2 ==================== */}
          {step === 2 && (
            <div className="glass-card" style={{ padding: '32px' }}>
              <h2 className="upload-section-header">
                📝 Informations du produit
              </h2>

              <div className="upload-form">
                <div className="upload-grid">
                  {/* Name */}
                  <div
                    className="form-group"
                    style={{ gridColumn: '1 / -1' }}
                  >
                    <label className="form-label" htmlFor="prod-name">
                      Nom du produit *
                    </label>
                    <input
                      id="prod-name"
                      name="name"
                      type="text"
                      className={`input-field${formErrors.name ? ' error' : ''}`}
                      placeholder="ex : Sérum Éclat Vitamine C"
                      value={form.name}
                      onChange={handleFormChange}
                    />
                    {formErrors.name && (
                      <span className="form-error">{formErrors.name}</span>
                    )}
                  </div>

                  {/* Brand */}
                  <div className="form-group">
                    <label className="form-label" htmlFor="prod-brand">
                      Marque (optionnel)
                    </label>
                    <input
                      id="prod-brand"
                      name="brand"
                      type="text"
                      className="input-field"
                      placeholder="ex : L'Oréal, Lancôme…"
                      value={form.brand}
                      onChange={handleFormChange}
                    />
                  </div>

                  {/* Category */}
                  <div className="form-group">
                    <label className="form-label" htmlFor="prod-category">
                      Catégorie
                    </label>
                    <select
                      id="prod-category"
                      name="category"
                      className="select-field"
                      value={form.category}
                      onChange={handleFormChange}
                    >
                      {CATEGORIES.map((c) => (
                        <option key={c.value} value={c.value}>
                          {c.label}
                        </option>
                      ))}
                    </select>
                  </div>

                  {/* Tone */}
                  <div className="form-group">
                    <label className="form-label" htmlFor="prod-tone">
                      Tonalité
                    </label>
                    <select
                      id="prod-tone"
                      name="tone"
                      className="select-field"
                      value={form.tone}
                      onChange={handleFormChange}
                    >
                      {TONES.map((t) => (
                        <option key={t.value} value={t.value}>
                          {t.label}
                        </option>
                      ))}
                    </select>
                  </div>

                  {/* Template */}
                  <div
                    className="form-group"
                    style={{ gridColumn: '1 / -1' }}
                  >
                    <label className="form-label">Template</label>
                    <div
                      style={{
                        display: 'flex',
                        gap: '10px',
                        flexWrap: 'wrap',
                      }}
                    >
                      {TEMPLATES.map((t) => (
                        <label
                          key={t.value}
                          style={{
                            flex: 1,
                            minWidth: '120px',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            gap: '6px',
                            padding: '12px',
                            background:
                              form.template === t.value
                                ? 'var(--gold-dim)'
                                : 'rgba(255,255,255,0.03)',
                            border: `1px solid ${
                              form.template === t.value
                                ? 'rgba(212,160,85,0.4)'
                                : 'var(--border-subtle)'
                            }`,
                            borderRadius: 'var(--radius-md)',
                            cursor: 'pointer',
                            transition: 'all 0.2s',
                            fontSize: '0.9rem',
                            fontWeight: form.template === t.value ? 600 : 400,
                            color:
                              form.template === t.value
                                ? 'var(--gold-bright)'
                                : 'var(--text-secondary)',
                          }}
                        >
                          <input
                            type="radio"
                            name="template"
                            value={t.value}
                            checked={form.template === t.value}
                            onChange={handleFormChange}
                            style={{ display: 'none' }}
                          />
                          {t.label}
                        </label>
                      ))}
                    </div>
                  </div>
                </div>
              </div>

              <div className="upload-nav">
                <button className="btn btn-secondary" onClick={() => setStep(1)}>
                  ← Retour
                </button>
                <button className="btn btn-primary" onClick={goToStep3}>
                  <span>Continuer →</span>
                </button>
              </div>
            </div>
          )}

          {/* ==================== STEP 3 ==================== */}
          {step === 3 && (
            <div className="glass-card" style={{ padding: '32px' }}>
              <h2 className="upload-section-header">
                🚀 Confirmation & Lancement
              </h2>

              {/* Summary */}
              <div className="confirm-preview glass-card" style={{ padding: '20px', marginBottom: '24px' }}>
                <div className="confirm-image">
                  {imagePreview && (
                    <img
                      src={imagePreview}
                      alt="Produit"
                      style={{ borderRadius: 'var(--radius-md)' }}
                    />
                  )}
                </div>
                <div className="confirm-details">
                  <div className="confirm-row">
                    <span className="confirm-row-label">Produit</span>
                    <span className="confirm-row-value">{form.name}</span>
                  </div>
                  {form.brand && (
                    <div className="confirm-row">
                      <span className="confirm-row-label">Marque</span>
                      <span className="confirm-row-value">{form.brand}</span>
                    </div>
                  )}
                  <div className="confirm-row">
                    <span className="confirm-row-label">Catégorie</span>
                    <span className="confirm-row-value">
                      {CATEGORIES.find((c) => c.value === form.category)?.label}
                    </span>
                  </div>
                  <div className="confirm-row">
                    <span className="confirm-row-label">Tonalité</span>
                    <span className="confirm-row-value">
                      {TONES.find((t) => t.value === form.tone)?.label}
                    </span>
                  </div>
                  <div className="confirm-row">
                    <span className="confirm-row-label">Template</span>
                    <span className="confirm-row-value">
                      {TEMPLATES.find((t) => t.value === form.template)?.label}
                    </span>
                  </div>
                  <div className="confirm-row">
                    <span className="confirm-row-label">Image</span>
                    <span className="confirm-row-value" style={{ color: 'var(--success)' }}>
                      ✅ {imageFile?.name}
                    </span>
                  </div>
                </div>
              </div>

              {/* Info box */}
              <div
                style={{
                  background: 'var(--gold-dim)',
                  border: '1px solid rgba(212,160,85,0.25)',
                  borderRadius: 'var(--radius-md)',
                  padding: '16px 20px',
                  marginBottom: '24px',
                  fontSize: '0.87rem',
                  color: 'var(--text-secondary)',
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: '10px',
                }}
              >
                <span style={{ fontSize: '1.1rem' }}>⏱️</span>
                <span>
                  La génération prend environ{' '}
                  <strong style={{ color: 'var(--gold)' }}>60 à 90 secondes</strong>.
                  Vous serez redirigé automatiquement pour suivre la progression.
                </span>
              </div>

              {/* Error */}
              {submitError && (
                <div className="auth-error" style={{ marginBottom: '20px' }}>
                  <span>⚠️</span>
                  {submitError}
                </div>
              )}

              <div className="upload-nav">
                <button
                  className="btn btn-secondary"
                  onClick={() => setStep(2)}
                  disabled={submitting}
                >
                  ← Retour
                </button>
                <button
                  className={`btn btn-primary btn-primary--large glow${submitting ? ' btn-loading' : ''}`}
                  onClick={handleSubmit}
                  disabled={submitting}
                >
                  <span>{submitting ? '' : '✨ Lancer la génération'}</span>
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default Upload
