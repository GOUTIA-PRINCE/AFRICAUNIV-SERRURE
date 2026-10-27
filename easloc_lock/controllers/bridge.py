"""Points d'accès appelés par le pont Windows (easloc_bridge.py).

Le pont interroge Odoo (il n'y a donc aucun port à ouvrir sur le PC de
réception) et s'authentifie avec la clé de l'encodeur dans l'en-tête
« X-Easloc-Key ».
"""
import hmac
import logging
from datetime import timedelta

from werkzeug.exceptions import BadRequest, Forbidden

from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)

# Une opération restée « en cours » plus longtemps est considérée perdue
STALE_PROCESSING = timedelta(minutes=2)


class EaslocBridgeController(http.Controller):

    def _get_encoder(self):
        key = request.httprequest.headers.get("X-Easloc-Key", "")
        if len(key) < 20:
            raise Forbidden()
        encoder = request.env["easloc.encoder"].sudo().search(
            [("api_key", "=", key), ("active", "=", True)], limit=1
        )
        if not encoder or not hmac.compare_digest(encoder.api_key or "", key):
            raise Forbidden()
        return encoder

    def _json(self):
        try:
            return request.get_json_data() or {}
        except ValueError as e:
            raise BadRequest("JSON invalide") from e

    @http.route("/easloc/bridge/poll", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def poll(self):
        encoder = self._get_encoder()
        data = self._json()
        now = fields.Datetime.now()
        vals = {}
        if not encoder.last_seen or now - encoder.last_seen > timedelta(seconds=10):
            vals["last_seen"] = now
        for key, field in (("bridge_version", "bridge_version"), ("dll_version", "dll_version")):
            if data.get(key) and data[key] != encoder[field]:
                vals[field] = str(data[key])[:64]
        if vals:
            encoder.write(vals)

        Job = request.env["easloc.job"].sudo()
        # Opérations orphelines (pont redémarré pendant l'exécution)
        Job.search([
            ("encoder_id", "=", encoder.id),
            ("state", "=", "processing"),
            ("write_date", "<", now - STALE_PROCESSING),
        ]).write({"state": "error", "error": "Pas de réponse du pont"})

        jobs = Job.search(
            [("encoder_id", "=", encoder.id), ("state", "=", "pending")], order="id", limit=5
        )
        jobs.write({"state": "processing"})
        return request.make_json_response({
            "encoder": {"id": encoder.id, "name": encoder.name, "dls_co_id": encoder.dls_co_id},
            "jobs": [
                {"id": job.id, "type": job.job_type, "payload": job.payload or {}}
                for job in jobs
            ],
        })

    @http.route("/easloc/bridge/result", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def result(self):
        encoder = self._get_encoder()
        data = self._json()
        try:
            job_id = int(data.get("job_id"))
        except (TypeError, ValueError) as e:
            raise BadRequest("job_id manquant") from e
        job = request.env["easloc.job"].sudo().browse(job_id).exists()
        if not job or job.encoder_id != encoder:
            raise BadRequest("Opération inconnue")
        if job.state not in ("pending", "processing"):
            return request.make_json_response({"status": "ignored", "state": job.state})
        job._apply_result(bool(data.get("ok")), data.get("result") or {}, data.get("error"))
        return request.make_json_response({"status": "ok", "state": job.state})
