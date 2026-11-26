# WallDance — fiche technicien

<!--
S2 (hub walldance#t-062) — skeleton of 26/11, written from release 1 (cordee-r2 @ c9bcb00). Filled on 1/12 from the
rehearsal build as it stands, rendered to PDF then, read by Thomas on 4/12. Must print on two A4 pages.
Conventions, checked by tmp_analysis/r2-1126/gates/s2/run_gate.sh <code-root>:
- A code span = a string exactly as the app shows it. Every code span is backed, on the same source line, by a
  citation comment "src: <path>:<line> «snippet»" whose snippet is a literal piece of that span, found at that
  line (paths relative to application/src unless they start with launcher/).
- Placeholders are bold brackets that start with the build step that fills them ([X3 : ...]) or name Thomas.
- §3 lists each red or orange state of release 1 with its one action (« screen text → **action** », a list rather
  than a table: half the height on paper); states that share the action share an item.
  Left out on purpose (not in Nico's flow): expert-tool toasts (auto-tune, known-N, dry-run, dials
  re-anchored), project rename/delete/new toasts, the IDS gain/exposure/crop controls (expert mode only, X5),
  and the Calibrate progress lines (neither red nor orange).
-->

**Pour Nico** — répétition des 7–8/12, spectacle du 12/12 seul. `CALIBRATE` = le texte exact de l'écran (traduction entre parenthèses) ; **[X3 : …]** = à compléter par l'étape X3. <!-- src: gui_builder.py:784 «CALIBRATE» -->

## 1. Lancer

- Secteur branché, arrière surélevé ; IR allumé tout près de l'objectif ; aucune lampe dans l'image.
- Lancer **[W1 ② : l'icône de la version de répétition]**. `WallDance - Select Project` : **[W1 ② : le projet]**, `Launch` (ouvrir). Jamais `Delete` (supprimer) ni `Rename`. <!-- src: gui.py:2258 «WallDance - Select Project» ; gui.py:2270 «Launch» ; gui.py:2274 «Delete» ; gui.py:2272 «Rename» -->
- `Previous session crashed (…)` en rouge (la séance d'avant s'est arrêtée net) : noter l'heure, continuer. <!-- src: services/crash_marker.py:174 «Previous session crashed (» -->

## 2. Installer, dans l'ordre

**① Le cadre** — onglet `1 Rig`. <!-- src: gui_builder.py:583 «1 Rig» -->

- Caméra fixée, mise au point sous IR (bouton QR : `Phone monitor`). Si elle bouge ensuite : ② à ⑤. <!-- src: gui_builder.py:752 «Phone monitor» -->
- `Region of Interest` (zone surveillée) : `Enable ROI`, double-clic sur l'image, tracer la zone dansée (+ marge). <!-- src: gui_builder.py:1858 «Region of Interest» ; gui_builder.py:1861 «Enable ROI» -->
- `Exclusion Mask` (cases ignorées) : `Edit` seulement où personne ne danse jamais ; dans le doute, rien. <!-- src: gui_builder.py:1829 «Exclusion Mask» ; gui_builder.py:1836 «Edit» -->
- Onglet `3 Live` : `Max dancers` = le nombre de danseurs sur le mur. <!-- src: gui_builder.py:585 «3 Live» ; gui_builder.py:1075 «Max dancers» -->

**② Mur vide, puis CALIBRATE** — onglet `2 Calibrate`, personne dans l'image. <!-- src: gui_builder.py:584 «2 Calibrate» -->

- `CALIBRATE`, attendre 20 à 30 s ; un deuxième appui annule. <!-- src: gui_builder.py:784 «CALIBRATE» -->
- `Scene Calibration`, dernière ligne : `Kept gamma …` ou `Kept the current gamma …` = bon ; `YOLO sees a person on the EMPTY wall …` (quelqu'un sur le mur vide) = enlever l'objet (sinon le peindre : `Exclusion Mask`), refaire `CALIBRATE`. <!-- src: gui.py:2840 «Scene Calibration» ; core/empty_wall.py:74 «Kept gamma » ; core/empty_wall.py:71 «Kept the current gamma » ; core/empty_wall.py:78 «YOLO sees a person on the EMPTY wall» ; gui_builder.py:1829 «Exclusion Mask» ; gui_builder.py:784 «CALIBRATE» -->
- Puis `Save to project` (enregistrer) ; `empty wall: ready (…)` (mur vide : prêt) s'affiche. <!-- src: gui.py:2860 «Save to project» ; app.py:1865 «empty wall: ready» -->
- Garder 15 s de ce mur vide : `REC`, un slot libre (1 à 9), 15 s, `REC`. Noter slot et éclairage. <!-- src: gui_builder.py:1244 «REC» -->
- **[X3 : quelqu'un dans l'image : arrêt, et où]** **[X1 : exposition des zones claires ; vrai mur le 4/12]** **[X2 : verdict lumière, quoi déplacer]**

**③ Un danseur, 10 s** — `3 Live`, `RUN` ; un danseur debout 10 s contre le mur, au point le plus loin de la caméra. **[X4 : l'étape danseur et la bande de taille ; le modèle si P5 entre]** <!-- src: gui_builder.py:585 «3 Live» ; gui_builder.py:985 «RUN» -->

**④ Vérifier** — la vérification se lance seule au démarrage et après chaque `CALIBRATE` (sinon `2 Calibrate`, `Check readiness`). En tête, `READY` seulement si toutes les lignes sont **[OK]** vert ; `NOT ALL CHECKED` : des lignes `[NOT CHECKED]` bleu-gris (pas vérifié, jamais vert ; `dancer size` avant ③). **[WARN]** orange, **[FAIL]** rouge : §3. <!-- src: gui_builder.py:806 «CALIBRATE» ; gui_builder.py:606 «2 Calibrate» ; gui_builder.py:840 «Check readiness» ; core/ops_monitor.py:59 «READY» ; core/ops_monitor.py:59 «NOT ALL CHECKED» ; core/ops_monitor.py:55 «NOT CHECKED» ; core/ops_monitor.py:323 «dancer size» ; gui.py:3021 «STATUS_WORDS.get(status» ; gui.py:3008 «"skip": NOT_CHECKED_COLOR» -->

**⑤ RUN** — rester en `RUN` (pas `STANDBY`) ; `Go-Live check: …` résume ④ ; `Ctrl+S` enregistre. <!-- src: gui_builder.py:985 «RUN» ; gui_builder.py:974 «STANDBY» ; core/ops_monitor.py:90 «Go-Live check: » ; gui_builder.py:393 «Ctrl+S» -->

- Ne toucher que `Max dancers` ; ni clic sur un slot (l'app lirait la prise), ni lettre tapée. **[A4 : bandeau « LECTURE »]** <!-- src: gui_builder.py:1075 «Max dancers» ; ui/adapter.py:292 «mvKey_P» -->
- Surveiller `Alerts:` et les messages rouges `/!\ …` ; beaucoup de boules à bord orange (danseur maintenu, pas vu) : noter l'heure. **[H2 : la ligne de santé]** <!-- src: gui_builder.py:1191 «Alerts:» ; ui/adapter.py:346 «/!\» ; core/visualization.py:34 «orange: holding» -->

## 3. Orange ou rouge : la seule chose à faire

<!-- Readiness rows: core/ops_monitor.py check_* (rows rendered by gui.py show_readiness_rows); alerts: HealthMonitor.tick, each shown as a red toast "/!\ <message>" (ui/adapter.py:346), light_changed also kept in the Alerts strip (ui/adapter.py:34). -->

- `not open`, `reconnecting`, `CAM: OFF`, `RECONNECTING`, `[CV]`, `Camera down for …` (caméra perdue) → **Vérifier le câble USB3 et l'alimentation de la caméra.** <!-- src: core/ops_monitor.py:105 «not open» ; core/ops_monitor.py:105 «reconnecting» ; gui_builder.py:412 «CAM:» ; gui_builder.py:422 «RECONNECTING» ; gui.py:1776 «[CV]» ; core/ops_monitor.py:574 «Camera down for » -->
- `below the 15 FPS show floor`, `FPS dropped to …`, `FPS:` en rouge (image lente) → **Secteur branché ; fermer tout sauf WallDance et TouchDesigner.** <!-- src: core/ops_monitor.py:112 «FPS show floor» ; core/ops_monitor.py:476 «FPS dropped to » ; gui_builder.py:450 «FPS:» -->
- `gpu: … at/above 85 C`, `GPU at … C` (carte graphique chaude) → **Surélever l'arrière du portable, dégager les aérations.** <!-- src: core/ops_monitor.py:362 «at/above» ; core/ops_monitor.py:595 «GPU at » -->
- `tensorrt:`, `TensorRT OFF - running PyTorch (…)`, `[CPU FALLBACK]`, `Running on CPU`, `Using PyTorch (fallback)`, `net input: … but the square … runs`, `frame processing error …` en boucle (détection dégradée) → **Relancer l'app (§4). Jamais `Rebuild TRT` (efface le moteur).** <!-- src: core/ops_monitor.py:136 «tensorrt» ; runtime/model_controller.py:335 «TensorRT OFF - running PyTorch (» ; gui_builder.py:445 «[CPU FALLBACK]» ; runtime/main_loop.py:351 «Running on CPU» ; runtime/model_controller.py:613 «Using PyTorch (fallback)» ; core/ops_monitor.py:171 «but the square » ; runtime/main_loop.py:455 «frame processing» ; gui_builder.py:480 «Rebuild TRT» -->
- `nothing listening on …`, `OSC send to … failing` (TouchDesigner ne reçoit rien) → **Lancer (ou relancer) TouchDesigner et son patch.** <!-- src: core/ops_monitor.py:204 «nothing listening on » ; core/osc_output.py:66 «OSC send to » -->
- `OSC: OFF`, `osc: disabled` (envoi coupé) → **`Advanced` → `OSC` → cocher `Enable OSC`.** <!-- src: gui_builder.py:428 «OSC:» ; core/ops_monitor.py:216 «disabled» ; gui_builder.py:627 «Advanced» ; gui_builder.py:1486 «OSC» ; gui_builder.py:1489 «Enable OSC» -->
- `calibration: …`, `empty wall: none`, `other camera crop`, `scene changed`, `Empty-wall capture failed: …`, `ENHANCEMENT OFF - run Calibrate …` (mur vide absent ou périmé) **[F2 : « plate stale » reste affiché, avec sa raison]** → **Dès que le mur est vide : ② jusqu'à `Save to project`.** <!-- src: core/ops_monitor.py:228 «calibration» ; app.py:1864 «empty wall: none» ; app.py:1866 «other camera crop» ; app.py:1867 «scene changed» ; app.py:1899 «Empty-wall capture failed: » ; gui_builder.py:1556 «ENHANCEMENT OFF - run Calibrate» ; gui.py:2860 «Save to project» -->
- `empty wall: off` → **`3 Live` → cocher `Use empty wall`.** <!-- src: app.py:1863 «empty wall: off» ; gui_builder.py:585 «3 Live» ; gui_builder.py:1165 «Use empty wall» -->
- `enhancement: OFF, but Calibrate chose …` → **`Ctrl+Shift+E`, `Advanced` → `Enhancement` → cocher `Enable`, `Ctrl+Shift+E`.** <!-- src: core/ops_monitor.py:415 «OFF, but Calibrate chose» ; core/ops_monitor.py:399 «Ctrl+Shift+E» ; gui_builder.py:649 «Advanced» ; gui_builder.py:1585 «Enhancement» ; gui_builder.py:1592 «Enable» -->
- `ROI: off: YOLO watches the whole …`, `ROI: … cut to …`, `plate exposure: … RUN refuses it …` (zone ou mur vide d'un autre cadrage, d'une autre exposition) → **Retracer `Region of Interest` (①) si `ROI`, puis ②.** <!-- src: core/ops_monitor.py:443 «off: YOLO watches the whole» ; core/ops_monitor.py:460 «cut to » ; core/ops_monitor.py:497 «RUN refuses it» ; gui_builder.py:1882 «Region of Interest» ; core/ops_monitor.py:443 «ROI» -->
- `The light is …x the empty-wall snapshot …` (la lumière a changé ; reste dans `Alerts:`) → **Au prochain mur vide : ②. Jamais vide : entre deux scènes, clic sur le slot du mur vide de cet éclairage, ②, `LIVE` (TouchDesigner sans danseurs entre-temps).** <!-- src: core/ops_monitor.py:559 «The light is » ; core/ops_monitor.py:559 «the empty-wall snapshot» ; gui_builder.py:1191 «Alerts:» ; gui_builder.py:1237 «LIVE» -->
- `dancer size: not measured …`, `Person height calibration looks stale …` (taille des danseurs inconnue) → **Faire ③ : un danseur debout, entier dans l'image, 10 s.** <!-- src: core/ops_monitor.py:295 «not measured» ; core/ops_monitor.py:535 «Person height calibration looks stale» -->
- `dancer size: … below the ~100 px YOLO needs` (danseurs trop petits) → **Resserrer `Region of Interest` sur la zone dansée, puis ② et ③.** <!-- src: core/ops_monitor.py:311 «dancer size» ; core/ops_monitor.py:311 «px YOLO needs» ; gui_builder.py:1858 «Region of Interest» -->
- `No dancers tracked for … while RUNNING` (aucun danseur suivi) → **Danseurs sur le mur : vérifier l'IR et l'image. Sinon rien.** <!-- src: core/ops_monitor.py:498 «No dancers tracked for » ; core/ops_monitor.py:499 «while RUNNING» -->
- `More people than max_persons visible …` (trop de monde) → **Vérifier `Max dancers`.** <!-- src: core/ops_monitor.py:513 «More people than max_persons visible» ; gui_builder.py:1075 «Max dancers» -->
- `Calibrate: scene too noisy for MOG2 …` (image trop bruitée) → **Éclairer le mur plus également (IR), puis `CALIBRATE`.** <!-- src: runtime/calibration_flows.py:410 «Calibrate: scene too noisy for MOG2» ; gui_builder.py:784 «CALIBRATE» -->
- `Calibration cancelled`, `Load a model before calibrating`, `Start the camera or play a recording first` → **`LIVE`, attendre l'image, puis un seul appui sur `CALIBRATE`.** <!-- src: runtime/calibration_flows.py:223 «Calibration cancelled» ; runtime/calibration_flows.py:229 «Load a model before calibrating» ; runtime/calibration_flows.py:239 «Start the camera or play a recording first» ; gui_builder.py:1237 «LIVE» ; gui_builder.py:784 «CALIBRATE» -->
- `disk:` (disque presque plein) → **Ne plus enregistrer (`REC`).** <!-- src: core/ops_monitor.py:348 «disk» ; gui_builder.py:1244 «REC» -->
- `Save FAILED: …` (enregistrement raté) → **Refaire `Ctrl+S` ; si ça rate encore, noter l'heure (le réglage reste actif).** <!-- src: runtime/config_manager.py:396 «Save FAILED: » ; gui_builder.py:393 «Ctrl+S» -->
- `REMOTE` (session à distance), `Exposure-limited: … FPS`, `Config: … adjusted on load …` → **Rien à faire.** <!-- src: gui_builder.py:432 «REMOTE» ; gui.py:1955 «Exposure-limited: » ; runtime/config_manager.py:608 «adjusted on load» -->

## 4. Secours

**Relancer l'app** — fermer WallDance, relancer (§1), `Launch` sur le même projet, ④, ⑤ ; pas de `empty wall: ready (…)` : ②. **[A2 : ce que la relance remet seule]** Figée : **[W1 ② : la fermer]**. <!-- src: gui.py:2270 «Launch» ; app.py:1865 «empty wall: ready» -->

**La version du 08/10 (prod)** — fermer la version de répétition (une seule app tient la caméra) ; double-clic sur l'icône `WallDance` : `WallDance Launcher` la démarre. `Update Available` : **Non**, sauf accord de Thomas ; `Local Version Differs` : `Keep local version`. Projet : **[W1 ② : celui du 08/10]**. Mêmes étapes ① ② ④ ⑤. <!-- src: launcher/install_manager.py:27 «WallDance» ; launcher/gui.py:75 «WallDance Launcher» ; launcher/gui.py:410 «Update Available» ; launcher/gui.py:196 «Local Version Differs» ; launcher/gui.py:204 «Keep local version» -->

**Le 12/12** — **[Thomas, 9–10/12 : la version de secours du spectacle et comment la lancer]**

## 5. Qui répond

**Les 7 et 8/12** — Une session sur l'ordinateur de Thomas regarde le portable (Nico est d'accord) : état, alertes, image de la caméra ; `REMOTE` s'allume. Elle peut écrire sur l'écran **[S1b : le message, le bouton qui l'efface ; `Allow remote control during RUN` ?]**. Thomas au téléphone : **[numéro de Thomas]**. Noter l'heure de chaque souci (journaux relus la nuit). <!-- src: gui_builder.py:432 «REMOTE» ; gui_builder.py:1040 «Allow remote control during RUN» -->

**Le 12/12** — Personne : cette fiche est tout. Un souci : sa ligne au §3 ; rien ne va : relancer ; ne repart pas : le secours (§4).
