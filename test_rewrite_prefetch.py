import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import stream_corpus


class PrefetchIntegrity(unittest.TestCase):
    def setUp(self):
        self.payload = b"private synthetic source"
        self.row = dict(path="part.step", bytes=len(self.payload),
                        md5=hashlib.md5(self.payload).hexdigest(),
                        source_sha=hashlib.sha256(self.payload).hexdigest(), ext=".step")
        self.args = SimpleNamespace(cache=None, offline=True, prefetch_small=2, prefetch_kib=64)

    def test_transfer_and_worker_both_verify_content(self):
        with patch.object(stream_corpus, 'fetch', return_value=(self.payload, self.row['source_sha'])):
            row, = stream_corpus.prefetch_small_sources(iter([self.row.copy()]), self.args)
        self.assertEqual(stream_corpus.input_bytes(row, self.args)[0], self.payload)
        for field, value in (('bytes', 4), ('md5', '0'*32), ('source_sha', '0'*64)):
            changed = {**row, field:value}
            self.assertEqual(stream_corpus.transformed(changed, self.args)[0], 'ready')
            self.assertEqual(stream_corpus.source_identities(changed, self.args)[0], 'discover')

    def test_transient_and_cached_sources(self):
        with patch.object(stream_corpus, 'fetch', side_effect=stream_corpus.SourceError('unavailable')):
            row, = stream_corpus.prefetch_small_sources(iter([self.row.copy()]), self.args)
        self.assertEqual(stream_corpus.transformed(row, self.args), ('ready', 'unavailable', None, None))
        with TemporaryDirectory() as directory:
            self.args.cache = Path(directory).resolve()
            (self.args.cache/'part.step').write_bytes(self.payload)
            row, = stream_corpus.prefetch_small_sources(iter([self.row.copy()]), self.args)
            self.assertNotIn('prefetched_input', row)
            self.assertEqual(stream_corpus.input_bytes(row, self.args)[0], self.payload)


if __name__ == '__main__':
    unittest.main()
