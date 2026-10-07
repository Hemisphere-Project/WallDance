# Séance démo (reportée du 7 octobre) — nouvelle version WallDance

**Pour :** l'opérateur · **Version :** « WallDance DEV » (icône sur le bureau), mise à jour par Thomas
**But :** montrer au client **deux points stables et continus** (un par danseur) envoyés à TouchDesigner.

## Ce qui change depuis le 6 octobre

- **Trois étapes au lieu de six** : **1 Rig → 2 Mur vide → 3 Live**. Les anciennes étapes Profile, Aim, Calib et Verify
  ont disparu (leurs outils restent dans *Advanced → Expert tools*, inutiles en séance).
- **Mur vide** (nouveau) : une **prise de mur vide par éclairage** (jour, nuit, lumière du spectacle). CALIBRATE sur
  cette prise règle tout pour cet éclairage, y compris l'exposition de la caméra, et enregistre une **photo du mur
  vide**. Elle sert à tenir un danseur immobile et à refuser les fantômes.
- **Contrôle « personne sur le mur vide »** (nouveau, automatique) : à la fin de CALIBRATE, le logiciel vérifie que la
  détection ne voit **personne** sur le mur vide. Sinon il baisse l'éclaircissement de l'image jusqu'à ce que ce soit
  le cas (le 7 octobre, un réglage trop éclairci faisait voir un « danseur » près du matériel à gauche de la porte).
- **Taille des danseurs automatique** : plus besoin de calibrer avec des danseurs. Si la taille réglée ne correspond
  pas aux danseurs vus, le logiciel la corrige seul (message « Person height 45 -> 127 px »).
- **Ceinture IR** : tenue tant que la détection ou la photo du mur vide confirme qu'un danseur est là (avant : 8 s au
  plus).
- **Boule** : sa taille suit la distance du danseur (plus petite au fond, plus grande devant ; affichage seulement).
  Touche **C** ou case **C** dans *View* pour la masquer. K et T sont décochés par défaut.
- Le curseur « smooth L » a disparu de Live (il retardait TouchDesigner par rapport à l'aperçu).

## Les bonnes conditions (inchangé, le plus important)

1. **Projecteurs IR collés à l'objectif** (moins de 10 cm), orientés vers le mur ; **aucune lampe ni projecteur dans
   l'image**.
2. **Region of Interest = le mur seulement**, avec une petite marge pour les entrées et sorties.
3. **Personne près de la caméra dans l'image** pendant le spectacle.
4. **Mise au point sous IR**, cadrage et objectif du spectacle.
5. **Portable sur secteur, mode Performance, arrière surélevé**. Surveiller les FPS avec TouchDesigner.

## Avant de commencer

1. Lancer **« WallDance DEV »**, ouvrir le projet (`mur25m-ceinture-0610`). En haut de **1 Rig**, « Config version » :
   la plus récente (Thomas l'a préparée).
2. **1 Rig** : **Region of Interest = le mur seulement**. Mettre à jour la **Rig sheet** si quelque chose a changé.
   *Si la caméra ou le cadrage bouge, refaire l'étape 3.*
3. **2 Mur vide** — une fois par éclairage, **mur vide, personne dans l'image** :
   - **REC**, puis un **slot libre** : **15 s**, puis arrêter. Noter sur la feuille : slot, heure, éclairage.
   - Pour régler l'éclairage du moment : **lire la prise de mur vide de cet éclairage** (clic sur son slot), puis
     **CALIBRATE**. Attendre le résultat (20 à 30 s, la ligne bleue indique l'avancement), puis **Save**.
   - En direct, sans prise : mur vide, **CALIBRATE** (la caméra règle aussi son exposition).
   - Lire la fin du résultat : « Kept gamma … » = bon. **« YOLO sees a person on the EMPTY wall »** = un objet ressemble
     à une personne : l'enlever, ou peindre une case d'exclusion dessus (jamais sur le chemin des danseurs), puis
     refaire CALIBRATE. Sinon prévenir Thomas.
   - **Check readiness** : tout doit être vert ou orange.
   - Changement d'éclairage pendant la soirée : relire la prise de cet éclairage et refaire CALIBRATE (30 s).
4. **3 Live → bloc « Dancer IDs »** :

   | Réglage | Valeur de départ |
   |---|---|
   | Stable IDs (D1..Dn, hold through losses) | **coché** |
   | Max dancers | **2** |
   | Hold (s) | **2.0** |
   | Stability | **0.5** |
   | IR belt | **coché** si les danseurs portent la ceinture (projecteur collé à l'objectif) |
   | Smart hold / Ignore static figures / Box clamp | **cochés** |
   | Use empty wall | **coché** — la ligne dessous doit dire « empty wall: ready » |
   | Send /dancer/state | décoché (sauf si le patch TouchDesigner l'utilise) |

5. Cocher **« Allow remote control during RUN »** pour que Thomas puisse ajuster à distance. **Ctrl+S**.

## Ajuster sur place

| Ce qu'on voit | Quoi faire |
|---|---|
| Le point **tremble** | monter **Stability** (0.8 à 1.25) |
| Le point **traîne** derrière les mouvements rapides | baisser **Stability** (0.3 à 0.4) |
| TouchDesigner **perd la vidéo** quand un danseur est perdu un instant | monter **Hold** (3 à 5 s) |
| Un point **reste** après le départ du danseur | baisser **Hold** (1 à 1.5 s) |
| Un point apparaît sur un **objet fixe** | refaire **2 Mur vide → CALIBRATE** ; resserrer la Region of Interest |
| « empty wall: scene changed » ou « other camera crop » | lumière ou cadrage changé : refaire **CALIBRATE** sur le mur vide |
| Message « Person height … » | rien à faire (correction automatique) |
| Alerte « Person height calibration looks stale » | prévenir Thomas |

La **boule** de chaque danseur est pleine, de la couleur du danseur (D1 vert, D2 bleu) ; son **bord fin** dit l'état :
**vert** = suivi normal, **cyan** = ceinture, **blanc** = photo du mur vide, **orange** = maintenu (perdu depuis moins
de « Hold »). **Beaucoup d'orange = conditions à améliorer** : le noter avec l'heure.

## Les prises utiles

Pour chaque éclairage : **15 s de mur vide**. Puis **3 à 5 min « comme le spectacle » avec les deux danseurs** et
TouchDesigner en marche (le 6 octobre, une seule personne était disponible : les deux danseurs ensemble restent à
filmer). Noter sur la feuille : slot, heure, contenu, éclairage, projecteurs.

## Si quelque chose ne va pas

Revenir au **WallDance habituel** (icône habituelle), noter l'heure, et prévenir Thomas.
