/** A recommended-but-absent layer category, or a layer whose Dataset is
 * null -- shown as a disabled row, never simply omitted from the list,
 * so the operator always knows what's absent, not just what's present.
 */
export function MissingLayerNotice({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-between rounded border border-dashed border-slate-800 px-2 py-1.5 text-xs text-slate-500">
      <span>{label}</span>
      <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] uppercase tracking-wide">not registered</span>
    </div>
  )
}
