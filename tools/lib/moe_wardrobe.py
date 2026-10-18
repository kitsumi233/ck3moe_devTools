"""Saved wardrobe rules; emitted by the existing mod and GUI builders.

Random assignment never consults this collection. Manual application is checked
again by the model pack. Ownership belongs to the house, or the character when
there is no house. All prices and the request threshold live here.
"""
from moe_chars import CHARS
from moe_cosplay_rules import _extend_vanilla_definition, sex_ok

REQUEST_GOLD = 50
REQUEST_OPINION = 50
TAILOR_GOLD = 150
TAILOR_DAYS = 60
PILGRIMAGE_SOURCE = 'common/scripted_effects/00_pilgrimage_effects.txt'
PILGRIMAGE_OUTPUT = 'common/scripted_effects/zz_moe_wardrobe_pilgrimage.txt'
CURRENT = 'moe_current_form'


def family(c):
    base = c.get('skin_of') or c['ck']
    return [x for x in CHARS if x['ck'] == base or x.get('skin_of') == base]


def pack_files(c):
    ck, slot = c['ck'], c['slot']
    sex = c.get('sex', 'female')
    return {
        f'events/moe_wardrobe_model_{ck}.txt': f'''namespace = moe_wardrobe_model_{ck}
moe_wardrobe_model_{ck}.0001 = {{
    type = character_event
    hidden = yes
    immediate = {{ set_variable = {{ name = moe_outfit_available_{ck} value = 1 }} }}
}}
moe_wardrobe_model_{ck}.0002 = {{
    type = character_event
    hidden = yes
    trigger = {{
        moe_cosplay_eligible = yes
        {sex_ok(sex)} = yes
        exists = scope:moe_wardrobe_actor
        scope:moe_wardrobe_actor = {{ moe_can_wear_{ck} = yes }}
    }}
    immediate = {{
        set_ethnicity = {c['type']}_ethnicity
        set_variable = {{ name = {CURRENT} value = {slot} }}
        add_character_flag = moe_portrait
        add_character_flag = moe_portrait_lineage
        remove_character_flag = moe_portrait_human_checked
        remove_character_flag = moe_portrait_assignment_pending
    }}
}}
''',
        f'common/on_action/zz_moe_wardrobe_model_{ck}.txt': f'''moe_register_wardrobe = {{
    events = {{ moe_wardrobe_model_{ck}.0001 }}
}}
''',
    }


