import { describe, expect, it } from 'vitest'
import { colorForLabel, LEGEND_COLORS, ORDINAL_SEVERITY_ORDER, UNKNOWN_LABEL_COLOR } from './legendColors'

describe('legendColors', () => {
  it('covers every ordinal severity label used across hazard/risk panels', () => {
    for (const label of ORDINAL_SEVERITY_ORDER) {
      expect(LEGEND_COLORS[label]).toBeDefined()
    }
  })

  it('covers the flood and EO-change label vocabularies', () => {
    for (const label of ['inundated', 'changed', 'no_change']) {
      expect(LEGEND_COLORS[label]).toBeDefined()
    }
  })

  it('colorForLabel returns the exact table color for a known label', () => {
    expect(colorForLabel('very_high')).toBe(LEGEND_COLORS.very_high)
  })

  it('colorForLabel never returns undefined for an unrecognized label -- falls back, never crashes', () => {
    expect(colorForLabel('totally-made-up-label')).toBe(UNKNOWN_LABEL_COLOR)
  })

  it('every color is a valid #RRGGBB hex string', () => {
    for (const color of Object.values(LEGEND_COLORS)) {
      expect(color).toMatch(/^#[0-9a-f]{6}$/i)
    }
  })
})
