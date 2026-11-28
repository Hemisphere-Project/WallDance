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
27/11 docs5c (cordee-r2 @ d537acc, the automatic swipe P6 merged): citations re-pointed; one line on the list
« Auto: » after the warm-up, written for « suggest » released (off by default; the three rules of 7-8/12).
Room taken from: the warm-up's « 20 s environ », §5's « rien à cocher (pas Allow remote control during RUN) » and
« état, alertes, image » (the guide's §9 keeps them), the glosses « (session à distance) », « (réglé par Thomas) »
and « absent, périmé, autre mur » of the arrival line.
N3 (hub walldance#t-067, 28/11, cordee-r2 @ 13138ba + the automatic swipe's three patches, gates/sadv/final): for a
man alone -- no instruction ends in asking anyone; each ends in the fix, « noter l'heure et la phrase » and carry on,
or the fallback (§4). The two icons named: « WallDance (répétition) » (the build), « WallDance » (the 08/10
fallback, now step by step; its labels checked in its tree 63f4989). W1's laptop blanks became Thomas's
(« [à compléter par Thomas …] », filled before printing); no phone number here: §5 sends to the paper card. The
warm-up's swipe: never Apply alone; « Auto: » rewritten for suggest by default, advice only (STRINGS.txt of
gates/sadv/final). Links to docs/SITUATIONS_FR.md#<id> (gates/sit1/situations.json). Room taken from: the arrival
line's « (normal à l'arrivée) » detail, the warm-up's Apply steps, the old « Auto: » conditions,
§4-§5 written short, ③'s « juge la ceinture ».
docfix (28/11, the swipe's fix fe28d83 on r2-int): citations re-pointed onto it; the warm-up's swipe says what the app
does with « suggest » (Thomas's decision of 28/11, the review's F1): the verdict's « Apply … » switches between two
frames, the show runs on; « … (STANDBY: …) » reloads, STANDBY only; then Ctrl+S. Room taken from ③'s « au plus loin
de la caméra » (now « au fond du mur ») and « (rien de réglé) », and the swipe line's « rien à régler », « du swipe ».
-->

**Pour Nico, seul** — 7–8/12 et 12/12. `CALIBRATE` = le texte de l'écran ; **[à compléter par Thomas]** = rempli avant l'impression ; un lien = ce cas en images (livret [*Situations*](SITUATIONS_FR.md)). Chaque consigne finit par : la faire ; ou noter l'heure et la phrase, et continuer ; ou le secours (§4). <!-- src: gui_builder.py:890 «CALIBRATE» -->

## 1. Lancer

- Secteur branché, arrière surélevé ; IR allumé tout près de l'objectif ; aucune lampe dans l'image.
- Icône **« WallDance (répétition) »** → `WallDance - Select Project` : **[à compléter par Thomas : le projet]**, `Launch`. Jamais `Delete` ni `Rename`. <!-- src: gui.py:2545 «WallDance - Select Project» ; gui.py:2557 «Launch» ; gui.py:2561 «Delete» ; gui.py:2559 «Rename» -->
- `Previous session crashed (…)` en rouge (la séance d'avant s'est arrêtée net) : noter l'heure, continuer. <!-- src: services/crash_marker.py:174 «Previous session crashed (» -->

## 2. Installer, dans l'ordre

**① Le cadre** — onglet `1 Rig`. <!-- src: gui_builder.py:643 «1 Rig» -->

- Caméra fixée, mise au point sous IR (bouton QR : `Phone monitor`). Si elle bouge ensuite : ② à ⑤. <!-- src: gui_builder.py:858 «Phone monitor» -->
- `Region of Interest` (zone surveillée) : `Enable ROI`, double-clic sur l'image, tracer la zone dansée (+ marge). <!-- src: gui_builder.py:2070 «Region of Interest» ; gui_builder.py:2073 «Enable ROI» -->
- `Exclusion Mask` (cases ignorées) : `Edit` seulement où personne ne danse jamais ; dans le doute, rien. <!-- src: gui_builder.py:2041 «Exclusion Mask» ; gui_builder.py:2048 «Edit» -->
- Onglet `3 Live` : `Max dancers` = le nombre de danseurs sur le mur. <!-- src: gui_builder.py:645 «3 Live» ; gui_builder.py:1251 «Max dancers» -->

**② Mur vide, puis CALIBRATE** — onglet `2 Calibrate`, personne dans l'image (contrôle amélioration allumée). <!-- src: gui_builder.py:644 «2 Calibrate» -->

- `CALIBRATE`, 20 à 30 s ; un 2e appui annule. `Stopped: someone in view, …` ([quelqu'un dans l'image](SITUATIONS_FR.md#calibrate_with_dancer)) → **une personne : la faire sortir ; un objet : le masquer (`1 Rig` → `Exclusion Mask`, `Edit`, ses cases, `Edit`, `Ctrl+S`) ; puis `CALIBRATE`. Qui est dans le masque n'arrête rien et entre dans la photo du mur vide.** <!-- src: gui_builder.py:890 «CALIBRATE» ; runtime/calibration_flows.py:720 «Stopped: someone in view, » ; gui_builder.py:643 «1 Rig» ; gui_builder.py:2041 «Exclusion Mask» ; gui_builder.py:2048 «Edit» ; gui_builder.py:396 «Ctrl+S» -->
- `Scene Calibration`, en bas : `Kept gamma …` ou `Kept the current gamma …` = bon ; `YOLO now and then sees a person on the EMPTY wall …` ([un objet pris pour quelqu'un](SITUATIONS_FR.md#object_taken_for_person)) = le masquer ou l'ôter, `CALIBRATE`. <!-- src: gui.py:3127 «Scene Calibration» ; core/empty_wall.py:228 «Kept gamma » ; core/empty_wall.py:225 «Kept the current gamma » ; core/empty_wall.py:232 «YOLO now and then sees a person on the EMPTY wall» ; gui_builder.py:890 «CALIBRATE» -->
- Sa dernière ligne, `Wall light: …` : `being measured …` → **attendre, mur vide** ; `no verdict - … - Calibrate on the empty wall` → **`CALIBRATE` sur le mur vide** ; un verdict → **comme la ligne `wall light`.** <!-- src: core/light_verdict.py:84 «Wall light: » ; app.py:202 «being measured» ; app.py:205 «no verdict - » ; app.py:204 «Calibrate on the empty wall» ; gui_builder.py:890 «CALIBRATE» ; core/ops_monitor.py:701 «wall light» -->
- Puis `Save to project` (enregistrer) → `Saved to project.`, `Close` ; `empty wall: ready (…)` (prêt). <!-- src: gui.py:3147 «Save to project» ; gui.py:2443 «Saved to project.» ; gui.py:2445 «Close» ; app.py:2073 «empty wall: ready» -->
- Garder 15 s de ce mur vide : `REC`, un slot libre (1 à 9), 15 s, `REC`. Noter slot et éclairage. <!-- src: gui_builder.py:1453 «REC» -->
- `wall light` rouge, `aim the IR flood more toward …`, `move the IR flood back from the wall …`, ou `Kept the exposure, the bright spot stays at white` ([lumière inégale](SITUATIONS_FR.md#wall_light_uneven)) → **si possible, l'IR comme écrit, puis ②.** <!-- src: core/ops_monitor.py:701 «wall light» ; core/light_verdict.py:200 «aim the IR flood more toward » ; core/light_verdict.py:205 «move the IR flood back from the wall» ; core/calibration.py:403 «Kept the exposure, the bright spot stays at white» -->

**③ Un danseur, 10 s** — facultatif, après ② : `A DANCER, 10 s` (sans `RUN`) ; un danseur seul, entier, immobile 10 s au fond du mur. L'app règle taille et image : `Set: …` → `Keep and save` (garder) ou `Cancel - put back` (tout remettre). `two people on the wall - nothing set`, `nobody seen …`, `… saw her in …` → **refaire ainsi, éclairé.** <!-- src: gui_builder.py:988 «A DANCER, 10 s» ; gui_builder.py:1158 «RUN» ; runtime/calibration_flows.py:1082 «Set: » ; gui.py:986 «Keep and save» ; gui.py:989 «Cancel - put back» ; runtime/calibration_flows.py:1067 «two people on the wall - nothing set» ; runtime/calibration_flows.py:1064 «nobody seen » ; runtime/calibration_flows.py:1060 «saw her in » -->

**④ Vérifier** — la vérification se lance seule au démarrage et après chaque `CALIBRATE` (sinon `2 Calibrate`, `Check readiness`). En tête, `READY` seulement si toutes les lignes sont **[OK]** vert ; `NOT ALL CHECKED` : des lignes `[NOT CHECKED]` bleu-gris (pas vérifié, jamais vert ; `dancer size` avant ③). **[WARN]** orange, **[FAIL]** rouge : §3. <!-- src: gui_builder.py:890 «CALIBRATE» ; gui_builder.py:644 «2 Calibrate» ; gui_builder.py:927 «Check readiness» ; core/ops_monitor.py:59 «READY» ; core/ops_monitor.py:59 «NOT ALL CHECKED» ; core/ops_monitor.py:55 «NOT CHECKED» ; core/ops_monitor.py:362 «dancer size» ; gui.py:3288 «STATUS_WORDS.get(status» ; gui.py:3275 «"skip": NOT_CHECKED_COLOR» -->

**⑤ RUN** — rester en `RUN` (pas `STANDBY`) ; `Go-Live check: …` résume ④ ; `Ctrl+S` enregistre. <!-- src: gui_builder.py:1137 «RUN» ; gui_builder.py:1087 «STANDBY» ; core/ops_monitor.py:127 «Go-Live check: » ; gui_builder.py:396 «Ctrl+S» -->

- Ne toucher que `Max dancers`, ni lettre ni clic sur un slot. [Bande rouge](SITUATIONS_FR.md#recording_playing) `LECTURE - pas en direct` → **`LIVE`.** <!-- src: gui_builder.py:1251 «Max dancers» ; ui/adapter.py:346 «mvKey_P» ; gui_builder.py:501 «LECTURE - pas en direct» ; gui_builder.py:1444 «LIVE» -->
- La bande sous l'image : `OK` vert = rien à faire ; `WARN` orange, `FAIL` rouge : le pire souci, sa consigne (§3). `(+N: ...)` (N autres soucis) et `...` (abrégé) : rien. Boule à bord orange : danseur maintenu, pas vu. <!-- src: core/ops_monitor.py:55 «OK» ; core/ops_monitor.py:55 «WARN» ; core/ops_monitor.py:55 «FAIL» ; ui/line_fit.py:136 «(+» ; ui/line_fit.py:36 «...» ; core/visualization.py:34 «orange: holding» -->

**Échauffement seulement, facultatif** — `RUN`, danseurs depuis 2 min 30 : `Try other settings`. `keep …` : rien. Un autre réglage : noter l'heure et la phrase ; puis `Dismiss`, ou son bouton : `Apply …` change de modèle entre deux images, **le spectacle continue** ; `… (STANDBY: …)` : en `STANDBY` seulement. Appliqué : `Ctrl+S`. <!-- src: gui_builder.py:1137 «RUN» ; gui_builder.py:939 «Try other settings» ; core/swipe.py:493 «keep » ; gui_builder.py:975 «Dismiss» ; app.py:3266 «Apply » ; app.py:3270 «(STANDBY: » ; gui_builder.py:1087 «STANDBY» ; gui_builder.py:396 «Ctrl+S» -->

**Essai automatique** (`Auto: suggest`, par défaut) — [une ligne orange](SITUATIONS_FR.md#swipe_suggestion) qui finit par `note the time and this line` : la noter, `Dismiss`. Alerte rouge (`… set Auto to off`), image ou TouchDesigner lents : liste `Auto:` → `Auto: off`, `Ctrl+S` ; ne rien fermer ni reconstruire. <!-- src: core/swipe_auto.py:65 «Auto: suggest» @«MODE_OFF: "Auto: off"» ; core/swipe_auto.py:266 «note the time and this line» ; gui_builder.py:975 «Dismiss» @«label="Dismiss"» ; gui_builder.py:966 «Apply» @«label="Apply"» ; runtime/seamless_switch.py:119 «set Auto to off» @«AUTO_TODO =» ; core/swipe_auto.py:65 «Auto: » @«MODE_OFF: "Auto: off"» ; core/swipe_auto.py:65 «Auto: off» @«MODE_OFF: "Auto: off"» ; gui_builder.py:396 «Ctrl+S» -->

## 3. Orange ou rouge : la seule chose à faire

<!-- Readiness rows: core/ops_monitor.py check_* (rows rendered by gui.py show_readiness_rows); alerts: HealthMonitor.tick, each shown as a red toast "/!\ <message>" (ui/adapter.py:346), light_changed also kept in the Alerts strip (ui/adapter.py:34). -->

- `points: … had no point for …` ([pas suivi](SITUATIONS_FR.md#max_dancers_low)) → **Danseur : vérifier `Max dancers` ; ombre, technicien : rien.** <!-- src: core/ops_monitor.py:1143 «points» ; core/ops_monitor.py:1297 «had no point for» ; gui_builder.py:1244 «Max dancers» -->
- `not open`, `CAM: OFF`, `RECONNECTING`, `Camera down for …` (caméra perdue) → **Câble USB3, alimentation.** <!-- src: core/ops_monitor.py:134 «not open» ; gui_builder.py:415 «CAM:» ; gui_builder.py:425 «RECONNECTING» ; core/ops_monitor.py:962 «Camera down for » -->
- `camera stalls: …` orange, `the picture freezes often` rouge ([la caméra se fige](SITUATIONS_FR.md#camera_stalls)) → **À la prochaine pause : rebrancher à fond le câble USB de la caméra, aux deux bouts ; toujours orange : l'autre câble.** <!-- src: core/ops_monitor.py:1349 «camera stalls» ; core/ops_monitor.py:1356 «the picture freezes often» -->
- `below the 15 FPS show floor`, `FPS dropped to …`, `FPS:` en rouge ([image lente](SITUATIONS_FR.md#fps_under_floor)) → **Secteur branché ; fermer tout sauf WallDance et TouchDesigner.** <!-- src: core/ops_monitor.py:144 «FPS show floor» ; core/ops_monitor.py:841 «FPS dropped to » ; gui_builder.py:453 «FPS:» -->
- `gpu: … at/above 85 C`, `GPU at … C` (trop chaud) → **Surélever l'arrière, dégager les aérations.** <!-- src: core/ops_monitor.py:497 «at/above» ; core/ops_monitor.py:983 «GPU at » -->
- `tensorrt:`, `TensorRT OFF - running PyTorch (…)`, `[CPU FALLBACK]`, `Running on CPU`, `frame processing error …` en boucle (détection dégradée) → **Relancer l'app (§4). Jamais `Rebuild TRT` (efface le moteur).** <!-- src: core/ops_monitor.py:190 «tensorrt» ; runtime/model_controller.py:434 «TensorRT OFF - running PyTorch (» ; gui_builder.py:448 «[CPU FALLBACK]» ; runtime/main_loop.py:361 «Running on CPU» ; runtime/main_loop.py:471 «frame processing» ; gui_builder.py:483 «Rebuild TRT» -->
- `nothing listening on …`, `OSC send to … failing` ([rien ne part](SITUATIONS_FR.md#visuals_not_listening)) → **(Re)lancer TouchDesigner et son patch.** <!-- src: core/ops_monitor.py:258 «nothing listening on » ; core/osc_output.py:66 «OSC send to » -->
- `OSC: OFF`, `osc: disabled`, `output: OSC is off` ([envoi coupé](SITUATIONS_FR.md#osc_off)) → **`Advanced` → `OSC` → cocher `Enable OSC`.** <!-- src: gui_builder.py:431 «OSC:» ; core/ops_monitor.py:273 «disabled» ; core/ops_monitor.py:1385 «OSC is off» ; gui_builder.py:687 «Advanced» ; gui_builder.py:1681 «OSC» ; gui_builder.py:1698 «Enable OSC» -->
- `calibration: …`, `empty wall: none`, `other camera crop`, `scene changed`, `stale since …`, `the empty-wall picture is not this wall …` ([normal à l'arrivée](SITUATIONS_FR.md#no_plate)) → **Vide (`the wall is empty now: …`) : `STANDBY`, ②.** <!-- src: gui_builder.py:1147 «STANDBY» ; core/ops_monitor.py:285 «calibration» ; app.py:2072 «empty wall: none» ; app.py:2074 «other camera crop» ; app.py:2075 «scene changed» ; core/ops_monitor.py:1412 «stale since » ; core/reference_watch.py:234 «the empty-wall picture is not this wall» ; core/reference_watch.py:604 «the wall is empty now: » ; gui.py:3147 «Save to project» -->
- `the camera moved about … px …` (caméra bougée) → **`STANDBY`, vérifier zone et masque (`1 Rig`), puis ②.** <!-- src: core/reference_watch.py:231 «the camera moved about » ; gui_builder.py:1087 «STANDBY» ; gui_builder.py:643 «1 Rig» -->
- `empty wall: off` → **`3 Live` → cocher `Use empty wall`.** <!-- src: app.py:2071 «empty wall: off» ; gui_builder.py:645 «3 Live» ; gui_builder.py:1341 «Use empty wall» -->
- `OFF, but Calibrate chose …` → **`Ctrl+Shift+E`, `Advanced` → `Enhancement` → `Enable`, `Ctrl+Shift+E`.** <!-- src: core/ops_monitor.py:521 «OFF, but Calibrate chose» ; core/ops_monitor.py:505 «Ctrl+Shift+E» ; gui_builder.py:687 «Advanced» ; gui_builder.py:1772 «Enhancement» ; gui_builder.py:1780 «Enable» -->
- `ROI: off: YOLO watches the whole …`, `ROI: … cut to …`, `plate exposure: … RUN refuses it …` ([zone ou mur vide d'un autre cadrage, d'une autre exposition](SITUATIONS_FR.md#plate_other_crop)) → **Retracer `Region of Interest` (①) si `ROI`, puis ②.** <!-- src: core/ops_monitor.py:549 «off: YOLO watches the whole» ; core/ops_monitor.py:566 «cut to » ; core/ops_monitor.py:603 «RUN refuses it» ; gui_builder.py:2070 «Region of Interest» ; core/ops_monitor.py:549 «ROI» -->
- `no IR light reaches the camera …` (pas d'IR) → **Allumer l'IR, vérifier son alimentation, ôter le bouchon.** <!-- src: core/ops_monitor.py:708 «no IR light reaches the camera» -->
- `The light is …x the empty-wall snapshot …` (la lumière a changé ; reste dans `Alerts:`) → **Au prochain mur vide : ②. Jamais vide : entre deux scènes, clic sur le slot du mur vide de cet éclairage, ②, `LIVE`.** <!-- src: core/ops_monitor.py:946 «The light is » ; core/ops_monitor.py:946 «the empty-wall snapshot» ; gui_builder.py:1400 «Alerts:» ; gui_builder.py:1444 «LIVE» -->
- `A dancer, 10 s: not started, …`, `stopped, …`, `not kept, …` ([mur vide faux](SITUATIONS_FR.md#dancer_step_before_calibrate)) → **la fin : ②, puis ③.** <!-- src: core/dancer_step.py:691 «A dancer, 10 s: not started, » ; core/dancer_step.py:695 «stopped, » ; core/dancer_step.py:699 «not kept, » -->
- `dancer size: not measured …`, `ENHANCEMENT OFF - run A DANCER …`, `… calibration looks stale …` → **③.** <!-- src: app.py:2622 «not measured» ; gui_builder.py:1768 «ENHANCEMENT OFF - run A DANCER» ; core/ops_monitor.py:908 «calibration looks stale» -->
- `dancer size: … below the ~100 px …`, `… too small for this input …` (orange, `3 Live`) → **À la pause : resserrer `Region of Interest`, ② et ③.** `belt glare BLINDS …` → **autre ceinture, ③.** <!-- src: core/ops_monitor.py:379 «dancer size» ; core/ops_monitor.py:379 «below the ~» ; gui_builder.py:645 «3 Live» ; core/dancer_step.py:660 «too small for this input» ; gui_builder.py:2070 «Region of Interest» ; core/dancer_step.py:357 «belt glare BLINDS» -->
- `No dancers tracked for … while RUNNING`, `Nobody seen by the detector for …`, `detector: nobody seen for …` ([rien de vu](SITUATIONS_FR.md#point_on_nobody)) → **Danseurs au mur : l'IR, l'image. Sinon, un point qui reste : voir ce qui bouge là.** <!-- src: core/ops_monitor.py:871 «No dancers tracked for » ; core/ops_monitor.py:872 «while RUNNING» ; core/ops_monitor.py:864 «Nobody seen by the detector » ; core/ops_monitor.py:1155 «detector» ; core/ops_monitor.py:1472 «nobody seen » -->
- `More people than max_persons visible …` (trop de monde) → **Vérifier `Max dancers`.** <!-- src: core/ops_monitor.py:886 «More people than max_persons visible» ; gui_builder.py:1251 «Max dancers» -->
- `Calibrate: scene too noisy for MOG2 …` (bruit) → **Éclairer le mur plus également, puis `CALIBRATE`.** <!-- src: runtime/calibration_flows.py:468 «Calibrate: scene too noisy for MOG2» ; gui_builder.py:890 «CALIBRATE» -->
- `Calibration cancelled`, `Start the camera or play a recording first` → **`LIVE`, puis `CALIBRATE`.** <!-- src: runtime/calibration_flows.py:256 «Calibration cancelled» ; runtime/calibration_flows.py:272 «Start the camera or play a recording first» ; gui_builder.py:1444 «LIVE» ; gui_builder.py:890 «CALIBRATE» -->
- `disk:` (disque presque plein) → **Ne plus enregistrer (`REC`).** <!-- src: core/ops_monitor.py:470 «disk» ; gui_builder.py:1453 «REC» -->
- `Save FAILED: …` (enregistrement raté) → **Refaire `Ctrl+S` ; sinon noter l'heure (le réglage reste actif).** <!-- src: runtime/config_manager.py:426 «Save FAILED: » ; gui_builder.py:396 «Ctrl+S» -->
- `REMOTE`, `Exposure-limited: … FPS`, `Config: … adjusted on load …`, `link limit … MB/s` → **Rien à faire.** <!-- src: gui_builder.py:435 «REMOTE» ; gui.py:2220 «Exposure-limited: » ; runtime/config_manager.py:708 «adjusted on load» ; core/ops_monitor.py:163 «link limit » -->

## 4. Secours, et qui répond

**Relancer l'app** — fermer WallDance (sa croix), relancer « WallDance (répétition) », `Launch` sur le même projet, ④, ⑤ ; elle reprend seule le mur vide, sinon (pas de `empty wall: ready (…)`) : ②. Figée : Ctrl+Maj+Échap, la ligne `WallDance Control Panel`, « Fin de tâche » (jamais TouchDesigner). <!-- src: gui.py:2557 «Launch» ; app.py:2073 «empty wall: ready» ; gui.py:3507 «WallDance Control Panel» -->

**Secours pas à pas : l'icône « WallDance » (08/10)** — 1) Fermer la version de répétition ; TouchDesigner reste ouvert. 2) « WallDance » : `WallDance Launcher` lance l'app seul. 3) `Update Available` : **Non** ; `Local Version Differs` : `Keep local version`. 4) **[à compléter par Thomas : le projet]**, `Launch`. 5) ① ② ④ ⑤ (bilan : `Check readiness`). 6) Laisser le lanceur ouvert. Rien ne repart : spectacle sans suivi ; noter l'heure. <!-- src: launcher/gui.py:75 «WallDance Launcher» ; launcher/gui.py:410 «Update Available» ; launcher/gui.py:196 «Local Version Differs» ; launcher/gui.py:204 «Keep local version» ; gui.py:2557 «Launch» ; gui_builder.py:927 «Check readiness» -->

**Qui répond, 7 et 8/12** — Thomas absent (un peu au téléphone le 7/12) : ne pas l'attendre ; un souci : sa ligne du §3, sinon noter, continuer. Bande jaune `MESSAGE DU SUPPORT` : la lire, `J'ai lu` ; qui peut en envoyer : **[à compléter par Thomas]**. <!-- src: gui_builder.py:522 «MESSAGE DU SUPPORT» ; gui_builder.py:523 «J'ai lu» -->

**12/12** — Personne ne répond : cette fiche est tout. Secours humain : **[à compléter par Thomas : une personne, ou aucune]**. Téléphones : carte papier remise avec la fiche, jamais ici (document public).
