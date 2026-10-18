"""Native-key contract for composing animations whose mouth proxies are shared."""
import hashlib
import json
from pathlib import Path
import numpy as np


FILENAME = 'moe_expression_basis.json'


def _recover_basis(coefficients, displacements):
    """Fit a full-rank basis while retaining the absolute source error limit.

    Large proxy translations can have several float32 ulps of export noise.
    Least squares minimizes average error, so an isolated sample may exceed
    the limit even when a nearby matrix satisfies every sample. Only in that
    case, project offending columns onto the same hard residual constraints.
    An inconsistent source still fails; no animation samples are changed.
    """
    basis, _, rank, _ = np.linalg.lstsq(coefficients, displacements, rcond=1e-9)
    limit = 1e-4
    initial_error = float(np.max(np.abs(coefficients @ basis - displacements)))
    refined = []
    if rank == coefficients.shape[1] and initial_error > limit:
        columns = np.flatnonzero(np.max(np.abs(coefficients @ basis - displacements), axis=0) > limit)
        target = limit * .95
        for column in columns:
            for iteration in range(256):
                residual = coefficients @ basis[:, column] - displacements[:, column]
                row = int(np.argmax(np.abs(residual)))
                if abs(residual[row]) <= target + 1e-10:
                    break
                direction = coefficients[row]
                norm_squared = float(direction @ direction)
                if norm_squared <= 1e-18:
                    break
                basis[:, column] -= (residual[row] - np.copysign(target, residual[row])) * direction / norm_squared
            refined.append(dict(column=int(column), projections=iteration))
    error = float(np.max(np.abs(coefficients @ basis - displacements)))
    if rank != coefficients.shape[1] or np.linalg.matrix_rank(basis, tol=1e-8) != coefficients.shape[1] or error > limit:
        raise ValueError(f'Shared native-key basis is not recoverable from source clips: rank={rank}, error={error}')
    refinement = dict(method='maximum-error constrained projection', error_limit=limit,
                      least_squares_error_max=initial_error, columns=refined) if refined else None
    return basis, error, refinement


def from_bake(report, directory, read_anim, unpack):
    """Measure the linear native-key basis from actual exported source clips.

    Fitting this tiny matrix also verifies Blender-to-PDX translation axes.
    It writes neither animation bytes nor skin weights. The measured residual
    must be below float export noise before the contract can be used.
    """
    shared = report.get('shared_mouth_fit')
    if not shared:
        return None
    directory = Path(directory)
    keys = shared['keys']
    names = sorted({name for key in keys for name in report['expression_fit'][key]['bones']})
    neutral_clip = read_anim(directory / 'moe_idle_calm.anim')
    neutral = {b['name']: (b.get('t') or [0., 0., 0.]) for b in neutral_clip['bones'] if b['name'] in names}
    x, y, hashes = [], [], {}
    for expression, samples in report['sampled_expression_targets'].items():
        path = directory / f'moe_{expression}.anim'
        clip = read_anim(path)
        _, tracks = unpack(clip)
        rest = {b['name']: (b.get('t') or [0., 0., 0.]) for b in clip['bones']}
        if len(samples) != clip['nf']:
            raise ValueError('Shared basis target/source frame count mismatch')
        x.extend([[sample.get(key, 0.) for key in keys] for sample in samples])
        values = np.asarray([tracks.get(name) or [rest[name]] * clip['nf'] for name in names]).transpose(1, 0, 2)
        y.extend((values - np.asarray([neutral[name] for name in names])).reshape(clip['nf'], -1))
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    coefficients, displacements = np.asarray(x), np.asarray(y)
    basis, error, refinement = _recover_basis(coefficients, displacements)
    result = dict(version=1, space='PDX animation parent-local translation deltas',
                keys=keys, bones=names, neutral_t=[neutral[name] for name in names],
                matrix=basis.tolist(), source_reconstruction_error_max=error,
                source_animation_sha256=hashes)
    if refinement:
        result['basis_refinement'] = refinement
    return result


def load(path):
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if value.get('version') != 1:
        raise ValueError('Unsupported shared expression basis version')
    return value


def _nonnegative_source_keys(values, matrix, keys, negative_tolerance=2e-4):
    """Recover boundary coefficients without altering already accepted rows.

    Float export noise can make a zero native key slightly negative when the
    basis vectors are close. For only the previously rejected rows, solve the
    tiny nonnegative least-squares problem by enumerating active key subsets.
    The caller still checks the unchanged source reconstruction bound.
    """
    bad = np.flatnonzero(np.min(keys, axis=1) < -negative_tolerance)
    if not len(bad):
        return keys
    if matrix.shape[0] > 8:
        raise ValueError('Too many native keys for bounded nonnegative source recovery')
    selected = values[bad]
    best = np.zeros((len(bad), matrix.shape[0]), dtype=float)
    errors = np.sum(selected * selected, axis=1)
    for mask in range(1, 1 << matrix.shape[0]):
        active = [index for index in range(matrix.shape[0]) if mask & (1 << index)]
        candidate = selected @ np.linalg.pinv(matrix[active], rcond=1e-9)
        feasible = np.min(candidate, axis=1) >= 0.0
        residual = candidate @ matrix[active] - selected
        error = np.sum(residual * residual, axis=1)
        improve = feasible & (error < errors)
        if np.any(improve):
            best[improve] = 0.0
            best[np.ix_(improve, active)] = candidate[improve]
            errors[improve] = error[improve]
    result = keys.copy()
    result[bad] = best
    return result


