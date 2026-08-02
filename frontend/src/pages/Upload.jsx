import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowRight,
  ImageSquare,
  ShieldCheck,
  UploadSimple,
  X,
} from '@phosphor-icons/react'
import { generations, products } from '../api/client.js'
import { createIdempotencyKey } from '../utils/idempotency.js'

const ACCEPTED_TYPES = ['image/jpeg', 'image/png', 'image/webp']
const MAX_FILE_SIZE = 10 * 1024 * 1024
const MAX_SEED = 4_294_967_295

const CATEGORIES = [
  ['soin_visage', 'Soin du visage'],
  ['soin_corps', 'Soin du corps'],
  ['cheveux', 'Soin des cheveux'],
  ['maquillage', 'Maquillage'],
  ['solaire', 'Protection solaire'],
  ['hygiene', 'Hygiène'],
]

const initialForm = {
  name: '',
  brand: '',
  category: 'soin_visage',
  language: 'fr',
  seed: '42',
  audience: '',
  benefits: '',
  ingredients: '',
  verifiedClaims: '',
  cta: '',
  creativeDirection: '',
}

const lines = (value) =>
  value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean)

const optional = (value) => {
  const trimmed = value.trim()
  return trimmed || undefined
}

function Upload() {
  const navigate = useNavigate()
  const fileRef = useRef(null)
  const uploadedProductRef = useRef(null)
  const idempotencyRef = useRef({ fingerprint: null, key: null })
  const [image, setImage] = useState(null)
  const [imageRevision, setImageRevision] = useState(0)
  const [preview, setPreview] = useState(null)
  const [form, setForm] = useState(initialForm)
  const [errors, setErrors] = useState({})
  const [submitError, setSubmitError] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [dragging, setDragging] = useState(false)

  useEffect(() => {
    if (!image) {
      setPreview(null)
      return undefined
    }
    const objectUrl = URL.createObjectURL(image)
    setPreview(objectUrl)
    return () => URL.revokeObjectURL(objectUrl)
  }, [image])

  const campaignPayload = useMemo(
    () => ({
      language: form.language,
      seed: Number(form.seed),
      ...(optional(form.audience) ? { audience: optional(form.audience) } : {}),
      ...(lines(form.benefits).length
        ? { benefits: lines(form.benefits) }
        : {}),
      ...(lines(form.ingredients).length
        ? { ingredients: lines(form.ingredients) }
        : {}),
      ...(lines(form.verifiedClaims).length
        ? { verified_claims: lines(form.verifiedClaims) }
        : {}),
      ...(optional(form.cta) ? { cta: optional(form.cta) } : {}),
      ...(optional(form.creativeDirection)
        ? { creative_direction: optional(form.creativeDirection) }
        : {}),
    }),
    [form]
  )
  const productFingerprint = useMemo(
    () =>
      JSON.stringify({
        imageRevision,
        name: form.name.trim(),
        brand: form.brand.trim(),
        category: form.category,
      }),
    [form.brand, form.category, form.name, imageRevision]
  )
  const generationFingerprint = useMemo(
    () => JSON.stringify({ productFingerprint, campaignPayload }),
    [campaignPayload, productFingerprint]
  )
  const evidenceLanguage = form.language === 'en' ? 'anglais' : 'français'

  const updateField = (event) => {
    const { name, value } = event.target
    setForm((current) => ({ ...current, [name]: value }))
    setErrors((current) => ({ ...current, [name]: null }))
  }

  const acceptFile = (file) => {
    setErrors((current) => ({ ...current, image: null }))
    if (!file) return
    setImageRevision((revision) => revision + 1)
    if (!ACCEPTED_TYPES.includes(file.type)) {
      setImage(null)
      setErrors((current) => ({
        ...current,
        image: 'Utilisez une image JPG, PNG ou WebP.',
      }))
      return
    }
    if (file.size > MAX_FILE_SIZE) {
      setImage(null)
      setErrors((current) => ({
        ...current,
        image: 'La photo ne doit pas dépasser 10 Mo.',
      }))
      return
    }
    setImage(file)
  }

  const validate = () => {
    const next = {}
    if (!image) next.image = 'Ajoutez une photo du produit.'
    if (!form.name.trim()) next.name = 'Le nom du produit est requis.'
    const seed = Number(form.seed)
    if (!Number.isInteger(seed) || seed < 0 || seed > MAX_SEED) {
      next.seed = 'Utilisez un nombre entier entre 0 et 4294967295.'
    }
    setErrors(next)
    return Object.keys(next).length === 0
  }

  const launchGeneration = async (
    productId,
    payload = campaignPayload,
    fingerprint = generationFingerprint
  ) => {
    if (idempotencyRef.current.fingerprint !== fingerprint) {
      idempotencyRef.current = {
        fingerprint,
        key: createIdempotencyKey(),
      }
    }
    const response = await generations.create(productId, payload, {
      idempotencyKey: idempotencyRef.current.key,
    })
    const generationId = response.data.id || response.data.generation_id
    navigate(`/generations/${generationId}`)
  }

  const submit = async (event) => {
    event?.preventDefault()
    if (!validate()) return
    setSubmitting(true)
    setSubmitError(null)

    try {
      const submittedProductFingerprint = productFingerprint
      const submittedGenerationFingerprint = generationFingerprint
      const submittedPayload = campaignPayload
      let uploadedProduct = uploadedProductRef.current
      if (
        !uploadedProduct ||
        uploadedProduct.fingerprint !== submittedProductFingerprint
      ) {
        const body = new FormData()
        body.set('image', image)
        body.set('name', form.name.trim())
        body.set('category', form.category)
        if (form.brand.trim()) body.set('brand', form.brand.trim())
        const response = await products.create(body)
        uploadedProduct = {
          id: response.data.id || response.data.product_id,
          fingerprint: submittedProductFingerprint,
        }
        uploadedProductRef.current = uploadedProduct
      }
      await launchGeneration(
        uploadedProduct.id,
        submittedPayload,
        submittedGenerationFingerprint
      )
    } catch (error) {
      setSubmitError(error)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="workspace-page workspace-page--wide">
      <header className="page-heading page-heading--editorial">
        <div>
          <p className="eyebrow">Nouvelle campagne</p>
          <h1>Donnez au produit toute la scène.</h1>
        </div>
        <p>
          Le produit reste intact. Les décors, la composition et le texte sont
          produits à partir de vos informations vérifiées.
        </p>
      </header>

      <form className="campaign-form" onSubmit={submit} noValidate>
        <section className="studio-panel studio-panel--upload">
          <div className="studio-panel__heading">
            <ImageSquare size={24} weight="light" aria-hidden="true" />
            <div>
              <h2>Photo du produit</h2>
              <p>Un produit net, entier et bien éclairé donne le meilleur masque.</p>
            </div>
          </div>

          <div
            className="drop-studio"
            data-dragging={dragging}
            onDragEnter={(event) => {
              event.preventDefault()
              setDragging(true)
            }}
            onDragOver={(event) => event.preventDefault()}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault()
              setDragging(false)
              acceptFile(event.dataTransfer.files?.[0])
            }}
          >
            {preview ? (
              <div className="drop-studio__preview">
                <img src={preview} alt="Aperçu du produit importé" />
                <button
                  type="button"
                  className="icon-button drop-studio__remove"
                  aria-label="Retirer la photo"
                  onClick={() => {
                    setImage(null)
                    setImageRevision((revision) => revision + 1)
                    if (fileRef.current) fileRef.current.value = ''
                  }}
                >
                  <X size={18} aria-hidden="true" />
                </button>
              </div>
            ) : (
              <div className="drop-studio__empty">
                <UploadSimple size={32} weight="light" aria-hidden="true" />
                <strong>Déposez votre photo ici</strong>
                <span>JPG, PNG ou WebP. 10 Mo maximum.</span>
              </div>
            )}
            <label className="button button--secondary" htmlFor="product-image">
              {image ? 'Changer la photo' : 'Choisir une photo'}
            </label>
            <input
              ref={fileRef}
              id="product-image"
              className="visually-hidden"
              type="file"
              aria-label="Photo du produit"
              accept={ACCEPTED_TYPES.join(',')}
              aria-describedby={errors.image ? 'product-image-error' : undefined}
              aria-invalid={Boolean(errors.image)}
              onChange={(event) => acceptFile(event.target.files?.[0])}
            />
          </div>
          {errors.image && (
            <p id="product-image-error" className="field-error">
              {errors.image}
            </p>
          )}
        </section>

        <div className="campaign-form__fields">
          <section className="studio-panel">
            <div className="studio-panel__heading">
              <ShieldCheck size={24} weight="light" aria-hidden="true" />
              <div>
                <h2>Fiche produit</h2>
                <p>Ces informations identifient le produit et encadrent le texte.</p>
              </div>
            </div>
            <p
              className="language-guidance"
              id="evidence-language-guidance"
              role="status"
            >
              Saisissez ces éléments en {evidenceLanguage}. Aucune traduction
              automatique n’est appliquée.
            </p>

            <div className="form-grid">
              <div className="field field--span-2">
                <label htmlFor="product-name">Nom du produit</label>
                <input
                  id="product-name"
                  name="name"
                  value={form.name}
                  onChange={updateField}
                  required
                  maxLength={120}
                  aria-invalid={Boolean(errors.name)}
                  aria-describedby={errors.name ? 'product-name-error' : undefined}
                />
                {errors.name && (
                  <span id="product-name-error" className="field-error">
                    {errors.name}
                  </span>
                )}
              </div>
              <div className="field">
                <label htmlFor="product-brand">Marque (optionnel)</label>
                <input
                  id="product-brand"
                  name="brand"
                  value={form.brand}
                  onChange={updateField}
                  maxLength={100}
                />
              </div>
              <div className="field">
                <label htmlFor="product-category">Catégorie</label>
                <select
                  id="product-category"
                  name="category"
                  value={form.category}
                  onChange={updateField}
                  required
                >
                  {CATEGORIES.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="campaign-language">Langue de la campagne</label>
                <select
                  id="campaign-language"
                  name="language"
                  value={form.language}
                  onChange={updateField}
                  required
                >
                  <option value="fr">Français</option>
                  <option value="en">Anglais</option>
                </select>
              </div>
              <div className="field">
                <label htmlFor="campaign-seed">Graine créative</label>
                <input
                  id="campaign-seed"
                  name="seed"
                  type="number"
                  min="0"
                  max="4294967295"
                  step="1"
                  value={form.seed}
                  onChange={updateField}
                  aria-invalid={Boolean(errors.seed)}
                  aria-describedby={errors.seed ? 'seed-error' : 'seed-help'}
                />
                <span id="seed-help" className="field-help">
                  Gardez la même valeur pour reproduire la direction visuelle.
                </span>
                {errors.seed && (
                  <span id="seed-error" className="field-error">
                    {errors.seed}
                  </span>
                )}
              </div>
            </div>
          </section>

          <section className="studio-panel">
            <div className="studio-panel__heading">
              <div>
                <h2>Brief vérifié</h2>
                <p>
                  Le texte publicitaire ne peut reformuler que les faits fournis
                  ici.
                </p>
              </div>
            </div>

            <div className="form-grid">
              <div className="field field--span-2">
                <label htmlFor="campaign-audience">Audience (optionnel)</label>
                <input
                  id="campaign-audience"
                  name="audience"
                  value={form.audience}
                  onChange={updateField}
                  maxLength={300}
                />
              </div>
              <div className="field">
                <label htmlFor="campaign-benefits">Bénéfices vérifiés</label>
                <textarea
                  id="campaign-benefits"
                  name="benefits"
                  rows="4"
                  value={form.benefits}
                  onChange={updateField}
                  aria-describedby="evidence-language-guidance"
                  placeholder="Un bénéfice par ligne"
                  maxLength={1000}
                />
              </div>
              <div className="field">
                <label htmlFor="campaign-ingredients">Ingrédients vérifiés</label>
                <textarea
                  id="campaign-ingredients"
                  name="ingredients"
                  rows="4"
                  value={form.ingredients}
                  onChange={updateField}
                  aria-describedby="evidence-language-guidance"
                  placeholder="Un ingrédient par ligne"
                  maxLength={1000}
                />
              </div>
              <div className="field field--span-2">
                <label htmlFor="campaign-claims">Allégations vérifiées</label>
                <textarea
                  id="campaign-claims"
                  name="verifiedClaims"
                  rows="3"
                  value={form.verifiedClaims}
                  onChange={updateField}
                  aria-describedby="evidence-language-guidance"
                  placeholder="Uniquement des affirmations que vous pouvez justifier"
                  maxLength={1200}
                />
              </div>
              <div className="field">
                <label htmlFor="campaign-cta">Appel à l’action (optionnel)</label>
                <input
                  id="campaign-cta"
                  name="cta"
                  value={form.cta}
                  onChange={updateField}
                  aria-describedby="evidence-language-guidance"
                  maxLength={80}
                />
              </div>
              <div className="field">
                <label htmlFor="campaign-direction">
                  Direction créative (optionnel)
                </label>
                <input
                  id="campaign-direction"
                  name="creativeDirection"
                  value={form.creativeDirection}
                  onChange={updateField}
                  maxLength={500}
                />
              </div>
            </div>
          </section>
          {submitError && (
            <div className="inline-alert inline-alert--error campaign-form__error" role="alert">
              <div>
                <strong>La campagne n’a pas été lancée.</strong>
                <p>{submitError.message}</p>
                <p>Vérifiez le brief ou le studio IA, puis utilisez le bouton de lancement unique ci-dessous.</p>
              </div>
            </div>
          )}
        </div>

        <footer className="campaign-form__footer">
          <p>
            Aucun bénéfice, ingrédient ou label ne sera ajouté sans preuve
            fournie.
          </p>
          <button
            type="submit"
            className="button button--primary button--large"
            disabled={submitting}
          >
            <span>{submitting ? 'Lancement en cours' : 'Lancer la campagne'}</span>
            {!submitting && <ArrowRight size={18} aria-hidden="true" />}
          </button>
        </footer>
      </form>
    </div>
  )
}

export default Upload
