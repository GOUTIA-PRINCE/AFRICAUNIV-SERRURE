from odoo import fields, models


class EaslocJob(models.Model):
    _name = "easloc.job"
    _description = "Opération sur l'encodeur EASLOC"
    _order = "id desc"

    encoder_id = fields.Many2one("easloc.encoder", "Encodeur", required=True, ondelete="cascade", index=True)
    job_type = fields.Selection(
        [
            ("guest", "Encodage carte client"),
            ("erase", "Effacement"),
            ("read", "Lecture"),
            ("buzzer", "Test (bip)"),
        ],
        "Opération", required=True,
    )
    card_id = fields.Many2one("easloc.card", "Carte", ondelete="set null", index=True)
    user_id = fields.Many2one("res.users", "Demandé par")
    state = fields.Selection(
        [
            ("pending", "En attente"),
            ("processing", "En cours"),
            ("done", "Terminé"),
            ("error", "Erreur"),
            ("cancelled", "Annulé"),
        ],
        "État", default="pending", required=True, index=True,
    )
    payload = fields.Json("Paramètres")
    result = fields.Json("Résultat")
    error = fields.Char("Erreur")
    date_done = fields.Datetime("Terminé le")

    def _apply_result(self, ok, result, error):
        self.ensure_one()
        self.write({
            "state": "done" if ok else "error",
            "result": result or {},
            "error": (error or "")[:500] or False,
            "date_done": fields.Datetime.now(),
        })
        card = self.card_id
        if not card:
            return
        if self.job_type == "guest":
            if ok:
                card._on_encoded((result or {}).get("card_data"))
            else:
                card.write({"state": "error", "last_error": self.error})
        elif self.job_type == "erase":
            if ok:
                card._on_erased()
            else:
                card.last_error = self.error
