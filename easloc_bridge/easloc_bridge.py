#!/usr/bin/env python3
"""Pont entre Odoo (module easloc_lock) et l'encodeur USB EASLOC proUSB.

À lancer sur le PC Windows de la réception, avec un Python **32 bits**
(proRFL.dll est une DLL 32 bits). Aucune dépendance externe : bibliothèque
standard uniquement.

    python easloc_bridge.py --selftest      vérifie la DLL et l'encodeur
    python easloc_bridge.py                 démarre le pont
    python easloc_bridge.py --simulate      démarre sans encodeur (tests)

Configuration : easloc_bridge.ini dans le même dossier que ce script.
"""
import argparse
import configparser
import ctypes
import json
import logging
import logging.handlers
import os
import ssl
import struct
import sys
import time
import urllib.error
import urllib.request

__version__ = "1.0.0"

HERE = os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, "frozen", False) else __file__))
_logger = logging.getLogger("easloc_bridge")


# ----------------------------------------------------------------------
# Accès à proRFL.dll
# ----------------------------------------------------------------------
class EncoderError(Exception):
    pass


class ProRFL:
    """Enveloppe ctypes de proRFL.dll (Card Lock System V9).

    Nombre de paramètres de chaque fonction vérifié dans le code de la DLL
    (convention stdcall, « ret N » = N/4 paramètres) :
      initializeUSB(fUSB)                                     1
      CloseUSB(fUSB)                                          1
      Buzzer(fUSB, t)                                         2
      GetDLLVersion(buf)                                      1
      ReadCard(fUSB, buf)                                     2
      GuestCard(fUSB, dlsCoID, CardNo, dai, LLock, pdoors,
                BDate, EDate, LockNo, cardHexStr)            10
      CardErase(fUSB, dlsCoID, cardHexStr)                    3
      GetCardTypeByCardDataStr(cardData, buf)                 2
      GetGuestLockNoByCardDataStr(dlsCoID, cardData, buf)     3
      GetGuestSTimeByCardDataStr(dlsCoID, cardData, buf)      3
      GetGuestETimeByCardDataStr(dlsCoID, cardData, buf)      3
    Toutes renvoient 0 en cas de succès.
    """

    BUF = 1024

    def __init__(self, dll_dir, fusb="auto"):
        if struct.calcsize("P") != 4:
            raise EncoderError(
                "proRFL.dll est une DLL 32 bits : installez et utilisez un Python 32 bits "
                "(« Windows installer (32-bit) » sur python.org)."
            )
        path = os.path.join(dll_dir, "proRFL.dll")
        if not os.path.isfile(path):
            raise EncoderError("proRFL.dll introuvable dans %s" % dll_dir)
        # La DLL charge d12.dll / d12c.dll / MFC42D.DLL / MSVCRTD.DLL depuis son dossier
        os.add_dll_directory(dll_dir)
        os.chdir(dll_dir)
        self.dll = ctypes.WinDLL(path)
        I, S = ctypes.c_int, ctypes.c_char_p
        sig = {
            "initializeUSB": [I],
            "CloseUSB": [I],
            "Buzzer": [I, I],
            "GetDLLVersion": [S],
            "ReadCard": [I, S],
            "GuestCard": [I, I, I, I, I, I, S, S, S, S],
            "CardErase": [I, I, S],
            "GetCardTypeByCardDataStr": [S, S],
            "GetGuestLockNoByCardDataStr": [I, S, S],
            "GetGuestSTimeByCardDataStr": [I, S, S],
            "GetGuestETimeByCardDataStr": [I, S, S],
        }
        for name, argtypes in sig.items():
            fn = getattr(self.dll, name)
            fn.argtypes = argtypes
            fn.restype = I
        self.fusb = None if str(fusb).lower() == "auto" else int(fusb)

    # -- utilitaires ----------------------------------------------------
    @staticmethod
    def _buf():
        return ctypes.create_string_buffer(ProRFL.BUF)

    @staticmethod
    def _str(buf):
        return buf.value.decode("latin-1").strip()

    def version(self):
        buf = self._buf()
        self.dll.GetDLLVersion(buf)
        return self._str(buf)

    def open(self):
        candidates = [self.fusb] if self.fusb is not None else [1, 0]
        last = None
        for fusb in candidates:
            last = self.dll.initializeUSB(fusb)
            if last == 0:
                self.fusb = fusb
                return fusb
        raise EncoderError(
            "Encodeur USB non détecté (initializeUSB=%s). Vérifiez le câble et fermez "
            "le logiciel Card Lock System s'il est ouvert." % last
        )

    def close(self):
        if self.fusb is not None:
            try:
                self.dll.CloseUSB(self.fusb)
            except OSError:
                pass

    def buzzer(self, t=10):
        return self.dll.Buzzer(self.fusb, t)

    def read_card(self):
        buf = self._buf()
        ret = self.dll.ReadCard(self.fusb, buf)
        return ret, self._str(buf)

    def guest_card(self, dls_co_id, card_no, dai, llock, pdoors, bdate, edate, lock_no):
        buf = self._buf()
        ret = self.dll.GuestCard(
            self.fusb, dls_co_id, card_no, dai, llock, pdoors,
            bdate.encode(), edate.encode(), lock_no.encode(), buf,
        )
        return ret, self._str(buf)

    def card_erase(self, dls_co_id):
        buf = self._buf()
        ret = self.dll.CardErase(self.fusb, dls_co_id, buf)
        return ret, self._str(buf)

    def card_type(self, data):
        buf = self._buf()
        ret = self.dll.GetCardTypeByCardDataStr(data.encode(), buf)
        raw = buf.raw[:1]
        text = self._str(buf)
        if not text or not text.isprintable():
            text = str(raw[0]) if raw else ""
        return ret, text

    def guest_lock_no(self, dls_co_id, data):
        buf = self._buf()
        ret = self.dll.GetGuestLockNoByCardDataStr(dls_co_id, data.encode(), buf)
        return ret, self._str(buf)

    def guest_times(self, dls_co_id, data):
        b1, b2 = self._buf(), self._buf()
        r1 = self.dll.GetGuestSTimeByCardDataStr(dls_co_id, data.encode(), b1)
        r2 = self.dll.GetGuestETimeByCardDataStr(dls_co_id, data.encode(), b2)
        return (self._str(b1) if r1 == 0 else ""), (self._str(b2) if r2 == 0 else "")


