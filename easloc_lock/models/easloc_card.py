from datetime import datetime, time, timedelta
import pytz

from odoo import _, api, fields, models, Command
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
    cni = fields.Char(related="partner_id.easloc_cni", string="CNI", readonly=True)
    line_ids = fields.One2many(
        "easloc.card.line", "card_id", string="Produits / services", copy=False
    )
    invoice_ids = fields.One2many("account.move", "easloc_card_id", string="Factures")
    invoice_count = fields.Integer(compute="_compute_invoice_count")
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
        tz = pytz.timezone(self.env.user.tz or "UTC")
        tomorrow = datetime.now(tz).date() + timedelta(days=1)
        local = tz.localize(datetime.combine(tomorrow, time(12, 0)))
        return local.astimezone(pytz.UTC).replace(tzinfo=None)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].next_by_code("easloc.card") or "/"
        return super().create(vals_list)

    @api.depends("invoice_ids", "parent_id.invoice_ids")
    def _compute_invoice_count(self):
        for card in self:
            origin = card.parent_id or card
            card.invoice_count = len(origin.sudo().invoice_ids)

    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        if self.partner_id:
            self.guest_name = self.partner_id.name

    def _create_customer_invoice(self):
        self.ensure_one()
        if self.parent_id:
            return (self.parent_id or self).invoice_ids[:1]
        if self.invoice_ids:
            return self.invoice_ids[:1]
        if not self.partner_id:
            raise UserError(_("Sélectionnez la fiche contact du client avant l'encodage."))
        if not self.line_ids:
            raise UserError(_("Ajoutez au moins un produit ou service avant l'encodage."))

        invoice_lines = []
        for line in self.line_ids:
            taxes = line.product_id.taxes_id.filtered_domain(
                self.env["account.tax"]._check_company_domain(self.company_id)
            )
            invoice_lines.append(Command.create({
                "product_id": line.product_id.id,
                "name": line.name or line.product_id.display_name,
                "quantity": line.quantity,
                "price_unit": line.price_unit,
                "tax_ids": [Command.set(taxes.ids)],
            }))
        return self.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": self.partner_id.id,
            "company_id": self.company_id.id,
            "invoice_origin": self.name,
            "ref": self.name,
            "easloc_card_id": self.id,
            "invoice_line_ids": invoice_lines,
        })

    def action_view_invoices(self):
        self.ensure_one()
        origin = self.parent_id or self
        invoices = origin.invoice_ids
        return {
            "type": "ir.actions.act_window",
            "name": _("Facture client"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", invoices.ids)],
            "context": {"create": False},
        }

    def action_create_invoice(self):
        self.ensure_one()
        if self.parent_id:
            raise UserError(_("La facture est liée à la carte principale du séjour."))
        if self.state != "active":
            raise UserError(_("La carte doit être encodée avant de créer sa facture."))
        invoice = self._create_customer_invoice()
        return {
            "type": "ir.actions.act_window",
            "name": _("Facture client"),
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": invoice.id,
            "target": "current",
        }

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
        tz = pytz.timezone(self.encoder_id.sudo().tz or "UTC")
        value_utc = pytz.UTC.localize(value) if value.tzinfo is None else value.astimezone(pytz.UTC)
        return value_utc.astimezone(tz).strftime(LOCK_DATE_FORMAT)

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
        if not self.partner_id:
            raise UserError(_("Sélectionnez la fiche contact du client avant l'encodage."))
        if not self.line_ids:
            raise UserError(_("Ajoutez au moins un produit ou service avant l'encodage."))
        res = self.encoder_id._run_job("guest", self._guest_payload(), card=self)
        if res["state"] == "done":
            if not self.parent_id:
                self._create_customer_invoice()
            return self.encoder_id._notify(
                _("Carte encodée"),
                _("Carte de la chambre %(room)s prête pour %(guest)s. La facture client a été créée en brouillon.",
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

class EaslocCardLine(models.Model):
    _name = "easloc.card.line"
    _description = "Produit ou service facturé sur une carte EASLOC"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    card_id = fields.Many2one("easloc.card", required=True, ondelete="cascade", index=True)
    product_id = fields.Many2one(
        "product.product", string="Produit / service", required=True,
        domain=[("type", "=", "service"), ("sale_ok", "=", True)],
    )
    name = fields.Char(string="Description")
    quantity = fields.Float(
        string="Heures", default=1.0, required=True,
        help="Nombre d'heures facturées. Configurez l'unité de mesure du produit en heures.",
    )
    price_unit = fields.Float(string="Prix unitaire", required=True, digits="Product Price")
    subtotal = fields.Monetary(string="Sous-total", compute="_compute_subtotal", currency_field="currency_id")
    currency_id = fields.Many2one(related="card_id.company_id.currency_id", store=True, readonly=True)

    @api.onchange("product_id")
    def _onchange_product_id(self):
        if self.product_id:
            self.name = self.product_id.display_name
            self.price_unit = self.product_id.lst_price

    @api.depends("quantity", "price_unit")
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = line.quantity * line.price_unit

    @api.constrains("quantity", "price_unit")
    def _check_amounts(self):
        for line in self:
            if line.quantity <= 0:
                raise ValidationError(_("La quantité doit être supérieure à zéro."))
            if line.price_unit < 0:
                raise ValidationError(_("Le prix unitaire ne peut pas être négatif."))
