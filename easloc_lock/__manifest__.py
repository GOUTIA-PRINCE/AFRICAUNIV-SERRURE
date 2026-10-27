{
    "name": "Serrures EASLOC proUSB",
    "version": "19.0.1.0.0",
    "category": "Services",
    "summary": "Encodage des cartes de chambre EASLOC proUSB (V9) depuis Odoo",
    "description": """
Intégration des serrures d'hôtel EASLOC proUSB (Card Lock System V9) dans Odoo.

* Chambres avec numéro de serrure (bâtiment / étage / n° interne)
* Émission de cartes client, copies de carte, effacement au départ
* Lecture d'une carte posée sur l'encodeur
* Communication avec l'encodeur USB via le pont Windows « easloc_bridge »
  (Python 32 bits + proRFL.dll du logiciel d'origine)
""",
    "author": "Votre société",
    "license": "LGPL-3",
    "depends": ["base", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_sequence.xml",
        "views/easloc_encoder_views.xml",
        "views/easloc_room_views.xml",
        "views/easloc_card_views.xml",
        "views/easloc_job_views.xml",
        "wizard/easloc_room_batch_views.xml",
        "views/menus.xml",
    ],
    "application": True,
    "installable": True,
}