def project_emotion(target, expression, basis):
    """Resample a shared mouth through native keys even without speech.

    Interpolated source proxy translations retain tiny source-fit residuals.
    Decoding those residuals can move a zero native key outside its allowed
    range. Speech already reconstructs the mouth from nonnegative native keys;
    emotion-only clips need the same source composition contract.
    """
    names = basis['bones']
    matrix = np.asarray(basis['matrix'], float)
    neutral = np.asarray(basis['neutral_t'], float)
    if matrix.shape != (len(basis['keys']), len(names) * 3) or neutral.shape != (len(names), 3):
        raise ValueError('Invalid shared expression basis shape')
    if not np.isfinite(matrix).all() or not np.isfinite(neutral).all():
        raise ValueError('Invalid shared expression basis')
    rest = {b['name']: b.get('t') or [0., 0., 0.] for b in expression['bones']}
    if not set(names) <= rest.keys():
        raise ValueError('Shared expression bones missing from source clip')
    lengths = {len(v) for n, v in target.items() if n in names and v}
    if not lengths:
        return 0
    if len(lengths) != 1:
        raise ValueError('Shared emotion tracks have inconsistent lengths')
    frames = next(iter(lengths))
    samples = np.asarray([target.get(name) or [rest[name]] * frames for name in names], float)
    if samples.shape != (len(names), frames, 3) or not np.isfinite(samples).all():
        raise ValueError('Shared emotion source tracks have incompatible values')
    values = (samples.transpose(1, 0, 2) - neutral).reshape(frames, -1)
    keys = values @ np.linalg.pinv(matrix, rcond=1e-9)
    if np.max(np.abs(keys @ matrix - values)) > 2e-4:
        raise ValueError('Emotion source mouth channels do not match the declared native-key basis')
    keys = _nonnegative_source_keys(values, matrix, keys, negative_tolerance=0.)
    keys = np.maximum(keys, 0.)
    output = keys @ matrix
    if np.max(np.abs(output - values)) > 2e-4:
        raise ValueError('Nonnegative emotion source mouth does not match the declared native-key basis')
    output = output.reshape(frames, len(names), 3) + neutral
    for i, name in enumerate(names):
        target[name] = [tuple(frame) for frame in output[:, i]]
    return len(names)


def overlay(target, expression, layer, speech, basis, gain=1.):
    """Take maximum per native mouth key, then map the whole mouth coherently.

    A per-bone maximum would splice unrelated proxy trajectories, which is
    invalid once multiple native keys share their weights. Native coefficients
    retain the old max-envelope semantics without adding speech to the same
    emotional mouth-open slot twice.
    """
    names = basis['bones']
    matrix = np.asarray(basis['matrix'], float)
    neutral = np.asarray(basis['neutral_t'], float)
    if matrix.shape != (len(basis['keys']), len(names) * 3) or neutral.shape != (len(names), 3):
        raise ValueError('Invalid shared expression basis shape')
    if not np.isfinite(matrix).all() or not np.isfinite(neutral).all() or not np.isfinite(gain) or gain < 0:
        raise ValueError('Invalid shared expression basis or gain')
    erest = {b['name']: b.get('t') or [0., 0., 0.] for b in expression['bones']}
    srest = {b['name']: b.get('t') or [0., 0., 0.] for b in speech['bones']}
    if not set(names) <= erest.keys() or not set(names) <= srest.keys():
        raise ValueError('Shared expression bones missing from source clip')
    lengths = {len(v) for n, v in layer.items() if n in names and v}
    if not lengths:
        return 0
    if len(lengths) != 1:
        raise ValueError('Shared speech tracks have inconsistent lengths')
    frames = next(iter(lengths))
    def samples(tracks, rest):
        arr = np.asarray([tracks.get(name) or [rest[name]] * frames for name in names], float)
        if arr.shape != (len(names), frames, 3):
            raise ValueError('Shared source tracks have incompatible lengths')
        return (arr.transpose(1, 0, 2) - neutral).reshape(frames, -1)
    emotion_values, speech_values = samples(target, erest), samples(layer, srest)
    inverse = np.linalg.pinv(matrix, rcond=1e-9)
    emotion_keys, speech_keys = emotion_values @ inverse, speech_values @ inverse
    reconstruction = max(float(np.max(np.abs(emotion_keys @ matrix - emotion_values))),
                         float(np.max(np.abs(speech_keys @ matrix - speech_values))))
    if reconstruction > 2e-4:
        raise ValueError(f'Source mouth channels do not match the declared native-key basis ({reconstruction})')
    emotion_keys = _nonnegative_source_keys(emotion_values, matrix, emotion_keys)
    speech_keys = _nonnegative_source_keys(speech_values, matrix, speech_keys)
    reconstruction = max(float(np.max(np.abs(emotion_keys @ matrix - emotion_values))),
                         float(np.max(np.abs(speech_keys @ matrix - speech_values))))
    if reconstruction > 2e-4 or min(emotion_keys.min(), speech_keys.min()) < -2e-4:
        raise ValueError(f'Source mouth channels do not match the declared native-key basis ({reconstruction})')
    combined = np.maximum(np.maximum(emotion_keys, gain * speech_keys), 0.)
    output = (combined @ matrix).reshape(frames, len(names), 3) + neutral
    for i, name in enumerate(names):
        target[name] = [tuple(frame) for frame in output[:, i]]
    return len(names)
