from pydantic import BaseModel


class RasterPreviewBoundsRead(BaseModel):
    west: float
    south: float
    east: float
    north: float
    native_crs: str
    reprojection_applied: bool
    nodata_present: bool
    # Present only for DEM/DSM datasets (the heightmap variant) -- None
    # for a plain colorized preview's bounds.
    elevation_min_m: float | None = None
    elevation_max_m: float | None = None
    pixel_size_x_m: float | None = None
    pixel_size_y_m: float | None = None
