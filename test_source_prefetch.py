"""Only eligible transfers run in the prefetch pool, and checksum failures remain retryable."""
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from check import document
import stream_corpus as stream


class Prefetch(unittest.TestCase):
    def test_order_size_and_integrity(self):
        data = document("test.stp", "linked.step")
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp).resolve()
            (cache / "cached").write_bytes(data)
            args = SimpleNamespace(cache=cache, offline=True, prefetch_small=2)
            def row(name, size=len(data)):
                return {"path": name, "ext": ".step", "source_sha": None,
                        "md5": hashlib.md5(data).hexdigest(), "bytes": size}
            rows = [row("small"), row("cached"), row("large", 200_000), row("missing")]
            def read(r, _):
                if r["path"] == "missing":
                    raise stream.SourceError("mirror HTTP 404")
                return stream.verified(r, data)
            with patch.object(stream, "fetch", side_effect=read) as fetch:
                output = list(stream.prefetch_small_sources(rows, args))
            self.assertEqual([r["path"] for r in output], ["small", "cached", "large", "missing"])
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(stream.source_identities(output[0], args)[0], "ready")
            self.assertNotIn("prefetched_input", output[1])
            self.assertNotIn("prefetched_input", output[2])
            self.assertEqual(stream.source_identities(output[3], args)[:3], ("discover", "mirror HTTP 404", None))
            output[0]["prefetched_input"] = data[:-1] + b"x"
            self.assertEqual(stream.source_identities(output[0], args)[:2], ("discover", "organizer MD5 mismatch"))


if __name__ == "__main__":
    unittest.main()
