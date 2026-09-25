// Pure bounds-union helpers -- no React, no fetch. Consumes the
// backend's WGS84 preview-bounds responses (never a Dataset/twin's own
// possibly-non-WGS84 bbox_* columns, and never `DigitalTwinState.extent`,
// which Phase 9 never reprojects -- see MapView's usage).

export interface LonLatBounds {
  west: number
  south: number
  east: number
  north: number
}

export function isValidBounds(b: LonLatBounds | null | undefined): b is LonLatBounds {
  if (!b) return false
  const { west, south, east, north } = b
  return (
    Number.isFinite(west) &&
    Number.isFinite(south) &&
    Number.isFinite(east) &&
    Number.isFinite(north) &&
    west <= east &&
    south <= north
  )
}

/** Unions a list of WGS84 bounds. Invalid/missing entries are skipped,
 * never allowed to poison the union with NaN/Infinity. Returns null if
 * nothing valid was given -- callers must treat that as "no fitBounds
 * target," never fall back to a guessed extent.
 */
export function unionBounds(boundsList: Array<LonLatBounds | null | undefined>): LonLatBounds | null {
  const valid = boundsList.filter(isValidBounds)
  if (valid.length === 0) return null

  return valid.reduce((acc, b) => ({
    west: Math.min(acc.west, b.west),
    south: Math.min(acc.south, b.south),
    east: Math.max(acc.east, b.east),
    north: Math.max(acc.north, b.north),
  }))
}

/** MapLibre's `fitBounds` expects [[west, south], [east, north]]. */
export function toMapLibreBounds(b: LonLatBounds): [[number, number], [number, number]] {
  return [
    [b.west, b.south],
    [b.east, b.north],
  ]
}

/** MapLibre's `image` source expects four corners, clockwise from the
 * top-left: [[nw],[ne],[se],[sw]].
 */
export function toImageSourceCoordinates(
  b: LonLatBounds,
): [[number, number], [number, number], [number, number], [number, number]] {
  return [
    [b.west, b.north],
    [b.east, b.north],
    [b.east, b.south],
    [b.west, b.south],
  ]
}
