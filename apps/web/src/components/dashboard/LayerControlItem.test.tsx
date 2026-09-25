import { afterEach, describe, expect, it } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { LayerControlItem } from './LayerControlItem'
import { useDashboardStore } from '../../state/dashboardStore'
import type { LayerState } from '../../state/dashboardStore'

const baseLayer: LayerState = {
  layerId: 'l1',
  datasetId: 'd1',
  datasetType: 'flood_inundation',
  category: 'hazard',
  visible: false,
  opacity: 0.8,
  zIndex: 0,
}

afterEach(() => {
  useDashboardStore.setState({ layers: {} })
})

describe('LayerControlItem', () => {
  it('toggling the checkbox updates the store visibility for that layer', () => {
    useDashboardStore.setState({ layers: { l1: baseLayer } })
    render(<LayerControlItem layer={baseLayer} hasCrs={true} hasError={false} legendLabel="inundated" />)

    fireEvent.click(screen.getByRole('checkbox'))

    expect(useDashboardStore.getState().layers.l1.visible).toBe(true)
  })

  it('shows a "no crs" badge when the dataset has no CRS', () => {
    render(<LayerControlItem layer={baseLayer} hasCrs={false} hasError={false} />)
    expect(screen.getByText('no crs')).toBeInTheDocument()
  })

  it('does not show the CRS badge when the dataset has a CRS', () => {
    render(<LayerControlItem layer={baseLayer} hasCrs={true} hasError={false} />)
    expect(screen.queryByText('no crs')).not.toBeInTheDocument()
  })

  it('shows an opacity slider only when the layer is visible', () => {
    const { rerender } = render(<LayerControlItem layer={{ ...baseLayer, visible: false }} hasCrs={true} hasError={false} />)
    expect(screen.queryByRole('slider')).not.toBeInTheDocument()

    rerender(<LayerControlItem layer={{ ...baseLayer, visible: true }} hasCrs={true} hasError={false} />)
    expect(screen.getByRole('slider')).toBeInTheDocument()
  })
})
