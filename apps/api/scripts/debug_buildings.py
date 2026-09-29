"""Debug building visibility in 3D scene."""

import sqlite3, json
from pathlib import Path
import geopandas as gpd

conn = sqlite3.connect('data/dev/crisisx.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Check active building layer
cursor.execute('SELECT * FROM twin_layers WHERE id = ?', ('3f2308ec-8f9c-4f90-afb7-e16539c4eb5f',))
layer = cursor.fetchone()
print('=== Active Building Layer ===')
print(f'ID: {layer["id"]}')
print(f'Twin ID: {layer["twin_id"]}')
print(f'Dataset ID: {layer["dataset_id"]}')
print(f'Dataset type: {layer["dataset_type"]}')
print(f'Category: {layer["category"]}')
print(f'Status: {layer["status"]}')
print()

# Check dataset
cursor.execute('SELECT * FROM datasets WHERE id = ?', ('04b04d93-5694-41bf-99d1-24116dff9a5d',))
ds = cursor.fetchone()
print('=== Active Building Dataset ===')
print(f'ID: {ds["id"]}')
print(f'Name: {ds["name"]}')
print(f'Type: {ds["dataset_type"]}')
print(f'CRS: {ds["crs"]}')
print(f'Status: {ds["status"]}')
print(f'Storage: {ds["storage_path"]}')
print(f'Bbox: ({ds["bbox_min_x"]:.2f}, {ds["bbox_min_y"]:.2f}) - ({ds["bbox_max_x"]:.2f}, {ds["bbox_max_y"]:.2f})')
meta = json.loads(ds['metadata_json'])
print(f'Metadata: {json.dumps(meta, indent=2)}')
print()

# Check actual file
storage_path = Path(ds['storage_path'])
if storage_path.exists():
    gdf = gpd.read_file(storage_path)
    print('=== Actual File Contents ===')
    print(f'Feature count: {len(gdf)}')
    print(f'CRS: {gdf.crs}')
    print(f'Geometry types: {gdf.geom_type.value_counts().to_dict()}')
    print(f'Bounds: {gdf.total_bounds}')
    print(f'Columns: {list(gdf.columns)}')
    if 'building_color' in gdf.columns:
        print(f'building_color sample: {gdf["building_color"].iloc[0]}')
    if 'height_m' in gdf.columns:
        print(f'height_m range: {gdf["height_m"].min()} - {gdf["height_m"].max()}')
else:
    print(f'ERROR: File not found: {storage_path}')

conn.close()
