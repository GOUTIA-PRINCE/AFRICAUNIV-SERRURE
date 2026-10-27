import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError


def _parse_range(value, label):
    match = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", value or "")
    if not match:
        raise UserError(_("%s : saisissez un nombre ou une plage, ex. 1-5.", label))
    start = int(match.group(1))
    end = int(match.group(2) or start)
    if end < start:
        raise UserError(_("%s : la fin de la plage est avant le début.", label))
    return start, end


class EaslocRoomBatch(models.TransientModel):
    _name = "easloc.room.batch"
    _description = "Création de chambres en lot"

    building = fields.Integer("Bâtiment", default=1, required=True)
    floors = fields.Char("Étages", required=True, default="1-5", help="Ex. : 1-5")
    rooms = fields.Char(
        "Chambres", required=True, default="101-518",
        help="Premier n° du premier étage et dernier n° du dernier étage, comme dans "
        "le logiciel EASLOC. Ex. : 101-518 = 18 chambres par étage, de 1 à 5.",
    )
    room_type = fields.Char("Type", default="Standard")
    preview = fields.Text("Aperçu", compute="_compute_preview")

    def _iter_rooms(self):
        self.ensure_one()
        floor_start, floor_end = _parse_range(self.floors, _("Étages"))
        room_start, room_end = _parse_range(self.rooms, _("Chambres"))
        first_idx = room_start % 100
        last_idx = room_end % 100
        if not first_idx or last_idx < first_idx:
            raise UserError(_("Chambres : utilisez une plage du type 101-518."))
        for floor in range(floor_start, floor_end + 1):
            for idx in range(first_idx, last_idx + 1):
                yield floor, idx, "%d%02d" % (floor, idx)

    @api.depends("building", "floors", "rooms")
    def _compute_preview(self):
        for wizard in self:
            try:
                rooms = list(wizard._iter_rooms())
            except UserError as e:
                wizard.preview = str(e)
                continue
            sample = ", ".join(
                "%s → %02d%02d%02d" % (name, wizard.building, floor, idx)
                for floor, idx, name in rooms[:3]
            )
            wizard.preview = _("%(count)s chambres. Ex. : %(sample)s…", count=len(rooms), sample=sample)

    def action_create(self):
        self.ensure_one()
        Room = self.env["easloc.room"]
        company = self.env.company
        existing = set(Room.with_context(active_test=False).search(
            [("company_id", "=", company.id)]).mapped("lock_no"))
        vals_list = []
        for floor, idx, name in self._iter_rooms():
            lock_no = "%02d%02d%02d" % (self.building, floor, idx)
            if lock_no in existing:
                continue
            vals_list.append({
                "name": name, "building": self.building, "floor": floor,
                "room_index": idx, "room_type": self.room_type, "company_id": company.id,
            })
        Room.create(vals_list)
        return {
            "type": "ir.actions.act_window",
            "name": _("Chambres"),
            "res_model": "easloc.room",
            "view_mode": "list,form",
        }
