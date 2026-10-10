import unittest
from unittest.mock import patch
import loom_mcp


class AgentLinkToolContracts(unittest.TestCase):
    def test_identity_and_callback_tools_are_in_mcp_registry(self):
        tools = {name: (description, schema) for name, description, schema, *_ in loom_mcp.TOOLS}
        for key in ('loom_agent_links','loom_origin_resolve','loom_task_bind_origin',
                    'loom_chat_title_sync','loom_agent_events','loom_agent_event_ack','loom_worker_reply'):
            self.assertIn(key,tools)
        schema=tools['loom_web_worker_create'][1]
        self.assertIn('origin_messages',schema['properties'])
        self.assertIn('origin_ref',schema['properties'])
        self.assertIn('origin_agent_name',schema['properties'])

    def test_new_worker_can_resolve_and_register_exact_origin_before_start(self):
        origin='https://chatgpt.com/c/abc-123'
        with patch.object(loom_mcp,'_ipc',side_effect=[
            {'ok':True,'url':origin}, {'ok':True,'task':{'id':'task-1','title':'Hello','originRef':origin}}
        ]) as ipc:
            created=loom_mcp.t_web_worker_create({'request_id':'create-1','title':'Hello','prompt':'Run test',
                'origin_messages':['Implement durable, verified origin callbacks']})
        self.assertEqual(created['task']['originRef'],origin)
        self.assertEqual(ipc.call_count,2)
        self.assertEqual(ipc.call_args_list[0].args[0]['command'],'chat-origin-resolve')
        request=ipc.call_args_list[1].args[0]
        self.assertEqual(request['origin_ref'],origin)
        self.assertEqual(request['origin_url'],origin)
        self.assertEqual(request['command'],'web-worker-create')

    def test_ambiguous_sources_abort_creation_without_reserving_worker(self):
        with patch.object(loom_mcp,'_ipc',side_effect=loom_mcp.ToolError('origin-ambiguous')) as ipc:
            with self.assertRaisesRegex(loom_mcp.ToolError,'origin-ambiguous'):
                loom_mcp.t_web_worker_create({'request_id':'a','title':'T','prompt':'P',
                    'origin_messages':['The exact chat-origin fingerprint is ambiguous']})
        self.assertEqual(ipc.call_count,1)

    def test_explicit_origin_and_auto_origin_are_exclusive(self):
        with patch.object(loom_mcp,'_ipc') as ipc:
            with self.assertRaisesRegex(loom_mcp.ToolError,'either'):
                loom_mcp.t_web_worker_create({'request_id':'a','title':'T','prompt':'P',
                    'origin_ref':'specific', 'origin_messages':['A long user message for origin lookup']})
        ipc.assert_not_called()

if __name__=='__main__': unittest.main()
