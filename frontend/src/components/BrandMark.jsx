export default function BrandMark({ compact = false }) {
  return (
    <span className={`brand-lockup${compact ? ' brand-lockup--compact' : ''}`}>
      <svg className="brand-lockup__mark" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
        <rect x="3.5" y="3.5" width="25" height="25" rx="5" fill="none" stroke="currentColor" strokeWidth="2" />
        <path d="M10 10h12v12H10z" fill="currentColor" opacity=".16" />
        <path d="M12 20.5 15.25 12h2.5L21 20.5h-2.25l-.68-2h-3.64l-.68 2H12Zm3.04-3.75h2.1l-1.05-3.1-1.05 3.1Z" fill="currentColor" />
      </svg>
      {!compact && <span className="brand-lockup__name">Cosmetique AI</span>}
    </span>
  )
}
