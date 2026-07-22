import unittest
from threading import Lock
from types import SimpleNamespace
from uuid import UUID, uuid4

from lob_recorder.sinks import ClickHouseSink, PartialWriteError


class RecordingClient:
    def __init__(self, commit_before_failure: bool, failure_table: str = "tick_events"):
        self.commit_before_failure = commit_before_failure
        self.failure_table = failure_table
        self.fail_once = True
        self.identities = {"lob_events": set(), "tick_events": set()}

    def insert(self, table, rows, column_names):
        session_index = column_names.index("session_id")
        sequence_index = column_names.index("sequence_no")
        identities = {
            (str(row[session_index]), int(row[sequence_index]))
            for row in rows
        }
        if table == self.failure_table and self.fail_once:
            self.fail_once = False
            if self.commit_before_failure:
                self.identities[table].update(identities)
            raise ConnectionError("ambiguous insert result")
        self.identities[table].update(identities)

    def query(self, query, parameters):
        table = "lob_events" if "FROM lob_events" in query else "tick_events"
        session_id = str(UUID(str(parameters["session_id"])))
        minimum = int(parameters["minimum"])
        maximum = int(parameters["maximum"])
        rows = [
            (UUID(identity_session), sequence)
            for identity_session, sequence in self.identities[table]
            if identity_session == session_id and minimum <= sequence <= maximum
        ]
        return SimpleNamespace(result_rows=rows)


def sink_with(client):
    sink = ClickHouseSink.__new__(ClickHouseSink)
    sink.client = client
    sink.lock = Lock()
    return sink


class ClickHouseSinkTests(unittest.TestCase):
    def records(self):
        session_id = str(uuid4())
        return [
            {
                "stream": "bidask", "trading_date": "2026-01-02",
                "session_id": session_id, "sequence_no": 1,
            },
            {
                "stream": "tick", "trading_date": "2026-01-02",
                "session_id": session_id, "sequence_no": 2,
            },
        ]

    def test_partial_write_spools_only_unconfirmed_stream(self):
        client = RecordingClient(commit_before_failure=False)
        sink = sink_with(client)
        records = self.records()

        with self.assertRaises(PartialWriteError) as raised:
            sink.write(records)

        self.assertEqual(raised.exception.written_count, 1)
        self.assertEqual(raised.exception.pending_records, [records[1]])
        sink.replay(raised.exception.pending_records)
        self.assertEqual(len(client.identities["lob_events"]), 1)
        self.assertEqual(len(client.identities["tick_events"]), 1)

    def test_replay_skips_insert_that_committed_before_connection_error(self):
        client = RecordingClient(commit_before_failure=True)
        sink = sink_with(client)
        records = self.records()

        with self.assertRaises(PartialWriteError) as raised:
            sink.write(records)

        sink.replay(raised.exception.pending_records)
        sink.replay(raised.exception.pending_records)
        self.assertEqual(len(client.identities["lob_events"]), 1)
        self.assertEqual(len(client.identities["tick_events"]), 1)

    def test_first_table_uncertain_commit_replays_only_missing_second_table(self):
        client = RecordingClient(
            commit_before_failure=True,
            failure_table="lob_events",
        )
        sink = sink_with(client)
        records = self.records()

        with self.assertRaises(PartialWriteError) as raised:
            sink.write(records)

        self.assertEqual(raised.exception.written_count, 0)
        self.assertEqual(raised.exception.pending_records, records)
        sink.replay(raised.exception.pending_records)
        self.assertEqual(len(client.identities["lob_events"]), 1)
        self.assertEqual(len(client.identities["tick_events"]), 1)


if __name__ == "__main__":
    unittest.main()
