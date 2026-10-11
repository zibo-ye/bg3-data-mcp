"""Exact owned inventory throw fixtures. Not native execution acceptance."""
import copy
import math
import tempfile
import unittest
from pathlib import Path

from bg3data.companion.native import NativeBackend
from bg3data.companion.protocol import Rejected, action, observation
from bg3data.companion.service import CompanionService
from bg3data.companion.simulation import (ACTOR, ENEMY, HIDDEN, HOST, SimulationBackend,
                                         THROW_WEAPON, THROW_CONSUMABLE, THROW_RETURNING)


class ThrowV2Tests(unittest.TestCase):
    def setUp(self):
        self.backend=SimulationBackend(); self.service=CompanionService(self.backend)
        self.service.bind(ACTOR); self.service.control(True)

    def tearDown(self): self.service.close()

    def observed_action(self,item=THROW_WEAPON,target=None):
        seen=self.service.observe()
        choice=next(i for i in seen['actor']['inventory_options'] if i['item_uuid']==item)
        return seen,dict(kind='throw',schema_version=2,inventory_option_id=choice['id'],target=target or {'entity_uuid':ENEMY})

    def request(self,item=THROW_WEAPON,target=None,client='throw_one'):
        seen,value=self.observed_action(item,target)
        return self.service.request(seen['observation_id'],value,client),value

    def item(self,uuid): return next(i for i in self.backend.state['actor']['inventory_items'] if i['item_uuid']==uuid)

    def test_strict_version_and_single_target_only_no_engine_or_item_fields(self):
        _,base=self.observed_action()
        self.assertEqual(base,action(base))
        cases=[dict(base,item_uuid=THROW_WEAPON),dict(base,quantity=2),dict(base,spell_option_id='native_guess'),dict(base,Source=ENEMY),
               dict(base,target={'entity_uuid':ENEMY,'position':{'x':4,'y':0,'z':0}}),dict(base,target=[{'entity_uuid':ENEMY}]*2),
               dict(base,target={'position':{'x':math.inf,'y':0,'z':0}}),dict(base,schema_version=1),dict(base,schema_version=True),
               {k:v for k,v in base.items() if k!='schema_version'}]
        for value in cases:
            with self.assertRaises(Rejected): action(value)
        with self.assertRaises(Rejected): action(base,supported_versions=(1,))

    def test_actor_only_verified_inventory_no_templates_or_other_player_sources(self):
        self.backend.state['party'][0]['inventory_options']=[dict(private='human_inventory')]
        seen=self.service.observe(); options=seen['actor']['inventory_options']
        self.assertEqual(3,len(options)); self.assertEqual({THROW_WEAPON,THROW_CONSUMABLE,THROW_RETURNING},{i['item_uuid'] for i in options})
        for option in options:
            self.assertEqual(ACTOR,option['owner_uuid']); self.assertTrue(option['ownership_verified'])
            self.assertFalse(any(k.startswith('_') for k in option))
        self.assertNotIn('inventory_items',seen['actor'])
        self.assertNotIn('inventory_options',next(u for u in seen['party'] if u['uuid']==HOST))

    def test_weapon_cost_damage_and_exact_item_instance_moves_to_world(self):
        result,value=self.request()
        self.assertEqual('completed',result['status']); self.assertEqual(15,self.backend._units()[ENEMY]['hp'])
        self.assertEqual(0,self.backend.state['actor']['resources']['ActionPoint']['0']['amount'])
        self.assertEqual(0,self.item(THROW_WEAPON)['quantity']); self.assertIsNone(self.item(THROW_WEAPON)['owner_uuid'])
        self.assertEqual(THROW_WEAPON,self.backend.world_items[0]['item_uuid'])
        self.assertEqual('dropped',result['events'][0]['disposition'])
        self.assertEqual(result,self.service.request('consumed',value,'throw_one'))
        self.assertEqual(1,len(self.backend.submissions)); self.assertEqual(1,len(self.backend.world_items))

    def test_consumable_decrements_exact_stack_once_and_reissues_choice(self):
        _,old=self.observed_action(THROW_CONSUMABLE)
        result,_=self.request(THROW_CONSUMABLE)
        self.assertEqual('consumed',result['events'][0]['disposition']); self.assertEqual(1,self.item(THROW_CONSUMABLE)['quantity'])
        self.assertEqual([],self.backend.world_items)
        seen,new=self.observed_action(THROW_CONSUMABLE)
        self.assertNotEqual(old['inventory_option_id'],new['inventory_option_id'])
        with self.assertRaises(Rejected): self.service.preview(seen['observation_id'],old)

    def test_returning_weapon_stays_owned_but_costs_action_and_changes_revision(self):
        result,_=self.request(THROW_RETURNING)
        self.assertEqual('returned',result['events'][0]['disposition'])
        self.assertEqual(1,self.item(THROW_RETURNING)['quantity']); self.assertEqual(ACTOR,self.item(THROW_RETURNING)['owner_uuid'])
        self.assertEqual([],self.backend.world_items); self.assertEqual('inventory_2',self.item(THROW_RETURNING)['revision'])

    def test_position_target_is_explicit_and_blocked_or_out_of_range_has_no_partial_change(self):
        for target,blocked,reason in (({'position':{'x':30,'y':0,'z':0}},False,'out_of_range'),
                                      ({'position':{'x':4,'y':0,'z':0}},True,'path_unavailable')):
            self.service.close(); self.setUp()
            if blocked: self.backend.blocked.add((4,0))
            before=copy.deepcopy(self.backend.state['actor'])
            result,_=self.request(target=target)
            self.assertEqual('rejected',result['status']); self.assertEqual(reason,result['reason'])
            self.assertEqual(before,self.backend.state['actor']); self.assertEqual([],self.backend.world_items)

    def test_depleted_action_resource_rejects_without_item_or_hp_mutation(self):
        self.backend.state['actor']['resources']['ActionPoint']['0']['amount']=0
        before=copy.deepcopy(self.backend.state['actor'])
        result,_=self.request()
        self.assertEqual('insufficient_resources',result['reason'])
        self.assertEqual(before,self.backend.state['actor']); self.assertEqual(20,self.backend._units()[ENEMY]['hp'])

    def test_hidden_target_and_unknown_inventory_option_never_submit(self):
        seen,value=self.observed_action()
        for changed in (dict(value,target={'entity_uuid':HIDDEN}),dict(value,inventory_option_id='unknown_instance')):
            with self.assertRaises(Rejected): self.service.preview(seen['observation_id'],changed)
        self.assertEqual([],self.backend.submissions)

    def test_transfer_unknown_owner_or_ineligible_item_is_withheld(self):
        for field,new in (('owner_uuid',HOST),('ownership_verified',None),('throw_eligible',False)):
            self.item(THROW_WEAPON)[field]=new
            self.assertNotIn(THROW_WEAPON,[i['item_uuid'] for i in self.service.observe()['actor']['inventory_options']])

    def test_quantity_revision_source_and_session_invalidation_are_exact(self):
        seen,value=self.observed_action(THROW_CONSUMABLE)
        self.item(THROW_CONSUMABLE)['quantity']=1
        with self.assertRaises(Rejected) as rejected: self.service.preview(seen['observation_id'],value)
        self.assertEqual('state_changed',rejected.exception.code)
        fresh=self.service.observe()
        with self.assertRaises(Rejected): self.service.preview(fresh['observation_id'],value)
        seen,value=self.observed_action(THROW_WEAPON)
        self.backend.state['session_id']='new-world'
        fresh=self.service.observe()
        with self.assertRaises(Rejected) as rejected: self.service.preview(fresh['observation_id'],value)
        self.assertEqual('stale_inventory_option',rejected.exception.code)

    def test_private_source_or_transfer_race_is_rechecked_before_any_mutation(self):
        original=self.backend._throw
        def transfer(actor,value,events):
            self.item(THROW_WEAPON)['owner_uuid']=HOST
            return original(actor,value,events)
        self.backend._throw=transfer
        result,_=self.request()
        self.assertEqual('rejected',result['status']); self.assertEqual('inventory_choice_changed',result['reason'])
        self.assertEqual(1,self.backend.state['actor']['resources']['ActionPoint']['0']['amount'])
        self.assertEqual(1,self.item(THROW_WEAPON)['quantity']); self.assertEqual([],self.backend.world_items)

    def test_forged_source_catalog_and_weapon_stack_split_are_rejected(self):
        self.item(THROW_WEAPON)['_source_record']='different_item_source'
        result,_=self.request()
        self.assertEqual('inventory_choice_changed',result['reason'])
        self.service.close(); self.setUp(); self.item(THROW_WEAPON)['quantity']=2
        result,_=self.request()
        self.assertEqual('unsupported_item_stack',result['reason'])
        self.assertEqual(2,self.item(THROW_WEAPON)['quantity'])

    def test_inventory_bounds_duplicate_instance_and_unknown_quantity_fail_closed(self):
        raw=self.backend.observe(ACTOR)
        raw['actor']['inventory_options'].append(copy.deepcopy(raw['actor']['inventory_options'][0]))
        with self.assertRaises(Rejected): observation(raw,ACTOR)
        for qty in (None,True,-1,10001):
            raw=self.backend.observe(ACTOR); raw['actor']['inventory_options'][0]['quantity']=qty
            with self.assertRaises(Rejected): observation(raw,ACTOR)
        self.backend.state['actor']['inventory_items']*=30
        with self.assertRaises(Rejected): self.service.observe()

    def test_throw_capability_gate_and_native_stay_closed(self):
        original=self.backend.capabilities
        self.backend.capabilities=lambda:dict(original(),action_features=dict(original()['action_features'],throw_inventory_item=False))
        seen,value=self.observed_action()
        with self.assertRaises(Rejected): self.service.preview(seen['observation_id'],value)
        native=CompanionService(NativeBackend()); self.addCleanup(native.close)
        self.assertFalse(native.capabilities()['actions']['throw']); self.assertFalse(native.capabilities()['action_features']['throw_inventory_item'])

    def test_durable_duplicate_after_restart_returns_saved_receipt_without_second_throw(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=Path(directory)/'throw.sqlite'
            self.service.close(); self.service=CompanionService(self.backend,journal)
            self.service.bind(ACTOR); self.service.control(True)
            result,value=self.request()
            self.service.close(); self.service=CompanionService(self.backend,journal)
            self.service.bind(ACTOR)
            duplicate=self.service.request('not_replayed',value,'throw_one')
            self.assertEqual(result,duplicate); self.assertEqual(1,len(self.backend.submissions))
            self.assertEqual(1,len(self.backend.world_items)); self.assertFalse(self.service.status()['armed'])
            self.service.close()
        self.service=CompanionService(self.backend)

    def test_pending_item_transfer_is_rejected_on_execution_without_resubmit(self):
        self.backend.deferred=True
        result,_=self.request()
        self.assertEqual('pending',result['status'])
        self.item(THROW_WEAPON)['owner_uuid']=HOST
        settled=self.service.result(result['request_id'])
        self.assertEqual('rejected',settled['status'])
        self.assertEqual('unknown_inventory_option',settled['reason'])
        self.assertEqual(1,len(self.backend.submissions)); self.assertEqual([],self.backend.world_items)
        self.assertEqual(1,self.backend.state['actor']['resources']['ActionPoint']['0']['amount'])


if __name__=='__main__': unittest.main()
