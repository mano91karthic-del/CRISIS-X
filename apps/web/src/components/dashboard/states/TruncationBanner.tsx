export function TruncationBanner({ shown, total }: { shown: number; total: number }) {
  return (
    <div
      className="rounded border border-amber-900 bg-amber-950/50 px-2 py-1 text-xs text-amber-300"
      role="status"
      aria-live="polite"
    >
      Showing {shown.toLocaleString()} of {total.toLocaleString()} features (truncated for display).
    </div>
  )
}
