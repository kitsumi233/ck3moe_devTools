"""Cultural-tradition eligibility and reconciliation for the normal mod builder.

Character scopes own portrait changes. Culture hooks only select their members.
Native hooks are extended with event/on_action lists, never a second effect.
"""
from pathlib import Path
import re

TRADITION = 'tradition_moe_cosplay_identity'
HUMANOID_MERCENARIES_TRADITION = 'tradition_moe_humanoid_mercenaries'
ELIGIBLE = 'moe_cosplay_eligible'
# Culture gate. Core defines it as always-true; the optional cultural-units package
# replaces the file at the same relative path (VFS path override is reliable,
# duplicate top-level keys across files are not).
CULTURE_OK = 'moe_cosplay_culture_ok'
CULTURE_OK_REL = 'common/scripted_triggers/39_moe_cosplay_culture.txt'
MIGRATION_REL = 'common/scripted_effects/39_moe_cosplay_culture.txt'
CULTURE_HOOKS_REL = 'common/on_action/zz_moe_cosplay_culture.txt'
# Manual wardrobe sex check: the global toggle lets either sex wear any model.
# Random assignment keeps plain is_<sex> checks.
IGNORE_GENDER = 'moe_ignore_gender'
TOGGLE_GUI = 'moe_toggle_ignore_gender'
STATE_GUI = 'moe_ignore_gender_state'
# Per-character opt-out set by the wardrobe's cancel button. It makes the character
# ineligible, so the ordinary sync restores a culture-appropriate vanilla appearance
# (the beauty trait is untouched) and assignment never claims the character again.
OPT_OUT = 'moe_cosplay_opt_out'
CANCEL_GUI = 'moe_cancel_cosplay'
WELCOME_KEY = 'moe_welcome.0002.desc'
WELCOME_REPLACE_REL = 'localization/replace/{lang}/moe_welcome_culture_l_{lang}.yml'
TRADITION_LOC_REL = 'localization/{lang}/moe_cosplay_tradition_l_{lang}.yml'
HUMANOID_MERCENARIES_REL = 'common/culture/traditions/41_moe_humanoid_mercenaries.txt'
TRADITION_REL = 'common/culture/traditions/40_moe_cosplay_identity.txt'


def sex_ok(sex):
    """Trigger name for the manual wardrobe sex check of a model of this sex."""
    return f'moe_sex_ok_{sex}'
MINIMUM_AGE = None  # Girls remain eligible; child meshes are age-shaped separately.
PORTRAIT_ADULT_MINIMUM_AGE = 18
PORTRAIT_CHILD_MAXIMUM_AGE = 18
ICON_REL = 'gfx/interface/icons/culture_tradition/4-items/moe_cosplay_identity.dds'
ICON_SOURCE = 'assets/ui/cosplay_identity/moe_cosplay_identity.dds'
HUMANOID_MERCENARIES_ICON_REL = 'gfx/interface/icons/culture_tradition/4-items/moe_humanoid_mercenaries.dds'
HUMANOID_MERCENARIES_ICON_SOURCE = 'assets/ui/cosplay_identity/moe_humanoid_mercenaries.dds'
BEAUTY_PARAMETER = 'moe_cosplay_beauty_bonus'
COURT_COST_PARAMETER = 'moe_cosplay_fashion_cost'
BEAUTY_SOURCE = 'common/traits/00_traits.txt'
COURT_COST_SOURCE = 'common/script_values/00_court_amenities_values.txt'
BEAUTY_OUTPUT = 'common/traits/zz_moe_cosplay_beauty.txt'
COURT_COST_OUTPUT = 'common/script_values/zz_moe_cosplay_fashion_cost.txt'
FASHION_COSTS = tuple(f'court_fashion_{level}_cost' for level in ('acceptable', 'decent', 'good', 'fantastic'))


