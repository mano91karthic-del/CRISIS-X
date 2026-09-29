"""Create polygon road surface dataset from existing road centerlines."""

import sqlite3, json, uuid, hashlib, shutil
from datetime import datetime, timezone
from pathlib import Path
import geopandas as gpd
from shapely.geometry import LineString, Polygon, MultiPolygon
from shapely.ops import unary_union, transform
from shapely import wkt

# Config
PROJECT_ID = '006bc42f-757a-4e76-a09c-1f82ede26250'
TWIN_ID = '1ecb1ae7-66c1-4b77-a46a-36cffbdb2cea'
STORAGE_ROOT = Path('data/storage')
SOURCE_DATASET_ID = '04321452-def4-4160-aec6-bd95b4e570fe'

# Road width mapping (visualization only)
ROAD_WIDTHS = {
    'arterial': 12,
    'primary': 10,
    'secondary': 7,
    'residential': 5,
    'service': 3.5,
}

conn = sqlite3.connect('data/dev/crisisx.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Read source road centerlines
cursor.execute('SELECT storage_path FROM datasets WHERE id = ?', (SOURCE_DATASET_ID,))
source_path = Path(cursor.fetchone()['storage_path'])
print(f'Source: {source_path}')

roads_gdf = gpd.read_file(source_path)
print(f'Source roads: {len(roads_gdf)}')
print(f'Source CRS: {roads_gdf.crs}')
print(f'Source geometry types: {roads_gdf.geom_type.value_counts().to_dict()}')
print(f'Road types: {roads_gdf["road_type"].value_counts().to_dict()}')

# Create polygon road surfaces by buffering centerlines
surface_features = []
for idx, row in roads_gdf.iterrows():
    geom = row.geometry
    road_type = row.get('road_type', 'residential')
    width = ROAD_WIDTHS.get(road_type, 5)

    # Buffer the centerline to create a road surface polygon
    if geom.geom_type == 'LineString':
        surface = geom.buffer(width / 2, cap_style=2, join_style=2)  # flat caps, round joins
    elif geom.geom_type == 'MultiLineString':
        surface = geom.buffer(width / 2, cap_style=2, join_style=2)
    else:
        continue

    # Ensure valid geometry
    if not surface.is_valid:
        surface = surface.buffer(0)

    surface_features.append({
        'road_id': row.get('road_id', f'road_{idx}'),
        'road_type': road_type,
        'width_m': width,
        'source_centerline_id': row.get('road_id', f'road_{idx}'),
        'lanes': row.get('lanes', 2),
        'speed_kmh': row.get('speed_kmh', 40),
        'surface': row.get('surface', 'asphalt'),
        'geometry': surface,
    })

# Create GeoDataFrame
surface_gdf = gpd.GeoDataFrame(surface_features, crs=roads_gdf.crs)
print(f'\nCreated {len(surface_gdf)} road surface polygons')
print(f'Geometry types: {surface_gdf.geom_type.value_counts().to_dict()}')
print(f'Bounds: {surface_gdf.total_bounds}')

# Verify all geometries are valid
invalid = surface_gdf[~surface_gdf.is_valid]
if len(invalid) > 0:
    print(f'WARNING: {len(invalid)} invalid geometries found')
    surface_gdf['geometry'] = surface_gdf.geometry.apply(lambda g: g.buffer(0) if not g.is_valid else g)

# Save to storage
dataset_id = str(uuid.uuid4())
dest_dir = STORAGE_ROOT / PROJECT_ID / dataset_id
dest_dir.mkdir(parents=True, exist_ok=True)
dest_path = dest_dir / 'road_surfaces.geojson'
surface_gdf.to_file(dest_path, driver='GeoJSON')
print(f'\nSaved to: {dest_path}')

# Create dataset record
now = datetime.now(timezone.utc)
file_size = dest_path.stat().st_size
checksum = hashlib.sha256(dest_path.read_bytes()).hexdigest()
minx, miny, maxx, maxy = surface_gdf.total_bounds

cursor.execute('''INSERT INTO datasets (
    id, project_id, name, dataset_type, origin, source_filename,
    storage_path, file_format, crs,
    bbox_min_x, bbox_min_y, bbox_max_x, bbox_max_y,
    file_size_bytes, checksum_sha256, status, validation_message,
    metadata_json, provenance, created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
    (
        dataset_id,
        PROJECT_ID,
        'Synthetic Road Surfaces (Polygon Visualization)',
        'roads',
        'crisisx_derived',
        'road_surfaces.geojson',
        str(dest_path.relative_to(Path('.'))),
        'geojson',
        'EPSG:32644',
        float(minx), float(miny), float(maxx), float(maxy),
        file_size,
        checksum,
        'validated',
        None,
        json.dumps({
            'feature_count': len(surface_gdf),
            'geometry_types': ['Polygon', 'MultiPolygon'],
            'source_dataset_id': SOURCE_DATASET_ID,
            'width_mapping': ROAD_WIDTHS,
        }),
        json.dumps({
            'source': 'CRISIS-X Chennai Coherent Synthetic Test Dataset',
            'scientific_status': 'synthetic_test_data',
            'description': 'Polygon road surfaces derived from centerlines for 3D visualization. Not measured road widths.',
            'derived_from': SOURCE_DATASET_ID,
        }),
        now,
        now,
    )
)

# Register as visual road layer in Digital Twin
cursor.execute('SELECT version FROM digital_twins WHERE id = ?', (TWIN_ID,))
twin = cursor.fetchone()
new_version = twin['version'] + 1

# Supersede old road layer
cursor.execute('SELECT id FROM twin_layers WHERE twin_id = ? AND dataset_type = ? AND status = ?', (TWIN_ID, 'roads', 'active'))
old_layer = cursor.fetchone()
if old_layer:
    cursor.execute('UPDATE twin_layers SET status = ? WHERE id = ?', ('superseded', old_layer['id']))

# Create new road surface layer
layer_id = str(uuid.uuid4())
cursor.execute('''INSERT INTO twin_layers (
    id, twin_id, dataset_id, dataset_type, category,
    status, registered_at_version, provenance, created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
    (
        layer_id,
        TWIN_ID,
        dataset_id,
        'roads',
        'observation',
        'active',
        new_version,
        json.dumps({'registered_at_version': new_version, 'superseded_layer_id': old_layer['id'] if old_layer else None, 'visualization_layer': True}),
        now,
        now,
    )
)

cursor.execute('UPDATE digital_twins SET version = ? WHERE id = ?', (new_version, TWIN_ID))
conn.commit()

print('\n=== Road Surface Dataset Created ===')
print(f'New dataset ID: {dataset_id}')
print(f'New layer ID: {layer_id}')
print(f'Old layer ID: {old_layer["id"] if old_layer else "None"} (superseded)')
print(f'New twin version: {new_version}')
print(f'Feature count: {len(surface_gdf)}')
print(f'CRS: EPSG:32644')
print(f'Geometry types: Polygon/MultiPolygon')

conn.close()
