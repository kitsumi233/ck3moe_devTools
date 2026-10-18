"""Select native blink contacts from geometry without character-specific IDs.

The radii below describe which native contacts can be measured; they are not
acceptance tolerances for an exported animation. Callers must compare against
the native target and report unavailable evidence when few contacts exist.
"""
import numpy as np


def native_blink_contacts(positions, material_triangles, blink_deltas, *, up_axis=2,
                          affected_floor=1e-5, near_fraction=.03, open_fraction=.12,
                          minimum_unique_pairs=4):
    """Return unit-native blink contact pairs and selection diagnostics.

    positions: (N, 3) neutral world coordinates.
    material_triangles: {material identifier: (M, 3) source vertex indices}.
        Identifiers may be integers, strings, or tuples such as (mesh, slot).
    blink_deltas: {shape-key name: (N, 3) unit native displacement field} in
        the same world axes/units as positions. Each key is evaluated alone.
    up_axis: 2 for Blender world, 1 for exported PDX world.

    Eye height is the P95-P5 vertical span of vertices affected by the native
    key. Pairs must be material-boundary vertices, close after the native key,
    and separated substantially before it. This excludes short neighboring
    edges. The resulting nonzero native pair vector remains the target.
    """
    p = np.asarray(positions, dtype=np.float64)
    if p.ndim != 2 or p.shape[1] != 3 or not np.isfinite(p).all():
        raise ValueError('Blink contact positions must be finite (N, 3) coordinates')
    if up_axis not in (0, 1, 2):
        raise ValueError('Invalid blink up axis')
    if not (0 < near_fraction < open_fraction) or affected_floor <= 0:
        raise ValueError('Invalid blink contact detection scales')
    regions, fields = {}, {}
    for key, values in blink_deltas.items():
        delta = np.asarray(values, dtype=np.float64)
        if delta.shape != p.shape or not np.isfinite(delta).all():
            raise ValueError(f'Blink displacement field {key!r} has invalid shape or values')
        moved = np.linalg.norm(delta, axis=1) > affected_floor
        if int(moved.sum()) < 4:
            continue
        bounds = np.quantile(p[moved, up_axis], [.05, .95])
        height = float(bounds[1] - bounds[0])
        if height <= affected_floor * 10:
            continue
        regions[key] = dict(eye_height=height, affected_vertices=int(moved.sum()),
                            maximum_native_gap=height * near_fraction,
                            minimum_open_gap=height * open_fraction, pair_count=0)
        fields[key] = (delta, moved)
    pairs, materials = [], []
    all_moved = np.zeros(len(p), dtype=bool)
    for _, moved in fields.values():
        all_moved |= moved
    for material, triangles in material_triangles.items():
        triangles = np.asarray(triangles, dtype=np.int64)
        if not triangles.size:
            continue
        if triangles.ndim != 2 or triangles.shape[1] != 3:
            raise ValueError(f'Material {material!r} triangles must have shape (M, 3)')
        if triangles.min() < 0 or triangles.max() >= len(p):
            raise ValueError(f'Material {material!r} has an invalid vertex index')
        if not all_moved[triangles].any():
            continue
        edges = triangles[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)
        edges.sort(axis=1)
        unique_edges, counts = np.unique(edges, axis=0, return_counts=True)
        boundary = np.unique(unique_edges[counts == 1])
        if not len(boundary):
            continue
        material_pairs = {}
        active_region = False
        for key, (delta, moved) in fields.items():
            origins = boundary[moved[boundary]]
            if not len(origins):
                continue
            active_region = True
            region = regions[key]
            target = p + delta
            # Work in short chunks to bound temporary memory on dense models.
            for start in range(0, len(origins), 128):
                batch = origins[start:start+128]
                distances = np.linalg.norm(target[batch, None] - target[boundary], axis=2)
                for i, j in np.argwhere(distances <= region['maximum_native_gap']):
                    a, b = sorted((int(batch[i]), int(boundary[j])))
                    if a == b or (key, a, b) in material_pairs:
                        continue
                    open_gap = float(np.linalg.norm(p[a] - p[b]))
                    if open_gap < region['minimum_open_gap']:
                        continue
                    vector = target[a] - target[b]
                    row = dict(vertices=[a, b], blink_key=key, material_index=material,
                               eye_height=region['eye_height'], open_gap=open_gap,
                               native_gap=float(np.linalg.norm(vector)),
                               native_separation=vector.tolist())
                    material_pairs[key, a, b] = row
                    region['pair_count'] += 1
        if active_region:
            materials.append(dict(material_index=material, boundary_vertices=int(len(boundary)),
                                  pair_count=len(material_pairs)))
        pairs.extend(material_pairs.values())
    pairs.sort(key=lambda row: (str(row['material_index']), str(row['blink_key']), row['vertices']))
    # Different materials can duplicate both endpoints of one physical seam.
    # Count those once when deciding whether the detector has enough evidence.
    resolution = max((r['eye_height'] for r in regions.values()), default=1.) * 1e-5
    geometric_pairs = {tuple(sorted(tuple(np.rint(p[i] / resolution).astype(np.int64))
                                    for i in pair['vertices'])) for pair in pairs}
    available = len(geometric_pairs) >= minimum_unique_pairs
    result = dict(status='available' if available else 'unavailable',
                  detector='Material boundary contacts in unit native blink shape',
                  eye_height_definition='Affected neutral vertices, vertical P95 minus P5',
                  up_axis=up_axis, near_fraction=near_fraction, open_fraction=open_fraction,
                  regions=regions, pair_count=len(pairs), unique_geometric_pairs=len(geometric_pairs),
                  materials=materials, pairs=pairs)
    if not available:
        result['reason'] = (f'Only {len(geometric_pairs)} independent native contacts; '
                            f'at least {minimum_unique_pairs} needed to assess closure')
    return result