class FakeProRFL:
    """Encodeur simulé : permet de tester Odoo + le pont sans matériel."""

    def __init__(self, *args, **kwargs):
        self.fusb = 1
        self.card = ""  # données de la carte « posée » sur l'encodeur

    def version(self):
        return "SIMULATION"

    def open(self):
        return 1

    def close(self):
        pass

    def buzzer(self, t=10):
        _logger.info("[simulation] bip")
        return 0

    def read_card(self):
        return (0, self.card) if self.card else (1, "")

    def guest_card(self, dls_co_id, card_no, dai, llock, pdoors, bdate, edate, lock_no):
        self.card = "G|%06X|%s|%s|%s|%d" % (dls_co_id, lock_no, bdate, edate, card_no)
        return 0, self.card

    def card_erase(self, dls_co_id):
        self.card = ""
        return 0, ""

    def card_type(self, data):
        return (0, "6") if data.startswith("G|") else (1, "")

    def guest_lock_no(self, dls_co_id, data):
        parts = data.split("|")
        if len(parts) > 2 and parts[1] == "%06X" % dls_co_id:
            return 0, parts[2]
        return 1, ""

    def guest_times(self, dls_co_id, data):
        parts = data.split("|")
        return (parts[3], parts[4]) if len(parts) > 4 else ("", "")


# ----------------------------------------------------------------------
# Exécution des opérations
# ----------------------------------------------------------------------
def _fmt_lock_date(value):
    """« 2610281200 » → « 28/10/2026 12:00 »."""
    if len(value) >= 10 and value[:10].isdigit():
        return "%s/%s/20%s %s:%s" % (value[4:6], value[2:4], value[0:2], value[6:8], value[8:10])
    return value


