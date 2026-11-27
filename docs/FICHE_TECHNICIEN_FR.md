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
27/11 (docs pass, cordee-r2 @ c823b93): citations re-pointed; the stall line (reseat the cable), « no IR light »,
the reference watch (H4), the swipe by hand (P5, Apply in STANDBY only), the drawn marks; the landed steps' placeholders
(X2, X3, F2, A2, A4) filled. Left out for room, the rarest: « net input ... but the square ... runs », « Load a model
before calibrating », « [CV] », « Empty-wall capture failed: », and two duplicates of a state kept (« reconnecting »,
« Using PyTorch (fallback) »); the swipe's own failure rows (its toasts say what to do); the « link limit » variants.
27/11 later (cordee-r2 @ 8023250): citations re-pointed; step ② gains the card's last line « Wall light: … » (the
verdict, « being measured », « no verdict »). Its rare « not judged here (<error>) » form is left out: that line names
its own action. Room kept by trimming « Calibration cancelled », « dancer size » and the new line's own glosses.
27/11 X4e (r2-x4e, the dancer step's code on 789b8ff): step ③ written for « A DANCER, 10 s » (the line, the card,
the « nothing set » forms; optional); ② says the empty-wall check runs with the enhancement on; §3: the new
« ENHANCEMENT OFF - run A DANCER, 10 s ... » label goes to « do ③ », the live size band and the belt glare join
the « too small » line. The X4 placeholder is gone (W1's stay). Citations re-pointed onto that code.
27/11 docs5a (cordee-r2 @ 64d4dc7): citations re-pointed; ② the stop with an object -> the Exclusion Mask, and
its one warning (a person in a masked cell is in the plate), the button back at CALIBRATE, « Saved to project. »;
§3: the arrival-day band (normal, STANDBY then ②), the dancer step's « not started / stopped / not kept » and
« dancer at the network: not measured (...) » -> ② then ③, the detector's held-point alert and row.
-->

**Pour Nico** — répétition des 7–8/12, spectacle du 12/12 seul. `CALIBRATE` = le texte exact de l'écran (traduction entre parenthèses) ; **[W1 : …]** = à compléter par l'étape W1. <!-- src: gui_builder.py:854 «CALIBRATE» -->

## 1. Lancer

- Secteur branché, arrière surélevé ; IR allumé tout près de l'objectif ; aucune lampe dans l'image.
- Lancer **[W1 ② : l'icône de la version de répétition]**. `WallDance - Select Project` : **[W1 ② : le projet]**, `Launch` (ouvrir). Jamais `Delete` (supprimer) ni `Rename`. <!-- src: gui.py:2544 «WallDance - Select Project» ; gui.py:2556 «Launch» ; gui.py:2560 «Delete» ; gui.py:2558 «Rename» -->
- `Previous session crashed (…)` en rouge (la séance d'avant s'est arrêtée net) : noter l'heure, continuer. <!-- src: services/crash_marker.py:174 «Previous session crashed (» -->

## 2. Installer, dans l'ordre

**① Le cadre** — onglet `1 Rig`. <!-- src: gui_builder.py:642 «1 Rig» -->

- Caméra fixée, mise au point sous IR (bouton QR : `Phone monitor`). Si elle bouge ensuite : ② à ⑤. <!-- src: gui_builder.py:822 «Phone monitor» -->
- `Region of Interest` (zone surveillée) : `Enable ROI`, double-clic sur l'image, tracer la zone dansée (+ marge). <!-- src: gui_builder.py:2039 «Region of Interest» ; gui_builder.py:2042 «Enable ROI» -->
- `Exclusion Mask` (cases ignorées) : `Edit` seulement où personne ne danse jamais ; dans le doute, rien. <!-- src: gui_builder.py:2010 «Exclusion Mask» ; gui_builder.py:2017 «Edit» -->
- Onglet `3 Live` : `Max dancers` = le nombre de danseurs sur le mur. <!-- src: gui_builder.py:644 «3 Live» ; gui_builder.py:1220 «Max dancers» -->

**② Mur vide, puis CALIBRATE** — onglet `2 Calibrate`, personne dans l'image (contrôle amélioration allumée). <!-- src: gui_builder.py:643 «2 Calibrate» -->

- `CALIBRATE`, 20 à 30 s ; un 2e appui annule. `Stopped: someone in view, …` (quelqu'un dans l'image) → **une personne : la faire sortir ; un objet : le masquer (`1 Rig` → `Exclusion Mask`, `Edit`, ses cases, `Edit`, `Ctrl+S`) ; puis `CALIBRATE`. Qui est dans le masque n'arrête rien et entre dans la photo du mur vide.** <!-- src: gui_builder.py:854 «CALIBRATE» ; runtime/calibration_flows.py:720 «Stopped: someone in view, » ; gui_builder.py:642 «1 Rig» ; gui_builder.py:2010 «Exclusion Mask» ; gui_builder.py:2017 «Edit» ; gui_builder.py:395 «Ctrl+S» -->
- `Scene Calibration`, en bas : `Kept gamma …` ou `Kept the current gamma …` = bon ; `YOLO now and then sees a person on the EMPTY wall …` (un objet pris pour quelqu'un) = le masquer ou l'ôter, `CALIBRATE`. <!-- src: gui.py:3126 «Scene Calibration» ; core/empty_wall.py:228 «Kept gamma » ; core/empty_wall.py:225 «Kept the current gamma » ; core/empty_wall.py:232 «YOLO now and then sees a person on the EMPTY wall» ; gui_builder.py:854 «CALIBRATE» -->
- Sa dernière ligne, `Wall light: …` : `being measured …` → **attendre, mur vide** ; `no verdict - … - Calibrate on the empty wall` → **`CALIBRATE` sur le mur vide** ; un verdict → **comme la ligne `wall light`.** <!-- src: core/light_verdict.py:84 «Wall light: » ; app.py:200 «being measured» ; app.py:203 «no verdict - » ; app.py:203 «Calibrate on the empty wall» ; gui_builder.py:854 «CALIBRATE» ; core/ops_monitor.py:675 «wall light» -->
- Puis `Save to project` (enregistrer) → `Saved to project.`, `Close` ; `empty wall: ready (…)` (prêt). <!-- src: gui.py:3146 «Save to project» ; gui.py:2442 «Saved to project.» ; gui.py:2444 «Close» ; app.py:2054 «empty wall: ready» -->
- Garder 15 s de ce mur vide : `REC`, un slot libre (1 à 9), 15 s, `REC`. Noter slot et éclairage. <!-- src: gui_builder.py:1422 «REC» -->
- `wall light` rouge, `aim the IR flood more toward …`, `move the IR flood back from the wall …`, ou `Kept the exposure, the bright spot stays at white` (lumière inégale) → **si possible, l'IR comme écrit, puis ②.** <!-- src: core/ops_monitor.py:675 «wall light» ; core/light_verdict.py:200 «aim the IR flood more toward » ; core/light_verdict.py:205 «move the IR flood back from the wall» ; core/calibration.py:403 «Kept the exposure, the bright spot stays at white» -->

**③ Un danseur, 10 s** — facultatif, après ② : `A DANCER, 10 s` (sans `RUN`) ; un danseur seul, entier, immobile 10 s au plus loin de la caméra. L'app règle taille et image, juge la ceinture : `Set: …` → `Keep and save` (garder) ou `Cancel - put back` (tout remettre). `two people on the wall - nothing set`, `nobody seen …`, `… saw her in …` (rien de réglé) → **refaire ainsi, éclairé.** <!-- src: gui_builder.py:957 «A DANCER, 10 s» ; gui_builder.py:1127 «RUN» ; runtime/calibration_flows.py:1082 «Set: » ; gui.py:985 «Keep and save» ; gui.py:988 «Cancel - put back» ; runtime/calibration_flows.py:1067 «two people on the wall - nothing set» ; runtime/calibration_flows.py:1064 «nobody seen » ; runtime/calibration_flows.py:1060 «saw her in » -->

**④ Vérifier** — la vérification se lance seule au démarrage et après chaque `CALIBRATE` (sinon `2 Calibrate`, `Check readiness`). En tête, `READY` seulement si toutes les lignes sont **[OK]** vert ; `NOT ALL CHECKED` : des lignes `[NOT CHECKED]` bleu-gris (pas vérifié, jamais vert ; `dancer size` avant ③). **[WARN]** orange, **[FAIL]** rouge : §3. <!-- src: gui_builder.py:854 «CALIBRATE» ; gui_builder.py:643 «2 Calibrate» ; gui_builder.py:891 «Check readiness» ; core/ops_monitor.py:59 «READY» ; core/ops_monitor.py:59 «NOT ALL CHECKED» ; core/ops_monitor.py:55 «NOT CHECKED» ; core/ops_monitor.py:362 «dancer size» ; gui.py:3287 «STATUS_WORDS.get(status» ; gui.py:3274 «"skip": NOT_CHECKED_COLOR» -->

**⑤ RUN** — rester en `RUN` (pas `STANDBY`) ; `Go-Live check: …` résume ④ ; `Ctrl+S` enregistre. <!-- src: gui_builder.py:1106 «RUN» ; gui_builder.py:1056 «STANDBY» ; core/ops_monitor.py:127 «Go-Live check: » ; gui_builder.py:395 «Ctrl+S» -->

- Ne toucher que `Max dancers`, ni lettre ni clic sur un slot. Bande rouge `LECTURE - pas en direct` → **`LIVE`.** <!-- src: gui_builder.py:1220 «Max dancers» ; ui/adapter.py:346 «mvKey_P» ; gui_builder.py:500 «LECTURE - pas en direct» ; gui_builder.py:1413 «LIVE» -->
- La bande sous l'image : `OK` vert = rien à faire ; `WARN` orange, `FAIL` rouge : le pire souci, sa consigne (§3). `(+N: ...)` (N autres soucis) et `...` (abrégé) : rien. Boule à bord orange : danseur maintenu, pas vu. <!-- src: core/ops_monitor.py:55 «OK» ; core/ops_monitor.py:55 «WARN» ; core/ops_monitor.py:55 «FAIL» ; ui/line_fit.py:136 «(+» ; ui/line_fit.py:36 «...» ; core/visualization.py:34 «orange: holding» -->

**Échauffement, facultatif** — mur vide fait, `RUN`, danseurs sur le mur depuis 2 min 30 : `Try other settings` (20 s environ ; le spectacle continue). `keep …` : rien à faire. Un autre réglage proposé : à la pause, `STANDBY`, `Apply … (STANDBY)`, `Ctrl+S` ; sinon `Dismiss`. `the empty-wall plate is no longer valid …` : ②, puis relancer. <!-- src: gui_builder.py:1106 «RUN» ; gui_builder.py:903 «Try other settings» ; core/swipe.py:493 «keep » ; gui_builder.py:1056 «STANDBY» ; app.py:3195 «(STANDBY)» ; gui_builder.py:395 «Ctrl+S» ; gui_builder.py:944 «Dismiss» ; runtime/swipe_controller.py:94 «the empty-wall plate is no longer valid» -->

## 3. Orange ou rouge : la seule chose à faire

<!-- Readiness rows: core/ops_monitor.py check_* (rows rendered by gui.py show_readiness_rows); alerts: HealthMonitor.tick, each shown as a red toast "/!\ <message>" (ui/adapter.py:346), light_changed also kept in the Alerts strip (ui/adapter.py:34). -->

- `points: … had no point for …` (pas suivi) → **Danseur : vérifier `Max dancers` ; ombre, technicien : rien.** <!-- src: core/ops_monitor.py:1117 «points» ; core/ops_monitor.py:1271 «had no point for» ; gui_builder.py:1213 «Max dancers» -->
- `not open`, `CAM: OFF`, `RECONNECTING`, `Camera down for …` (caméra perdue) → **Câble USB3, alimentation.** <!-- src: core/ops_monitor.py:134 «not open» ; gui_builder.py:414 «CAM:» ; gui_builder.py:424 «RECONNECTING» ; core/ops_monitor.py:936 «Camera down for » -->
- `camera stalls: …` orange, `the picture freezes often` rouge (la caméra se fige) → **À la prochaine pause : rebrancher à fond le câble USB de la caméra, aux deux bouts ; toujours orange : l'autre câble.** <!-- src: core/ops_monitor.py:1323 «camera stalls» ; core/ops_monitor.py:1330 «the picture freezes often» -->
- `below the 15 FPS show floor`, `FPS dropped to …`, `FPS:` en rouge (image lente) → **Secteur branché ; fermer tout sauf WallDance et TouchDesigner.** <!-- src: core/ops_monitor.py:144 «FPS show floor» ; core/ops_monitor.py:815 «FPS dropped to » ; gui_builder.py:452 «FPS:» -->
- `gpu: … at/above 85 C`, `GPU at … C` (trop chaud) → **Surélever l'arrière, dégager les aérations.** <!-- src: core/ops_monitor.py:471 «at/above» ; core/ops_monitor.py:957 «GPU at » -->
- `tensorrt:`, `TensorRT OFF - running PyTorch (…)`, `[CPU FALLBACK]`, `Running on CPU`, `frame processing error …` en boucle (détection dégradée) → **Relancer l'app (§4). Jamais `Rebuild TRT` (efface le moteur).** <!-- src: core/ops_monitor.py:190 «tensorrt» ; runtime/model_controller.py:434 «TensorRT OFF - running PyTorch (» ; gui_builder.py:447 «[CPU FALLBACK]» ; runtime/main_loop.py:353 «Running on CPU» ; runtime/main_loop.py:463 «frame processing» ; gui_builder.py:482 «Rebuild TRT» -->
- `nothing listening on …`, `OSC send to … failing` (rien ne part) → **(Re)lancer TouchDesigner et son patch.** <!-- src: core/ops_monitor.py:258 «nothing listening on » ; core/osc_output.py:66 «OSC send to » -->
- `OSC: OFF`, `osc: disabled`, `output: OSC is off` (envoi coupé) → **`Advanced` → `OSC` → cocher `Enable OSC`.** <!-- src: gui_builder.py:430 «OSC:» ; core/ops_monitor.py:273 «disabled» ; core/ops_monitor.py:1359 «OSC is off» ; gui_builder.py:686 «Advanced» ; gui_builder.py:1650 «OSC» ; gui_builder.py:1667 «Enable OSC» -->
- `calibration: …`, `empty wall: none`, `other camera crop`, `scene changed`, `stale since …`, `the empty-wall picture is not this wall …` (absent, périmé, autre mur : normal à l'arrivée) → **Vide (`the wall is empty now: …`) : `STANDBY`, ②.** <!-- src: gui_builder.py:1116 «STANDBY» ; core/ops_monitor.py:285 «calibration» ; app.py:2053 «empty wall: none» ; app.py:2055 «other camera crop» ; app.py:2056 «scene changed» ; core/ops_monitor.py:1386 «stale since » ; core/reference_watch.py:234 «the empty-wall picture is not this wall» ; core/reference_watch.py:604 «the wall is empty now: » ; gui.py:3146 «Save to project» -->
- `the camera moved about … px …` (caméra bougée) → **`STANDBY`, vérifier zone et masque (`1 Rig`), puis ②.** <!-- src: core/reference_watch.py:231 «the camera moved about » ; gui_builder.py:1056 «STANDBY» ; gui_builder.py:642 «1 Rig» -->
- `empty wall: off` → **`3 Live` → cocher `Use empty wall`.** <!-- src: app.py:2052 «empty wall: off» ; gui_builder.py:644 «3 Live» ; gui_builder.py:1310 «Use empty wall» -->
- `OFF, but Calibrate chose …` → **`Ctrl+Shift+E`, `Advanced` → `Enhancement` → `Enable`, `Ctrl+Shift+E`.** <!-- src: core/ops_monitor.py:495 «OFF, but Calibrate chose» ; core/ops_monitor.py:479 «Ctrl+Shift+E» ; gui_builder.py:686 «Advanced» ; gui_builder.py:1741 «Enhancement» ; gui_builder.py:1749 «Enable» -->
- `ROI: off: YOLO watches the whole …`, `ROI: … cut to …`, `plate exposure: … RUN refuses it …` (zone ou mur vide d'un autre cadrage, d'une autre exposition) → **Retracer `Region of Interest` (①) si `ROI`, puis ②.** <!-- src: core/ops_monitor.py:523 «off: YOLO watches the whole» ; core/ops_monitor.py:540 «cut to » ; core/ops_monitor.py:577 «RUN refuses it» ; gui_builder.py:2039 «Region of Interest» ; core/ops_monitor.py:523 «ROI» -->
- `no IR light reaches the camera …` (pas d'IR) → **Allumer l'IR, vérifier son alimentation, ôter le bouchon.** <!-- src: core/ops_monitor.py:682 «no IR light reaches the camera» -->
- `The light is …x the empty-wall snapshot …` (la lumière a changé ; reste dans `Alerts:`) → **Au prochain mur vide : ②. Jamais vide : entre deux scènes, clic sur le slot du mur vide de cet éclairage, ②, `LIVE`.** <!-- src: core/ops_monitor.py:920 «The light is » ; core/ops_monitor.py:920 «the empty-wall snapshot» ; gui_builder.py:1369 «Alerts:» ; gui_builder.py:1413 «LIVE» -->
- `A dancer, 10 s: not started, …`, `stopped, …`, `not kept, …` (mur vide faux) → **la fin : ②, puis ③.** <!-- src: core/dancer_step.py:691 «A dancer, 10 s: not started, » ; core/dancer_step.py:695 «stopped, » ; core/dancer_step.py:699 «not kept, » -->
- `dancer size: not measured …`, `ENHANCEMENT OFF - run A DANCER …`, `… calibration looks stale …` → **③.** <!-- src: app.py:2601 «not measured» ; gui_builder.py:1737 «ENHANCEMENT OFF - run A DANCER» ; core/ops_monitor.py:882 «calibration looks stale» -->
- `dancer size: … below the ~100 px …`, `… too small for this input …` (orange, `3 Live`) → **À la pause : resserrer `Region of Interest`, ② et ③.** `belt glare BLINDS …` → **autre ceinture, ③.** <!-- src: core/ops_monitor.py:379 «dancer size» ; core/ops_monitor.py:379 «below the ~» ; gui_builder.py:644 «3 Live» ; core/dancer_step.py:660 «too small for this input» ; gui_builder.py:2039 «Region of Interest» ; core/dancer_step.py:357 «belt glare BLINDS» -->
- `No dancers tracked for … while RUNNING`, `Nobody seen by the detector for …`, `detector: nobody seen for …` (rien de vu) → **Danseurs au mur : l'IR, l'image. Sinon, un point qui reste : voir ce qui bouge là.** <!-- src: core/ops_monitor.py:845 «No dancers tracked for » ; core/ops_monitor.py:846 «while RUNNING» ; core/ops_monitor.py:838 «Nobody seen by the detector » ; core/ops_monitor.py:1129 «detector» ; core/ops_monitor.py:1446 «nobody seen » -->
- `More people than max_persons visible …` (trop de monde) → **Vérifier `Max dancers`.** <!-- src: core/ops_monitor.py:860 «More people than max_persons visible» ; gui_builder.py:1220 «Max dancers» -->
- `Calibrate: scene too noisy for MOG2 …` (bruit) → **Éclairer le mur plus également, puis `CALIBRATE`.** <!-- src: runtime/calibration_flows.py:468 «Calibrate: scene too noisy for MOG2» ; gui_builder.py:854 «CALIBRATE» -->
- `Calibration cancelled`, `Start the camera or play a recording first` → **`LIVE`, puis `CALIBRATE`.** <!-- src: runtime/calibration_flows.py:256 «Calibration cancelled» ; runtime/calibration_flows.py:272 «Start the camera or play a recording first» ; gui_builder.py:1413 «LIVE» ; gui_builder.py:854 «CALIBRATE» -->
- `disk:` (disque presque plein) → **Ne plus enregistrer (`REC`).** <!-- src: core/ops_monitor.py:444 «disk» ; gui_builder.py:1422 «REC» -->
- `Save FAILED: …` (enregistrement raté) → **Refaire `Ctrl+S` ; sinon noter l'heure (le réglage reste actif).** <!-- src: runtime/config_manager.py:422 «Save FAILED: » ; gui_builder.py:395 «Ctrl+S» -->
- `REMOTE` (session à distance), `Exposure-limited: … FPS`, `Config: … adjusted on load …`, `link limit … MB/s` (réglé par Thomas) → **Rien à faire.** <!-- src: gui_builder.py:434 «REMOTE» ; gui.py:2219 «Exposure-limited: » ; runtime/config_manager.py:697 «adjusted on load» ; core/ops_monitor.py:163 «link limit » -->

## 4. Secours

**Relancer l'app** — fermer WallDance, relancer (§1), `Launch` sur le même projet, ④, ⑤. Elle reprend seule le mur vide du projet (gardé chaque minute) ; pas de `empty wall: ready (…)` : ②. Figée : **[W1 ② : la fermer]**. <!-- src: gui.py:2556 «Launch» ; app.py:2054 «empty wall: ready» -->

**La version du 08/10 (prod)** — fermer la version de répétition (une seule app tient la caméra) ; double-clic sur l'icône `WallDance` : `WallDance Launcher` la démarre. `Update Available` : **Non**, sauf accord de Thomas ; `Local Version Differs` : `Keep local version`. Projet : **[W1 ② : celui du 08/10]**. Mêmes étapes ① ② ④ ⑤. <!-- src: launcher/install_manager.py:27 «WallDance» ; launcher/gui.py:75 «WallDance Launcher» ; launcher/gui.py:410 «Update Available» ; launcher/gui.py:196 «Local Version Differs» ; launcher/gui.py:204 «Keep local version» -->

**Le 12/12** — **[Thomas, 9–10/12 : la version de secours du spectacle et comment la lancer]**

## 5. Qui répond

**Les 7 et 8/12** — L'ordinateur de Thomas suit le portable : état, alertes, image ; `REMOTE` s'allume. Un message : bande jaune `MESSAGE DU SUPPORT`, jusqu'au clic sur `J'ai lu` ; le spectacle continue, rien à cocher (pas `Allow remote control during RUN`). Thomas au téléphone : **[numéro de Thomas]**. Noter l'heure de chaque souci. <!-- src: gui_builder.py:434 «REMOTE» ; gui_builder.py:521 «MESSAGE DU SUPPORT» ; gui_builder.py:522 «J'ai lu» ; gui_builder.py:1182 «Allow remote control during RUN» -->

**Le 12/12** — Personne : cette fiche est tout. Un souci : sa ligne au §3 ; rien ne va : relancer ; sinon le secours (§4).
