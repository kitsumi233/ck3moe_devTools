"""Fit native expression fields within the actual four-influence skin budget.

Proxy translations sharing a native key are solved together. Optional weight
refinement preserves native weights and each vertex's proxy budget; an existing
blink proxy may occupy a genuinely empty fourth slot. Skeleton and binds stay
fixed. These are least-squares deformation fits, not expression-strength gains.
"""
import numpy as np
from collections import defaultdict
from itertools import combinations

from moe_expression_closure import native_blink_contacts


def _row_norm_max(solution, width):
    """Largest per-proxy translation length; ``width`` is 3 (xyz) or 1 (one axis)."""
    s = np.asarray(solution, dtype=np.float64)
    if width == 1:
        return float(np.abs(s).max(initial=0.))
    return float(np.linalg.norm(s.reshape(len(s), -1, 3), axis=2).max(initial=0.))


def bounded_lstsq(x, y, cap=None, rcond=1e-8, width=3):
    """Least squares whose per-proxy translation length never exceeds ``cap``.

    Why (2026-09-29, face spikes on col/mis/zho): the unregularized solve lets a
    proxy whose weights are all tiny (col bn_m_fx_mouth_up: 422 vertices, w<=0.006)
    or whose columns are nearly collinear with another proxy (mis mouth_up vs
    mouth_up_rev) run to 30~190 world units. The fit stays exact at rest-neutral
    sample points only by cancellation, and any real-time blend of two keys
    breaks the cancellation and throws spikes.

    With ``cap`` None, or when the plain solution already satisfies it, the plain
    ``lstsq`` result is returned unchanged (bit-identical), so well-conditioned
    characters (lau control) are unaffected. Otherwise a Tikhonov ridge toward
    zero is bisected until the largest proxy translation equals ``cap``; the
    ridge path shortens every row monotonically, so this is a smooth shrink,
    not a clip that would change a translation's direction.
    Returns (solution, ridge_lambda).
    """
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    plain = np.linalg.lstsq(x, y, rcond=rcond)[0]
    if cap is None or not np.isfinite(cap) or _row_norm_max(plain, width) <= cap:
        return plain, 0.
    gram, rhs = x.T @ x, x.T @ y
    eye = np.eye(len(gram))
    scale = float(np.trace(gram)) / max(len(gram), 1) or 1.

    def solve(lam):
        return np.linalg.solve(gram + lam * eye, rhs)

    lo, hi = 0., scale * 1e-6
    while _row_norm_max(solve(hi), width) > cap:
        lo, hi = hi, hi * 4.
        if hi > scale * 1e12:
            break
    for _ in range(60):
        mid = (lo + hi) / 2.
        if _row_norm_max(solve(mid), width) > cap:
            lo = mid
        else:
            hi = mid
    return solve(hi), hi


def fit_translations(weights, target, original):
    """Return bone translations and error evidence for one shape-key field.

    weights: (vertices, proxy bones), nonnegative final skin weights.
    target: (vertices, 3), the unit shape's displacements in a common frame.
    original: (proxy bones, 3), the former projection translations.
    """
    w = np.asarray(weights, dtype=np.float64)
    d = np.asarray(target, dtype=np.float64)
    old = np.asarray(original, dtype=np.float64)
    if w.ndim != 2 or d.shape != (len(w), 3) or old.shape != (w.shape[1], 3):
        raise ValueError('Expression fit arrays have inconsistent shapes')
    if not all(np.isfinite(a).all() for a in (w, d, old)) or (w < 0).any():
        raise ValueError('Expression fit requires finite values and nonnegative weights')
    if not w.shape[1] or not np.any(w > 0):
        raise ValueError('Expression has no surviving proxy influence')
    result, _, rank, _ = np.linalg.lstsq(w, d, rcond=1e-8)
    before = float(np.sum((w @ old - d) ** 2))
    after = float(np.sum((w @ result - d) ** 2))
    if not np.isfinite(result).all() or after > before + max(1e-10, before * 1e-9):
        raise ValueError('Expression translation fit increased deformation error')
    energy = float(np.sum(d * d))
    evidence = dict(vertices=len(w), proxy_bones=w.shape[1], rank=int(rank),
                    target_energy=energy, squared_error_before=before,
                    squared_error_after=after,
                    normalized_rmse_before=(before / energy) ** .5 if energy else 0.,
                    normalized_rmse_after=(after / energy) ** .5 if energy else 0.)
    return result, evidence