class Bridge:
    def __init__(self, cfg, encoder):
        self.url = cfg.get("odoo", "url").rstrip("/")
        self.key = cfg.get("odoo", "api_key")
        self.poll_interval = cfg.getfloat("bridge", "poll_interval", fallback=1.0)
        self.card_wait = cfg.getfloat("bridge", "card_wait", fallback=8.0)
        self.buzzer_ms = cfg.getint("bridge", "buzzer", fallback=10)
        verify = cfg.getboolean("odoo", "verify_ssl", fallback=True)
        self.ssl_ctx = None if verify else ssl._create_unverified_context()
        self.encoder = encoder
        self.dll_version = ""

    # -- HTTP -------------------------------------------------------------
    def _post(self, path, data):
        req = urllib.request.Request(
            self.url + path,
            data=json.dumps(data).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Easloc-Key": self.key,
                "User-Agent": "easloc-bridge/" + __version__,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30, context=self.ssl_ctx) as resp:
            return json.loads(resp.read().decode() or "{}")

    # -- opérations ---------------------------------------------------------
    def _retry(self, fn):
        """Réessaie tant que la carte n'est pas posée (ou mal posée) sur l'encodeur."""
        deadline = time.monotonic() + self.card_wait
        while True:
            ret, data = fn()
            if ret == 0 or time.monotonic() >= deadline:
                return ret, data
            time.sleep(0.4)

    def do_guest(self, p):
        ret, data = self._retry(lambda: self.encoder.guest_card(
            int(p["dls_co_id"]), int(p.get("card_no", 0)), int(p.get("dai", 0)),
            int(p.get("llock", 1)), int(p.get("pdoors", 0)),
            p["bdate"], p["edate"], p["lock_no"],
        ))
        if ret != 0:
            raise EncoderError(
                "Encodage refusé (code %s). Posez la carte bien à plat sur l'encodeur "
                "et réessayez." % ret
            )
        self.encoder.buzzer(self.buzzer_ms)
        return {"card_data": data, "lock_no": p["lock_no"]}

    def do_read(self, p):
        dls = int(p.get("dls_co_id", 0))
        ret, data = self._retry(self.encoder.read_card)
        if ret != 0 or not data:
            raise EncoderError("Aucune carte lisible sur l'encodeur (code %s)." % ret)
        _r, ctype = self.encoder.card_type(data)
        r_lock, lock_no = self.encoder.guest_lock_no(dls, data)
        begin, end = self.encoder.guest_times(dls, data) if r_lock == 0 else ("", "")
        self.encoder.buzzer(self.buzzer_ms)
        return {
            "card_data": data,
            "card_type": ctype,
            "card_type_label": ("Carte client" if r_lock == 0 and lock_no
                                else "Autre carte / autre hôtel (type %s)" % ctype),
            "lock_no": lock_no if r_lock == 0 else "",
            "begin": _fmt_lock_date(begin),
            "end": _fmt_lock_date(end),
        }

    def do_erase(self, p):
        dls = int(p.get("dls_co_id", 0))
        expected = p.get("expected_lock_no") or ""
        if expected:
            ret, data = self._retry(self.encoder.read_card)
            if ret != 0 or not data:
                raise EncoderError("Aucune carte lisible sur l'encodeur (code %s)." % ret)
            r_lock, lock_no = self.encoder.guest_lock_no(dls, data)
            if r_lock != 0 or not lock_no:
                raise EncoderError(
                    "La carte posée n'est pas une carte client de cet hôtel : effacement refusé."
                )
            if lock_no != expected:
                raise EncoderError(
                    "La carte posée est celle de la serrure %s, pas de la chambre %s (%s) : "
                    "effacement refusé." % (lock_no, p.get("room", "?"), expected)
                )
        ret, _data = self._retry(lambda: self.encoder.card_erase(dls))
        if ret != 0:
            raise EncoderError("Effacement refusé (code %s)." % ret)
        self.encoder.buzzer(self.buzzer_ms)
        return {"erased": True}

    def do_buzzer(self, p):
        ret = self.encoder.buzzer(self.buzzer_ms * 3)
        if ret != 0:
            raise EncoderError("Bip refusé (code %s)." % ret)
        return {"buzzer": True}

    def run_job(self, job):
        handler = {
            "guest": self.do_guest,
            "read": self.do_read,
            "erase": self.do_erase,
            "buzzer": self.do_buzzer,
        }.get(job["type"])
        _logger.info("Opération %s #%s %s", job["type"], job["id"], job.get("payload", {}).get("room", ""))
        try:
            if not handler:
                raise EncoderError("Opération inconnue : %s" % job["type"])
            self.encoder.open()
            try:
                result = handler(job.get("payload") or {})
            finally:
                self.encoder.close()
            out = {"job_id": job["id"], "ok": True, "result": result}
            _logger.info("  → OK %s", result)
        except Exception as e:  # noqa: BLE001 – toute erreur est renvoyée à Odoo
            _logger.warning("  → ERREUR %s", e)
            out = {"job_id": job["id"], "ok": False, "error": str(e)}
        self._post("/easloc/bridge/result", out)

    # -- boucle principale --------------------------------------------------
    def loop(self):
        try:
            self.dll_version = self.encoder.version()
        except Exception:  # noqa: BLE001
            self.dll_version = "?"
        _logger.info("Pont EASLOC %s démarré – Odoo %s – DLL %s", __version__, self.url, self.dll_version)
        backoff = self.poll_interval
        connected = False
        while True:
            try:
                data = self._post("/easloc/bridge/poll", {
                    "bridge_version": __version__,
                    "dll_version": self.dll_version,
                })
                if not connected:
                    _logger.info("Connecté à Odoo (encodeur « %s »)", data.get("encoder", {}).get("name"))
                    connected = True
                backoff = self.poll_interval
                for job in data.get("jobs", []):
                    self.run_job(job)
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    _logger.error("Clé refusée par Odoo : vérifiez api_key dans easloc_bridge.ini")
                else:
                    _logger.error("Erreur HTTP %s depuis Odoo", e.code)
                connected = False
                backoff = min(backoff * 2, 30)
            except (urllib.error.URLError, OSError, ValueError) as e:
                if connected:
                    _logger.error("Odoo injoignable : %s", e)
                connected = False
                backoff = min(backoff * 2, 30)
            time.sleep(backoff)


