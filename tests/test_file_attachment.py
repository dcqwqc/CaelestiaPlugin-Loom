from pathlib import Path
import tempfile
import threading
import time
import unittest
from backend import TabbyBackend


class FakeState:
    def __init__(self):
        self.current = {'summoned': True}
        self.history = []
        self.event = threading.Event()

    def snapshot(self):
        return self.current.copy()

    def update(self, **kwargs):
        self.current.update(kwargs)
        self.history.append(kwargs)
        if kwargs.get('attachmentName'):
            self.event.set()


class FakeVoice:
    def __init__(self):
        self.calls = []
        self.event = threading.Event()

    def paste_image(self, data, mime, name):
        self.calls.append((data, mime, name))
        self.event.set()
        return {'ok': True, 'result': 'image-attached'}


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.backend = TabbyBackend.__new__(TabbyBackend)
        self.backend.state = FakeState()
        self.backend.voice = FakeVoice()
        self.backend._generation = 1
        self.backend._cancel_hide = lambda: None
        self.backend._valid = lambda g: g == 1

    def test_file_url_attaches_pdf_without_sending_message(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'example.pdf'
            p.write_bytes(b'%PDF-1.4\n%%EOF\n')
            result = self.backend.attach_file(p.as_uri())
            self.assertTrue(result['ok'])
            self.assertTrue(self.backend.state.event.wait(3))
            self.assertEqual(self.backend.voice.calls, [(p.read_bytes(), 'application/pdf', 'example.pdf')])
            self.assertTrue(self.backend.state.current['attachmentPending'])
            self.assertEqual(self.backend.state.current['attachmentName'], 'example.pdf')

    def test_invalid_files_never_touch_browser(self):
        for path in ('', 'file://remote-server/file.txt', '/not-here/invalid-attachment.txt'):
            result = self.backend.attach_file(path)
            self.assertFalse(result['ok'])
        self.assertEqual(self.backend.voice.calls, [])

    def test_rejects_oversize_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'oversize.txt'
            with p.open('wb') as fp:
                fp.truncate(16 * 1024 * 1024 + 1)
            self.assertFalse(self.backend.attach_file(str(p))['ok'])
            self.assertEqual(self.backend.voice.calls, [])


if __name__ == '__main__':
    unittest.main()
