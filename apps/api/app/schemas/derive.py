from typing import Literal

from pydantic import BaseModel

DerivableProduct = Literal["slope", "aspect", "flow_direction", "flow_accumulation"]


class DeriveRequest(BaseModel):
    product: DerivableProduct
