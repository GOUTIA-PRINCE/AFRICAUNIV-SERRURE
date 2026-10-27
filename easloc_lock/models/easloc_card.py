from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# Format de date attendu par proRFL.dll (format « YYMMDDHHMM » du logiciel d'origine)
LOCK_DATE_FORMAT = "%y%m%d%H%M"


class EaslocCard(models.Model):
    _name = "easloc.card"
    _description = "Carte client EASLOC"
    _inherit = ["mail.thread"]
    _order = "id desc"

    name = fields.Char("Référence", readonly=True, copy=False, default="/")
    company_id = fields.Many2one(
        "res.company", "Société", required=True, default=lambda self: self.env.company
    )
    room_id = fields.Many2one(
        "easloc.room", "Chambre", required=True, tracking=True,
        domain="[('company_id', '=', company_id)]",
    )
    lock_no = fields.Char(related="room_id.lock_no", string="N° de serrure")
    guest_name = fields.Char("Client", required=True, tracking=True)
    partner_id = fields.Many2one("res.partner", "Contact")
    begin_date = fields.Datetime(
        "Valide du", required=True, default=fields.Datetime.now, tracking=True,
        help="Heure d'émission de la carte. Une carte plus récente pour la même "
        "chambre annule les précédentes dès qu'elle est passée sur la serrure.",
    )
    end_date = fields.Datetime(
        "Valide jusqu'au", required=True, tracking=True,
        default=lambda self: self._default_end_date(),
    )
    card_no = fields.Integer(
        "N° de carte", default=0, copy=False,
        help="0 pour la première carte, 1, 2… pour les copies remises au même client.",
    )
    llock = fields.Boolean(
        "Ouvre le verrou intérieur",
        default=lambda self: self.env["easloc.encoder"]._get_default().default_llock,
    )
    pdoors = fields.Boolean(
        "Ouvre les portes communes",
        default=lambda self: self.env["easloc.encoder"]._get_default().default_pdoors,
    )
    encoder_id = fields.Many2one(
        "easloc.encoder", "Encodeur", required=True,
        default=lambda self: self.env["easloc.encoder"]._get_default(),
        domain="[('company_id', '=', company_id)]",
    )
    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("active", "Active"),
            ("erased", "Effacée"),
            ("cancelled", "Annulée"),
            ("error", "Erreur"),
        ],
        "État", default="draft", required=True, tracking=True, copy=False,
    )
    card_data = fields.Char("Données carte", readonly=True, copy=False)
    last_error = fields.Char("Dernière erreur", readonly=True, copy=False)
    parent_id = fields.Many2one("easloc.card", "Carte d'origine", readonly=True, copy=False)
    copy_ids = fields.One2many("easloc.card", "parent_id", "Copies")
    job_ids = fields.One2many("easloc.job", "card_id", "Opérations")
    issued_date = fields.Datetime("Encodée le", readonly=True, copy=False)
    erased_date = fields.Datetime("Effacée le", readonly=True, copy=False)

    @api.model
    def _default_end_date(self):
        """Lendemain à l'heure de départ (12:00) dans le fuseau de l'utilisateur."""
        tz = ZoneInfo(self.env.user.tz or "UTC")
        local = datetime.now(tz) + timedelta(days=1)
        local = datetime.combine(local.date(), time(12, 0), tzinfo=tz)
        return local.astimezone(timezone.utc).replace(tzinfo=None)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].next_by_code("easloc.card") or "/"
        return super().create(vals_list)

    @api.constrains("begin_date", "end_date")
    def _check_dates(self):
        for card in self:
            if card.end_date <= card.begin_date:
                raise ValidationError(_("La date de fin doit être après la date de début."))

    @api.constrains("card_no")
    def _check_card_no(self):
        for card in self:
            if not 0 <= card.card_no <= 15:
                raise ValidationError(_("Le n° de carte doit être entre 0 et 15."))

    # ------------------------------------------------------------------
    def _lock_datetime(self, value):
        tz = ZoneInfo(self.encoder_id.sudo().tz or "UTC")
        return value.replace(tzinfo=timezone.utc).astimezone(tz).strftime(LOCK_DATE_FORMAT)

    def _guest_payload(self):
        self.ensure_one()
        encoder = self.encoder_id.sudo()
        return {
            "dls_co_id": encoder.dls_co_id,
            "card_no": self.card_no,
            "dai": encoder.dai,
            "llock": int(self.llock),
            "pdoors": int(self.pdoors),
            "bdate": self._lock_datetime(self.begin_date),
            "edate": self._lock_datetime(self.end_date),
            "lock_no": self.room_id.lock_no,
            "room": self.room_id.name,
        }

    def action_encode(self):
        self.ensure_one()
        if self.state not in ("draft", "error"):
            raise UserError(_("Seule une carte en brouillon ou en erreur peut être encodée."))
        if self.room_id.state == "blocked":
            raise UserError(_("La chambre %s est hors service.", self.room_id.name))
        if self.end_date <= fields.Datetime.now():
            raise UserError(_("La date de fin est déjà passée."))
        res = self.encoder_id._run_job("guest", self._guest_payload(), card=self)
        if res["state"] == "done":
            return self.encoder_id._notify(
                _("Carte encodée"),
                _("Carte de la chambre %(room)s prête pour %(guest)s.",
                  room=self.room_id.name, guest=self.guest_name),
                "success",
            )
        return self.encoder_id._notify(
            _("Échec de l'encodage"),
            res["error"] or _("L'encodeur n'a pas répondu. Posez la carte sur l'encodeur puis réessayez."),
            "danger",
        )

    def action_make_copy(self):
        """Carte supplémentaire pour le même séjour (même début, n° de carte suivant)."""
        self.ensure_one()
        if self.state != "active":
            raise UserError(_("On ne peut copier qu'une carte active."))
        origin = self.parent_id or self
        used = (origin | origin.copy_ids).mapped("card_no")
        new_no = max(used) + 1
        if new_no > 15:
            raise UserError(_("Nombre maximum de cartes atteint pour ce séjour."))
        copy = self.copy({
            "parent_id": origin.id,
            "card_no": new_no,
            "begin_date": origin.begin_date,
            "end_date": self.end_date,
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": "easloc.card",
            "res_id": copy.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_erase(self):
        """Départ : efface la carte rendue par le client (posée sur l'encodeur)."""
        self.ensure_one()
        if self.state != "active":
            raise UserError(_("Seule une carte active peut être effacée."))
        encoder = self.encoder_id.sudo()
        payload = {
            "dls_co_id": encoder.dls_co_id,
            "expected_lock_no": self.room_id.lock_no if encoder.verify_before_erase else "",
            "room": self.room_id.name,
        }
        res = self.encoder_id._run_job("erase", payload, card=self)
        if res["state"] == "done":
            return self.encoder_id._notify(
                _("Carte effacée"), _("Carte %s effacée.", self.name), "success"
            )
        return self.encoder_id._notify(
            _("Effacement impossible"), res["error"] or _("L'encodeur n'a pas répondu."), "danger"
        )

    def action_mark_lost(self):
        """Carte non rendue : elle reste valable jusqu'à sa date de fin ou jusqu'à
        l'utilisation d'une carte plus récente sur la serrure."""
        for card in self.filtered(lambda c: c.state == "active"):
            card.write({"state": "cancelled"})
            card.message_post(body=_(
                "Carte déclarée non rendue. Elle reste valable jusqu'au %s ou jusqu'à ce "
                "qu'une nouvelle carte soit passée sur la serrure.",
                fields.Datetime.to_string(card.end_date)))
            card._update_room_after_checkout()
        return True

    def action_cancel(self):
        self.filtered(lambda c: c.state in ("draft", "error")).write({"state": "cancelled"})
        return True

    def action_reset_draft(self):
        self.filtered(lambda c: c.state in ("cancelled", "error")).write({"state": "draft"})
        return True

    # ------------------------------------------------------------------
    # Appelés par le contrôleur du pont
    # ------------------------------------------------------------------
    def _on_encoded(self, card_data):
        self.write({
            "state": "active",
            "card_data": card_data or False,
            "issued_date": fields.Datetime.now(),
            "last_error": False,
        })
        self.room_id.filtered(lambda r: r.state != "occupied").write({"state": "occupied"})

    def _on_erased(self):
        self.write({"state": "erased", "erased_date": fields.Datetime.now()})
        self._update_room_after_checkout()

    def _update_room_after_checkout(self):
        for card in self:
            room = card.room_id
            others = room.card_ids.filtered(lambda c: c.state == "active" and c != card)
            if not others and room.state == "occupied":
                room.state = card.encoder_id.sudo().checkout_room_state or "dirty"
