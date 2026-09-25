import { useState, type ReactNode } from 'react'

export interface InfoPanelTab {
  id: string
  label: string
  content: ReactNode
}

export function InfoPanelDock({ tabs }: { tabs: InfoPanelTab[] }) {
  const [activeTabId, setActiveTabId] = useState(tabs[0]?.id)
  const activeTab = tabs.find((t) => t.id === activeTabId) ?? tabs[0]

  return (
    <div className="flex h-full flex-col">
      <div role="tablist" className="flex flex-wrap border-b border-slate-800">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={tab.id === activeTab?.id}
            onClick={() => setActiveTabId(tab.id)}
            className={`px-3 py-2 text-xs font-medium ${
              tab.id === activeTab?.id ? 'border-b-2 border-emerald-500 text-emerald-400' : 'text-slate-500 hover:text-slate-300'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto" role="tabpanel">
        {activeTab?.content}
      </div>
    </div>
  )
}