def _field_error(actual, target, mask):
    """Error in the caller's world units, with the normalization made explicit."""
    predicted, expected = actual[mask], target[mask]
    residual = predicted - expected
    lengths = np.linalg.norm(residual, axis=1)
    energy = float(np.sum(expected * expected))
    squared_error = float(np.sum(residual * residual))
    magnitude = float(np.linalg.norm(expected, axis=1).sum())
    return dict(vertices=int(np.count_nonzero(mask)), target_energy=energy,
                squared_error=squared_error,
                normalized_rmse=float(np.sqrt(squared_error / energy)) if energy else None,
                absolute_rmse=float(np.sqrt(np.mean(lengths * lengths))) if len(lengths) else 0.,
                absolute_max_error=float(lengths.max(initial=0.)),
                absolute_p95_error=float(np.percentile(lengths, 95)) if len(lengths) else 0.,
                displacement_ratio=float(np.linalg.norm(predicted, axis=1).sum() / magnitude)
                if magnitude else None)


def _fixed_budget_weight_step(current, desired, drives, groups, lower, class_scales):
    """Solve each <=4-variable nonnegative equality QP by active-set enumeration.

    Group rows with the same allowed support and loss scales. The lower bounds
    preserve every original positive influence; only existing blink proxies
    explicitly admitted by the caller can acquire a previously empty slot.
    """
    result = np.zeros_like(current)
    for (support, row_class), row_list in groups.items():
        if not support:
            continue
        columns, rows = np.asarray(support), np.asarray(row_list)
        initial = current[np.ix_(rows, columns)]
        floor = lower[np.ix_(rows, columns)]
        budget = initial.sum(axis=1) - floor.sum(axis=1)
        design = drives[columns] * class_scales[row_class]
        target = desired[rows] * class_scales[row_class] - floor @ design
        quadratic, linear = design @ design.T, target @ design.T
        best = initial - floor
        best_cost = np.sum((best @ design - target) ** 2, axis=1)
        for count in range(1, len(columns) + 1):
            for subset in combinations(range(len(columns)), count):
                selected = np.asarray(subset)
                # Equality-constrained least squares for this nonzero subset.
                kkt = np.zeros((count + 1, count + 1))
                kkt[:count, :count] = quadratic[np.ix_(selected, selected)]
                kkt[:count, count] = 1.
                kkt[count, :count] = 1.
                rhs = np.column_stack((linear[:, selected], budget))
                solution = rhs @ np.linalg.pinv(kkt, rcond=1e-12).T
                values = solution[:, :count]
                feasible = ((values >= -1e-10).all(axis=1)
                            & (np.abs(values.sum(axis=1) - budget) <= 1e-8))
                candidate = np.zeros_like(initial)
                candidate[:, selected] = np.maximum(values, 0.)
                cost = np.sum((candidate @ design - target) ** 2, axis=1)
                improved = feasible & (cost < best_cost)
                best[improved], best_cost[improved] = candidate[improved], cost[improved]
        result[np.ix_(rows, columns)] = best + floor
    return result


