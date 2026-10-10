import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from tabby.web_workers import WebWorkerManager
from tabby.working import WorkingStore
from tabby.chat_projects import chat_route


class ChatNameSynchronizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = WorkingStore(Path(self.temp.name)/'working.json')
        self.task, _ = self.store.create_web_worker('req-title', 'Write the Sumi handbook', 'Implement the task')
        self.task = self.store.update(self.task['id'], url='https://chatgpt.com/g/g-p-'+'1'*32+'/c/chat-title-1',
                                      phase='running', status='working')
        self.zen = Mock()
        self.manager = WebWorkerManager(self.store, self.zen, reconcile_delay=0)

    def tearDown(self):
        self.temp.cleanup()

    def test_success_requires_verified_conversation_and_exact_name(self):
        self.zen.worker_rename_chat.return_value = {'ok':True,'conversationId':'chat-title-1',
                                                     'name':'Write the Sumi handbook'}
        result = self.manager.sync_title(self.task['id'])
        self.assertTrue(result['ok'])
        self.assertEqual(self.zen.worker_rename_chat.call_args.args,
                         (self.task['id'],'chat-title-1','Write the Sumi handbook'))
        reloaded = WorkingStore(Path(self.temp.name)/'working.json').get(self.task['id'])
        self.assertEqual(reloaded['titleSyncState'],'synced')
        self.assertEqual(reloaded['chatTitle'],reloaded['title'])
        self.manager.sync_title(self.task['id'])
        self.assertEqual(self.zen.worker_rename_chat.call_count, 1)

    def test_unknown_rename_is_not_marked_synced(self):
        self.zen.worker_rename_chat.return_value = {'ok':False,'result':'chat-name-unverified'}
        self.assertFalse(self.manager.sync_title(self.task['id'])['ok'])
        self.assertEqual(self.store.get(self.task['id'])['titleSyncState'],'pending')

    def test_wrong_chat_identity_does_not_mark_success(self):
        self.zen.worker_rename_chat.return_value = {'ok':True,'conversationId':'another-chat',
                                                     'name':self.task['title']}
        self.assertFalse(self.manager.sync_title(self.task['id'])['ok'])
        self.assertNotEqual(self.store.get(self.task['id'])['titleSyncState'],'synced')

    def test_new_task_title_invalidates_previous_verified_name(self):
        self.store.update(self.task['id'],titleSyncState='synced',chatTitle=self.task['title'])
        self.store.update(self.task['id'],title='Different name')
        self.assertEqual(self.store.get(self.task['id'])['titleSyncState'],'pending')


if __name__ == '__main__':
    unittest.main()