def _extend_vanilla_definition(game, relative, key, addition):
    """Copy one native definition verbatim and append an explicit extension.

    Read the installed game to preserve native trait fields, cost floors and
    discounts. Quoted/comment braces never delimit the copied block.
    """
    from moe_culture_ethnicities import _LEXER, _entries, _tokens
    text = (game / relative).read_text(encoding='utf-8-sig')
    definitions = [entry for entry in _entries(_tokens(text, relative), relative) if entry.key == key]
    if len(definitions) != 1:
        raise ValueError(f'{relative}: expected one native {key} definition')
    matches = list(re.finditer(r'^' + re.escape(key) + r'\s*=\s*\{', text, re.M))
    if len(matches) != 1:
        raise ValueError(f'{relative}: cannot locate native {key} block')
    match = matches[0]
    depth = 1
    for token in _LEXER.finditer(text, match.end()):
        depth += (token.group() == '{') - (token.group() == '}')
        if depth == 0:
            original = text[match.start():token.start()]
            return f'# Generated from {relative}; only {key} is extended.\n' + original + addition + '}\n'
    raise ValueError(f'{relative}: unclosed native {key} block')


def eligibility(ind=0):
    return '\t' * ind + ELIGIBLE + ' = yes'


def welcome_description(language, cost, poses, culture_pack=False):
    """Game-start popup text. Core never mentions the tradition; the cultural-units
    package ships a localization/replace copy generated with culture_pack=True."""
    if language == 'english':
        who = ("#T Who gets an anime portrait?#!\\n\\nAny character with the highest beauty trait "
               "(#Y Beautiful#!) qualifies, men and women alike, and there is no minimum age. "
               "Random assignment only picks a model of the character's own sex, so men receive male "
               "models and women female models. ")
        who += ("With the Cultural Units add-on enabled, the character's culture must also have "
                "#Y Cosplay Identity#!. Moe starts with this tradition; any other culture may adopt it "
                "without becoming Moe. " if culture_pack else "")
        when = ("changing culture, or gaining or losing a cultural tradition" if culture_pack
                else "or changing culture")
        return (who + "Characters without the required trait keep a vanilla human appearance.\\n\\n"
                "#T When do portraits update?#!\\n\\nAt game setup, birth, birthdays, completing the ruler designer, "
                f"{when}. Losing eligibility restores a vanilla appearance appropriate to the current culture.\\n\\n"
                "#T Changing portrait and pose#!\\n\\nRight-click an assigned character and choose "
                "#Y Change Cosplay Outfit#!. All installed outfits are unlocked by default; changing outfits "
                "and poses is free. The #Y Ignore Gender#! switch in that window lets you pick models of "
                "either sex by hand; random assignment still follows the character's sex, and turning the "
                "switch off keeps outfits already worn. The optional Collection mod adds paid requests, "
                f"tailor commissions and pilgrimage rewards. Available: {poses}.")
    if language == 'simp_chinese':
        who = ("#T 哪些角色会获得二次元立绘#!\\n\\n拥有最高级美貌特质（#Y 倾国倾城#!）的角色即可使用立绘，不分男女，"
               "未满18岁同样可以。随机分配只会选与角色同性别的模型：男性获得男性模型，女性获得女性模型。")
        who += ("启用文化兵种Mod时，角色所属文化还需拥有#Y cosplay认同#!传统。#Y 二次元#!文化默认拥有此传统；"
                "其他文化也能选取，无需改成二次元文化。" if culture_pack else "")
        when = "改换文化或文化传统增减时" if culture_pack else "改换文化时"
        return (who + "不具备所需美貌特质的角色保留原版人类外观。\\n\\n"
                f"#T 立绘什么时候会变？#!\\n\\n开局、出生、生日、角色编辑器点完成、{when}检查资格。"
                "失去资格后恢复符合当前文化的原版外观。\\n\\n"
                "#T 更换立绘与姿势#!\\n\\n右键已获得立绘的角色，选择#Y 更换cos服#!。"
                "主Mod默认解锁全部已安装衣装，换装与姿势免费。窗口里的#Y 忽略性别#!开关打开后，可以手动选择"
                "另一性别的模型与皮肤；随机分配仍按角色性别，关掉开关也不会脱下已穿的衣装。启用独立获取机制Mod后，"
                f"才需要通过好感付费申请、裁缝委托或朝圣奖励收藏。可选姿势：{poses}。")
    raise ValueError(language)


