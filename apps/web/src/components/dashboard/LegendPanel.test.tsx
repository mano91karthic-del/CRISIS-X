import { afterEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { LegendPanel } from './LegendPanel'
import { useDashboardStore } from '../../state/dashboardStore'

afterEach(() => {
  useDashboardStore.setState({ layers: {} })
})

describe('LegendPanel', () => {
  it('shows a placeholder when no layer is visible', () => {
    render(<LegendPanel />)
    expect(screen.getByText(/No layers currently visible/)).toBeInTheDocument()
  })

  it('renders a fixed-color swatch for a visible roads/route layer', () => {
    useDashboardStore.setState({
      layers: {
        a: { layerId: 'a', datasetId: 'd1', datasetType: 'roads', category: 'observation', visible: true, opacity: 0.8, zIndex: 0 },
        b: {
          layerId: 'b',
          datasetId: 'd2',
          datasetType: 'route_hazard_aware',
          category: 'route',
          visible: true,
          opacity: 0.8,
          zIndex: 0,
        },
        c: { layerId: 'c', datasetId: 'd3', datasetType: 'shelters', category: 'observation', visible: false, opacity: 0.8, zIndex: 0 },
      },
    })
    render(<LegendPanel />)
    expect(screen.getByText('roads')).toBeInTheDocument()
    expect(screen.getByText('hazard-aware route')).toBeInTheDocument()
    expect(screen.queryByText('shelters')).not.toBeInTheDocument()
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
