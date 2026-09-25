import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { LoadingState } from './LoadingState'
import { EmptyState } from './EmptyState'
import { ErrorBanner } from './ErrorBanner'
import { MissingLayerNotice } from './MissingLayerNotice'
import { TruncationBanner } from './TruncationBanner'

describe('state atoms', () => {
  it('LoadingState announces its label via a live region', () => {
    render(<LoadingState label="Loading twin…" />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading twin…')
  })

  it('EmptyState renders title, description, and an optional action', () => {
    render(<EmptyState title="Nothing here" description="Do something" action={<button>Go</button>} />)
    expect(screen.getByText('Nothing here')).toBeInTheDocument()
    expect(screen.getByText('Do something')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Go' })).toBeInTheDocument()
  })

  it('ErrorBanner shows the message and fires onRetry', () => {
    const onRetry = vi.fn()
    render(<ErrorBanner message="Failed to load" onRetry={onRetry} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Failed to load')
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('ErrorBanner renders without a retry button when none is given', () => {
    render(<ErrorBanner message="Failed" />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('MissingLayerNotice flags a category as not registered', () => {
    render(<MissingLayerNotice label="hazard" />)
    expect(screen.getByText('hazard')).toBeInTheDocument()
    expect(screen.getByText('not registered')).toBeInTheDocument()
  })

  it('TruncationBanner reports shown vs total feature counts', () => {
    render(<TruncationBanner shown={100} total={5000} />)
    expect(screen.getByRole('status')).toHaveTextContent('100')
    expect(screen.getByRole('status')).toHaveTextContent('5,000')
  })
})
