import unittest
from unittest.mock import patch
from tabby import philipedia_project_router as router


class PhilipediaRoutingTests(unittest.TestCase):
    def test_status_works_without_chatgpt_login(self):
        with patch.object(router, '_call', return_value={'ok': True, 'loggedIn': False}):
            output = router.invoke('loom_router_status', {})
        self.assertFalse(output['isError'])
        self.assertFalse(output['structuredContent']['loggedIn'])

    def test_chat_route_fails_fast_when_not_authenticated(self):
        with patch.object(router, '_call', return_value={'ok': True, 'loggedIn': False}) as request:
            output = router.invoke('loom_chat_route', {'lifecycle': 'working', 'url': 'https://chatgpt.com/c/example'})
        self.assertTrue(output['isError'])
        self.assertIn('not signed in', output['structuredContent']['error'])
        request.assert_called_once_with('/v1/status')

    def test_authenticated_route_preserves_exact_identity_and_lifecycle(self):
        with patch.object(router, '_call', side_effect=[{'ok': True, 'loggedIn': True}, {'ok': True, 'result': 'project-move-verified'}]) as request:
            output = router.invoke('loom_chat_route', {'url': 'https://chatgpt.com/c/example', 'lifecycle': 'working'})
        self.assertFalse(output['isError'])
        self.assertEqual(request.call_args_list[1].args[0], '/v1/route')
        self.assertEqual(request.call_args_list[1].args[1], {'url': 'https://chatgpt.com/c/example', 'lifecycle': 'working'})

    def test_unregistered_origin_never_guessed(self):
        with patch.object(router, '_call', return_value={'ok': True, 'loggedIn': True}) as request:
            output = router.invoke('loom_chat_route', {'lifecycle': 'working', 'origin_ref': 'unknown'})
        self.assertTrue(output['isError'])
        self.assertIn('canonical ChatGPT URL', output['structuredContent']['error'])
        request.assert_called_once_with('/v1/status')


if __name__ == '__main__':
    unittest.main()
