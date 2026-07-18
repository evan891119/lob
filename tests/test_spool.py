import tempfile
import unittest
from pathlib import Path

from lob_recorder.spool import DurableSpool


class SpoolTests(unittest.TestCase):
    def test_ordered_replay_and_clear(self):
        with tempfile.TemporaryDirectory() as folder:
            spool = DurableSpool(folder)
            spool.append([{"sequence_no": 1}, {"sequence_no": 2}])
            written = []
            count = spool.replay(lambda batch: written.extend(batch), 1)
            self.assertEqual(count, 2)
            self.assertEqual([row["sequence_no"] for row in written], [1, 2])
            self.assertFalse((Path(folder) / "pending.jsonl").exists())
