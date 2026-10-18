"""Persistent source physics recipes with isolated per-action native options.

Recipes live in this repository. Missing character recipes preserve the normal
bake defaults. Explicit experiments set cfg['release_recipe'] = False, and pass
enabled=False below, to opt out of both source and solver defaults.
"""
import copy
import json
import math
from pathlib import Path
import re

import physics_profile


RECIPE_DIRECTORY = Path(__file__).resolve().parent / 'release_recipes'
_UNSET = object()
_CHARACTER = re.compile(r'^[a-z][a-z0-9_]{1,15}$')
_ACTION = re.compile(r'^[A-Za-z][A-Za-z0-9_]*$')
_NUMBERS = {
    'wind': (0., 1000.),
    'physx_contact_frequency_hz': (0., 1000.),
    'physx_contact_damping_ratio': (0., 10.),
    'physx_angular_limit_hz': (0., 1000.),
    'physx_max_depenetration_velocity': (.000001, 1000.),
    'physx_iters': (1, 255),
    'physx_velocity_iters': (0, 255),
}
_INTEGERS = {'physx_iters', 'physx_velocity_iters'}
_BOOLEANS = {'physx_disable_preprocessing', 'physx_external_forces_every_iteration'}


def _character(character):
    if not isinstance(character, str) or not _CHARACTER.fullmatch(character):
        raise ValueError('Recipe character must be a simple registry identifier')


def _solver(values):
    if not isinstance(values, dict):
        raise ValueError('Native option overrides must be a dictionary')
    for key, value in values.items():
        if key in _NUMBERS:
            low, high = _NUMBERS[key]
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not low <= value <= high):
                raise ValueError(f'Invalid native recipe option {key}')
            if key in _INTEGERS and type(value) is not int:
                raise ValueError(f'{key} must be an integer')
        elif key in _BOOLEANS:
            if type(value) is not bool:
                raise ValueError(f'{key} must be a boolean')
        else:
            raise ValueError(f'Unsupported native recipe option {key}')


def validate(recipe, character):
    """Validate a recipe completely before exposing any mutable profile data."""
    _character(character)
    if not isinstance(recipe, dict):
        raise ValueError('Release recipe must be a dictionary')
    allowed = {'version', 'character', 'source_profile', 'native_defaults',
               'action_solver_overrides', 'evidence'}
    if set(recipe) - allowed or type(recipe.get('version')) is not int or recipe['version'] != 1:
        raise ValueError('Unknown release recipe field or version')
    if recipe.get('character') != character:
        raise ValueError('Release recipe belongs to a different character')
    profile = recipe.get('source_profile')
    if profile is not None:
        physics_profile.validate(profile, character)
    _solver(recipe.get('native_defaults', {}))
    actions = recipe.get('action_solver_overrides', {})
    if not isinstance(actions, dict):
        raise ValueError('Action overrides must be a dictionary keyed by exact action')
    for action, values in actions.items():
        if not isinstance(action, str) or not _ACTION.fullmatch(action):
            raise ValueError('Action overrides require exact unprefixed action identifiers')
        _solver(values)
    if 'evidence' in recipe and not isinstance(recipe['evidence'], dict):
        raise ValueError('Recipe evidence must be metadata, not a runtime dependency')
    return recipe


def load(character):
    """Load only the fixed repository recipe path; a missing file is a no-op."""
    _character(character)
    path = RECIPE_DIRECTORY / f'{character}.json'
    if not path.is_file():
        return None
    return validate(json.loads(path.read_text(encoding='utf-8')), character)


def _resolve(character, recipe):
    if recipe is _UNSET:
        return load(character)
    return validate(recipe, character) if recipe is not None else None


def source_profile(character, *, recipe=_UNSET):
    """Return an independent source profile; explicit recipe=None disables it."""
    selected = _resolve(character, recipe)
    return copy.deepcopy(selected.get('source_profile')) if selected is not None else None


def options_for_action(character, action, options, *, recipe=_UNSET, enabled=True):
    """Clone options before applying an exact-action native override.

    Neither the caller's Namespace nor a loaded recipe is mutated. Source-only
    Bullet baking never receives PhysX options. enabled=False skips loading and
    validating a recipe, providing a strict opt-out for isolated experiments.
    """
    result = copy.deepcopy(options)
    if not enabled or getattr(options, 'backend', 'bullet') == 'bullet':
        return result
    selected = _resolve(character, recipe)
    if selected is None:
        return result
    if not isinstance(action, str) or not _ACTION.fullmatch(action):
        raise ValueError('Expected an exact unprefixed action identifier')
    for key, value in selected.get('native_defaults', {}).items():
        setattr(result, key, copy.deepcopy(value))
    for key, value in selected.get('action_solver_overrides', {}).get(action, {}).items():
        setattr(result, key, copy.deepcopy(value))
    return result


def partition_actions(character, named_items, options, *, recipe=_UNSET, enabled=True):
    """Group source items by effective native options, in first-seen order.

    Returns [(independent Namespace, [original item, ...]), ...]. Callers chunk
    each group to their batch size. Actions with distinct native parameters can
    never enter the same native command or share a mutated options object.
    """
    named_items = list(named_items)
    if not enabled or getattr(options, 'backend', 'bullet') == 'bullet':
        return [(copy.deepcopy(options), [item for _, item in named_items])] if named_items else []
    selected = _resolve(character, recipe)
    groups = []
    lookup = {}
    for action, item in named_items:
        effective = options_for_action(character, action, options, recipe=selected)
        keys = _NUMBERS.keys() | _BOOLEANS
        signature = tuple((key, getattr(effective, key, None)) for key in sorted(keys))
        if signature not in lookup:
            lookup[signature] = len(groups)
            groups.append((effective, []))
        groups[lookup[signature]][1].append(item)
    return groups