def refine_expression_weights(weights, targets, key_bones, positions,
                              material_triangles, native_weights, blink_keys, *,
                              iterations=80, closure_weight=16., pair_weight=0.,
                              preserve_support_fraction=.01,
                              preserve_support_floor=1.01e-4,
                              allow_blink_support=True, affected_floor=1e-5,
                              up_axis=2, translation_cap_ratio=None,
                              min_fit_weight=0.):
    """Refine expression skinning without altering native weights or skeleton.

    ``weights`` is N x B final proxy skin weights. ``targets`` maps native shape
    keys to N x 3 unit displacement fields; ``key_bones`` maps the same keys to
    exclusive proxy-column lists. Positions and target fields share world axes
    and units. ``native_weights`` contains every actual native influence, with
    zero padding permitted. Material triangles use global source indices.

    Each vertex keeps its exact original proxy-weight budget and all original
    positive influences. An affected blink vertex with a truly empty influence
    slot may receive an existing proxy for that same native blink key. The
    combined native+proxy support can never exceed four actual nonzero weights.
    No skeleton, native weight, normalization, or bind/rest change is performed.

    Alternate least-squares translations and fixed-budget nonnegative weights.
    Native contact endpoints receive ``closure_weight`` point loss. Optional
    ``pair_weight`` also penalizes their relative displacement error. Pair loss
    uses a graph-Laplacian majorizer during the independent per-vertex updates.
    These are fitting controls, not visual acceptance thresholds. The report
    describes tradeoffs; exported assets and rendered expression plates still
    need validation. Inputs are never mutated.

    ``translation_cap_ratio`` (None = off) bounds each proxy translation of a
    key to ratio x that key's largest native displacement (see bounded_lstsq).
    ``min_fit_weight`` (0 = off) leaves rows whose proxy budget for the key is
    below it out of the translation solve; they still receive weight updates.
    Both are off by default so existing bakes stay bit-identical.

    Return (refined_weights, {key: len(key_bones[key]) x 3 translations}, report).
    """
    w_all = np.array(weights, dtype=np.float64, copy=True)
    native = np.array(native_weights, dtype=np.float64, copy=True)
    p = np.asarray(positions, dtype=np.float64)
    if w_all.ndim != 2 or native.ndim != 2 or len(native) != len(w_all):
        raise ValueError('Proxy and native weights must be two-dimensional arrays with equal rows')
    if p.shape != (len(w_all), 3):
        raise ValueError('Neutral positions must be (vertices, 3)')
    if (not all(np.isfinite(a).all() for a in (w_all, native, p))
            or (w_all < 0).any() or (native < 0).any()):
        raise ValueError('Skinning inputs must be finite, with nonnegative weights')
    if (not isinstance(iterations, (int, np.integer)) or iterations < 0
            or not np.isfinite([closure_weight, pair_weight, preserve_support_fraction,
                               preserve_support_floor, affected_floor]).all()
            or closure_weight < 1 or pair_weight < 0
            or not 0 < preserve_support_fraction <= 1
            or preserve_support_floor <= 0 or affected_floor <= 0):
        raise ValueError('Invalid refinement iterations, loss weights, or support floors')
    original_counts = (native > 0).sum(axis=1) + (w_all > 0).sum(axis=1)
    if (original_counts > 4).any():
        raise ValueError('Input skin has more than four actual nonzero native+proxy influences')
    original_sums = native.sum(axis=1) + w_all.sum(axis=1)
    if np.any(np.abs(original_sums - 1.) > 1e-5):
        raise ValueError('Input native+proxy weights must already sum to one; refinement does not normalize them')
    if set(targets) != set(key_bones) or not key_bones:
        raise ValueError('Targets and key_bones must contain the same nonempty key set')
    keys = list(key_bones)
    key_index = {key: i for i, key in enumerate(keys)}
    indices, fields, owners = {}, {}, {}
    for key in keys:
        field = np.asarray(targets[key], dtype=np.float64)
        if field.shape != p.shape or not np.isfinite(field).all():
            raise ValueError(f'Invalid native target field {key!r}')
        columns = np.asarray(key_bones[key], dtype=np.int64)
        if (columns.ndim != 1 or not len(columns) or len(set(columns)) != len(columns)
                or columns.min() < 0 or columns.max() >= w_all.shape[1]):
            raise ValueError(f'Invalid proxy columns for {key!r}')
        for column in columns:
            if int(column) in owners:
                raise ValueError('A proxy bone cannot belong to multiple native shape keys')
            owners[int(column)] = key
        indices[key], fields[key] = columns, field
    if set(owners) != set(range(w_all.shape[1])):
        raise ValueError('Every proxy column must have exactly one native shape-key owner')
    blink = set(blink_keys)
    if not blink <= set(keys):
        raise ValueError('Blink keys must be present in targets and key_bones')
    contacts = native_blink_contacts(p, material_triangles,
                                     {key: fields[key] for key in keys if key in blink},
                                     up_axis=up_axis, affected_floor=affected_floor)
    usable_pairs = contacts['pairs'] if contacts['status'] == 'available' else []
    active = np.flatnonzero(w_all.sum(axis=1) > 0)
    if not len(active):
        raise ValueError('No positive expression proxy weights to refine')
    inverse = np.full(len(w_all), len(active), dtype=np.int64)
    inverse[active] = np.arange(len(active))
    w0 = w_all[active].copy()
    target = np.concatenate([fields[key][active] for key in keys], axis=1)
    point_loss = np.ones((len(active), len(keys)))
    degree = np.zeros_like(point_loss)
    pairs_by_key = {}
    for key in keys:
        selected = [row['vertices'] for row in usable_pairs if row['blink_key'] == key]
        if not selected:
            continue
        global_pairs = np.asarray(selected, dtype=np.int64)
        local_pairs = inverse[global_pairs]
        ki = key_index[key]
        local_vertices = local_pairs[local_pairs < len(active)]
        point_loss[local_vertices, ki] = closure_weight
        np.add.at(degree[:, ki], local_vertices, 1.)
        pairs_by_key[key] = (local_pairs[:, 0], local_pairs[:, 1],
                            fields[key][global_pairs[:, 0]] - fields[key][global_pairs[:, 1]])
    permitted = w0 > 0
    used = original_counts[active].copy()
    additional_candidates = 0
    if allow_blink_support:
        for key in keys:
            if key not in blink:
                continue
            affected = np.linalg.norm(fields[key][active], axis=1) > affected_floor
            for column in indices[key]:
                admit = affected & ~permitted[:, column] & (used < 4)
                permitted[admit, column] = True
                used[admit] += 1
                additional_candidates += int(admit.sum())
    lower = np.minimum(w0, np.maximum(w0 * preserve_support_fraction, preserve_support_floor))
    scales_per_key = np.sqrt(point_loss + pair_weight * 2. * degree)
    classes, row_classes = np.unique(scales_per_key, axis=0, return_inverse=True)
    class_scales = np.repeat(classes, 3, axis=1)
    groups = defaultdict(list)
    for row in range(len(active)):
        groups[(tuple(np.flatnonzero(permitted[row])), int(row_classes[row]))].append(row)
    point_scales = np.repeat(np.sqrt(point_loss), 3, axis=1)
    regularized = translation_cap_ratio is not None or min_fit_weight > 0
    key_cap = {key: (float(translation_cap_ratio) * float(np.linalg.norm(fields[key], axis=1).max(initial=0.))
                     if translation_cap_ratio is not None else None) for key in keys}
    ridge_used = {}

    def fit(current, weighted=True):
        drives = np.zeros((w_all.shape[1], len(keys) * 3))
        padded = np.vstack((current, np.zeros((1, current.shape[1]))))
        for key in keys:
            ki, columns = key_index[key], indices[key]
            block = slice(ki * 3, ki * 3 + 3)
            scale = point_scales[:, ki * 3] if weighted else np.ones(len(active))
            if min_fit_weight > 0:
                keep = current[:, columns].sum(axis=1) >= min_fit_weight
                if keep.any():
                    scale = scale * keep
            x, y = current[:, columns] * scale[:, None], target[:, block] * scale[:, None]
            if weighted and pair_weight and key in pairs_by_key:
                left, right, delta = pairs_by_key[key]
                x = np.vstack((x, (padded[left][:, columns] - padded[right][:, columns]) * pair_weight**.5))
                y = np.vstack((y, delta * pair_weight**.5))
            drives[columns, block], lam = bounded_lstsq(x, y, key_cap[key], rcond=1e-8)
            if lam:
                ridge_used[key] = lam
        return drives

    def pair_residuals(predicted):
        padded = np.vstack((predicted, np.zeros((1, predicted.shape[1]))))
        for key, (left, right, delta) in pairs_by_key.items():
            ki = key_index[key]
            block = slice(ki * 3, ki * 3 + 3)
            yield block, left, right, padded[left, block] - padded[right, block] - delta

    def objective(current, drives):
        predicted = current @ drives
        value = float(np.sum(((predicted - target) * point_scales) ** 2))
        if pair_weight:
            value += pair_weight * sum(float(np.sum(delta * delta))
                                      for _, _, _, delta in pair_residuals(predicted))
        return value

    def update_weights(current, drives):
        desired = target
        if pair_weight:
            predicted = current @ drives
            laplacian = np.zeros((len(active) + 1, predicted.shape[1]))
            for block, left, right, delta in pair_residuals(predicted):
                np.add.at(laplacian[:, block], left, delta)
                np.add.at(laplacian[:, block], right, -delta)
            double_degree = np.repeat(2. * degree, 3, axis=1)
            neighbour_target = predicted - laplacian[:-1] / np.maximum(double_degree, 1.)
            point_weight = point_scales**2
            pair_loss = pair_weight * double_degree
            desired = (point_weight * target + pair_loss * neighbour_target) / (point_weight + pair_loss)
        return _fixed_budget_weight_step(current, desired, drives, groups, lower, class_scales)

    original_drives = fit(w0, weighted=False)
    current = w0.copy()
    drives = fit(current)
    initial_objective = objective(current, drives)
    history, converged = [], False
    for _ in range(iterations):
        before = objective(current, drives)
        updated = update_weights(current, drives)
        updated_drives = fit(updated)
        after = objective(updated, updated_drives)
        if not np.isfinite(after) or after > before + max(1e-8, before * 1e-8):
            if regularized and np.isfinite(after):
                # A bounded refit is not the exact minimizer, so a step may go
                # slightly uphill; stop at the last accepted state instead.
                break
            raise ValueError('Expression refinement increased the fixed fitting objective')
        current, drives = updated, updated_drives
        history.append(after)
        if before - after <= max(before * 1e-7, 1e-10):
            converged = True
            break
    result = w_all.copy()
    result[active] = current
    final_counts = (native > 0).sum(axis=1) + (result > 0).sum(axis=1)
    budget_error = float(np.max(np.abs(result.sum(axis=1) - w_all.sum(axis=1))))
    unsupported = (current > 0) & ~permitted
    lost = (w0 > 0) & (current <= 0)
    if (not np.isfinite(result).all() or not np.isfinite(drives).all()
            or (result < 0).any() or (final_counts > 4).any()
            or budget_error > 1e-8 or unsupported.any() or lost.any()
            or not np.array_equal(np.asarray(native_weights), native)):
        raise ValueError('Refinement violated native-weight, support, or four-influence constraints')
    before_fields, after_fields, per_key = {}, {}, {}
    for key in keys:
        block = slice(key_index[key] * 3, key_index[key] * 3 + 3)
        old_field, new_field = np.zeros_like(p), np.zeros_like(p)
        old_field[active], new_field[active] = w0 @ original_drives[:, block], current @ drives[:, block]
        before_fields[key], after_fields[key] = old_field, new_field
        moved = np.linalg.norm(fields[key], axis=1) > affected_floor
        union = moved | (w_all[:, indices[key]].sum(axis=1) > 0) | (result[:, indices[key]].sum(axis=1) > 0)
        per_key[key] = dict(before=_field_error(old_field, fields[key], moved),
                            after=_field_error(new_field, fields[key], moved),
                            supported_union_before=_field_error(old_field, fields[key], union),
                            supported_union_after=_field_error(new_field, fields[key], union),
                            unsupported_moved_vertices_before=int(np.count_nonzero(moved & (w_all[:, indices[key]].sum(axis=1) == 0))),
                            unsupported_moved_vertices_after=int(np.count_nonzero(moved & (result[:, indices[key]].sum(axis=1) == 0))))
    contact_report = dict(contacts)
    measured_pairs = []
    for pair in contacts['pairs']:
        key, (a, b) = pair['blink_key'], pair['vertices']
        old_vector = p[a] + before_fields[key][a] - p[b] - before_fields[key][b]
        new_vector = p[a] + after_fields[key][a] - p[b] - after_fields[key][b]
        native_vector = np.asarray(pair['native_separation'])
        measured_pairs.append(dict(pair, before_gap=float(np.linalg.norm(old_vector)),
                                   after_gap=float(np.linalg.norm(new_vector)),
                                   before_separation=old_vector.tolist(), after_separation=new_vector.tolist(),
                                   before_vector_error=float(np.linalg.norm(old_vector - native_vector)),
                                   after_vector_error=float(np.linalg.norm(new_vector - native_vector))))
    contact_report['pairs'] = measured_pairs
    native_budget = native.sum(axis=1)
    sums = native_budget + result.sum(axis=1)
    old_sums = native_budget + w_all.sum(axis=1)
    report = dict(method='Fixed native weights and per-vertex expression budget; alternating constrained least squares',
                  status='numerical_candidate_requires_export_and_visual_validation',
                  space='Caller world coordinates and units', iterations=len(history), converged=converged,
                  requested_iterations=iterations, objective_initial=initial_objective,
                  objective_final=objective(current, drives), objective_history=history,
                  closure_weight=closure_weight, pair_weight=pair_weight,
                  active_vertices=len(active), vertices=len(result), proxy_bones=result.shape[1],
                  support_patterns=len(groups), added_blink_support_candidates=additional_candidates,
                  added_positive_blink_influences=int(np.count_nonzero((current > 0) & (w0 == 0))),
                  added_influences_below_export_epsilon=int(np.count_nonzero((w0 == 0) & (current > 0) & (current <= 1e-4))),
                  preserve_support_fraction=preserve_support_fraction,
                  preserve_support_floor=preserve_support_floor,
                  native_weight_max_difference=0., maximum_total_float_nonzero_influences=int(final_counts.max(initial=0)),
                  vertices_over_four=int(np.count_nonzero(final_counts > 4)),
                  original_influences_removed=int(np.count_nonzero(lost)),
                  influences_fallen_below_export_epsilon=int(np.count_nonzero((w0 > 1e-4) & (current <= 1e-4))),
                  fx_budget_max_error=budget_error, skin_sum_max_difference=float(np.max(np.abs(sums - old_sums))),
                  skin_sum_min=float(sums.min()), skin_sum_max=float(sums.max()),
                  maximum_translation=float(np.linalg.norm(drives.reshape(len(drives), -1, 3), axis=2).max(initial=0.)),
                  translation_cap_ratio=translation_cap_ratio, min_fit_weight=min_fit_weight,
                  ridge_keys=sorted(ridge_used),
                  keys=per_key, closure_contacts=contact_report)
    translations = {key: drives[indices[key], key_index[key] * 3:key_index[key] * 3 + 3].copy() for key in keys}
    return result, translations, report


