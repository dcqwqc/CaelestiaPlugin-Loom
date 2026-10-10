import tempfile
import unittest
from pathlib import Path

from tabby.agent_links import AgentLinks
from tabby.working import WorkingStore


class DurableAgentLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.store = WorkingStore(self.path/'work.json')
        self.links = AgentLinks(self.path/'links.sqlite3')
        self.origin = 'https://chatgpt.com/c/origin-123'
        self.task, _ = self.store.create_web_worker('req-a', 'Implement callback system', 'Prompt text',
            created_by='Loom', origin_ref=self.origin, origin_url=self.origin,
            origin_agent_name='Planning agent')

    def tearDown(self):
        self.tmp.cleanup()

    def test_link_record_survives_restart_and_uses_stable_identity(self):
        self.assertEqual(self.links.reconcile(self.task)['task_name'], 'Implement callback system')
        task = self.store.update(self.task['id'], url='https://chatgpt.com/c/worker-123',
                                 phase='running', status='working')
        r = AgentLinks(self.path/'links.sqlite3').reconcile(task)
        self.assertEqual(r['chat_id'], 'worker-123')
        self.assertEqual(r['origin_ref'], self.origin)
        self.assertEqual(r['agent_name'], 'Planning agent')
        self.assertEqual(WorkingStore(self.path/'work.json').get(self.task['id'])['originRef'], self.origin)
        other = {'id':'different','originRef':'https://chatgpt.com/g/g-p-abc/c/origin-123'}
        self.assertEqual(self.links.agent_identity(task)[0],self.links.agent_identity(other)[0])

    def test_review_callback_is_exactly_once_and_acknowledged_durably(self):
        self.links.reconcile(self.task)
        t = self.store.update(self.task['id'], url='https://chatgpt.com/c/worker-123',
                              phase='awaiting-review', status='waiting', response='Task result')
        a = self.links.reconcile(t)
        self.links.reconcile(t)
        events = AgentLinks(self.path/'links.sqlite3').inbox(a['agent_id'])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['kind'], 'awaiting-review')
        self.assertEqual(events[0]['message'], 'Task result')
        self.assertTrue(self.links.acknowledge(a['agent_id'], events[0]['event_id']))
        self.assertEqual(self.links.inbox(a['agent_id']), [])
        self.assertFalse(self.links.acknowledge('different-agent', events[0]['event_id']))

    def test_reply_is_idempotent_and_does_not_mutate_chat(self):
        self.links.reconcile(self.task)
        first = self.links.reply(self.task['id'], 'result-1', 'Ready for review')
        second = AgentLinks(self.path/'links.sqlite3').reply(self.task['id'], 'result-1','Ready for review')
        self.assertEqual(first,second)
        self.assertEqual(len(self.links.inbox(first['agent_id'])),1)
        with self.assertRaises(ValueError):
            self.links.reply(self.task['id'],'result-1','Some other response')
        self.assertEqual(self.store.get(self.task['id'])['url'], '')

    def test_conversation_identity_cannot_change_or_be_stolen(self):
        task = self.store.update(self.task['id'], url='https://chatgpt.com/c/worker-123')
        self.links.reconcile(task)
        with self.assertRaisesRegex(ValueError,'conversation identity changed'):
            self.links.reconcile({**task,'url':'https://chatgpt.com/c/other-chat'})
        with self.assertRaisesRegex(ValueError,'already belongs'):
            self.links.reconcile({**task,'id':'other-task'})
        with self.assertRaisesRegex(ValueError,'another origin'):
            self.links.reconcile({**task,'originRef':'https://chatgpt.com/c/foreign-origin'})

    def test_existing_unbound_task_can_be_bound_once(self):
        t = self.store.create('New task')
        self.links.reconcile(t)
        task = self.store.bind_origin(t['id'], origin_ref=self.origin)
        row = self.links.reconcile(task)
        self.assertTrue(row['agent_id'].startswith('agent-'))
        with self.assertRaisesRegex(ValueError,'immutable'):
            self.store.bind_origin(t['id'],origin_ref='elsewhere')

    def test_idempotency_key_cannot_rebind_origin(self):
        with self.assertRaisesRegex(ValueError,'another origin'):
            self.store.create_web_worker('req-a','other', 'Prompt text', origin_ref='different')


if __name__ == '__main__':
    unittest.main()