# ----------------------------------------------------------------------
def selftest(encoder, cfg):
    print("Python :", sys.version.split()[0], "%d bits" % (struct.calcsize("P") * 8))
    print("Version DLL :", encoder.version())
    fusb = encoder.open()
    print("Encodeur détecté (fUSB=%s) – vous devez entendre un bip." % fusb)
    encoder.buzzer(30)
    ret, data = encoder.read_card()
    if ret == 0 and data:
        dls = cfg.getint("bridge", "dls_co_id", fallback=0)
        print("Carte présente :", data[:64] + ("…" if len(data) > 64 else ""))
        print("  type :", encoder.card_type(data)[1])
        r, lock_no = encoder.guest_lock_no(dls, data)
        print("  n° de serrure (dlsCoID=%s) :" % dls, lock_no if r == 0 else "illisible (code %s)" % r)
    else:
        print("Pas de carte sur l'encodeur (code %s). Posez une carte et relancez pour la lire." % ret)
    encoder.close()
    print("Autotest terminé.")


def main():
    parser = argparse.ArgumentParser(description="Pont Odoo ⇄ encodeur EASLOC proUSB")
    parser.add_argument("-c", "--config", default=os.path.join(HERE, "easloc_bridge.ini"))
    parser.add_argument("--selftest", action="store_true", help="tester la DLL et l'encodeur puis quitter")
    parser.add_argument("--simulate", action="store_true", help="encodeur simulé (sans matériel)")
    args = parser.parse_args()

    cfg = configparser.ConfigParser()
    if not cfg.read(args.config, encoding="utf-8"):
        sys.exit("Fichier de configuration introuvable : %s" % args.config)

    log_file = cfg.get("bridge", "log_file", fallback=os.path.join(HERE, "easloc_bridge.log"))
    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.handlers.RotatingFileHandler(
            log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(message)s")

    if args.simulate or cfg.getboolean("bridge", "simulate", fallback=False):
        encoder = FakeProRFL()
    else:
        try:
            encoder = ProRFL(cfg.get("bridge", "dll_dir"), cfg.get("bridge", "fusb", fallback="auto"))
        except (EncoderError, OSError) as e:
            sys.exit("Impossible de charger proRFL.dll : %s" % e)

    if args.selftest:
        try:
            selftest(encoder, cfg)
        except EncoderError as e:
            sys.exit(str(e))
        return
    try:
        Bridge(cfg, encoder).loop()
    except KeyboardInterrupt:
        _logger.info("Arrêt du pont.")


if __name__ == "__main__":
    main()