def fit_shared_expression_region(weights, targets, positions, material_triangles,
                                 fixed_weights, *, iterations=80,
                                 edge_weight=.5, boundary_weight=32.,
                                 boundary_point_weight=4., support_floor=1.01e-4,
                                 point_weights=None, coupled_edges=None,
                                 coupling_weight=0., translation_cap_ratio=None,
                                 min_fit_weight=0.):
    """Fit overlapping mouth keys with shared, sparse translation proxies.

    A proxy has one translation per native key; simultaneous keys add their
    translations. Each vertex keeps its original region-weight sum and every
    fixed native/non-region weight. Its available influence count is checked
    before solving, so no exported weight pruning or normalization is needed.
    The sparse simplex solve chooses at most the remaining four-bone slots.
    Boundary loss preserves native relative displacement, not a flattened lip.
    Optional N x 3 ``point_weights`` strengthens selected native-coordinate
    targets (for example tooth depth). It changes fitting loss only, never the
    native target, geometry, skin budget, or emitted animation amplitude.
    ``coupled_edges`` may connect nearby interior and face vertices. Their loss
    preserves each native key's relative displacement across thin surfaces.
    """
    original = np.asarray(weights, dtype=float)
    fixed = np.asarray(fixed_weights, dtype=float)
    p = np.asarray(positions, dtype=float)
    keys = list(targets)
    if original.ndim != 2 or fixed.ndim != 2 or len(fixed) != len(original):
        raise ValueError('Shared expression fit requires matching weight rows')
    if p.shape != (len(original), 3) or not keys or original.shape[1] == 0:
        raise ValueError('Shared expression fit requires geometry and native fields')
    if (iterations < 1 or min(edge_weight, boundary_weight) < 0
            or boundary_point_weight < 1 or support_floor <= 0):
        raise ValueError('Invalid shared expression fitting controls')
    field = np.concatenate([np.asarray(targets[k], float) for k in keys], axis=1)
    if field.shape != (len(p), len(keys) * 3):
        raise ValueError('Invalid shared native expression field shape')
    if (not all(np.isfinite(a).all() for a in (original, fixed, p, field))
            or (original < 0).any() or (fixed < 0).any()):
        raise ValueError('Shared expression inputs must be finite and nonnegative')
    if np.max((original > 0).sum(axis=1) + (fixed > 0).sum(axis=1), initial=0) > 4:
        raise ValueError('Input already exceeds the actual four-influence budget')
    if np.max(np.abs(original.sum(axis=1) + fixed.sum(axis=1) - 1.), initial=0) > 1e-5:
        raise ValueError('Input weights must already sum to one; shared fitting does not normalize')
    budget = original.sum(axis=1)
    active = np.flatnonzero(budget > 0)
    if not len(active):
        raise ValueError('Shared region has no positive weight budget')
    capacity = 4 - (fixed[active] > 0).sum(axis=1)
    b = budget[active]
    target = field[active]
    coordinate_loss = None
    if point_weights is not None:
        point_weights = np.asarray(point_weights, dtype=float)
        if (point_weights.shape != p.shape or not np.isfinite(point_weights).all()
                or (point_weights < 1.).any()):
            raise ValueError('Shared point weights must be finite N x 3 values >= one')
        coordinate_loss = np.tile(point_weights[active], (1, len(keys)))
    lookup = np.full(len(p), len(active), int)
    lookup[active] = np.arange(len(active))
    all_edges, boundary_edges = [], []
    for triangles in material_triangles.values():
        t = np.asarray(triangles, int).reshape(-1, 3)
        e, counts = np.unique(np.sort(np.concatenate((t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]])), axis=1),
                              axis=0, return_counts=True)
        all_edges.extend(e)
        boundary_edges.extend(e[counts == 1])
    coupling = set()
    if coupled_edges is not None:
        coupled = np.asarray(coupled_edges, dtype=np.int64)
        if (coupled.ndim != 2 or coupled.shape[1] != 2 or not len(coupled)
                or coupled.min() < 0 or coupled.max() >= len(p)
                or not np.isfinite(coupling_weight) or coupling_weight <= 0
                or (coupled[:, 0] == coupled[:, 1]).any()):
            raise ValueError('Native surface coupling needs valid vertex pairs and positive loss')
        coupled = np.unique(np.sort(coupled, axis=1), axis=0)
        coupling = {tuple(pair) for pair in coupled}
        all_edges.extend(coupled)
    elif coupling_weight != 0:
        raise ValueError('Native surface coupling weight requires explicit vertex pairs')
    edges = np.unique(np.asarray(all_edges, int).reshape(-1, 2), axis=0)
    edges = edges[(budget[edges] > 0).any(axis=1)]
    # Coincident seam endpoints still constrain native relative motion. No
    # inverse-length penalty is used, so tiny edges cannot dominate the loss.
    border = {tuple(e) for e in boundary_edges}
    is_boundary = np.asarray([tuple(e) in border for e in edges])
    is_coupled = np.asarray([tuple(e) in coupling for e in edges])
    local_edges = lookup[edges]
    delta = field[edges[:, 0]] - field[edges[:, 1]]
    point_scale = np.ones(len(active))
    point_scale[np.isin(active, np.unique(edges[is_boundary]))] = boundary_point_weight
    if coordinate_loss is not None:
        point_scale = point_scale[:, None] * coordinate_loss
    region_bones = original.shape[1]
    # Optional bound (see bounded_lstsq): per key, cap = ratio x largest native
    # displacement of that key. Off (None) keeps the plain solve bit-identical.
    key_cap = [(float(translation_cap_ratio) * float(np.linalg.norm(field[:, 3 * i:3 * i + 3], axis=1).max(initial=0.))
                if translation_cap_ratio is not None else None) for i in range(len(keys))]
    fit_row = np.ones(len(active))
    if min_fit_weight > 0 and (b >= min_fit_weight).any():
        fit_row = (b >= min_fit_weight).astype(float)
    ridge_used = set()

    def fit(current, ew, ps):
        padded = np.vstack((current, np.zeros((1, region_bones))))
        if ps.ndim == 2:
            edge_x = ((padded[local_edges[:, 0]] - padded[local_edges[:, 1]])
                      * np.sqrt(ew)[:, None])
            result = np.zeros((region_bones, target.shape[1]))
            systems = []
            for dim in range(target.shape[1]):
                root = np.sqrt(ps[:, dim] * fit_row)
                x = np.vstack((current * root[:, None], edge_x))
                y = np.concatenate((target[:, dim] * root,
                                    delta[:, dim] * np.sqrt(ew)))
                result[:, dim] = np.linalg.lstsq(x, y, rcond=1e-7)[0]
                systems.append((x, y))
            if translation_cap_ratio is None:
                return result
            # Per-axis design matrices differ (coordinate loss), so bound each
            # key's three axes with one shared ridge to keep direction coherent.
            for i in range(len(keys)):
                cap = key_cap[i]
                block = slice(3 * i, 3 * i + 3)
                if cap is None or _row_norm_max(result[:, block], 3) <= cap:
                    continue

                def solve(lam, i=i):
                    out = np.zeros((region_bones, 3))
                    for axis in range(3):
                        x, y = systems[3 * i + axis]
                        out[:, axis] = np.linalg.solve(x.T @ x + lam * np.eye(region_bones), x.T @ y)
                    return out
                scale = float(np.mean([np.trace(systems[3 * i + a][0].T @ systems[3 * i + a][0])
                                       for a in range(3)])) / region_bones or 1.
                lo, hi = 0., scale * 1e-6
                while _row_norm_max(solve(hi), 3) > cap and hi < scale * 1e12:
                    lo, hi = hi, hi * 4.
                for _ in range(60):
                    mid = (lo + hi) / 2.
                    lo, hi = (mid, hi) if _row_norm_max(solve(mid), 3) > cap else (lo, mid)
                result[:, block] = solve(hi)
                ridge_used.add(keys[i])
            return result
        root = np.sqrt(ps * fit_row)
        x = np.vstack((current * root[:, None],
                       (padded[local_edges[:, 0]] - padded[local_edges[:, 1]]) * np.sqrt(ew)[:, None]))
        y = np.vstack((target * root[:, None], delta * np.sqrt(ew)[:, None]))
        if translation_cap_ratio is None:
            return np.linalg.lstsq(x, y, rcond=1e-7)[0]
        # Same design matrix for every key, so solving key blocks separately is
        # exactly the joint plain solve; each block gets its own bound.
        result = np.zeros((region_bones, y.shape[1]))
        for i in range(len(keys)):
            block = slice(3 * i, 3 * i + 3)
            result[:, block], lam = bounded_lstsq(x, y[:, block], key_cap[i], rcond=1e-7)
            if lam:
                ridge_used.add(keys[i])
        return result

    def sparse_inner(desired, drives, b, capacity):
        quadratic, linear = drives @ drives.T, desired @ drives.T
        chosen = np.zeros((len(b), region_bones))
        best_cost = np.full(len(b), np.inf)
        for count in range(1, min(region_bones, int(capacity.max())) + 1):
            eligible = capacity >= count
            floor = np.minimum(b / count, support_floor)
            remainder = b - count * floor
            for subset in combinations(range(region_bones), count):
                cols = np.asarray(subset)
                kkt = np.zeros((count + 1, count + 1))
                kkt[:count, :count] = quadratic[np.ix_(cols, cols)]
                kkt[:count, count] = kkt[count, :count] = 1.
                rhs = linear[:, cols] - floor[:, None] * quadratic[np.ix_(cols, cols)].sum(axis=0)
                solution = np.column_stack((rhs, remainder)) @ np.linalg.pinv(kkt, rcond=1e-12).T
                values = solution[:, :count]
                valid = (eligible & (values >= -1e-10).all(axis=1)
                         & (np.abs(values.sum(axis=1) - remainder) <= 1e-8))
                values = np.maximum(values, 0.) + floor[:, None]
                cost = np.sum((values @ drives[cols] - desired) ** 2, axis=1)
                selected = np.flatnonzero(valid & (cost < best_cost))
                chosen[selected] = 0.
                chosen[np.ix_(selected, cols)] = values[selected]
                best_cost[selected] = cost[selected]
        if not np.isfinite(best_cost).all():
            raise ValueError('No feasible sparse expression support')
        return chosen

    def sparse_step(desired, drives, metric=None):
        if metric is None:
            return sparse_inner(desired, drives, b, capacity)
        result = np.zeros((len(active), region_bones))
        scales, classes = np.unique(metric, axis=0, return_inverse=True)
        for index, scale in enumerate(scales):
            rows = np.flatnonzero(classes == index)
            root = np.sqrt(scale)
            result[rows] = sparse_inner(desired[rows] * root, drives * root,
                                        b[rows], capacity[rows])
        return result

    current = original[active].copy()
    stages = []
    # First recover lost native fields, then enforce lip continuity from that
    # solution. Starting a strong boundary solve at truncated per-key support
    # produces a poorer local minimum and unnecessarily weakens mouth opening.
    initial_scale = np.ones(len(active)) if coordinate_loss is None else coordinate_loss
    edge_scale = np.where(is_boundary, boundary_weight, edge_weight)
    if coupling:
        edge_scale = np.where(is_coupled, coupling_weight, edge_scale)
    for ew, ps in ((np.zeros(len(edges)), initial_scale), (edge_scale, point_scale)):
        point_metric = ps[:, None] if ps.ndim == 1 else ps
        degree = np.bincount(local_edges.ravel(), weights=np.repeat(ew, 2), minlength=len(active) + 1)[:-1]
        drives = fit(current, ew, ps)
        history = []
        for _ in range(iterations):
            predicted = current @ drives
            padded = np.vstack((predicted, np.zeros((1, predicted.shape[1]))))
            residual = padded[local_edges[:, 0]] - padded[local_edges[:, 1]] - delta
            before = float(np.sum((predicted - target) ** 2 * point_metric) + np.sum(residual ** 2 * ew[:, None]))
            laplacian = np.zeros_like(padded)
            np.add.at(laplacian, local_edges[:, 0], residual * ew[:, None])
            np.add.at(laplacian, local_edges[:, 1], -residual * ew[:, None])
            metric = point_metric + 2 * degree[:, None]
            desired = (point_metric * target + 2 * degree[:, None] * predicted - laplacian[:-1]) / metric
            updated = sparse_step(desired, drives, metric if ps.ndim == 2 else None)
            next_drives = fit(updated, ew, ps)
            next_predicted = updated @ next_drives
            next_padded = np.vstack((next_predicted, np.zeros((1, predicted.shape[1]))))
            next_residual = next_padded[local_edges[:, 0]] - next_padded[local_edges[:, 1]] - delta
            after = float(np.sum((next_predicted - target) ** 2 * point_metric) + np.sum(next_residual ** 2 * ew[:, None]))
            if after > before + max(1e-8, before * 1e-8):
                # The initial source skin can have weights below the chosen
                # export floor; reject an uphill sparse support change.
                break
            current, drives = updated, next_drives
            history.append(after)
            if before - after <= max(1e-10, before * 1e-7):
                break
        stages.append(dict(iterations=len(history), objective_history=history))
    result = original.copy()
    result[active] = current
    total_count = (fixed > 0).sum(axis=1) + (result > 0).sum(axis=1)
    budget_error = float(np.abs(result.sum(axis=1) - budget).max(initial=0))
    if ((total_count > 4).any() or budget_error > 1e-8
            or not np.isfinite(result).all() or not np.isfinite(drives).all()):
        raise ValueError('Shared region fit violates fixed weights or four-influence budget')
    translations = {key: drives[:, i * 3:i * 3 + 3].copy() for i, key in enumerate(keys)}
    report = dict(method='Shared native-key translations with sparse per-vertex simplex weights',
                  stages=stages, keys=keys, proxy_bones=region_bones,
                  active_vertices=len(active), fixed_weight_max_difference=0.,
                  region_budget_max_error=budget_error,
                  maximum_total_positive_influences=int(total_count.max(initial=0)),
                  vertices_over_four=int((total_count > 4).sum()),
                  positive_weights_below_export_floor=int(((result > 0) & (result <= 1e-4)).sum()),
                  boundary_edges=int(is_boundary.sum()), edge_weight=edge_weight,
                  boundary_weight=boundary_weight, boundary_point_weight=boundary_point_weight,
                  coordinate_weighted_vertices=int(np.count_nonzero(np.any(point_weights > 1., axis=1))) if point_weights is not None else 0,
                  coordinate_weight_max=float(point_weights.max()) if point_weights is not None else 1.,
                  coupled_edges=int(is_coupled.sum()), coupling_weight=coupling_weight,
                  translation_cap_ratio=translation_cap_ratio, min_fit_weight=min_fit_weight,
                  ridge_keys=sorted(ridge_used),
                  maximum_translation=_row_norm_max(drives, 3),
                  fields={key: _field_error(result @ translations[key], targets[key],
                                           np.linalg.norm(targets[key], axis=1) > 1e-5) for key in keys})
    return result, translations, report