def _migration_effect(culture_pack):
    """Old-save repair. Core only marks and resyncs; the cultural pack also grants
    the tradition to Moe once (its own marker, so enabling the pack later still works)."""
    tradition = f'''
	# Cultural-units package only: an existing Moe culture receives the tradition once.
	# Mark first: adding the tradition fires the tradition hooks.
	if = {{
		limit = {{ NOT = {{ has_global_variable = moe_cosplay_tradition_added_v1 }} }}
		set_global_variable = {{ name = moe_cosplay_tradition_added_v1 value = 1 }}
		culture:moe = {{
			if = {{
				limit = {{ NOT = {{ has_cultural_tradition = {TRADITION} }} }}
				add_culture_tradition = {TRADITION}
			}}
		}}
		every_living_character = {{ moe_sync_portrait_effect = yes }}
	}}''' if culture_pack else ''
    owner = 'moe_cultural_units' if culture_pack else 'moe_core'
    return f'''# Generated by tools/lib/moe_cosplay_rules.py for {owner}.
# Same relative path in moe_core and moe_cultural_units: the package copy replaces this file.
moe_migrate_cosplay_tradition_effect = {{
	if = {{
		limit = {{ NOT = {{ has_global_variable = moe_cosplay_tradition_migrated_v1 }} }}
		set_global_variable = {{ name = moe_cosplay_tradition_migrated_v1 value = 1 }}
		every_living_character = {{ moe_mark_legacy_portrait_repair_effect = yes }}
		every_living_character = {{ moe_sync_portrait_effect = yes }}
	}}{tradition}
}}
'''


def _culture_ok(culture_pack):
    body = f'culture = {{ has_cultural_tradition = {TRADITION} }}' if culture_pack else 'always = yes'
    owner = 'moe_cultural_units (tradition gate)' if culture_pack else 'moe_core (no culture gate)'
    return f'''# Generated by tools/lib/moe_cosplay_rules.py for {owner}.
# Same relative path in moe_core and moe_cultural_units: the package copy replaces this file.
{CULTURE_OK} = {{
	{body}
}}
'''


