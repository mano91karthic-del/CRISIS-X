import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { HazardInfoPanel } from './HazardInfoPanel'
import { ExposureInfoPanel } from './ExposureInfoPanel'
import { RiskInfoPanel } from './RiskInfoPanel'
import { RouteInfoPanel } from './RouteInfoPanel'
import type { HazardScenario } from '../../../types/hazard'
import type { ExposureAnalysis } from '../../../types/exposure'
import type { RiskAnalysis } from '../../../types/risk'
import type { RouteAnalysis } from '../../../types/routing'

// Regression tests asserting the disclosure discipline (limitations/
// assumptions text) isn't silently dropped by a future refactor -- see
// docs/architecture/0012-phase-11-command-dashboard.md.

describe('info panel empty states', () => {
  it('HazardInfoPanel shows an empty state when there is no hazard scenario', () => {
    render(<HazardInfoPanel hazard={null} />)
    expect(screen.getByText('No hazard scenario yet')).toBeInTheDocument()
  })

  it('ExposureInfoPanel shows an empty state when there is no exposure analysis', () => {
    render(<ExposureInfoPanel exposure={null} />)
    expect(screen.getByText('No exposure analysis yet')).toBeInTheDocument()
  })

  it('RiskInfoPanel shows an empty state when there is no risk analysis', () => {
    render(<RiskInfoPanel risk={null} />)
    expect(screen.getByText('No risk analysis yet')).toBeInTheDocument()
  })

  it('RouteInfoPanel shows an empty state when there is no route analysis', () => {
    render(<RouteInfoPanel route={null} />)
    expect(screen.getByText('No route analysis yet')).toBeInTheDocument()
  })
})

describe('info panels always surface limitations when present', () => {
  it('HazardInfoPanel renders every limitation passed to it', () => {
    const hazard: HazardScenario = {
      id: 'h1', project_id: 'p1', hazard_type: 'landslide', name: 'Test', description: null,
      input_datasets: { dem: 'd1' }, parameters: { slope_breakpoints_deg: [5, 15, 25, 35] },
      rainfall_dataset_id: null, scenario_id: null, status: 'completed', error_message: null, created_at: '',
    }
    render(<HazardInfoPanel hazard={hazard} limitations={['This is a susceptibility screening, not a prediction.']} />)
    expect(screen.getByText('This is a susceptibility screening, not a prediction.')).toBeInTheDocument()
  })

  it('ExposureInfoPanel renders limitations embedded in results', () => {
    const exposure: ExposureAnalysis = {
      id: 'e1', project_id: 'p1', name: 'Test', description: null, hazard_dataset_id: 'd1', exposure_dataset_id: 'd2',
      hazard_dataset_type: 'flood_inundation', exposure_dataset_type: 'roads', population_field: null, scenario_id: null,
      parameters: {}, results: { by_class: {}, limitations: ['Exposure does not imply confirmed damage.'] },
      status: 'completed', error_message: null, created_at: '',
    }
    render(<ExposureInfoPanel exposure={exposure} />)
    expect(screen.getByText('Exposure does not imply confirmed damage.')).toBeInTheDocument()
  })

  it('RiskInfoPanel labels vulnerability/consequence as user-declared assumptions, and renders limitations', () => {
    const risk: RiskAnalysis = {
      id: 'r1', project_id: 'p1', name: 'Test', description: null, exposure_analysis_id: 'e1',
      hazard_dataset_id: 'd1', exposure_dataset_id: 'd2', hazard_dataset_type: 'flood_inundation',
      exposure_dataset_type: 'roads', vulnerability_weight: 0.5, consequence_weight: 0.7, risk_breakpoints: [0.2, 0.4, 0.6, 0.8],
      scenario_id: null, parameters: {},
      results: {
        by_class: { inundated: { hazard_class_code: 1, hazard_intensity_weight: 1, risk_score: 0.35, risk_class: 'moderate', count: 3 } },
        limitations: ['risk_score is NOT weighted, scaled, or normalized by exposure quantity.'],
      },
      status: 'completed', error_message: null, created_at: '',
    }
    render(<RiskInfoPanel risk={risk} />)
    expect(screen.getByText(/User-declared assumptions/)).toBeInTheDocument()
    expect(screen.getByText('risk_score is NOT weighted, scaled, or normalized by exposure quantity.')).toBeInTheDocument()
  })

  it('RouteInfoPanel shows infeasible status prominently and renders limitations', () => {
    const route: RouteAnalysis = {
      id: 'ro1', project_id: 'p1', name: 'Test', description: null, road_dataset_id: 'd1', hazard_dataset_id: 'd2',
      hazard_dataset_type: 'flood_inundation', risk_analysis_id: null, origin_lon: 0, origin_lat: 0,
      destination_lon: 1, destination_lat: 1, hazard_penalty_weight: 1, block_threshold: 1, node_snap_tolerance_m: 1,
      max_snap_distance_m: 100, scenario_id: null, parameters: {},
      results: {
        shortest_route: { feasible: false },
        hazard_aware_route: { feasible: false },
        limitations: ['This is NOT a guarantee of physical safety.'],
      },
      status: 'completed', error_message: null, created_at: '',
    }
    render(<RouteInfoPanel route={route} />)
    expect(screen.getAllByText('infeasible')).toHaveLength(2) // both shortest and hazard-aware
    expect(screen.getByText('This is NOT a guarantee of physical safety.')).toBeInTheDocument()
  })
})
