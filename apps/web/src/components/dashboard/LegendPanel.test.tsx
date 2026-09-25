import { afterEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { LegendPanel } from './LegendPanel'
import { useDashboardStore } from '../../state/dashboardStore'

afterEach(() => {
  useDashboardStore.setState({ layers: {} })
})

describe('LegendPanel', () => {
  it('shows a placeholder when no classified layer is visible', () => {
    render(<LegendPanel />)
    expect(screen.getByText(/No classified layers currently visible/)).toBeInTheDocument()
  })

  it('renders a legend row for a visible classified layer, not a hidden one', () => {
    useDashboardStore.setState({
      layers: {
        a: { layerId: 'a', datasetId: 'd1', datasetType: 'landslide_susceptibility', category: 'hazard', visible: true, opacity: 0.8, zIndex: 0 },
        b: { layerId: 'b', datasetId: 'd2', datasetType: 'risk_classification', category: 'risk', visible: false, opacity: 0.8, zIndex: 0 },
      },
    })
    render(<LegendPanel />)
    expect(screen.getByText('landslide_susceptibility:')).toBeInTheDocument()
    expect(screen.queryByText('risk_classification:')).not.toBeInTheDocument()
  })

  it('shows the ordinal severity labels for a landslide legend', () => {
    useDashboardStore.setState({
      layers: {
        a: { layerId: 'a', datasetId: 'd1', datasetType: 'landslide_susceptibility', category: 'hazard', visible: true, opacity: 0.8, zIndex: 0 },
      },
    })
    render(<LegendPanel />)
    for (const label of ['very_low', 'low', 'moderate', 'high', 'very_high']) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
  })
})
