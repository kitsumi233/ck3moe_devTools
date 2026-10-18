"""Exercise generated character reconciliation rules without mutating game saves.

This interprets the small supported effect/trigger subset, not the CK3 engine.
It validates lifecycle behavior and emitted guards; runtime loading remains a
separate acceptance step.
"""
import copy
import argparse
import hashlib
import json
import re
import math
from dataclasses import dataclass, field
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools/lib'))
import moe_cosplay_rules as rules
from moe_culture_ethnicities import _entries, _tokens

GAME = Path('E:/SteamLibrary/steamapps/common/Crusader Kings III/game')
BOM = bytes((0xEF, 0xBB, 0xBF))


def entries(text):
    return _entries(_tokens(text, 'generated_rules'), 'generated_rules')


def nested(value):
    return _entries(value, 'generated_rules')


def values(block):
    return {entry.key:entry.value for entry in nested(block)}


def verify_tradition_bonuses(generated, check):
    """Check native-field preservation and actual amenity price dependencies."""
    def semantic(rows):
        return [(r.key, tuple(t.value for t in r.value) if isinstance(r.value, tuple) else r.value) for r in rows]

    tradition = values(entries(generated[rules.TRADITION_REL])[0].value)
    params = values(tradition['parameters'])
    check('fertility_applies_as_native_cultural_character_modifier', values(tradition['character_modifier']) == {'fertility':'0.05'})
    check('both_bonus_parameters_enabled', all(params.get(k) == 'yes' for k in (rules.BEAUTY_PARAMETER, rules.COURT_COST_PARAMETER)))
    native_traits = {r.key:r.value for r in entries((GAME/rules.BEAUTY_SOURCE).read_text('utf-8-sig'))}
    traits = entries(generated[rules.BEAUTY_OUTPUT])
    check('only_highest_beauty_trait_extended', [r.key for r in traits] == ['beauty_good_3'])
    beauty = nested(traits[0].value)
    check('all_native_beauty_fields_preserved', semantic(beauty[:-1]) == semantic(nested(native_traits['beauty_good_3'])))
    check('beauty_uses_native_culture_modifier', beauty[-1].key == 'culture_modifier' and
          values(beauty[-1].value) == {'parameter':rules.BEAUTY_PARAMETER, 'attraction_opinion':'5'})
    # Native culture modifiers require both the trait and its cultural parameter.
    for enabled in (False, True):
        culture_params = params if enabled else {}
        for tier in range(4):
            for female in (False, True):
                modifiers = beauty if tier == 3 else nested(native_traits[f'beauty_good_{tier}']) if tier else []
                extra = sum(float(values(r.value).get('attraction_opinion', 0)) for r in modifiers
                            if r.key == 'culture_modifier' and culture_params.get(values(r.value)['parameter']) == 'yes')
                check(f'beauty_bonus_culture_{enabled}_tier_{tier}_female_{female}', extra == (5 if enabled and tier == 3 else 0))
    native_costs = {r.key:r.value for r in entries((GAME/rules.COURT_COST_SOURCE).read_text('utf-8-sig'))}
    overrides = {r.key:r.value for r in entries(generated[rules.COURT_COST_OUTPUT])}
    check('only_four_fashion_costs_extended', set(overrides) == set(rules.FASHION_COSTS))
    for key, block in overrides.items():
        rows = nested(block)
        check('native_fashion_cost_and_discount_preserved_'+key, semantic(rows[:-1]) == semantic(nested(native_costs[key])))
        check('surcharge_after_native_fashion_discount_'+key, rows[-1].key == 'if')

    def condition(rows, context):
        results = []
        for row in rows:
            k, v = row.key, row.value
            if k in ('culture', 'AND', 'NOT'):
                result = condition(nested(v), context)
                if k == 'NOT': result = not result
            elif k == 'OR': result = any(condition([r], context) for r in nested(v))
            elif k == 'has_cultural_parameter': result = v in context['parameters']
            elif k == 'government_has_flag':
                assert v == 'government_is_tribal'
                result = context['tribal']
            elif k in ('has_character_flag', 'has_character_modifier'): result = v in context['benefits']
            else: raise AssertionError(('Unsupported cost condition', k, v))
            results.append(result)
        return all(results)

    def cost(key, definitions, context):
        if key == 'base_court_amenities_cost': return context['base']
        try: return float(key)
        except ValueError: pass
        return calculate(nested(definitions[key]), definitions, context)

    def calculate(rows, definitions, context, total=0):
        branch_taken = False
        for row in rows:
            k, v = row.key, row.value
            if k in ('if', 'else_if', 'else'):
                if k == 'if': branch_taken = False
                parts = nested(v)
                matches = k == 'else' or condition(nested(values(v)['limit']), context)
                if not branch_taken and matches:
                    total = calculate([r for r in parts if r.key != 'limit'], definitions, context, total)
                    branch_taken = True
                continue
            number = cost(v, definitions, context)
            if k == 'value': total = number
            elif k == 'add': total += number
            elif k == 'multiply': total *= number
            elif k == 'divide': total /= number
            elif k == 'min': total = max(total, number)  # CK3 min sets the lower bound.
            else: raise AssertionError(('Unsupported cost operation', k))
        return total

    # Follow both gold and treasury entries from all actual vanilla amenity settings.
    amenities = entries((GAME/'common/court_amenities/00_court_amenities.txt').read_text('utf-8-sig'))
    endpoints = set()
    for category in amenities:
        for setting in nested(category.value):
            if not isinstance(setting.value, tuple): continue
            data = values(setting.value)
            if 'cost' in data:
                endpoints.update(t.value for t in data['cost'] if t.value in native_costs)
    check('all_sixteen_amenity_costs_found', len(endpoints) == 16)
    benefits = [set(), {'court_free_fashion', 'court_free_food', 'court_free_lodgings', 'court_free_servants'},
                {'court_cheaper_food','court_cheaper_servants','rivers_of_wine_modifier',
                 'lodgings_epidemics_resistance_modifier_2','employer_booner_food_taster_3_modifier',
                 'employer_booner_seneschal_3_modifier','employer_booner_cupbearer_3_modifier'}]
    combined = native_costs | overrides
    for base in (0.01, 20, 500):
        for tribal in (False, True):
            for discount, benefit in enumerate(benefits):
                context = dict(base=base, tribal=tribal, benefits=benefit,
                               parameters={'reduces_court_fashion_costs_parameter'})
                for key in sorted(endpoints):
                    vanilla = cost(key, native_costs, context)
                    check(f'cost_unchanged_without_tradition_{key}_{base}_{tribal}_{discount}',
                          math.isclose(cost(key, combined, context), vanilla, abs_tol=1e-10))
                    enabled = context | {'parameters':context['parameters'] | {rules.COURT_COST_PARAMETER}}
                    factor = 1.05 if key in rules.FASHION_COSTS else 1
                    check(f'fashion_only_five_percent_{key}_{base}_{tribal}_{discount}',
                          math.isclose(cost(key, combined, enabled), vanilla * factor, abs_tol=1e-10))
    # Adopting/removing the tradition changes native parameter evaluation immediately;
    # neither bonus leaves behind an event-applied character modifier.
    check('bonuses_need_no_persistent_modifier_or_polling', 'add_character_modifier' not in '\n'.join(generated.values()))


