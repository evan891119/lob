import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lob_recorder.config import load_instruments


class ConfigTests(unittest.TestCase):
    def load(self, document):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "instruments.yaml"
            path.write_text("fixture")
            fake_yaml = SimpleNamespace(safe_load=lambda _text: document)
            with patch.dict("sys.modules", {"yaml": fake_yaml}):
                return load_instruments(path)

    def test_loads_multiple_public_instruments(self):
        instruments = self.load({"instruments": [
            {"code": "2330", "security_type": "STK", "exchange": "TSE", "streams": ["bidask", "tick"], "enabled": True},
            {"code": "TXFR1", "security_type": "FUT", "exchange": "TAIFEX", "streams": ["tick"], "enabled": True},
        ]})
        self.assertEqual([item.code for item in instruments], ["2330", "TXFR1"])

    def test_rejects_duplicate_stream_and_non_stock_odd_lot(self):
        with self.assertRaises(ValueError):
            self.load({"instruments": [{"code": "2330", "security_type": "STK", "exchange": "TSE", "streams": ["tick", "tick"]}]})
        with self.assertRaises(ValueError):
            self.load({"instruments": [{"code": "TXFR1", "security_type": "FUT", "exchange": "TAIFEX", "streams": ["tick"], "intraday_odd": True}]})