def files(game):
    triggers, effects, guis = [], [], ['moe_collection_enabled = { scope = character is_shown = { always = yes } }\n']
    owned = lambda ck: f'moe_owns_{ck}'
    for c in CHARS:
        ck, slot = c['ck'], c['slot']
        sex = c.get('sex', 'female')
        triggers.append(f'''
{owned(ck)} = {{ OR = {{
    has_variable = moe_outfit_{ck}
    AND = {{ exists = house house = {{ has_variable = moe_outfit_{ck} }} }}
}} }}
moe_can_wear_{ck} = {{
    has_variable = moe_outfit_available_{ck}
    OR = {{
        has_variable = moe_wardrobe_lobby
        {owned(ck)} = yes
        NOT = {{ has_variable = moe_wardrobe_seeded }}
    }}
}}
moe_can_tailor_{ck} = {{
    has_variable = moe_outfit_available_{ck}
    NOT = {{ has_variable = moe_wardrobe_lobby }}
    {owned(ck)} = no
    gold >= {TAILOR_GOLD}
    NOT = {{ has_variable = moe_tailor_order }}
    trigger_if = {{ limit = {{ exists = house }} house = {{ NOT = {{ has_variable = moe_tailor_order }} }} }}
}}
''')
        effects.append(f'''
moe_unlock_{ck}_effect = {{
    if = {{ limit = {{ exists = house }} house = {{ set_variable = {{ name = moe_outfit_{ck} value = 1 }} }} }}
    else = {{ set_variable = {{ name = moe_outfit_{ck} value = 1 }} }}
}}
moe_wear_{ck}_effect = {{
    if = {{
        limit = {{
            moe_can_wear_{ck} = yes
            exists = var:moe_cos_target
            var:moe_cos_target = {{ moe_cosplay_eligible = yes {sex_ok(sex)} = yes }}
        }}
        # Lobby previews never accumulate ownership. A legacy player with no
        # recorded form gets one starter choice, consumed before dispatch.
        if = {{ limit = {{ NOT = {{ has_variable = moe_wardrobe_lobby }} }}
            if = {{ limit = {{ NOT = {{ has_variable = moe_wardrobe_seeded }} }}
                moe_unlock_{ck}_effect = yes
                set_variable = {{ name = moe_wardrobe_seeded value = 1 }}
            }}
        }}
        save_scope_as = moe_wardrobe_actor
        var:moe_cos_target = {{ trigger_event = moe_wardrobe_model_{ck}.0002 }}
    }}
}}
moe_tailor_{ck}_effect = {{
    moe_refresh_wardrobe_effect = yes
    if = {{ limit = {{ moe_can_tailor_{ck} = yes }}
        add_gold = -{TAILOR_GOLD}
        moe_wardrobe_owner_effect = yes
        scope:moe_wardrobe_owner = {{
            set_variable = {{ name = moe_tailor_order value = {slot} }}
            set_variable = {{ name = moe_tailor_wait value = 1 days = {TAILOR_DAYS} }}
        }}
        # Persist the owner as a variable on the patron. Saved scopes are event
        # local in CK3, while a variable survives the delayed event and can point
        # at either a house or the character when there is no house.
        set_variable = {{ name = moe_tailor_owner value = scope:moe_wardrobe_owner }}
        # Opening the wardrobe/pulse also settles an expired order if the delayed
        # event was skipped while a save was being loaded.
        trigger_event = {{ id = moe_wardrobe.0003 days = {TAILOR_DAYS} }}
        send_interface_toast = {{ type = event_generic_neutral title = moe_tailor_started }}
    }}
}}
''')
        guis.append(f'''
moe_wardrobe_wear_{ck} = {{
    scope = character
    is_valid = {{
        exists = var:moe_cos_target
        var:moe_cos_target = {{ moe_cosplay_eligible = yes {sex_ok(sex)} = yes }}
        custom_tooltip = {{ text = moe_wardrobe_locked_tt moe_can_wear_{ck} = yes }}
    }}
    effect = {{ moe_refresh_wardrobe_effect = yes moe_wear_{ck}_effect = yes }}
}}
moe_wardrobe_owned_{ck} = {{
    scope = character
    is_shown = {{ {owned(ck)} = yes }}
}}
moe_wardrobe_tailor_{ck} = {{
    scope = character
    is_valid = {{ custom_tooltip = {{ text = moe_tailor_requirements_tt moe_can_tailor_{ck} = yes }} }}
    effect = {{ moe_tailor_{ck}_effect = yes }}
}}
''')

    # Deterministic next-owned rotation. Missing/locked skins are skipped; this
    # also covers legacy buttons so they cannot bypass the explicit wardrobe.
    for c in CHARS:
        forms = family(c)
        slot = c['slot']
        triggers.append(f'moe_has_owned_cycle_{slot} = {{ OR = {{ ' + ' '.join(
            f'moe_can_wear_{x["ck"]} = yes' for x in forms) + ' } }\n')
        cases = []
        for i, current in enumerate(forms):
            choices = forms[i + 1:] + forms[:i + 1]
            body = '\n'.join(f'''{'if' if j == 0 else 'else_if'} = {{
                limit = {{ moe_can_wear_{x['ck']} = yes }}
                moe_wear_{x['ck']}_effect = yes
            }}''' for j, x in enumerate(choices))
            cases.append(f'''{'if' if i == 0 else 'else_if'} = {{
                limit = {{ var:moe_cos_target = {{ var:{CURRENT} = {current['slot']} }} }}
                {body}
            }}''')
        fallback = '\n'.join(f'''{'if' if j == 0 else 'else_if'} = {{
            limit = {{ moe_can_wear_{x['ck']} = yes }} moe_wear_{x['ck']}_effect = yes
        }}''' for j, x in enumerate(forms))
        effects.append(f'''moe_cycle_owned_{slot}_effect = {{
            if = {{ limit = {{ exists = var:moe_cos_target }}
                {chr(10).join(cases)}
                else = {{ {fallback} }}
            }}
        }}''')

    source_cases = '\n'.join(f'''AND = {{
        scope:recipient = {{ var:{CURRENT} = {c['slot']} }}
        scope:actor = {{ has_variable = moe_outfit_available_{c['ck']} moe_owns_{c['ck']} = no }}
    }}''' for c in CHARS)
    triggers.append(f'''
moe_can_request_outfit = {{
    scope:actor = {{ is_ai = no is_alive = yes gold >= {REQUEST_GOLD} }}
    scope:recipient = {{
        is_alive = yes
        NOT = {{ this = scope:actor }}
        moe_portrait_preview_actor = no
        moe_cosplay_eligible = yes
        has_character_flag = moe_portrait
        has_variable = {CURRENT}
        opinion = {{ target = scope:actor value >= {REQUEST_OPINION} }}
    }}
    OR = {{ {source_cases} }}
}}
''')
    grant_cases = '\n'.join(f'''{'if' if i == 0 else 'else_if'} = {{
        limit = {{ scope:recipient = {{ var:{CURRENT} = {c['slot']} }} }}
        scope:actor = {{ moe_unlock_{c['ck']}_effect = yes }}
    }}''' for i, c in enumerate(CHARS))
    seed = '\n'.join(f'''if = {{
        limit = {{ var:{CURRENT} = {c['slot']} }}
        moe_unlock_{c['ck']}_effect = yes
        set_variable = {{ name = moe_wardrobe_seeded value = 1 }}
    }}''' for c in CHARS)
    finish = '\n'.join(f'''if = {{ limit = {{ var:moe_tailor_order = {c['slot']} }}
        set_variable = {{ name = moe_outfit_{c['ck']} value = 1 }}
    }}''' for c in CHARS)
    migrate = '\n'.join(f'''if = {{ limit = {{ has_variable = moe_outfit_{c['ck']} }}
        house = {{ set_variable = {{ name = moe_outfit_{c['ck']} value = 1 }} }}
        remove_variable = moe_outfit_{c['ck']}
    }}''' for c in CHARS)
    rewards = '\n'.join(f'''1 = {{
        trigger = {{ has_variable = moe_outfit_available_{c['ck']} moe_owns_{c['ck']} = no }}
        moe_unlock_{c['ck']}_effect = yes
        send_interface_toast = {{ type = event_generic_good title = moe_pilgrimage_outfit_{c['ck']} }}
    }}''' for c in CHARS)
    reward_available = ' '.join(f'AND = {{ has_variable = moe_outfit_available_{c["ck"]} moe_owns_{c["ck"]} = no }}' for c in CHARS)
    effects.append(f'''
moe_wardrobe_owner_effect = {{
    save_scope_as = moe_wardrobe_owner
    if = {{ limit = {{ exists = house }} house = {{ save_scope_as = moe_wardrobe_owner }} }}
}}
moe_finish_tailor_effect = {{
    # Runs in the owner scope (house or character), not necessarily the patron.
    if = {{ limit = {{ has_variable = moe_tailor_order NOT = {{ has_variable = moe_tailor_wait }} }}
        {finish}
        remove_variable = moe_tailor_order
    }}
}}
moe_refresh_wardrobe_effect = {{
    # Finish personal orders before moving no-house ownership into a new house.
    moe_finish_tailor_effect = yes
    if = {{ limit = {{ exists = house }} {migrate} }}
    moe_wardrobe_owner_effect = yes
    scope:moe_wardrobe_owner = {{ moe_finish_tailor_effect = yes }}
    if = {{ limit = {{ NOT = {{ has_variable = moe_wardrobe_seeded }} has_character_flag = moe_portrait }}
        {seed}
    }}
}}
moe_request_outfit_effect = {{
    if = {{ limit = {{ moe_can_request_outfit = yes }}
        # One atomic acceptance path: validate before either transfer or unlock.
        scope:actor = {{ add_gold = -{REQUEST_GOLD} }}
        scope:recipient = {{ add_gold = {REQUEST_GOLD} }}
        {grant_cases}
        scope:actor = {{ send_interface_toast = {{ type = event_generic_good title = moe_outfit_requested }} }}
    }}
}}
moe_pilgrimage_outfit_effect = {{
    if = {{
        limit = {{ is_ai = no exists = involved_activity
            involved_activity = {{ NOT = {{ has_variable = moe_outfit_rewarded }} }}
        }}
        involved_activity = {{ set_variable = {{ name = moe_outfit_rewarded value = 1 }} }}
        moe_refresh_wardrobe_effect = yes
        if = {{ limit = {{ OR = {{ {reward_available} }} }}
            random_list = {{ {rewards} }}
        }}
    }}
}}
''')
    events = '''namespace = moe_wardrobe
moe_wardrobe.0001 = {
    scope = none
    hidden = yes
    immediate = {
        every_player = {
            remove_variable = moe_wardrobe_lobby
            # Availability is transient and must be rebuilt after a pack unload.
            moe_clear_wardrobe_availability_effect = yes
            trigger_event = { on_action = moe_register_wardrobe }
            trigger_event = { id = moe_wardrobe.0002 days = 1 }
        }
    }
}
moe_wardrobe.0002 = {
    type = character_event
    hidden = yes
    trigger = { is_ai = no }
    immediate = { moe_refresh_wardrobe_effect = yes }
}
moe_wardrobe.0003 = {
    type = character_event
    hidden = yes
    immediate = {
        var:moe_tailor_owner ?= { moe_finish_tailor_effect = yes }
        remove_variable = moe_tailor_owner
        moe_refresh_wardrobe_effect = yes
        if = { limit = { is_ai = no }
            send_interface_toast = { type = event_generic_good title = moe_tailor_finished }
        }
    }
}
'''
    effects.append('moe_clear_wardrobe_availability_effect = {\n' + '\n'.join(f'remove_variable = moe_outfit_available_{c["ck"]}' for c in CHARS) + '\n}\n')
    actions = '''on_game_start = { events = { moe_wardrobe.0001 } }
on_game_start_after_lobby = { events = { moe_wardrobe.0001 } }
quarterly_playable_pulse = { events = { moe_wardrobe.0002 } }
moe_register_wardrobe = { }
'''
    interactions = '''moe_request_same_outfit = {
    category = interaction_category_friendly
    icon = designate_favorite
    interface_priority = 49
    desc = moe_request_same_outfit_desc
    auto_accept = yes
    is_shown = {
        scope:actor = { is_ai = no }
        scope:recipient = {
            NOT = { this = scope:actor }
            moe_portrait_preview_actor = no
            has_character_flag = moe_portrait
            moe_cosplay_eligible = yes
        }
    }
    is_valid_showing_failures_only = {
        custom_tooltip = { text = moe_request_same_outfit_requirements moe_can_request_outfit = yes }
    }
    on_accept = { moe_request_outfit_effect = yes }
}
'''
    # Append to the actual completion effect used by both pilgrimage and Hajj.
    # Retain every native reward, memory and activity-log field.
    pilgrimage = _extend_vanilla_definition(game, PILGRIMAGE_SOURCE,
        'pilgrimage_completed_log_entry_effect', '\n    moe_pilgrimage_outfit_effect = yes\n')
    cn = dict(moe_request_same_outfit='申请同款衣装',
        moe_request_same_outfit_desc=f'对方对你的好感达到{REQUEST_OPINION}后，支付{REQUEST_GOLD}金币，永久解锁对方当前穿着的整套外观。',
        moe_request_same_outfit_requirements=f'需要对方对你的好感至少{REQUEST_OPINION}、{REQUEST_GOLD}金币，以及尚未收藏且已安装的款式。旧档未记录款式的角色暂不能提供同款。',
        moe_wardrobe_locked_tt='需要已收藏该款式。可向穿着同款的角色申请、委托裁缝或完成朝圣取得。首次选装可领取一套起始款。',
        moe_tailor_requirements_tt=f'需要{TAILOR_GOLD}金币、尚未收藏且已安装的款式，家族当前没有进行中的委托。制作{TAILOR_DAYS}天。',
        moe_tailor_started=f'裁缝已接单：{TAILOR_DAYS}天后衣装图样入藏。',
        moe_tailor_finished='裁缝已完成衣装图样，现可免费换装。',
        moe_outfit_requested='同款衣装已解锁',moe_wardrobe_tab='衣橱与裁缝',moe_models_tab='人物预览',
        moe_wardrobe_wear='免费换装',moe_wardrobe_tailor='委托裁缝',moe_wardrobe_owned='已收藏',
        moe_wardrobe_hint=f'同款：好感{REQUEST_OPINION}／{REQUEST_GOLD}金；裁缝：{TAILOR_GOLD}金／{TAILOR_DAYS}天；朝圣奖励一款。',
        moe_wardrobe_target_tt='可为自己或自己宫廷中的角色换装。')
    en = dict(moe_request_same_outfit='Request Matching Outfit',
        moe_request_same_outfit_desc=f'With at least {REQUEST_OPINION} opinion of you, pay {REQUEST_GOLD} gold to permanently learn their current complete appearance.',
        moe_request_same_outfit_requirements=f'Requires their opinion of you at least {REQUEST_OPINION}, {REQUEST_GOLD} gold, and an installed, uncollected outfit. Unrecorded appearances from old saves cannot supply a pattern yet.',
        moe_wardrobe_locked_tt='Collect this outfit through a matching-outfit request, a tailor, or pilgrimage. Your first selection may claim one starter outfit.',
        moe_tailor_requirements_tt=f'Requires {TAILOR_GOLD} gold, an installed uncollected outfit and no ongoing house commission. Takes {TAILOR_DAYS} days.',
        moe_tailor_started=f'The tailor has begun. The pattern will be ready in {TAILOR_DAYS} days.',
        moe_tailor_finished='Your outfit pattern is ready. Changing clothes is free.',
        moe_outfit_requested='Matching outfit unlocked',moe_wardrobe_tab='Wardrobe & Tailor',moe_models_tab='Portraits',
        moe_wardrobe_wear='Wear for Free',moe_wardrobe_tailor='Commission',moe_wardrobe_owned='Collected',
        moe_wardrobe_hint=f'Request: {REQUEST_OPINION} opinion / {REQUEST_GOLD} gold. Tailor: {TAILOR_GOLD} gold / {TAILOR_DAYS} days. Pilgrimage: one outfit.',
        moe_wardrobe_target_tt='You may dress yourself or members of your court.')
    for c in CHARS:
        cn['moe_wardrobe_name_'+c['ck']] = c['name_cn']
        en['moe_wardrobe_name_'+c['ck']] = c['name_en']
        cn['moe_pilgrimage_outfit_'+c['ck']] = '巡礼纪念衣装：'+c['name_cn']
        en['moe_pilgrimage_outfit_'+c['ck']] = 'Pilgrimage outfit: '+c['name_en']
    result = {
        'common/scripted_triggers/42_moe_wardrobe.txt': '\n'.join(triggers),
        'common/scripted_effects/42_moe_wardrobe.txt': '\n'.join(effects),
        'common/scripted_guis/42_moe_wardrobe.txt': '\n'.join(guis),
        'common/character_interactions/42_moe_wardrobe.txt': interactions,
        'common/on_action/zz_moe_wardrobe.txt': actions,
        'events/moe_wardrobe.txt': events,
        PILGRIMAGE_OUTPUT: pilgrimage,
    }
    for lang, loc in [('simp_chinese', cn), ('english', en)]:
        result[f'localization/{lang}/moe_wardrobe_l_{lang}.yml'] = f'l_{lang}:\n' + ''.join(
            f' {k}:0 "{v}"\n' for k, v in loc.items())
    return result


