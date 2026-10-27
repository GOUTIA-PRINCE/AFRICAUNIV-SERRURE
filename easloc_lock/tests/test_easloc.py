from datetime import datetime

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestEasloc(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.encoder = cls.env["easloc.encoder"].create({
            "name": "Test", "dls_co_id": 4660, "tz": "Africa/Lagos",
        })
        cls.room = cls.env["easloc.room"].create({
            "name": "T301", "building": 99, "floor": 3, "room_index": 1,
        })

    def test_lock_no(self):
        self.assertEqual(self.room.lock_no, "990301")

    @mute_logger("odoo.sql_db")
    def test_lock_no_unique(self):
        with self.assertRaises(Exception), self.cr.savepoint():
            self.env["easloc.room"].create({"name": "T301b", "building": 99, "floor": 3, "room_index": 1})

    def test_lock_part_range(self):
        with self.assertRaises(ValidationError):
            self.env["easloc.room"].create({"name": "x", "floor": 100, "room_index": 1})

    def test_guest_payload_local_time(self):
        card = self.env["easloc.card"].create({
            "room_id": self.room.id,
            "guest_name": "Test",
            "encoder_id": self.encoder.id,
            "begin_date": datetime(2026, 10, 27, 13, 5),   # UTC
            "end_date": datetime(2026, 10, 28, 11, 0),     # UTC
        })
        payload = card._guest_payload()
        # Africa/Lagos = UTC+1
        self.assertEqual(payload["bdate"], "2610271405")
        self.assertEqual(payload["edate"], "2610281200")
        self.assertEqual(payload["lock_no"], "990301")
        self.assertEqual(payload["dls_co_id"], 4660)
        self.assertTrue(card.name.startswith("CARTE/"))

    def test_job_result_updates_card_and_room(self):
        card = self.env["easloc.card"].create({
            "room_id": self.room.id, "guest_name": "Test", "encoder_id": self.encoder.id,
        })
        job = self.env["easloc.job"].create({
            "encoder_id": self.encoder.id, "job_type": "guest", "card_id": card.id,
            "state": "processing",
        })
        job._apply_result(True, {"card_data": "ABC"}, None)
        self.assertEqual(card.state, "active")
        self.assertEqual(card.card_data, "ABC")
        self.assertEqual(self.room.state, "occupied")
        erase = self.env["easloc.job"].create({
            "encoder_id": self.encoder.id, "job_type": "erase", "card_id": card.id,
            "state": "processing",
        })
        erase._apply_result(True, {}, None)
        self.assertEqual(card.state, "erased")
        self.assertEqual(self.room.state, "dirty")

    def test_batch(self):
        wizard = self.env["easloc.room.batch"].create({"building": 98, "floors": "1-2", "rooms": "101-203"})
        wizard.action_create()
        rooms = self.env["easloc.room"].search([("building", "=", 98)])
        self.assertEqual(len(rooms), 6)
        self.assertIn("980203", rooms.mapped("lock_no"))
        self.assertEqual(rooms.filtered(lambda r: r.lock_no == "980203").name, "203")
