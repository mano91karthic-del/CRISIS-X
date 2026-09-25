// Phase 11: the single shared label -> color table for the Command
// Dashboard. MUST stay byte-for-byte in sync with the backend's
// app/services/preview.py::LEGEND_COLORS -- this is what guarantees a
// 2D map layer, a 3D drape texture, and the backend's own colorized
// preview.png can never silently disagree about what a color means.
// There is no automated cross-language sync; keep both tables in step
// by hand (see docs/architecture/0012-phase-11-command-dashboard.md).
export const LEGEND_COLORS: Record<string, string> = {
  very_low: '#2c7bb6',
  low: '#abd9e9',
  moderate: '#ffffbf',
  high: '#fdae61',
  very_high: '#d7191c',
  inundated: '#2b8cbe',
  changed: '#e6550d',
  no_change: '#bdbdbd',
}

export const UNKNOWN_LABEL_COLOR = '#9e9e9e'

/** Never returns undefined -- an unrecognized label renders with the
 * fixed fallback color rather than crashing or silently omitting the
 * layer, consistent with this project's "never silently drop, always
 * flag" fail-closed UI convention.
 */
export function colorForLabel(label: string): string {
  return LEGEND_COLORS[label] ?? UNKNOWN_LABEL_COLOR
}

/** Ordinal severity legends (landslide susceptibility, risk class) share
 * this exact 5-step order everywhere in the app -- one vocabulary, not a
 * parallel one per panel.
 */
export const ORDINAL_SEVERITY_ORDER = ['very_low', 'low', 'moderate', 'high', 'very_high'] as const