def core_files(game):
    """Free, unlocked baseline. The optional collection mod replaces these paths.

    Exact-path VFS overrides plus an explicit launcher dependency avoid relying
    on duplicate top-level definitions to switch collection on and off. Removing
    the add-on restores these rules even if a save retains old unlock variables.
    """
    acquisition = files(game)
    from moe_culture_ethnicities import _LEXER

    # Copy the pure cycling helpers verbatim; both modes use the same can-wear
    # contract. Everything that charges, rewards or maintains ownership stays
    # in the acquisition add-on.
    def definition(text, key):
        import re
        match = re.search(r'^' + re.escape(key) + r'\s*=\s*\{', text, re.M)
        assert match, key
        depth = 1
        for token in _LEXER.finditer(text, match.end()):
            depth += (token.group() == '{') - (token.group() == '}')
            if depth == 0: return text[match.start():token.end()] + '\n'
        raise ValueError(key)

    triggers=[]; effects=['moe_refresh_wardrobe_effect = { }\n']; guis=[]
    for c in CHARS:
        ck, slot = c['ck'], c['slot']
        sex = c.get('sex', 'female')
        triggers.append(f'''
moe_owns_{ck} = {{ always = yes }}
moe_can_wear_{ck} = {{ has_variable = moe_outfit_available_{ck} }}
moe_can_tailor_{ck} = {{ always = no }}
''')
        triggers.append(definition(acquisition['common/scripted_triggers/42_moe_wardrobe.txt'], f'moe_has_owned_cycle_{slot}'))
        effects.append(f'''
moe_wear_{ck}_effect = {{
    if = {{ limit = {{
        moe_can_wear_{ck} = yes
        exists = var:moe_cos_target
        var:moe_cos_target = {{ moe_cosplay_eligible = yes {sex_ok(sex)} = yes }}
    }}
        save_scope_as = moe_wardrobe_actor
        var:moe_cos_target = {{ trigger_event = moe_wardrobe_model_{ck}.0002 }}
    }}
}}
''')
        effects.append(definition(acquisition['common/scripted_effects/42_moe_wardrobe.txt'],f'moe_cycle_owned_{slot}_effect'))
        for key in (f'moe_wardrobe_wear_{ck}',f'moe_wardrobe_owned_{ck}'):
            guis.append(definition(acquisition['common/scripted_guis/42_moe_wardrobe.txt'],key))
        guis.append(f'moe_wardrobe_tailor_{ck} = {{ scope = character is_valid = {{ always = no }} }}\n')
    effects.append(definition(acquisition['common/scripted_effects/42_moe_wardrobe.txt'],'moe_clear_wardrobe_availability_effect'))
    guis.append('moe_collection_enabled = { scope = character is_shown = { always = no } }\n')
    result = {
        'common/scripted_triggers/42_moe_wardrobe.txt':'\n'.join(triggers),
        'common/scripted_effects/42_moe_wardrobe.txt':'\n'.join(effects),
        'common/scripted_guis/42_moe_wardrobe.txt':'\n'.join(guis),
        'common/on_action/zz_moe_wardrobe.txt':'''on_game_start = { events = { moe_wardrobe.0001 } }
on_game_start_after_lobby = { events = { moe_wardrobe.0001 } }
moe_register_wardrobe = { }
''',
        'events/moe_wardrobe.txt':'''namespace = moe_wardrobe
moe_wardrobe.0001 = {
    scope = none
    hidden = yes
    immediate = {
        every_player = {
            remove_variable = moe_wardrobe_lobby
            moe_clear_wardrobe_availability_effect = yes
            trigger_event = { on_action = moe_register_wardrobe }
        }
    }
}
# Compatibility no-ops for events queued before disabling the collection mod.
moe_wardrobe.0002 = { type = character_event hidden = yes immediate = { } }
moe_wardrobe.0003 = { type = character_event hidden = yes immediate = { } }
''',
    }
    for lang in ('english','simp_chinese'):
        path=f'localization/{lang}/moe_wardrobe_l_{lang}.yml'
        # Keep common UI labels; no acquisition interactions or prices in core.
        loc=[]
        replacements = {
            'moe_wardrobe_tab': '衣橱' if lang=='simp_chinese' else 'Wardrobe',
            'moe_wardrobe_owned': '可使用' if lang=='simp_chinese' else 'Available',
            'moe_wardrobe_locked_tt': '需要已安装的模型包。' if lang=='simp_chinese' else 'Requires the installed model pack.',
            'moe_wardrobe_hint': '所有已安装衣装默认解锁，换装与姿势免费。' if lang=='simp_chinese' else 'All installed outfits are unlocked. Changing outfits and poses is free.',
        }
        allowed={'moe_models_tab','moe_wardrobe_target_tt','moe_wardrobe_wear','moe_wardrobe_tailor'}
        for line in acquisition[path].splitlines()[1:]:
            key=line.strip().split(':',1)[0]
            if key in replacements:loc.append(f' {key}:0 "{replacements[key]}"')
            elif key in allowed or key.startswith('moe_wardrobe_name_'):loc.append(line)
        result[path]=f'l_{lang}:\n'+'\n'.join(loc)+'\n'
    return result
