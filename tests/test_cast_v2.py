"""Offline v2 selection/choice rules; no native gameplay or throw support."""
import copy
import math
import unittest

from bg3data.companion.native import NativeBackend
from bg3data.companion.protocol import Rejected, action, digest, observation, validate_context
from bg3data.companion.service import CompanionService
from bg3data.companion.simulation import ACTOR, ENEMY, FRIEND, HIDDEN, HOST, SimulationBackend


class CastV2Tests(unittest.TestCase):
    def setUp(self):
        self.backend = SimulationBackend()
        self.service = CompanionService(self.backend)
        self.service.bind(ACTOR); self.service.control(True)

    def tearDown(self): self.service.close()

    def choice(self, spell, observation=None):
        return next(c for c in (observation or self.service.observe())['actor']['cast_options'] if c['spell_id'] == spell)

    def request(self, spell, selections, client='cast_v2'):
        observed=self.service.observe(); option=self.choice(spell,observed)
        value={'kind':'cast','schema_version':2,'spell_option_id':option['id'],'targets':selections}
        return self.service.request(observed['observation_id'],value,client),value

    def test_v1_shape_unchanged_and_v2_explicit_version_is_required(self):
        original={'kind':'cast','spell_id':'Target_HealingWord','target_uuid':FRIEND}
        self.assertEqual(original,action(original))
        value={'kind':'cast','schema_version':2,'spell_option_id':'choice','targets':[{'entity_uuid':ENEMY}]}
        self.assertEqual(value,action(value))
        for changed in (dict(value,schema_version=1),dict(value,schema_version=True),dict(value,schema_version=3),
                        {k:v for k,v in value.items() if k!='schema_version'}):
            with self.assertRaises(Rejected): action(changed)
        with self.assertRaises(Rejected): action(value,supported_versions=(1,))

    def test_order_and_multiplicity_remain_canonical_and_affect_digest(self):
        base={'kind':'cast','schema_version':2,'spell_option_id':'choice','targets':[{'entity_uuid':ENEMY},{'entity_uuid':FRIEND},{'entity_uuid':ENEMY}]}
        self.assertEqual(base,action(base))
        self.assertNotEqual(digest(action(base)),digest(action(dict(base,targets=list(reversed(base['targets'][:-1]))))))

    def test_invalid_selection_budget_coordinates_and_native_fields_rejected(self):
        base={'kind':'cast','schema_version':2,'spell_option_id':'choice','targets':[{'entity_uuid':ENEMY}]}
        cases=[dict(base,targets=[]),dict(base,targets=[{'entity_uuid':ENEMY}]*17),dict(base,targets=[{'position':{'x':math.nan,'y':0,'z':0}}]),
               dict(base,targets=[{'entity_uuid':ENEMY,'position':{'x':0,'y':0,'z':0}}]),dict(base,targets=[{'entity_uuid':ENEMY,'TargetingType':'Target'}]),
               dict(base,Source=ENEMY),dict(base,slot_level=3),dict(base,spell_id='fake'),dict(base,CastOptions=['Forced'])]
        for value in cases:
            with self.assertRaises(Rejected): action(value)

    def test_duplicate_prototype_sources_have_distinct_opaque_options_and_private_fields_are_stripped(self):
        self.backend.state['party'][0]['cast_options']=[{'private':'other player'}]
        seen=self.service.observe()
        weapon=[c for c in seen['actor']['cast_options'] if c['spell_id']=='Target_MainHandAttack']
        self.assertEqual(2,len(weapon)); self.assertNotEqual(weapon[0]['id'],weapon[1]['id'])
        for c in seen['actor']['cast_options']: self.assertNotIn('_source_record',c)
        self.assertNotIn('cast_options',next(u for u in seen['party'] if u['uuid']==HOST))
        self.assertNotIn('cast_options',next(u for u in seen['combatants'] if u['uuid']==ENEMY))

    def test_repeated_projectile_allocation_pays_once_and_preserves_order(self):
        hidden=next(u for u in self.backend.state['combatants'] if u['uuid']==HIDDEN)
        hidden.update(visible=True,position={'x':5,'y':0,'z':0})
        selections=[{'entity_uuid':ENEMY},{'entity_uuid':HIDDEN},{'entity_uuid':ENEMY}]
        result,value=self.request('Projectile_MagicMissile',selections)
        self.assertEqual('completed',result['status'])
        self.assertEqual(selections,result['events'][0]['targets'])
        self.assertEqual(12,self.backend._units()[ENEMY]['hp'])
        self.assertEqual(16,self.backend._units()[HIDDEN]['hp'])
        self.assertEqual(0,self.backend.state['actor']['resources']['ActionPoint']['0']['amount'])
        self.assertEqual(1,self.backend.state['actor']['resources']['SpellSlot']['1']['amount'])
        self.assertEqual(result,self.service.request('consumed',value,'cast_v2'))
        self.assertEqual(1,len(self.backend.submissions))

    def test_higher_observed_choice_uses_distinct_prototype_and_higher_slot(self):
        result,_=self.request('Fixture_HealingWord_Level3',[{'entity_uuid':FRIEND}])
        self.assertEqual('completed',result['status'])
        self.assertEqual('Fixture_HealingWord_Level3',result['events'][0]['spell_id'])
        slots=self.backend.state['actor']['resources']['SpellSlot']
        self.assertEqual(0,slots['3']['amount']); self.assertEqual(2,slots['1']['amount'])

    def test_depleted_higher_slots_do_not_fall_back_or_partially_pay(self):
        resources=self.backend.state['actor']['resources']; resources['SpellSlot']['3']['amount']=0
        before=copy.deepcopy(resources)
        result,_=self.request('Fixture_HealingWord_Level3',[{'entity_uuid':FRIEND}])
        self.assertEqual('rejected',result['status']); self.assertEqual('insufficient_resources',result['reason'])
        self.assertEqual(before,resources)

    def test_position_selects_area_center_and_does_not_require_enemy_enumeration(self):
        result,_=self.request('Projectile_Fireball',[{'position':{'x':4,'y':0,'z':0}}])
        self.assertEqual('completed',result['status']); self.assertTrue(self.backend._units()[ENEMY]['dead'])
        self.assertNotIn('affected_hidden_targets',result['events'][0])

    def test_unknown_hidden_stale_and_over_budget_selections_fail_before_submission(self):
        observed=self.service.observe(); missile=self.choice('Projectile_MagicMissile',observed)
        for targets in ([{'entity_uuid':HIDDEN}], [{'entity_uuid':ENEMY}]*4, [{'position':{'x':4,'y':0,'z':0}}]):
            value={'kind':'cast','schema_version':2,'spell_option_id':missile['id'],'targets':targets}
            with self.assertRaises(Rejected): self.service.preview(observed['observation_id'],value)
        self.assertEqual([],self.backend.submissions)
        self.backend.state['session_id']='offline-world-new'
        fresh=self.service.observe()
        with self.assertRaises(Rejected) as rejected:
            self.service.preview(fresh['observation_id'],{'kind':'cast','schema_version':2,'spell_option_id':missile['id'],'targets':[{'entity_uuid':ENEMY}]})
        self.assertEqual('stale_cast_option',rejected.exception.code)

    def test_source_or_revision_change_invalidates_old_token_and_observation(self):
        observed=self.service.observe(); option=self.choice('Projectile_MagicMissile',observed)
        value={'kind':'cast','schema_version':2,'spell_option_id':option['id'],'targets':[{'entity_uuid':ENEMY}]}
        internal=next(c for c in self.backend.state['actor']['cast_options'] if c['id']==option['id'])
        internal['_source_record']='replacement_source'
        with self.assertRaises(Rejected) as rejected: self.service.preview(observed['observation_id'],value)
        self.assertEqual('state_changed',rejected.exception.code)
        fresh=self.service.observe()
        with self.assertRaises(Rejected): self.service.preview(fresh['observation_id'],value)
        self.assertEqual([],self.backend.submissions)

    def test_private_source_race_is_rechecked_before_any_resource_or_hp_mutation(self):
        original=self.backend._cast
        def changed_source(actor,value,events):
            option=next(c for c in actor['cast_options'] if c['id']==value['spell_option_id'])
            option['_source_record']='unknown_replacement_source'
            return original(actor,value,events)
        self.backend._cast=changed_source
        before=copy.deepcopy(self.backend.state['actor']['resources'])
        result,_=self.request('Projectile_MagicMissile',[{'entity_uuid':ENEMY}])
        self.assertEqual('rejected',result['status']); self.assertEqual('cast_choice_changed',result['reason'])
        self.assertEqual(before,self.backend.state['actor']['resources'])
        self.assertEqual(20,self.backend._units()[ENEMY]['hp'])

    def test_revision_change_reissues_choice_id_and_catalog_blocks_forged_cost_variant(self):
        observed=self.service.observe(); option=self.choice('Projectile_MagicMissile',observed)
        internal=next(c for c in self.backend.state['actor']['cast_options'] if c['id']==option['id'])
        internal['revision']='spellbook_2'
        fresh=self.service.observe(); new=self.choice('Projectile_MagicMissile',fresh)
        self.assertNotEqual(option['id'],new['id'])
        internal['slot_level']=3
        result,_=self.request('Projectile_MagicMissile',[{'entity_uuid':ENEMY}])
        self.assertEqual('rejected',result['status']); self.assertEqual('cast_choice_changed',result['reason'])
        self.assertEqual(2,self.backend.state['actor']['resources']['SpellSlot']['1']['amount'])

    def test_session_and_actor_binding_change_the_option_identity(self):
        old=self.backend.observe(ACTOR)['actor']['cast_options'][0]['id']
        self.backend.state['session_id']='offline-world-2'
        self.backend.state['actor']['cast_options'][0]['session_id']='offline-world-2'
        fresh=self.backend.observe(ACTOR)['actor']['cast_options'][0]['id']
        self.assertNotEqual(old,fresh)
        self.backend.state['actor']['uuid']='00000000-0000-0000-0000-000000000009'
        different_actor=self.backend.observe()['actor']['cast_options'][0]['id']
        self.assertNotEqual(fresh,different_actor)

    def test_nonrepeat_choice_duplicate_is_not_silently_deduplicated(self):
        public=observation(self.backend.observe(ACTOR),ACTOR)
        option=next(c for c in public['actor']['cast_options'] if c['spell_id']=='Target_MainHandAttack')
        option['max_targets']=2
        value=action({'kind':'cast','schema_version':2,'spell_option_id':option['id'],'targets':[{'entity_uuid':ENEMY}]*2})
        with self.assertRaises(Rejected) as rejected: validate_context(public,ACTOR,value)
        self.assertEqual('duplicate_target',rejected.exception.code)

    def test_capability_version_and_variant_feature_gates_fail_closed(self):
        original=self.backend.capabilities
        for change in ({'action_contract_versions':[1]}, {'action_features':{'cast_multiple_targets':False,'cast_upcast_options':True}},
                       {'action_features':{'cast_multiple_targets':True,'cast_upcast_options':False}}, {'simulation_only':False}):
            self.backend.capabilities=lambda change=change: dict(original(),**change)
            seen=self.service.observe(); option=self.choice('Projectile_MagicMissile',seen)
            value={'kind':'cast','schema_version':2,'spell_option_id':option['id'],'targets':[{'entity_uuid':ENEMY}]*2}
            with self.assertRaises(Rejected): self.service.preview(seen['observation_id'],value)
        self.assertEqual([],self.backend.submissions)
        native=CompanionService(NativeBackend())
        self.addCleanup(native.close)
        self.assertEqual([1],native.capabilities()['action_contract_versions'])
        self.assertFalse(any(native.capabilities()['action_features'].values()))
        self.assertFalse(any(native.capabilities()['actions'].values()))

    def test_legacy_throw_fields_remain_unsupported_and_raw_inventory_is_not_exposed(self):
        with self.assertRaises(Rejected) as rejected:
            action({'kind':'throw','schema_version':2,'spell_option_id':'throw','item_uuid':ENEMY,'target':{'entity_uuid':FRIEND}})
        self.assertEqual('invalid_action',rejected.exception.code)
        self.assertTrue(self.service.capabilities()['action_features']['throw_inventory_item'])
        self.backend.state['actor']['inventory_items']=[{'uuid':ENEMY,'private':'not validated'}]
        self.assertNotIn('inventory_items',self.service.observe()['actor'])

    def test_malformed_duplicate_or_unbounded_observed_options_fail_closed(self):
        original=copy.deepcopy(self.backend.state['actor']['cast_options'])
        for field,value in (('max_targets',17),('max_targets',True),('repeat_targets',None),
                            ('target_types',[{}]),('slot_level',True),('resource_mode','free_bypass')):
            raw=self.backend.observe(ACTOR)
            raw['actor']['cast_options'][0][field]=value
            with self.assertRaises(Rejected): observation(raw,ACTOR)
        raw=self.backend.observe(ACTOR)
        raw['actor']['cast_options'].append(copy.deepcopy(raw['actor']['cast_options'][0]))
        with self.assertRaises(Rejected): observation(raw,ACTOR)
        raw['actor']['cast_options']=original*10
        with self.assertRaises(Rejected): observation(raw,ACTOR)


if __name__=='__main__': unittest.main()
