"""Generate character-scope ethnicity restoration from vanilla CK3 data.

``script, report = generate_restore_effect(game_directory)`` only reads files and
returns values. It never writes mod files, launches the game, or changes actors.
Exact culture branches reproduce the configured ethnicity distribution, not the
character's original DNA. Heritage branches are explicitly approximate: each
vanilla culture in that heritage contributes equally, using its original table.
Unknown heritages use a validated human ethnicity, Byzantine by default.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re


EFFECT_NAME = "moe_restore_cultural_ethnicity_effect"
DEFAULT_ETHNICITY = "mediterranean_byzantine"
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_LEXER = re.compile(r'\s+|#[^\n]*|"(?:\\.|[^"\\])*"|>=|<=|>|<|[{}=]|[^\s#"{}=<>]+')


@dataclass(frozen=True)
class _Token:
    value: str
    line: int


@dataclass(frozen=True)
class _Entry:
    key: str
    value: str | tuple[_Token, ...]
    line: int


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid script identifier: {value!r}")
    return value


def _tokens(text: str, source: str) -> tuple[_Token, ...]:
    result = []
    end, line = 0, 1
    for match in _LEXER.finditer(text):
        if match.start() != end:
            raise ValueError(f"{source}:{line}: malformed quoted text")
        value = match.group()
        if not value.isspace() and not value.startswith('#'):
            result.append(_Token(value, line))
        line += value.count('\n')
        end = match.end()
    if end != len(text):
        raise ValueError(f"{source}:{line}: unterminated quoted text")
    return tuple(result)


def _entries(tokens: tuple[_Token, ...], source: str) -> list[_Entry]:
    """Read one level; preserve repeated integer weights and nested blocks."""
    result, i = [], 0
    while i < len(tokens):
        key = tokens[i]
        if i + 2 >= len(tokens):
            raise ValueError(f"{source}:{key.line}: expected key = value")
        if tokens[i + 1].value in ('>', '>=', '<', '<='):
            operator, value = tokens[i + 1].value, tokens[i + 2].value
            if value in ('{', '}', '='):
                raise ValueError(f"{source}:{key.line}: invalid comparison value")
            result.append(_Entry(key.value, f"{operator} {value}", key.line))
            i += 3
            continue
        if tokens[i + 1].value != '=':
            raise ValueError(f"{source}:{key.line}: expected key = value")
        i += 2
        # Culture colors may be `color = hsv { ... }`.
        if i + 1 < len(tokens) and tokens[i + 1].value == '{':
            i += 1
        if tokens[i].value == '{':
            start, depth = i + 1, 1
            i += 1
            while i < len(tokens) and depth:
                depth += (tokens[i].value == '{') - (tokens[i].value == '}')
                i += 1
            if depth:
                raise ValueError(f"{source}:{key.line}: unclosed block")
            value = tokens[start:i - 1]
        else:
            value = tokens[i].value
            if value in ('}', '='):
                raise ValueError(f"{source}:{key.line}: invalid scalar")
            if value.startswith('"'):
                value = value[1:-1]
            i += 1
        result.append(_Entry(key.value, value, key.line))
    return result


def _one(entries: list[_Entry], name: str, source: str, *, optional=False):
    matches = [entry.value for entry in entries if entry.key == name]
    if len(matches) == 1:
        return matches[0]
    if not matches and optional:
        return None
    raise ValueError(f"{source}: expected one {name}, found {len(matches)}")


class _Sources:
    def __init__(self):
        self.hashes: dict[str, str] = {}

    def read(self, path: Path) -> list[_Entry]:
        path = path.resolve()
        data = path.read_bytes()
        self.hashes[str(path)] = hashlib.sha256(data).hexdigest()
        return _entries(_tokens(data.decode('utf-8-sig'), str(path)), str(path))

    def definitions(self, folder: Path):
        files = sorted(folder.glob('*.txt'))
        if not files:
            raise ValueError(f"No vanilla definitions found in {folder}")
        definitions = {}
        for path in files:
            for entry in self.read(path):
                if entry.key.startswith('@'):
                    continue
                _identifier(entry.key)
                if not isinstance(entry.value, tuple):
                    raise ValueError(f"{path}:{entry.line}: definition must be a block")
                if entry.key in definitions:
                    raise ValueError(f"Duplicate vanilla definition: {entry.key}")
                definitions[entry.key] = (path, entry, _entries(entry.value, str(path)))
        return definitions


def _weights(entries: list[_Entry], source: str) -> tuple[tuple[int, str], ...]:
    value = _one(entries, 'ethnicities', source)
    if not isinstance(value, tuple) or not value:
        raise ValueError(f"{source}: ethnicities must be a nonempty weight table")
    weights = []
    for entry in _entries(value, source):
        if not re.fullmatch(r'[0-9]+', entry.key) or int(entry.key) <= 0:
            raise ValueError(f"{source}:{entry.line}: ethnicity weight must be a positive integer")
        if not isinstance(entry.value, str):
            raise ValueError(f"{source}:{entry.line}: ethnicity must be a literal identifier")
        weights.append((int(entry.key), _identifier(entry.value)))
    return tuple(weights)


def _weight_script(weights: tuple[tuple[int, str], ...], indent: int) -> list[str]:
    prefix = '\t' * indent
    return [prefix + 'random_list = {', *[
        prefix + f'\t{weight} = {{ set_ethnicity = {ethnicity} }}'
        for weight, ethnicity in weights
    ], prefix + '}']


def generate_restore_effect(
    game_directory: str | Path,
    *,
    effect_name: str = EFFECT_NAME,
    moe_culture: str = 'moe',
    fallback_ethnicity: str = DEFAULT_ETHNICITY,
) -> tuple[str, dict]:
    """Return (script, JSON-serializable validation report), with no file writes.

    ``game_directory`` is the vanilla game's ``game`` folder, not a mod or merged
    virtual filesystem. A third-party override of the same culture/ethnicity key
    is outside this validation. ``fallback_ethnicity`` must exist in vanilla and
    resolve to human; it is used for moe and unknown heritage. Template cycles,
    missing references, nonhuman groups and malformed weights fail explicitly.
    """
    for value in (effect_name, moe_culture, fallback_ethnicity):
        _identifier(value)
    game = Path(game_directory).resolve()
    sources = _Sources()
    default_path = game.parent / 'jomini/common/defines/jomini/portraits.txt'
    default_entries = sources.read(default_path)
    portrait_defines = _one(default_entries, 'NJominiPortraits', str(default_path))
    if not isinstance(portrait_defines, tuple):
        raise ValueError('NJominiPortraits must be a block')
    default_group = _one(_entries(portrait_defines, str(default_path)),
                         'DEFAULT_PORTRAIT_GROUP', str(default_path))
    if default_group != 'human':
        raise ValueError(f"Vanilla default portrait group is not human: {default_group!r}")
    # A game-specific graphics override must not silently change the default.
    for path in sorted((game / 'common/defines/graphic').glob('*.txt')):
        for entry in sources.read(path):
            if entry.key == 'NJominiPortraits' and isinstance(entry.value, tuple):
                override = _one(_entries(entry.value, str(path)), 'DEFAULT_PORTRAIT_GROUP',
                                str(path), optional=True)
                if override is not None and override != 'human':
                    raise ValueError(f"{path}: nonhuman default portrait group override")
    groups = sources.definitions(game / 'common/portrait_types')
    if 'human' not in groups:
        raise ValueError('Vanilla human portrait group is missing')
    ethnicities = sources.definitions(game / 'common/ethnicities')
    cultures = sources.definitions(game / 'common/culture/cultures')
    if moe_culture in cultures:
        raise ValueError(f"Custom moe culture conflicts with vanilla: {moe_culture}")
    validated = {}

    def validate_ethnicity(name: str, ancestors: tuple[str, ...] = ()):
        if name in ancestors:
            raise ValueError(f"Ethnicity template cycle: {' -> '.join((*ancestors, name))}")
        if name in validated:
            return validated[name]['template_chain']
        if name not in ethnicities:
            raise ValueError(f"Missing vanilla ethnicity/template: {name}")
        path, entry, fields = ethnicities[name]
        group = _one(fields, 'portrait_group', str(path), optional=True)
        if group not in (None, 'human'):
            raise ValueError(f"{path}:{entry.line}: nonhuman ethnicity/template {name}: {group}")
        template = _one(fields, 'template', str(path), optional=True)
        chain = [name]
        if template is not None:
            if not isinstance(template, str):
                raise ValueError(f"{path}:{entry.line}: template must be a literal identifier")
            chain += validate_ethnicity(_identifier(template), (*ancestors, name))
        validated[name] = dict(path=str(path), line=entry.line, explicit_group=group,
                               effective_group='human', template_chain=chain)
        return chain

    validate_ethnicity(fallback_ethnicity)
    rows = []
    by_heritage = defaultdict(list)
    for name, (path, entry, fields) in sorted(cultures.items()):
        heritage = _one(fields, 'heritage', str(path))
        if not isinstance(heritage, str):
            raise ValueError(f"{path}:{entry.line}: heritage must be a literal identifier")
        _identifier(heritage)
        weights = _weights(fields, f'{path}:{entry.line} ({name})')
        for _, ethnicity in weights:
            validate_ethnicity(ethnicity)
        row = dict(culture=name, heritage=heritage, weights=weights, path=str(path), line=entry.line)
        rows.append(row)
        by_heritage[heritage].append(row)

    lines = [
        '# Generated from vanilla culture definitions; CHARACTER scope only.',
        '# Exact culture branches preserve source weights, not original character DNA.',
        '# Heritage branches approximate: each source culture has equal weight.',
        '# Unknown heritage uses a validated human fallback, not an exact restoration.',
        f'{effect_name} = {{', '\tif = {', f'\t\tlimit = {{ culture = culture:{moe_culture} }}',
        f'\t\tset_ethnicity = {fallback_ethnicity}', '\t}',
    ]
    for row in rows:
        lines.extend(['\telse_if = {', f"\t\tlimit = {{ culture = culture:{row['culture']} }}",
                      *_weight_script(row['weights'], 2), '\t}'])
    heritage_report = {}
    for heritage, members in sorted(by_heritage.items()):
        # Group identical source tables; count keeps every source culture equally
        # weighted. Nested random_list avoids rounding or rescaling source weights.
        tables = Counter(row['weights'] for row in members)
        lines.extend(['\telse_if = {',
                      f'\t\tlimit = {{ culture = {{ has_cultural_pillar = {heritage} }} }}',
                      '\t\t# Approximate fallback for dynamic/unknown cultures of this heritage.'])
        if len(tables) == 1:
            lines.extend(_weight_script(next(iter(tables)), 2))
        else:
            lines.append('\t\t# Different vanilla cultures have different ethnicity distributions.')
            lines.append('\t\trandom_list = {')
            for weights, count in sorted(tables.items()):
                lines.extend([f'\t\t\t{count} = {{', *_weight_script(weights, 4), '\t\t\t}'])
            lines.append('\t\t}')
        lines.append('\t}')
        heritage_report[heritage] = dict(
            approximate=True, source_cultures=[row['culture'] for row in members],
            distinct_tables=len(tables),
            tables=[dict(culture_weight=count, weights=weights) for weights, count in sorted(tables.items())])
    lines.extend(['\telse = {', '\t\t# Unknown heritage: human fallback only; no claim of cultural/DNA accuracy.',
                  f'\t\tset_ethnicity = {fallback_ethnicity}', '\t}', '}', ''])
    script = '\n'.join(lines)
    report = dict(
        status='validated_vanilla_culture_ethnicity_restore_script', effect_name=effect_name,
        character_scope=True, culture_count=len(rows), heritage_count=len(by_heritage),
        referenced_ethnicity_count=len({ethnicity for row in rows for _, ethnicity in row['weights']}),
        default_portrait_group=default_group, all_referenced_template_chains_human=True,
        exact_cultures=rows, heritage_fallbacks=heritage_report,
        non_unique_heritages=[key for key, value in heritage_report.items() if value['distinct_tables'] > 1],
        non_unique_heritage_count=sum(value['distinct_tables'] > 1 for value in heritage_report.values()),
        heritage_method='Equal weight per vanilla culture, then its unchanged integer ethnicity table; identical tables grouped by culture count.',
        moe_culture=moe_culture, moe_ethnicity=fallback_ethnicity,
        unknown_heritage_fallback=fallback_ethnicity, validated_ethnicities=validated,
        source_hashes=sources.hashes, script_sha256=hashlib.sha256(script.encode('utf8')).hexdigest(),
        limitations=[
            'Recreates configured culture distributions; does not restore a previously saved character DNA.',
            'Heritage fallback is approximate even when all current vanilla member tables agree.',
            'Dynamic hybrids may have mixed ethnicity distributions not represented by their heritage.',
            'Vanilla files only: third-party overrides of the same keys require separate compatibility validation.',
            'Caller must restrict when restoration runs; this module does not change eligibility, flags or events.',
        ])
    return script, report
