import struct
import unittest
import uuid

import inspect_native
from pdf_identity import Observations


def scalar_set(format_id, props):
    entries = [(1, struct.pack("<IHxx", 2, 1200))]
    for pid, value in props:
        string = (value + "\0").encode("utf-16le")
        content = struct.pack("<II", 31, len(string) // 2) + string
        entries.append((pid, content + b"\0" * (-len(content) % 4)))
    offset = 8 + len(entries) * 8
    table, payload = bytearray(), bytearray()
    for pid, raw in entries:
        table += struct.pack("<II", pid, offset + len(payload))
        payload += raw
    section = struct.pack("<II", offset + len(payload), len(entries)) + table + payload
    header = struct.pack("<HHI", 0xfffe, 0, 0) + bytes(16) + struct.pack("<I", 1)
    return header + uuid.UUID(format_id).bytes_le + struct.pack("<I", 48) + section


class InventorRoles(unittest.TestCase):
    def test_tracking_approval_vendor_and_engineering_data(self):
        raw = scalar_set("32853f0f-3444-11d1-9e93-0060b03c1ca6", [
            (10, r"LABREALM\smith"), (12, "Example Approver"), (20, "Mild steel"),
            (23, "https://parts.example.test/catalog"), (30, "Example Supplier"),
            (34, "default"), (41, "smith"),
        ])
        values = Observations()
        inspect_native.observe_properties(raw, values)
        values.finish()
        self.assertIn("Example Approver", values)
        self.assertIn("Example Supplier", values)
        self.assertNotIn("Mild steel", values)
        self.assertNotIn("default", values)
        self.assertTrue(any(kind == "hostname" for kind, raw, target in values.forms))
        self.assertTrue(any(kind == "confirmed" for kind, raw, target in values.forms))

    def test_control_identifier_roles_omit_save_dates_and_versions(self):
        raw = scalar_set("d861fb30-3136-11d1-9e92-0060b03c1ca6", [
            (5, "Example Checkout"), (7, "Example Checkin"), (16, r"REALM\editor"),
            (15, "Previous data version"), (17, "Unchanged save timestamp"),
        ])
        values = Observations()
        inspect_native.observe_properties(raw, values)
        self.assertIn(r"REALM\editor", values)
        self.assertIn("Example Checkout", values)
        self.assertIn("Example Checkin", values)
        self.assertNotIn("Previous data version", values)
        self.assertNotIn("Unchanged save timestamp", values)
        self.assertTrue(any(kind == "account" for kind, raw, target in values.forms))


if __name__ == "__main__":
    unittest.main()
