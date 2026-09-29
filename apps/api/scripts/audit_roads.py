"""Audit road datasets and create polygon road surfaces if needed."""

import sqlite3, json
from pathlib import Path
import geopandas as gpd
from shapely.geometry import LineString, Polygon, MultiPolygon
from shapely.ops import unary_union

conn = sqlite3.connect('data/dev/crisisx.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Find all road-related datasets
cursor.execute('SELECT id, name, dataset_type, crs, status, bbox_min_x, bbox_min_y, bbox_max_x, bbox_max_y, metadata_json, storage_path FROM datasets WHERE project_id = ? AND (dataset_type LIKE ? OR name LIKE ?) ORDER BY created_at', ('006bc42f-757a-4e76-a09c-1f82ede26250', '%road%', '%road%'))
rows = cursor.fetchall()
print('=== Road-related datasets ===')
for r in rows:
    meta = json.loads(r['metadata_json']) if r['metadata_json'] else {}
    print(f'ID: {r["id"]}')
    print(f'  Name: {r["name"]}')
    print(f'  Type: {r["dataset_type"]}')
    print(f'  CRS: {r["crs"]}')
    print(f'  Status: {r["status"]}')
    print(f'  Storage: {r["storage_path"]}')
    print(f'  Bbox: ({r["bbox_min_x"]:.2f}, {r["bbox_min_y"]:.2f}) - ({r["bbox_max_x"]:.2f}, {r["bbox_max_y"]:.2f})')
    print(f'  Metadata: {json.dumps(meta, indent=4)}')
    print()

# Find road-related twin layers
cursor.execute('SELECT tl.id, tl.twin_id, tl.dataset_id, tl.dataset_type, tl.category, tl.status, d.name as dataset_name FROM twin_layers tl JOIN datasets d ON tl.dataset_id = d.id WHERE tl.twin_id = ? AND (tl.dataset_type LIKE ? OR d.name LIKE ?)', ('1ecb1ae7-66c1-4b77-a46a-36cffbdb2cea', '%road%', '%road%'))
layers = cursor.fetchall()
print('=== Road-related twin layers ===')
for l in layers:
    print(f'Layer ID: {l["id"]}')
    print(f'  Dataset ID: {l["dataset_id"]}')
    print(f'  Dataset name: {l["dataset_name"]}')
    print(f'  Type: {l["dataset_type"]}')
    print(f'  Category: {l["category"]}')
    print(f'  Status: {l["status"]}')
    print()

# Check if polygon road surface dataset exists
cursor.execute('SELECT id FROM datasets WHERE project_id = ? AND name LIKE ?', ('006bc42f-757a-4e76-a09c-1f82ede26250', '%surface%'))
surface = cursor.fetchone()
if surface:
    print(f'Polygon road surface dataset EXISTS: {surface["id"]}')
else:
    print('NO polygon road surface dataset found - need to create one')

conn.close()
