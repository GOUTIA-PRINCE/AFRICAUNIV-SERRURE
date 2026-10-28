from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    easloc_card_id = fields.Many2one(
        "easloc.card", string="Carte / séjour EASLOC", index=True, copy=False, ondelete="set null"
    )

    _easloc_card_invoice_uniq = models.Constraint(
        "UNIQUE(easloc_card_id)", "Une seule facture EASLOC peut être liée à une carte principale."
    )
