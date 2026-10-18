"""Offline execution of emitted wardrobe rules, not a CK3 engine substitute.

Exercises transaction boundaries, delayed ownership, callback locks and rewards.
Unknown script operations fail rather than being silently treated as successful.
"""
import argparse
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools/lib'))
import moe_wardrobe as W
import moe_chars as X
import moe_cosplay_rules as CR

GAME = Path('E:/SteamLibrary/steamapps/common/Crusader Kings III/game')


def parse(text):
    ts = re.findall(r'"(?:\\.|[^"\\])*"|#[^\n]*|\?=|>=|<=|[{}=><]|[^\s{}=><]+', text)
    ts = [t for t in ts if not t.startswith('#')]
    pos = 0
    def block(inner=False):
        nonlocal pos
        out = []
        while pos < len(ts) and ts[pos] != '}':
            k, op, v = ts[pos:pos+3]
            assert op in ('=', '?=', '>=', '<=', '>', '<'), (k, op, v)
            pos += 3
            if v == '{':
                v = block(True)
            elif v.startswith('"'): v = v[1:-1]
            out.append((k, op, v))
        if inner:
            assert pos < len(ts) and ts[pos] == '}'
            pos += 1
        return out
    out = block()
    assert pos == len(ts)
    return out


def fields(rows):
    return {k:v for k, _, v in rows}


@dataclass(eq=False)
class Scope:
    kind: str = 'character'
    vars: dict = field(default_factory=dict)
    expiry: dict = field(default_factory=dict)
    flags: set = field(default_factory=lambda: {'moe_portrait'})
    house: object = None
    activity: object = None
    eligible: bool = True
    ai: bool = False
    alive: bool = True
    preview: bool = False
    gold: int = 500
    opinions: dict = field(default_factory=dict)
    ethnicity: str = 'unchanged'
    sex: str = 'female'


_SEX_TRIGGERS = None


def _sex_triggers():
    global _SEX_TRIGGERS
    if _SEX_TRIGGERS is None:
        _SEX_TRIGGERS = {k: v for k, _, v in parse(CR.files(GAME)['common/scripted_triggers/40_moe_cosplay.txt'])
                         if k.startswith('moe_sex_ok_')}
    return dict(_SEX_TRIGGERS)


