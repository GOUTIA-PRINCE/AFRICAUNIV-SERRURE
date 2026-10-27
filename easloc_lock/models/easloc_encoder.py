import logging
import secrets
import time
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.addons.base.models.res_partner import _tz_get
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Durée sans nouvelles du pont au-delà de laquelle il est considéré hors ligne
BRIDGE_ONLINE_DELAY = 30


def _new_api_key(*_args):
    return secrets.token_urlsafe(32)


class EaslocEncoder(models.Model):
    _name = "easloc.encoder"
    _description = "Encodeur EASLOC (poste de réception)"
    _order = "sequence, id"

    name = fields.Char("Nom", required=True, default=lambda self: _("Réception"))
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", "Société", required=True, default=lambda self: self.env.company
    )
    dls_co_id = fields.Integer(
        "Code hôtel (dlsCoID)",
        required=True,
        help="Code hôtel avec lequel les serrures ont été autorisées par le logiciel "
        "EASLOC. Il doit être identique, sinon les serrures refuseront les cartes. "
        "Demandez-le au fournisseur ou lisez-le avec « Lire une carte ».",
    )
    tz = fields.Selection(
        _tz_get,
        "Fuseau horaire des serrures",
        required=True,
        default=lambda self: self.env.user.tz or "Africa/Lagos",
        help="Les serrures sont à l'heure locale du PC de réception.",
    )
    dai = fields.Integer(
        "Génération (dai)",
        default=0,
        help="Paramètre « dai » de la DLL (table Dai du logiciel d'origine). Laisser 0 "
        "sauf indication contraire du fournisseur.",
    )
    default_llock = fields.Boolean(
        "Ouvre le verrou intérieur (par défaut)",
        default=True,
        help="Carte client autorisée à ouvrir même si le pêne dormant est fermé "
        "(GuestLLock du logiciel d'origine).",
    )
    default_pdoors = fields.Boolean("Ouvre les portes communes (par défaut)", default=False)
    verify_before_erase = fields.Boolean(
        "Vérifier la chambre avant effacement",
        default=True,
        help="Lit la carte avant de l'effacer et refuse si elle appartient à une autre chambre.",
    )
    checkout_room_state = fields.Selection(
        [("vacant", "Libre"), ("dirty", "À nettoyer")],
        "État de la chambre après départ",
        default="dirty",
        required=True,
    )
    wait_timeout = fields.Integer(
        "Délai d'attente (s)",
        default=25,
        help="Temps maximum pendant lequel Odoo attend la réponse de l'encodeur.",
    )
    api_key = fields.Char(
        "Clé du pont",
        default=_new_api_key,
        copy=False,
        groups="base.group_system",
        help="À recopier dans easloc_bridge.ini sur le PC de réception.",
    )
    last_seen = fields.Datetime("Dernier contact du pont", readonly=True, copy=False)
    bridge_version = fields.Char("Version du pont", readonly=True, copy=False)
    dll_version = fields.Char("Version proRFL.dll", readonly=True, copy=False)
    bridge_online = fields.Boolean("Pont en ligne", compute="_compute_bridge_online")

    _api_key_uniq = models.Constraint("UNIQUE(api_key)", "Cette clé de pont est déjà utilisée.")

    @api.depends("last_seen")
    def _compute_bridge_online(self):
        limit = fields.Datetime.now() - timedelta(seconds=BRIDGE_ONLINE_DELAY)
        for encoder in self:
            encoder.bridge_online = bool(encoder.last_seen and encoder.last_seen >= limit)

    @api.model
    def _get_default(self, company=None):
        company = company or self.env.company
        encoder = self.search([("company_id", "=", company.id)], limit=1)
        return encoder

    def action_regenerate_key(self):
        self.ensure_one()
        self.sudo().api_key = _new_api_key()
        return True

    # ------------------------------------------------------------------
    # Exécution d'une opération sur l'encodeur
    # ------------------------------------------------------------------
    def _run_job(self, job_type, payload, card=None):
        """Crée une opération, attend que le pont l'exécute et renvoie le résultat.

        L'opération est créée dans un curseur séparé (validé immédiatement) pour
        que le pont puisse la voir pendant que la requête de l'utilisateur attend.
        La transaction courante ne doit rien écrire après cet appel : c'est le
        contrôleur du pont qui met à jour la carte et la chambre.

        :return: dict(state, result, error, job_id)
        """
        self.ensure_one()
        if not self.bridge_online:
            raise UserError(
                _(
                    "Le pont de l'encodeur « %(name)s » est hors ligne. Vérifiez que le "
                    "programme easloc_bridge tourne sur le PC de réception et que "
                    "l'encodeur est branché.",
                    name=self.name,
                )
            )
        registry = self.env.registry
        with registry.cursor() as cr:
            env = self.env(cr=cr, su=True)
            job = env["easloc.job"].create(
                {
                    "encoder_id": self.id,
                    "job_type": job_type,
                    "card_id": card.id if card else False,
                    "payload": payload,
                    "user_id": self.env.uid,
                }
            )
            job_id = job.id

        deadline = time.monotonic() + max(self.wait_timeout, 5)
        state = "pending"
        while time.monotonic() < deadline:
            time.sleep(0.5)
            with registry.cursor() as cr:
                cr.execute("SELECT state FROM easloc_job WHERE id = %s", [job_id])
                state = cr.fetchone()[0]
            if state in ("done", "error", "cancelled"):
                break
        else:
            # Pas de réponse : on annule si le pont ne l'a pas encore prise,
            # pour éviter qu'une carte soit encodée plus tard par surprise.
            with registry.cursor() as cr:
                cr.execute(
                    """UPDATE easloc_job SET state = 'cancelled', error = %s
                       WHERE id = %s AND state = 'pending' RETURNING id""",
                    ["Délai dépassé : le pont n'a pas répondu", job_id],
                )
                if cr.fetchone():
                    state = "cancelled"

        with registry.cursor() as cr:
            env = self.env(cr=cr, su=True)
            job = env["easloc.job"].browse(job_id)
            return {
                "job_id": job_id,
                "state": job.state,
                "result": job.result or {},
                "error": job.error or "",
            }

    # ------------------------------------------------------------------
    # Lecture d'une carte
    # ------------------------------------------------------------------
    @api.model
    def action_read_card_default(self):
        encoder = self._get_default()
        if not encoder:
            raise UserError(_("Aucun encodeur n'est configuré pour cette société."))
        return encoder.action_read_card()

    def action_read_card(self):
        self.ensure_one()
        res = self._run_job("read", {"dls_co_id": self.dls_co_id})
        if res["state"] != "done":
            return self._notify(
                _("Lecture impossible"),
                res["error"] or _("L'encodeur n'a pas répondu."),
                "danger",
            )
        data = res["result"]
        lock_no = data.get("lock_no") or ""
        room = self.env["easloc.room"].search(
            [("lock_no", "=", lock_no), ("company_id", "=", self.company_id.id)], limit=1
        ) if lock_no else self.env["easloc.room"]
        card = self.env["easloc.card"].search(
            [("card_data", "=", data.get("card_data")), ("card_data", "!=", False)], limit=1
        ) if data.get("card_data") else self.env["easloc.card"]
        lines = [
            _("Type de carte : %s", data.get("card_type_label") or data.get("card_type") or "?"),
            _("N° de serrure : %s", lock_no or "—"),
            _(
                "Chambre Odoo : %s",
                room.name if room else (_("aucune (n° de serrure inconnu)") if lock_no else "—"),
            ),
        ]
        if data.get("begin"):
            lines.append(_("Début : %s", data["begin"]))
        if data.get("end"):
            lines.append(_("Fin : %s", data["end"]))
        if card:
            lines.append(_("Carte Odoo : %(name)s – %(guest)s", name=card.name, guest=card.guest_name))
        return self._notify(_("Carte lue"), "\n".join(lines), "info", sticky=True)

    def action_test_buzzer(self):
        self.ensure_one()
        res = self._run_job("buzzer", {})
        if res["state"] == "done":
            return self._notify(_("Encodeur OK"), _("L'encodeur a bipé."), "success")
        return self._notify(_("Échec"), res["error"] or _("Pas de réponse."), "danger")

    @api.model
    def _notify(self, title, message, kind="info", sticky=False, reload=True):
        params = {"title": title, "message": message, "type": kind, "sticky": sticky}
        if reload:
            params["next"] = {"type": "ir.actions.client", "tag": "soft_reload"}
        return {"type": "ir.actions.client", "tag": "display_notification", "params": params}