@dataclass
class Character:
    char_id: int = 1
    female: bool = True
    age: int = 25
    beauty: bool = True
    culture: str = 'han'
    tradition: bool = True
    flags: set = field(default_factory=set)
    parents: list = field(default_factory=list)
    queued: list = field(default_factory=list)
    group: str = 'human'
    restores: int = 0
    poses_cleared: bool = False
    variables: dict = field(default_factory=dict)


class Evaluator:
    def __init__(self, generated):
        self.triggers = {}
        self.effects = {}
        for rel, text in generated.items():
            if rel.startswith('common/scripted_triggers/'):
                self.triggers.update({r.key:r.value for r in entries(text)})
            elif rel.startswith('common/scripted_effects/'):
                self.effects.update({r.key:r.value for r in entries(text)})
        self.globals = set()

    def condition(self, rows, who):
        results = []
        for row in rows:
            k, v = row.key, row.value
            if k in self.triggers:
                result = self.condition(nested(self.triggers[k]), who)
                result = result if v == 'yes' else not result
            elif k in ('AND', 'NOT'):
                result = self.condition(nested(v), who)
                if k == 'NOT': result = not result
            elif k == 'OR': result = any(self.condition([r], who) for r in nested(v))
            elif k == 'any_parent': result = any(self.condition(nested(v), p) for p in who.parents)
            elif k == 'is_female': result = who.female == (v == 'yes')
            elif k == 'is_male': result = (not who.female) == (v == 'yes')
            elif k == 'age': result = who.age >= int(v.split()[1]) if v.startswith('>= ') else False
            elif k == 'has_trait':
                assert v == 'beauty_good_3'
                result = who.beauty
            elif k == 'culture':
                result = self.condition(nested(v), who) if isinstance(v, tuple) else v == 'culture:'+who.culture
            elif k == 'has_cultural_tradition':
                assert v == rules.TRADITION
                result = who.tradition
            elif k == 'has_character_flag': result = v in who.flags
            elif k == 'has_global_variable': result = v in self.globals
            elif k == 'always': result = v == 'yes'
            elif k == 'exists':
                assert v.startswith('character:')
                result = True  # Preview IDs exist in this synthetic history.
            elif k == 'this': result = v == f'character:{who.char_id}'
            else: raise AssertionError(('Unsupported condition', k, v))
            results.append(result)
        return all(results)

    def effect(self, rows, who):
        branch_taken = False
        for row in rows:
            k, v = row.key, row.value
            if k in ('if', 'else_if', 'else'):
                if k == 'if': branch_taken = False
                parts = nested(v)
                limits = [r for r in parts if r.key == 'limit']
                matches = k == 'else' or self.condition(nested(limits[0].value), who)
                if not branch_taken and matches:
                    self.effect([r for r in parts if r.key != 'limit'], who)
                    branch_taken = True
            elif k == 'add_character_flag':
                who.flags.add(values(v)['flag'] if isinstance(v, tuple) else v)
            elif k == 'remove_character_flag': who.flags.discard(v)
            elif k == 'set_variable':
                d = values(v); who.variables[d['name']] = d['value']
            elif k == 'trigger_event': who.queued.append(values(v))
            elif k == 'moe_restore_cultural_ethnicity_effect':
                # Culture tables are independently verified by their generator.
                who.group = 'human'; who.restores += 1
            elif k == 'moe_clear_pose_flags': who.poses_cleared = True
            elif k == 'debug_log': pass
            elif k in self.effects: self.effect(nested(self.effects[k]), who)
            else: raise AssertionError(('Unsupported effect', k, v))

    def eligible(self, who):
        return self.condition(nested(self.triggers[rules.ELIGIBLE]), who)

    def sync(self, who):
        self.effect(nested(self.effects['moe_sync_portrait_effect']), who)


