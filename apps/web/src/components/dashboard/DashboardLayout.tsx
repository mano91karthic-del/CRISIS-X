import type { ReactNode } from 'react'

export interface DashboardLayoutProps {
  topBar: ReactNode
  layerControl: ReactNode
  viewport: ReactNode
  infoPanel: ReactNode
  legend: ReactNode
}

/** Command-center layout: viewport dominates, layer control on the left
 * (source before content), info panels on the right, legend as a
 * persistent bottom strip. Desktop-first (see ADR 0012 §22) --
 * responsive collapsing below 1280px is a follow-up, not required for
 * this phase's functional completeness.
 */
export function DashboardLayout({ topBar, layerControl, viewport, infoPanel, legend }: DashboardLayoutProps) {
  return (
    <div className="grid h-[calc(100vh-73px)] grid-rows-[auto_1fr_auto] overflow-hidden">
      <div>{topBar}</div>
      <div className="grid grid-cols-[280px_1fr_360px] overflow-hidden">
        <div className="overflow-hidden border-r border-slate-800 bg-slate-950">{layerControl}</div>
        <div className="relative overflow-hidden bg-slate-900">{viewport}</div>
        <div className="overflow-hidden border-l border-slate-800 bg-slate-950">{infoPanel}</div>
      </div>
      <div className="border-t border-slate-800 bg-slate-950">{legend}</div>
    </div>
  )
}
