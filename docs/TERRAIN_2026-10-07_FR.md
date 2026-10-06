# Séance du 7 octobre — réglages de la nouvelle version (WallDance DEV)

**Pour :** l'opérateur · **Version :** « WallDance DEV » (icône sur le bureau), mise à jour le matin par Thomas

## Ce qui change

- **Identifiants stables D1, D2** : chaque danseur garde le même numéro (1, 2…) pendant tout le spectacle. Si la
  détection le perd un instant, son point reste en place (« Hold ») au lieu de disparaître. TouchDesigner ne perd
  donc plus la vidéo à chaque décrochage.
- **Point lissé** : le centroïde ne tremble plus, et suit quand même les déplacements rapides (réglage « Stability »).
- **Ceinture IR** : quand la détection perd un danseur, la ceinture réfléchissante peut prendre le relais.
- **Plus léger** : l'ordinateur laisse plus de place à TouchDesigner.

## Avant de commencer

1. Portable sur secteur, **mode Performance**, arrière surélevé.
2. **Projecteurs IR collés à l'objectif**, orientés vers le mur.
3. Lancer **« WallDance DEV »**.

## Réglages (dans l'ordre)

1. **Phase 1 Rig** :
   - **Region of Interest = le mur seulement**, avec une petite marge pour les entrées et sorties. Moins on prend
     autour du mur, moins il y a de fantômes.
   - Mettre à jour la **Rig sheet** si quelque chose a changé.
2. **Phase 3 Aim** : mur **vide** → **CALIBRATE**.
3. **Exclusion Mask** : peindre les cases sur les objets fixes qui ressemblent à une personne ou qui brillent
   (taches, lampes, objets au bord du mur). Si un doute : prévenir Thomas, on peut le faire à distance.
4. **Phase 6 Live → bloc « Dancer IDs »** :

   | Réglage | Valeur de départ |
   |---|---|
   | Stable IDs (D1..Dn, hold through losses) | **coché** |
   | Max dancers | **2** (le nombre de danseurs sur le mur) |
   | Hold (s) | **2.0** |
   | Stability | **0.5** |
   | IR belt | **coché** |
   | Send /dancer/state | décoché (sauf si le patch TouchDesigner l'utilise) |

5. **Phase 6** : cocher **« Allow remote control during RUN »** pour que Thomas puisse ajuster à distance.
6. **Ctrl+S**.

## Ajuster sur place

| Ce qu'on voit | Quoi faire |
|---|---|
| Le point **tremble** | monter **Stability** (0.6 à 0.8) |
| Le point **traîne** derrière les mouvements rapides | baisser **Stability** (0.3 à 0.4) |
| TouchDesigner **perd la vidéo** quand un danseur est perdu un instant | monter **Hold** (3 s) |
| Un point **reste** quelque part après le départ du danseur | baisser **Hold** (1 à 1.5 s) |
| Un point apparaît sur un **objet fixe ou une tache** (fantôme) | peindre des cases d'**Exclusion Mask** dessus, ou resserrer la **Region of Interest** ; noter l'heure |
| **Plus de points que de danseurs** | vérifier **Max dancers** |

Dans l'aperçu, les danseurs émis s'affichent **D1, D2** : **vert** = suivi normal, **cyan** = suivi par la
ceinture, **orange** = maintenu (danseur perdu depuis moins de « Hold »).

## Pour le patch TouchDesigner

- Les identifiants envoyés sont maintenant **1 et 2**, stables (plus de grands numéros qui changent).
- `/walldance/count` donne la liste des identifiants présents.
- Un identifiant ne disparaît qu'après **Hold** secondes sans danseur.

## Si quelque chose ne va pas

Revenir au **WallDance habituel** (icône habituelle), noter l'heure, et prévenir Thomas.