def configurations():
    """(name, generated files) for core alone and core + moe_cultural_units.

    The package files replace core files at the same relative path, exactly like
    the CK3 VFS does when the package loads after core.
    """
    import moe_cultural_units as CU
    core = rules.files(GAME)
    culture = (ROOT/'mod/moe_core'/CU.CULTURE_REL).read_text(encoding='utf-8-sig')
    # Offline mode must not depend on whether the core product was rebuilt yet;
    # --built checks the real core file (which must not list them) separately.
    for tradition in CU.TRADITIONS:
        culture = re.sub(r'\n\s*' + tradition + r'\b', '', culture)
    pack = CU.files(GAME, culture, {'english': 'poses', 'simp_chinese': 'poses'})
    return core, pack, {**core, **pack}


TRADITION_TOKENS = (rules.TRADITION, rules.HUMANOID_MERCENARIES_TRADITION, rules.COURT_COST_PARAMETER,
                    rules.BEAUTY_PARAMETER, 'moe_cosplay_portraits', 'unlock_maa_moe_ratkin',
                    'has_cultural_tradition', 'on_tradition_added', 'on_tradition_removed',
                    'moe_humanoid_mercenaries.dds', 'moe_cosplay_identity.dds')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--built', action='store_true',
                        help='also check the real build output under mod/ (run after build_moe_mod.py)')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    core, pack, combined = configurations()
    checks = []

    def check(name, predicate):
        assert predicate, name
        checks.append(name)

    # ---- core must be self-contained: no custom tradition, parameter or tradition hook
    for rel, text in core.items():
        hits = [t for t in TRADITION_TOKENS if t in text]
        check('core_has_no_tradition_reference_'+rel, not hits)
    check('core_culture_gate_is_unconditional',
          values(entries(core[rules.CULTURE_OK_REL])[0].value) == {'always': 'yes'})
    check('pack_culture_gate_requires_tradition',
          'has_cultural_tradition = '+rules.TRADITION in pack[rules.CULTURE_OK_REL])
    check('pack_replaces_core_gate_at_same_path', rules.CULTURE_OK_REL in core and rules.CULTURE_OK_REL in pack)
    check('pack_replaces_core_migration_at_same_path', rules.MIGRATION_REL in core and rules.MIGRATION_REL in pack
          and 'add_culture_tradition' in pack[rules.MIGRATION_REL] and 'add_culture_tradition' not in core[rules.MIGRATION_REL])
    check('pack_owns_tradition_hooks', 'on_tradition_added' in pack[rules.CULTURE_HOOKS_REL]
          and 'on_tradition_removed' in pack[rules.CULTURE_HOOKS_REL])
    check('pack_owns_both_tradition_definitions', rules.TRADITION_REL in pack and rules.HUMANOID_MERCENARIES_REL in pack)
    for lang in ('english', 'simp_chinese'):
        loc = pack[rules.TRADITION_LOC_REL.format(lang=lang)]
        for t in (rules.TRADITION, rules.HUMANOID_MERCENARIES_TRADITION):
            check(f'pack_tradition_localized_{lang}_{t}', f' {t}_name:0 ' in loc and f' {t}_desc:0 ' in loc)
        for key in ('moe_cosplay_portraits', rules.COURT_COST_PARAMETER, rules.BEAUTY_PARAMETER,
                    'unlock_maa_moe_ratkin_wandering_knights', 'unlock_maa_moe_ratkin_muster_levies'):
            check(f'pack_parameter_localized_{lang}_{key}', f' culture_parameter_{key}:0 ' in loc)
        replace = pack[rules.WELCOME_REPLACE_REL.format(lang=lang)]
        check('pack_welcome_replace_uses_core_key_'+lang, f' {rules.WELCOME_KEY}:0 "' in replace)
        core_welcome = rules.welcome_description(lang, 0, 'poses')
        pack_welcome = rules.welcome_description(lang, 0, 'poses', culture_pack=True)
        name = 'Cosplay Identity' if lang == 'english' else 'cosplay认同'
        check('core_welcome_never_mentions_tradition_'+lang, name not in core_welcome and 'tradition' not in core_welcome.lower()
              and '传统' not in core_welcome)
        check('pack_welcome_states_tradition_requirement_'+lang, name in pack_welcome)
        for text, label in ((core_welcome, 'core'), (pack_welcome, 'pack')):
            if lang == 'english':
                ok = all(x in text for x in ('men and women', 'no minimum age', 'Ignore Gender', 'own sex'))
            else:
                ok = all(x in text for x in ('不分男女', '未满18岁同样可以', '忽略性别', '同性别'))
            check(f'{label}_welcome_states_sex_age_and_switch_{lang}', ok)

    e_core, e_pack = Evaluator(core), Evaluator(combined)
    # ---- eligibility: beauty only (core) / beauty + tradition (pack); sex and age never matter
    for culture in ('moe', 'han', 'norse', 'dynamic_hybrid'):
        for tradition in (True, False):
            for age in (0, 7, 17, 18, 70):
                for female in (True, False):
                    for beauty in (True, False):
                        c = Character(culture=culture, age=age, female=female, beauty=beauty, tradition=tradition)
                        tag = f'{culture}_{tradition}_{age}_{"f" if female else "m"}_{beauty}'
                        check('core_eligible_'+tag, e_core.eligible(c) == beauty)
                        check('pack_eligible_'+tag, e_pack.eligible(c) == (beauty and tradition))
    for e, label in ((e_core, 'core'), (e_pack, 'pack')):
        for female in (True, False):
            c = Character(female=female)
            e.sync(c)
            check(f'{label}_eligible_{"female" if female else "male"}_requests_same_sex_pool',
                  c.queued == [{'on_action': 'moe_pick_model'}])
            e.sync(c)
            check(f'{label}_pending_does_not_reroll_{female}', len(c.queued) == 1)

    # ---- manual wardrobe sex triggers and the global switch
    for e, label in ((e_core, 'core'), (e_pack, 'pack')):
        for switch in (False, True):
            e.globals = {rules.IGNORE_GENDER} if switch else set()
            for model_sex in ('female', 'male'):
                trig = nested(e.triggers[rules.sex_ok(model_sex)])
                for female in (True, False):
                    same = (model_sex == 'female') == female
                    check(f'{label}_manual_{model_sex}_model_for_{"f" if female else "m"}_switch_{switch}',
                          e.condition(trig, Character(female=female)) == (same or switch))
        e.globals = set()
    # Turning the switch off must not strip an opposite-sex outfit at birthday/culture sync.
    for e, label in ((e_core, 'core'), (e_pack, 'pack')):
        worn = Character(female=False, flags={'moe_portrait', 'moe_portrait_lineage'}, group='moe_lau_g')
        e.globals = set()
        for _ in range(3):
            e.sync(worn)
        check(label+'_switch_off_keeps_opposite_sex_outfit', worn.group == 'moe_lau_g' and worn.restores == 0
              and 'moe_portrait' in worn.flags and not worn.queued and not worn.poses_cleared)
        worn.beauty = False; e.sync(worn)
        check(label+'_losing_beauty_still_restores', worn.group == 'human' and 'moe_portrait' not in worn.flags)

    # ---- lifecycle regressions (run with the package: strictest configuration)
    e = e_pack
    for c in (Character(beauty=False), Character(tradition=False), Character(culture='moe', tradition=False)):
        check('disqualified_even_with_old_flag', not e.eligible(c))
        c.flags.add('moe_portrait'); c.group = 'moe_lau_g'; e.sync(c)
        check('disqualified_restored_and_flag_removed', c.group == 'human' and 'moe_portrait' not in c.flags)
        check('disqualified_never_assigned', not c.queued)
    no_tradition = Character(tradition=False, flags={'moe_portrait'}, group='moe_lau_g')
    e_core.sync(no_tradition)
    check('core_ignores_missing_tradition', no_tradition.group == 'moe_lau_g' and 'moe_portrait' in no_tradition.flags)
    parent = Character(flags={'moe_portrait'})
    for female, age in ((False, 1), (False, 25), (True, 7), (True, 25)):
        c = Character(female=female, age=age, beauty=False, culture='han', tradition=False,
                      parents=[parent], group='moe_lau_g')
        e.sync(c)
        check('unmarked_inherited_portrait_corrected', c.group == 'human' and c.restores == 1)
        check('lineage_retained_after_restoration', 'moe_portrait_lineage' in c.flags)
        e.sync(c)
        check('inherited_repair_not_repeated_at_birthday', c.restores == 1)
        c.culture = 'norse'; e.sync(c)
        check('restored_human_not_rerolled_on_culture_change', c.restores == 1)
    ordinary = Character(culture='han', tradition=False, beauty=False)
    e.sync(ordinary)
    check('unrelated_ordinary_character_untouched', not ordinary.queued and ordinary.restores == 0)
    for culture, tradition in (('moe', True), ('moe', False), ('han', True), ('norse', True)):
        ordinary = Character(culture=culture, tradition=tradition, beauty=False, female=False)
        e.sync(ordinary); e.sync(ordinary)
        check('ordinary_human_in_cosplay_culture_untouched', ordinary.restores == 0 and not ordinary.flags)
        child = Character(beauty=False, parents=[ordinary], culture=culture, tradition=tradition)
        e.sync(child)
        check('ordinary_human_does_not_spread_lineage', not child.flags and child.restores == 0)
    for group in ('human', 'moe_lau_g'):
        legacy = Character(culture='moe', beauty=False, group=group)
        e.effect(nested(e.effects['moe_mark_legacy_portrait_repair_effect']), legacy)
        e.sync(legacy); e.sync(legacy)
        check('legacy_moe_repair_runs_once', legacy.group == 'human' and legacy.restores == 1)
        check('legacy_only_repair_does_not_invent_lineage', legacy.flags == {'moe_portrait_human_checked'})
    newborn = Character(beauty=False, age=0, parents=[parent], group='moe_lau_g')
    e.sync(newborn)
    newborn.group = 'moe_lau_g'  # Simulate inherited DNA settling after the birth hook.
    e.effect(nested(e.effects['moe_recheck_newborn_portrait_effect']), newborn)
    e.sync(newborn)
    check('delayed_birth_recheck_repairs_late_inheritance_once', newborn.group == 'human' and newborn.restores == 2)
    unrelated_newborn = Character(beauty=False, age=0)
    e.effect(nested(e.effects['moe_recheck_newborn_portrait_effect']), unrelated_newborn)
    check('delayed_birth_recheck_ignores_unrelated_humans', unrelated_newborn.restores == 0 and not unrelated_newborn.flags)
    stale = Character(beauty=False, flags={'moe_portrait', 'moe_portrait_human_checked'}, group='moe_lau_g')
    e.sync(stale)
    check('stale_checked_marker_does_not_block_mmd_recovery', stale.restores == 1 and 'moe_portrait' not in stale.flags)
    stale.flags.add('moe_portrait_assignment_pending'); stale.poses_cleared = False
    e.sync(stale)
    check('checked_human_still_clears_stale_pending_and_pose', 'moe_portrait_assignment_pending' not in stale.flags and stale.poses_cleared and stale.restores == 1)
    stale.beauty = True; e.sync(stale)
    check('regaining_eligibility_invalidates_human_marker', 'moe_portrait_human_checked' not in stale.flags and len(stale.queued) == 1)
    from moe_chars import CHARS
    for model in CHARS:
        if model.get('skin_of'): continue
        proxy = Character(char_id=model['slot_char_id'], beauty=False, culture='moe',
                          flags={'moe_portrait'}, group=model['type']+'_g')
        before = copy.deepcopy(proxy)
        e.effect(nested(e.effects['moe_mark_legacy_portrait_repair_effect']), proxy)
        e.sync(proxy)
        e.effect(nested(e.effects['moe_recheck_newborn_portrait_effect']), proxy)
        check('preview_actor_untouched_'+model['ck'], proxy == before)
    accepted = Character(flags={'moe_portrait'}, group='moe_lau_g')
    e.sync(accepted)
    check('accepted_portrait_not_rerolled', not accepted.queued and accepted.group == 'moe_lau_g')
    accepted.culture = 'norse'; e.sync(accepted)
    check('eligible_culture_change_preserves_selected_model', not accepted.queued and accepted.group == 'moe_lau_g')
    accepted.tradition = False; e.sync(accepted)
    check('pack_tradition_removal_reclaims_portrait', accepted.group == 'human' and accepted.poses_cleared)
    # Cancel button: opt-out flag + sync turns an assigned character back into a culture-random human
    # and assignment must never claim her again, even after the usual rechecks (birthday, culture change).
    cancel = next(r for r in entries(core['common/scripted_guis/42_moe_cancel_cosplay.txt']) if r.key == rules.CANCEL_GUI)
    check('cancel_gui_requires_eligible_target', rules.ELIGIBLE in str(cancel.value))
    quitter = Character(flags={'moe_portrait', 'moe_portrait_lineage'}, group='moe_lau_g')
    quitter.flags.add(rules.OPT_OUT); e.sync(quitter)
    check('cancel_restores_human_and_clears_assignment', quitter.group == 'human' and quitter.restores == 1
          and 'moe_portrait' not in quitter.flags and quitter.poses_cleared and not quitter.queued)
    quitter.beauty = True
    for _ in range(3): e.sync(quitter)
    check('cancelled_character_is_never_reassigned', not quitter.queued and quitter.group == 'human' and not e.eligible(quitter))
    quitter.culture = 'norse'; e.sync(quitter)
    check('cancelled_character_survives_culture_change', not quitter.queued and quitter.group == 'human')
    minor = Character(age=7, female=False)
    e.sync(minor)
    check('underage_male_remains_eligible', e.eligible(minor) and minor.queued == [{'on_action':'moe_pick_model'}])
    for name in ('on_tradition_added', 'on_tradition_removed'):
        block = next(r for r in entries(pack[rules.CULTURE_HOOKS_REL]) if r.key == name)
        check(name+'_does_not_override_native_effect', all(r.key != 'effect' for r in nested(block.value)))
    block = next(r for r in entries(core['common/on_action/zz_moe_portrait_assign.txt']) if r.key == 'on_character_culture_change')
    check('on_character_culture_change_does_not_override_native_effect', all(r.key != 'effect' for r in nested(block.value)))
    for culture in ('han', 'norse', 'moe'):
        check(culture+'_has_restore_branch', 'culture = culture:'+culture in core['common/scripted_effects/41_moe_portrait_rules.txt'])
    check('tradition_has_no_culture_or_heritage_restriction', 'can_pick' not in pack[rules.TRADITION_REL] and 'is_shown' not in pack[rules.TRADITION_REL])
    eligibility_rows = nested(e_core.triggers[rules.ELIGIBLE])
    check('eligibility_has_no_sex_filter', not any(r.key in ('is_female', 'is_male') for r in eligibility_rows))
    check('underage_qualification_has_no_age_filter', not any(r.key == 'age' for r in eligibility_rows)
          and rules.MINIMUM_AGE is None)
    verify_tradition_bonuses(pack, check)
    built = verify_built(check, core, pack) if args.built else None
    report = dict(status='passed_offline_rules_and_output_checks', count=len(checks), checks=checks,
                  built=built, actual_game_loaded=False,
                  rules_sha256=hashlib.sha256(Path(rules.__file__).read_bytes()).hexdigest(),
                  verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    print(f'PASS: {len(checks)} generated-rule behavioral/output checks; CK3 runtime validation remains separate.')
    if built:
        print(json.dumps(built, ensure_ascii=False))


def verify_built(check, core, pack):
    """Compare the real build output under mod/ with the generator contract."""
    import moe_cultural_units as CU
    from moe_chars import active
    mods = ROOT/'mod'
    core_root, pack_root = mods/'moe_core', mods/'moe_cultural_units'
    text_suffixes = {'.txt', '.yml', '.gui', '.asset', '.modifierpack', '.mod'}
    scanned = 0
    for path in core_root.rglob('*'):
        if path.is_file() and path.suffix in text_suffixes:
            text = path.read_text(encoding='utf-8-sig', errors='replace')
            scanned += 1
            rel = path.relative_to(core_root).as_posix()
            check('built_core_no_tradition_reference_'+rel, not [t for t in TRADITION_TOKENS if t in text])
    check('built_core_has_no_tradition_icons', not list(core_root.glob(CU.ICON_DIR+'/*.dds')))
    check('built_core_has_no_tradition_definitions', not (core_root/'common/culture/traditions').exists())
    for rel, text in core.items():
        check('built_core_matches_generator_'+rel, (core_root/rel).read_bytes().startswith(BOM)
              and (core_root/rel).read_text(encoding='utf-8-sig') == text)
    for rel, text in pack.items():
        if rel.startswith('localization/replace/'):
            continue  # pose list differs from the offline placeholder; content checked below
        check('built_pack_matches_generator_'+rel, (pack_root/rel).read_bytes().startswith(BOM)
              and (pack_root/rel).read_text(encoding='utf-8-sig') == text)
    core_culture = (core_root/CU.CULTURE_REL).read_text(encoding='utf-8-sig')
    check('built_pack_culture_derived_from_core',
          (pack_root/CU.CULTURE_REL).read_text(encoding='utf-8-sig') == CU.culture_override(core_culture))
    check('built_pack_legacy_culture_copy_removed', not (pack_root/CU.LEGACY_CULTURE_REL).exists())
    for rel, src in CU.binaries().items():
        check('built_pack_icon_matches_source_'+Path(rel).name, (pack_root/rel).read_bytes() == src.read_bytes())
    # Ignore-gender switch: both windows, scripted_gui and localization.
    for rel in ('gui/hud_sidebars.gui', 'gui/multiplayer_lobby.gui'):
        text = (core_root/rel).read_text(encoding='utf-8-sig')
        check('built_ignore_gender_button_'+rel, text.count('name = "moe_cos_ignore_gender"') == 1
              and "GetScriptedGui( '"+rules.TOGGLE_GUI+"' )" in text and rules.STATE_GUI in text)
    for lang in ('english', 'simp_chinese'):
        loc = (core_root/f'localization/{lang}/moe_moe_ui_l_{lang}.yml').read_text(encoding='utf-8-sig')
        check('built_ignore_gender_localized_'+lang, all(f' moe_cos_ignore_gender{s}:0 ' in loc for s in ('', '_on', '_off', '_tt')))
        welcome = re.search(r'^ moe_welcome\.0002\.desc:0 "(.*)"$', loc, re.M).group(1)
        check('built_core_welcome_has_no_tradition_'+lang,
              'Cosplay Identity' not in welcome and 'cosplay认同' not in welcome and '传统' not in welcome)
        replace = (pack_root/rules.WELCOME_REPLACE_REL.format(lang=lang)).read_text(encoding='utf-8-sig')
        check('built_pack_welcome_replace_mentions_tradition_'+lang,
              ('Cosplay Identity' if lang == 'english' else 'cosplay认同') in replace)
    # Random pool stays same-sex; manual slot claims honour the switch.
    pools = slots = 0
    for model in active():
        sex = model.get('sex', 'female')
        mod = mods/('moe_models_'+model['game'])
        pool = (mod/f'events/moe_pool_{model["ck"]}.txt').read_text(encoding='utf-8-sig')
        check('built_pool_same_sex_'+model['ck'], f'trigger = {{ {rules.ELIGIBLE} = yes is_{sex} = yes }}' in pool)
        pools += 1
        ward = (mod/f'events/moe_wardrobe_model_{model["ck"]}.txt').read_text(encoding='utf-8-sig')
        check('built_wardrobe_claim_uses_switch_'+model['ck'], f'{rules.sex_ok(sex)} = yes' in ward and f'is_{sex} = yes' not in ward)
        if not model.get('skin_of'):
            slot = (mod/f'events/moe_slot_{model["ck"]}.txt').read_text(encoding='utf-8-sig')
            check('built_slot_claim_uses_switch_'+model['ck'], f'{rules.ELIGIBLE} = yes {rules.sex_ok(sex)} = yes' in slot)
            slots += 1
    return dict(core_text_files_scanned=scanned, pack_files=len(pack), pools=pools, slots=slots)


if __name__ == '__main__':
    main()
