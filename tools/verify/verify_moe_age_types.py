"""Audit age-aware portrait type output without starting the game."""
from __future__ import annotations

import json
import re
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODS = ROOT / "mod"
CHILD_SCALE = "0.75"
import sys
sys.path.insert(0, str(ROOT / "tools" / "lib"))
import moe_chars as X


def entity_end(text: str, start: int) -> int:
    opening = text.find("{", start)
    if opening < 0:
        raise ValueError("entity has no opening brace")
    depth = 0
    quoted = escaped = comment = False
    for i in range(opening, len(text)):
        char = text[i]
        if comment:
            if char == "\n":
                comment = False
            continue
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == "#":
            comment = True
        elif char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    raise ValueError("unclosed entity block")


def find_entity(text: str, name: str) -> str:
    match = re.search(
        r'(?m)^entity\s*=\s*\{\s*\n\s*name\s*=\s*"'
        + re.escape(name)
        + r'"',
        text,
    )
    if not match:
        raise AssertionError("missing entity " + name)
    return text[match.start():entity_end(text, match.start())]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report", type=Path,
        default=ROOT / "work/evidence/ratkin_reference_20260924/child_portrait_validation.json",
        help="write the audit report to this path",
    )
    args = parser.parse_args()

    groups = []
    checks = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            raise AssertionError(name)
        checks.append(name)

    assets = {
        path.parts[len(MODS.parts)]: path
        for path in MODS.glob("moe_models_*/gfx/models/portraits/moe/moe_portrait_*.asset")
    }
    asset_text = {name: path.read_text(encoding="utf-8-sig") for name, path in assets.items()}
    type_files = sorted(MODS.glob("moe_models_*/common/portrait_types/10_moe_types_*.txt"))
    expected_groups = len({character["type"] for character in X.active()})
    check("all_active_model_groups_have_generated_types", len(type_files) == expected_groups)

    animation_path = MODS / "moe_core/gfx/portraits/portrait_animations/animations.txt"
    animation = animation_path.read_text(encoding="utf-8-sig")
    alias_pairs = 0

    for path in type_files:
        text = path.read_text(encoding="utf-8-sig")
        group_match = re.search(r"(?m)^([A-Za-z0-9_]+_g)\s*=\s*\{", text)
        if not group_match:
            raise AssertionError("missing portrait group in " + str(path))
        group = group_match.group(1)
        block_start = group_match.start()
        group_text = text[block_start:entity_end(text, block_start)]
        adult_match = re.search(r"(?m)^\t(moe_[A-Za-z0-9_]+)\s*=\s*\{\s*\n\s*sex\s*=\s*(female|male)", group_text)
        if not adult_match:
            raise AssertionError("missing adult type in " + str(path))
        character = X.by_type(adult_match.group(1))
        check("first_type_is_registry_type_" + adult_match.group(1), character is not None)
        expected = X.portrait_types(character)
        declared = re.findall(r"(?m)^\t(moe_[A-Za-z0-9_]+)\s*=\s*\{", group_text)
        check("group_has_exactly_four_sex_age_types_" + group, declared == [name for name, _s, _a in expected])
        check("group_covers_both_sexes_and_ages_" + group,
              {(sex, age) for _n, sex, age in expected} == {(s_, a_) for s_ in ("female", "male") for a_ in ("adult", "child")})
        mod_name = path.parts[len(MODS.parts)]
        corresponding = next((key for key in asset_text if key == mod_name), None)
        if corresponding is None:
            raise AssertionError("missing model asset for " + mod_name)
        entities = {}
        for type_name, type_sex, age in expected:
            start = re.search(r"(?m)^\t" + re.escape(type_name) + r"\s*=\s*\{", group_text).start()
            block = group_text[start:group_text.find("\n\t}", start) + len("\n\t}")]
            check("type_sex_" + type_name, "sex = " + type_sex in block)
            if age == "adult":
                check("adult_minimum_age_" + type_name, "minimum_age = 18" in block and "maximum_age" not in block)
            else:
                check("child_maximum_age_" + type_name, "maximum_age = 18" in block and "minimum_age" not in block)
            head = re.search(r'head\s*=\s*"([^"]+)"', block).group(1)
            torso = re.search(r'torso\s*=\s*"([^"]+)"', block).group(1)
            entities.setdefault(age, set()).add((head, torso))
        # Opposite-sex types reuse the same head/torso entities as the own-sex ones.
        check("opposite_sex_types_share_entities_" + group,
              all(len(pairs) == 1 for pairs in entities.values()))
        (child_head, child_torso), = entities["child"]
        for part, entity_name in (("head", child_head), ("torso", child_torso)):
            block = find_entity(asset_text[corresponding], entity_name)
            check("child_entity_scale_" + entity_name, re.search(r"(?m)^\tscale = " + CHILD_SCALE + r"\s*$", block) is not None)
            expected_mesh = entity_name.removesuffix("_entity_child") + "_mesh"
            check("child_entity_uses_correct_mesh_" + entity_name,
                  f'pdxmesh = "{expected_mesh}"' in block)
            adult_entity = entity_name.removesuffix("_child")
            adult_block = find_entity(asset_text[corresponding], adult_entity)
            adult_anims = re.findall(r'(?m)^\tstate\s*=\s*\{[^\n]*animation\s*=\s*"([^"]+)"', adult_block)
            child_anims = re.findall(r'(?m)^\tstate\s*=\s*\{[^\n]*animation\s*=\s*"([^"]+)"', block)
            check("child_entity_preserves_animation_states_" + entity_name,
                  bool(adult_anims) and child_anims == adult_anims)
        adult = adult_match.group(1)
        aliases = re.findall(r"(?m)^\t" + re.escape(adult) + r"\s*=\s*(male|female)\s*$", animation)
        for type_name, _sex, _age in expected[1:]:
            other = re.findall(r"(?m)^\t" + re.escape(type_name) + r"\s*=\s*(male|female)\s*$", animation)
            check("animation_aliases_match_adult_" + type_name, len(aliases) == 2 and other == aliases)
            alias_pairs += len(other)
        props_gene = MODS / mod_name / f"common/genes/42_moe_genes_props_{adult[len('moe_'):]}.txt"
        if props_gene.is_file():
            gene_text = props_gene.read_text(encoding="utf-8-sig")
            for type_name, _sex, _age in expected[1:]:
                check("props_gene_covers_" + type_name,
                      gene_text.count(f"\t{type_name} = {adult}\n") == gene_text.count(f"\t{adult} = {{"))
        child = expected[1][0]
        groups.append({"group": group, "adult_type": adult, "child_type": child,
                       "model_mod": mod_name})

    # Qualification stays age-neutral; the engine selects child-specific model
    # geometry for under-18 characters.
    triggers = (MODS / "moe_core/common/scripted_triggers/40_moe_cosplay.txt").read_text(encoding="utf-8-sig")
    eligible = re.search(r"moe_cosplay_eligible\s*=\s*\{([^}]+)\}", triggers, re.S)
    check("cosplay_eligibility_has_no_sex_filter", eligible is not None and "is_female" not in eligible.group(1)
          and "is_male" not in eligible.group(1))
    check("cosplay_eligibility_has_no_age_filter", eligible is not None and "age" not in eligible.group(1))
    check("all_three_extra_type_aliases_present_in_two_native_animation_blocks",
          alias_pairs == 3 * 2 * expected_groups)

    report = {"status": "passed_offline_age_type_output_checks", "checks": len(checks),
              "groups": groups, "actual_game_loaded": False,
              "types_per_group": 4,
              "limitations": ["Current uniformly scaled child meshes need body-proportion correction.",
                              "CK3 runtime selection, framing, attachment, and child proportions remain unverified."]}
    out = args.report if args.report.is_absolute() else ROOT / args.report
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "check_count": len(checks),
                      "groups": len(groups), "actual_game_loaded": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
