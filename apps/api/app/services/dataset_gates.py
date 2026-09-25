"""Shared eligibility checks for using a Dataset as a computation source
(terrain derivation, hazard modeling). Centralizes the vertical_reference
gate so it's enforced identically everywhere a TERRAIN-X import might be
used as an elevation source — Phase 2's /derive and Phase 4's hazard
scenario endpoints both call this rather than duplicating the check.
"""

from app.models.dataset import Dataset, DatasetOrigin


class SourceNotEligible(ValueError):
    """Raised when a dataset fails an eligibility gate for use as a
    computation source. Callers translate this into an HTTP 400."""


def ensure_metric_calibrated_if_terrain_x_import(dataset: Dataset) -> None:
    """No-op for any dataset whose origin isn't a TERRAIN-X import. For a
    TERRAIN-X import, raises SourceNotEligible unless its declared
    vertical_reference is exactly 'metric_calibrated' — refusing to treat
    relative or unknown-reference depth data as real-world elevation for any
    computation (slope/aspect/flow derivation, flood/landslide modeling).
    """
    if dataset.origin != DatasetOrigin.TERRAIN_X_IMPORT.value:
        return

    vertical_reference = (dataset.provenance or {}).get("vertical_reference")
    if vertical_reference != "metric_calibrated":
        raise SourceNotEligible(
            f"Source dataset is a TERRAIN-X import with vertical_reference="
            f"'{vertical_reference}'. Only 'metric_calibrated' elevation data can be "
            "used for terrain derivation or hazard modeling — refusing to treat "
            "relative or unknown-reference depth data as real-world elevation."
        )