class Engine:
    def __init__(self, collection=True):
        generated = W.files(GAME) if collection else W.core_files(GAME)
        for c in X.CHARS: generated.update(W.pack_files(c))
        self.effects = {}; self.triggers = {}; self.events = {}; self.guis = {}
        for path, text in generated.items():
            if path == W.PILGRIMAGE_OUTPUT or path.endswith('.yml') or '/on_action/' in path: continue
            rows = fields(parse(text))
            if '/scripted_effects/' in path: self.effects.update(rows)
            elif '/scripted_triggers/' in path: self.triggers.update(rows)
            elif '/scripted_guis/' in path: self.guis.update(rows)
            elif path.startswith('events/'): self.events.update({k:v for k,v in rows.items() if k != 'namespace'})
        self.scopes = {}; self.root = None; self.day = 0; self.queue = []; self.toasts = []
        self.globals = set()
        # Toggle-aware sex triggers come from the core rules; moe_cosplay_eligible stays a Scope attribute.
        self.triggers.update(_sex_triggers())
        assert all(CR.sex_ok(sex) in self.triggers for sex in ('female', 'male'))

    def clean(self, who):
        for key in list(who.expiry):
            if who.expiry[key] <= self.day:
                who.vars.pop(key, None); del who.expiry[key]

    def resolve(self, name, who):
        if name == 'this': return who
        if name == 'root': return self.root
        if name == 'house': return who.house
        if name == 'involved_activity': return who.activity
        if name.startswith('scope:'): return self.scopes.get(name[6:])
        if name.startswith('var:'):
            self.clean(who); return who.vars.get(name[4:])
        try: return float(name)
        except ValueError: return name

    def cond(self, rows, who):
        if who is None: return False
        self.clean(who)
        result = []
        for k, op, v in rows:
            if k in ('AND', 'NOT', 'custom_tooltip'):
                ok = self.cond([r for r in v if r[0] != 'text'], who)
                if k == 'NOT': ok = not ok
            elif k == 'OR': ok = any(self.cond([r], who) for r in v)
            elif k == 'trigger_if':
                d = fields(v)
                ok = not self.cond(d['limit'], who) or self.cond([r for r in v if r[0] != 'limit'], who)
            elif k in self.triggers: ok = self.cond(self.triggers[k], who) == (v == 'yes')
            elif k == 'always': ok = v == 'yes'
            elif k == 'has_variable': ok = v in who.vars
            elif k == 'has_character_flag': ok = v in who.flags
            elif k == 'has_global_variable': ok = v in self.globals
            elif k == 'exists': ok = isinstance(self.resolve(v, who), Scope)
            elif k in ('is_ai', 'is_alive', 'moe_cosplay_eligible', 'moe_portrait_preview_actor'):
                attr = {'is_ai':'ai', 'is_alive':'alive', 'moe_cosplay_eligible':'eligible', 'moe_portrait_preview_actor':'preview'}[k]
                ok = getattr(who, attr) == (v == 'yes')
            elif k == 'is_female': ok = (who.sex == 'female') == (v == 'yes')
            elif k == 'is_male': ok = (who.sex == 'male') == (v == 'yes')
            elif k == 'opinion':
                d = fields(v); ok = who.opinions.get(self.resolve(d['target'], who), 0) >= float(d['value'])
            elif isinstance(v, list): ok = self.cond(v, self.resolve(k, who))
            else:
                left = who.gold if k == 'gold' else self.resolve(k, who)
                right = self.resolve(v, who)
                if op == '=': ok = left == right
                elif op == '>=': ok = left is not None and left >= right
                else: raise AssertionError(('Unsupported comparison', k,op,v))
            result.append(ok)
        return all(result)

    def run(self, name, who):
        self.root = who
        self.effect(self.effects[name], who)

    def event(self, name, who):
        event = fields(self.events[name]); old = self.root; self.root = who
        if self.cond(event.get('trigger', []), who): self.effect(event['immediate'], who)
        self.root = old

    def effect(self, rows, who):
        self.clean(who); branch = False
        for k, op, v in rows:
            if k in ('if','else_if','else'):
                if k == 'if': branch = False
                if not branch and (k == 'else' or self.cond(fields(v)['limit'], who)):
                    self.effect([r for r in v if r[0] != 'limit'], who); branch = True
            elif k in self.effects: self.effect(self.effects[k], who)
            elif k == 'set_variable':
                d = fields(v); who.vars[d['name']] = self.resolve(d['value'], who)
                if 'days' in d: who.expiry[d['name']] = self.day + int(d['days'])
            elif k == 'remove_variable': who.vars.pop(v, None); who.expiry.pop(v, None)
            elif k == 'save_scope_as': self.scopes[v] = who
            elif k == 'add_gold': who.gold += float(v)
            elif k == 'add_character_flag': who.flags.add(v)
            elif k == 'remove_character_flag': who.flags.discard(v)
            elif k == 'set_ethnicity': who.ethnicity = v
            elif k == 'send_interface_toast': self.toasts.append(fields(v)['title'])
            elif k == 'trigger_event':
                if isinstance(v, str): self.event(v, who)
                else:
                    d = fields(v)
                    assert 'id' in d and 'days' in d, d
                    self.queue.append((self.day+int(d['days']), d['id'], who, self.scopes.copy()))
            elif k == 'random_list':
                choices = [parts for _,_,parts in v if self.cond(fields(parts)['trigger'], who)]
                assert choices, 'Empty reward selection'
                self.effect([r for r in choices[0] if r[0] != 'trigger'], who)
            elif isinstance(v, list):
                target = self.resolve(k, who)
                if target is None and op == '?=': continue
                assert isinstance(target, Scope), (k,target)
                self.effect(v, target)
            else: raise AssertionError(('Unsupported effect',k,op,v))


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--report',type=Path,required=True); args=ap.parse_args()
    checked=[]
    def check(name, value):
        assert value,name
        checked.append(name)
    forms=X.CHARS
    for c in forms:
        sex=c.get('sex','female')
        opposite='male' if sex == 'female' else 'female'
        e=Engine(); h=Scope(kind='house'); a=Scope(house=h,sex=sex); b=Scope(ai=True,sex=sex)
        a.vars.update({f'moe_outfit_available_{c["ck"]}':1, 'moe_wardrobe_seeded':1, 'moe_cos_target':a})
        b.vars[W.CURRENT]=c['slot']; b.opinions[a]=W.REQUEST_OPINION
        e.scopes.update(actor=a,recipient=b)
        key='moe_outfit_'+c['ck']; wear='moe_wear_'+c['ck']+'_effect'
        e.run(wear,a); check(c['ck']+'_locked_cannot_apply',a.ethnicity=='unchanged')
        b.opinions[a]=W.REQUEST_OPINION-1
        e.run('moe_request_outfit_effect',a)
        check(c['ck']+'_opinion_direction_and_threshold',key not in h.vars and a.gold==500)
        a.opinions[b]=100
        e.run('moe_request_outfit_effect',a)
        check(c['ck']+'_reverse_opinion_cannot_purchase',key not in h.vars)
        b.opinions[a]=W.REQUEST_OPINION; a.gold=W.REQUEST_GOLD-1
        e.run('moe_request_outfit_effect',a)
        check(c['ck']+'_insufficient_gold_no_effect',key not in h.vars and b.gold==500)
        a.gold=500; e.run('moe_request_outfit_effect',a)
        check(c['ck']+'_request_transfers_once',key in h.vars and a.gold==500-W.REQUEST_GOLD and b.gold==500+W.REQUEST_GOLD)
        e.run('moe_request_outfit_effect',a)
        check(c['ck']+'_duplicate_request_free_noop',a.gold==500-W.REQUEST_GOLD)
        e.run(wear,a)
        check(c['ck']+'_wear_free_exact_form',a.vars[W.CURRENT]==c['slot'] and a.gold==500-W.REQUEST_GOLD)
        wrong=Scope(house=h,sex=opposite)
        wrong.vars.update({f'moe_outfit_available_{c["ck"]}':1,'moe_wardrobe_seeded':1,'moe_cos_target':wrong})
        e.run(wear,wrong)
        check(c['ck']+'_cannot_apply_other_sex_model',wrong.ethnicity=='unchanged' and W.CURRENT not in wrong.vars)
        e.globals.add(CR.IGNORE_GENDER); e.run(wear,wrong)
        check(c['ck']+'_ignore_gender_applies_other_sex_model',wrong.ethnicity==c['type']+'_ethnicity' and wrong.vars[W.CURRENT]==c['slot'])
        e.globals.discard(CR.IGNORE_GENDER)
        a.eligible=False; a.ethnicity='unchanged'; e.run(wear,a)
        check(c['ck']+'_lost_eligibility_blocks',a.ethnicity=='unchanged')
        a.eligible=True; a.vars.pop(f'moe_outfit_available_{c["ck"]}'); e.run(wear,a)
        check(c['ck']+'_missing_pack_blocks_without_losing_collection',a.ethnicity=='unchanged' and key in h.vars)
        a.vars[f'moe_outfit_available_{c["ck"]}']=1
        heir=Scope(house=h,sex=sex); heir.vars.update(a.vars); heir.vars['moe_cos_target']=heir
        e.run(wear,heir); check(c['ck']+'_same_house_heir_owns',heir.vars[W.CURRENT]==c['slot'])

        e=Engine(); h=Scope(kind='house'); a=Scope(house=h)
        a.vars[f'moe_outfit_available_{c["ck"]}']=1
        e.run('moe_tailor_'+c['ck']+'_effect',a)
        check(c['ck']+'_commission_charged_not_early',a.gold==500-W.TAILOR_GOLD and key not in h.vars)
        e.run('moe_tailor_'+c['ck']+'_effect',a)
        check(c['ck']+'_duplicate_commission_blocked',a.gold==500-W.TAILOR_GOLD and len(e.queue)==1)
        e.day=W.TAILOR_DAYS-1;e.run('moe_refresh_wardrobe_effect',a)
        check(c['ck']+'_not_ready_before_deadline',key not in h.vars)
        a.alive=False; heir=Scope(house=h);e.day=W.TAILOR_DAYS;e.run('moe_refresh_wardrobe_effect',heir)
        check(c['ck']+'_order_survives_patron_death',key in h.vars and 'moe_tailor_order' not in h.vars)

    e=Engine(); a=Scope(house=Scope(kind='house')); a.vars['moe_cos_target']=a
    for c in forms:a.vars['moe_outfit_available_'+c['ck']]=1
    e.run('moe_wear_'+forms[0]['ck']+'_effect',a)
    e.run('moe_wear_'+forms[1]['ck']+'_effect',a)
    check('one_starter_only',a.vars[W.CURRENT]==forms[0]['slot'])
    lobby=Scope();lobby.vars.update(a.vars);lobby.vars['moe_cos_target']=lobby;lobby.vars['moe_wardrobe_lobby']=1
    for c in forms:e.run('moe_wear_'+c['ck']+'_effect',lobby)
    check('lobby_preview_never_collects',not any(k.startswith('moe_outfit_') and not k.startswith('moe_outfit_available_') for k in lobby.vars))
    lobby.vars.pop('moe_wardrobe_lobby');lobby.vars.pop('moe_wardrobe_seeded',None);e.run('moe_refresh_wardrobe_effect',lobby)
    check('lobby_final_choice_only',sum(k.startswith('moe_outfit_') and not k.startswith('moe_outfit_available_') for k in lobby.vars)==1)
    for c in forms:e.run('moe_unlock_'+c['ck']+'_effect',a)
    family=next(W.family(c) for c in forms if len(W.family(c))>2)
    a.vars[W.CURRENT]=family[0]['slot'];a.house.vars.pop('moe_outfit_'+family[1]['ck'])
    e.run('moe_cycle_owned_'+str(family[0]['slot'])+'_effect',a)
    check('cycle_skips_locked_middle_skin',a.vars[W.CURRENT]==family[2]['slot'])
    p=Scope(house=Scope(kind='house'),activity=Scope(kind='activity'))
    for c in forms:p.vars['moe_outfit_available_'+c['ck']]=1
    e.run('moe_pilgrimage_outfit_effect',p);e.run('moe_pilgrimage_outfit_effect',p)
    check('pilgrimage_one_reward_per_activity',len(p.house.vars)==1)
    p.activity=Scope(kind='activity');e.run('moe_pilgrimage_outfit_effect',p)
    check('pilgrimage_never_draws_owned',len(p.house.vars)==2)
    p.ai=True;p.activity=Scope(kind='activity');e.run('moe_pilgrimage_outfit_effect',p)
    check('random_npc_does_not_need_collection',len(p.house.vars)==2)
    p.ai=False;p.activity=None;e.run('moe_pilgrimage_outfit_effect',p)
    check('ordinary_travel_not_rewarded',len(p.house.vars)==2)
    p.activity=Scope(kind='activity')
    for c in forms:e.run('moe_unlock_'+c['ck']+'_effect',p)
    before=len(e.toasts);e.run('moe_pilgrimage_outfit_effect',p)
    check('completed_collection_no_empty_random',len(e.toasts)==before)
    # Native completion definition must be preserved exactly before extension.
    generated=W.files(GAME)
    plain=W._extend_vanilla_definition(GAME,W.PILGRIMAGE_SOURCE,'pilgrimage_completed_log_entry_effect','')
    check('native_pilgrimage_rewards_preserved',generated[W.PILGRIMAGE_OUTPUT].replace('\n    moe_pilgrimage_outfit_effect = yes\n','')==plain)
    # Residual saved locks/orders must not affect the default all-unlocked mod;
    # with the switch off each character only receives models of their own sex,
    # with it on every installed model of either sex.
    for switch in (False, True):
        for sex in ('female','male'):
            e=Engine(collection=False); a=Scope(sex=sex); a.vars['moe_cos_target']=a
            if switch: e.globals.add(CR.IGNORE_GENDER)
            a.vars.update(moe_wardrobe_seeded=1,moe_tailor_order=999)
            for c in forms:
                a.vars['moe_outfit_available_'+c['ck']]=1
                before=a.vars.get(W.CURRENT)
                e.run('moe_wear_'+c['ck']+'_effect',a)
                same=c.get('sex','female')==sex
                if same or switch:
                    check(f'core_all_unlocked_{switch}_{sex}_'+c['ck'],a.vars[W.CURRENT]==c['slot'] and a.gold==500)
                else:
                    check(f'core_other_sex_blocked_without_switch_{sex}_'+c['ck'],a.vars.get(W.CURRENT)==before)
    core=W.core_files(GAME)
    check('acquisition_not_emitted_into_core',W.PILGRIMAGE_OUTPUT not in core and
          not any('/character_interactions/' in p for p in core) and
          'add_gold' not in '\n'.join(core.values()))
    e.event('moe_wardrobe.0003',a)
    check('disabled_collection_pending_event_safe',a.gold==500)
    report=dict(status='passed_offline_wardrobe_checks',count=len(checked),checks=checked,
        source_sha256=hashlib.sha256(Path(W.__file__).read_bytes()).hexdigest(),actual_game_loaded=False)
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k!='checks'}))


if __name__=='__main__':main()
