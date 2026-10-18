"""Explicit destination-mesh dependencies for lossless animation sample export.

Only sampled channels may be omitted. Bone tables and rest values remain in
the file. A dependency set must include positive skin weights, every ancestor,
the caller's semantic contract, and all runtime attachment/camera references.
"""
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import re

import pdx_parse


@dataclass(frozen=True)
class MeshObservability:
    mesh_path: str
    mesh_sha256: str
    bone_names: tuple
    directly_skinned_names: frozenset
    protected_names: frozenset
    observable_names: frozenset
    reference_sha256: tuple

    def for_animation(self, bone_names):
        """Return explicit dependencies only for this exact ordered bone table.

        Legacy/template animations with a different table need ordinary export,
        or an explicit new contract. They must not borrow another mesh's set.
        """
        if tuple(bone_names) != self.bone_names:
            raise ValueError('Animation bone table differs from observable mesh')
        return self.observable_names


def from_mesh(mesh_path, *, protected_names, reference_paths=()):
    """Read actual weights and parents; callers supply semantics and runtime files.

    There is no filename/character/head heuristic. ``protected_names`` may be
    a shared semantic contract with optional bones; names absent from this mesh
    are ignored. Every matching bone token in the supplied runtime files is
    also preserved, so parent/child hooks and camera references stay animated.
    """
    mesh_path = Path(mesh_path).resolve()
    data = mesh_path.read_bytes()
    grouped = {}
    for node, key, _kind, values in pdx_parse.walk(pdx_parse.parse(data)):
        grouped.setdefault(node, {})[key] = values
    bones = {}
    for node, values in grouped.items():
        if '/skeleton/' not in node or 'ix' not in values or 'tx' not in values:
            continue
        index = values['ix'][0]
        if index in bones:
            raise ValueError('Duplicate skeleton index')
        bones[index] = (node.rsplit('/', 1)[1], values.get('pa', [-1])[0])
    if not bones or sorted(bones) != list(range(len(bones))):
        raise ValueError('Mesh needs a contiguous skeleton')
    names = tuple(bones[i][0] for i in range(len(bones)))
    if len(set(names)) != len(names):
        raise ValueError('Mesh contains duplicate bone names')
    for start in bones:
        index = start
        visited = set()
        while index >= 0:
            if index not in bones or index in visited:
                raise ValueError('Mesh skeleton has invalid or cyclic parents')
            visited.add(index)
            index = bones[index][1]
    positive = set()
    geometry = 0
    for node, values in grouped.items():
        if 'p' not in values or 'tri' not in values:
            continue
        geometry += 1
        skin = grouped.get(node + '/skin', {})
        width = skin.get('bones', [0])[0]
        indices, weights = skin.get('ix', []), skin.get('w', [])
        if (not 0 < width <= 4 or len(values['p']) % 3 or
                len(indices) != len(values['p']) // 3 * width or len(weights) != len(indices)):
            raise ValueError('Mesh skin arrays violate the four-influence contract')
        for index, weight in zip(indices, weights):
            if not math.isfinite(weight) or weight < 0:
                raise ValueError('Mesh skin weights must be finite and nonnegative')
            if weight > 0:
                if index not in bones:
                    raise ValueError('Positive weight references a missing bone')
                positive.add(index)
    if not geometry or not positive:
        raise ValueError('Mesh contains no positive skinned geometry')
    if isinstance(protected_names, (str, bytes)):
        raise ValueError('protected_names must be an explicit collection')
    protected = set(protected_names) & set(names)
    references = []
    for source in reference_paths:
        path = Path(source).resolve()
        content = path.read_bytes()
        tokens = set(re.findall(r'[A-Za-z_][A-Za-z_0-9.]*', content.decode('utf-8-sig')))
        protected.update(tokens & set(names))
        references.append((str(path), hashlib.sha256(content).hexdigest()))
    observable = positive | {i for i, name in enumerate(names) if name in protected}
    for index in list(observable):
        visited = set()
        while index >= 0:
            if index not in bones or index in visited:
                raise ValueError('Mesh skeleton has invalid or cyclic parents')
            visited.add(index)
            observable.add(index)
            index = bones[index][1]
    return MeshObservability(str(mesh_path), hashlib.sha256(data).hexdigest(), names,
                             frozenset(names[i] for i in positive), frozenset(protected),
                             frozenset(names[i] for i in observable), tuple(references))
