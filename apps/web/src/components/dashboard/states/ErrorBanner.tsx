export function ErrorBanner({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      className="flex items-center justify-between gap-3 rounded-md border border-red-900 bg-red-950/60 px-3 py-2 text-sm text-red-300"
      role="alert"
      aria-live="polite"
    >
      <span>{message}</span>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 rounded border border-red-800 px-2 py-1 text-xs hover:bg-red-900"
        >
          Retry
        </button>
      )}
    </div>
  )
}
