# Intégration EASLOC proUSB ⇄ Odoo 19

Deux éléments :

| Dossier | Où l'installer | Rôle |
|---|---|---|
| `easloc_lock/` | Serveur Odoo 19 (addons) | Chambres, cartes client, départs, lecture de carte, journal |
| `easloc_bridge/` | PC Windows de la réception (celui où l'encodeur USB est branché) | Reçoit les ordres d'Odoo et pilote l'encodeur via `proRFL.dll` |

```
Odoo 19 (cloud ou serveur)  ◄── HTTPS (le PC interroge Odoo) ──  easloc_bridge.py  ──►  proRFL.dll  ──►  encodeur USB
```

Le PC appelle Odoo, pas l'inverse : aucun port à ouvrir, ça fonctionne avec Odoo.sh, Odoo en ligne
(si les modules personnalisés sont autorisés) ou un serveur local.

---

## 1. Côté Odoo

1. Copier `easloc_lock` dans un dossier d'addons, redémarrer Odoo, mettre à jour la liste des
   applications, installer **Serrures EASLOC proUSB** (dépend seulement de `mail`).
2. **Serrures › Configuration › Encodeurs** → Nouveau :
   - **Code hôtel (dlsCoID)** : *obligatoire et doit être exact* (voir §4).
   - **Fuseau horaire des serrures** : celui du PC de réception (ex. Africa/Lagos).
   - Copier la **Clé du pont** (bouton copier).
3. **Serrures › Configuration › Créer des chambres en lot** : même saisie que « Room Definition »
   du logiciel EASLOC (ex. étages `1-5`, chambres `101-518`). Supprimez ensuite les chambres
   inexistantes. Le n° de serrure = bâtiment + étage + n° interne sur 2 chiffres (301 → `010301`),
   exactement comme le logiciel d'origine.

Si plusieurs bases sont servies par le même Odoo, configurez `dbfilter` pour que l'URL du pont
tombe sur la bonne base.

## 2. Côté PC de réception (Windows)

1. Le logiciel **Card Lock System** doit déjà être installé (il fournit `proRFL.dll`, `d12c.dll`…).
2. Installer **Python 3 32 bits** depuis python.org (« Windows installer (32-bit) »).
   ⚠ `proRFL.dll` est une DLL 32 bits : un Python 64 bits ne peut pas la charger.
3. Copier le dossier `easloc_bridge` (ex. `C:\easloc_bridge`).
4. Éditer `easloc_bridge.ini` :
   - `url` = adresse de votre Odoo, `api_key` = clé copiée à l'étape 1.2
   - `dll_dir` = dossier d'installation de Card Lock System (clic droit sur l'icône du logiciel
     › *Ouvrir l'emplacement du fichier*)
5. **Fermer le logiciel Card Lock System**, brancher l'encodeur, lancer `autotest.bat` :
   l'encodeur doit biper. Posez une carte déjà encodée par le logiciel d'origine et relancez :
   le n° de serrure s'affiche (mettez `dls_co_id` dans l'ini pour le décoder).
6. Lancer `lancer_pont.bat` (fenêtre visible) pour les essais. Dans Odoo, l'encodeur passe
   « Pont en ligne ». Ensuite, `demarrage_automatique.bat` le lance sans fenêtre à chaque session.

Sans matériel : `py -3-32 easloc_bridge.py --simulate` (encodeur simulé).

## 3. Utilisation quotidienne

| Action | Où | Ce qu'il faut faire |
|---|---|---|
| Arrivée | Chambre › **Nouvelle carte** (ou Cartes client › Nouveau) | Client + date de départ, **poser la carte sur l'encodeur**, cliquer **Encoder la carte** |
| 2ᵉ carte même séjour | Carte active › **Carte supplémentaire** | Puis *Encoder la carte* (n° de carte +1, même début) |
| Départ | Carte active › **Départ : effacer la carte** | Poser la carte rendue sur l'encodeur. Elle est lue d'abord : si elle appartient à une autre chambre, l'effacement est refusé |
| Carte non rendue | Carte active › **Carte non rendue** | Elle reste valable jusqu'à sa date de fin **ou** jusqu'à ce qu'une carte plus récente soit passée sur la serrure |
| Identifier une carte | Réception › **Lire une carte** | Affiche type, n° de serrure, chambre, dates |

Odoo attend la réponse de l'encodeur (25 s maximum, réglable). Si le pont ne répond pas,
l'opération est annulée : aucune carte ne sera encodée « plus tard » par surprise.

## 4. Points à valider sur place avant la mise en service

1. **Code hôtel (dlsCoID)** : à demander au fournisseur EASLOC (il est lié à l'enregistrement du
   logiciel). Contrôle : *Lire une carte* sur une carte faite par le logiciel d'origine doit
   afficher le bon n° de serrure. Si le code est faux, les serrures refusent les cartes d'Odoo.
2. **Correspondance des chambres** : pour 2 ou 3 chambres, lisez une carte faite par le logiciel
   d'origine et vérifiez que le n° de serrure affiché est celui de la chambre dans Odoo
   (corrigez le « N° interne serrure » sinon).
3. **Premier essai sur UNE serrure** : encoder une carte dans Odoo, l'essayer sur la porte.
4. Le logiciel d'origine reste nécessaire pour : la **System Card**, la programmation des
   serrures, les cartes du personnel (Master, étage, urgence) et la collecte des historiques.
   Ne lancez pas le logiciel et le pont en même temps sur le même encodeur.

## 5. Détails techniques

- Fonctions de `proRFL.dll` utilisées et nombre de paramètres **vérifié dans le code binaire de la
  DLL** (stdcall) : `initializeUSB(1)`, `CloseUSB(1)`, `Buzzer(2)`, `GetDLLVersion(1)`,
  `ReadCard(2)`, `GuestCard(10)`, `CardErase(3)`, `GetCardTypeByCardDataStr(2)`,
  `GetGuestLockNoByCardDataStr(3)`, `GetGuestSTime/ETimeByCardDataStr(3)`.
- Dates au format `YYMMDDHHMM`, heure locale des serrures (format trouvé dans `CardLock.exe`).
- Numéro de serrure `%.2d%.2d%.2d` (bâtiment, étage, RomID), format trouvé dans `CardLock.exe`.
- La sémantique exacte de certains paramètres (`dai`, valeur de `fUSB`, codes retour) n'est pas
  documentée dans le dossier fourni : le pont détecte `fUSB` automatiquement et renvoie le code
  retour brut dans les messages d'erreur. Demandez la documentation du SDK proRFL au fournisseur
  si une carte encodée est refusée par la serrure.
- Sécurité : le pont s'authentifie avec une clé par encodeur (en-tête `X-Easloc-Key`) ; seule
  l'administration voit la clé. Régénérez-la si le PC est remplacé.
- Testé sur Odoo 19.0 (PostgreSQL 16) avec l'encodeur simulé : encodage, copie, lecture,
  effacement, refus d'une carte d'une autre chambre, délai dépassé, pont hors ligne, clé refusée.
  Tests unitaires : `odoo-bin -i easloc_lock --test-tags /easloc_lock`.
