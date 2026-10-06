# Tournage du 6 octobre (soir) — protocole opérateur

**Pour :** l'opérateur WallDance · **Version :** « WallDance DEV » (icône sur le bureau) · **Durée :** ~1 h 15

**But des prises :** régler demain, sur place, un centroïde **continu** (pas de décrochage), **stable** (pas de
tremblement, mais qui suit les déplacements rapides), **sans fantômes**, en s'appuyant sur la **ceinture
réfléchissante IR**. Situation visée : **mur à ~25 m, 2 danseurs** accrochés au mur.

Les prises restent sur le portable : on les analyse **sur le portable** à distance (en 4G on ne rapatrie que des
résultats et des images). Rien à envoyer, sauf le nom du projet et la photo de la feuille papier.

---

## 0. Matériel : le point le plus important

1. **Projecteur IR collé à l'objectif**, au plus près (idéalement ≤ 5–10 cm du centre de l'objectif), orienté vers
   le mur. La ceinture renvoie la lumière **vers sa source** : avec un projecteur loin de la caméra (ex. les
   projecteurs au sol), la ceinture est **invisible** pour la caméra. Hier soir c'était le cas sur les slots 4, 8 et 9.
2. **Ceinture sur chaque danseur**, réfléchissante **devant et derrière** (les danseurs peuvent être de dos).
3. **Cadrage, objectif, mise au point = ceux du spectacle.** Tout le mur dans l'image, plus un peu de marge pour les
   entrées et sorties. Mise au point sous IR (moniteur téléphone : bouton QR en phase 2).
4. **Feuille papier** : pour chaque slot, noter le contenu de la prise, l'heure et tout changement (lumière,
   réglage, incident). Prendre une photo de la feuille à la fin.
5. Disque : ~1 Go par minute de prise (codec FFV1). Il y a 1,6 To libres, pas de souci.

## 1. Lancer la version de test

- Double-clic sur **« WallDance DEV »** sur le bureau (**pas** sur le WallDance habituel).
- Pour vérifier qu'on est bien dans la bonne version : la fenêtre noire s'appelle « WallDance DEV (version de test) »,
  et la phase **1 Rig** contient un bloc **« Rig sheet »**.
- Si elle ne démarre pas ou plante : relancer le WallDance habituel, faire quand même les prises, et le noter sur la
  feuille.

## 2. Nouveau projet

1. Barre du haut, liste **Project** → **« + new project »** → nom : `mur25m-ceinture-0610`.
2. **Phase 1 Rig** :
   - source caméra **IDS** ;
   - **Region of Interest** = tout le mur, plus la marge d'entrée et de sortie ;
   - **Input** : ne pas cocher Mirror ni Rotate (sauf si le spectacle l'exige).
3. Remplir la **Rig sheet** (phase 1). C'est ce qui permettra de comprendre les images demain :

   | Champ | Quoi mettre |
   |---|---|
   | Lens / Focal (mm) | modèle d'objectif / focale |
   | Aperture (f/) | ouverture (ex. 1.4, 2, 2.8) |
   | Focus ring (m) | distance lue sur la bague de mise au point |
   | Filter | filtre (ex. « BP850 ») |
   | IR light | modèle et puissance du projecteur IR (et nombre de projecteurs) |
   | IR offset (cm) | distance entre le projecteur et le centre de l'objectif |
   | Cam->stage (m) | distance caméra → mur (≈ 25) |
   | Cam height (m) | hauteur de la caméra |
   | IR markers | « ceinture réfléchissante avant + arrière » + type de tissu |
   | Notes | lumières du lieu, projecteurs au sol allumés ou non, etc. |

4. **Ctrl+S** (Save config).
5. **Phase 2 Profile** : profil **Show**.

## 3. Réglage de base (mur vide)

1. Mur **vide**, lumières comme pour le spectacle, projecteur IR collé à l'objectif.
2. **Phase 3 Aim** → **CALIBRATE** (scène vide). Attendre la fin.
3. Noter sur la feuille les valeurs **IDS Exposure (µs)** et **IDS Gain (dB)** (curseurs du panneau avancé caméra).
   C'est le **réglage A**.
4. **Ctrl+S**.

L'enregistrement note aussi automatiquement l'exposition et le gain chaque seconde. Changer un réglage pendant une
prise est donc sans risque : on saura quand.

## 4. Les prises (une prise par slot)

Pour chaque prise :

1. être en **RUN** ;
2. choisir le slot et lancer **REC** ;
3. faire l'action ;
4. attendre **3 s** ;
5. **STOP**.

Puis noter sur la feuille : slot, heure, contenu.

| Slot | Prise | Durée |
|---|---|---|
| **1** | **Mur vide**, lumières du spectacle, personne dans le cadre. Sert à repérer les reflets fixes (sources de fantômes). | 30 s |
| **2** | **Danseur A seul, immobile au mur**, 5 positions : haut gauche, haut droite, centre, bas gauche, bas droite. Environ 8 s à chaque position, **face caméra puis de dos**. | 90 s |
| **3** | **Danseur A seul, en mouvement** : déplacements lents, puis **rapides** (grands déplacements latéraux, descentes, changements de direction). | 60 s |
| **4** | **Deux danseurs éloignés** l'un de l'autre (≥ 2 m), mouvements typiques du spectacle. | 90 s |
| **5** | **Deux danseurs qui se rapprochent, se croisent, se superposent** (l'un devant l'autre), puis se séparent. Plusieurs fois. | 60 s |
| **6** | **Entrées et sorties** : un danseur entre dans le cadre, sort, revient ; puis les deux. | 60 s |
| **7** | **Échelle d'exposition** : danseur A immobile à la position **la plus éloignée ou la plus sombre**. 10 s au réglage A, puis **Exposure ÷ 2** pendant 10 s, puis **÷ 4** pendant 10 s, sans toucher au gain. Puis remettre le réglage A. | 40 s |
| **8** | **Comparaison lumière** : comme le slot 4 (deux danseurs), mais **projecteur IR à son emplacement habituel**, ou projecteurs au sol éteints si le projecteur est déjà collé. Noter lequel. | 60 s |
| **9** | **Prise longue « comme le spectacle »** : chorégraphie représentative avec les deux danseurs, lumières du spectacle, **TouchDesigner ouvert et en marche** si possible (pour mesurer la charge de l'ordinateur). | 3–5 min |

Si une prise est ratée : la refaire **dans le même slot**. L'ancienne est gardée dans l'historique, rien n'est perdu.

## 5. À la fin

1. **Ctrl+S**, puis fermer WallDance DEV normalement (fenêtre de l'appli).
2. **Ne rien supprimer ni déplacer** dans le dossier du projet.
3. Envoyer à Thomas : le **nom du projet** et la **photo de la feuille papier**.

## Si quelque chose ne va pas

- **La caméra n'est pas détectée** (badge CAM rouge) : débrancher et rebrancher la caméra, attendre 10 s, relancer
  l'appli.
- **L'appli plante** : la relancer ; au redémarrage elle affiche un message sur le plantage, c'est normal et utile.
  Noter l'heure sur la feuille.
- **Doute sur un réglage** : ne pas chercher à corriger. Noter ce qui a été fait, c'est suffisant pour l'analyse.
