from typing import Literal

from pydantic import BaseModel

DerivableProduct = Literal["slope", "aspect", "flow_direction", "flow_accumulation"]


class DeriveRequest(BaseModel):
    product: DerivableProduct


class ClipRequest(BaseModel):
    """Clips the target dataset (a vector dataset) to a registered
    STUDY_AREA dataset's polygon -- see app/services/clip.py.
    """

    aoi_dataset_id: str
