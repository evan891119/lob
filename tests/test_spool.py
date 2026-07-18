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

    def test_partial_replay_restores_only_uncommitted_tail(self):
        with tempfile.TemporaryDirectory() as folder:
            spool = DurableSpool(folder)
            spool.append([{"sequence_no": 1}, {"sequence_no": 2}, {"sequence_no": 3}])
            calls = 0

            def writer(batch):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise ConnectionError("offline")

            with self.assertRaises(ConnectionError):
                spool.replay(writer, 1)
            self.assertEqual([row["sequence_no"] for row in spool.records()], [2, 3])

    def test_interrupted_replay_is_recovered_before_new_append(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "pending.jsonl.replaying").write_text('{"sequence_no":1}\n')
            (root / "pending.jsonl").write_text('{"sequence_no":2}\n')
            spool = DurableSpool(root)
            self.assertEqual([row["sequence_no"] for row in spool.records()], [1, 2])
