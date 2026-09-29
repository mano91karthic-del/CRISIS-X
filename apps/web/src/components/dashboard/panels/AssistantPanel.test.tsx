import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { AssistantPanel } from './AssistantPanel'
import { useDashboardStore } from '../../../state/dashboardStore'
import { queryAssistant } from '../../../lib/api/assistant'

vi.mock('../../../lib/api/assistant', () => ({
  queryAssistant: vi.fn(),
}))

afterEach(() => {
  vi.clearAllMocks()
  useDashboardStore.setState({ projectId: null, twinId: null, scenarioId: null, activeFeature: null })
})

async function askQuestion(text: string) {
  fireEvent.change(screen.getByLabelText('Ask about this project'), { target: { value: text } })
  fireEvent.click(screen.getByRole('button', { name: /ask/i }))
}

describe('AssistantPanel', () => {
  it('shows an empty state when no project is selected', () => {
    render(<AssistantPanel />)
    expect(screen.getByText('No project selected')).toBeInTheDocument()
  })

  it('renders the question form when a project is selected', () => {
    useDashboardStore.setState({ projectId: 'p1' })
    render(<AssistantPanel />)
    expect(screen.getByLabelText('Ask about this project')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /ask/i })).toBeInTheDocument()
  })

  it('submits a question and shows a loading state', async () => {
    useDashboardStore.setState({ projectId: 'p1' })
    vi.mocked(queryAssistant).mockImplementation(() => new Promise(() => {})) // never resolves -- loading stays visible

    render(<AssistantPanel />)
    await askQuestion('What is the risk?')

    expect(screen.getByText(/gathering evidence/i)).toBeInTheDocument()
    expect(queryAssistant).toHaveBeenCalledWith('p1', expect.objectContaining({ question: 'What is the risk?', project_id: 'p1' }))
  })

  it('displays the conversational answer as the dominant section', async () => {
    useDashboardStore.setState({ projectId: 'p1' })
    vi.mocked(queryAssistant).mockResolvedValue({
      answer: "The highest risk class in the current analysis is 'high' (risk score 0.54).",
      evidence: [],
      unavailable: [],
      relevant_limitations: [],
    })

    render(<AssistantPanel />)
    await askQuestion('Which areas have the highest risk?')

    await waitFor(() =>
      expect(screen.getByText("The highest risk class in the current analysis is 'high' (risk score 0.54).")).toBeInTheDocument(),
    )
  })

  it('displays relevant limitations in their own section, separate from the answer', async () => {
    useDashboardStore.setState({ projectId: 'p1' })
    vi.mocked(queryAssistant).mockResolvedValue({
      answer: 'The risk classes are summarized above.',
      evidence: [],
      unavailable: [],
      relevant_limitations: ['risk_score must never be read as an aggregate figure.'],
    })

    render(<AssistantPanel />)
    await askQuestion('What is the risk?')

    await waitFor(() => expect(screen.getByText('Relevant limitations')).toBeInTheDocument())
    expect(screen.getByText('risk_score must never be read as an aggregate figure.')).toBeInTheDocument()
  })

  it('does not render a limitations section when none are relevant', async () => {
    useDashboardStore.setState({ projectId: 'p1' })
    vi.mocked(queryAssistant).mockResolvedValue({
      answer: "Dataset 'roads.geojson' is a roads dataset, currently validated.",
      evidence: [],
      unavailable: [],
      relevant_limitations: [],
    })

    render(<AssistantPanel />)
    await askQuestion('Tell me about this dataset')

    await waitFor(() => expect(screen.getByText(/roads.geojson/)).toBeInTheDocument())
    expect(screen.queryByText('Relevant limitations')).not.toBeInTheDocument()
  })

  it('shows evidence/sources as a secondary, collapsible section with tool name and status', async () => {
    useDashboardStore.setState({ projectId: 'p1' })
    vi.mocked(queryAssistant).mockResolvedValue({
      answer: 'See evidence for details.',
      evidence: [
        {
          tool: 'get_risk_analysis',
          status: 'success',
          data: {},
          limitations: ['risk_score must never be read as an aggregate figure.'],
          provenance: null,
          detail: null,
        },
      ],
      unavailable: [],
      relevant_limitations: [],
    })

    render(<AssistantPanel />)
    await askQuestion('What is the risk?')

    await waitFor(() => expect(screen.getByText('get_risk_analysis')).toBeInTheDocument())
    expect(screen.getByText('success')).toBeInTheDocument()
    expect(screen.getByText(/Evidence \/ sources/)).toBeInTheDocument()
  })

  it('displays unavailable information distinctly from the answer', async () => {
    useDashboardStore.setState({ projectId: 'p1' })
    vi.mocked(queryAssistant).mockResolvedValue({
      answer: "I don't currently have a risk analysis available for this project.",
      evidence: [],
      unavailable: ['a risk analysis to rank'],
      relevant_limitations: [],
    })

    render(<AssistantPanel />)
    await askQuestion('What is the highest risk area?')

    await waitFor(() => expect(screen.getByText('Unavailable information')).toBeInTheDocument())
    expect(screen.getByText('a risk analysis to rank')).toBeInTheDocument()
  })

  it('shows an error banner when the request fails', async () => {
    useDashboardStore.setState({ projectId: 'p1' })
    vi.mocked(queryAssistant).mockRejectedValue(new Error('backend unreachable'))

    render(<AssistantPanel />)
    await askQuestion('What is the risk?')

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('backend unreachable'))
  })
})
