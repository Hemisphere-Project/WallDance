# WallDance — guide de l'opérateur

<!--
GUIDE (hub walldance#t-062, Thomas's request of 26/11 14:50): the full operator manual behind the two-page sheet
docs/FICHE_TECHNICIEN_FR.md, in French, for Nico. Written from the rehearsal build as it stands on 26/11 (cordee-r2
@ 0912d44) and its docs; updated as the last steps land (see « État du guide » at the end).
Conventions, checked by tmp_analysis/r2-1126/gates/guide/run_gate.sh <code-root>:
- A code span = a string exactly as the app shows it. « … » inside a span stands for a value the app fills in.
  Every code span is backed, on the same source line, by a citation comment "src: <path>:<line> «snippet»": the
  snippet is a literal piece of the span, found at that line (paths relative to application/src unless they start
  with launcher/), and every piece of the span between « … » is a string literal of a cited file.
- Section 8 quotes every alert, toast, banner and readiness row the code can show (the gate lists them from the
  code and reports the coverage).
- Markers: [à confirmer avec Thomas …], [à compléter le 2/12 …], [étape en cours de développement — X4];
  [capture d'écran : …] where a screenshot comes in a later pass. No phone number, no address, no account name.
-->

**Pour Nico** — la répétition des 7 et 8/12 (avec un support à distance) et le premier spectacle du 12/12 (seul).

**Ce guide décrit la version de répétition telle qu'elle est le 26/11.** Elle bouge encore : la dernière page, « État du guide », dit quoi.

La fiche de deux pages (*FICHE_TECHNICIEN_FR*) résume ce guide. Ce guide, on le lit une fois avant ; on l'ouvre à la bonne page quand quelque chose arrive.

> **Comment lire ce guide**
>
> - `CALIBRATE` : un texte écrit **exactement** comme à l'écran (en anglais). La traduction suit, entre parenthèses. <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» -->
> - `Camera down for …s` : les « … » remplacent un nombre ou un nom que l'app écrit (une durée, une heure, des pixels). <!-- src: core/ops_monitor.py:802 «Camera down for » -->
> - **[à confirmer avec Thomas]**, **[à compléter le 2/12]** : un point pas encore réglé. Ne pas deviner : demander.
> - *[capture d'écran : …]* : une image de l'écran viendra là.

**Sommaire**

1. En deux minutes — p. 1
2. Le matériel et le branchement — p. 3
3. Lancer l'application — p. 5
4. L'écran, zone par zone — p. 7
5. La mise en place, pas à pas — p. 10
6. Pendant le spectacle — p. 15
7. Entre deux pièces — p. 18
8. Si quelque chose ne va pas — p. 20
9. Le support à distance — p. 27
10. Lexique — p. 28
11. L'essentiel — p. 31

## 1. En deux minutes

### Ce que fait WallDance

- Une caméra filme le mur **en infrarouge** (IR).
- Un projecteur IR éclaire le mur. Le public ne voit pas cette lumière.
- L'application WallDance repère chaque danseur sur l'image.
- Elle donne à chacun un numéro qui ne change pas (D1, D2…).
- Elle envoie sa position aux visuels (TouchDesigner), plusieurs fois par seconde.
- Ce message réseau s'appelle **OSC**.
- Les visuels suivent les danseurs grâce à ces positions.

### Le rôle de Nico

- **Installer** : cadrer, faire la photo du mur vide (`CALIBRATE`), vérifier. <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» -->
- **Lancer** : `RUN`. <!-- src: gui_builder.py:1045 «RUN» @«label="RUN"» -->
- **Surveiller une seule chose** : la ligne de santé, la bande de couleur sous l'image.
- **Réagir** : chaque message de l'app dit la seule chose à faire. Ce guide la redit en français (§6 et §8).
- **Noter l'heure** de chaque souci : les journaux de l'app sont relus après la séance.

### La mise en place, sur une page

| | Étape | Où | Ce qu'on fait | C'est bon quand |
|---|---|---|---|---|
| ① | Le cadre | `1 Rig` | tracer la zone des danseurs ; régler le nombre de danseurs | la zone entoure le mur dansé <!-- src: gui_builder.py:641 «1 Rig» --> |
| ② | Le mur vide | `2 Calibrate` | personne dans l'image, `CALIBRATE`, puis `Save to project` | la carte `Scene Calibration` s'est affichée et a été enregistrée <!-- src: gui_builder.py:642 «2 Calibrate» ; gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» ; gui.py:2949 «Save to project» ; gui.py:2929 «Scene Calibration» --> |
| ③ | Un danseur, 10 s | `3 Live` | en `RUN`, un danseur debout 10 s au fond du mur | la ligne `dancer size` est `OK` <!-- src: gui_builder.py:643 «3 Live» ; gui_builder.py:1045 «RUN» @«label="RUN"» ; core/ops_monitor.py:326 «dancer size» ; core/ops_monitor.py:55 «OK» @«"OK"» --> |
| ④ | Vérifier | `2 Calibrate` | lire le bilan (`Check readiness`) | le bilan dit `READY` <!-- src: gui_builder.py:642 «2 Calibrate» ; gui_builder.py:876 «Check readiness» ; core/ops_monitor.py:59 «READY» @«"READY"» --> |
| ⑤ | Le spectacle | `3 Live` | `RUN`, puis ne plus toucher | la ligne de santé est verte (`OK`) <!-- src: gui_builder.py:643 «3 Live» ; gui_builder.py:1045 «RUN» @«label="RUN"» ; core/ops_monitor.py:55 «OK» @«"OK"» --> |

*[capture d'écran : la fenêtre WallDance en RUN, la ligne de santé verte]*

### Les quatre règles d'or

1. **Une seule app caméra à la fois** : jamais deux WallDance ouverts.
2. **Pendant une pièce, ne toucher que `Max dancers`** (le nombre de danseurs). Tout le reste attend la pause. <!-- src: gui_builder.py:1128 «Max dancers» -->
3. **Après chaque changement : `Ctrl+S`** (enregistrer). Sinon une relance l'oublie. <!-- src: gui_builder.py:394 «Ctrl+S» @«Save config (Ctrl+S)» -->
4. **Jamais un emplacement de prise (1 à 9) cliqué en `RUN`** : les visuels suivraient l'enregistrement, plus les danseurs (§6). <!-- src: gui_builder.py:1045 «RUN» @«label="RUN"» -->

<div style="break-before: page"></div>

## 2. Le matériel et le branchement

Ce que le code et les notes de Thomas établissent. Le reste est marqué **[à confirmer avec Thomas]**.

### Le portable

- Un portable Windows avec une carte graphique NVIDIA. Il fait tourner WallDance.
- TouchDesigner (les visuels) tourne aussi sur ce portable **[à confirmer avec Thomas : le même portable le 7/12 et le 12/12]**.
- **Toujours sur secteur.** Sur batterie, l'image ralentit.
- **Arrière surélevé, aérations dégagées.** La carte graphique chauffe ; l'app alerte à 85 °C.
- Ne pas fermer le capot ni toucher au bouton d'alimentation pendant que l'app tourne : le portable se mettrait en veille.
- Ne pas ouvrir d'autre logiciel de caméra (par exemple IDS peak Cockpit) pendant que WallDance tourne : une caméra ne sert qu'à un logiciel.

### La caméra

- Une caméra industrielle IDS, branchée en **USB3**.
- Sur les prises du 08/10 : objectif 6 mm, filtre infrarouge derrière l'objectif, caméra montée à la verticale **[à confirmer avec Thomas : même caméra, même objectif, même montage]**.
- Montée à la verticale, l'image est tournée par l'app (`Rotate`, dans `1 Rig`). <!-- src: gui_builder.py:1796 «Rotate» @«"Rotate"» ; gui_builder.py:641 «1 Rig» -->
- La mise au point se fait à la main, sur la bague de l'objectif, **sous la lumière IR**.
- Câble USB3 enfoncé à fond des deux côtés, alimentation de la caméra branchée **[à confirmer avec Thomas : la caméra a-t-elle une alimentation à part]**.
- **Brancher la caméra avant de lancer l'app.** Sans elle, l'app prend une autre caméra (la webcam du portable) et écrit `[CV]` en haut, au lieu de `[IDS]`. <!-- src: gui.py:1865 «[CV]» @«"[CV]"» ; gui.py:1862 «[IDS]» @«"[IDS]"» -->
- La caméra peut se figer environ 1,7 s (« stall »). Quelques fois par minute, c'est connu. L'app les compte (`camera stalls`). <!-- src: core/ops_monitor.py:987 «camera stalls» -->

### Le projecteur IR (« IR flood »)

- Allumé **avant** `CALIBRATE`. <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» -->
- Tout près de l'objectif (moins de 10 cm), tourné vers le mur.
- Aucune lampe, aucun projecteur visible dans l'image.
- `CALIBRATE` juge la lumière et peut demander de déplacer le projecteur (§5.2). <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» -->

### Les ceintures IR des danseurs

- Si les danseurs portent la ceinture réfléchissante : cocher `IR belt` (dans `3 Live`). Elle aide à garder un danseur quand l'app perd sa silhouette. <!-- src: gui_builder.py:1175 «IR belt» @«label="IR belt"» ; gui_builder.py:643 «3 Live» -->
- **[à confirmer avec Thomas : ceintures portées le 7/12 et le 12/12]**

### Le réseau

- **7–8/12** : le portable doit être en ligne (4G) pour le support à distance (§9) **[à confirmer avec Thomas : la 4G du portable ou un routeur 4G]**.
- L'adresse où l'app envoie les positions est dans le projet (`Advanced` → `OSC` → `Target Address`). Ne pas la changer. <!-- src: gui_builder.py:685 «Advanced» @«label="Advanced"» ; gui_builder.py:1569 «OSC» @«label="OSC"» ; gui_builder.py:1581 «Target Address» -->

### Avant de lancer l'app, dans l'ordre

1. Portable sur secteur, arrière surélevé.
2. Caméra fixée et branchée en USB3.
3. Projecteur IR allumé, tourné vers le mur.
4. TouchDesigner lancé avec son patch. Sinon le bilan dit `nothing listening on …` (personne n'écoute). <!-- src: core/ops_monitor.py:233 «nothing listening on » -->
5. 7–8/12 : la connexion 4G **[à confirmer avec Thomas]**.
6. Puis WallDance (§3).

*[capture d'écran : photo du montage, la caméra et le projecteur IR en place]*

<div style="break-before: page"></div>

## 3. Lancer l'application

### Deux versions sur le portable

| Version | À quoi elle sert | Comment la lancer |
|---|---|---|
| La **version de répétition** | celle que décrit ce guide ; la répétition des 7–8/12 | **[à compléter le 2/12 : l'icône]** |
| La **version du 8 octobre** | la version de secours | l'icône `WallDance`, qui ouvre le lanceur `WallDance Launcher` <!-- src: launcher/install_manager.py:27 «WallDance» ; launcher/gui.py:75 «WallDance Launcher» --> |

- Le 12/12 : quelle version on lance, et laquelle sert de secours **[à confirmer avec Thomas]**.
- **Une seule ouverte à la fois.** Fermer l'une avant d'ouvrir l'autre.

### Lancer la version de répétition

1. Double-cliquer **[à compléter le 2/12 : l'icône de la version de répétition ; aujourd'hui une icône « WallDance DEV » existe sur le bureau]**.
2. La fenêtre `WallDance - Select Project` s'ouvre (choisir un projet). Les projets sont rangés du plus récent au plus ancien. <!-- src: gui.py:2347 «WallDance - Select Project» -->
3. Cliquer le projet **[à compléter le 2/12 : le projet à ouvrir]**.
4. Cliquer `Launch` (lancer), ou la touche Entrée (`Enter = launch highlighted`). <!-- src: gui.py:2359 «Launch» @«label="Launch"» ; gui.py:2352 «Enter = launch highlighted» -->
5. Jamais `Delete` (supprimer), `Rename` (renommer) ni `New` (nouveau). <!-- src: gui.py:2363 «Delete» @«label="Delete"» ; gui.py:2361 «Rename» @«label="Rename"» ; gui.py:2366 «New» @«label="New"» -->
6. Si une fenêtre `Model Loading` (chargement du modèle) s'affiche : attendre qu'elle se ferme. <!-- src: gui.py:2696 «Model Loading» -->
7. L'app démarre **en `RUN`** : le badge en haut dit `RUN`. <!-- src: gui_builder.py:1045 «RUN» @«label="RUN"» ; gui_builder.py:1045 «RUN» @«label="RUN"» -->
8. Après quelques secondes d'image, le bilan se lance seul. Un message `Go-Live check: …` s'affiche en haut à gauche de l'image (§5.4). <!-- src: core/ops_monitor.py:127 «Go-Live check: » -->

*[capture d'écran : la fenêtre WallDance - Select Project, un projet surligné]*

### Au démarrage, on peut voir

- `Previous session crashed (…)` en rouge, 15 s : la séance d'avant s'est arrêtée anormalement. **Noter l'heure, continuer.** <!-- src: services/crash_marker.py:174 «Previous session crashed (» -->
- La fenêtre `TensorRT Engine` (`No TensorRT engine found for …`) : voir §8, ligne « moteur ». <!-- src: gui.py:2776 «TensorRT Engine» ; gui.py:2789 «No TensorRT engine found for » -->
- `/!\ Running on CPU - no GPU acceleration` : voir §8. <!-- src: runtime/main_loop.py:351 «Running on CPU - no GPU acceleration» -->
- `[CV]` en haut au lieu de `[IDS]` : la caméra IDS n'est pas trouvée. Fermer l'app, brancher la caméra, relancer. <!-- src: gui.py:1865 «[CV]» @«"[CV]"» ; gui.py:1862 «[IDS]» @«"[IDS]"» -->

### Lancer la version du 8 octobre (secours)

1. Fermer la version de répétition (voir « Fermer l'application » ci-dessous).
2. Double-cliquer l'icône `WallDance` du bureau. La fenêtre `WallDance Launcher` s'ouvre. <!-- src: launcher/install_manager.py:27 «WallDance» ; launcher/gui.py:75 «WallDance Launcher» -->
3. Si la question `Update Available` (une mise à jour existe) s'affiche : répondre **non**, sauf accord de Thomas. <!-- src: launcher/gui.py:410 «Update Available» -->
4. Si `Local Version Differs` (la version locale est différente) s'affiche : `Keep local version` (garder la version locale). <!-- src: launcher/gui.py:196 «Local Version Differs» ; launcher/gui.py:204 «Keep local version» -->
5. Le lanceur démarre l'app **[à confirmer avec Thomas : par le bouton `Start` ou tout seul]**. <!-- src: launcher/gui.py:104 «Start» @«text="Start"» -->
6. Même fenêtre de choix du projet : **[à compléter le 2/12 : le projet de la version du 8 octobre]**.
7. Mêmes étapes ① ② ④ ⑤.
8. **Laisser la fenêtre du lanceur ouverte** pendant le spectacle : un ancien lanceur fermait l'app avec sa fenêtre.

Cette version est plus ancienne que ce guide : pas de ligne de santé, pas de bande `MESSAGE DU SUPPORT`, le bilan ne se lance pas seul. Ce que montre son écran **[à confirmer avec Thomas]**. <!-- src: gui_builder.py:520 «MESSAGE DU SUPPORT» -->

### Fermer l'application

1. `3 Live` → `STANDBY` : plus rien n'est envoyé aux visuels. <!-- src: gui_builder.py:643 «3 Live» ; gui_builder.py:1034 «STANDBY» @«label="STANDBY"» -->
2. `Ctrl+S` si un réglage a changé. <!-- src: gui_builder.py:394 «Ctrl+S» @«Save config (Ctrl+S)» -->
3. Fermer la fenêtre par sa croix **[à confirmer avec Thomas]**. L'app garde une dernière fois la photo du mur vide en partant.

<div style="break-before: page"></div>

## 4. L'écran, zone par zone

| Zone | Où | Ce qu'on y voit |
|---|---|---|
| 1. Barre du haut | tout en haut | le projet, l'état (`RUN` / `STANDBY`), la caméra, l'envoi, la vitesse <!-- src: gui_builder.py:1045 «RUN» @«label="RUN"» ; gui_builder.py:1034 «STANDBY» @«label="STANDBY"» --> |
| 2. Rail des phases | sous la barre | `PHASE`, `1 Rig`, `2 Calibrate`, `3 Live` ; `Advanced` à droite <!-- src: gui_builder.py:666 «PHASE» @«"PHASE"» ; gui_builder.py:641 «1 Rig» ; gui_builder.py:642 «2 Calibrate» ; gui_builder.py:643 «3 Live» ; gui_builder.py:685 «Advanced» @«label="Advanced"» --> |
| 3. L'image | au milieu, à gauche | la caméra, les danseurs, les points envoyés ; les bandeaux au-dessus |
| 4. Le panneau | au milieu, à droite | les réglages de la phase choisie |
| 5. La ligne de santé | sous l'image, toute la largeur | une bande de couleur : la santé du spectacle |
| 6. Les alertes | sous la ligne de santé | `Alerts:` puis `(none)` (aucune) ou les alertes qui durent <!-- src: gui_builder.py:1274 «Alerts:» @«"Alerts:"» ; gui_builder.py:1275 «(none)» @«"(none)"» --> |
| 7. Les enregistrements | sous les alertes | `Recordings`, `LIVE`, `REC`, les emplacements 1 à 9 <!-- src: gui_builder.py:1283 «Recordings» @«"Recordings"» ; gui_builder.py:1320 «LIVE» @«label="LIVE"» ; gui_builder.py:1327 «REC» @«label="REC"» --> |
| 8. Barre du bas | tout en bas | des chiffres de vitesse, rien à faire |

*[capture d'écran : la fenêtre entière, les huit zones numérotées]*

### 4.1 La barre du haut

- `Project:` (projet) et son nom. L'icône disquette à côté : `Save config (Ctrl+S)` (enregistrer). <!-- src: gui_builder.py:375 «Project:» @«"Project:"» ; gui_builder.py:394 «Ctrl+S» @«Save config (Ctrl+S)» -->
- L'état, en gros : `RUN` (vert) ou `STANDBY` (jaune). <!-- src: gui_builder.py:63 «RUN» @«"RUN"» ; gui_builder.py:62 «STANDBY» @«"STANDBY"» -->
  - `RUN` = l'app détecte les danseurs et envoie leurs positions. <!-- src: gui_builder.py:411 «RUN» @«RUN: Full YOLO + OSC output» -->
  - `STANDBY` = l'image seule ; rien n'est détecté, rien n'est envoyé. <!-- src: gui_builder.py:411 «STANDBY» @«STANDBY: Preview only, no YOLO» -->
- `CAM:` puis `ON` (vert, la caméra marche) ou `OFF` (rouge, pas de caméra). <!-- src: gui_builder.py:413 «CAM:» @«"CAM:"» ; gui.py:1855 «ON» @«"ON" if camera_running» ; gui_builder.py:414 «OFF» @«"OFF"» -->
  - `[IDS]` = la caméra du spectacle. `[CV]` = une autre caméra : **pas la bonne**. <!-- src: gui.py:1862 «[IDS]» @«"[IDS]"» ; gui.py:1865 «[CV]» @«"[CV]"» -->
  - `RECONNECTING` (orange) = la caméra se reconnecte. <!-- src: gui_builder.py:423 «RECONNECTING» @«"RECONNECTING"» -->
- `OSC:` puis `ON` / `OFF` : l'envoi des positions aux visuels. <!-- src: gui_builder.py:429 «OSC:» @«"OSC:"» ; gui.py:1882 «ON» @«"ON" if osc_enabled» ; gui_builder.py:414 «OFF» @«"OFF"» -->
- `REMOTE` (orange) : une session à distance parle à l'app. `REMOTE*` : elle a aussi le droit d'agir en RUN (§9). <!-- src: gui_builder.py:433 «REMOTE» @«"REMOTE"» ; gui.py:971 «REMOTE*» @«"REMOTE*"» -->
- Le nom du modèle de détection, puis `[TRT]` (le moteur rapide : normal) ou `[PT]` (le moteur lent : anormal, §8). <!-- src: gui.py:2078 «[TRT]» @«"[TRT]"» ; gui_builder.py:442 «[PT]» @«"[PT]"» -->
- `[CPU FALLBACK]` en rouge : la carte graphique n'est pas utilisée (§8). <!-- src: gui_builder.py:446 «[CPU FALLBACK]» -->
- `FPS:` : images traitées par seconde. Vert à 19 et plus, rouge à 10 et moins. <!-- src: gui_builder.py:451 «FPS:» @«"FPS:"» -->
- `GPU:` et `VRAM:` : la charge et la mémoire de la carte graphique. Rien à faire. <!-- src: gui_builder.py:454 «GPU:» @«"GPU:"» ; gui_builder.py:457 «VRAM:» @«"VRAM:"» -->

### 4.2 Le rail des phases

- `PHASE` puis trois boutons : `1 Rig` (le montage), `2 Calibrate` (le mur vide), `3 Live` (le direct). <!-- src: gui_builder.py:666 «PHASE» @«"PHASE"» ; gui_builder.py:641 «1 Rig» ; gui_builder.py:642 «2 Calibrate» ; gui_builder.py:643 «3 Live» -->
- Un clic montre le panneau de cette phase à droite. Le bouton choisi devient vert.
- Ouvrir `2 Calibrate` relance le bilan tout seul. <!-- src: gui_builder.py:642 «2 Calibrate» -->
- `Advanced` (avancé), à droite : une fenêtre de réglages experts, `Advanced  (numeric knobs)`. **Ne pas l'ouvrir**, sauf quand §8 le dit. <!-- src: gui_builder.py:685 «Advanced» @«label="Advanced"» ; gui_builder.py:1290 «Advanced  (numeric knobs)» -->

### 4.3 L'image

- L'image de la caméra, déjà tournée.
- Le cadre de la zone surveillée (la `Region of Interest`). Les cases masquées sont assombries. <!-- src: gui_builder.py:1941 «Region of Interest» @«label="Region of Interest"» -->
- Sur chaque danseur : son squelette, sa boîte, son numéro, et **une boule** : le point envoyé aux visuels.
- Le bord de la boule dit l'état du danseur :
  - **vert** : suivi normal ;
  - **cyan** : tenu par la ceinture IR ;
  - **blanc** : tenu par la photo du mur vide ;
  - **orange** : pas vu à cet instant, tenu à sa dernière place quelques secondes.
- Beaucoup d'orange = de mauvaises conditions (lumière, taille). Noter l'heure.

**Les bandeaux au-dessus de l'image** (ils apparaissent seulement quand il le faut) :

- Rouge : `TensorRT OFF - running PyTorch (…)` et un bouton `Rebuild TRT`. Le moteur rapide ne marche pas (§8). **Ne jamais cliquer `Rebuild TRT`** pendant un spectacle. <!-- src: runtime/model_controller.py:335 «TensorRT OFF - running PyTorch (» ; gui_builder.py:481 «Rebuild TRT» -->
- Rouge : `LECTURE - pas en direct` `- TouchDesigner recoit un enregistrement, pas la camera` et un bouton `LIVE (camera)` (§6.3). <!-- src: gui_builder.py:499 «LECTURE - pas en direct» ; gui_builder.py:510 «TouchDesigner recoit un enregistrement, pas la camera» ; gui_builder.py:512 «LIVE (camera)» -->
- Jaune : `MESSAGE DU SUPPORT` et un bouton `J'ai lu` (§9). <!-- src: gui_builder.py:520 «MESSAGE DU SUPPORT» ; gui_builder.py:521 «J'ai lu» -->

**Les messages en haut à gauche de l'image** (« toasts ») : ils s'effacent seuls après quelques secondes. Un seul à la fois. Ceux qui commencent par `/!\ ` sont des alertes, en rouge, 8 s. <!-- src: ui/adapter.py:368 «/!» @«{e.message}» -->

### 4.4 Le panneau de droite

- `1 Rig` montre `1 - Rig & Frame` (montage et cadre) : §5.1. <!-- src: gui_builder.py:641 «1 Rig» ; gui_builder.py:714 «1 - Rig & Frame» -->
- `2 Calibrate` montre `2 - Calibrate (empty wall)` (mur vide) : §5.2 et §5.4. <!-- src: gui_builder.py:642 «2 Calibrate» ; gui_builder.py:834 «2 - Calibrate (empty wall)» -->
- `3 Live` montre `6 - Go Live` (le direct). Le titre dit « 6 », le bouton dit « 3 » : c'est le même panneau. <!-- src: gui_builder.py:643 «3 Live» ; gui_builder.py:1026 «6 - Go Live» -->

*[capture d'écran : le panneau 3 Live en entier]*

### 4.5 La ligne de santé et les alertes

- La **ligne de santé** : une bande de couleur, toute la largeur de la fenêtre, sous l'image. Elle change chaque seconde. Tout est au §6.
- Dessous, `Alerts:` garde les alertes qui durent (la lumière, la photo du mur vide) jusqu'à ce qu'on les règle. `(none)` = aucune. <!-- src: gui_builder.py:1274 «Alerts:» @«"Alerts:"» ; gui_builder.py:1275 «(none)» @«"(none)"» -->

### 4.6 La barre des enregistrements

- `Recordings` (enregistrements), puis : <!-- src: gui_builder.py:1283 «Recordings» @«"Recordings"» -->
- `LIVE` : revenir à la caméra en direct. <!-- src: gui_builder.py:1320 «LIVE» @«label="LIVE"» -->
- `REC` : enregistrer une prise (§5.2). <!-- src: gui_builder.py:1327 «REC» @«label="REC"» -->
- 1 à 9 : les emplacements des prises. Gris = vide, bleu = une prise, bleu vif = en lecture, rouge = en enregistrement.
- **Un clic sur un emplacement lance la lecture de sa prise.** En `RUN`, les visuels suivent alors la prise, plus les danseurs. <!-- src: gui_builder.py:1045 «RUN» @«label="RUN"» -->
- `IMPORT` (importer une vidéo) : outil expert, ne pas utiliser. <!-- src: gui_builder.py:1347 «IMPORT» @«label="IMPORT"» -->
- L'état, à droite : `LIVE` (direct), `REC ARMED - Select slot` (choisir un emplacement), `SLOT …` (l'emplacement en cours). <!-- src: gui.py:1259 «LIVE» @«"rec_status_text", "LIVE"» ; gui.py:1262 «REC ARMED - Select slot» ; gui.py:1265 «SLOT» @«f"SLOT {current_slot}"» -->
- En lecture : vitesse, pause, image par image, et `ISSUE` (signaler un problème, outil expert). <!-- src: gui_builder.py:1397 «ISSUE» @«label="ISSUE"» -->

### 4.7 La barre du bas

- `Dancers:` (danseurs suivis), `In:` (taille de l'image), `Bright:` (luminosité), `FPS:`, puis des temps de calcul (`Enh:`, `YOLO:`, `Trk:`, `Tot:`). <!-- src: gui_builder.py:565 «Dancers:» @«"Dancers:"» ; gui_builder.py:568 «In:» @«"In:"» ; gui_builder.py:574 «Bright:» @«"Bright:"» ; gui_builder.py:451 «FPS:» @«"FPS:"» ; gui_builder.py:582 «Enh:» @«"Enh:"» ; gui_builder.py:585 «YOLO:» @«"YOLO:"» ; gui_builder.py:588 «Trk:» @«"Trk:"» ; gui_builder.py:594 «Tot:» @«"Tot:"» -->
- Rien à faire. Utile seulement au téléphone avec le support.

### 4.8 Le clavier

| Touche | Effet | Pendant une pièce |
|---|---|---|
| `Ctrl+S` | enregistre le projet <!-- src: gui_builder.py:394 «Ctrl+S» @«Save config (Ctrl+S)» --> | permis |
| S, K, B, T, I, C | affichent ou cachent squelette, points, boîtes, traînées, numéros, boule (`View:`) <!-- src: gui_builder.py:1497 «View:» @«"View:"» --> | sans effet sur le spectacle ; éviter |
| P | fige l'image **à l'écran** (le suivi continue) ; P à nouveau la rend | éviter |
| F8 | ouvre le signalement d'un problème (outil expert) | non |
| `Ctrl+Shift+E` | le mode expert, `Expert mode ON` / `Expert mode OFF` (§8) <!-- src: core/ops_monitor.py:402 «Ctrl+Shift+E» ; gui.py:528 «Expert mode ON» @«"Expert mode ON"» ; gui.py:528 «Expert mode OFF» @«"Expert mode OFF"» --> | non |

Pendant qu'on écrit dans une case (un nom, la fiche du montage), les lettres seules ne déclenchent rien.

<div style="break-before: page"></div>

## 5. La mise en place, pas à pas

### 5.1 Le cadre — `1 Rig` <!-- src: gui_builder.py:641 «1 Rig» -->

**Faire :**

1. Cliquer `1 Rig`. Le panneau `1 - Rig & Frame` s'ouvre. <!-- src: gui_builder.py:641 «1 Rig» ; gui_builder.py:714 «1 - Rig & Frame» -->
2. Bloc `Input` (l'entrée) : `Camera` = la caméra IDS. <!-- src: gui_builder.py:1757 «Input» @«label="Input"» ; gui_builder.py:1758 «Camera» @«"Camera"» -->
3. `Rotate` (tourner) = la rotation du montage (caméra verticale : 90° **[à confirmer avec Thomas]**). `Mirror` (miroir) : décoché, sauf consigne. <!-- src: gui_builder.py:1796 «Rotate» @«"Rotate"» ; gui_builder.py:1790 «Mirror» @«label="Mirror"» -->
4. Mise au point : tourner la bague de l'objectif, sous l'IR, jusqu'à une image nette.
   - Aide : l'icône QR, `Phone monitor: show a QR code to open the web UI`. La fenêtre `Phone Monitor` dit `Scan with a phone on the same Wi-Fi / hotspot:`. On voit alors l'image sur un téléphone, près de la caméra. <!-- src: gui_builder.py:810 «Phone monitor: show a QR code to open the web UI» ; gui.py:2991 «Phone Monitor» @«"Phone Monitor"» ; gui.py:3002 «Scan with a phone on the same Wi-Fi / hotspot:» -->
5. Bloc `Region of Interest` (la zone surveillée) : cocher `Enable ROI`. <!-- src: gui_builder.py:1941 «Region of Interest» @«label="Region of Interest"» ; gui_builder.py:1944 «Enable ROI» -->
6. Double-cliquer l'image. Message : `ROI edit mode: drag on preview` (tracer sur l'image). <!-- src: ui/roi_mask_editor.py:674 «ROI edit mode: drag on preview» -->
7. Tirer un rectangle autour du mur où l'on danse, avec une petite marge pour les entrées et sorties.
8. Double-cliquer à nouveau : `ROI edit mode: off` (fini). <!-- src: ui/roi_mask_editor.py:674 «ROI edit mode: off» -->
9. Bloc `Exclusion Mask` (les cases ignorées) : seulement pour un endroit où personne ne danse jamais et où l'app croit voir quelqu'un (une affiche, un reflet). <!-- src: gui_builder.py:1912 «Exclusion Mask» @«label="Exclusion Mask"» -->
   - `Edit`, puis cliquer ou glisser sur l'image : les cases deviennent rouges. `Edit` à nouveau pour finir. <!-- src: gui_builder.py:1919 «Edit» @«label="Edit"» ; gui_builder.py:1919 «Edit» @«label="Edit"» -->
   - **Dans le doute : ne rien masquer.** `Clear all` efface tout le masque. <!-- src: gui_builder.py:1925 «Clear all» @«label="Clear all"» -->
10. Le nombre de danseurs : `3 Live` → `Max dancers` = le nombre de danseurs sur le mur, au plus (1 à 8). <!-- src: gui_builder.py:643 «3 Live» ; gui_builder.py:1128 «Max dancers» -->
11. `Rig sheet` (la fiche du montage) : facultatif, à remplir si Thomas le demande. <!-- src: gui_builder.py:752 «Rig sheet» @«label="Rig sheet"» -->
12. `Ctrl+S`. <!-- src: gui_builder.py:394 «Ctrl+S» @«Save config (Ctrl+S)» -->

**Plus la zone est serrée sur le mur dansé, plus les danseurs sont grands pour la détection.** C'est le premier réglage de la qualité.

**C'est bon quand :**

- le rectangle entoure le mur dansé, et rien d'autre de large ;
- sous `Region of Interest`, la ligne donne la zone (position et taille) ; <!-- src: gui_builder.py:1941 «Region of Interest» @«label="Region of Interest"» -->
- `Masked:` donne le nombre de cases masquées (souvent `0 cell(s)`). <!-- src: gui_builder.py:1915 «Masked:» @«"Masked:"» ; gui_builder.py:1916 «0 cell(s)» @«"0 cell(s)"» -->

**Si ce n'est pas bon :** le bilan (§5.4) a une ligne `ROI` ; ses messages sont au §8. <!-- src: core/ops_monitor.py:446 «ROI» @«CheckResult("ROI"» -->

**Si la caméra bouge après cette étape :** refaire ② à ④.

`Reset` (sous `Enable ROI`) efface la zone : ne pas cliquer sans raison. <!-- src: gui_builder.py:1950 «Reset» @«label="Reset"» ; gui_builder.py:1944 «Enable ROI» -->

*[capture d'écran : le panneau 1 Rig, la zone tracée sur l'image]*

### 5.2 Le mur vide et CALIBRATE — `2 Calibrate` <!-- src: gui_builder.py:642 «2 Calibrate» -->

**Pourquoi :** `CALIBRATE` règle l'exposition de la caméra et l'amélioration de l'image, puis prend la **photo du mur vide**. Cette photo sert à repérer les danseurs et à refuser les « fantômes » (une forme fixe prise pour quelqu'un). <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» -->

**Quand :** une fois par éclairage (jour, nuit, lumière du spectacle), et après tout changement de caméra, de zone ou de projecteur.

**Avant :** personne sur le mur, personne dans l'image, même au bord. Projecteur IR allumé. `CAM:` `ON`. <!-- src: gui_builder.py:413 «CAM:» @«"CAM:"» ; gui.py:1855 «ON» @«"ON" if camera_running» -->

**Faire :**

1. Cliquer `2 Calibrate`. Le panneau `2 - Calibrate (empty wall)` s'ouvre. <!-- src: gui_builder.py:642 «2 Calibrate» ; gui_builder.py:834 «2 - Calibrate (empty wall)» -->
2. Vider le mur.
3. Appuyer **une fois** sur `CALIBRATE`. Le bouton devient `CANCEL` (annuler) pendant le calcul. <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» ; gui.py:854 «CANCEL» @«"CANCEL"» -->
4. Lire la ligne bleue sous le bouton. Elle passe par :
   - `Checking that the wall is empty...` (vérifie que le mur est vide) ; <!-- src: runtime/calibration_flows.py:622 «Checking that the wall is empty...» -->
   - `Calibrating exposure...` (règle l'exposition, avec la caméra en direct seulement) ; <!-- src: runtime/calibration_flows.py:283 «Calibrating exposure...» -->
   - `Calibrating …%` (mesure la scène) ; <!-- src: runtime/calibration_flows.py:426 «Calibrating» @«f"Calibrating {int(cal.progress() * 100)}%"» -->
   - `Checking the empty wall …%` (vérifie que l'app ne voit personne sur le mur vide). <!-- src: runtime/calibration_flows.py:536 «Checking the empty wall » -->
5. Attendre 20 à 30 secondes. Ne rien toucher.
6. La carte `Scene Calibration` s'ouvre : `Measured and applied to this session:` (mesuré et appliqué à cette séance). <!-- src: gui.py:2929 «Scene Calibration» ; gui.py:2942 «Measured and applied to this session:» -->
7. Lire la carte (plus bas), puis cliquer **`Save to project`** (enregistrer dans le projet). **Toujours.** <!-- src: gui.py:2949 «Save to project» -->
8. Le bilan se relance seul (§5.4).

- `Keep this session only` (garder pour cette séance seulement) : ne pas utiliser. Sans `Save to project`, la photo du mur vide n'est pas gardée. <!-- src: gui.py:2955 «Keep this session only» ; gui.py:2949 «Save to project» -->
- Un deuxième appui sur `CANCEL` annule : tout revient comme avant (`Calibration cancelled`). <!-- src: gui.py:854 «CANCEL» @«"CANCEL"» ; runtime/calibration_flows.py:239 «Calibration cancelled» -->
- Pendant le calcul, des messages disent `Calibrating - driving exposure/gain, keep the stage clear` ou `Calibrating scene - keep the stage clear` (garder la scène vide). <!-- src: runtime/calibration_flows.py:284 «Calibrating - driving exposure/gain, keep the stage clear» ; runtime/calibration_flows.py:311 «Calibrating scene - keep the stage clear» -->

*[capture d'écran : le panneau 2 Calibrate pendant le calcul, la ligne bleue]*

#### Si quelqu'un est dans l'image : CALIBRATE s'arrête

- `CALIBRATE` regarde d'abord si le mur est vide. S'il voit quelqu'un, il **s'arrête tout de suite, ne change rien** et dit où. <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» -->
- La ligne bleue : `Stopped: someone in view, …` (arrêté : quelqu'un dans l'image). <!-- src: runtime/calibration_flows.py:661 «Stopped: someone in view, » -->
- Le message (10 s) : `Someone is in view, … Calibrate stopped and changed nothing. Clear the wall, then press Calibrate again.` <!-- src: runtime/calibration_flows.py:655 «Someone is in view, » ; runtime/calibration_flows.py:655 «Calibrate stopped and changed nothing. » -->
- L'endroit est dit en tiers du mur : `top` (haut), `middle` (milieu), `bottom` (bas), `left` (gauche), `centre`, `right` (droite), suivi de `of the wall (…)`. Ou `outside the Region of Interest (…)` : hors de la zone. <!-- src: core/empty_wall.py:78 «top» @«("top", "middle", "bottom")» ; core/empty_wall.py:78 «middle» @«("top", "middle", "bottom")» ; core/empty_wall.py:78 «bottom» @«("top", "middle", "bottom")» ; core/empty_wall.py:79 «left» @«("left", "centre", "right")» ; core/empty_wall.py:79 «centre» @«("left", "centre", "right")» ; core/empty_wall.py:79 «right» @«("left", "centre", "right")» ; core/empty_wall.py:81 «of the wall (» ; core/empty_wall.py:77 «outside the Region of Interest (» -->
- **Faire :** vider le mur à l'endroit dit, puis rappuyer sur `CALIBRATE`. <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» -->
- **Pendant `CALIBRATE`, personne n'attend près du mur, même immobile.** <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» -->

#### L'exposition

- Avec la caméra en direct, `CALIBRATE` règle l'exposition et le gain de la caméra. Il protège la partie la plus claire du mur : elle ne doit pas devenir blanche. <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» -->
- **Le mur peut paraître plus sombre qu'avant : c'est voulu** **[à confirmer avec Thomas : ce réglage est encore mesuré]**.
- Ligne de la carte : `Exposure: …` (exposition, gain, et `highlights p99.5 …`, les parties claires). Rien à faire. <!-- src: core/calibration.py:375 «Exposure:» @«f"Exposure: {self.exposure_us/1000.0:.1f} ms  gain» ; core/calibration.py:387 «highlights p99.5 » -->
- Si la lumière elle-même est la limite, un message (10 s) et la carte disent : `Exposure: the light is the limit - the brightest part of the wall stays at white with the wall already at its floor …` suivi d'une consigne sur le projecteur. <!-- src: core/calibration.py:393 «Exposure: the light is the limit - the brightest part of the wall stays at white with the wall » -->
  - **Faire :** suivre la consigne (souvent `move the IR flood back from the wall (or widen its beam) so it covers the whole zone`), puis `CALIBRATE` à nouveau. <!-- src: core/calibration.py:653 «move the IR flood back from the wall (or widen its beam) so it covers the whole zone» @«move the IR flood back from the wall (or widen its beam) so it covers the whole zone, » ; gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» -->
- En lisant une prise enregistrée, l'exposition vient de la prise : `Exposure …: from the take`, ou `Exposure: not driven (playback or non-IDS camera) - kept current`. Normal. <!-- src: runtime/calibration_flows.py:299 «: from the take» ; core/calibration.py:369 «Exposure: not driven (playback or non-IDS camera) - kept current» -->

#### Le verdict de la lumière

La dernière ligne de la carte commence par `Wall light:` (la lumière du mur), puis `ok`, `CHECK` (à vérifier) ou `RED` (rouge). Le bilan a la même ligne, `wall light`. <!-- src: core/light_verdict.py:74 «Wall light: » ; core/light_verdict.py:73 «ok» @«"ok": "ok"» ; core/light_verdict.py:73 «CHECK» @«"CHECK"» ; core/light_verdict.py:73 «RED» @«"RED"» ; core/ops_monitor.py:587 «wall light» @«"wall light"» -->

L'app compare la partie la plus claire de la zone à la plus sombre. Au-dessus de 4 fois, c'est rouge (`red above …x`). <!-- src: core/light_verdict.py:182 «red above » -->

| Le message | Ce que ça veut dire | Faire |
|---|---|---|
| `even enough` | la lumière est assez égale <!-- src: core/light_verdict.py:206 «even enough» --> | rien |
| `aim the IR flood more toward the …, away from the …` | la lumière tombe d'un côté <!-- src: core/light_verdict.py:187 «aim the IR flood more toward the » ; core/light_verdict.py:187 «, away from the » --> | tourner le projecteur vers le côté nommé, puis `CALIBRATE` <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `the light is a spot in the middle of the zone` | une tache au milieu, le reste trop sombre <!-- src: core/light_verdict.py:190 «the light is a spot in the middle of the zone» --> | reculer le projecteur du mur, ou élargir son faisceau, puis `CALIBRATE` <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `the light does not reach the … of the zone` | un côté reste dans l'ombre <!-- src: core/light_verdict.py:194 «the light does not reach the » --> | tourner le projecteur vers ce côté, puis `CALIBRATE` <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `the whole zone is dark` | toute la zone est sombre <!-- src: core/light_verdict.py:175 «the whole zone is dark» --> | allumer ou rapprocher le projecteur (`light the zone with the IR flood`), puis `CALIBRATE` <!-- src: core/light_verdict.py:174 «light the zone with the IR flood» ; gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `… is at white, so its light is at least …` | la partie claire est blanche : l'app ne peut pas juger <!-- src: core/light_verdict.py:201 «is at white, so its light is at least» --> | refaire `CALIBRATE`, puis relire le bilan <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |

- **Déplacer le projecteur IR**, c'est le bouger pour de vrai : l'orienter, le reculer, l'élargir.
- Une seule correction à la fois. Puis `CALIBRATE`, puis relire le verdict. <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» -->
- Rouge malgré tout, et plus le temps : continuer, noter l'heure, le dire au support (7–8/12).

#### Le contrôle « personne sur le mur vide »

- À la fin, `CALIBRATE` vérifie que la détection ne voit personne sur le mur vide : `Empty-wall YOLO check (persons >= …):`. <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» ; core/empty_wall.py:169 «Empty-wall YOLO check (persons >= » -->
- `Kept the current gamma …` ou `Kept gamma …` = **bon**. <!-- src: core/empty_wall.py:179 «Kept the current gamma » ; core/empty_wall.py:182 «Kept gamma » -->
- `YOLO now and then sees a person on the EMPTY wall …` = un objet ressemble à une personne, à l'endroit dit (`near x=…, y=…`). <!-- src: core/empty_wall.py:186 «YOLO now and then sees a person on the EMPTY wall» ; core/empty_wall.py:185 «near x=» -->
  - **Faire :** enlever l'objet, ou sortir la zone (`Region of Interest`) de là, puis `CALIBRATE` à nouveau. <!-- src: gui_builder.py:1941 «Region of Interest» @«label="Region of Interest"» ; gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» -->

#### Les autres lignes de la carte

| Ligne | Ce que ça veut dire | Faire |
|---|---|---|
| `Person height: NOT measured …` | pas de danseur sur le mur vide <!-- src: core/calibration.py:746 «Person height: NOT measured » --> | rien : c'est normal |
| `Gamma:`, `CLAHE clip:`, `MOG2: …` | les réglages d'image choisis <!-- src: runtime/calibration_flows.py:592 «Gamma:» @«f"Gamma: » ; core/calibration.py:762 «CLAHE clip: » ; core/calibration.py:750 «MOG2:» @«MOG2: varThreshold » --> | rien |
| `… the scene is too noisy for MOG2 (raise IR / decouple CLAHE)` | l'image est trop « bruitée » <!-- src: core/calibration.py:757 «the scene is too noisy for MOG2 (raise IR / decouple CLAHE)» --> | éclairer le mur plus également (IR), puis `CALIBRATE` <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `Focus score: …` `(LOW - check focus)` | l'image est floue <!-- src: core/calibration.py:769 «Focus score: » ; core/calibration.py:768 « (LOW - check focus)» --> | refaire la mise au point sous IR, puis `CALIBRATE` <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `IR uniformity: …` `aim IR there` | une partie du mur est trop sombre (colonne, rangée) <!-- src: core/calibration.py:773 «IR uniformity: » ; core/calibration.py:771 «aim IR there» --> | tourner le projecteur vers elle, puis `CALIBRATE` <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `Highlight clipping: …` `- lower gain/exposure` | des parties claires sont blanches <!-- src: core/calibration.py:775 «Highlight clipping: » ; core/calibration.py:776 «- lower gain/exposure» --> | lire `Wall light:` et suivre sa consigne **[à confirmer avec Thomas]** <!-- src: core/light_verdict.py:74 «Wall light: » --> |
| `Brightness:`, `Achieved inference FPS:` | luminosité, vitesse <!-- src: core/calibration.py:764 «Brightness: » ; core/calibration.py:777 «Achieved inference FPS: » --> | rien |

*[capture d'écran : la carte Scene Calibration, avec la ligne Wall light]*

#### Garder 15 secondes de ce mur vide

Utile pour refaire `CALIBRATE` plus tard sans vider la scène (§7.2). <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» -->

1. `REC`. L'état dit `REC ARMED - Select slot` (choisir un emplacement). <!-- src: gui_builder.py:1327 «REC» @«label="REC"» ; gui.py:1262 «REC ARMED - Select slot» -->
2. Cliquer un emplacement libre (gris), de 1 à 9. L'enregistrement commence.
3. Attendre 15 s, mur vide.
4. `REC` pour arrêter. <!-- src: gui_builder.py:1327 «REC» @«label="REC"» -->
5. Noter sur papier : l'emplacement, l'heure, l'éclairage.

#### Si CALIBRATE ne démarre pas

- `Load a model before calibrating` : le modèle charge encore. Attendre, rappuyer. <!-- src: runtime/calibration_flows.py:245 «Load a model before calibrating» -->
- `Start the camera or play a recording first` : pas d'image. Vérifier `CAM:` `ON`, cliquer `LIVE`, rappuyer. <!-- src: runtime/calibration_flows.py:255 «Start the camera or play a recording first» ; gui_builder.py:413 «CAM:» @«"CAM:"» ; gui.py:1855 «ON» @«"ON" if camera_running» ; gui_builder.py:1320 «LIVE» @«label="LIVE"» -->

### 5.3 Le danseur — [étape en cours de développement — X4]

**Ce que fait la version d'aujourd'hui.** Il n'y a pas encore de bouton « danseur ». L'app mesure seule la taille des danseurs quand elle les voit en `RUN` (silhouettes entières, au moins 2 s). <!-- src: gui_builder.py:1045 «RUN» @«label="RUN"» -->

**Faire :**

1. `3 Live` → `RUN`. <!-- src: gui_builder.py:643 «3 Live» ; gui_builder.py:1045 «RUN» @«label="RUN"» -->
2. Un danseur debout, entier dans l'image, contre le mur, **à l'endroit le plus loin de la caméra** (là où il paraît le plus petit). 10 secondes.
3. Cliquer `2 Calibrate` : le bilan se relance (ou `Check readiness`). <!-- src: gui_builder.py:642 «2 Calibrate» ; gui_builder.py:876 «Check readiness» -->
4. Lire la ligne `dancer size` (taille des danseurs) : <!-- src: core/ops_monitor.py:333 «dancer size» @«"dancer size", "ok"» -->
   - `OK` : `dancers ~… px -> ~… px in YOLO's input at …`. La taille suffit (100 pixels ou plus pour la détection). <!-- src: core/ops_monitor.py:55 «OK» @«"OK"» ; core/ops_monitor.py:331 «px in YOLO's input at» @«f"dancers ~{h:.0f} px -> ~{net:.0f} px in YOLO's input at {where}"» -->
   - `WARN` : `… below the ~… px YOLO needs: …`. Les danseurs sont trop petits. La ligne propose de resserrer la zone (`narrow the ROI to the dancers' area`). Voir §8. <!-- src: core/ops_monitor.py:55 «WARN» @«"WARN"» ; core/ops_monitor.py:343 « px YOLO needs: » ; core/ops_monitor.py:342 «narrow the ROI to the dancers' area» -->
   - `NOT CHECKED` : `not measured - have a dancer stand at the far wall for 10 s, then check again`. Refaire 2 et 3. <!-- src: core/ops_monitor.py:55 «NOT CHECKED» @«"NOT CHECKED"» ; core/ops_monitor.py:327 «not measured - have a dancer stand at the far wall for 10 s, then check again» -->
- La mesure vaut 30 minutes. Sans danseur vu depuis, la ligne repasse en `NOT CHECKED`. <!-- src: core/ops_monitor.py:55 «NOT CHECKED» @«"NOT CHECKED"» -->
- Message possible : `Person height … -> … px (measured on the dancers)`. L'app a corrigé la taille seule. **Rien à faire.** <!-- src: runtime/main_loop.py:1310 «px (measured on the dancers)» @«f"Person height {old_h} -> {new_h} px (measured on the dancers)"» -->
- **L'amélioration d'image** (ligne `enhancement`) : aujourd'hui `CALIBRATE` ne l'allume pas. Si sa ligne est rouge ou orange, suivre §8 **[à confirmer avec Thomas]**. <!-- src: core/ops_monitor.py:417 «enhancement» @«CheckResult("enhancement"» ; gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» -->

**Ce qui vient (la nouvelle étape) :** un danseur pendant 10 s ; l'app mesure sa taille, choisit l'amélioration d'image sur lui, vérifie le reflet de la ceinture et qu'il est bien vu. **[étape en cours de développement — X4]**

### 5.4 Vérifier — le bilan (`Check readiness`) <!-- src: gui_builder.py:876 «Check readiness» -->

**Il se lance seul :**

- au démarrage, après 3 s d'image (ou 15 s sans image) ;
- après chaque `CALIBRATE` (ou son annulation) ; <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» -->
- quand on ouvre `2 Calibrate` ; <!-- src: gui_builder.py:642 «2 Calibrate» -->
- au passage de `STANDBY` à `RUN` (le message `Go-Live check:` seulement). <!-- src: core/ops_monitor.py:127 «Go-Live check:» ; gui_builder.py:1034 «STANDBY» @«label="STANDBY"» ; gui_builder.py:1045 «RUN» @«label="RUN"» -->

Le bouton `Check readiness` (dans `2 Calibrate`, bloc `Pre-show check`) le relance à tout moment. Il dure moins d'une seconde et n'arrête rien. <!-- src: gui_builder.py:876 «Check readiness» ; gui_builder.py:642 «2 Calibrate» ; gui_builder.py:874 «Pre-show check» @«"Pre-show check"» -->

Un message `Go-Live check: …` résume le bilan, en haut à gauche de l'image. <!-- src: core/ops_monitor.py:127 «Go-Live check: » -->

**En tête de la liste, le verdict :**

| Verdict | Couleur | Ce que ça veut dire | Faire |
|---|---|---|---|
| `READY` | vert | toutes les lignes sont `OK` <!-- src: core/ops_monitor.py:59 «READY» @«"READY"» ; core/ops_monitor.py:55 «OK» @«"OK"» --> | passer à `RUN` <!-- src: gui_builder.py:1045 «RUN» @«label="RUN"» --> |
| `NOT ALL CHECKED` | bleu-gris | des lignes ne sont pas vérifiées <!-- src: core/ops_monitor.py:59 «NOT ALL CHECKED» @«"NOT ALL CHECKED"» --> | normal avant ③ (`dancer size`) ; sinon lire les lignes <!-- src: core/ops_monitor.py:326 «dancer size» --> |
| `CHECK` | orange | au moins une ligne orange <!-- src: core/ops_monitor.py:59 «CHECK» @«"warn": "CHECK"» --> | lire la ligne, faire son action (§8) |
| `NOT READY` | rouge | au moins une ligne rouge <!-- src: core/ops_monitor.py:59 «NOT READY» @«"NOT READY"» --> | faire l'action de la ligne rouge (§8) |

Après le verdict : le compte des lignes (`ok`, `fail`, `warn`, `not checked`) et leurs noms. `RUN` reste toujours possible : le bilan n'empêche rien. <!-- src: core/ops_monitor.py:80 «ok» @«("ok", "ok"), ("fail", "fail"), ("warn", "warn"), ("skip", "not checked")» ; core/ops_monitor.py:80 «fail» @«("ok", "ok"), ("fail", "fail"), ("warn", "warn"), ("skip", "not checked")» ; core/ops_monitor.py:80 «warn» @«("ok", "ok"), ("fail", "fail"), ("warn", "warn"), ("skip", "not checked")» ; core/ops_monitor.py:80 «not checked» @«("ok", "ok"), ("fail", "fail"), ("warn", "warn"), ("skip", "not checked")» ; gui_builder.py:1045 «RUN» @«label="RUN"» -->

**Chaque ligne** commence par son mot, entre crochets, en couleur :

- `OK` vert : bon ; <!-- src: core/ops_monitor.py:55 «OK» @«"OK"» -->
- `NOT CHECKED` bleu-gris : pas vérifié (jamais vert) ; <!-- src: core/ops_monitor.py:55 «NOT CHECKED» @«"NOT CHECKED"» -->
- `WARN` orange : à surveiller, une action conseillée ; <!-- src: core/ops_monitor.py:55 «WARN» @«"WARN"» -->
- `FAIL` rouge : à corriger. <!-- src: core/ops_monitor.py:55 «FAIL» @«"FAIL"» -->

Puis le nom de la ligne, puis le détail. **L'action est à la fin du détail**, après le dernier « - » ou « : ».

**Les treize lignes :**

| Ligne | Ce qu'elle vérifie | Verte quand |
|---|---|---|
| `camera` | la caméra envoie des images, assez vite | `… @ … FPS` (15 et plus) <!-- src: core/ops_monitor.py:142 «camera» @«CheckResult("camera", "ok", detail)» ; core/ops_monitor.py:136 «FPS» @«f"{source} @ {fps:.1f} FPS"» --> |
| `tensorrt` | le moteur rapide de détection | `engine … active` (moteur actif) <!-- src: core/ops_monitor.py:159 «tensorrt» @«CheckResult("tensorrt", "ok"» ; core/ops_monitor.py:155 «engine» @«f"engine{engine} active"» --> |
| `net input` | la forme d'image donnée à la détection | `… (rectangle)` <!-- src: core/ops_monitor.py:203 «net input» @«CheckResult("net input", "ok"» ; core/ops_monitor.py:203 « (rectangle)» --> |
| `osc` | TouchDesigner reçoit | `responded to the probe` ou `probe sent to … (UDP - listener cannot be confirmed)` : les deux sont bons <!-- src: core/ops_monitor.py:248 «osc» @«CheckResult("osc"» ; core/ops_monitor.py:231 «responded to the probe» ; core/ops_monitor.py:235 «probe sent to » ; core/ops_monitor.py:236 «(UDP - listener cannot be confirmed)» --> |
| `calibration` | l'âge du dernier enregistrement du projet | `saved … h ago` (moins de 24 h) <!-- src: core/ops_monitor.py:274 «calibration» @«CheckResult("calibration", "ok"» ; core/ops_monitor.py:270 «saved» @«f"saved {age_h:.1f} h ago» --> |
| `empty wall` | la photo du mur vide | `empty wall: ready` (prête) <!-- src: core/ops_monitor.py:304 «empty wall» @«CheckResult("empty wall", "ok" if ok» ; app.py:1902 «empty wall: ready» @«"empty wall: ready"» --> |
| `dancer size` | la taille des danseurs pour la détection | `dancers ~… px -> ~… px in YOLO's input at …` (§5.3) <!-- src: core/ops_monitor.py:333 «dancer size» @«"dancer size", "ok"» ; core/ops_monitor.py:331 «px in YOLO's input at» @«f"dancers ~{h:.0f} px -> ~{net:.0f} px in YOLO's input at {where}"» --> |
| `disk` | la place pour enregistrer | `… GB free for recordings` (60 Go et plus) <!-- src: core/ops_monitor.py:383 «disk» @«CheckResult("disk", "ok"» ; core/ops_monitor.py:377 « GB free for recordings» --> |
| `enhancement` | l'amélioration d'image | `on: …` (allumée) <!-- src: core/ops_monitor.py:434 «enhancement» @«CheckResult("enhancement", "ok", f"on: {which} ({by})")» ; core/ops_monitor.py:434 «on:» @«CheckResult("enhancement", "ok", f"on: {which} ({by})")» --> |
| `ROI` | la zone tracée va avec le cadre de la caméra | `… on the … picture` <!-- src: core/ops_monitor.py:458 «ROI» @«CheckResult("ROI", "ok"» ; core/ops_monitor.py:458 «picture» @«f"{zone} on the {W}x{H} picture» --> |
| `plate exposure` | la photo du mur vide a la même exposition que la caméra | `as the camera now` <!-- src: core/ops_monitor.py:476 «plate exposure» @«name = "plate exposure"» ; core/ops_monitor.py:509 «as the camera now» --> |
| `wall light` | la lumière sur la zone (§5.2) | `even enough` <!-- src: core/ops_monitor.py:587 «wall light» @«"wall light"» ; core/light_verdict.py:206 «even enough» --> |
| `gpu` | la température de la carte graphique | `… C, …% util` (sous 85 °C) <!-- src: core/ops_monitor.py:395 «gpu» @«CheckResult("gpu", "ok", detail)» ; core/ops_monitor.py:390 «% util» @«f"{temp} C, {gpu_stats.get('util', 0)}% util"» --> |

**C'est bon quand :** `READY`. Avant l'étape ③, `NOT ALL CHECKED` avec la seule ligne `dancer size` est normal. <!-- src: core/ops_monitor.py:59 «READY» @«"READY"» ; core/ops_monitor.py:59 «NOT ALL CHECKED» @«"NOT ALL CHECKED"» ; core/ops_monitor.py:326 «dancer size» -->

**Sinon :** chaque message de chaque ligne est au §8, avec l'action.

*[capture d'écran : le panneau 2 Calibrate, le verdict en tête et les treize lignes en couleur]*

### 5.5 RUN — `3 Live` <!-- src: gui_builder.py:643 «3 Live» -->

1. Cliquer `3 Live`. Le panneau `6 - Go Live` s'ouvre. <!-- src: gui_builder.py:643 «3 Live» ; gui_builder.py:1026 «6 - Go Live» -->
2. Cliquer `RUN` : il devient vert. (L'app démarre déjà en `RUN`.) <!-- src: gui_builder.py:1045 «RUN» @«label="RUN"» ; gui_builder.py:1045 «RUN» @«label="RUN"» -->
3. Vérifier en haut : `RUN`, `CAM:` `ON` `[IDS]`, `OSC:` `ON`, `[TRT]`. <!-- src: gui_builder.py:63 «RUN» @«"RUN"» ; gui_builder.py:413 «CAM:» @«"CAM:"» ; gui.py:1855 «ON» @«"ON" if camera_running» ; gui.py:1855 «ON» @«"ON" if camera_running» ; gui.py:1862 «[IDS]» @«"[IDS]"» ; gui_builder.py:429 «OSC:» @«"OSC:"» ; gui.py:2078 «[TRT]» @«"[TRT]"» -->
4. Vérifier dans le panneau : `Max dancers` = le nombre de danseurs ; `Use empty wall` coché ; dessous, `empty wall: ready`. <!-- src: gui_builder.py:1128 «Max dancers» ; gui_builder.py:1225 «Use empty wall» @«label="Use empty wall"» ; app.py:1902 «empty wall: ready» @«"empty wall: ready"» -->
5. `Ctrl+S`. <!-- src: gui_builder.py:394 «Ctrl+S» @«Save config (Ctrl+S)» -->

- `STANDBY` = l'image seule : pas de détection, rien envoyé aux visuels. <!-- src: gui_builder.py:1034 «STANDBY» @«label="STANDBY"» -->
- `Allow remote control during RUN` (autoriser le contrôle à distance en RUN) : **[à confirmer avec Thomas : cocher ou non les 7–8/12]**. Le 12/12 : ne pas cocher. <!-- src: gui_builder.py:1100 «Allow remote control during RUN» -->
- Les autres réglages du panneau (`Detection`, `Dancer IDs`, `Stability`, `Hold (s)`…) sont réglés par Thomas dans le projet. Ne pas les changer. <!-- src: gui_builder.py:1057 «Detection» @«add_text("Detection"» ; gui_builder.py:1120 «Dancer IDs» @«"Dancer IDs"» ; gui_builder.py:1145 «Stability» @«label="Stability"» ; gui_builder.py:1159 «Hold (s)» @«label="Hold (s)"» -->

**C'est bon quand :** la ligne de santé est verte : `OK - all … checks fine (…)`, et une boule suit chaque danseur. <!-- src: core/ops_monitor.py:1337 «checks fine (» @«f"OK - all {len(rows)} checks fine ({shown})"» -->

*[capture d'écran : le panneau 3 Live (6 - Go Live), RUN actif]*

<div style="break-before: page"></div>

## 6. Pendant le spectacle

### 6.1 La ligne de santé

- Une bande de couleur, toute la largeur de la fenêtre, sous l'image. Elle change chaque seconde.
- Elle montre **le pire** de tout ce que l'app surveille, avec **la seule chose à faire**.
- Les couleurs :

| Couleur | Mot | Ce que ça veut dire |
|---|---|---|
| vert | `OK` | tout va bien <!-- src: core/ops_monitor.py:55 «OK» @«"OK"» --> |
| bleu-gris | `NOT CHECKED` | quelque chose n'est pas vérifié (jamais vert) <!-- src: core/ops_monitor.py:55 «NOT CHECKED» @«"NOT CHECKED"» --> |
| orange | `WARN` | à surveiller <!-- src: core/ops_monitor.py:55 «WARN» @«"WARN"» --> |
| rouge | `FAIL` | un vrai problème <!-- src: core/ops_monitor.py:55 «FAIL» @«"FAIL"» --> |

- Le texte : le mot, la source, le fait, puis l'action (après le dernier « - »). S'il y a d'autres soucis : `(+… : …)` à la fin, avec leurs noms. <!-- src: core/ops_monitor.py:1342 «(+» @«   (+{len(others)}» -->
- Au démarrage : `NOT CHECKED - the health line starts with the app's first second`. Normal, une seconde. <!-- src: gui_builder.py:1244 «NOT CHECKED - the health line starts with the app's first second» -->
- En `STANDBY`, la ligne est bleu-grise (`not checked:` puis `STANDBY: no frame is processed`). Normal. <!-- src: gui_builder.py:1034 «STANDBY» @«label="STANDBY"» ; runtime/main_loop.py:1539 «STANDBY: no frame is processed» ; core/ops_monitor.py:1117 «not checked:» @«f"not checked: {self._why}"» -->

**La règle pendant une pièce :**

- Orange : **ne rien faire, continuer.** Noter l'heure. Agir à la pause.
- Rouge : faire l'action **seulement si elle n'arrête pas le spectacle** (tableau ci-dessous). Sinon : continuer, agir à la pause.
- Toujours noter l'heure et le message.

**Chaque message de la ligne, et quoi faire :**

| Source | Le message (début) | Pendant la pièce | À la pause |
|---|---|---|---|
| `camera` | `… not open` / `reconnecting …` `check the camera's USB3 cable and power` <!-- src: core/ops_monitor.py:1206 «camera» @«CheckResult("camera", "fail", f"{source + » ; core/ops_monitor.py:135 «camera» @«CheckResult("camera", "fail"» ; core/ops_monitor.py:1207 «not open» @«'reconnecting' if reconnecting else 'not open'» ; core/ops_monitor.py:1207 «reconnecting» @«'reconnecting' if reconnecting else 'not open'» ; core/ops_monitor.py:983 «check the camera's USB3 cable and power» --> | vérifier tout de suite le câble USB3 et l'alimentation de la caméra : sans caméra, plus rien ne suit | si elle ne revient pas : relancer l'app (§7.4) |
| `camera` | `a recording is playing, not the camera - LIVE (Recordings bar)` <!-- src: core/ops_monitor.py:1203 «camera» @«CheckResult("camera", "warn", "a recording» ; core/ops_monitor.py:1203 «a recording is playing, not the camera - LIVE (Recordings bar)» --> | **tout de suite** : `LIVE (camera)` sur le bandeau rouge, ou `LIVE` <!-- src: gui_builder.py:512 «LIVE (camera)» ; gui_builder.py:1320 «LIVE» @«label="LIVE"» --> | — |
| `output` | `OSC is off: the visuals get no point` <!-- src: core/ops_monitor.py:1215 «output» @«CheckResult("output", "warn"» ; core/ops_monitor.py:1215 «OSC is off: the visuals get no point» @«OSC is off: the visuals get no point - » ; core/ops_monitor.py:1215 «OSC» @«OSC is off: the visuals get no point - » --> | tout de suite : `Advanced` → `OSC` → cocher `Enable OSC` <!-- src: gui_builder.py:685 «Advanced» @«label="Advanced"» ; gui_builder.py:1569 «OSC» @«label="OSC"» ; gui_builder.py:1572 «Enable OSC» --> | `Ctrl+S` <!-- src: gui_builder.py:394 «Ctrl+S» @«Save config (Ctrl+S)» --> |
| `output` | `OSC sends to … fail (… dropped) - start TouchDesigner and its patch` <!-- src: core/ops_monitor.py:1217 «output» @«CheckResult("output", "fail"» ; core/ops_monitor.py:1217 «f"OSC sends to {target} fail ({errors} dropped) - start "» ; core/ops_monitor.py:1218 «TouchDesigner and its patch» --> | tout de suite : relancer TouchDesigner et son patch (avec l'équipe des visuels) | — |
| `points` | `a body on the wall has` `had no point for … s` … `check Max dancers (3 Live)` <!-- src: core/ops_monitor.py:981 «points» @«POINTS_ROW = "points"» ; core/ops_monitor.py:1125 «a body on the wall has» ; core/ops_monitor.py:1127 «had no point for » ; core/ops_monitor.py:982 «check Max dancers (3 Live)» --> | si c'est un danseur sans boule : `Max dancers` doit valoir au moins le nombre de danseurs, le monter s'il est trop bas ; si c'est une ombre ou quelqu'un hors danse : **ne rien faire, continuer** <!-- src: gui_builder.py:1128 «Max dancers» --> | noter l'heure |
| `points` | `not checked: …` <!-- src: core/ops_monitor.py:981 «points» @«POINTS_ROW = "points"» ; core/ops_monitor.py:1117 «not checked:» @«f"not checked: {self._why}"» --> | **ne rien faire, continuer** | — |
| `empty wall` | `stale since …` / `no longer matches the wall` / `from another camera crop` <!-- src: core/ops_monitor.py:1228 «empty wall» @«"empty wall", "fail", f"stale since» ; core/ops_monitor.py:1228 «stale since» @«f"stale since {stale_since}{why} - {EMPTY_WALL_HOW}"» ; core/ops_monitor.py:842 «no longer matches the wall» ; core/ops_monitor.py:587 «from another camera crop» @«from another camera crop - » --> | **ne rien faire, continuer** | `CALIBRATE` sur le mur vide (§7.2) <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `empty wall` | `none - …` `when the wall is empty: CALIBRATE (2 Calibrate), then Save to project` <!-- src: core/ops_monitor.py:1237 «empty wall» @«CheckResult("empty wall", "warn", f"none» ; core/ops_monitor.py:1237 «none -» @«f"none - {EMPTY_WALL_HOW}"» ; core/ops_monitor.py:984 «when the wall is empty: CALIBRATE (2 Calibrate), then Save to project» --> | **ne rien faire, continuer** | §5.2 |
| `empty wall` | `off: the dancers' points are not checked - 3 Live > Use empty wall` <!-- src: core/ops_monitor.py:1234 «empty wall» @«CheckResult("empty wall", "warn", "off:» ; core/ops_monitor.py:1234 «off: the dancers' points are not checked - 3 Live > Use » --> | **ne rien faire, continuer** | cocher `Use empty wall`, `Ctrl+S` <!-- src: gui_builder.py:1225 «empty wall» @«label="Use empty wall"» ; gui_builder.py:1225 «Use empty wall» @«label="Use empty wall"» ; gui_builder.py:394 «Ctrl+S» @«Save config (Ctrl+S)» --> |
| `empty wall` | `being captured` <!-- src: core/ops_monitor.py:1232 «empty wall» @«CheckResult("empty wall", "skip", "being captured")» ; core/ops_monitor.py:1232 «being captured» @«"being captured"» --> | **ne rien faire** : 2 s | — |
| `frames` | `… frame(s) in a row failed to process - …` `restart the app` <!-- src: core/ops_monitor.py:1247 «frames» @«CheckResult("frames", "fail"» ; core/ops_monitor.py:1247 «frame(s) in a row failed to process - » ; core/ops_monitor.py:985 «restart the app» @«RESTART_HOW = "restart the app"» --> | si les boules suivent encore : continuer ; si l'image est figée et rien ne suit : relancer l'app (§7.4) | relancer l'app |
| `engine` | `running PyTorch, not the TensorRT engine` `restart the app` <!-- src: core/ops_monitor.py:985 «restart the app» @«RESTART_HOW = "restart the app"» ; core/ops_monitor.py:1256 «engine» @«running PyTorch, not the TensorRT engine - » ; core/ops_monitor.py:1256 «running PyTorch, not the TensorRT engine» @«running PyTorch, not the TensorRT engine - » --> | **ne rien faire, continuer** | relancer l'app (§7.4) |
| `camera stalls` | `… a minute …` `at the next break: …` / `the picture freezes often` <!-- src: core/ops_monitor.py:1178 «camera stalls» @«name = "camera stalls"» ; core/ops_monitor.py:1183 «a minute» @«f"{per_min:.1f} a minute ({n} in the last» ; core/ops_monitor.py:1188 «at the next break: » ; core/ops_monitor.py:1185 «the picture freezes often» --> | **ne rien faire, continuer** | brancher la caméra sur un autre port USB3, vérifier le câble |
| `fps` | `FPS under half its baseline … - mains plugged in, close everything but WallDance and TouchDesigner` <!-- src: core/ops_monitor.py:1271 «fps» @«CheckResult("fps", "warn"» ; core/ops_monitor.py:1271 «FPS under half its baseline» ; core/ops_monitor.py:1272 «mains plugged in, close everything but WallDance and » --> | vérifier le secteur ; fermer ce qui n'est ni WallDance ni TouchDesigner | idem |
| `gpu` | `GPU at … C for … s - raise the laptop's back, clear its vents` <!-- src: core/ops_monitor.py:1277 «gpu» @«CheckResult("gpu", "warn", f"GPU at» ; core/ops_monitor.py:1277 «GPU at» @«f"GPU at {temp} C for {s:.0f} s - raise the laptop's back, clear "» --> | surélever l'arrière du portable, dégager les aérations | idem |
| `light` | `the light is not the empty-wall snapshot's - …` <!-- src: core/ops_monitor.py:1280 «light» @«CheckResult("light", "warn"» ; core/ops_monitor.py:1280 «the light is not the empty-wall snapshot's - » --> | **ne rien faire, continuer** | `CALIBRATE` sur le mur vide (§7.2) <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `no dancer` | `no dancer tracked for … s - if dancers are on the wall: check the IR and the picture` <!-- src: core/ops_monitor.py:1283 «no dancer» @«CheckResult("no dancer", "warn"» ; core/ops_monitor.py:1283 «no dancer» @«f"no dancer tracked for {s:.0f} s - if dancers are on the "» ; core/ops_monitor.py:1284 «wall: check the IR and the picture» --> | s'il y a des danseurs : le projecteur IR est-il allumé ? l'image est-elle normale ? Sinon **ne rien faire** | — |
| `over cap` | `more people than max_persons for … s` `check Max dancers (3 Live)` <!-- src: core/ops_monitor.py:1287 «over cap» @«CheckResult("over cap", "warn"» ; core/ops_monitor.py:1287 «more people than max_persons for» @«f"more people than max_persons for {s:.0f} s - "» ; core/ops_monitor.py:982 «check Max dancers (3 Live)» --> | vérifier `Max dancers` <!-- src: gui_builder.py:1128 «Max dancers» --> | — |
| `height` | `the dancers' height looks stale - a dancer standing 10 s at the far wall` <!-- src: core/ops_monitor.py:1291 «height» @«CheckResult("height", "warn"» ; core/ops_monitor.py:1291 «the dancers' height looks stale - a dancer standing 10 s at » --> | **ne rien faire, continuer** | étape ③ (§5.3) |
| `enhancement`, `ROI`, `plate exposure` | les lignes du bilan qui sont en `FAIL` (§8) <!-- src: core/ops_monitor.py:417 «enhancement» @«CheckResult("enhancement"» ; core/ops_monitor.py:446 «ROI» @«CheckResult("ROI"» ; core/ops_monitor.py:476 «plate exposure» @«name = "plate exposure"» ; core/ops_monitor.py:55 «FAIL» @«"FAIL"» --> | **ne rien faire, continuer** | l'action du §8 |

**Bon à savoir :** la ligne passe parfois au rouge `points` alors que tous les danseurs ont leur boule (une ombre à côté d'un danseur, des gens au bord du toit). Regarder l'image : si chaque danseur a sa boule, **continuer**. <!-- src: core/ops_monitor.py:981 «points» @«POINTS_ROW = "points"» -->

*[capture d'écran : la ligne de santé en orange, puis en rouge, avec leur texte]*

### 6.2 Ce qu'on ne touche jamais pendant une pièce

- Les emplacements 1 à 9, `REC`, `IMPORT` : une prise lue en `RUN` part aux visuels à la place des danseurs. <!-- src: gui_builder.py:1327 «REC» @«label="REC"» ; gui_builder.py:1347 «IMPORT» @«label="IMPORT"» ; gui_builder.py:1045 «RUN» @«label="RUN"» -->
- `CALIBRATE` et `STANDBY`. <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» ; gui_builder.py:1034 «STANDBY» @«label="STANDBY"» -->
- `Advanced`, `Project:`, `Config version`, `Rebuild TRT`. <!-- src: gui_builder.py:685 «Advanced» @«label="Advanced"» ; gui_builder.py:375 «Project:» @«"Project:"» ; gui_builder.py:780 «Config version» @«"Config version"» ; gui_builder.py:481 «Rebuild TRT» -->
- Dans `1 Rig` : l'image (un double-clic change la zone), `Reset`, `Clear all`. <!-- src: gui_builder.py:641 «1 Rig» ; gui_builder.py:1950 «Reset» @«label="Reset"» ; gui_builder.py:1925 «Clear all» @«label="Clear all"» -->
- Le clavier, sauf `Ctrl+S`. La touche P fige l'image à l'écran. <!-- src: gui_builder.py:394 «Ctrl+S» @«Save config (Ctrl+S)» -->
- **Le seul réglage permis : `Max dancers`**, si un danseur n'a pas de boule. <!-- src: gui_builder.py:1128 «Max dancers» -->

### 6.3 « LECTURE - pas en direct »

- Un bandeau rouge au-dessus de l'image : `LECTURE - pas en direct` `- TouchDesigner recoit un enregistrement, pas la camera`. <!-- src: gui_builder.py:499 «LECTURE - pas en direct» ; gui_builder.py:510 «TouchDesigner recoit un enregistrement, pas la camera» -->
- Il veut dire : l'app est en `RUN` et lit une prise enregistrée (un emplacement a été cliqué). **Les visuels suivent la prise, pas les danseurs.** Même en pause de lecture. <!-- src: gui_builder.py:1045 «RUN» @«label="RUN"» -->
- **Faire, tout de suite :** cliquer `LIVE (camera)` sur le bandeau (ou `LIVE` dans la barre des enregistrements). Le bandeau disparaît. <!-- src: gui_builder.py:512 «LIVE (camera)» ; gui_builder.py:1320 «LIVE» @«label="LIVE"» -->

*[capture d'écran : le bandeau rouge LECTURE - pas en direct au-dessus de l'image]*

### 6.4 Noter

Sur une feuille, pour chaque souci : **l'heure**, **le message** (les premiers mots suffisent), **ce qui a été fait**. Les journaux de l'app sont relus après la séance ; l'heure permet de retrouver le moment.

<div style="break-before: page"></div>

## 7. Entre deux pièces

### 7.1 Quand refaire CALIBRATE

- l'alerte `The light is … the empty-wall snapshot` (la lumière a changé) ; <!-- src: core/ops_monitor.py:787 «the empty-wall snapshot» @«f"The light is {light_ratio_text(light_ratio)} the empty-wall snapshot{when}: "» -->
- `empty wall: scene changed - Calibrate on the empty wall` ou l'alerte `The empty-wall snapshot no longer matches the wall (…)` (la photo du mur vide est périmée) ; <!-- src: app.py:1904 «empty wall: scene changed - Calibrate on the empty wall» @«"empty wall: scene changed - Calibrate on the empty wall"» ; core/ops_monitor.py:842 «The empty-wall snapshot no longer matches the wall (» @«f"The empty-wall snapshot no longer matches the wall ({plate_stale}): the foreground is not "» -->
- la caméra, la zone ou le projecteur IR ont bougé ;
- `wall light` rouge, après avoir déplacé le projecteur ; <!-- src: core/ops_monitor.py:587 «wall light» @«"wall light"» -->
- une ligne du bilan qui dit `Calibrate on the empty wall`. <!-- src: core/ops_monitor.py:290 «Calibrate on the empty wall» -->

### 7.2 Comment, entre deux pièces

**La scène peut être vidée :**

1. `3 Live` → `STANDBY`. Plus rien n'est envoyé aux visuels. <!-- src: gui_builder.py:643 «3 Live» ; gui_builder.py:1034 «STANDBY» @«label="STANDBY"» -->
2. Vider le mur.
3. `2 Calibrate` → `CALIBRATE` → attendre → `Save to project`. <!-- src: gui_builder.py:642 «2 Calibrate» ; gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» ; gui.py:2949 «Save to project» -->
4. Lire le bilan (§5.4).
5. `3 Live` → `RUN`. <!-- src: gui_builder.py:643 «3 Live» ; gui_builder.py:1045 «RUN» @«label="RUN"» -->

**La scène ne peut pas être vidée,** mais une prise de mur vide de cet éclairage existe (§5.2) :

1. `3 Live` → `STANDBY`. <!-- src: gui_builder.py:643 «3 Live» ; gui_builder.py:1034 «STANDBY» @«label="STANDBY"» -->
2. Cliquer l'emplacement de la prise de mur vide : elle se lit.
3. `2 Calibrate` → `CALIBRATE` → attendre → `Save to project`. <!-- src: gui_builder.py:642 «2 Calibrate» ; gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» ; gui.py:2949 «Save to project» -->
4. **`LIVE`** : retour à la caméra. <!-- src: gui_builder.py:1320 «LIVE» @«label="LIVE"» -->
5. `3 Live` → `RUN`. <!-- src: gui_builder.py:643 «3 Live» ; gui_builder.py:1045 «RUN» @«label="RUN"» -->

**Jamais une prise lue en `RUN`.** Le message de l'app le dit aussi : `never play a take in RUN`. <!-- src: gui_builder.py:1045 «RUN» @«label="RUN"» ; core/ops_monitor.py:291 «never play a take in RUN» -->

### 7.3 La photo du mur vide (« plate ») et « plate stale »

- L'app compare chaque image à la photo du mur vide : ce qui n'est pas sur la photo est quelqu'un.
- Quand la scène change (lumière, caméra bougée), la photo est **périmée** (« plate stale »). L'app le dit à trois endroits :
  - sous `Use empty wall` et dans `2 Calibrate` : `empty wall: scene changed - Calibrate on the empty wall` ; <!-- src: gui_builder.py:1225 «Use empty wall» @«label="Use empty wall"» ; gui_builder.py:642 «2 Calibrate» ; app.py:1904 «empty wall: scene changed - Calibrate on the empty wall» @«"empty wall: scene changed - Calibrate on the empty wall"» -->
  - dans `Alerts:` : `The empty-wall snapshot no longer matches the wall (…): the foreground is not to be trusted until a new one - …` ; <!-- src: gui_builder.py:1274 «Alerts:» @«"Alerts:"» ; core/ops_monitor.py:842 «f"The empty-wall snapshot no longer matches the wall ({plate_stale}): the foreground is not "» ; core/ops_monitor.py:843 «to be trusted until a new one - » -->
  - sur la ligne de santé, en rouge : `empty wall` `stale since …`. <!-- src: core/ops_monitor.py:1228 «empty wall» @«"empty wall", "fail", f"stale since» ; core/ops_monitor.py:1228 «stale since» @«"empty wall", "fail", f"stale since» ; core/ops_monitor.py:1228 «f"stale since {stale_since}{why} - {EMPTY_WALL_HOW}"» -->
- **Ce message reste** jusqu'à une nouvelle photo : il ne part pas tout seul, même si la scène revient.
- Pendant la pièce : continuer (les points sont moins sûrs). À la pause : §7.2.
- Une photo prise à une autre exposition ou avec un autre cadre est refusée : ligne `plate exposure`, `RUN refuses it: Calibrate on the empty wall`. Même action : §7.2. <!-- src: core/ops_monitor.py:476 «plate exposure» @«name = "plate exposure"» ; core/ops_monitor.py:500 «RUN refuses it: Calibrate on the empty wall» @« - RUN refuses it: Calibrate on the empty wall» -->

### 7.4 Relancer l'app : le spectacle revient

**Ce que l'app garde seule :**

- les réglages du projet, à chaque `Ctrl+S` ou `Save to project` ; <!-- src: gui_builder.py:394 «Ctrl+S» @«Save config (Ctrl+S)» ; gui.py:2949 «Save to project» -->
- la photo du mur vide « vivante », toutes les minutes, sans rien faire.

**Ce qu'une relance perd :** un réglage changé sans `Ctrl+S`. <!-- src: gui_builder.py:394 «Ctrl+S» @«Save config (Ctrl+S)» -->

**Relancer :**

1. Si possible : `3 Live` → `STANDBY`. <!-- src: gui_builder.py:643 «3 Live» ; gui_builder.py:1034 «STANDBY» @«label="STANDBY"» -->
2. Fermer l'app (§3). Figée et impossible à fermer : **[à confirmer avec Thomas : comment la fermer de force]**.
3. La relancer (§3), le **même projet**, `Launch`. <!-- src: gui.py:2359 «Launch» @«label="Launch"» -->
4. Elle revient en `RUN`, avec les derniers réglages enregistrés et la dernière photo du mur vide. <!-- src: gui_builder.py:1045 «RUN» @«label="RUN"» -->
5. Lire le bilan. Si `empty wall: ready` : continuer. Sinon : `CALIBRATE` dès que le mur est vide. <!-- src: app.py:1902 «empty wall: ready» @«"empty wall: ready"» ; gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» -->
- Après un arrêt forcé, `Previous session crashed (…)` s'affiche une fois au démarrage suivant : normal, noter l'heure. <!-- src: services/crash_marker.py:174 «Previous session crashed (» -->

<div style="break-before: page"></div>

## 8. Si quelque chose ne va pas

**Où l'app parle :**

- les **messages** en haut à gauche de l'image (quelques secondes) ; ceux qui commencent par `/!\ ` sont des alertes, rouges, 8 s ; <!-- src: ui/adapter.py:368 «/!» @«{e.message}» -->
- `Alerts:`, sous la ligne de santé : les alertes qui durent ; <!-- src: gui_builder.py:1274 «Alerts:» @«"Alerts:"» -->
- la **ligne de santé** (§6) ;
- les **lignes du bilan** (§5.4) ;
- les **bandeaux** au-dessus de l'image ;
- les **badges** de la barre du haut.

Les tableaux suivent par thème. Chercher les **premiers mots** du message.

### 8.1 La caméra

| À l'écran | Ce que ça veut dire | Que faire |
|---|---|---|
| `CAM:` `OFF` ; `RECONNECTING` <!-- src: gui_builder.py:413 «CAM:» @«"CAM:"» ; gui_builder.py:414 «OFF» @«"OFF"» ; gui_builder.py:423 «RECONNECTING» @«"RECONNECTING"» ; gui_builder.py:423 «ON» @«"RECONNECTING"» --> | pas de caméra, ou elle se reconnecte | vérifier le câble USB3 et l'alimentation ; attendre `ON` <!-- src: gui.py:1855 «ON» @«"ON" if camera_running» --> |
| `camera` `FAIL` `… not open` / `reconnecting` <!-- src: core/ops_monitor.py:135 «camera» @«CheckResult("camera", "fail", f"{source}: {state}")» ; core/ops_monitor.py:55 «FAIL» @«"FAIL"» ; core/ops_monitor.py:134 «not open» @«"reconnecting" if reconnecting else "not open"» ; core/ops_monitor.py:134 «reconnecting» @«"reconnecting" if reconnecting else "not open"» --> | idem (ligne du bilan) | idem ; si elle ne revient pas : relancer l'app (§8.8) |
| `camera` `… check the camera's USB3 cable and power` <!-- src: core/ops_monitor.py:1206 «camera» @«CheckResult("camera", "fail", f"{source + ': ' if source else ''}"» ; core/ops_monitor.py:983 «check the camera's USB3 cable and power» --> | idem (ligne de santé) | idem |
| `Camera down for …s - check USB3/power` <!-- src: core/ops_monitor.py:802 «Camera down for » ; core/ops_monitor.py:803 «check USB3/power» --> | alerte : la caméra est perdue depuis 15 s ou plus | idem ; l'alerte revient toutes les 2 min tant qu'elle manque |
| `[CV]` (au lieu de `[IDS]`) <!-- src: gui.py:1865 «[CV]» @«"[CV]"» ; gui.py:1862 «[IDS]» @«"[IDS]"» --> | l'app a pris une autre caméra (la webcam) | fermer l'app, brancher la caméra IDS, relancer |
| `camera` `WARN` `… below the … FPS show floor` <!-- src: core/ops_monitor.py:140 «camera» @«CheckResult("camera", "warn",» ; core/ops_monitor.py:55 «WARN» @«"WARN"» ; core/ops_monitor.py:141 « FPS show floor» --> | la caméra donne moins de 15 images par seconde | secteur branché ; fermer tout sauf WallDance et TouchDesigner |
| `camera stalls` `WARN` `… - at the next break: …, try another USB3 port` <!-- src: core/ops_monitor.py:1178 «camera stalls» @«name = "camera stalls"» ; core/ops_monitor.py:55 «WARN» @«"WARN"» ; core/ops_monitor.py:1188 «try another USB3 port» --> | l'image se fige 2 fois par minute ou plus | pendant la pièce : rien ; à la pause : autre port USB3, câble |
| `camera stalls` `FAIL` `…: the picture freezes often - …, on another USB3 port at the next break` <!-- src: core/ops_monitor.py:1178 «camera stalls» @«name = "camera stalls"» ; core/ops_monitor.py:55 «FAIL» @«"FAIL"» ; core/ops_monitor.py:1185 «the picture freezes often - » ; core/ops_monitor.py:1186 «USB3 port at the next break» --> | 6 fois par minute ou plus | idem |
| `camera stalls` `not checked: the camera is not live` <!-- src: core/ops_monitor.py:1178 «camera stalls» @«name = "camera stalls"» ; core/ops_monitor.py:1181 «not checked: the camera is not live» --> | pas de caméra en direct (lecture, `STANDBY` sans image) <!-- src: gui_builder.py:1034 «STANDBY» @«label="STANDBY"» --> | rien |
| `camera` `WARN` `a recording is playing, not the camera - LIVE (Recordings bar)` <!-- src: core/ops_monitor.py:1203 «camera» @«CheckResult("camera", "warn", "a recording» ; core/ops_monitor.py:55 «WARN» @«"WARN"» ; core/ops_monitor.py:1203 «a recording is playing, not the camera - LIVE (Recordings bar)» --> | une prise est lue à la place de la caméra | `LIVE` tout de suite (§6.3) <!-- src: gui_builder.py:1320 «LIVE» @«label="LIVE"» --> |
| `LECTURE - pas en direct` <!-- src: gui_builder.py:499 «LECTURE - pas en direct» --> | idem, en `RUN` <!-- src: gui_builder.py:1045 «RUN» @«label="RUN"» --> | `LIVE (camera)` tout de suite <!-- src: gui_builder.py:512 «LIVE (camera)» --> |
| `Slot … was recorded with mirror/rotate: … - the project now uses: … (1 Rig > Input)` <!-- src: runtime/recording_controller.py:532 «was recorded with mirror/rotate: » ; runtime/recording_controller.py:533 «- the project now uses: » ; runtime/recording_controller.py:533 «(1 Rig > Input)» --> | une prise lue a été filmée avec une autre rotation | rien ; `LIVE` pour revenir à la caméra <!-- src: gui_builder.py:1320 «LIVE» @«label="LIVE"» --> |
| `Exposure-limited: … FPS` <!-- src: gui.py:2044 «Exposure-limited: » --> | (réglage expert) l'exposition limite la vitesse | rien |

### 8.2 La vitesse, la carte graphique, le moteur de détection

| À l'écran | Ce que ça veut dire | Que faire |
|---|---|---|
| `FPS dropped to … (baseline …) for …s` ; `fps` `FPS under half its baseline …` ; `FPS:` en rouge <!-- src: core/ops_monitor.py:704 «FPS dropped to » ; core/ops_monitor.py:705 «(baseline » ; core/ops_monitor.py:1271 «fps» @«CheckResult("fps", "warn"» ; core/ops_monitor.py:1271 «FPS under half its baseline» ; gui_builder.py:451 «FPS:» @«"FPS:"» --> | l'app tourne deux fois moins vite que d'habitude | secteur branché ; fermer tout sauf WallDance et TouchDesigner |
| `GPU at … C for …s` ; `gpu` `WARN` `… at/above … C` ; `gpu` `GPU at … C for … s - raise the laptop's back, clear its vents` <!-- src: core/ops_monitor.py:823 «GPU at» @«f"GPU at {stats['temp']} C for "» ; core/ops_monitor.py:823 «C» @«f"GPU at {stats['temp']} C for "» ; core/ops_monitor.py:394 «gpu» @«CheckResult("gpu", "warn", detail» ; core/ops_monitor.py:394 «gpu» @«CheckResult("gpu", "warn", detail» ; core/ops_monitor.py:55 «WARN» @«"WARN"» ; core/ops_monitor.py:394 « - at/above » ; core/ops_monitor.py:1277 «raise the laptop's back, clear » --> | la carte graphique chauffe (85 °C ou plus) | surélever l'arrière du portable, dégager les aérations |
| `gpu` `NOT CHECKED` `no NVIDIA GPU stats available` <!-- src: core/ops_monitor.py:388 «gpu» @«CheckResult("gpu", "skip"» ; core/ops_monitor.py:55 «NOT CHECKED» @«"NOT CHECKED"» ; core/ops_monitor.py:388 «no NVIDIA GPU stats available» --> | la température n'est pas lue | rien ; le dire au support |
| `TensorRT OFF - running PyTorch (…)` + `Rebuild TRT` ; `[PT]` <!-- src: runtime/model_controller.py:335 «TensorRT OFF - running PyTorch (» ; gui_builder.py:481 «Rebuild TRT» ; gui_builder.py:442 «[PT]» @«"[PT]"» --> | le moteur rapide ne marche pas : la détection est lente | **jamais `Rebuild TRT`** ; relancer l'app à la pause (§8.8) <!-- src: gui_builder.py:481 «Rebuild TRT» --> |
| `Rebuilding TensorRT engine...` <!-- src: runtime/model_controller.py:322 «Rebuilding TensorRT engine...» --> | quelqu'un a cliqué `Rebuild TRT` : plusieurs minutes <!-- src: gui_builder.py:481 «Rebuild TRT» --> | attendre la fin ; appeler (§8.8) |
| `tensorrt` `FAIL` `requested but the engine … is MISSING …` / `requested but running PyTorch (…)` <!-- src: core/ops_monitor.py:165 «tensorrt» @«CheckResult("tensorrt", "fail",» ; core/ops_monitor.py:55 «FAIL» @«"FAIL"» ; core/ops_monitor.py:166 «requested but the engine» @«f"requested but the engine{engine} is MISSING» ; core/ops_monitor.py:171 «requested but running PyTorch (» @«f"requested but running PyTorch ({reason})» --> | le moteur du projet manque ou ne charge pas | relancer l'app ; si ça reste : la version du 8 octobre (§8.8) |
| `tensorrt` `WARN` `not requested (PyTorch, 3-7x slower) …` / `… (GPU path degraded: …)` <!-- src: core/ops_monitor.py:157 «tensorrt» @«CheckResult("tensorrt", "warn",» ; core/ops_monitor.py:55 «WARN» @«"WARN"» ; core/ops_monitor.py:177 «not requested (PyTorch, 3-7x slower)» ; core/ops_monitor.py:158 « (GPU path degraded: » --> | le moteur rapide est décoché dans le projet, ou dégradé | entre deux pièces, en `STANDBY` : `Advanced` → `Model` → cocher `TensorRT:`, attendre **[à confirmer avec Thomas]** <!-- src: gui_builder.py:1034 «STANDBY» @«label="STANDBY"» ; gui_builder.py:685 «Advanced» @«label="Advanced"» ; gui_builder.py:1603 «Model» @«label="Model"» ; gui_builder.py:1627 «)» @«add_text("TensorRT:")» ; gui_builder.py:1627 «TensorRT:» @«add_text("TensorRT:")» --> |
| `engine` `FAIL` `running PyTorch, not the TensorRT engine` `restart the app` <!-- src: core/ops_monitor.py:1256 «engine» @«CheckResult("engine", "fail"» ; core/ops_monitor.py:55 «FAIL» @«"FAIL"» ; core/ops_monitor.py:1256 «running PyTorch, not the TensorRT engine» @«running PyTorch, not the TensorRT engine - » ; core/ops_monitor.py:985 «restart the app» @«RESTART_HOW = "restart the app"» --> | idem (ligne de santé) | relancer l'app à la pause |
| `engine` `not checked: no model loaded` <!-- src: core/ops_monitor.py:1254 «engine» @«CheckResult("engine", "skip"» ; core/ops_monitor.py:1254 «not checked: no model loaded» --> | le modèle charge | attendre |
| `Using PyTorch (fallback)` ; `TRT engine incompatible — using PyTorch` ; `TensorRT not available` ; `TensorRT is not installed on this system` <!-- src: runtime/model_controller.py:613 «Using PyTorch (fallback)» ; runtime/model_controller.py:679 «TRT engine incompatible — using PyTorch» ; runtime/model_controller.py:283 «TensorRT not available» @«"TensorRT not available"» ; runtime/model_controller.py:310 «TensorRT is not installed on this system» --> | le moteur rapide n'a pas pu charger | relancer l'app ; si ça reste : la version du 8 octobre |
| `No TRT for …px, using PyTorch` ; `No TRT engine for …px — running PyTorch (slower). …` <!-- src: runtime/main_loop.py:868 «px, using PyTorch» @«f"No TRT for {app.settings.imgsz}px, using PyTorch"» ; app.py:2068 «px — running PyTorch (slower).» @«f"No TRT engine for {new_imgsz}px — running PyTorch (slower). "» --> | un réglage de taille d'image a changé (outil expert) | relancer l'app sur le projet ; appeler |
| `Model reload failed (…) — kept the current engine.` <!-- src: runtime/model_controller.py:415 «Model reload failed (» @«f"Model reload failed ({model_to_load}) — kept the current "» ; runtime/model_controller.py:415 «Model» @«f"Model reload failed ({model_to_load}) — kept the current "» ; runtime/model_controller.py:416 «"engine."» --> | un changement de modèle a échoué ; l'ancien reste | rien ; ne plus toucher `Model` <!-- src: gui_builder.py:1603 «Model» @«label="Model"» --> |
| fenêtre `TensorRT Engine` : `No TensorRT engine found for …`, `Build TensorRT (5-10 min)` / `Use PyTorch` <!-- src: gui.py:2776 «TensorRT Engine» ; gui.py:2789 «No TensorRT engine found for » ; gui.py:2800 «Build TensorRT (5-10 min)» ; gui.py:2806 «Use PyTorch» @«label="Use PyTorch"» ; gui.py:2806 «Use PyTorch» @«label="Use PyTorch"» --> | le moteur du modèle demandé n'est pas sur le portable | plus de 15 min avant le spectacle : `Build TensorRT (5-10 min)`, attendre ; sinon `Use PyTorch` puis la version du 8 octobre **[à confirmer avec Thomas]** <!-- src: gui.py:2800 «Build TensorRT (5-10 min)» ; gui.py:2806 «Use PyTorch» @«label="Use PyTorch"» ; gui.py:2806 «Use PyTorch» @«label="Use PyTorch"» --> |
| `net input` `WARN` `the project asks for … but the square … runs` <!-- src: core/ops_monitor.py:199 «net input» @«CheckResult("net input", "warn",» ; core/ops_monitor.py:55 «WARN» @«"WARN"» ; core/ops_monitor.py:200 «the project asks for» @«f"the project asks for {shape_str(requested)} but the square {imgsz} runs"» --> | le moteur « rectangle » du projet manque : la détection voit les danseurs plus petits | relancer l'app ; si ça reste : continuer, le dire au support **[à confirmer avec Thomas]** |
| `[CPU FALLBACK]` ; `/!\ Running on CPU - no GPU acceleration` <!-- src: gui_builder.py:446 «[CPU FALLBACK]» ; runtime/main_loop.py:351 «Running on CPU - no GPU acceleration» --> | la carte graphique n'est pas utilisée | relancer l'app ; si ça reste : appeler (§8.8) |
| `frames` `FAIL` `… frame(s) in a row failed to process - …` `restart the app` <!-- src: core/ops_monitor.py:1247 «frames» @«CheckResult("frames", "fail"» ; core/ops_monitor.py:55 «FAIL» @«"FAIL"» ; core/ops_monitor.py:1247 «frame(s) in a row failed to process - » ; core/ops_monitor.py:985 «restart the app» @«RESTART_HOW = "restart the app"» --> | des images n'ont pas pu être traitées | §6.1 : si rien ne suit plus, relancer tout de suite ; sinon à la pause |
| `… error, frame skipped: …` (alerte) <!-- src: runtime/main_loop.py:483 «"frame_error" if where == "frame processing" else "tick_error"» ; runtime/main_loop.py:484 «error, frame skipped: » --> | une image sautée ; après une longue série, l'app s'arrête seule | noter l'heure ; en boucle : relancer l'app |

### 8.3 L'envoi aux visuels (OSC)

| À l'écran | Ce que ça veut dire | Que faire |
|---|---|---|
| `OSC:` `OFF` ; `osc` `WARN` `disabled: the visuals get no point` `Advanced > OSC > Enable OSC` ; `output` `OSC is off: the visuals get no point - …` <!-- src: gui_builder.py:429 «OSC:» @«"OSC:"» ; gui_builder.py:429 «OSC» @«"OSC:"» ; gui_builder.py:414 «OFF» @«"OFF"» ; core/ops_monitor.py:248 «osc» @«CheckResult("osc", "warn"» ; core/ops_monitor.py:55 «WARN» @«"WARN"» ; core/ops_monitor.py:248 «disabled: the visuals get no point» @«disabled: the visuals get no point - » ; core/ops_monitor.py:241 «Advanced > OSC > Enable OSC» @«OSC_ON_HOW = "Advanced > OSC > Enable OSC"» ; core/ops_monitor.py:241 «Advanced» @«OSC_ON_HOW = "Advanced > OSC > Enable OSC"» ; core/ops_monitor.py:241 «Enable OSC» @«OSC_ON_HOW = "Advanced > OSC > Enable OSC"» ; core/ops_monitor.py:1215 «output» @«CheckResult("output", "warn"» ; core/ops_monitor.py:1215 «OSC is off: the visuals get no point - » --> | l'envoi est coupé : les visuels ne reçoivent rien | `Advanced` → `OSC` → cocher `Enable OSC` ; `Ctrl+S` <!-- src: gui_builder.py:685 «Advanced» @«label="Advanced"» ; gui_builder.py:1569 «OSC» @«label="OSC"» ; gui_builder.py:1572 «Enable OSC» ; gui_builder.py:394 «Ctrl+S» @«Save config (Ctrl+S)» --> |
| `osc` `FAIL` `host reachable but nothing listening on …` / `… unreachable (…)` <!-- src: core/ops_monitor.py:250 «osc» @«CheckResult("osc", status, detail)» ; core/ops_monitor.py:55 «FAIL» @«"FAIL"» ; core/ops_monitor.py:233 «host reachable but nothing listening on » ; core/ops_monitor.py:238 « unreachable (» --> | TouchDesigner n'écoute pas | lancer (ou relancer) TouchDesigner et son patch |
| `output` `FAIL` `OSC sends to … fail (… dropped) - start TouchDesigner and its patch` <!-- src: core/ops_monitor.py:1217 «output» @«CheckResult("output", "fail"» ; core/ops_monitor.py:55 «FAIL» @«"FAIL"» ; core/ops_monitor.py:1217 «f"OSC sends to {target} fail ({errors} dropped) - start "» ; core/ops_monitor.py:1218 «TouchDesigner and its patch» --> | les envois échouent en ce moment | idem, tout de suite |
| `OSC send to … failing (…) - … message(s) dropped; output continues` <!-- src: core/osc_output.py:66 «OSC send to » ; core/osc_output.py:68 «message(s) dropped; output continues» --> | idem (alerte) | idem |
| `osc` `… (UDP - listener cannot be confirmed)` ; `output` `OSC to …` <!-- src: core/ops_monitor.py:250 «osc» @«CheckResult("osc", status, detail)» ; core/ops_monitor.py:236 «(UDP - listener cannot be confirmed)» ; core/ops_monitor.py:1219 «output» @«CheckResult("output", "ok"» ; core/ops_monitor.py:1219 «OSC to» @«f"OSC to {target}"» --> | bon | rien |

### 8.4 Le mur vide, la lumière, l'exposition

| À l'écran | Ce que ça veut dire | Que faire |
|---|---|---|
| `empty wall: none - Calibrate on the empty wall` ; `empty wall` `WARN` `… - Calibrate on the empty wall (2 - Calibrate)` ; `empty wall` `none - …` <!-- src: app.py:1901 «empty wall: none - Calibrate on the empty wall» @«"empty wall: none - Calibrate on the empty wall"» ; app.py:1901 «empty wall» @«"empty wall: none - Calibrate on the empty wall"» ; app.py:1901 «empty wall» @«"empty wall: none - Calibrate on the empty wall"» ; app.py:1901 «none -» @«"empty wall: none - Calibrate on the empty wall"» ; core/ops_monitor.py:304 «CheckResult("empty wall", "ok" if ok else "warn",» ; core/ops_monitor.py:55 «WARN» @«"WARN"» ; core/ops_monitor.py:306 « - Calibrate on the empty wall (2 - Calibrate)» ; core/ops_monitor.py:1237 «CheckResult("empty wall", "warn", f"none - {EMPTY_WALL_HOW}")» ; core/ops_monitor.py:984 «when the wall is empty: » --> | pas de photo du mur vide | dès que le mur est vide : §5.2 jusqu'à `Save to project` <!-- src: gui.py:2949 «Save to project» --> |
| `empty wall: off` ; `empty wall` `off: the dancers' points are not checked - 3 Live > Use empty wall` <!-- src: app.py:1900 «empty wall: off» @«"empty wall: off"» ; app.py:1900 «empty wall» @«"empty wall: off"» ; core/ops_monitor.py:1234 «off: the dancers' points are not checked - 3 Live > Use » --> | la comparaison au mur vide est coupée | à la pause : `3 Live` → cocher `Use empty wall` ; `Ctrl+S` <!-- src: gui_builder.py:643 «3 Live» ; gui_builder.py:1225 «empty wall» @«label="Use empty wall"» ; gui_builder.py:1225 «Use empty wall» @«label="Use empty wall"» ; gui_builder.py:394 «Ctrl+S» @«Save config (Ctrl+S)» --> |
| `empty wall: other camera crop - …` ; `empty wall` `from another camera crop - …` ; `wall light` `the empty-wall snapshot is from another camera crop - …` <!-- src: core/ops_monitor.py:587 «wall light» @«CheckResult("wall light", "skip"» ; core/ops_monitor.py:1239 «empty wall» @«"empty wall", "fail", f"from another camera crop» ; app.py:1903 «empty wall: other camera crop -» @«"empty wall: other camera crop - Calibrate on the empty wall"» ; app.py:1903 «empty wall» @«"empty wall: other camera crop - Calibrate on the empty wall"» ; core/ops_monitor.py:587 «from another camera crop - » ; core/ops_monitor.py:587 «the empty-wall snapshot is from another camera crop - » --> | la photo vient d'un autre cadre de la caméra | à la pause : `CALIBRATE` (§7.2) <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `empty wall: scene changed - …` ; `empty wall` `stale since …` / `no longer matches the wall …` ; `The empty-wall snapshot no longer matches the wall (…)` (alerte) <!-- src: app.py:1904 «empty wall: scene changed -» @«"empty wall: scene changed - Calibrate on the empty wall"» ; app.py:1904 «empty wall» @«"empty wall: scene changed - Calibrate on the empty wall"» ; core/ops_monitor.py:1228 «stale since» @«f"stale since {stale_since}{why} - {EMPTY_WALL_HOW}"» ; core/ops_monitor.py:1241 «no longer matches the wall» @«f"no longer matches the wall{why} - {EMPTY_WALL_HOW}"» ; core/ops_monitor.py:842 «The empty-wall snapshot no longer matches the wall (» @«f"The empty-wall snapshot no longer matches the wall ({plate_stale}): the foreground is not "» --> | la photo est périmée (« plate stale ») ; le message reste jusqu'à une nouvelle photo | pendant la pièce : continuer ; à la pause : §7.2 |
| `empty wall: capturing...` ; `empty wall` `being captured` ; `plate exposure` `the empty wall is being captured now` <!-- src: core/ops_monitor.py:476 «plate exposure» @«name = "plate exposure"» ; core/ops_monitor.py:1232 «empty wall» @«CheckResult("empty wall", "skip", "being captured")» ; app.py:1905 «empty wall: capturing...» @«"empty wall: capturing..."» ; app.py:1905 «empty wall» @«"empty wall: capturing..."» ; core/ops_monitor.py:1232 «being captured» @«"being captured"» ; core/ops_monitor.py:478 «the empty wall is being captured now» --> | la photo se prend (2 s) | ne rien faire, mur vide |
| `empty wall` `not checked (…)` <!-- src: core/ops_monitor.py:1242 «empty wall» @«CheckResult("empty wall", "skip", f"not checked» ; core/ops_monitor.py:1242 «not checked (» @«f"not checked ({status})"» --> | état inconnu | rien ; noter l'heure |
| `Capturing the empty wall (2 s): keep the wall empty` ; `Empty wall captured (dancer-id evidence ready)` <!-- src: app.py:1964 «Capturing the empty wall (2 s): keep the wall empty» ; app.py:1956 «Empty wall captured (dancer-id evidence ready)» --> | la photo se prend / est prise (`Capture empty wall`) <!-- src: gui_builder.py:865 «Capture empty wall» @«label="Capture empty wall"» --> | garder le mur vide 2 s ; puis rien |
| `Empty-wall capture failed: …` <!-- src: app.py:1937 «Empty-wall capture failed:» @«f"Empty-wall capture failed: {error}"» --> | la photo a raté | refaire `CALIBRATE` <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `The light is … the empty-wall snapshot …` `between two pieces, in STANDBY, Calibrate on the empty wall` (alerte) ; `empty wall` `WARN` `… - the light is …` ; `light` `WARN` <!-- src: core/ops_monitor.py:787 «the empty-wall snapshot» @«f"The light is {light_ratio_text(light_ratio)} the empty-wall snapshot{when}: "» ; core/ops_monitor.py:787 «light» @«f"The light is {light_ratio_text(light_ratio)} the empty-wall snapshot{when}: "» ; core/ops_monitor.py:290 «LIGHT_REMEDY = ("between two pieces, in STANDBY, Calibrate» ; core/ops_monitor.py:302 «empty wall» @«f"{detail or 'empty wall: ' + status} - the light is "» ; core/ops_monitor.py:302 «- the light is» @«f"{detail or 'empty wall: ' + status} - the light is "» ; core/ops_monitor.py:55 «WARN» @«"WARN"» ; core/ops_monitor.py:55 «WARN» @«"WARN"» ; core/ops_monitor.py:1280 «CheckResult("light", "warn"» --> | la lumière a changé depuis la photo (reste dans `Alerts:`) <!-- src: gui_builder.py:1274 «Alerts:» @«"Alerts:"» --> | à la pause : §7.2 |
| `wall light` `FAIL` : `aim the IR flood more toward the …`, `move the IR flood back from the wall …`, `the light does not reach the …`, `light the zone with the IR flood` <!-- src: core/ops_monitor.py:591 «wall light» @«CheckResult("wall light", v.status, v.detail)» ; core/ops_monitor.py:55 «FAIL» @«"FAIL"» ; core/light_verdict.py:187 «aim the IR flood more toward the » ; core/light_verdict.py:192 «move the IR flood back from the wall » ; core/light_verdict.py:194 «the light does not reach the » ; core/light_verdict.py:174 «light the zone with the IR flood» --> | la lumière est trop inégale (§5.2) | déplacer le projecteur comme dit, puis `CALIBRATE` <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `wall light` `FAIL` : `the … of the zone is … brighter than the …` ; `the light is a spot in the middle of the zone, …` <!-- src: core/ops_monitor.py:55 «FAIL» @«STATUS_WORDS = » ; core/ops_monitor.py:591 «wall light» @«CheckResult("wall light", v.status» ; core/light_verdict.py:186 «of the zone is» @«brighter than the {dark_at} ({limit})» ; core/light_verdict.py:186 «brighter than the» @«brighter than the {dark_at} ({limit})» ; core/light_verdict.py:190 «the light is a spot in the middle of the zone,» --> | le fait : quelle partie est plus claire que laquelle, et de combien | la consigne qui suit (ligne du dessus) |
| `wall light` `WARN` `… is at white, so its light is at least …` <!-- src: core/ops_monitor.py:587 «wall light» @«"wall light"» ; core/ops_monitor.py:55 «WARN» @«"WARN"» ; core/light_verdict.py:201 «is at white, so its light is at least» --> | partie claire blanche : pas jugeable | refaire `CALIBRATE`, relire <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `wall light` `no empty-wall snapshot to judge the light - …` / `the zone is too small to judge the light - check the ROI` / `not judged (…)` <!-- src: core/ops_monitor.py:587 «wall light» @«"wall light"» ; core/light_verdict.py:151 «no empty-wall snapshot to judge the light - » ; core/light_verdict.py:154 «the zone is too small to judge the light - check the ROI» ; app.py:2437 «not judged (» @«f"not judged ({type(e).__name__}: {e})"» --> | pas de photo, ou zone trop petite | §5.2 ; si la zone est minuscule : §5.1 |
| `Exposure: the light is the limit - …` <!-- src: core/calibration.py:393 «Exposure: the light is the limit - » --> | même au plus bas, la partie claire reste blanche | suivre la consigne qui suit (le projecteur), puis `CALIBRATE` <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `plate exposure` `FAIL` `refused: …` / `… - RUN refuses it: Calibrate on the empty wall` <!-- src: core/ops_monitor.py:476 «plate exposure» @«name = "plate exposure"» ; core/ops_monitor.py:55 «FAIL» @«"FAIL"» ; core/ops_monitor.py:492 «refused:» @«f"refused: {invalid}"» ; core/ops_monitor.py:500 « - RUN refuses it: Calibrate on the empty wall» --> | la photo n'a pas l'exposition, le gain ou le cadre de la caméra : l'app la refuse | à la pause : `CALIBRATE`, `Save to project` <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» ; gui.py:2949 «Save to project» --> |
| `plate exposure` `WARN` `…: the snapshot does not say at which exposure and gain it was taken - …` <!-- src: core/ops_monitor.py:476 «plate exposure» @«name = "plate exposure"» ; core/ops_monitor.py:55 «WARN» @«"WARN"» ; core/ops_monitor.py:502 «the snapshot does not say at which exposure and gain » --> | une ancienne photo | `CALIBRATE` quand le mur est vide <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `plate exposure` `… is not loaded yet (no picture since the project opened)` <!-- src: core/ops_monitor.py:476 «plate exposure» @«name = "plate exposure"» ; core/ops_monitor.py:480 «is not loaded yet (no picture since » --> | pas encore d'image | vérifier la caméra ; attendre |
| `plate exposure` `no empty-wall snapshot to compare - …` <!-- src: core/ops_monitor.py:476 «plate exposure» @«name = "plate exposure"» ; core/ops_monitor.py:483 «no empty-wall snapshot to compare - » --> | pas de photo | §5.2 |
| `plate exposure` `…: not compared` <!-- src: core/ops_monitor.py:476 «plate exposure» @«name = "plate exposure"» ; core/ops_monitor.py:508 «: not compared» @«picture, auto, or not an IDS camera): not compared» --> | la caméra ne dit pas son exposition (pas d'image, ou pas la caméra IDS) | vérifier `[IDS]` <!-- src: gui.py:1862 «[IDS]» @«"[IDS]"» --> |

### 8.5 Le cadre, l'image, les danseurs

| À l'écran | Ce que ça veut dire | Que faire |
|---|---|---|
| `ROI` `WARN` `off: YOLO watches the whole … - draw the dancers' wall (1 Rig > Region of Interest)` <!-- src: core/ops_monitor.py:446 «ROI» @«CheckResult("ROI", "warn", f"off: YOLO watches the whole {where}» ; core/ops_monitor.py:55 «WARN» @«"WARN"» ; core/ops_monitor.py:447 «(1 Rig > Region of Interest)» --> | pas de zone : l'app regarde toute l'image | §5.1 ; puis `CALIBRATE` <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `ROI` `FAIL` `the zone is empty - …` <!-- src: core/ops_monitor.py:449 «the zone is empty - » @«CheckResult("ROI", "fail", "the zone is empty» ; core/ops_monitor.py:449 «ROI» @«CheckResult("ROI", "fail", "the zone is empty» ; core/ops_monitor.py:55 «FAIL» @«"FAIL"» --> | zone vide | §5.1 ; puis `CALIBRATE` <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `ROI` `WARN` `… it may frame another part of the wall - check it on the picture (1 Rig)` <!-- src: core/ops_monitor.py:460 «ROI» @«CheckResult("ROI", "warn", f"zone {zone} drawn» ; core/ops_monitor.py:55 «WARN» @«"WARN"» ; core/ops_monitor.py:461 «may frame another part of the wall - check it on the picture (1 Rig)» --> | le cadre de la caméra a changé depuis le tracé | regarder la zone sur l'image ; la retracer si elle a glissé ; puis `CALIBRATE` <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `ROI` `FAIL` `…, cut to … by the camera's …: draw it again (1 Rig > Region of Interest), then Calibrate` <!-- src: core/ops_monitor.py:463 «ROI» @«CheckResult("ROI", "fail", f"zone {zone} drawn» ; core/ops_monitor.py:55 «FAIL» @«"FAIL"» ; core/ops_monitor.py:464 «draw it again (1 Rig > Region of Interest), then Calibrate» --> | la zone dépasse le cadre de la caméra | retracer la zone (§5.1), puis `CALIBRATE` <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `ROI` `NOT CHECKED` `…; no picture now, so not checked against the camera's crop` <!-- src: core/ops_monitor.py:453 «ROI» @«CheckResult("ROI", "skip"» ; core/ops_monitor.py:55 «NOT CHECKED» @«"NOT CHECKED"» ; core/ops_monitor.py:454 «checked against the camera's crop» --> | pas d'image en ce moment | vérifier la caméra |
| `points` `FAIL` `… had no point for … s …` <!-- src: core/ops_monitor.py:981 «points» @«POINTS_ROW = "points"» ; core/ops_monitor.py:1127 «CheckResult(POINTS_ROW, "fail"» ; core/ops_monitor.py:55 «FAIL» @«"FAIL"» ; core/ops_monitor.py:1127 «had no point for » --> | une forme sur le mur n'a pas de boule depuis plus de 3 s | un danseur : `Max dancers` au moins égal au nombre de danseurs ; une ombre : rien (§6.1) <!-- src: gui_builder.py:1128 «Max dancers» --> |
| `points` `… on the wall, … point…: none without a point for more than … s` ; `points` `not checked: …` <!-- src: core/ops_monitor.py:981 «points» @«POINTS_ROW = "points"» ; core/ops_monitor.py:1130 «CheckResult(POINTS_ROW, "ok"» ; core/ops_monitor.py:1131 «none without a point for more than » ; core/ops_monitor.py:1119 «not checked:» @«CheckResult(POINTS_ROW, "skip", "not checked: no new frame processed")» ; core/ops_monitor.py:1117 «f"not checked: {self._why}"» --> | bon ; ou pas vérifiable (`STANDBY`, pas de photo) <!-- src: gui_builder.py:1034 «STANDBY» @«label="STANDBY"» --> | rien |
| `No dancers tracked for …s while RUNNING` (alerte) ; `no dancer` `WARN` <!-- src: core/ops_monitor.py:726 «No dancers tracked for » ; core/ops_monitor.py:727 «while RUNNING» ; core/ops_monitor.py:1283 «no dancer» @«CheckResult("no dancer", "warn"» ; core/ops_monitor.py:55 «WARN» @«"WARN"» --> | personne n'est suivi depuis 30 s | des danseurs sur le mur : vérifier l'IR et l'image ; sinon rien |
| `More people than max_persons visible - …` (alerte) ; `over cap` `WARN` <!-- src: core/ops_monitor.py:741 «More people than max_persons visible - » ; core/ops_monitor.py:1287 «over cap» @«CheckResult("over cap", "warn"» ; core/ops_monitor.py:55 «WARN» @«"WARN"» --> | plus de silhouettes que `Max dancers` <!-- src: gui_builder.py:1128 «Max dancers» --> | vérifier `Max dancers` ; si c'est juste, rien <!-- src: gui_builder.py:1128 «Max dancers» --> |
| `Person height calibration looks stale - …` (alerte) ; `height` `WARN` <!-- src: core/ops_monitor.py:763 «Person height calibration looks stale -» @«Person height calibration looks stale - live median » ; core/ops_monitor.py:763 «height» @«Person height calibration looks stale - live median » ; core/ops_monitor.py:1291 «CheckResult("height", "warn"» ; core/ops_monitor.py:55 «WARN» @«"WARN"» --> | la taille des danseurs ne correspond plus | à la pause : étape ③ (§5.3) |
| `Person height … -> … px (measured on the dancers)` <!-- src: runtime/main_loop.py:1310 «px (measured on the dancers)» @«f"Person height {old_h} -> {new_h} px (measured on the dancers)"» --> | l'app a corrigé la taille seule | rien |
| `dancer size` `NOT CHECKED` `not measured - …` <!-- src: core/ops_monitor.py:326 «dancer size» @«CheckResult("dancer size", "skip",» ; core/ops_monitor.py:55 «NOT CHECKED» @«"NOT CHECKED"» ; core/ops_monitor.py:327 «not measured -» @«not measured - have a dancer stand at the far wall for 10 s, then check again» --> | pas de danseur mesuré depuis 30 min | étape ③ (§5.3) |
| `dancer size` `WARN` `…, below the ~… px YOLO needs: …` <!-- src: core/ops_monitor.py:343 «dancer size» @«CheckResult("dancer size", "warn", f"{detail}, below the» ; core/ops_monitor.py:55 «WARN» @«"WARN"» ; core/ops_monitor.py:343 « px YOLO needs: » --> | les danseurs sont trop petits pour la détection | resserrer la zone sur le mur dansé (§5.1), puis ② et ③ ; le jour même : demander d'abord **[à confirmer avec Thomas]** |
| `enhancement` `FAIL` `OFF, but Calibrate chose … for this project (…): turn it on - …` `Ctrl+Shift+E, Advanced > Enhancement > Enable` <!-- src: core/ops_monitor.py:417 «enhancement» @«CheckResult("enhancement", "fail",» ; core/ops_monitor.py:55 «FAIL» @«"FAIL"» ; core/ops_monitor.py:418 «OFF, but Calibrate chose» @«f"OFF, but Calibrate chose {which} for this project ({calibrated}): turn it on - "» ; core/ops_monitor.py:402 «Ctrl+Shift+E, Advanced > Enhancement > Enable» @«ENHANCE_ON_HOW = "Ctrl+Shift+E, Advanced > Enhancement > Enable"» --> | l'amélioration d'image est éteinte : la détection voit moins bien | à la pause : les cinq gestes ci-dessous |
| `enhancement` `WARN` `OFF, and Calibrate never chose one for this project …` <!-- src: core/ops_monitor.py:422 «enhancement» @«CheckResult("enhancement", "warn",» ; core/ops_monitor.py:55 «WARN» @«"WARN"» ; core/ops_monitor.py:423 «OFF, and Calibrate never chose one for this project» --> | éteinte, et jamais choisie | `CALIBRATE`, puis les cinq gestes **[à confirmer avec Thomas]** <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `enhancement` `WARN` `on (…), … - Calibrate on the empty wall` (`not chosen by Calibrate on this project`) <!-- src: core/ops_monitor.py:433 «enhancement» @«CheckResult("enhancement", "warn", f"on ({which}), {by} - Calibrate on the empty wall")» ; core/ops_monitor.py:433 «- Calibrate on the empty wall» @«CheckResult("enhancement", "warn", f"on ({which}), {by} - Calibrate on the empty wall")» ; core/ops_monitor.py:55 «WARN» @«"WARN"» ; core/ops_monitor.py:425 «not chosen by Calibrate on this project» --> | allumée, mais pas réglée par `CALIBRATE` <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> | `CALIBRATE` quand le mur est vide <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `enhancement` `WARN` `… but faded out: … - tick Force (expert mode, Advanced > Enhancement)` <!-- src: core/ops_monitor.py:422 «enhancement» @«CheckResult("enhancement", "warn",» ; core/ops_monitor.py:55 «WARN» @«"WARN"» ; core/ops_monitor.py:431 «tick Force (expert mode, Advanced > Enhancement)» --> | effacée par la luminosité | appeler (§8.8) **[à confirmer avec Thomas]** |
| `enhancement` `off; Calibrate chose none here …` ; `on, neutral (…)` <!-- src: core/ops_monitor.py:421 «enhancement» @«CheckResult("enhancement", "ok", f"off; Calibrate» ; core/ops_monitor.py:421 «off; Calibrate chose none here» ; core/ops_monitor.py:427 «on, neutral (» @«f"on, neutral ({which}, {by})» --> | bon | rien |
| `ENHANCEMENT OFF - run Calibrate (or tick it in expert mode)` (en rouge dans `3 Live`) <!-- src: gui_builder.py:1639 «ENHANCEMENT OFF - run Calibrate (or tick it in expert mode)» ; gui_builder.py:643 «3 Live» --> | l'amélioration d'image est éteinte | lire la ligne `enhancement` du bilan, et faire son action <!-- src: core/ops_monitor.py:417 «enhancement» @«CheckResult("enhancement"» --> |

**Allumer l'amélioration d'image, les cinq gestes** (à la pause) :

1. `Ctrl+Shift+E` : le message dit `Expert mode ON`. <!-- src: core/ops_monitor.py:402 «Ctrl+Shift+E» ; gui.py:528 «Expert mode ON» @«"Expert mode ON"» -->
2. `Advanced` → bloc `Enhancement` → cocher `Enable`. <!-- src: gui_builder.py:685 «Advanced» @«label="Advanced"» ; gui_builder.py:1644 «Enhancement» @«label="Enhancement"» ; gui_builder.py:1651 «Enable» @«label="Enable"» -->
3. Fermer la fenêtre `Advanced  (numeric knobs)`. <!-- src: gui_builder.py:1290 «Advanced  (numeric knobs)» -->
4. `Ctrl+Shift+E` : `Expert mode OFF`. <!-- src: core/ops_monitor.py:402 «Ctrl+Shift+E» ; gui.py:528 «Expert mode OFF» @«"Expert mode OFF"» -->
5. `Ctrl+S`, puis `Check readiness` : la ligne `enhancement` doit passer `OK`. <!-- src: core/ops_monitor.py:417 «enhancement» @«CheckResult("enhancement", "fail"» ; gui_builder.py:394 «Ctrl+S» @«Save config (Ctrl+S)» ; gui_builder.py:876 «Check readiness» ; core/ops_monitor.py:55 «OK» @«"OK"» -->

### 8.6 CALIBRATE, le projet, l'enregistrement, le disque

| À l'écran | Ce que ça veut dire | Que faire |
|---|---|---|
| `Someone is in view, … Calibrate stopped and changed nothing. …` ; `Stopped: someone in view, …` <!-- src: runtime/calibration_flows.py:655 «Someone is in view, » ; runtime/calibration_flows.py:655 «Calibrate stopped and changed nothing. » ; runtime/calibration_flows.py:661 «Stopped: someone in view, » --> | quelqu'un dans l'image (§5.2) | vider le mur à l'endroit dit, `CALIBRATE` <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `YOLO now and then sees a person on the EMPTY wall, even at the weakest setting: …` <!-- src: runtime/calibration_flows.py:575 «YOLO now and then sees a person on the EMPTY wall, even at the weakest » --> | un objet ressemble à une personne | l'enlever ou sortir la zone de là ; `CALIBRATE` <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `Calibrate: scene too noisy for MOG2 (varThreshold saturated) …` <!-- src: runtime/calibration_flows.py:448 «Calibrate: scene too noisy for MOG2 (varThreshold saturated) » --> | l'image est trop bruitée : les fantômes sont mal refusés | éclairer le mur plus également (IR), `CALIBRATE` ; si ça reste : continuer, le dire au support <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> |
| `Calibration cancelled` <!-- src: runtime/calibration_flows.py:239 «Calibration cancelled» --> | un 2e appui a annulé ; tout est revenu | rappuyer une seule fois si besoin |
| `Load a model before calibrating` <!-- src: runtime/calibration_flows.py:245 «Load a model before calibrating» --> | le modèle charge encore | attendre, rappuyer |
| `Start the camera or play a recording first` <!-- src: runtime/calibration_flows.py:255 «Start the camera or play a recording first» --> | pas d'image | `CAM:` `ON`, `LIVE`, rappuyer <!-- src: gui_builder.py:413 «CAM:» @«"CAM:"» ; gui.py:1855 «ON» @«"ON" if camera_running» ; gui_builder.py:1320 «LIVE» @«label="LIVE"» --> |
| `Calibrating - driving exposure/gain, keep the stage clear` ; `Calibrating scene - keep the stage clear` <!-- src: runtime/calibration_flows.py:284 «Calibrating - driving exposure/gain, keep the stage clear» ; runtime/calibration_flows.py:311 «Calibrating scene - keep the stage clear» --> | CALIBRATE travaille | garder la scène vide |
| `Finish or cancel the calibration before changing Mirror/Rotate` <!-- src: app.py:1129 «Finish or cancel the calibration before changing Mirror/Rotate» --> | on a touché `Rotate` pendant CALIBRATE <!-- src: gui_builder.py:1796 «Rotate» @«"Rotate"» --> | attendre la fin de CALIBRATE |
| `Mirror/Rotate: … - ROI and mask moved with the image (Save to keep)` ; `. Re-run Calibrate: heights were measured sideways` <!-- src: app.py:1178 «f"Mirror/Rotate: {t.label()} - ROI and mask moved with the "» ; app.py:1179 «image (Save to keep)» ; app.py:1181 «. Re-run Calibrate: heights were measured sideways» --> | la rotation ou le miroir a changé | si c'est une erreur : remettre comme avant ; sinon `CALIBRATE`, `Save to project` <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» ; gui.py:2949 «Save to project» --> |
| `Previous session crashed (…): … - details in logs/…` <!-- src: services/crash_marker.py:174 «Previous session crashed (» ; services/crash_marker.py:175 «- details in logs/» --> | la séance d'avant s'est arrêtée anormalement | noter l'heure ; continuer |
| `Go-Live check: …` <!-- src: core/ops_monitor.py:127 «Go-Live check: » --> | le résumé du bilan | lire les lignes (§5.4) |
| `readiness` `check error: …` ; `… not checked (…)` <!-- src: app.py:2469 «readiness» @«"name": "readiness", "status": "fail"» ; app.py:2470 «check error:» @«f"check error: {e}"» ; app.py:2571 «not checked (» @«f"not checked ({type(e).__name__}: {e})"» --> | le bilan n'a pas pu lire une ligne | `Check readiness` à nouveau ; si ça reste : continuer, noter l'heure <!-- src: gui_builder.py:876 «Check readiness» --> |
| `calibration` `WARN` `no saved config found …` / `… - consider recalibrating after a re-rig` / `unreadable save timestamp …` <!-- src: core/ops_monitor.py:260 «calibration» @«CheckResult("calibration", "warn",» ; core/ops_monitor.py:55 «WARN» @«"WARN"» ; core/ops_monitor.py:261 «no saved config found» @«f"no saved config found (profile: {active_profile})"» ; core/ops_monitor.py:273 «consider recalibrating after a re-rig» ; core/ops_monitor.py:267 «unreadable save timestamp» @«f"unreadable save timestamp {saved_at_iso!r}"» --> | le projet n'a pas été enregistré depuis 24 h | après la mise en place : `Ctrl+S` ; si le montage a bougé : ② <!-- src: gui_builder.py:394 «Ctrl+S» @«Save config (Ctrl+S)» --> |
| `Save FAILED: …` <!-- src: runtime/config_manager.py:398 «Save FAILED:» @«f"Save FAILED: {e}"» --> | l'enregistrement a raté | `Ctrl+S` à nouveau ; si ça rate encore : noter l'heure (le réglage reste actif jusqu'à la fermeture) <!-- src: gui_builder.py:394 «Ctrl+S» @«Save config (Ctrl+S)» --> |
| `Could not load … (unreadable) - pick another save` <!-- src: runtime/config_manager.py:189 «f"Could not load {os.path.basename(config_filepath)} "» ; runtime/config_manager.py:190 «(unreadable) - pick another save» --> | une sauvegarde du projet est abîmée | appeler (§8.8) ; ou `Config version` : la sauvegarde d'avant **[à confirmer avec Thomas]** <!-- src: gui_builder.py:780 «Config version» @«"Config version"» --> |
| `Config: … value(s) adjusted on load - see console` <!-- src: runtime/config_manager.py:610 «value(s) adjusted on load - see console» --> | l'app a corrigé des réglages à l'ouverture | rien |
| `disk` `WARN` / `FAIL` `… GB free for recordings …` ; `disk` `could not stat …` <!-- src: core/ops_monitor.py:382 «disk» @«CheckResult("disk", "warn", detail)» ; core/ops_monitor.py:382 «disk» @«CheckResult("disk", "warn", detail)» ; core/ops_monitor.py:380 «CheckResult("disk", "fail", detail)» ; core/ops_monitor.py:55 «WARN» @«"WARN"» ; core/ops_monitor.py:55 «FAIL» @«"FAIL"» ; core/ops_monitor.py:377 « GB free for recordings» ; core/ops_monitor.py:373 «could not stat» @«f"could not stat {recordings_dir} ({e})"» --> | peu de place sur le disque (moins de 60 Go, rouge sous 10 Go) | ne plus enregistrer (`REC`) <!-- src: gui_builder.py:1327 «REC» @«label="REC"» --> |
| `Preview OFF: playback keeps running but the image will not update` ; `Preview ON` <!-- src: app.py:2289 «Preview OFF: playback keeps running but the image will not update» ; app.py:2260 «Preview ON» @«"Preview ON"» --> | l'image à l'écran est arrêtée / reprend (le suivi continue) | touche P pour la rendre |
| `ROI edit mode: drag on preview` ; `ROI edit mode: off` <!-- src: ui/roi_mask_editor.py:674 «ROI edit mode: drag on preview» ; ui/roi_mask_editor.py:674 «ROI edit mode: off» --> | un double-clic sur l'image a ouvert ou fermé le tracé de la zone | pendant une pièce : double-cliquer pour fermer, ne rien tirer |
| `Mask edit: click/drag preview cells` ; `Mask edit: off` ; `Exclusion mask cleared (auto + manual)` <!-- src: ui/roi_mask_editor.py:585 «Mask edit: click/drag preview cells» ; ui/roi_mask_editor.py:586 «Mask edit: off» @«"Mask edit: off"» ; ui/roi_mask_editor.py:597 «Exclusion mask cleared (auto + manual)» --> | le masque s'édite / est effacé (§5.1) | si c'est une erreur : refaire le masque (§5.1) |
| `Web monitor is off (set WEB_MONITOR_ENABLED=True)` <!-- src: app.py:879 «Web monitor is off (set WEB_MONITOR_ENABLED=True)» --> | l'aide téléphone n'est pas disponible | faire la mise au point en regardant l'écran |
| `Expert mode ON` / `Expert mode OFF` <!-- src: gui.py:528 «Expert mode ON» @«"Expert mode ON"» ; gui.py:528 «Expert mode OFF» @«"Expert mode OFF"» --> | `Ctrl+Shift+E` a été tapé <!-- src: core/ops_monitor.py:402 «Ctrl+Shift+E» --> | `Ctrl+Shift+E` à nouveau si ce n'était pas voulu <!-- src: core/ops_monitor.py:402 «Ctrl+Shift+E» --> |
| `NOT CHECKED - the health line starts with the app's first second` <!-- src: gui_builder.py:1244 «NOT CHECKED - the health line starts with the app's first second» --> | l'app démarre | attendre une seconde |
| `REMOTE` ; `REMOTE*` <!-- src: gui_builder.py:433 «REMOTE» @«add_text("REMOTE", tag="badge_remote"» ; gui.py:971 «REMOTE*» --> | une session du support est connectée ; avec l'étoile, elle peut aussi agir en `RUN` (§9) <!-- src: gui_builder.py:1045 «RUN» @«label="RUN"» --> | rien ; le 12/12 : ne pas cocher `Allow remote control during RUN` <!-- src: gui_builder.py:1100 «Allow remote control during RUN» --> |
| `Scene Calibration` <!-- src: gui.py:2929 «Scene Calibration» --> | la carte du résultat de `CALIBRATE` (§5.2) <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» --> | la lire, puis `Save to project` <!-- src: gui.py:2949 «Save to project» --> |
| `WallDance - Select Project` <!-- src: gui.py:2347 «WallDance - Select Project» --> | la fenêtre de choix du projet, au démarrage | le projet de la séance, `Launch` (§3) <!-- src: gui.py:2359 «Launch» @«label="Launch"» --> |
| `Model Loading` <!-- src: gui.py:2696 «Model Loading» --> | le modèle de détection se charge | attendre qu'elle se ferme |
| `MESSAGE DU SUPPORT` <!-- src: gui_builder.py:520 «MESSAGE DU SUPPORT» --> | un message du support (§9) | le lire, `J'ai lu` <!-- src: gui_builder.py:521 «J'ai lu» --> |

### 8.7 Les messages des outils experts

Nico n'utilise pas ces outils. Si un de ces messages apparaît, quelqu'un a cliqué dans `Advanced`, `IMPORT`, `ISSUE` ou la fenêtre de choix du projet. **Ne rien faire de plus**, fermer la fenêtre ouverte, revenir à `LIVE`, puis `RUN`. <!-- src: gui_builder.py:685 «Advanced» @«label="Advanced"» ; gui_builder.py:1347 «IMPORT» @«label="IMPORT"» ; gui_builder.py:1397 «ISSUE» @«label="ISSUE"» ; gui_builder.py:1320 «LIVE» @«label="LIVE"» ; gui_builder.py:1045 «RUN» @«label="RUN"» -->

| À l'écran | L'outil |
|---|---|
| `Dry-run: replaying the last recording...` ; `Dry-run runs in STANDBY (stop the show first).` ; `Dry-run: … frames, …` ; `… live Mirror/Rotate (…) differs from the saved config …` <!-- src: app.py:2588 «Dry-run: replaying the last recording...» ; app.py:2582 «Dry-run runs in STANDBY (stop the show first).» ; app.py:2663 «Dry-run:» @«f"Dry-run: {summary.get('frames_processed', '?')} frames, "» ; app.py:2611 «live Mirror/Rotate (» @«: live Mirror/Rotate (» ; app.py:2612 «from the saved config (» --> | relecture d'essai |
| `Auto-tune: sweeping CLAHE x confidence (~2-4 min)...` ; `Auto-tune runs in STANDBY (stop the show first).` ; `Auto-tune done: …` ; `Auto-tune applied: …` ; `No auto-tune result to apply yet - run it first.` <!-- src: app.py:2687 «Auto-tune: sweeping CLAHE x confidence (~2-4 min)...» ; app.py:2681 «Auto-tune runs in STANDBY (stop the show first).» ; app.py:2746 «Auto-tune done:» @«f"Auto-tune done: CLAHE » ; app.py:2797 «Auto-tune applied:» @«f"Auto-tune applied: CLAHE » ; app.py:2765 «No auto-tune result to apply yet - run it first.» --> | réglage automatique |
| `Known-N tune: searching vs known counts (several min)...` ; `Known-N tune runs in STANDBY (stop the show first).` ; `Known-N done: …` ; `Known-N applied + saved (…).` ; `No known-N result to apply yet — run it first.` <!-- src: app.py:2817 «Known-N tune: searching vs known counts (several min)...» ; app.py:2811 «Known-N tune runs in STANDBY (stop the show first).» ; app.py:2845 «Known-N done:» @«f"Known-N done: » ; app.py:2877 «Known-N applied + saved (» @«f"Known-N applied + saved (» ; app.py:2862 «No known-N result to apply yet — run it first.» --> | réglage par comptage |
| `Dancer calibration - have 1-4 dancers move around` ; `Dancer calibration cancelled` ; `Scene calibration is running` ; `Cleared … run(s)` ; `Dancer calibration (pooled)` <!-- src: runtime/calibration_flows.py:858 «Dancer calibration - have 1-4 dancers move around» ; runtime/calibration_flows.py:825 «Dancer calibration cancelled» ; runtime/calibration_flows.py:831 «Scene calibration is running» ; runtime/calibration_flows.py:1078 «Cleared » @«f"Cleared {removed} run(s)"» ; runtime/calibration_flows.py:1071 «Dancer calibration (pooled)» --> | ancienne calibration avec danseurs |
| `Drops<->Ghosts dial re-anchored (conf …)` ; `Gap-bridging dial re-anchored` <!-- src: app.py:1777 «Drops<->Ghosts dial re-anchored (conf» @«f"Drops<->Ghosts dial re-anchored (conf {value:.2f})"» ; app.py:1825 «Gap-bridging dial re-anchored» @«"Gap-bridging dial re-anchored"» --> | réglages de détection bruts |
| `Lighting profile: …` <!-- src: runtime/config_manager.py:387 «Lighting profile:» @«f"Lighting profile: {name.upper()}"» --> | anciens profils d'éclairage |
| `New project: give it a name` ; `'…' already exists - pick another name` ; `New project: camera rig from '…' (…)` ; `Rename failed - name already in use?` ; `Could not delete '…'` <!-- src: runtime/config_manager.py:489 «New project: give it a name» ; runtime/config_manager.py:495 «' already exists - pick another name» ; runtime/config_manager.py:588 «New project: camera rig from '» @«f"New project: camera rig from '{name}' ("» ; runtime/config_manager.py:588 «'» @«f"New project: camera rig from '{name}' ("» ; runtime/config_manager.py:465 «Rename failed - name already in use?» ; runtime/config_manager.py:477 «f"Could not delete '{name}'"» --> | création, renommage, suppression de projet : **ne pas toucher** |
| `Switch to STANDBY to import a video` ; les messages de `IMPORT` <!-- src: gui.py:1053 «Switch to STANDBY to import a video» ; gui_builder.py:1347 «IMPORT» @«label="IMPORT"» --> | import d'une vidéo |
| `An import is already running - wait for it to finish` ; `Stop the recording before importing a video` ; `Import refused: …` ; `Importing … into slot … (…)...` ; `Import slot …: …% (…)` ; `Import failed: …` ; `Slot …: … (… frames @ …` <!-- src: runtime/recording_controller.py:611 «An import is already running - wait for it to finish» ; runtime/recording_controller.py:615 «Stop the recording before importing a video» ; runtime/recording_controller.py:621 «Import refused: » ; runtime/recording_controller.py:625 «Importing » @«into slot {slot} ({how})...» ; runtime/recording_controller.py:645 «Import slot » @«{fraction * 100:.0f}%» ; runtime/recording_controller.py:652 «Import failed: » ; runtime/recording_controller.py:660 «frames @ » @«({result.frames} frames @» --> | import d'une vidéo (`IMPORT`) <!-- src: gui_builder.py:1347 «IMPORT» @«label="IMPORT"» --> |
| `Issue saved: slot … frame …` ; `Issue reporting is available during playback only` <!-- src: app.py:2229 «Issue saved: slot» @«f"Issue saved: slot {slot_num} frame {frame_num}"» ; app.py:2140 «Issue reporting is available during playback only» --> | signalement d'un problème (F8, `ISSUE`) <!-- src: gui_builder.py:1397 «ISSUE» @«label="ISSUE"» --> |
| `Review mode: slot …` ; `Paused at frame …` <!-- src: runtime/recording_controller.py:296 «Review mode: slot» @«f"Review mode: slot {opts.slot}"» ; runtime/recording_controller.py:322 «Paused at frame» @«f"Paused at frame {target}"» --> | relecture lancée par le support |

### 8.8 Les secours, dans l'ordre

1. **Relancer l'app** (§7.4) : fermer, relancer, le même projet, `Launch`, lire le bilan, `RUN`. <!-- src: gui.py:2359 «Launch» @«label="Launch"» ; gui_builder.py:1045 «RUN» @«label="RUN"» -->
2. **La version du 8 octobre** (§3) : fermer la version de répétition d'abord, puis l'icône `WallDance`, mêmes étapes ① ② ④ ⑤. <!-- src: launcher/install_manager.py:27 «WallDance» -->
3. **Appeler** :
   - 7–8/12 : le support à distance (§9) et Thomas au téléphone **[à compléter le 2/12 : le numéro, sur papier]**.
   - 12/12 : personne ne répond. Ce guide et la fiche sont tout.
4. **Toujours noter l'heure** de chaque souci et de chaque relance.

<div style="break-before: page"></div>

## 9. Le support à distance

### Les 7 et 8/12

- Une session sur l'ordinateur de Thomas regarde le portable à distance. Nico est d'accord.
- Elle lit, toutes les 30 secondes environ : l'état de l'app, la ligne de santé, les alertes, une petite photo de l'image de la caméra.
- Quand elle est connectée, `REMOTE` s'allume en haut. <!-- src: gui_builder.py:433 «REMOTE» @«"REMOTE"» -->
- Dans `3 Live`, bloc `Remote` : `remote: idle` (personne) ou `remote: connected …`. <!-- src: gui_builder.py:643 «3 Live» ; gui_builder.py:1099 «Remote» @«add_text("Remote"» ; gui_builder.py:1108 «remote: idle» @«"remote: idle"» ; gui.py:975 «remote: connected» -->

### Le message du support

- Une bande **jaune** au-dessus de l'image : `MESSAGE DU SUPPORT`, l'heure (`reçu à …`), le texte, et un bouton `J'ai lu`. <!-- src: gui_builder.py:520 «MESSAGE DU SUPPORT» ; gui.py:1008 «reçu à » ; gui_builder.py:521 «J'ai lu» -->
- Le message reste jusqu'au clic sur `J'ai lu`. <!-- src: gui_builder.py:521 «J'ai lu» -->
- **Le lire, faire ce qu'il dit si c'est possible, cliquer `J'ai lu`.** Le support voit alors qu'il est lu. <!-- src: gui_builder.py:521 «J'ai lu» -->
- Un deuxième message attend son tour : `encore … message… après celui-ci`. Il vient après le clic. <!-- src: gui.py:1010 «f" - encore {waiting} message» ; gui.py:1010 «après celui-ci» -->
- Un clic dans la seconde où un nouveau message arrive ne compte pas : un double-clic ne fait pas sauter un message.
- Le spectacle continue pendant ce temps : la bande n'arrête rien, ne cache ni le panneau ni la ligne de santé.
- Rien à cocher pour recevoir les messages.
- Seul Nico peut dire `J'ai lu` : le support ne peut pas le faire à sa place. <!-- src: gui_builder.py:521 «J'ai lu» -->

*[capture d'écran : la bande jaune MESSAGE DU SUPPORT au-dessus de l'image, avec le bouton J'ai lu]*

### Ce que le support peut faire, et ne peut pas faire

| Le support peut | Le support ne peut pas |
|---|---|
| lire l'état, la ligne de santé, les alertes, les journaux | voir la vidéo en continu (seulement des petites photos, la 4G est lente) |
| écrire un message sur l'écran | cliquer `J'ai lu` à la place de Nico <!-- src: gui_builder.py:521 «J'ai lu» --> |
| en `STANDBY` : lancer le bilan, enregistrer, régler <!-- src: gui_builder.py:1034 «STANDBY» @«label="STANDBY"» --> | en `RUN` : changer quoi que ce soit, **sauf** si `Allow remote control during RUN` est coché sur le portable (alors `REMOTE*`) <!-- src: gui_builder.py:1045 «RUN» @«label="RUN"» ; gui_builder.py:1100 «Allow remote control during RUN» ; gui.py:971 «REMOTE*» @«"REMOTE*"» --> |
| — | supprimer ou renommer un projet : jamais ; quitter l'app : seulement si la version a été lancée pour le permettre **[à confirmer avec Thomas]** |
| — | faire les gros calculs (réglages automatiques, changement de modèle) en `RUN` : jamais <!-- src: gui_builder.py:1045 «RUN» @«label="RUN"» --> |

- `Allow remote control during RUN` se coche sur le portable, par Nico ; personne ne peut le cocher à distance. Selon la façon dont la version de répétition est lancée, il peut être déjà coché au démarrage (`REMOTE*` dès que le support se connecte). **[à confirmer avec Thomas : le cocher ou non, et comment l'app est lancée]** <!-- src: gui.py:971 «REMOTE*» ; gui_builder.py:1100 «Allow remote control during RUN» -->
- Thomas est joignable par téléphone, à temps partiel. Le support prépare chaque message ; Thomas dit oui avant l'envoi.

### Le 12/12

- **Personne derrière.** Pas de message du support : le projet du 12/12 n'a pas la bande des messages.
- Un enregistreur silencieux peut tourner chez Thomas : il garde l'état et des photos pour plus tard ; il ne contacte personne et n'agit sur rien **[à confirmer avec Thomas : en marche ou non]**.
- Un souci : sa ligne au §6 ou au §8. Rien ne va : relancer (§7.4). Ne repart pas : la version de secours (§8.8).

<div style="break-before: page"></div>

## 10. Lexique

Les mots anglais de l'écran, en français.

| À l'écran | En français |
|---|---|
| `Project:` | projet <!-- src: gui_builder.py:375 «Project:» @«"Project:"» --> |
| `Save config (Ctrl+S)` | enregistrer le réglage <!-- src: gui_builder.py:394 «Ctrl+S» @«Save config (Ctrl+S)» --> |
| `Config version` | version du réglage (les sauvegardes du projet) <!-- src: gui_builder.py:780 «Config version» @«"Config version"» --> |
| `RUN` | en marche : détection et envoi aux visuels <!-- src: gui_builder.py:1045 «RUN» @«label="RUN"» --> |
| `STANDBY` | en attente : image seule, rien n'est envoyé <!-- src: gui_builder.py:1034 «STANDBY» @«label="STANDBY"» --> |
| `CAM:` | caméra <!-- src: gui_builder.py:413 «CAM:» @«"CAM:"» --> |
| `ON` / `OFF` | marche / arrêt <!-- src: gui.py:1855 «ON» @«"ON" if camera_running» ; gui_builder.py:414 «OFF» @«"OFF"» --> |
| `RECONNECTING` | reconnexion en cours <!-- src: gui_builder.py:423 «RECONNECTING» @«"RECONNECTING"» --> |
| `OSC:` | l'envoi des positions aux visuels <!-- src: gui_builder.py:429 «OSC:» @«"OSC:"» --> |
| `REMOTE` | à distance : une session du support est connectée <!-- src: gui_builder.py:433 «REMOTE» @«"REMOTE"» --> |
| `[TRT]` / `[PT]` | moteur rapide (normal) / moteur lent (anormal) <!-- src: gui.py:2078 «[TRT]» @«"[TRT]"» ; gui_builder.py:442 «[PT]» @«"[PT]"» --> |
| `[CPU FALLBACK]` | secours sur le processeur : pas de carte graphique <!-- src: gui_builder.py:446 «[CPU FALLBACK]» --> |
| `FPS:` | images par seconde <!-- src: gui_builder.py:451 «FPS:» @«"FPS:"» --> |
| `PHASE` | étape <!-- src: gui_builder.py:666 «PHASE» @«"PHASE"» --> |
| `1 Rig` | 1 – le montage <!-- src: gui_builder.py:641 «1 Rig» --> |
| `2 Calibrate` | 2 – le calibrage (mur vide) <!-- src: gui_builder.py:642 «2 Calibrate» --> |
| `3 Live` | 3 – le direct <!-- src: gui_builder.py:643 «3 Live» --> |
| `Advanced` | avancé (réglages experts) <!-- src: gui_builder.py:685 «Advanced» @«label="Advanced"» --> |
| `Rig & Frame` | montage et cadre <!-- src: gui_builder.py:714 «Rig & Frame» @«1 - Rig & Frame» --> |
| `Input` / `Camera` | l'entrée / la caméra <!-- src: gui_builder.py:1757 «Input» @«label="Input"» ; gui_builder.py:1758 «Camera» @«"Camera"» --> |
| `Mirror` / `Rotate` | miroir / tourner <!-- src: gui_builder.py:1790 «Mirror» @«label="Mirror"» ; gui_builder.py:1796 «Rotate» @«"Rotate"» --> |
| `Region of Interest` / `ROI` | zone surveillée (le mur dansé) <!-- src: gui_builder.py:1941 «Region of Interest» @«label="Region of Interest"» ; core/ops_monitor.py:446 «ROI» @«CheckResult("ROI"» --> |
| `Enable ROI` | activer la zone <!-- src: gui_builder.py:1944 «Enable ROI» --> |
| `Reset` | remettre à zéro <!-- src: gui_builder.py:1950 «Reset» @«label="Reset"» --> |
| `Exclusion Mask` | masque : les cases ignorées <!-- src: gui_builder.py:1912 «Exclusion Mask» @«label="Exclusion Mask"» --> |
| `Edit` / `Clear all` | modifier / tout effacer <!-- src: gui_builder.py:1919 «Edit» @«label="Edit"» ; gui_builder.py:1925 «Clear all» @«label="Clear all"» --> |
| `Masked:` | masquées <!-- src: gui_builder.py:1915 «Masked:» @«"Masked:"» --> |
| `Rig sheet` | fiche du montage <!-- src: gui_builder.py:752 «Rig sheet» @«label="Rig sheet"» --> |
| `Phone Monitor` | l'image sur un téléphone <!-- src: gui.py:2991 «Phone Monitor» @«"Phone Monitor"» --> |
| `CALIBRATE` / `CANCEL` | calibrer / annuler <!-- src: gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» ; gui.py:854 «CANCEL» @«"CANCEL"» --> |
| `Empty wall snapshot` / `empty wall` | la photo du mur vide (en anglais technique : « plate ») <!-- src: gui_builder.py:864 «Empty wall snapshot» @«"Empty wall snapshot"» ; core/ops_monitor.py:301 «empty wall» @«"empty wall"» --> |
| `Capture empty wall` | prendre la photo du mur vide (2 s ; CALIBRATE le fait déjà) <!-- src: gui_builder.py:865 «Capture empty wall» @«label="Capture empty wall"» --> |
| `ready` / `none` / `off` / `stale` | prête / aucune / coupée / périmée <!-- src: core/ops_monitor.py:1230 «ready» @«CheckResult("empty wall", "ok", "ready")» ; core/ops_monitor.py:1236 «none» @«if status == "none":» ; core/ops_monitor.py:1233 «off» @«if status == "off":» ; core/ops_monitor.py:1240 «stale» @«if status == "stale":» --> |
| `scene changed` | la scène a changé <!-- src: app.py:1904 «scene changed» @«"empty wall: scene changed - Calibrate on the empty wall"» --> |
| `other camera crop` | un autre cadre de caméra <!-- src: app.py:1903 «other camera crop» @«"empty wall: other camera crop - Calibrate on the empty wall"» --> |
| `Pre-show check` / `Check readiness` | contrôle avant spectacle / lancer le bilan <!-- src: gui_builder.py:874 «Pre-show check» @«"Pre-show check"» ; gui_builder.py:876 «Check readiness» --> |
| `READY` / `NOT READY` | prêt / pas prêt <!-- src: core/ops_monitor.py:59 «READY» @«"READY"» ; core/ops_monitor.py:59 «NOT READY» @«"NOT READY"» --> |
| `NOT ALL CHECKED` / `NOT CHECKED` | pas tout vérifié / pas vérifié <!-- src: core/ops_monitor.py:59 «NOT ALL CHECKED» @«"NOT ALL CHECKED"» ; core/ops_monitor.py:55 «NOT CHECKED» @«"NOT CHECKED"» --> |
| `OK` / `WARN` / `FAIL` / `CHECK` | bon / attention / échec / à vérifier <!-- src: core/ops_monitor.py:55 «OK» @«"OK"» ; core/ops_monitor.py:55 «WARN» @«"WARN"» ; core/ops_monitor.py:55 «FAIL» @«"FAIL"» ; core/ops_monitor.py:59 «CHECK» @«"warn": "CHECK"» --> |
| `Scene Calibration` | calibrage de la scène (la carte du résultat) <!-- src: gui.py:2929 «Scene Calibration» --> |
| `Save to project` / `Keep this session only` | enregistrer dans le projet / garder pour cette séance seulement <!-- src: gui.py:2949 «Save to project» ; gui.py:2955 «Keep this session only» --> |
| `Wall light` | la lumière du mur <!-- src: core/light_verdict.py:74 «Wall light» @«Wall light: » --> |
| `IR flood` | le projecteur infrarouge <!-- src: core/light_verdict.py:187 «IR flood» @«aim the IR flood more toward the » --> |
| `zone` | la zone surveillée <!-- src: core/light_verdict.py:175 «zone» @«the whole zone is dark» --> |
| `top` / `middle` / `bottom` | haut / milieu / bas <!-- src: core/empty_wall.py:78 «top» @«("top", "middle", "bottom")» ; core/empty_wall.py:78 «middle» @«("top", "middle", "bottom")» ; core/empty_wall.py:78 «bottom» @«("top", "middle", "bottom")» --> |
| `left` / `centre` / `right` | gauche / centre / droite <!-- src: core/empty_wall.py:79 «left» @«("left", "centre", "right")» ; core/empty_wall.py:79 «centre» @«("left", "centre", "right")» ; core/empty_wall.py:79 «right» @«("left", "centre", "right")» --> |
| `Someone is in view` | quelqu'un est dans l'image <!-- src: runtime/calibration_flows.py:655 «Someone is in view» @«Someone is in view, » --> |
| `Clear the wall` | vider le mur <!-- src: runtime/calibration_flows.py:656 «Clear the wall» @«Clear the wall, then press Calibrate again.» --> |
| `Detection` / `Drops <-> Ghosts` / `Gap bridging` | détection / pertes ou fantômes / combler les trous (réglages de Thomas) <!-- src: gui_builder.py:1057 «Detection» @«add_text("Detection"» ; gui_builder.py:1058 «Drops <-> Ghosts» ; gui_builder.py:1077 «Gap bridging» @«"Gap bridging"» --> |
| `Dancer IDs` | les numéros des danseurs <!-- src: gui_builder.py:1120 «Dancer IDs» @«"Dancer IDs"» --> |
| `Max dancers` | nombre de danseurs au plus <!-- src: gui_builder.py:1128 «Max dancers» --> |
| `Stability` / `Hold (s)` | lissage du point / tenue en secondes après une perte <!-- src: gui_builder.py:1145 «Stability» @«label="Stability"» ; gui_builder.py:1159 «Hold (s)» @«label="Hold (s)"» --> |
| `IR belt` | ceinture infrarouge <!-- src: gui_builder.py:1175 «IR belt» @«label="IR belt"» --> |
| `Use empty wall` | se servir de la photo du mur vide <!-- src: gui_builder.py:1225 «Use empty wall» @«label="Use empty wall"» --> |
| `Smart hold (earned, none at exits)` | tenue intelligente <!-- src: gui_builder.py:1193 «Smart hold (earned, none at exits)» --> |
| `Ignore static figures` | ignorer les formes immobiles <!-- src: gui_builder.py:1204 «Ignore static figures» --> |
| `Allow remote control during RUN` | autoriser le contrôle à distance en RUN <!-- src: gui_builder.py:1100 «Allow remote control during RUN» --> |
| `Alerts:` / `(none)` | alertes / aucune <!-- src: gui_builder.py:1274 «Alerts:» @«"Alerts:"» ; gui_builder.py:1275 «(none)» @«"(none)"» --> |
| `Recordings` / `LIVE` / `REC` | enregistrements / direct / enregistrer <!-- src: gui_builder.py:1283 «Recordings» @«"Recordings"» ; gui_builder.py:1320 «LIVE» @«label="LIVE"» ; gui_builder.py:1327 «REC» @«label="REC"» --> |
| `REC ARMED - Select slot` | enregistrement prêt : choisir un emplacement <!-- src: gui.py:1262 «REC ARMED - Select slot» --> |
| `IMPORT` / `ISSUE` | importer / signaler (outils experts) <!-- src: gui_builder.py:1347 «IMPORT» @«label="IMPORT"» ; gui_builder.py:1397 «ISSUE» @«label="ISSUE"» --> |
| `Dancers:` | danseurs suivis <!-- src: gui_builder.py:565 «Dancers:» @«"Dancers:"» --> |
| `Bright:` | luminosité <!-- src: gui_builder.py:574 «Bright:» @«"Bright:"» --> |
| `points` | les boules envoyées aux visuels <!-- src: core/ops_monitor.py:981 «points» @«POINTS_ROW = "points"» --> |
| `a body on the wall` | une forme (un corps) sur le mur <!-- src: core/ops_monitor.py:1125 «a body on the wall» @«a body on the wall has» --> |
| `output` | la sortie vers les visuels <!-- src: core/ops_monitor.py:1219 «output» @«CheckResult("output", "ok"» --> |
| `frames` | les images <!-- src: core/ops_monitor.py:1247 «frames» @«CheckResult("frames", "fail"» ; core/ops_monitor.py:1249 «frames» @«CheckResult("frames", "ok"» --> |
| `engine` | le moteur de détection <!-- src: core/ops_monitor.py:1257 «engine» @«CheckResult("engine", "ok"» --> |
| `camera stalls` | la caméra qui se fige <!-- src: core/ops_monitor.py:1178 «camera stalls» @«name = "camera stalls"» ; core/ops_monitor.py:1178 «camera stalls» @«name = "camera stalls"» --> |
| `enhancement` | amélioration d'image (contraste) <!-- src: core/ops_monitor.py:417 «enhancement» @«CheckResult("enhancement"» --> |
| `plate exposure` | l'exposition de la photo du mur vide <!-- src: core/ops_monitor.py:476 «plate exposure» @«name = "plate exposure"» --> |
| `dancer size` | la taille des danseurs <!-- src: core/ops_monitor.py:326 «dancer size» --> |
| `net input` | l'image donnée à la détection <!-- src: core/ops_monitor.py:203 «net input» @«CheckResult("net input", "ok"» --> |
| `restart the app` | relancer l'app <!-- src: core/ops_monitor.py:985 «restart the app» @«RESTART_HOW = "restart the app"» --> |
| `between two pieces` | entre deux pièces <!-- src: core/ops_monitor.py:290 «between two pieces» @«LIGHT_REMEDY = ("between two pieces, in STANDBY, Calibrate» --> |
| `LECTURE - pas en direct` | (déjà en français) <!-- src: gui_builder.py:499 «LECTURE - pas en direct» --> |
| `Expert mode ON` | mode expert activé <!-- src: gui.py:528 «Expert mode ON» @«"Expert mode ON"» --> |
| `YOLO` | le nom du logiciel de détection des silhouettes <!-- src: gui_builder.py:585 «YOLO» @«"YOLO:"» --> |
| `MOG2`, `CLAHE`, `gamma` | des réglages d'image internes : rien à faire <!-- src: core/empty_wall.py:182 «gamma» @«Kept gamma » ; core/calibration.py:750 «MOG2» @«MOG2: varThreshold » ; core/calibration.py:762 «CLAHE» @«CLAHE clip: » ; runtime/calibration_flows.py:592 «f"Gamma: » --> |

<div style="break-before: page"></div>

## 11. L'essentiel

**La fiche de deux pages, *FICHE_TECHNICIEN_FR*, résume ce guide. L'avoir imprimée à côté du portable.**

1. Secteur, caméra USB3, projecteur IR, TouchDesigner — **puis** WallDance.
2. ① `1 Rig` : la zone (`Region of Interest`), `Max dancers`. <!-- src: gui_builder.py:641 «1 Rig» ; gui_builder.py:1941 «Region of Interest» @«label="Region of Interest"» ; gui_builder.py:1128 «Max dancers» -->
3. ② `2 Calibrate` : mur vide, `CALIBRATE`, **`Save to project`**. <!-- src: gui_builder.py:642 «2 Calibrate» ; gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» ; gui.py:2949 «Save to project» -->
4. ③ Un danseur 10 s au fond du mur, en `RUN`. <!-- src: gui_builder.py:1045 «RUN» @«label="RUN"» -->
5. ④ Le bilan : `READY`, ou chaque ligne réglée (§8). <!-- src: core/ops_monitor.py:59 «READY» @«"READY"» -->
6. ⑤ `RUN`, `Ctrl+S`, puis on ne touche plus que `Max dancers`. <!-- src: gui_builder.py:1045 «RUN» @«label="RUN"» ; gui_builder.py:394 «Ctrl+S» @«Save config (Ctrl+S)» ; gui_builder.py:1128 «Max dancers» -->
7. La ligne de santé : vert = rien ; orange = continuer, noter ; rouge = l'action seulement si elle n'arrête pas le spectacle (§6).
8. `LECTURE - pas en direct` : `LIVE (camera)`, tout de suite. <!-- src: gui_builder.py:499 «LECTURE - pas en direct» ; gui_builder.py:512 «LIVE (camera)» -->
9. Entre deux pièces : `STANDBY`, `CALIBRATE`, `Save to project`, `RUN` (§7). <!-- src: gui_builder.py:1034 «STANDBY» @«label="STANDBY"» ; gui_builder.py:842 «CALIBRATE» @«label="CALIBRATE"» ; gui.py:2949 «Save to project» ; gui_builder.py:1045 «RUN» @«label="RUN"» -->
10. Rien ne va : relancer ; ne repart pas : la version du 8 octobre ; noter l'heure.

> **État du guide — 26/11** (ce qui bouge encore ; la prochaine mise à jour regarde ici)
>
> - **L'étape danseur (X4)** : en cours. §5.3 décrit la version d'aujourd'hui (mesure seule en RUN, pas de bouton). La nouvelle étape choisira aussi l'amélioration d'image : §5.3, §8.5 (`enhancement`) et la ligne de la fiche changeront. <!-- src: core/ops_monitor.py:417 «enhancement» @«CheckResult("enhancement"» -->
> - **Le point tenu (F4)** : un point tenu devra reposer sur une forme du mur ; pas encore dans cette version. Les bords orange des boules (§4.3) pourront changer.
> - **La caméra qui se fige (U1)** : la tenue d'un danseur ne sera plus mangée par un arrêt de la caméra ; pas encore dans cette version. §8.1 inchangé.
> - **Le choix automatique du réglage (« swipe », P5/P6)** : pas dans cette version ; s'il entre, un bouton et des messages nouveaux viendront en §5 et §8.
> - **L'exposition (X1)** : jusqu'où le mur peut s'assombrir est encore mesuré (§5.2).
> - **La ligne de santé (H2)** : le rouge `points` pourrait se partager entre rouge et orange (§6.1). <!-- src: core/ops_monitor.py:981 «points» @«POINTS_ROW = "points"» -->
> - **Le 2/12** : l'icône et le projet de la version de répétition, le projet de la version du 8 octobre (§3).
> - **Avec Thomas** : chaque **[à confirmer avec Thomas]** de ce guide ; le numéro de téléphone (§8.8) ; la version et le secours du 12/12.
> - **Les captures d'écran** : une passe plus tard, aux lignes *[capture d'écran : …]*.
