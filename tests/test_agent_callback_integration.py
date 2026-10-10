import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend import TabbyBackend
from tabby.working import WorkingStore
from tabby.agent_links import AgentLinks
from tabby.notifications import NotificationStore


class BackendCallbackIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.backend = TabbyBackend.__new__(TabbyBackend)
        self.backend.working = WorkingStore(root / 'working.json')
        self.backend.agent_links = AgentLinks(root / 'links.sqlite3')
        self.backend.state = Mock()
        self.notifications = NotificationStore(root / 'notifications.sqlite3')
        self.patch = patch('backend.NotificationStore', return_value=self.notifications)
        self.patch.start()
        self.task, _ = self.backend.working.create_web_worker(
            'create-sumi','Write SUMI Handbook','Implement task',
            origin_ref='https://chatgpt.com/c/origin-001',origin_agent_name='Origin agent')

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_review_generates_one_native_alert_and_origin_event_on_restart(self):
        b = self.backend
        b._publish_working()
        task = b.working.update(self.task['id'],phase='awaiting-review',status='waiting',
                               url='https://chatgpt.com/c/worker-001',response='Ready to review')
        b._publish_working()
        b._publish_working()
        agent_id = b.agent_links.get(self.task['id'])['agent_id']
        events = AgentLinks(b.agent_links.path).inbox(agent_id)
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['message'],'Ready to review')
        self.assertEqual(len(self.notifications.list()),1)
        self.assertEqual(self.notifications.list()[0]['kind'],'status')
        self.assertTrue(b.agent_links.acknowledge(agent_id,events[0]['event_id']))
        self.assertEqual(AgentLinks(b.agent_links.path).inbox(agent_id),[])


if __name__ == '__main__': unittest.main()