def files(game: Path):
    """Core-owned rules. No custom tradition, parameter or tradition hook is referenced."""
    from moe_chars import CHARS
    from moe_culture_ethnicities import generate_restore_effect
    restore, _report = generate_restore_effect(game)
    # Fixed preview actors are intentionally MMD even without the beauty trait.
    # History IDs also identify them in saves predating the new mechanism.
    proxies = '\n'.join(
        f'\t\tAND = {{ exists = character:{c["slot_char_id"]} this = character:{c["slot_char_id"]} }}'
        for c in CHARS if not c.get('skin_of'))
    sex_triggers = ''.join(f'''
# Manual wardrobe only: a {sex} model fits {sex} characters, or anyone while the
# global "ignore gender" switch is on. Random assignment keeps plain is_{sex}.
{sex_ok(sex)} = {{
	OR = {{
		is_{sex} = yes
		has_global_variable = {IGNORE_GENDER}
	}}
}}
''' for sex in ('female', 'male'))
    triggers = f'''# Character scope. Shared by assignment, UI and model-pack events.
# Sex is deliberately absent: an eligible character keeps an opposite-sex outfit
# after the ignore-gender switch is turned off (models check sex when claimed).
{ELIGIBLE} = {{
	has_trait = beauty_good_3
	{CULTURE_OK} = yes
	NOT = {{ has_character_flag = {OPT_OUT} }}
}}
{sex_triggers}
moe_portrait_preview_actor = {{
	OR = {{
{proxies}
	}}
}}
'''
    effects = f'''# Restore uses the character's current culture, not a fixed Moe appearance.
{restore}

moe_sync_portrait_effect = {{
	if = {{
		limit = {{ moe_portrait_preview_actor = no }}
		moe_sync_character_portrait_effect = yes
	}}
}}

moe_sync_character_portrait_effect = {{
	# Ethnicity can be inherited without inheriting our assignment flag.
	if = {{
		limit = {{ OR = {{
			has_character_flag = moe_portrait
			any_parent = {{ OR = {{
				has_character_flag = moe_portrait
				has_character_flag = moe_portrait_lineage
			}} }}
		}} }}
		add_character_flag = moe_portrait_lineage
	}}
	if = {{
		limit = {{
			{ELIGIBLE} = yes
			NOT = {{ has_character_flag = moe_portrait }}
			NOT = {{ has_character_flag = moe_portrait_assignment_pending }}
		}}
		# A second hook may run before the queued reservoir events claim a model.
		add_character_flag = moe_portrait_lineage
		remove_character_flag = moe_portrait_human_checked
		add_character_flag = {{ flag = moe_portrait_assignment_pending days = 1 }}
		set_variable = {{ name = moe_pick_n value = 0 }}
		trigger_event = {{ on_action = moe_pick_model }}
	}}
	else_if = {{
		limit = {{
			{ELIGIBLE} = no
			OR = {{
				has_character_flag = moe_portrait
				has_character_flag = moe_portrait_lineage
				has_character_flag = moe_portrait_legacy_repair_pending
			}}
		}}
		# Restore once per MMD assignment/inherited birth, not every birthday.
		if = {{
			limit = {{ OR = {{
				has_character_flag = moe_portrait
				NOT = {{ has_character_flag = moe_portrait_human_checked }}
			}} }}
			moe_restore_cultural_ethnicity_effect = yes
			add_character_flag = moe_portrait_human_checked
		}}
		remove_character_flag = moe_portrait
		remove_character_flag = moe_portrait_assignment_pending
		moe_clear_pose_flags = yes
	}}
	remove_character_flag = moe_portrait_legacy_repair_pending
}}

moe_mark_legacy_portrait_repair_effect = {{
	# Old saves could inherit Moe ethnicity without an assignment flag.
	# Do not give ordinary human characters a permanent MMD lineage.
	if = {{
		limit = {{
			moe_portrait_preview_actor = no
			culture = culture:moe
			{ELIGIBLE} = no
		}}
		add_character_flag = moe_portrait_legacy_repair_pending
	}}
}}

moe_recheck_newborn_portrait_effect = {{
	if = {{
		limit = {{
			moe_portrait_preview_actor = no
			OR = {{
				has_character_flag = moe_portrait
				has_character_flag = moe_portrait_lineage
				any_parent = {{ OR = {{
					has_character_flag = moe_portrait
					has_character_flag = moe_portrait_lineage
				}} }}
			}}
		}}
		remove_character_flag = moe_portrait_human_checked
	}}
	moe_sync_portrait_effect = yes
}}
'''
    actions = '''# Append lists to native hooks; keep vanilla effects intact.
# Tradition add/remove hooks belong to the cultural-units package (zz_moe_cosplay_culture.txt).
on_birth_child = { events = { moe_assign.0001 } }
on_birthday = { events = { moe_assign.0002 } }
on_game_start = { events = { moe_assign.0005 } }
on_game_start_after_lobby = { events = { moe_assign.0003 } }
on_ruler_designer_finished = { events = { moe_assign.0004 } }
on_character_culture_change = { on_actions = { moe_character_culture_changed } }

moe_character_culture_changed = {
	effect = { moe_sync_portrait_effect = yes }
}
# Model packs append their reservoir registration events here.
moe_pick_model = { }
'''
    events = '''namespace = moe_assign

# Immediate birth correction plus a delayed recheck after inherited DNA settles.
moe_assign.0001 = {
	type = character_event
	hidden = yes
	immediate = {
		moe_migrate_cosplay_tradition_effect = yes
		moe_sync_portrait_effect = yes
		trigger_event = { id = moe_assign.0006 days = 1 }
	}
}
# Birthday reconciliation also repairs unmarked descendants.
moe_assign.0002 = {
	type = character_event
	hidden = yes
	immediate = {
		moe_migrate_cosplay_tradition_effect = yes
		moe_sync_portrait_effect = yes
	}
}
moe_assign.0003 = {
	scope = none
	hidden = yes
	immediate = {
		moe_migrate_cosplay_tradition_effect = yes
		every_living_character = { moe_sync_portrait_effect = yes }
		# Moe still needs a county for its existing culture-head setup.
		# Other cultures adopting cosplay are never converted to Moe.
		every_ruler = {
			limit = { culture = culture:moe exists = capital_county }
			capital_county = { set_county_culture = culture:moe }
		}
	}
}
moe_assign.0004 = {
	type = character_event
	hidden = yes
	immediate = {
		moe_migrate_cosplay_tradition_effect = yes
		moe_sync_portrait_effect = yes
	}
}
moe_assign.0005 = {
	scope = none
	hidden = yes
	immediate = { moe_migrate_cosplay_tradition_effect = yes }
}
# Only a birth recheck invalidates the human restoration marker.
moe_assign.0006 = {
	type = character_event
	hidden = yes
	immediate = { moe_recheck_newborn_portrait_effect = yes }
}
'''
    toggle = f'''# Global "ignore gender" switch of the wardrobe windows (in-game and lobby).
# scope is the window owner (player or lobby character); the state itself is global.
{TOGGLE_GUI} = {{
	scope = character
	effect = {{
		if = {{
			limit = {{ has_global_variable = {IGNORE_GENDER} }}
			remove_global_variable = {IGNORE_GENDER}
			debug_log = "MOE_COS ignore_gender off"
		}}
		else = {{
			set_global_variable = {{ name = {IGNORE_GENDER} value = 1 }}
			debug_log = "MOE_COS ignore_gender on"
		}}
	}}
}}
{STATE_GUI} = {{
	scope = character
	is_shown = {{ has_global_variable = {IGNORE_GENDER} }}
}}
'''
    cancel = f'''# Wardrobe "cancel cosplay" button (in-game and lobby; root is the window owner).
# The target is the character the window was opened for. Opting out makes it ineligible and
# the regular sync restores a vanilla appearance of the current culture; the beauty trait stays.
{CANCEL_GUI} = {{
	scope = character
	is_valid = {{
		exists = var:moe_cos_target
		var:moe_cos_target = {{ {ELIGIBLE} = yes }}
	}}
	effect = {{
		var:moe_cos_target ?= {{
			add_character_flag = {OPT_OUT}
			moe_sync_portrait_effect = yes
			debug_log = "MOE_COS cancel"
		}}
		remove_variable = moe_cos_open
	}}
}}
'''
    return {
        'common/scripted_triggers/40_moe_cosplay.txt': triggers,
        CULTURE_OK_REL: _culture_ok(False),
        'common/scripted_effects/41_moe_portrait_rules.txt': effects,
        MIGRATION_REL: _migration_effect(False),
        'common/scripted_guis/41_moe_ignore_gender.txt': toggle,
        'common/scripted_guis/42_moe_cancel_cosplay.txt': cancel,
        'common/on_action/zz_moe_portrait_assign.txt': actions,
        'events/moe_assign.txt': events,
    }


