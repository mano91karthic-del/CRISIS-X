"""Replace synthetic rectangular buildings with realistic same-position buildings."""

import sqlite3, json, uuid, shutil, hashlib
from datetime import datetime, timezone
from pathlib import Path
import geopandas as gpd

# Config
PROJECT_ID = '006bc42f-757a-4e76-a09c-1f82ede26250'
TWIN_ID = '1ecb1ae7-66c1-4b77-a46a-36cffbdb2cea'
STORAGE_ROOT = Path('data/storage')
SOURCE_FILE = Path(r'C:\Users\manoj karthikeyan\Downloads\CRISIS_X_Chennai_Realistic_Same_Position_Buildings.geojson')

conn = sqlite3.connect('data/dev/crisisx.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Read new buildings with correct CRS
new_gdf = gpd.read_file(SOURCE_FILE)
new_gdf = new_gdf.set_crs('EPSG:32644', allow_override=True)

# Create new dataset
dataset_id = str(uuid.uuid4())
now = datetime.now(timezone.utc)

# Copy file to storage
dest_dir = STORAGE_ROOT / PROJECT_ID / dataset_id
dest_dir.mkdir(parents=True, exist_ok=True)
dest_path = dest_dir / 'CRISIS_X_Chennai_Realistic_Same_Position_Buildings.geojson'
shutil.copy2(SOURCE_FILE, dest_path)

# Get file info
file_size = dest_path.stat().st_size
checksum = hashlib.sha256(dest_path.read_bytes()).hexdigest()
minx, miny, maxx, maxy = new_gdf.total_bounds

# Insert dataset record
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
        'CRISIS-X Realistic Same-Position Synthetic Buildings',
        'buildings',
        'uploaded',
        'CRISIS_X_Chennai_Realistic_Same_Position_Buildings.geojson',
        str(dest_path.relative_to(Path('.'))),
        'geojson',
        'EPSG:32644',
        float(minx), float(miny), float(maxx), float(maxy),
        file_size,
        checksum,
        'validated',
        None,
        json.dumps({
            'feature_count': len(new_gdf),
            'geometry_types': ['Polygon'],
            'has_height_m': True,
            'has_building_color': True,
            'has_wall_color': True,
            'has_roof_color': True,
            'has_building_material': True,
        }),
        json.dumps({
            'source': 'CRISIS-X Chennai Coherent Synthetic Test Dataset',
            'scientific_status': 'synthetic_test_data',
            'description': 'Synthetic realistic same-position buildings for visualization testing. Not authoritative real-world observations.',
            'geometry_source': 'synthetic_realistic_same_position',
        }),
        now,
        now,
    )
)

# Update twin layer to point to new dataset
cursor.execute('SELECT id, version FROM digital_twins WHERE id = ?', (TWIN_ID,))
twin = cursor.fetchone()
new_version = twin['version'] + 1

# Find existing buildings layer
cursor.execute('SELECT id FROM twin_layers WHERE twin_id = ? AND dataset_type = ? AND status = ?', (TWIN_ID, 'buildings', 'active'))
old_layer = cursor.fetchone()

if old_layer:
    # Supersede old layer
    cursor.execute('UPDATE twin_layers SET status = ? WHERE id = ?', ('superseded', old_layer['id']))

# Create new layer
layer_id = str(uuid.uuid4())
cursor.execute('''INSERT INTO twin_layers (
    id, twin_id, dataset_id, dataset_type, category,
    status, registered_at_version, provenance, created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
    (
        layer_id,
        TWIN_ID,
        dataset_id,
        'buildings',
        'terrain',
        'active',
        new_version,
        json.dumps({'registered_at_version': new_version, 'superseded_layer_id': old_layer['id'] if old_layer else None}),
        now,
        now,
    )
)

# Update twin version
cursor.execute('UPDATE digital_twins SET version = ? WHERE id = ?', (new_version, TWIN_ID))

conn.commit()

print('=== Dataset Replacement Complete ===')
print(f'New dataset ID: {dataset_id}')
print(f'New layer ID: {layer_id}')
print(f'Old layer ID: {old_layer["id"] if old_layer else "None"}')
print(f'New twin version: {new_version}')
print(f'Feature count: {len(new_gdf)}')
print(f'CRS: EPSG:32644')
print(f'Bounds: ({minx:.2f}, {miny:.2f}) - ({maxx:.2f}, {maxy:.2f})')

conn.close()
