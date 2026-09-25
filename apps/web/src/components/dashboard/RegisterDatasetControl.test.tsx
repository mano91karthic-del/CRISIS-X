import { describe, expect, it, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { RegisterDatasetControl } from './RegisterDatasetControl'
import { registerTwinLayer } from '../../lib/api/digitalTwin'
import type { Dataset } from '../../types/dataHub'

vi.mock('../../lib/api/digitalTwin', () => ({
  registerTwinLayer: vi.fn(),
}))

function makeDataset(overrides: Partial<Dataset> = {}): Dataset {
  return {
    id: 'd1',
    project_id: 'p1',
    name: 'output_SRTMGL1.tif',
    dataset_type: 'dem',
    source_filename: 'output_SRTMGL1.tif',
    file_format: 'GeoTIFF',
    crs: 'EPSG:4326',
    bbox_min_x: 0,
    bbox_min_y: 0,
    bbox_max_x: 1,
    bbox_max_y: 1,
    file_size_bytes: 1000,
    checksum_sha256: 'abc',
    status: 'validated',
    validation_message: null,
    metadata_json: {},
    provenance: {},
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('RegisterDatasetControl', () => {
  it('lists only validated, not-yet-registered datasets as candidates', () => {
    const datasets = [
      makeDataset({ id: 'd1', name: 'new-dem', status: 'validated' }),
      makeDataset({ id: 'd2', name: 'already-registered-dem', status: 'validated' }),
      makeDataset({ id: 'd3', name: 'unvalidated', status: 'uploaded' }),
    ]
    render(
      <RegisterDatasetControl
        twinId="twin1"
        datasets={datasets}
        activeLayers={[{ datasetId: 'd2', datasetType: 'dem' }]}
        onRegistered={vi.fn()}
      />,
    )

    expect(screen.getByRole('option', { name: /new-dem/ })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /already-registered-dem/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /unvalidated/ })).not.toBeInTheDocument()
  })

  it('renders nothing when there are no registerable candidates', () => {
    const datasets = [makeDataset({ id: 'd1', status: 'validated' })]
    const { container } = render(
      <RegisterDatasetControl
        twinId="twin1"
        datasets={datasets}
        activeLayers={[{ datasetId: 'd1', datasetType: 'dem' }]}
        onRegistered={vi.fn()}
      />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('warns that registering will supersede the active layer of the same dataset_type', () => {
    const datasets = [makeDataset({ id: 'd1', name: 'new-dem', dataset_type: 'dem', status: 'validated' })]
    render(
      <RegisterDatasetControl
        twinId="twin1"
        datasets={datasets}
        activeLayers={[{ datasetId: 'old-dem-id', datasetType: 'dem' }]}
        onRegistered={vi.fn()}
      />,
    )

    fireEvent.change(screen.getByLabelText('Select dataset to register'), { target: { value: 'd1' } })
    expect(screen.getByText(/will supersede the current active "dem" layer/)).toBeInTheDocument()
  })

  it('registers the selected dataset and calls onRegistered on success', async () => {
    vi.mocked(registerTwinLayer).mockResolvedValue({} as never)
    const onRegistered = vi.fn()
    const datasets = [makeDataset({ id: 'd1', name: 'new-dem', status: 'validated' })]
    render(<RegisterDatasetControl twinId="twin1" datasets={datasets} activeLayers={[]} onRegistered={onRegistered} />)

    fireEvent.change(screen.getByLabelText('Select dataset to register'), { target: { value: 'd1' } })
    fireEvent.click(screen.getByRole('button', { name: /register/i }))

    await waitFor(() => expect(onRegistered).toHaveBeenCalled())
    expect(registerTwinLayer).toHaveBeenCalledWith('twin1', 'd1')
  })

  it('shows an error message and does not call onRegistered when registration fails', async () => {
    vi.mocked(registerTwinLayer).mockRejectedValue(new Error('boom'))
    const onRegistered = vi.fn()
    const datasets = [makeDataset({ id: 'd1', name: 'new-dem', status: 'validated' })]
    render(<RegisterDatasetControl twinId="twin1" datasets={datasets} activeLayers={[]} onRegistered={onRegistered} />)

    fireEvent.change(screen.getByLabelText('Select dataset to register'), { target: { value: 'd1' } })
    fireEvent.click(screen.getByRole('button', { name: /register/i }))

    await waitFor(() => expect(screen.getByText('boom')).toBeInTheDocument())
    expect(onRegistered).not.toHaveBeenCalled()
  })
})
