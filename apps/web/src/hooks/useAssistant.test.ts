import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useAssistant } from './useAssistant'
import { queryAssistant } from '../lib/api/assistant'

vi.mock('../lib/api/assistant', () => ({
  queryAssistant: vi.fn(),
}))

afterEach(() => {
  vi.clearAllMocks()
})

describe('useAssistant', () => {
  it('starts with no answer and not loading', () => {
    const { result } = renderHook(() => useAssistant({ projectId: 'p1' }))
    expect(result.current.answer).toBeNull()
    expect(result.current.evidence).toEqual([])
    expect(result.current.loading).toBe(false)
  })

  it('tracks loading, then populates answer/evidence/unavailable/relevantLimitations on success', async () => {
    vi.mocked(queryAssistant).mockResolvedValue({
      answer: 'The risk is high in class 5.',
      evidence: [{ tool: 'get_risk_analysis', status: 'success', data: {}, limitations: ['a limitation'], provenance: null, detail: null }],
      unavailable: [],
      relevant_limitations: ['a limitation'],
    })

    const { result } = renderHook(() => useAssistant({ projectId: 'p1', twinId: 't1' }))

    act(() => {
      void result.current.ask('What is the risk?')
    })
    expect(result.current.loading).toBe(true)

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.answer).toBe('The risk is high in class 5.')
    expect(result.current.evidence).toHaveLength(1)
    expect(result.current.unavailable).toEqual([])
    expect(result.current.relevantLimitations).toEqual(['a limitation'])
    expect(queryAssistant).toHaveBeenCalledWith('p1', {
      question: 'What is the risk?',
      project_id: 'p1',
      twin_id: 't1',
      scenario_id: null,
      active_feature: null,
    })
  })

  it('tracks unavailable information from a successful response', async () => {
    vi.mocked(queryAssistant).mockResolvedValue({
      answer: "I don't currently have that information for this project.",
      evidence: [],
      unavailable: ['no risk analysis found'],
      relevant_limitations: [],
    })

    const { result } = renderHook(() => useAssistant({ projectId: 'p1' }))
    await act(async () => {
      await result.current.ask('highest risk?')
    })

    expect(result.current.unavailable).toEqual(['no risk analysis found'])
  })

  it('exposes an error and does not set an answer when the request fails', async () => {
    vi.mocked(queryAssistant).mockRejectedValue(new Error('network down'))

    const { result } = renderHook(() => useAssistant({ projectId: 'p1' }))
    await act(async () => {
      await result.current.ask('question')
    })

    expect(result.current.error).toBe('network down')
    expect(result.current.answer).toBeNull()
  })

  it('does not submit when no project is selected', async () => {
    const { result } = renderHook(() => useAssistant({ projectId: null }))
    await act(async () => {
      await result.current.ask('question')
    })

    expect(queryAssistant).not.toHaveBeenCalled()
    expect(result.current.error).toBe('No project selected')
  })

  it('prevents duplicate submissions while a request is already in flight', async () => {
    let resolveFirst: (value: { answer: string; evidence: never[]; unavailable: never[]; relevant_limitations: never[] }) => void =
      () => {}
    vi.mocked(queryAssistant).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve
        }),
    )

    const { result } = renderHook(() => useAssistant({ projectId: 'p1' }))

    act(() => {
      void result.current.ask('first')
    })
    expect(result.current.loading).toBe(true)

    act(() => {
      void result.current.ask('second')
    })

    expect(queryAssistant).toHaveBeenCalledTimes(1)

    await act(async () => {
      resolveFirst({ answer: 'done', evidence: [], unavailable: [], relevant_limitations: [] })
    })
  })
})
