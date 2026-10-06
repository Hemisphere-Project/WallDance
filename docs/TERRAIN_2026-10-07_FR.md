# Séance du 7 octobre — réglages de la nouvelle version (WallDance DEV)

**Pour :** l'opérateur · **Version :** « WallDance DEV » (icône sur le bureau), mise à jour le matin par Thomas
**But de la semaine :** montrer au client **deux points stables et continus** (un par danseur) envoyés à TouchDesigner.

## Ce qui change

- **Identifiants stables D1, D2** : chaque danseur garde le même numéro pendant tout le spectacle. Si la détection le
  perd un instant, son point reste en place (« Hold ») au lieu de disparaître : TouchDesigner ne perd plus la vidéo à
  chaque décrochage.
- **Point lissé** : le centroïde ne tremble plus et suit quand même les déplacements rapides (réglage « Stability »).
- **Formes immobiles ignorées** (nouveau, coché par défaut) : une silhouette qui ne bouge jamais (un manteau près d'une
  porte, une affiche, une tache) ne peut plus garder un numéro dont un vrai danseur a besoin.
- **Ceinture IR** : quand la détection perd un danseur, la ceinture réfléchissante peut prendre le relais — seulement
  si le projecteur est collé à l'objectif (voir plus bas). Thomas dira ce matin s'il faut la garder cochée.
- **Plus léger** : l'ordinateur laisse plus de place à TouchDesigner.

## Les bonnes conditions (le plus important)

Le logiciel fait de son mieux dans toutes les situations, mais ces conditions font la différence entre un point
stable et un point qui saute. Elles se règlent une fois à l'installation.

1. **Projecteurs IR collés à l'objectif** (moins de 10 cm du centre de l'objectif), orientés vers le mur.
   - La ceinture renvoie la lumière **vers sa source** : avec un projecteur éloigné de la caméra, elle est invisible.
   - Un projecteur décalé fait aussi une **ombre du danseur sur le mur**, que la détection prend pour un deuxième
     danseur. Collé à l'objectif, l'ombre est cachée derrière le danseur.
   - **Aucun projecteur ni lampe dans l'image** (les projecteurs posés au sol dans le cadre trompent la ceinture).
2. **Region of Interest = le mur seulement**, avec une petite marge pour les entrées et sorties. Rien du sol, des
   portes, du matériel autour : moins il y a de décor dans le cadre, moins il y a de fantômes.
3. **Personne près de la caméra dans l'image** pendant le spectacle (technicien, opérateur) : une silhouette proche
   et très éclairée perturbe tout.
4. **Mise au point sous IR** (moniteur téléphone : bouton QR en phase 2), cadrage et objectif du spectacle.
5. **Portable sur secteur, mode Performance, arrière surélevé** (hier la carte graphique chauffait à 87 °C et
   ralentissait). TouchDesigner tourne sur le même portable : surveiller les FPS.

## Avant de commencer

1. Lancer **« WallDance DEV »**, ouvrir le projet de la veille (`mur25m-ceinture-0610`) ou un nouveau projet.
2. **Phase 1 Rig** : **Region of Interest = le mur seulement** (ci-dessus). Mettre à jour la **Rig sheet** si
   quelque chose a changé (distance, projecteurs, décalage du projecteur en cm).
3. **Phase 3 Aim** : mur **vide** → **CALIBRATE**. Puis **enregistrer 30 s de mur vide** dans un slot libre (sert à
   repérer les reflets fixes).
4. **Exclusion Mask** : peindre des cases **uniquement là où les danseurs ne vont jamais** (une lampe, un panneau, un
   objet au bord du mur). **Jamais sur le chemin des danseurs** : une case peinte rend le danseur invisible à cet
   endroit. En cas de doute, ne rien peindre et prévenir Thomas (il peut le faire à distance).
5. **Phase 6 Live → bloc « Dancer IDs »** :

   | Réglage | Valeur de départ |
   |---|---|
   | Stable IDs (D1..Dn, hold through losses) | **coché** |
   | Max dancers | **2** (le nombre de danseurs sur le mur) |
   | Hold (s) | **2.0** |
   | Stability | **0.5** |
   | IR belt | selon Thomas (coché si la ceinture est vue) |
   | Send /dancer/state | décoché (sauf si le patch TouchDesigner l'utilise) |
   | Smart hold (earned, none at exits) | **coché** |
   | Ignore static figures | **coché** |

6. **Phase 6** : cocher **« Allow remote control during RUN »** pour que Thomas puisse ajuster à distance
   (sensibilité de détection, mode de suivi : il les règle depuis son poste).
7. **Ctrl+S**.

## Ajuster sur place

| Ce qu'on voit | Quoi faire |
|---|---|
| Le point **tremble** | monter **Stability** (0.8 à 1.25 ; jusqu'à 3 = très lissé mais en retard sur les gestes rapides) |
| Le point **traîne** derrière les mouvements rapides | baisser **Stability** (0.3 à 0.4) |
| TouchDesigner **perd la vidéo** quand un danseur est perdu un instant | monter **Hold** (3 à 5 s, jusqu'à 10) en gardant **Smart hold** coché |
| Un point **reste** quelque part après le départ du danseur | baisser **Hold** (1 à 1.5 s) |
| Un point apparaît sur un **objet fixe** (fantôme) | resserrer la **Region of Interest** ; peindre une case seulement si aucun danseur n'y passe ; noter l'heure |
| Un point apparaît sur **l'ombre** d'un danseur | rapprocher le projecteur de l'objectif |
| **Plus de points que de danseurs** | vérifier **Max dancers** |

Dans l'aperçu, chaque point envoyé à TouchDesigner est une **grosse boule numérotée 1, 2**, exactement là où
TouchDesigner la reçoit : **pleine** = suivi, **transparente avec un anneau** = maintenue. Couleur : **vert** = suivi
normal, **cyan** = suivi par la ceinture, **orange** = maintenu (danseur perdu depuis moins de « Hold »).
**Beaucoup d'orange = conditions à améliorer** (lumière, cadrage, projecteur) : le noter avec l'heure.

**Smart hold** : seul un danseur bien suivi (squelette vu, un peu de mouvement) a droit au « Hold » complet ; un
point nouveau ou immobile est maintenu 1,5 s au plus ; un danseur qui sort par un bord de la Region of Interest
disparaît après 0,3 s. Un long « Hold » ne garde donc pas les fantômes en vie.

## Les prises utiles aujourd'hui

Si possible, pour chaque nouvelle installation : **30 s de mur vide**, puis **3 à 5 min « comme le spectacle »** avec
les deux danseurs et TouchDesigner en marche. Noter sur la feuille : slot, heure, contenu, projecteurs (où, combien).

## Pour le patch TouchDesigner

- Les identifiants envoyés sont **1 et 2**, stables (plus de grands numéros qui changent).
- `/walldance/count` donne la liste des identifiants présents.
- Un identifiant ne disparaît qu'après **Hold** secondes sans danseur (moins s'il sort par un bord ou vient
  d'apparaître : « Smart hold »). Pendant ce temps le point est **figé** ; s'il réapparaît loin, il **saute**.

## Si quelque chose ne va pas

Revenir au **WallDance habituel** (icône habituelle), noter l'heure, et prévenir Thomas.