def culture_pack_files(game: Path, welcome):
    """Files owned by moe_cultural_units for the cosplay tradition.

    welcome: {language: welcome text with the tradition requirement}; written as
    localization/replace so it wins over the core key of the same name.
    """
    tradition = f'''# Generated by tools/lib/moe_cosplay_rules.py (moe_cultural_units).
# Any culture may adopt this societal tradition.
{TRADITION} = {{
	category = societal
	layers = {{ 0 = learning 1 = western 4 = moe_cosplay_identity.dds }}
	parameters = {{
		moe_cosplay_portraits = yes
		{COURT_COST_PARAMETER} = yes
		{BEAUTY_PARAMETER} = yes
	}}
	character_modifier = {{ fertility = 0.05 }}
	cost = {{
		prestige = {{
			add = {{ value = tradition_base_cost desc = BASE format = "BASE_VALUE_FORMAT" }}
			multiply = tradition_replacement_cost_if_relevant
		}}
	}}
	ai_will_do = {{ value = 10 }}
}}
'''
    hooks = '''# Generated by tools/lib/moe_cosplay_rules.py (moe_cultural_units).
# Eligibility depends on the tradition only while this package is loaded.
on_tradition_added = { on_actions = { moe_cultural_traditions_changed } }
on_tradition_removed = { on_actions = { moe_cultural_traditions_changed } }
on_culture_created = { on_actions = { moe_cultural_traditions_changed } }

moe_cultural_traditions_changed = {
	# Root is a CULTURE here, not the character receiving a portrait.
	effect = {
		every_living_character = {
			limit = { culture = root }
			moe_sync_portrait_effect = yes
		}
	}
}
'''
    loc = {
        'english': f''' {TRADITION}_name:0 "Cosplay Identity"
 {TRADITION}_desc:0 "Costume and performance are accepted expressions of personal identity. People of this culture welcome those who take on a beloved character's appearance."
 culture_parameter_moe_cosplay_portraits:0 "Characters of either sex and any age with the [GetTrait('beauty_good_3').GetName( GetNullCharacter )] [trait|E] may use installed anime portrait models of their own sex and the Change Cosplay Outfit interaction."
 culture_parameter_{COURT_COST_PARAMETER}:0 "Court Fashion [court_amenities|E] Cost: #N +5%#!"
 culture_parameter_{BEAUTY_PARAMETER}:0 "The [GetTrait('beauty_good_3').GetName( GetNullCharacter )] [trait|E] grants an additional #P +5#! [attraction|E] opinion."
''',
        'simp_chinese': f''' {TRADITION}_name:0 "cosplay认同"
 {TRADITION}_desc:0 "这一文化将装扮与扮演视为表达自我的方式，尊重人们借心仪角色的外表展现自己的个性。"
 culture_parameter_moe_cosplay_portraits:0 "不分性别与年龄，拥有[GetTrait('beauty_good_3').GetName( GetNullCharacter )][trait|E]的角色可使用已安装的同性别二次元立绘，并通过“更换cos服”选择模型与姿势。"
 culture_parameter_{COURT_COST_PARAMETER}:0 "宫廷服饰[court_amenities|E]开销：#N +5%#!"
 culture_parameter_{BEAUTY_PARAMETER}:0 "拥有[GetTrait('beauty_good_3').GetName( GetNullCharacter )][trait|E]的角色额外获得#P +5#![attraction|E]好感。"
''',
    }
    result = {
        TRADITION_REL: tradition,
        CULTURE_OK_REL: _culture_ok(True),
        MIGRATION_REL: _migration_effect(True),
        CULTURE_HOOKS_REL: hooks,
        BEAUTY_OUTPUT: _extend_vanilla_definition(game, BEAUTY_SOURCE, 'beauty_good_3', f'''
	# Applies to either sex; independent of portrait selection and model packs.
	culture_modifier = {{
		parameter = {BEAUTY_PARAMETER}
		attraction_opinion = 5
	}}
'''),
        COURT_COST_OUTPUT: '\n'.join(
            _extend_vanilla_definition(game, COURT_COST_SOURCE, key, f'''
	# Fashion only, after native discounts; free fashion remains free.
	if = {{
		limit = {{ culture = {{ has_cultural_parameter = {COURT_COST_PARAMETER} }} }}
		multiply = 1.05
	}}
''') for key in FASHION_COSTS),
    }
    for lang, body in loc.items():
        result[TRADITION_LOC_REL.format(lang=lang)] = f'l_{lang}:\n' + body
        result[WELCOME_REPLACE_REL.format(lang=lang)] = f'l_{lang}:\n {WELCOME_KEY}:0 "{welcome[lang]}"\n'
    return result
