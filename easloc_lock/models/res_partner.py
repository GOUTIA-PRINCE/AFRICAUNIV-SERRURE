from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    easloc_cni = fields.Char(string="CNI", copy=False, groups="base.group_user")
