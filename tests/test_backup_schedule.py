"""Offline time simulations; never loads sessions or calls Telegram."""
import copy
import tempfile
import unittest
from pathlib import Path
from tgcf.backup_schedule import FairScheduler, DEFAULTS, category, settings, scheduled_names
from tgcf.state_store import atomic_json


def routes(normal=2, dump=100):
    return [{'connection_id': 'normal-'+str(i), 'con_name': 'Topic '+str(i)} for i in range(normal)] + [{'connection_id':'dump-'+str(i), 'con_name':'Dump · Files '+str(i)} for i in range(dump)]


class ScheduleTests(unittest.TestCase):
    def test_default_share_independent_of_dump_count(self):
        for count in (1, 20, 200):
            scheduler=FairScheduler(); totals={'normal':0,'dump':0}
            for _ in range(200):
                route, kind, limit, competing=scheduler.choose(routes(dump=count),DEFAULTS)
                totals[kind]+=limit; scheduler.record(route['connection_id'],kind,limit,competing)
            self.assertAlmostEqual(totals['normal']/sum(totals.values()), .7, places=2)

    def test_actual_short_turns_get_more_normal_turns(self):
        scheduler=FairScheduler(); totals={'normal':0,'dump':0}
        for _ in range(5000):
            route,kind,limit,competing=scheduler.choose(routes(),DEFAULTS)
            elapsed=60 if kind=='normal' else limit
            totals[kind]+=elapsed; scheduler.record(route['connection_id'],kind,elapsed,competing)
        self.assertGreater(totals['normal']/sum(totals.values()), .69)

    def test_round_robin_within_both_queues(self):
        scheduler=FairScheduler(); seen={'normal':[],'dump':[]}
        for _ in range(12):
            route,kind,limit,competing=scheduler.choose(routes(3,3),DEFAULTS)
            seen[kind].append(route['connection_id']);scheduler.record(route['connection_id'],kind,limit,competing)
        for kind in seen:self.assertEqual(len(set(seen[kind][:3])),3)

    def test_spare_capacity_creates_no_future_debt(self):
        scheduler=FairScheduler()
        for _ in range(20):
            route,kind,limit,competing=scheduler.choose(routes(0,2),DEFAULTS)
            scheduler.record(route['connection_id'],kind,limit,competing)
        self.assertEqual(scheduler.choose(routes(),DEFAULTS)[1],'normal')
        self.assertEqual(scheduler.state['used'],{'normal':0,'dump':0})

    def test_interrupted_turn_checkpoint_is_charged_on_restart(self):
        state={'used':{'normal':0,'dump':0},'last':{},'pending':{'key':'normal-0','kind':'normal','elapsed':4200,'competing':True}}
        scheduler=FairScheduler(state)
        self.assertEqual(scheduler.choose(routes(),DEFAULTS)[1],'dump')
        self.assertNotIn('pending',scheduler.state)

    def test_custom_half_share_is_respected(self):
        scheduler=FairScheduler(); options=dict(DEFAULTS,normal_percent=50,normal_minutes=30);totals={'normal':0,'dump':0}
        for _ in range(100):
            route,kind,limit,competing=scheduler.choose(routes(),options)
            totals[kind]+=limit;scheduler.record(route['connection_id'],kind,limit,competing)
        self.assertEqual(totals['normal'],totals['dump'])

    def test_saved_state_restores_next_queue_and_cursor(self):
        scheduler=FairScheduler();route,kind,limit,competing=scheduler.choose(routes(),DEFAULTS);scheduler.record(route['connection_id'],kind,limit,competing)
        restored=FairScheduler(copy.deepcopy(scheduler.state))
        self.assertEqual(restored.choose(routes(),DEFAULTS),scheduler.choose(routes(),DEFAULTS))
        self.assertEqual(restored.choose(routes(),DEFAULTS)[1],'dump')

    def test_existing_topic_with_dump_receiver_stays_normal(self):
        self.assertEqual(category({'connection_id':'topic','con_name':'Topic','dest':[-1009]},DEFAULTS),'normal')
        options=dict(DEFAULTS,priorities={'topic':'dump'})
        self.assertEqual(category({'connection_id':'topic','con_name':'Topic'},options),'dump')

    def test_settings_cannot_reduce_normal_below_half(self):
        with tempfile.TemporaryDirectory() as root:
            atomic_json(Path(root)/'.tgcf_schedule.json',{'normal_percent':1,'dump_minutes':999})
            self.assertEqual(settings(root)['normal_percent'],50)
            self.assertEqual(settings(root)['dump_minutes'],300)

    def test_once_visits_each_route_once_with_limits(self):
        with tempfile.TemporaryDirectory() as root:
            iterator=scheduled_names(root,'user1',lambda: routes(2,2),once=True); turns=[]
            turn=next(iterator)
            while True:
                turns.append(turn)
                try:turn=iterator.send((turn[1],True))
                except StopIteration:break
            self.assertEqual(len(turns),4);self.assertEqual(len(set(t[0] for t in turns)),4)
            self.assertTrue(all(t[1]==(4200 if t[2]=='normal' else 1800) for t in turns))

    def test_dynamic_removal_and_ownership_refresh(self):
        with tempfile.TemporaryDirectory() as root:
            current=routes(1,1); iterator=scheduled_names(root,'user1',lambda:current)
            self.assertEqual(next(iterator)[2],'normal');current[:]=routes(0,1)
            self.assertEqual(iterator.send((4200,True))[2],'dump')
            current.clear()
            with self.assertRaises(StopIteration):iterator.send((1800,True))

    def test_completed_routes_are_not_spun_repeatedly(self):
        with tempfile.TemporaryDirectory() as root:
            iterator=scheduled_names(root,'user1',lambda:routes(1,0));next(iterator)
            with self.assertRaises(StopIteration):iterator.send((1,False))

    def test_cooldown_does_not_reset_queue_cursor(self):
        all_routes = routes(5, 0)
        scheduler = FairScheduler({'last': {'normal': 'normal-1'}})
        eligible = [all_routes[0], *all_routes[2:]]
        self.assertEqual(scheduler.choose(eligible, DEFAULTS, all_routes)[0]['connection_id'], 'normal-2')

    def test_fast_completions_visit_later_routes(self):
        with tempfile.TemporaryDirectory() as root:
            iterator = scheduled_names(root, 'user1', lambda: routes(5, 0))
            seen = [next(iterator)[0]]
            for _ in range(4):
                seen.append(iterator.send((1, False))[0])
            self.assertEqual(seen, ['normal-' + str(i) for i in range(5)])
            iterator.close()
