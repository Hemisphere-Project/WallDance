# WallDance — Situations

<!--
N1 (hub walldance#t-065, 28/11): the technician's booklet « Situations », in French, for Nico alone with the app on a
show night (Thomas away). One page per situation of the verified catalogue (tmp_analysis/r2-1126/gates/sit1:
CATALOGUE.md + situations.json, 28 situations, each fault reproduced by running cordee-r2 @ 13138ba on the 08/10
façade takes, l @ 1920 x 1056), in the catalogue's ranking. The build Nico runs is 13138ba plus the automatic
swipe's three patches of 28/11 (gates/sadv/final: « suggest » by default, a background suggestion is advice only,
no Apply button on it): the swipe page quotes their sentences from gates/sadv/final/STRINGS.txt.
Conventions:
- A code span = a string exactly as the app shows it (English). « … » inside a span = a value the app fills in.
  Every line with a code span carries a citation comment: `application/src/<file>:<line> «snippet»`, the snippet a
  literal piece of a span of that line, found in a string literal at that line of the tree the booklet ships in
  (written on 13138ba; re-pointed on 28/11 onto fe28d83, which carries the swipe's three patches and its fix);
  `STRINGS.txt (block N)` for a sentence of the three swipe patches (gates/sadv/final/STRINGS.txt).
- Each page heading carries `{ #<situation id> }` (attr_list on the website): the exercises link
  SITUATIONS_FR.md#<id>. The ids are the catalogue's.
- Pictures: docs/img/situations/<id>_avant.png / <id>_apres.png, taken later from the final build by the capture
  kit (gates/shots3) from the plan in tmp_analysis/r2-1126/n1/shots.json.
- Every instruction ends in something Nico does alone: note the time and the line and carry on, apply the fix, or
  switch to the fallback. The booklet must not contradict GUIDE_OPERATEUR_FR.md and FICHE_TECHNICIEN_FR.md.
docfix (28/11): citations re-pointed; « 14. Le mauvais modèle » answers its open question from the code: from x @ 1280 on
the façade's tall zone, the operator's swipe tries the 1920 x 1056 inputs (core/swipe_runner.py plan_candidates: both
models at the largest suitable input with an engine, while the dancer is under 200 px in the network), so it can name
l @ 1920 x 1056; its Apply is then another input size (runtime/seamless_switch.py SIZE_CHANGE_SEAMLESS = False): the
reload, STANDBY only, refused in RUN. The same-input « Apply … » switches between two frames in RUN (Thomas, 28/11).
The swipe page names the readiness row « … is still to load … » (msg/p7fix/STRINGS_P7FIX.txt).
-->

**Pour Nico, seul avec l'app un soir de spectacle.** Une page par situation : ce que l'écran montre, ce que ça veut dire, quoi faire, l'écran une fois réglé.

> **Comment lire ce livret**
>
> - `CALIBRATE` : un texte écrit **exactement** comme à l'écran (en anglais). <!-- application/src/gui_builder.py:890 «CALIBRATE» -->
> - `… had no point for … s …` : les « … » remplacent un nombre, une heure ou un endroit que l'app écrit. <!-- application/src/core/ops_monitor.py:1297 «had no point for» -->
> - Chaque situation a été **vraiment produite** avec cette version de l'app, sur les prises du 8/10 de la façade. Les lignes citées sont celles que l'app a écrites. Les chiffres (« un point sur elle 6 % du temps ») ont été mesurés sur ces prises.
> - Au banc, la prise est relue à 20 images par seconde ; sur le portable, l'app en fait environ 17. C'est la seule différence de l'écran.

**Toujours, trois gestes possibles — on peut les faire seul :**

1. **Noter et continuer** : sur une feuille, l'heure, la ligne (les premiers mots suffisent), ce qui a été fait. Les journaux de l'app sont relus après.
2. **Faire la consigne de la page.** Chaque situation dit quand : tout de suite, ou à la pause.
3. **Le secours** : si rien ne va, la version de secours, par son icône (la fiche technicien, partie « Secours »).

**La règle pendant une pièce** (celle du guide, §6.1) : orange, ne rien faire, noter, agir à la pause. Rouge : faire la consigne **seulement si elle n'arrête pas le spectacle** ; sinon noter, agir à la pause. Le seul réglage permis pendant une pièce : `Max dancers`. <!-- application/src/gui_builder.py:1251 «Max dancers» -->

**Sommaire**

- [Un soir normal](#normal_evening) · [Les cinq choses à regarder](#cinq_choses)
- **Les points perdus sans bruit** — [1. Photo du mur vide d'un autre jour, plus sombre](#plate_dark_other_day) · [2. L'envoi OSC coupé](#osc_off) · [3. Les visuels n'écoutent pas](#visuals_not_listening) · [4. Une danseuse dans une zone masquée](#mask_hides_dancer) · [5. La zone trop petite](#roi_too_small) · [6. Max dancers trop bas](#max_dancers_low) · [7. Une prise lue à la place de la caméra](#recording_playing)
- **Sur la façade** — [8. La lumière du mur inégale](#wall_light_uneven) · [9. CALIBRATE avec quelqu'un sur le mur](#calibrate_with_dancer) · [10. Un objet pris pour quelqu'un](#object_taken_for_person) · [11. Photo du mur vide d'un autre cadrage](#plate_other_crop) · [12. Photo du mur vide d'une autre exposition](#plate_other_exposure) · [13. La caméra se fige](#camera_stalls)
- **Les erreurs de réglage** — [14. Le mauvais modèle](#wrong_model) · [15. L'amélioration éteinte](#enhancement_off) · [16. Un danseur, 10 s : deux personnes](#dancer_step_two_people) · [17. Un danseur, 10 s : personne](#dancer_step_nobody) · [18. Un danseur, 10 s avant CALIBRATE](#dancer_step_before_calibrate) · [19. Pas de photo du mur vide](#no_plate) · [20. Moins de 15 images par seconde](#fps_under_floor) · [21. Photo du mur vide d'un autre jour, même lumière](#plate_other_day_same_light) · [22. Max dancers trop haut](#max_dancers_high)
- **À la fin** — [Ctrl+S oublié](#ctrl_s_forgotten) · [L'essai automatique (Auto:)](#swipe_suggestion) · [Deux messages rares](#osc_send_failing)

<div style="break-before: page"></div>

## Un soir normal { #normal_evening }

Trois prises de 90 s de la façade (mur2 6, 7 et 8), rejouées dans cette version, sans aucun défaut. Voici ce que l'écran montre, et **les lignes qui font peur mais sont normales**.

![Un soir normal : la ligne de santé verte, Dancers: 2, ids: D1 live | D2 live](img/situations/normal_evening_apres.png)

**Presque tout le temps (85 à 89 s sur 90) :**

- La ligne de santé, verte : `OK - all 7 checks fine (camera, output, points, empty wall, frames, engine, ...)`. <!-- application/src/core/ops_monitor.py:1529 «checks fine (» -->
- `3 Live`, la ligne des danseurs : `ids: D1 live | D2 live   belt: ready`. <!-- application/src/gui_builder.py:645 «3 Live» ; application/src/core/points_report.py:75 «ids: » ; application/src/core/points_report.py:14 «live» -->
- En bas : `Dancers:` `2` (le nombre de points envoyés) ; `FPS:` vers 17 sur le portable. <!-- application/src/gui_builder.py:567 «Dancers:» ; application/src/gui_builder.py:581 «FPS:» -->
- `2 Calibrate` : `empty wall: ready (10-08 21:36, 1260x1824)` (la photo du mur vide est prête). <!-- application/src/gui_builder.py:644 «2 Calibrate» ; application/src/app.py:2073 «empty wall: ready» -->
- `Alerts:` vide. <!-- application/src/gui_builder.py:1400 «Alerts:» -->

**Normal, même si ça fait peur :**

- La première seconde : `NOT CHECKED - points: not checked: no frame yet` (bleu-gris). Normal. <!-- application/src/core/ops_monitor.py:55 «NOT CHECKED» ; application/src/core/ops_monitor.py:1287 «not checked: » ; application/src/core/ops_monitor.py:1221 «no frame yet» -->
- Un rouge court : `FAIL - points: a body on the wall has had no point for 3 s (centre) - if it is a dancer: check Max dancers (3 Live)`. Il dure 1 à 2 s. Sur 90 s : 0 fois (mur2 7), 1 fois (mur2 6), 3 fois (mur2 8). <!-- application/src/core/ops_monitor.py:1295 «a body on the wall has» ; application/src/core/ops_monitor.py:1297 «had no point for» ; application/src/core/ops_monitor.py:1144 «check Max dancers (3 Live)» -->
- `Dancers:` `2 (1 held)` quelques secondes (3 à 8 s sur 90) : un point tenu un instant sans détection. <!-- application/src/gui_builder.py:567 «Dancers:» ; application/src/core/points_report.py:81 « held)» -->
- Dans `ids:`, un instant : `held on the foreground 0.1s` ou `coasting 0.2s` (le point est tenu, puis la détection revient). <!-- application/src/core/points_report.py:75 «ids:» ; application/src/core/points_report.py:15 «held on the foreground» ; application/src/core/points_report.py:18 «coasting» -->
- `D2 lost` tant que la deuxième danseuse n'est pas sur le mur. <!-- application/src/core/points_report.py:19 «lost» -->

![Le rouge court d'un soir normal : FAIL - points: a body on the wall has had no point for 3 s](img/situations/normal_evening_avant.png)

**Faire :** rien. Regarder que le rouge s'en va en 2 s environ. **Agir seulement** si le rouge reste (10 s ou plus), ou si `Dancers:` reste sous le nombre de danseurs sur le mur : alors, le n° qui correspond (4, 5 ou 6). <!-- application/src/gui_builder.py:567 «Dancers:» -->

Si on ne fait rien : rien de perdu. Sur ces trois prises, un point sur chaque danseuse 93 à 99 % du temps ; au plus 0,8 à 2,5 s sans point.

<div style="break-before: page"></div>

## Les cinq choses à regarder { #cinq_choses }

Quatre défauts de ce livret **gardent la ligne de santé verte** (n° 3, 4, 5, 20) ; un autre la garde presque verte (n° 14). Seules ces cinq choses les montrent.

1. **La ligne de santé**, sous l'image. Vert `OK` : rien à faire. Orange `WARN`, rouge `FAIL` : la fin de la ligne dit l'action ; ce livret la redit. <!-- application/src/core/ops_monitor.py:55 «OK» ; application/src/core/ops_monitor.py:55 «WARN» ; application/src/core/ops_monitor.py:55 «FAIL» -->
2. **`Dancers:`**, en bas : le nombre de points envoyés. Il doit valoir le nombre de danseurs sur le mur. Moins, plus de 10 s : n° 4, 5, 6. <!-- application/src/gui_builder.py:567 «Dancers:» -->
3. **La ligne `ids:`**, dans `3 Live` : chaque danseuse sur le mur doit être `live`. `lost` pour une danseuse qui danse : elle n'a pas de point (n° 4, 5, 6). `(nothing sent)` à la fin : rien ne part aux visuels (n° 2). <!-- application/src/gui_builder.py:645 «3 Live» ; application/src/core/points_report.py:14 «live» ; application/src/core/points_report.py:19 «lost» ; application/src/core/points_report.py:62 «(nothing sent)» ; application/src/core/points_report.py:75 «ids:» -->
4. **L'écran des visuels** (TouchDesigner) : les points bougent avec les danseuses. L'app ne peut pas savoir si les visuels écoutent (n° 3).
5. **Le bilan** (`2 Calibrate`, `Check readiness`), au démarrage et après chaque `CALIBRATE` : une ligne par contrôle, verte, orange ou rouge. Lire surtout `osc` (n° 3), `camera` (n° 20), `references` (n° 14, 21) et `wall light` (n° 8). Sur la façade, `wall light` est rouge et connu : l'en-tête peut dire `NOT READY` pour cette seule ligne (n° 8). <!-- application/src/gui_builder.py:644 «2 Calibrate» ; application/src/gui_builder.py:927 «Check readiness» ; application/src/gui_builder.py:890 «CALIBRATE» ; application/src/core/ops_monitor.py:273 «osc» ; application/src/core/ops_monitor.py:143 «camera» ; application/src/core/ops_monitor.py:1669 «references» ; application/src/core/ops_monitor.py:701 «wall light» ; application/src/core/ops_monitor.py:59 «NOT READY» -->

Un soir normal, la ligne `references` du bilan finit par `runs yolo11l-pose @ 1920x1056` (le réglage de la façade). <!-- application/src/core/ops_monitor.py:1661 «runs » ; application/src/core/ops_monitor.py:1669 «references» -->

<div style="break-before: page"></div>

## 1. La photo du mur vide est d'un autre jour, plus sombre { #plate_dark_other_day }

*Le plus grave : presque plus aucun point.*

![Avant : la ligne de santé rouge « the empty-wall picture is too dark », l'alerte « The light is >= 4x », Dancers: 0](img/situations/plate_dark_other_day_avant.png)

**L'écran**

- La ligne de santé, rouge dès la première seconde : `FAIL - empty wall: the empty-wall picture is too dark for the foreground (its brightest part 13 DN, 2.1 x its noise, needs 3 x) - light the wall as for the show, then CALIBRATE on the empty wall (2 Calibrate), then Save to project` <!-- application/src/core/reference_watch.py:663 «the empty-wall picture is too dark for the foreground (its brightest part» ; application/src/core/reference_watch.py:626 «light the wall as for the show, then CALIBRATE on the empty wall (» -->
- 5 s après, dans `Alerts:` et en message rouge : `The light is >= 4x the empty-wall snapshot (10-14 10:02): between two pieces, in STANDBY, Calibrate on the empty wall (live with the stage cleared, or on this lighting's empty take, then back to LIVE) and Save - never play a take in RUN` <!-- application/src/gui_builder.py:1400 «Alerts:» ; application/src/core/ops_monitor.py:946 «The light is » ; application/src/core/ops_monitor.py:315 «, in STANDBY, Calibrate on the empty wall (live with the stage cleared, or» ; application/src/core/ops_monitor.py:316 «never play a take in RUN» -->
- `Dancers:` `0`, alors que deux danseuses dansent. <!-- application/src/gui_builder.py:567 «Dancers:» -->
- Piège : dans `2 Calibrate`, la ligne dit encore `empty wall: ready (10-14 10:02, 1260x1824)`. <!-- application/src/gui_builder.py:644 «2 Calibrate» ; application/src/app.py:2073 «empty wall: ready» -->

**Ce que ça veut dire.** La photo du mur vide a été faite un autre jour, sous une lumière plus faible. L'app ne sépare plus les danseuses du mur : un point sur elles 6 % du temps seulement.

**Faire** — avant le spectacle, ou à la pause, mur vide (30 s sans points) :

1. Si quelqu'un tient les visuels, le prévenir : les points s'arrêtent 30 s.
2. `3 Live` → `STANDBY`. <!-- application/src/gui_builder.py:645 «3 Live» ; application/src/gui_builder.py:1147 «STANDBY» -->
3. Personne sur le mur.
4. `2 Calibrate` → `CALIBRATE`. Attendre 20 à 30 s, mur vide. <!-- application/src/gui_builder.py:644 «2 Calibrate» ; application/src/gui_builder.py:890 «CALIBRATE» -->
5. La carte `Scene Calibration` : `Save to project`. <!-- application/src/gui.py:3127 «Scene Calibration» ; application/src/gui.py:3147 «Save to project» -->
6. `3 Live` → `RUN`. <!-- application/src/gui_builder.py:645 «3 Live» ; application/src/gui_builder.py:1158 «RUN» -->

Pendant une pièce, le mur n'est jamais vide : noter l'heure, continuer, faire les étapes à la pause. (Le guide, partie « Entre deux pièces », dit aussi comment faire avec la prise de 15 s du mur vide de cet éclairage.)

**Une fois réglé**

![Après : la ligne de santé verte, Dancers: 2, empty wall: ready avec la date du jour](img/situations/plate_dark_other_day_apres.png)

- `OK - all 7 checks fine (camera, output, points, empty wall, frames, engine, ...)` ; `ids: D1 live | D2 live   belt: ready` ; `Dancers:` `2`. <!-- application/src/core/ops_monitor.py:1529 «checks fine (» ; application/src/core/points_report.py:75 «ids: » ; application/src/gui_builder.py:567 «Dancers:» -->
- `empty wall: ready (…)` avec la date et l'heure du jour. Un point sur elles 97,7 % du temps. <!-- application/src/app.py:2073 «empty wall: ready» -->

<div style="break-before: page"></div>

## 2. L'envoi OSC est coupé { #osc_off }

*Seulement orange, mais les visuels ne reçoivent rien.*

![Avant : la ligne de santé orange « OSC is off », la ligne ids qui finit par (nothing sent)](img/situations/osc_off_avant.png)

**L'écran**

- La ligne de santé, orange : `WARN - output: OSC is off: the visuals get no point - Advanced > OSC > Enable OSC` <!-- application/src/core/ops_monitor.py:1385 «OSC is off: the visuals get no point - » ; application/src/core/ops_monitor.py:266 «Advanced > OSC > Enable OSC» -->
- `ids: D1 live | D2 live   belt: ready   (nothing sent)` : les danseuses sont suivies, mais rien ne part. <!-- application/src/core/points_report.py:75 «ids: » ; application/src/core/points_report.py:62 «(nothing sent)» -->
- Le bilan, ligne orange : `osc: disabled: the visuals get no point - Advanced > OSC > Enable OSC` <!-- application/src/core/ops_monitor.py:273 «disabled: the visuals get no point - » -->

**Ce que ça veut dire.** L'envoi des points (OSC) a été coupé, puis enregistré ainsi (un essai, un projet copié). Les visuels ne reçoivent aucun point : 0 % pendant toute la pièce.

**Faire** — tout de suite, même en `RUN` (aucun suivi perdu) : <!-- application/src/gui_builder.py:1158 «RUN» -->

1. `Advanced` (en haut à droite) → `OSC` → cocher `Enable OSC`. <!-- application/src/gui_builder.py:687 «Advanced» ; application/src/gui_builder.py:1695 «OSC» ; application/src/gui_builder.py:1698 «Enable OSC» -->
2. `Ctrl+S`. <!-- application/src/gui_builder.py:396 «Ctrl+S» -->

**Une fois réglé**

![Après : la ligne de santé verte, la ligne ids sans (nothing sent)](img/situations/osc_off_apres.png)

- `OK - all 7 checks fine (camera, output, points, empty wall, frames, engine, ...)` ; `ids: D1 live | D2 live   belt: ready`. Les visuels reçoivent tout de suite. <!-- application/src/core/ops_monitor.py:1529 «checks fine (» ; application/src/core/points_report.py:75 «ids: » -->
- Le bilan : `osc: probe sent to 127.0.0.1:9000 (UDP - listener cannot be confirmed)` (vert). <!-- application/src/core/ops_monitor.py:260 «probe sent to » ; application/src/core/ops_monitor.py:261 «(UDP - listener cannot be confirmed)» -->

<div style="break-before: page"></div>

## 3. Les visuels (TouchDesigner) n'écoutent pas { #visuals_not_listening }

*Silencieux : la ligne de santé reste verte.*

![Avant : le bilan, la ligne osc rouge « host reachable but nothing listening »](img/situations/visuals_not_listening_avant.png)

**L'écran**

- La ligne de santé reste verte tout le temps : `OK - all 7 checks fine (camera, output, points, empty wall, frames, engine, ...)`. <!-- application/src/core/ops_monitor.py:1529 «checks fine (» -->
- Seul le bilan le dit (au démarrage, après `CALIBRATE`, ou `Check readiness`), ligne rouge : `osc: host reachable but nothing listening on 127.0.0.1:9000` <!-- application/src/gui_builder.py:890 «CALIBRATE» ; application/src/gui_builder.py:927 «Check readiness» ; application/src/core/ops_monitor.py:258 «host reachable but nothing listening on » -->
- Sur l'écran des visuels : aucun point ne bouge.

**Ce que ça veut dire.** L'app suit très bien (97,7 %) et envoie, mais personne ne reçoit : TouchDesigner n'est pas lancé, s'est arrêté, ou écoute sur un autre port que 9000.

**Faire** — tout de suite (aucun suivi perdu) :

1. Regarder l'écran des visuels : les points bougent-ils ?
2. Non : (re)lancer TouchDesigner et son patch, avec son entrée OSC sur le port 9000.
3. `2 Calibrate` → `Check readiness` : la ligne `osc` redevient verte. <!-- application/src/gui_builder.py:644 «2 Calibrate» ; application/src/gui_builder.py:927 «Check readiness» ; application/src/core/ops_monitor.py:273 «osc» -->

**Une fois réglé**

![Après : le bilan, la ligne osc verte « probe sent to 127.0.0.1:9000 »](img/situations/visuals_not_listening_apres.png)

- Le bilan : `osc: probe sent to 127.0.0.1:9000 (UDP - listener cannot be confirmed)` (vert). Les points bougent sur l'écran des visuels. <!-- application/src/core/ops_monitor.py:260 «probe sent to » ; application/src/core/ops_monitor.py:261 «(UDP - listener cannot be confirmed)» -->

<div style="break-before: page"></div>

## 4. Une danseuse dans une zone masquée { #mask_hides_dancer }

*Silencieux : ni rouge, ni orange, ni alerte.*

![Avant : la ligne de santé verte, Dancers: 1, ids: D1 live | D2 lost ; à côté, 1 Rig et les cases masquées sur la danseuse](img/situations/mask_hides_dancer_avant.png)

**L'écran**

- La ligne de santé reste verte : `OK - all 7 checks fine (camera, output, points, empty wall, frames, engine, ...)`. <!-- application/src/core/ops_monitor.py:1529 «checks fine (» -->
- `ids: D1 live | D2 lost   belt: ready` ; `Dancers:` `1`, alors que deux dansent. <!-- application/src/core/points_report.py:75 «ids: » ; application/src/core/points_report.py:19 «lost» ; application/src/gui_builder.py:567 «Dancers:» -->
- `1 Rig` : des cases rouges de l'`Exclusion Mask` (cases ignorées) là où elle danse. <!-- application/src/gui_builder.py:643 «1 Rig» ; application/src/gui_builder.py:2041 «Exclusion Mask» -->

**Ce que ça veut dire.** Des cases ont été masquées (pour cacher un objet), et une danseuse danse dessus. L'app ne la voit pas du tout : elle n'a aucun point.

**Faire** — à la pause (20 s sans points) :

1. `3 Live` → `STANDBY`. <!-- application/src/gui_builder.py:645 «3 Live» ; application/src/gui_builder.py:1147 «STANDBY» -->
2. `1 Rig` → `Exclusion Mask` → `Edit`. <!-- application/src/gui_builder.py:643 «1 Rig» ; application/src/gui_builder.py:2041 «Exclusion Mask» ; application/src/gui_builder.py:2048 «Edit» -->
3. Cliquer chaque case rouge où elle danse : elle redevient libre. Garder seulement les cases de l'objet.
4. `Edit` à nouveau (fini), puis `Ctrl+S`. <!-- application/src/gui_builder.py:2048 «Edit» ; application/src/gui_builder.py:396 «Ctrl+S» -->
5. `3 Live` → `RUN`. <!-- application/src/gui_builder.py:645 «3 Live» ; application/src/gui_builder.py:1158 «RUN» -->

Pendant une pièce : noter l'heure, continuer, le faire à la pause.

**Le même piège à `CALIBRATE`.** Une personne debout dans des cases masquées n'arrête pas `CALIBRATE` : il règle tout avec elle dans l'image (au banc : `Kept gamma 0.80 / CLAHE 1.5`, `Person height: 57 px  (measured from 31 detections)`, mesuré sur elle). Après avoir corrigé le masque : mur vide, `CALIBRATE` à nouveau, `Save to project`. <!-- application/src/gui_builder.py:890 «CALIBRATE» ; application/src/core/empty_wall.py:228 «Kept gamma » ; application/src/core/calibration.py:750 «Person height:» ; application/src/gui.py:3147 «Save to project» -->

**Une fois réglé**

![Après : ids: D1 live | D2 live, Dancers: 2](img/situations/mask_hides_dancer_apres.png)

- `ids: D1 live | D2 live   belt: ready` ; `Dancers:` `2`. <!-- application/src/core/points_report.py:75 «ids: » ; application/src/gui_builder.py:567 «Dancers:» -->

Si on ne fait rien : une danseuse sans point toute la pièce (sur deux danseuses, 48 % du temps avec un point).

<div style="break-before: page"></div>

## 5. La zone (Region of Interest) est trop petite { #roi_too_small }

*Silencieux : la ligne de santé reste verte.*

![Avant : la ligne de santé verte, ids: D1 live | D2 lost ; à côté, 1 Rig et la zone qui coupe le bas du mur](img/situations/roi_too_small_avant.png)

**L'écran**

- La ligne de santé reste verte : `OK - all 7 checks fine (camera, output, points, empty wall, frames, engine, ...)`. <!-- application/src/core/ops_monitor.py:1529 «checks fine (» -->
- `ids: D1 live | D2 lost   belt: ready` ; `Dancers:` `1`. <!-- application/src/core/points_report.py:75 «ids: » ; application/src/core/points_report.py:19 «lost» ; application/src/gui_builder.py:567 «Dancers:» -->
- `1 Rig` : le rectangle de la zone ne couvre pas tout le mur dansé ; une danseuse est dehors. <!-- application/src/gui_builder.py:643 «1 Rig» -->
- Le bilan change aussi sa ligne `wall light` : `wall light: the bottom of the zone is at least 51x brighter than the left (red above 4x): aim the IR flood more toward the top, away from the bottom, then Calibrate on the empty wall again`. **Ne pas bouger l'IR pour ça** : c'est la zone trop petite qui le fait dire. <!-- application/src/core/light_verdict.py:200 «aim the IR flood more toward the » ; application/src/core/ops_monitor.py:701 «wall light» -->

**Ce que ça veut dire.** La zone a été tracée trop serrée (ou la caméra a bougé). Hors de la zone, l'app ne voit personne : la danseuse dehors n'a jamais de point.

**Faire** — à la pause (1 min environ) :

1. `3 Live` → `STANDBY`. <!-- application/src/gui_builder.py:645 «3 Live» ; application/src/gui_builder.py:1147 «STANDBY» -->
2. `1 Rig` → `Region of Interest` : double-cliquer l'image, tirer un rectangle autour de tout le mur dansé, avec une marge ; double-cliquer à nouveau. <!-- application/src/gui_builder.py:643 «1 Rig» ; application/src/gui_builder.py:2070 «Region of Interest» -->
3. Personne sur le mur : `2 Calibrate` → `CALIBRATE`, puis `Save to project`. <!-- application/src/gui_builder.py:644 «2 Calibrate» ; application/src/gui_builder.py:890 «CALIBRATE» ; application/src/gui.py:3147 «Save to project» -->
4. `3 Live` → `RUN`. <!-- application/src/gui_builder.py:645 «3 Live» ; application/src/gui_builder.py:1158 «RUN» -->

Pendant une pièce : noter l'heure, continuer, le faire à la pause.

**Une fois réglé**

![Après : ids: D1 live | D2 live, la zone autour de tout le mur](img/situations/roi_too_small_apres.png)

- `ids: D1 live | D2 live   belt: ready` ; `Dancers:` `2`. <!-- application/src/core/points_report.py:75 «ids: » ; application/src/gui_builder.py:567 «Dancers:» -->
- La ligne `wall light` redevient celle de la façade (n° 8). <!-- application/src/core/ops_monitor.py:701 «wall light» -->

Si on ne fait rien : la danseuse hors zone n'a aucun point (sur deux danseuses, 50 % du temps avec un point).

<div style="break-before: page"></div>

## 6. Max dancers trop bas : 1 pour 2 danseuses { #max_dancers_low }

![Avant : la ligne de santé rouge « a body on the wall has had no point for 4 s », ids: D1 live seul](img/situations/max_dancers_low_avant.png)

**L'écran**

- 4 s après l'entrée de la deuxième danseuse, rouge : `FAIL - points: a body on the wall has had no point for 4 s (centre) - if it is a dancer: check Max dancers (3 Live)` <!-- application/src/core/ops_monitor.py:1295 «a body on the wall has» ; application/src/core/ops_monitor.py:1297 «had no point for» ; application/src/core/ops_monitor.py:1144 «check Max dancers (3 Live)» -->
- `ids: D1 live   belt: ready` : un seul numéro. <!-- application/src/core/points_report.py:75 «ids: » -->

**Ce que ça veut dire.** `Max dancers` est resté à 1 (une pièce solo, un projet copié). L'app ne suit qu'une danseuse : l'autre n'a pas de point. Le bilan n'a pas de ligne pour ça. <!-- application/src/gui_builder.py:1251 «Max dancers» -->

**Faire** — tout de suite, même pendant la pièce (c'est le seul réglage permis) :

1. `3 Live` → `Max dancers` = le nombre de danseuses de la pièce (ici 2). <!-- application/src/gui_builder.py:645 «3 Live» ; application/src/gui_builder.py:1251 «Max dancers» -->
2. `Ctrl+S`. <!-- application/src/gui_builder.py:396 «Ctrl+S» -->

**Une fois réglé**

![Après : la ligne de santé verte, ids: D1 live | D2 live](img/situations/max_dancers_low_apres.png)

- Le deuxième point arrive aussitôt : `OK - all 7 checks fine (camera, output, points, empty wall, frames, engine, ...)` ; `ids: D1 live | D2 live   belt: ready`. <!-- application/src/core/ops_monitor.py:1529 «checks fine (» ; application/src/core/points_report.py:75 «ids: » -->

Si on ne fait rien : une danseuse sans point (50 % du temps avec un point, sur deux).

<div style="break-before: page"></div>

## 7. Une prise enregistrée est lue à la place de la caméra { #recording_playing }

![Avant : le bandeau rouge LECTURE - pas en direct au-dessus de l'image, la ligne de santé orange « a recording is playing »](img/situations/recording_playing_avant.png)

**L'écran**

- Au-dessus de l'image, un bandeau rouge : `LECTURE - pas en direct`. <!-- application/src/gui_builder.py:501 «LECTURE - pas en direct» -->
- La ligne de santé, orange : `WARN - camera: a recording is playing, not the camera - LIVE (Recordings bar)   (+1: camera stalls)` <!-- application/src/core/ops_monitor.py:1373 «is playing, not the camera - LIVE (Recordings bar)» -->

**Ce que ça veut dire.** Un emplacement de prise (1 à 9) a été cliqué en `RUN`. **Les visuels suivent les danseuses d'un autre soir**, pas celles du mur. <!-- application/src/gui_builder.py:1158 «RUN» -->

**Faire** — tout de suite :

1. Cliquer `LIVE (camera)` sur le bandeau rouge (ou `LIVE` dans la barre des enregistrements). <!-- application/src/gui_builder.py:514 «LIVE (camera)» ; application/src/gui_builder.py:1446 «LIVE» -->

**Une fois réglé**

![Après : plus de bandeau, la ligne de santé verte](img/situations/recording_playing_apres.png)

- Le bandeau disparaît. `OK - all 7 checks fine (camera, output, points, empty wall, frames, engine, ...)`. <!-- application/src/core/ops_monitor.py:1529 «checks fine (» -->

<div style="break-before: page"></div>

## 8. La lumière du mur est inégale { #wall_light_uneven }

*La façade du 8/10 la donne déjà en rouge : on peut continuer.*

![Avant : le bilan, la ligne wall light rouge « the light is a spot in the middle of the zone »](img/situations/wall_light_uneven_avant.png)

**L'écran**

- Le bilan, ligne rouge (jamais dans la ligne de santé) : `wall light: the light is a spot in the middle of the zone, at least 9.2x brighter than the bottom left (red above 4x): move the IR flood back from the wall (or widen its beam) so it covers the whole zone, then Calibrate on the empty wall again` <!-- application/src/core/light_verdict.py:203 «light is a spot in the middle of the zone, » ; application/src/core/light_verdict.py:205 «IR flood back from the wall (or widen its beam) so it covers the whole zone, » -->

**Ce que ça veut dire.** Le projecteur IR fait une tache claire au milieu du mur. Sur la façade le 8/10, avec cette ligne rouge, l'app a quand même suivi 97,7 % du temps.

**Faire** — avant le spectacle seulement :

1. Si on peut : reculer le projecteur IR du mur, ou ouvrir son faisceau.
2. Puis, mur vide : `2 Calibrate` → `CALIBRATE`, `Save to project`. <!-- application/src/gui_builder.py:644 «2 Calibrate» ; application/src/gui_builder.py:890 «CALIBRATE» ; application/src/gui.py:3147 «Save to project» -->
3. Si le projecteur ne peut pas bouger : **continuer**, tant que la ligne de santé est verte et que `Dancers:` compte juste. <!-- application/src/gui_builder.py:567 «Dancers:» -->

Pendant une pièce : ne rien faire.

**Une fois réglé** (ou si on continue)

![Après : la ligne de santé verte, ids: D1 live | D2 live](img/situations/wall_light_uneven_apres.png)

- `OK - all 7 checks fine (camera, output, points, empty wall, frames, engine, ...)` ; `ids: D1 live | D2 live   belt: ready`. <!-- application/src/core/ops_monitor.py:1529 «checks fine (» ; application/src/core/points_report.py:75 «ids: » -->

<div style="break-before: page"></div>

## 9. CALIBRATE avec quelqu'un sur le mur { #calibrate_with_dancer }

![Avant : 2 Calibrate, le message rouge « Someone is in view, centre of the wall »](img/situations/calibrate_with_dancer_avant.png)

**L'écran**

- `CALIBRATE` commence par `Checking that the wall is empty...` (le mur est-il vide ?). <!-- application/src/gui_builder.py:890 «CALIBRATE» ; application/src/runtime/calibration_flows.py:678 «Checking that the wall is empty...» -->
- Dans la première seconde, un message : `Someone is in view, centre of the wall (x 524, y 611): Calibrate stopped and changed nothing. If it is a person: clear the wall. If it is an object: exclude it (1 Rig > Exclusion Mask) or move the Region of Interest off it. Then press Calibrate again.` <!-- application/src/runtime/calibration_flows.py:711 «Someone is in view, » ; application/src/runtime/calibration_flows.py:712 «If it is a person: clear the wall. If it is an object: » ; application/src/core/empty_wall.py:61 «Rig > Exclusion Mask) or move the Region of Interest off it» ; application/src/runtime/calibration_flows.py:713 «Then press Calibrate again.» -->

**Ce que ça veut dire.** Quelqu'un était sur le mur (une danseuse qui s'échauffe). `CALIBRATE` s'est arrêté et **n'a rien changé** : les anciens réglages restent. Rien de perdu. <!-- application/src/gui_builder.py:890 «CALIBRATE» -->

**Faire** — avant le spectacle ou à la pause :

1. Faire sortir tout le monde du mur.
2. `2 Calibrate` → `CALIBRATE` à nouveau (20 à 30 s). <!-- application/src/gui_builder.py:644 «2 Calibrate» ; application/src/gui_builder.py:890 «CALIBRATE» -->
3. La carte `Scene Calibration` : `Save to project`. <!-- application/src/gui.py:3127 «Scene Calibration» ; application/src/gui.py:3147 «Save to project» -->

**Une fois réglé**

![Après : Calibrating scene - keep the stage clear, puis la carte Scene Calibration](img/situations/calibrate_with_dancer_apres.png)

- `Calibrating scene - keep the stage clear` (le mur reste vide), puis la carte, avec par exemple `Kept gamma 1.28 / CLAHE 2.5`. <!-- application/src/runtime/calibration_flows.py:328 «Calibrating scene - keep the stage clear» ; application/src/core/empty_wall.py:228 «Kept gamma » -->

<div style="break-before: page"></div>

## 10. Un objet pris pour quelqu'un arrête CALIBRATE { #object_taken_for_person }

![Avant : 2 Calibrate, le message « Someone is in view, bottom left of the wall », alors que le mur est vide](img/situations/object_taken_for_person_avant.png)

**L'écran**

- Le mur est vide, et pourtant : `Someone is in view, bottom left of the wall (x 405, y 1623): Calibrate stopped and changed nothing. If it is a person: clear the wall. If it is an object: exclude it (1 Rig > Exclusion Mask) or move the Region of Interest off it. Then press Calibrate again.` <!-- application/src/runtime/calibration_flows.py:711 «Someone is in view, » ; application/src/runtime/calibration_flows.py:712 «If it is a person: clear the wall. If it is an object: » ; application/src/core/empty_wall.py:61 «Rig > Exclusion Mask) or move the Region of Interest off it» ; application/src/runtime/calibration_flows.py:713 «Then press Calibrate again.» -->

**Ce que ça veut dire.** Un objet sur le mur (une statue, une échelle, un manteau) ressemble à une personne pour la détection. `CALIBRATE` ne finira pas tant qu'il est là ; en `RUN`, il pourrait prendre un point. <!-- application/src/gui_builder.py:890 «CALIBRATE» ; application/src/gui_builder.py:1158 «RUN» -->

**Faire** — avant le spectacle ou à la pause (1 min environ) :

1. Si on peut : enlever l'objet. Sinon : `1 Rig` → `Exclusion Mask` → `Edit`, cliquer **seulement** les cases de l'objet (elles deviennent rouges), `Edit` à nouveau. <!-- application/src/gui_builder.py:643 «1 Rig» ; application/src/gui_builder.py:2041 «Exclusion Mask» ; application/src/gui_builder.py:2048 «Edit» -->
2. `Ctrl+S`. <!-- application/src/gui_builder.py:396 «Ctrl+S» -->
3. `2 Calibrate` → `CALIBRATE`, puis `Save to project`. <!-- application/src/gui_builder.py:644 «2 Calibrate» ; application/src/gui_builder.py:890 «CALIBRATE» ; application/src/gui.py:3147 «Save to project» -->

Attention : une danseuse qui danse plus tard sur ces cases n'aura pas de point (n° 4). Masquer le moins possible.

**Une fois réglé**

![Après : la carte Scene Calibration, Kept gamma 1.42 / CLAHE 2.5](img/situations/object_taken_for_person_apres.png)

- La carte `Scene Calibration`, avec par exemple `Kept gamma 1.42 / CLAHE 2.5`. <!-- application/src/gui.py:3127 «Scene Calibration» ; application/src/core/empty_wall.py:228 «Kept gamma » -->

<div style="break-before: page"></div>

## 11. La photo du mur vide est d'un autre cadrage { #plate_other_crop }

*Le 8/10, la prise mur 5 a tourné toute la soirée ainsi.*

![Avant : la ligne de santé rouge « from another camera crop », 2 Calibrate : empty wall: other camera crop](img/situations/plate_other_crop_avant.png)

**L'écran**

- La ligne de santé, rouge dès la première seconde : `FAIL - empty wall: from another camera crop - when the wall is empty: CALIBRATE (2 Calibrate), then Save to project   (+2: plate exposure, points)` <!-- application/src/core/ops_monitor.py:1423 «from another camera crop - » ; application/src/core/ops_monitor.py:1149 «when the wall is empty: CALIBRATE (» -->
- `2 Calibrate` : `empty wall: other camera crop - Calibrate on the empty wall (10-08 20:06, 1296x1776)` <!-- application/src/gui_builder.py:644 «2 Calibrate» ; application/src/app.py:2074 «empty wall: other camera crop - Calibrate on the empty wall» -->
- Le bilan, ligne rouge : `plate exposure: taken on a 1296x1776 picture, the camera gives 1260x1824 - RUN refuses it: Calibrate on the empty wall` <!-- application/src/core/ops_monitor.py:598 «picture, the camera gives » ; application/src/core/ops_monitor.py:603 «- RUN refuses it: Calibrate on the empty wall» -->

**Ce que ça veut dire.** Le cadrage de la caméra a changé après la photo du mur vide. L'app refuse cette photo : plus de points tenus, plus de contrôle des corps sans point (ici, la détection suit encore 97 % du temps).

**Faire** — avant le spectacle, ou à la pause, mur vide (30 s sans points) :

1. Si quelqu'un tient les visuels, le prévenir : les points s'arrêtent 30 s.
2. `3 Live` → `STANDBY`. <!-- application/src/gui_builder.py:645 «3 Live» ; application/src/gui_builder.py:1147 «STANDBY» -->
3. Personne sur le mur : `2 Calibrate` → `CALIBRATE` (20 à 30 s). <!-- application/src/gui_builder.py:644 «2 Calibrate» ; application/src/gui_builder.py:890 «CALIBRATE» -->
4. `Save to project`. <!-- application/src/gui.py:3147 «Save to project» -->
5. `3 Live` → `RUN`. <!-- application/src/gui_builder.py:645 «3 Live» ; application/src/gui_builder.py:1158 «RUN» -->

Pendant une pièce : noter l'heure, continuer.

**Une fois réglé**

![Après : la ligne de santé verte, empty wall: ready (…, 1260x1824)](img/situations/plate_other_crop_apres.png)

- `OK - all 7 checks fine (camera, output, points, empty wall, frames, engine, ...)` ; `empty wall: ready (…, 1260x1824)`. <!-- application/src/core/ops_monitor.py:1529 «checks fine (» ; application/src/app.py:2073 «empty wall: ready» -->

<div style="break-before: page"></div>

## 12. La photo du mur vide est d'une autre exposition { #plate_other_exposure }

![Avant : la ligne de santé rouge « plate exposure: plate taken at 25.0 ms, the camera is at 12.0 ms »](img/situations/plate_other_exposure_avant.png)

**L'écran**

- La ligne de santé, rouge : `FAIL - plate exposure: plate taken at 25.0 ms, the camera is at 12.0 ms - RUN refuses it: Calibrate on the empty wall   (+1: points)` <!-- application/src/core/foreground.py:76 «ms, the camera is at » ; application/src/core/ops_monitor.py:603 «- RUN refuses it: Calibrate on the empty wall» -->
- Une seconde après : `FAIL - empty wall: no longer matches the wall (...) - when the wall is empty: CALIBRATE (2 Calibrate), then Save to project   (+2: ...)` <!-- application/src/core/ops_monitor.py:1425 «no longer matches the wall» ; application/src/core/ops_monitor.py:1149 «when the wall is empty: CALIBRATE (» -->
- `2 Calibrate` : `empty wall: scene changed - Calibrate on the empty wall (10-08 21:36, 1260x1824)` <!-- application/src/gui_builder.py:644 «2 Calibrate» ; application/src/app.py:2075 «empty wall: scene changed - Calibrate on the empty wall» -->

**Ce que ça veut dire.** L'exposition de la caméra a changé après la photo du mur vide (à la main, ou par un `CALIBRATE` sous une autre lumière). L'app refuse la photo : plus de points tenus, plus de contrôle des corps sans point. <!-- application/src/gui_builder.py:890 «CALIBRATE» -->

**Faire** — avant le spectacle, ou à la pause, mur vide (30 s sans points) :

1. Si quelqu'un tient les visuels, le prévenir : les points s'arrêtent 30 s.
2. `3 Live` → `STANDBY`. <!-- application/src/gui_builder.py:645 «3 Live» ; application/src/gui_builder.py:1147 «STANDBY» -->
3. Personne sur le mur : `2 Calibrate` → `CALIBRATE` (20 à 30 s). <!-- application/src/gui_builder.py:644 «2 Calibrate» ; application/src/gui_builder.py:890 «CALIBRATE» -->
4. `Save to project`. <!-- application/src/gui.py:3147 «Save to project» -->
5. `3 Live` → `RUN`. <!-- application/src/gui_builder.py:645 «3 Live» ; application/src/gui_builder.py:1158 «RUN» -->

Pendant une pièce : noter l'heure, continuer.

**Une fois réglé**

![Après : la ligne de santé verte, empty wall: ready](img/situations/plate_other_exposure_apres.png)

- `OK - all 7 checks fine (camera, output, points, empty wall, frames, engine, ...)` ; `empty wall: ready (…, 1260x1824)`. <!-- application/src/core/ops_monitor.py:1529 «checks fine (» ; application/src/app.py:2073 «empty wall: ready» -->

<div style="break-before: page"></div>

## 13. La caméra se fige (USB) { #camera_stalls }

![Avant : la ligne de santé orange « camera stalls: 2.0 a minute », l'image figée un instant](img/situations/camera_stalls_avant.png)

**L'écran**

- Pendant un arrêt de 4 s, l'image se fige ; la ligne passe en bleu-gris : `NOT CHECKED - points: not checked: no new frame processed` <!-- application/src/core/ops_monitor.py:55 «NOT CHECKED» ; application/src/core/ops_monitor.py:1289 «not checked: no new frame processed» -->
- Après le deuxième arrêt, orange : `WARN - camera stalls: 2.0 a minute (2 in the last 0.4 min of camera) - at the next break: reseat the camera's USB cable at both ends, then try the other cable` <!-- application/src/core/ops_monitor.py:1354 «min of camera)» ; application/src/core/ops_monitor.py:1148 «reseat the camera's USB cable at both ends, then try the other cable» -->
- Le orange reste environ 1 min 30 après le dernier arrêt. Plus souvent (6 par minute) : rouge, `the picture freezes often`. <!-- application/src/core/ops_monitor.py:1356 «the picture freezes often» -->

**Ce que ça veut dire.** La caméra s'arrête quelques secondes : un câble USB3 mal enfoncé ou trop long, un hub, le port du portable. Chaque arrêt coûte 2 à 6 s sans point frais ; les points reviennent seuls.

**Faire :**

1. Pendant la pièce : **rien**. Noter l'heure. Les points reviennent d'eux-mêmes.
2. À la pause : rebrancher à fond le câble USB de la caméra, aux deux bouts.
3. Toujours orange ou rouge après : mettre l'autre câble.

**Une fois réglé**

![Après : la ligne de santé verte](img/situations/camera_stalls_apres.png)

- `OK - all 7 checks fine (camera, output, points, empty wall, frames, engine, ...)`. <!-- application/src/core/ops_monitor.py:1529 «checks fine (» -->

<div style="break-before: page"></div>

## 14. Le mauvais modèle pour la façade { #wrong_model }

*Presque silencieux : la ligne de santé est verte presque tout le temps.*

![Avant : le bilan, la ligne net input « square 1280 … suits yolo_shape 1760x960 », la ligne references « runs yolo11x-pose @ 1280 »](img/situations/wrong_model_avant.png)

**L'écran**

- Le bilan, ligne `references` : elle finit par `runs yolo11x-pose @ 1280` au lieu de `runs yolo11l-pose @ 1920x1056`. <!-- application/src/core/ops_monitor.py:1661 «runs » ; application/src/core/ops_monitor.py:1669 «references» -->
- Le bilan, ligne `net input`, **verte** : `net input: square 1280, 46 % padding for the 965x1771 ROI; this ROI's shape suits yolo_shape 1760x960`. Seul indice. <!-- application/src/core/ops_monitor.py:222 «% padding for the » ; application/src/core/ops_monitor.py:233 «; this ROI's shape suits yolo_shape » ; application/src/core/ops_monitor.py:234 «net input» -->
- De temps en temps, un rouge `FAIL - points: a body on the wall has had no point for 4 s (centre) - if it is a dancer: check Max dancers (3 Live)` (une fois en 40 s). <!-- application/src/core/ops_monitor.py:1295 «a body on the wall has» ; application/src/core/ops_monitor.py:1297 «had no point for» ; application/src/core/ops_monitor.py:1144 «check Max dancers (3 Live)» -->

**Ce que ça veut dire.** Le projet tourne avec le modèle « x » à 1280 (un projet copié d'une salle, ou un réglage changé) au lieu de « l » à 1920 x 1056. Le point saute : 89 % du temps sur elles au lieu de 98 %, jusqu'à 4 s sans point.

**Faire** — à l'échauffement :

1. Danseuses sur le mur depuis 2 min 30, en `RUN` : `2 Calibrate` → `Try other settings`. Le spectacle continue. <!-- application/src/gui_builder.py:1158 «RUN» ; application/src/gui_builder.py:644 «2 Calibrate» ; application/src/gui_builder.py:939 «Try other settings» -->
2. Depuis « x » à 1280, sur la zone haute de la façade, l'essai compare aussi les entrées 1920 x 1056 des deux modèles (leurs moteurs sont sur le portable). Il propose un autre réglage, par exemple `l @ 1920 x 1056 covers … % of the dancers, …` (ou le modèle « x » à 1920 x 1056 : les mêmes gestes), avec le bouton `Apply l @ 1920 x 1056 (STANDBY: a few seconds without tracking)` : une autre taille d'entrée, l'app doit recharger le modèle, quelques secondes sans suivi. En `RUN`, elle refuse et rien ne change : `Apply: stop the show first - this Apply reloads the model (another input size), a few seconds without tracking`. Donc : `3 Live` → `STANDBY`, cliquer ce bouton, attendre `Running l @ 1920 x 1056 (the swipe's pick) - Save the project to keep it`, puis `RUN`. Enfin `Ctrl+S`. <!-- application/src/core/swipe.py:512 « covers » ; application/src/core/swipe.py:512 « % of the dancers, » ; application/src/app.py:3270 «(STANDBY: a few seconds without tracking)» ; application/src/gui_builder.py:1158 «RUN» ; application/src/app.py:3345 «Apply: stop the show first - this Apply reloads the model » ; application/src/runtime/seamless_switch.py:571 «another input size» ; application/src/app.py:3346 «), a few seconds without tracking» ; application/src/gui_builder.py:645 «3 Live» ; application/src/gui_builder.py:1147 «STANDBY» ; application/src/app.py:3457 «(the swipe's pick) - Save the project to keep it» ; application/src/gui_builder.py:396 «Ctrl+S» -->
3. Un bouton sans `(STANDBY …)`, `Apply …` (la même taille d'entrée, l'autre modèle) : le cliquer en `RUN`. L'app change de modèle entre deux images, **le spectacle continue**. Puis `Ctrl+S`. <!-- application/src/app.py:3270 «(STANDBY» ; application/src/app.py:3266 «Apply » ; application/src/gui_builder.py:1158 «RUN» ; application/src/gui_builder.py:396 «Ctrl+S» -->
4. Il dit `keep …`, ou on n'a pas le temps : noter l'heure et la ligne `references`, continuer. L'app suit quand même, moins bien. <!-- application/src/core/swipe.py:493 «keep » ; application/src/core/ops_monitor.py:1669 «references» -->

Pendant une pièce : noter l'heure, continuer. À la pause, le verdict vaut encore 30 min : les mêmes gestes ; plus vieux, l'app répond `Swipe Apply: the swipe is … min old: run it again` : noter l'heure, continuer, refaire l'essai au prochain échauffement. <!-- application/src/app.py:3353 «Swipe Apply: » ; application/src/runtime/swipe_controller.py:602 «the swipe is » ; application/src/runtime/swipe_controller.py:602 « min old: run it again» -->

### La taille du danseur { #dancer_too_small }

Avec ce modèle, après l'étape `A DANCER, 10 s`, la ligne `dancer size` passe à l'orange : `dancer size: dancers ~104 px -> ~75 px in YOLO's input at 1280, below the ~100 px YOLO needs: …`. La même étape peut dire `belt glare BLINDS …` (la ceinture éblouit). Même consigne : `Try other settings` ci-dessus. Avec le bon modèle, la ligne est verte. <!-- application/src/gui_builder.py:988 «A DANCER, 10 s» ; application/src/core/ops_monitor.py:379 «px YOLO needs:» ; application/src/core/dancer_step.py:357 «belt glare BLINDS» ; application/src/gui_builder.py:939 «Try other settings» ; application/src/core/ops_monitor.py:379 «dancer size» -->

**Une fois réglé**

![Après : la ligne references « runs yolo11l-pose @ 1920x1056 », la ligne de santé verte](img/situations/wrong_model_apres.png)

- `OK - all 7 checks fine (camera, output, points, empty wall, frames, engine, ...)` ; le bilan : `runs yolo11l-pose @ 1920x1056`. <!-- application/src/core/ops_monitor.py:1529 «checks fine (» ; application/src/core/ops_monitor.py:1661 «runs » -->
- Après `A DANCER, 10 s` : `dancer size: dancers ~101 px -> ~110 px in YOLO's input at 1920x1056` (vert). <!-- application/src/gui_builder.py:988 «A DANCER, 10 s» ; application/src/core/ops_monitor.py:367 «px in YOLO's input at » -->

<div style="break-before: page"></div>

## 15. L'amélioration de l'image est éteinte { #enhancement_off }

*Rouge, mais sans perte mesurée sur la façade.*

![Avant : la ligne de santé rouge « enhancement: OFF, but Calibrate chose gamma 1.21 / CLAHE 2.5 »](img/situations/enhancement_off_avant.png)

**L'écran**

- La ligne de santé, rouge : `FAIL - enhancement: OFF, but Calibrate chose gamma 1.21 / CLAHE 2.5 for this project (2026-10-08 21:36): turn it on - Ctrl+Shift+E, Advanced > Enhancement > Enable` <!-- application/src/core/ops_monitor.py:521 «OFF, but Calibrate chose » ; application/src/core/ops_monitor.py:505 «Ctrl+Shift+E, Advanced > Enhancement > Enable» -->

**Ce que ça veut dire.** L'amélioration de l'image a été éteinte après `CALIBRATE` (le 8/10, toute la soirée a tourné ainsi). Sur la façade, la lumière suffit : l'app suit 97,7 % du temps avec ou sans. <!-- application/src/gui_builder.py:890 «CALIBRATE» -->

**Faire** — à la pause, ou avant le spectacle (aucun suivi perdu) :

1. `Ctrl+Shift+E` (le mode expert s'ouvre). <!-- application/src/core/ops_monitor.py:505 «Ctrl+Shift+E» -->
2. `Advanced` → `Enhancement` → cocher `Enable`. <!-- application/src/gui_builder.py:687 «Advanced» ; application/src/gui_builder.py:1773 «Enhancement» ; application/src/gui_builder.py:1780 «Enable» -->
3. `Ctrl+Shift+E` (le mode expert se ferme), puis `Ctrl+S`. <!-- application/src/core/ops_monitor.py:505 «Ctrl+Shift+E» ; application/src/gui_builder.py:396 «Ctrl+S» -->

Pendant une pièce : noter l'heure, continuer.

**Une fois réglé**

![Après : la ligne de santé verte](img/situations/enhancement_off_apres.png)

- `OK - all 7 checks fine (camera, output, points, empty wall, frames, engine, ...)`. <!-- application/src/core/ops_monitor.py:1529 «checks fine (» -->

<div style="break-before: page"></div>

## 16. Un danseur, 10 s, avec deux personnes sur le mur { #dancer_step_two_people }

![Avant : 2 Calibrate, le message « A dancer, 10 s: two people on the wall - nothing set »](img/situations/dancer_step_two_people_avant.png)

**L'écran**

- À la fin des 10 s de `A DANCER, 10 s` : `A dancer, 10 s: two people on the wall - nothing set. ONE dancer alone on the wall, then A DANCER, 10 s again.` <!-- application/src/gui_builder.py:988 «A DANCER, 10 s» ; application/src/runtime/calibration_flows.py:1067 «two people on the wall - nothing set. ONE dancer alone on the wall, then» -->

**Ce que ça veut dire.** Les deux danseuses sont restées sur le mur. L'étape **n'a rien réglé** : l'ancienne taille reste. Rien de perdu.

**Faire** — avant le spectacle ou à la pause (10 s) :

1. Une seule danseuse sur le mur, tout le corps éclairé, immobile, au plus loin de la caméra.
2. `2 Calibrate` → `A DANCER, 10 s` à nouveau. <!-- application/src/gui_builder.py:644 «2 Calibrate» ; application/src/gui_builder.py:988 «A DANCER, 10 s» -->
3. Sur sa carte : `Keep and save`. <!-- application/src/gui.py:986 «Keep and save» -->

**Une fois réglé**

![Après : la carte de l'étape, Set: person height … ; belt glare: fine](img/situations/dancer_step_two_people_apres.png)

- La carte : `Set: person height 101 px (~110 px at the network, 1920x1056); enhancement ON, gamma 1.07 / CLAHE 2.5; belt glare: fine. …` <!-- application/src/runtime/calibration_flows.py:1072 «px at the network, » ; application/src/core/dancer_step.py:363 «belt glare: fine» -->

<div style="break-before: page"></div>

## 17. Un danseur, 10 s, avec personne sur le mur { #dancer_step_nobody }

![Avant : 2 Calibrate, le message « A dancer, 10 s: nobody seen on the wall - nothing set »](img/situations/dancer_step_nobody_avant.png)

**L'écran**

- À la fin des 10 s : `A dancer, 10 s: nobody seen on the wall - nothing set. One dancer standing on the wall, then A DANCER, 10 s again.` <!-- application/src/runtime/calibration_flows.py:1064 «nobody seen on the wall - nothing set. One dancer standing on the wall, then» -->

**Ce que ça veut dire.** Le bouton a été pressé avant que la danseuse soit en place. Rien n'est réglé. Rien de perdu.

**Faire** — avant le spectacle ou à la pause (10 s) :

1. Une danseuse debout sur le mur, seule.
2. `2 Calibrate` → `A DANCER, 10 s` à nouveau. <!-- application/src/gui_builder.py:644 «2 Calibrate» ; application/src/gui_builder.py:988 «A DANCER, 10 s» -->
3. Sur sa carte : `Keep and save`. <!-- application/src/gui.py:986 «Keep and save» -->

**Une fois réglé**

![Après : la carte de l'étape, Set: person height …](img/situations/dancer_step_nobody_apres.png)

- La carte : `Set: person height 101 px (~110 px at the network, 1920x1056); enhancement ON, gamma 1.07 / CLAHE 2.5; belt glare: fine. …` <!-- application/src/runtime/calibration_flows.py:1072 «px at the network, » ; application/src/core/dancer_step.py:363 «belt glare: fine» -->

<div style="break-before: page"></div>

## 18. Un danseur, 10 s, avant CALIBRATE sur le mur vide { #dancer_step_before_calibrate }

![Avant : la carte de l'étape, « (the one running: no clean empty-wall check to go higher) »](img/situations/dancer_step_before_calibrate_avant.png)

**L'écran**

- La carte règle quand même la taille, mais dit entre parenthèses : `Set: person height 101 px (~109 px at the network, 1920x1056); enhancement ON, gamma 1.21 / CLAHE 2.5 (the one running: no clean empty-wall check to go higher); belt glare: fine. …` <!-- application/src/runtime/calibration_flows.py:1072 «px at the network, » ; application/src/runtime/calibration_flows.py:1078 «(the one running: no clean empty-wall check to go higher)» ; application/src/core/dancer_step.py:363 «belt glare: fine» -->

**Ce que ça veut dire.** Les étapes ont été faites dans le désordre : `A DANCER, 10 s` avant `CALIBRATE`. L'amélioration de l'image reste celle d'avant. Petit défaut. <!-- application/src/gui_builder.py:988 «A DANCER, 10 s» ; application/src/gui_builder.py:890 «CALIBRATE» -->

**Faire** — avant le spectacle ou à la pause (40 s environ) :

1. `3 Live` → `STANDBY`. <!-- application/src/gui_builder.py:645 «3 Live» ; application/src/gui_builder.py:1147 «STANDBY» -->
2. Mur vide : `2 Calibrate` → `CALIBRATE`, puis `Save to project`. <!-- application/src/gui_builder.py:644 «2 Calibrate» ; application/src/gui_builder.py:890 «CALIBRATE» ; application/src/gui.py:3147 «Save to project» -->
3. Puis une danseuse seule : `A DANCER, 10 s`, et sur sa carte `Keep and save`. <!-- application/src/gui_builder.py:988 «A DANCER, 10 s» ; application/src/gui.py:986 «Keep and save» -->
4. `3 Live` → `RUN`. <!-- application/src/gui_builder.py:645 «3 Live» ; application/src/gui_builder.py:1158 «RUN» -->

**Une fois réglé**

![Après : la carte de l'étape sans la parenthèse](img/situations/dancer_step_before_calibrate_apres.png)

- La carte, sans la parenthèse : `Set: person height 101 px (~110 px at the network, 1920x1056); enhancement ON, gamma 1.07 / CLAHE 2.5; belt glare: fine. …` <!-- application/src/runtime/calibration_flows.py:1072 «px at the network, » ; application/src/core/dancer_step.py:363 «belt glare: fine» -->

<div style="break-before: page"></div>

## 19. Pas de photo du mur vide { #no_plate }

*Seulement orange.*

![Avant : la ligne de santé orange « empty wall: none », 2 Calibrate : empty wall: none](img/situations/no_plate_avant.png)

**L'écran**

- La ligne de santé, orange : `WARN - empty wall: none - when the wall is empty: CALIBRATE (2 Calibrate), then Save to project   (+1: points)` <!-- application/src/core/ops_monitor.py:1149 «when the wall is empty: CALIBRATE (» -->
- `2 Calibrate` : `empty wall: none - Calibrate on the empty wall` <!-- application/src/gui_builder.py:644 «2 Calibrate» ; application/src/app.py:2072 «empty wall: none - Calibrate on the empty wall» -->

**Ce que ça veut dire.** Un projet neuf, ou le fichier de la photo perdu : `CALIBRATE` n'a jamais été fait sur le mur vide. La détection suit (97 % ici), mais sans points tenus ni contrôle des corps sans point. <!-- application/src/gui_builder.py:890 «CALIBRATE» -->

**Faire** — avant le spectacle, ou à la pause, mur vide (30 s sans points) :

1. Si quelqu'un tient les visuels, le prévenir : les points s'arrêtent 30 s.
2. `3 Live` → `STANDBY`. <!-- application/src/gui_builder.py:645 «3 Live» ; application/src/gui_builder.py:1147 «STANDBY» -->
3. Personne sur le mur : `2 Calibrate` → `CALIBRATE` (20 à 30 s). <!-- application/src/gui_builder.py:644 «2 Calibrate» ; application/src/gui_builder.py:890 «CALIBRATE» -->
4. `Save to project`. <!-- application/src/gui.py:3147 «Save to project» -->
5. `3 Live` → `RUN`. <!-- application/src/gui_builder.py:645 «3 Live» ; application/src/gui_builder.py:1158 «RUN» -->

**Une fois réglé**

![Après : la ligne de santé verte, empty wall: ready](img/situations/no_plate_apres.png)

- `OK - all 7 checks fine (camera, output, points, empty wall, frames, engine, ...)` ; `empty wall: ready (…, 1260x1824)`. <!-- application/src/core/ops_monitor.py:1529 «checks fine (» ; application/src/app.py:2073 «empty wall: ready» -->

<div style="break-before: page"></div>

## 20. Moins de 15 images par seconde { #fps_under_floor }

*Silencieux dans la ligne de santé.*

![Avant : la barre du bas FPS: 12.0, le bilan « camera: ids @ 12.0 FPS - below the 15 FPS show floor »](img/situations/fps_under_floor_avant.png)

**L'écran**

- La ligne de santé reste verte : `OK - all 7 checks fine (camera, output, points, empty wall, frames, engine, ...)`. <!-- application/src/core/ops_monitor.py:1529 «checks fine (» -->
- En bas : `FPS:` `12.0`. <!-- application/src/gui_builder.py:581 «FPS:» -->
- Le bilan, ligne orange : `camera: ids @ 12.0 FPS - below the 15 FPS show floor` <!-- application/src/core/ops_monitor.py:144 «FPS show floor» -->

**Ce que ça veut dire.** La caméra donne moins de 15 images par seconde : une exposition trop longue, le câble USB, un portable qui ralentit. Entre deux images, la danseuse bouge plus : le point suit moins bien.

**Faire** — avant le spectacle ou à la pause :

1. Le portable sur secteur ; fermer tout sauf WallDance et TouchDesigner.
2. Vérifier le câble USB3 de la caméra (enfoncé aux deux bouts).
3. `3 Live` → `STANDBY` ; mur vide : `2 Calibrate` → `CALIBRATE` (il choisit à nouveau l'exposition), `Save to project`. <!-- application/src/gui_builder.py:645 «3 Live» ; application/src/gui_builder.py:1147 «STANDBY» ; application/src/gui_builder.py:644 «2 Calibrate» ; application/src/gui_builder.py:890 «CALIBRATE» ; application/src/gui.py:3147 «Save to project» -->
4. `Check readiness` : lire la ligne `camera`. `3 Live` → `RUN`. <!-- application/src/gui_builder.py:927 «Check readiness» ; application/src/gui_builder.py:645 «3 Live» ; application/src/gui_builder.py:1158 «RUN» ; application/src/core/ops_monitor.py:143 «camera» -->

Pendant une pièce : noter l'heure, continuer.

**Une fois réglé**

![Après : le bilan « camera: ids @ 20.0 FPS »](img/situations/fps_under_floor_apres.png)

- Le bilan : `camera: ids @ 20.0 FPS` (vert ; sur le portable, vers 17). <!-- application/src/core/ops_monitor.py:136 « FPS» -->

<div style="break-before: page"></div>

## 21. La photo du mur vide est d'un autre jour, même lumière { #plate_other_day_same_light }

*Le jour d'arrivée : normal. La ligne de santé reste verte.*

![Avant : le bilan, la ligne references orange « the empty-wall picture is from another day »](img/situations/plate_other_day_same_light_avant.png)

**L'écran**

- La ligne de santé reste verte : `OK - all 7 checks fine (camera, output, points, empty wall, frames, engine, ...)`. <!-- application/src/core/ops_monitor.py:1529 «checks fine (» -->
- Le bilan, ligne orange : `references: empty wall taken 2026-10-08 21:36 (… days ago); Calibrate 2026-10-08 21:36 (its model not recorded); runs yolo11l-pose @ 1920x1056: the empty-wall picture is from another day (2026-10-08) - STANDBY, then CALIBRATE on the empty wall (2 Calibrate), then Save to project` <!-- application/src/core/ops_monitor.py:1645 «empty wall taken » ; application/src/core/ops_monitor.py:1656 «(its model not recorded)» ; application/src/core/ops_monitor.py:1661 «runs » ; application/src/core/ops_monitor.py:1648 «the empty-wall picture is from another day (» -->

**Ce que ça veut dire.** Le projet garde la photo du mur vide de la dernière visite. Même lumière : rien de perdu (95,6 % ici). Plus sombre : c'est le n° 1.

**Faire** — avant le spectacle, mur vide (30 s) :

1. `3 Live` → `STANDBY`. <!-- application/src/gui_builder.py:645 «3 Live» ; application/src/gui_builder.py:1147 «STANDBY» -->
2. Personne sur le mur : `2 Calibrate` → `CALIBRATE` (20 à 30 s). <!-- application/src/gui_builder.py:644 «2 Calibrate» ; application/src/gui_builder.py:890 «CALIBRATE» -->
3. `Save to project`. <!-- application/src/gui.py:3147 «Save to project» -->
4. `3 Live` → `RUN`. <!-- application/src/gui_builder.py:645 «3 Live» ; application/src/gui_builder.py:1158 «RUN» -->

Pendant une pièce : noter l'heure, continuer.

**Une fois réglé**

![Après : le bilan, la ligne references verte « empty wall taken … (… min ago) »](img/situations/plate_other_day_same_light_apres.png)

- Le bilan, vert : `references: empty wall taken … (… min ago); Calibrate …; runs yolo11l-pose @ 1920x1056`. <!-- application/src/core/ops_monitor.py:1645 «empty wall taken » ; application/src/core/ops_monitor.py:1661 «runs » -->

<div style="break-before: page"></div>

## 22. Max dancers trop haut : 3 pour 2 danseuses { #max_dancers_high }

*Sans gravité.*

![Avant : ids: D1 live | D2 live | D3 lost](img/situations/max_dancers_high_avant.png)

**L'écran**

- La ligne de santé reste verte. Seule la ligne `ids:` a un numéro de trop : `ids: D1 live | D2 live | D3 lost   belt: ready` <!-- application/src/core/points_report.py:75 «ids: » ; application/src/core/points_report.py:19 «lost» ; application/src/core/points_report.py:75 «ids:» -->

**Ce que ça veut dire.** `Max dancers` est plus haut que le nombre de danseuses. Sur la façade, rien de perdu (97,7 % dans les deux cas). <!-- application/src/gui_builder.py:1251 «Max dancers» -->

**Faire** — quand on veut, même pendant la pièce :

1. `3 Live` → `Max dancers` = le nombre de danseuses (ici 2). <!-- application/src/gui_builder.py:645 «3 Live» ; application/src/gui_builder.py:1251 «Max dancers» -->
2. `Ctrl+S`. <!-- application/src/gui_builder.py:396 «Ctrl+S» -->

**Une fois réglé**

![Après : ids: D1 live | D2 live](img/situations/max_dancers_high_apres.png)

- `ids: D1 live | D2 live   belt: ready`. <!-- application/src/core/points_report.py:75 «ids: » -->

<div style="break-before: page"></div>

## Ctrl+S oublié après CALIBRATE { #ctrl_s_forgotten }

*Vérifié par un test de l'app, pas sur une prise : il faut relancer l'app pour le voir.*

![Avant : la carte Scene Calibration et son bouton Save to project](img/situations/ctrl_s_forgotten_avant.png)

**L'écran**

- Après `CALIBRATE`, la carte `Scene Calibration` a un bouton `Save to project`. Sans ce clic (ou `Ctrl+S`), le réglage ne vaut que jusqu'à la fermeture de l'app. <!-- application/src/gui_builder.py:890 «CALIBRATE» ; application/src/gui.py:3127 «Scene Calibration» ; application/src/gui.py:3147 «Save to project» ; application/src/gui_builder.py:396 «Ctrl+S» -->
- Après une relance, on le voit : le bilan, ligne `references`, donne l'heure de l'ancienne photo. <!-- application/src/core/ops_monitor.py:1669 «references» -->

**Ce que ça veut dire.** Le nouveau réglage n'a pas été enregistré dans le projet. Une relance reprend l'ancien.

**Faire** — chaque fois :

1. Sur la carte : `Save to project` → `Saved to project.` → `Close`. Jamais `Keep this session only` (pour cette séance seulement). <!-- application/src/gui.py:3147 «Save to project» ; application/src/gui.py:2443 «Saved to project.» ; application/src/gui.py:2445 «Close» ; application/src/gui.py:3153 «Keep this session only» -->
2. Après tout autre changement : `Ctrl+S`. <!-- application/src/gui_builder.py:396 «Ctrl+S» -->
3. Oublié, et l'app relancée : refaire `CALIBRATE` sur le mur vide (n° 21), puis `Save to project`. <!-- application/src/gui_builder.py:890 «CALIBRATE» ; application/src/gui.py:3147 «Save to project» -->
4. `Save FAILED: …` : refaire `Ctrl+S` ; sinon noter l'heure (le réglage reste actif jusqu'à la fermeture). <!-- application/src/runtime/config_manager.py:426 «Save FAILED: » ; application/src/gui_builder.py:396 «Ctrl+S» -->

**Une fois réglé**

![Après : la carte, Saved to project.](img/situations/ctrl_s_forgotten_apres.png)

- `Saved to project.` en vert sur la carte. <!-- application/src/gui.py:2443 «Saved to project.» -->

<div style="break-before: page"></div>

## L'essai automatique (Auto:) quand on est seul { #swipe_suggestion }

*Un conseil pour plus tard. L'app ne change jamais rien d'elle-même.*

Dans cette version, la liste `Auto:` (sous `Try other settings`, dans `2 Calibrate`) est d'habitude sur `Auto: suggest` : c'est le choix par défaut. À l'ouverture du projet, un message le dit : `Automatic swipe: suggest (the default)` ou `Automatic swipe: suggest (from the project)`. <!-- application/src/core/swipe_auto.py:65 «Auto: suggest» ; application/src/gui_builder.py:939 «Try other settings» ; application/src/gui_builder.py:644 «2 Calibrate» ; application/src/app.py:3538 «Automatic swipe: » ; application/src/core/swipe_auto.py:65 «Auto:» --> <!-- STRINGS.txt (blocks 1, 5) -->

En `RUN`, quand des danseuses sont sur le mur, l'app essaie de temps en temps, en fond, l'autre modèle sur ses images. Le bilan a une ligne verte : `auto swipe` `the automatic swipe keeps a second model loaded: about … MB` (normal). <!-- application/src/gui_builder.py:1158 «RUN» ; application/src/core/ops_monitor.py:457 «auto swipe» ; application/src/core/ops_monitor.py:457 «the automatic swipe keeps a second model loaded» -->

Avant que ce deuxième modèle soit chargé (au démarrage, juste après `CALIBRATE`), la même ligne verte dit `the automatic swipe's second model (…) is still to load (…)` : normal aussi. Pour l'avoir avant le spectacle : `3 Live` → `STANDBY`, attendre 10 s, puis `Check readiness`. Sinon il se charge au premier mur vide du spectacle. <!-- application/src/gui_builder.py:890 «CALIBRATE» ; application/src/core/ops_monitor.py:452 «the automatic swipe's second model (» ; application/src/core/ops_monitor.py:452 «) is still to load (» ; application/src/gui_builder.py:645 «3 Live» ; application/src/gui_builder.py:1147 «STANDBY» ; application/src/gui_builder.py:927 «Check readiness» -->

![La ligne sous Try other settings : « … covers … % of the dancers, …, seen twice: note the time and this line »](img/situations/swipe_suggestion_avant.png)

**Ce qu'on peut voir**

- Une fois, un message : `A better model may be available - see 'Try other settings' (note the time and the line)` <!-- STRINGS.txt (block 17) -->
- Sous `Try other settings`, une ligne comme : `x @ 1920 x 1056 covers 97 % of the dancers, l @ 1920 x 1056 88 % (a frame would take about 52 ms against 35; the floor is 15 fps (67 ms)), seen twice: note the time and this line` <!-- application/src/gui_builder.py:939 «Try other settings» --> <!-- STRINGS.txt (block 15) -->
- Plus tard, la même ligne avec `seen 3 times: note the time and this line`, ou avec `- not confirmed at the last look (…)`, ou `suggestion withdrawn: …` (le conseil n'est plus confirmé, ou il est retiré). <!-- application/src/core/swipe_auto.py:280 «not confirmed at the last look (» ; application/src/runtime/swipe_background.py:1441 «suggestion withdrawn: » --> <!-- STRINGS.txt (blocks 16, 21) -->

**Ce que ça veut dire.** L'autre modèle aurait vu plus de danseuses, deux fois de suite. C'est un conseil pour après le spectacle. **Il n'y a pas de bouton pour l'appliquer** : rien ne change.

**Faire :** noter l'heure et la ligne (les deux pourcentages suffisent). **Rien d'autre** — pas besoin de `Dismiss`, le bouton à côté. Le spectacle continue avec le modèle en cours. <!-- application/src/gui_builder.py:975 «Dismiss» -->

Si un `Apply` traîne encore à l'écran et qu'on clique : `Swipe Apply: nothing switched - a suggestion is advice only in this build: note the time and the line`. Rien ne change : noter, continuer. <!-- application/src/gui_builder.py:966 «Apply» --> <!-- STRINGS.txt (block 18) -->

**Mettre `Auto: off`** — à tout moment, même en `RUN` : <!-- application/src/core/swipe_auto.py:65 «Auto: off» ; application/src/gui_builder.py:1158 «RUN» -->

- Quand : une alerte rouge qui parle de l'essai automatique ; l'image ou les visuels saccadent ; ou pour une soirée tranquille.
- Comment : `2 Calibrate` → sous `Try other settings`, la liste `Auto:` → `Auto: off`. Puis `Ctrl+S` (le choix est gardé avec le projet). <!-- application/src/gui_builder.py:644 «2 Calibrate» ; application/src/gui_builder.py:939 «Try other settings» ; application/src/core/swipe_auto.py:65 «Auto: off» ; application/src/gui_builder.py:396 «Ctrl+S» ; application/src/core/swipe_auto.py:65 «Auto:» -->
- Ne rien fermer d'autre (jamais TouchDesigner), ne rien reconstruire. Le suivi continue avec le modèle en cours.

![Après : la liste Auto: sur Auto: off](img/situations/swipe_suggestion_apres.png)

Avec `Auto: off`, l'app ne fait plus d'essai en fond et libère le deuxième modèle ; la ligne `auto swipe` quitte le bilan. <!-- application/src/core/swipe_auto.py:65 «Auto: off» ; application/src/core/ops_monitor.py:457 «auto swipe» -->

<div style="break-before: page"></div>

## Deux messages rares, pas reproduits au banc { #osc_send_failing }

Ces deux lignes n'ont pas pu être produites sur les prises du 8/10. Leur consigne est celle de la fiche technicien.

- `OSC send to … failing`, ou la ligne de santé rouge `OSC sends to … fail (… dropped) - start TouchDesigner and its patch` (les points ne partent plus sur le réseau) → **tout de suite : (re)lancer TouchDesigner et son patch.** Noter l'heure. <!-- application/src/core/osc_output.py:66 «OSC send to » ; application/src/core/ops_monitor.py:1387 «OSC sends to » ; application/src/core/ops_monitor.py:1388 «TouchDesigner and its patch» -->

### Un point qui reste là où il n'y a personne { #point_on_nobody }

- `Nobody seen by the detector for …`, ou dans la ligne de santé `detector: nobody seen for …`, alors qu'un point part encore (`1 point sent: D1 held on the foreground`) → **s'il y a des danseuses sur le mur : l'IR est-il allumé ? l'image est-elle normale ? S'il n'y a personne : regarder ce qui bouge à cet endroit (un rideau, une corde) ; noter l'heure.** Un point `held on the foreground` qui dure moins d'une seconde est normal (« Un soir normal »). <!-- application/src/core/ops_monitor.py:864 «Nobody seen by the detector » ; application/src/core/ops_monitor.py:1472 «nobody seen » ; application/src/core/points_report.py:59 « sent: » ; application/src/core/points_report.py:15 «held on the foreground» -->
