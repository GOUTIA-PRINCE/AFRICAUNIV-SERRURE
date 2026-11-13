from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class EaslocRoom(models.Model):
    _name = "easloc.room"
    _description = "Chambre (serrure EASLOC)"
    _inherit = ["mail.thread"]
    _order = "building, floor, room_index, name"

    name = fields.Char("Chambre", required=True, tracking=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", "Société", required=True, default=lambda self: self.env.company
    )
    building = fields.Integer("Bâtiment", required=True, default=1)
    floor = fields.Integer("Étage", required=True, default=1)
    room_index = fields.Integer(
        "N° interne serrure",
        required=True,
        help="Numéro de la chambre dans l'étage, tel que défini dans le logiciel EASLOC "
        "(colonne RomID). En général les deux derniers chiffres : 101 → 1, 118 → 18.",
    )
    lock_no = fields.Char(
        "N° de serrure",
        compute="_compute_lock_no",
        inverse="_inverse_lock_no",
        store=True,
        index=True,
        help="Numéro exact affiché dans la colonne Lock No. d'EASLOC. Saisissez-le comme "
        "texte pour conserver les zéros initiaux (ex. 01010199). Si vous ne le modifiez pas, "
        "Odoo propose le format bâtiment + étage + n° interne (6 chiffres).",
    )
    lock_no_override = fields.Char("Surcharge du n° de serrure", copy=False)
    room_type = fields.Char("Type")
    state = fields.Selection(
        [
            ("vacant", "Libre"),
            ("occupied", "Occupée"),
            ("dirty", "À nettoyer"),
            ("blocked", "Hors service"),
        ],
        "État",
        default="vacant",
        required=True,
        tracking=True,
    )
    card_ids = fields.One2many("easloc.card", "room_id", "Cartes")
    active_card_count = fields.Integer("Cartes actives", compute="_compute_active_card_count")
    current_guest = fields.Char("Client actuel", compute="_compute_active_card_count")
    note = fields.Text("Remarques")

    _lock_no_uniq = models.Constraint(
        "UNIQUE(company_id, lock_no)",
        "Deux chambres ne peuvent pas avoir le même n° de serrure.",
    )

    @api.depends("building", "floor", "room_index", "lock_no_override")
    def _compute_lock_no(self):
        for room in self:
            room.lock_no = room.lock_no_override or "%02d%02d%02d" % (
                room.building, room.floor, room.room_index
            )

    def _inverse_lock_no(self):
        for room in self:
            generated = "%02d%02d%02d" % (room.building, room.floor, room.room_index)
            room.lock_no_override = room.lock_no if room.lock_no != generated else False

    @api.depends("card_ids.state", "card_ids.guest_name")
    def _compute_active_card_count(self):
        for room in self:
            cards = room.card_ids.filtered(lambda c: c.state == "active")
            room.active_card_count = len(cards)
            room.current_guest = cards[:1].guest_name or False

    @api.constrains("building", "floor", "room_index")
    def _check_lock_parts(self):
        for room in self:
            for value, label in (
                (room.building, _("bâtiment")),
                (room.floor, _("étage")),
                (room.room_index, _("n° interne")),
            ):
                if not 0 <= value <= 99:
                    raise ValidationError(
                        _("Chambre %(room)s : le %(what)s doit être entre 0 et 99.", room=room.name, what=label)
                    )

    @api.constrains("building", "floor", "room_index", "lock_no", "lock_no_override")
    def _check_lock_no(self):
        for room in self:
            if not room.lock_no or not room.lock_no.isdigit() or len(room.lock_no) not in (6, 8):
                raise ValidationError(
                    _("Le n° de serrure doit contenir 6 chiffres (format proposé par Odoo) "
                      "ou 8 chiffres (format EASLOC), sans espaces ni séparateurs.")
                )

    def action_new_card(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "easloc.card",
            "view_mode": "form",
            "target": "current",
            "context": {"default_room_id": self.id},
        }

    def action_view_cards(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Cartes – %s", self.name),
            "res_model": "easloc.card",
            "view_mode": "list,form",
            "domain": [("room_id", "=", self.id)],
            "context": {"default_room_id": self.id},
        }

    def action_set_vacant(self):
        self.write({"state": "vacant"})
        return True

    def action_set_blocked(self):
        self.write({"state": "blocked"})
        return True

