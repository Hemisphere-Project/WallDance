# WallDance — journal de bord

<!--
Journal de bord, tenu à jour après chaque séance de travail (demande de Thomas, 26/11, hub walldance log 2026-11-26).
Règles de forme, vérifiées par tmp_analysis/r2-1126/gates/journal/run_gate.sh <racine du code> :
- Une section « ## Séance … » par séance, la plus récente en haut, avec une date dans le titre.
  Chaque séance a exactement ces cinq titres « ### », dans cet ordre :
  Où on en était · Ce qu'on a observé · Ce qu'on a fait · Ce que ça apporte · Limites et ce qui reste.
  Des sous-titres « #### » sont permis à l'intérieur.
- Chaque nombre visible a sa source dans un commentaire HTML du même bloc (paragraphe, élément de liste,
  titre, ou tableau : le commentaire dans une cellule). Forme : src: <réf> ; <réf> … avec <réf> parmi
    hub log AAAA-MM-JJ            les entrées du journal du hub à cette date
    hub note <fichier>[@<rév>]    une note du hub (projects/walldance/notes/), à la révision du hub donnée
    hub project.md[@<rév>]
    commit <sujet exact>          un commit de cordee-r2, par son sujet
  Une note du hub réécrite au fil de l'eau (le statut du montage) se cite avec @<rév> du hub.
- Le texte visible ne porte ni sha, ni nom de fichier, ni nom de fonction : ils vont dans les commentaires.
- Phrases de 35 mots au plus. « On » plutôt que « nous ». Ne pas nommer les danseurs.
- Jamais deux tirets de suite dans un commentaire HTML.
-->

Ce journal raconte, séance de travail après séance de travail, ce qu'on a vu et ce qu'on a changé dans WallDance. WallDance est le logiciel qui repère les danseurs sur le mur et envoie leur position aux visuels du spectacle.

Il est écrit pour la compagnie, pour Nico, le technicien, et pour Thomas quand il le relira dans quelques mois. Ce n'est pas un mode d'emploi : celui de Nico est un document à part. La séance la plus récente est en haut ; chacune suit les cinq mêmes rubriques. Les mots anglais de l'écran sont expliqués dans le glossaire, à la fin.

## Séance du 25 au 27 novembre 2026 (en cours) <!-- src: hub log 2026-11-25 ; hub log 2026-11-26 ; hub note 2026-11-26-rehearsal-build-status.md@05f992706 -->

En bref : on a relu de près la répétition sur façade du 8 octobre. Le détecteur n'y voyait les danseurs qu'environ une fois sur deux, d'abord parce que l'image qu'on lui donnait était trop petite. On a corrigé cela dans la nuit. Puis on a commencé le « montage de répétition » : la version que Nico fera tourner à la répétition, puis seul au premier spectacle. Dans la nuit du 26 au 27, on a enfin trouvé d'où viennent les arrêts de la caméra. <!-- src: hub log 2026-11-25 ; hub log 2026-11-26 ; hub note 2026-11-27-camera-stalls-addendum.md -->

### Où on en était

- Sur le portable du spectacle, l'app tournait toujours dans sa version du 8 octobre. <!-- src: hub log 2026-11-25 -->
- Le 18 novembre, un audit du code avait abouti au « Plan Cordée », adopté par Thomas le jour même. Aucune ligne de code n'avait changé depuis. <!-- src: hub log 2026-11-18 -->
- Les vidéos de la répétition sur façade du 8 octobre n'avaient jamais été analysées : elles étaient restées sur le portable. <!-- src: hub log 2026-11-18 ; hub log 2026-11-25 -->
- Le 25 novembre, Nico a allumé le portable. On a pu le rejoindre à distance et copier ces vidéos sur la machine d'analyse de Thomas. <!-- src: hub log 2026-11-25 -->

### Ce qu'on a observé

#### Sur les vidéos du 8 octobre (relues le 25 novembre) <!-- src: hub log 2026-11-25 -->

Deux lectures indépendantes, faites le 25 novembre par deux sessions différentes, arrivent aux mêmes constats sur l'essentiel. La seconde, la « contre-analyse », a refait passer chaque image dans le logiciel tel qu'il tournait ce soir-là. <!-- src: hub log 2026-11-25 -->

- **La soirée.** 13 prises, 59 minutes en tout, de 18 h 39 à 22 h 24, sur une façade d'immeuble en plein air. Les danseurs sont suspendus à des cordes depuis le toit. Un seul projecteur infrarouge dessine un ovale de lumière au milieu du mur. <!-- src: hub log 2026-11-25 -->
- **Le détecteur ne voyait les danseurs qu'à moitié.** Le détecteur est le réseau de neurones qui repère une silhouette humaine dans l'image. La nuit, il trouvait un danseur sur 55 % des images où celui-ci est sur le mur (92 % sur la seule prise de jour). Avec deux danseurs sur le mur, il voyait les deux sur 11 à 37 % des images seulement. <!-- src: hub log 2026-11-25 -->
- **Première cause : l'image donnée au détecteur était trop petite.** La zone surveillée sur la façade est haute et étroite. Le logiciel la faisait entrer dans un carré en remplissant les côtés de vide : 45 % de l'image du détecteur était du vide. Un danseur haut de 110 pixels (les points de l'image) lui arrivait à 80 pixels. <!-- src: hub note 2026-11-25-rehearsal-0810-counter-analysis.md -->
- Avec une image plus grande, le même détecteur passe de 61 % à 81 % des images, et à 91 % si l'on coupe la zone en deux. <!-- src: hub log 2026-11-25 -->
- **Deuxième cause : un réglage automatique trompé.** Le bouton « Calibrate » apprend le mur vide et règle l'image. Ce soir-là, il a été lancé avec quelqu'un sur le mur. Il a vu une personne à chaque essai et a réduit, cran par cran, le renforcement de contraste à presque rien. <!-- src: hub note 2026-11-25-rehearsal-0810-counter-analysis.md -->
- Ce renforcement vaut 18 points de détection. Le contact entre danseurs en coûte 19, et l'ovale brûlé 10. <!-- src: hub log 2026-11-25 -->
- **L'ovale de lumière était « brûlé ».** Environ 16 % du mur est blanc pur dans chaque image des prises sous l'ovale, et les danseurs travaillent dedans. Un danseur en costume clair y devient blanc sur blanc. <!-- src: hub note 2026-11-25-rehearsal-0810-counter-analysis.md -->
- La cause : Calibrate règle la caméra pour que le mur soit bien exposé dans l'ensemble, sans regarder ses parties les plus claires. L'ovale part donc au blanc. <!-- src: hub log 2026-11-25 -->
- **Ce qui partait vers les visuels.** On appelle « point » la position envoyée pour un danseur. La nuit, un point suivait le danseur sur 68 à 92 % de ses images selon la prise. De 2 à 13 % des points envoyés n'étaient sur personne. Le plus long trou sans point allait de 5 à 63 secondes. <!-- src: hub log 2026-11-25 -->
- Deux points envoyés ne veulent pas dire deux danseurs suivis. Dans une prise, deux points partent sur 94 % des images, mais ils sont vraiment sur les deux danseurs sur 50 % seulement. <!-- src: hub log 2026-11-25 -->
- **Le suivi comble la moitié des manques du détecteur.** Le « suivi » relie les détections d'une image à l'autre. Il ne perd lui-même un danseur bien détecté que sur 0 à 10 % des images. Ses défauts viennent de ce qu'il maintient un point sans preuve : des points sur personne, et de longs trous quand le maintien s'arrête. <!-- src: hub note 2026-11-25-setup-and-detection-roadmap.md -->
- **La caméra a été aveugle 11,5 % du temps.** On compte 243 arrêts d'environ 1,7 seconde, un toutes les 15 secondes en moyenne. Leur cause reste inconnue. <!-- src: hub log 2026-11-25 -->
- **La comparaison avec le mur vide peut voir tout le monde.** L'app garde une image du mur vide (la « plate ») et cherche ce qui en diffère. Telle qu'elle était, cette comparaison produisait surtout du bruit. Avec une règle qui tient compte des zones blanches, elle trouve le danseur sur 99,7 à 100 % des images, dans cinq des six prises concernées. <!-- src: hub log 2026-11-25 -->
- **Le portable lui-même.** Les « blocages » signalés depuis octobre sont presque tous des mises en veille de l'ordinateur, capot fermé avec l'app ouverte. <!-- src: hub log 2026-11-25 -->

#### Sur le portable (26 novembre) <!-- src: hub log 2026-11-26 -->

- Rejouées sur le portable, les prises du 8 octobre donnent la même détection que sur la machine d'analyse, à 0,1 point près. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@e577566b9 -->
- Thomas a branché la caméra : l'app a démarré avec elle, et la vérification de départ s'est lancée seule. Elle n'est pas au vert, pour des raisons extérieures au montage : une vue d'intérieur, jugée avec l'image du mur vide de la façade. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@e577566b9 -->

### Ce qu'on a fait

#### Le 25 novembre : la contre-analyse et une feuille de route <!-- src: hub log 2026-11-25 -->

- La contre-analyse a été faite par un autre modèle d'IA. Elle a repassé les 13 prises dans le logiciel du 8 octobre, image par image. Pour savoir où sont vraiment les danseurs, elle n'a pas utilisé le détecteur : elle a comparé chaque image au mur, et noté à la main les entrées et sorties. <!-- src: hub log 2026-11-25 -->
- Elle a donné une feuille de route : d'abord de meilleurs pixels pour le détecteur, puis réparer Calibrate et la comparaison au mur vide, puis guider le technicien. La refonte du suivi prévue par le Plan Cordée vient après. Thomas a dit oui. <!-- src: hub log 2026-11-25 -->
- Les objectifs chiffrés : le détecteur doit voir un danseur sur au moins 85 % de ses images. Les visuels doivent recevoir un point sur chaque danseur au moins 95 % du temps. Jamais plus de 1 seconde sans point, et au plus 2 % de points sur personne. <!-- src: hub note 2026-11-25-setup-and-detection-roadmap.md -->
- Deux décisions de Thomas. Le portable doit garder au moins 15 images par seconde environ, en laissant de la place à TouchDesigner, le logiciel des visuels. Et l'app peut demander au technicien de déplacer le projecteur infrarouge quand la lumière est trop inégale. <!-- src: hub log 2026-11-25 -->

#### La nuit du 25 au 26 novembre : la « release 1 » <!-- src: hub log 2026-11-25 ; hub log 2026-11-26 -->

Une session de travail a tourné seule, la nuit, sur la machine d'analyse. Chaque morceau n'a été gardé que si son test chiffré passait. Le résultat s'appelle la « release 1 », la première étape de la feuille de route ; ce n'est pas encore la version du spectacle. <!-- src: hub log 2026-11-26 ; hub note 2026-11-26-night-session-wrap.md -->

- **Le détecteur voit la façade dans sa vraie forme.** La zone haute et étroite lui est donnée en rectangle, et non plus dans un carré à moitié vide. Le danseur lui arrive plus grand, et il est trouvé bien plus souvent. Sur les quatre longues prises à deux danseurs : <!-- src: hub log 2026-11-26 -->

| Image donnée au détecteur | Danseur trouvé, part de ses images <!-- src: hub note 2026-11-26-night-session-wrap.md --> |
|---|---|
| Avant : un carré, le gros modèle (« x ») | 50,9 % |
| Rectangle moyen (1760 × 960), même modèle | 73,7 % |
| Grand rectangle (1920 × 1056), même modèle | 83,3 % |
| Grand rectangle, modèle un cran plus léger (« l ») | 91,0 % |

- **Ce que 91 % veut dire pour un spectateur.** Sur dix instants où un danseur est sur la façade, le détecteur le trouve neuf fois, contre une fois sur deux avant. Entre deux détections, le suivi tient la position. Moins il a de trous à combler, moins le point des visuels se pose à côté ou disparaît. <!-- src: hub log 2026-11-26 -->
- **Le portable suit la cadence.** Avec TouchDesigner ouvert, une image est traitée en 35,1 millisecondes au plus dans 95 % des cas, sous la limite fixée à 55. La carte graphique n'a pas dépassé 67 °C. <!-- src: hub note 2026-11-26-night-session-wrap.md -->
- **La comparaison au mur vide est réparée.** Elle tient compte des zones blanches et du bruit de l'image. Sous chacune des 6 263 détections sûres des prises sous l'ovale, elle voit bien quelqu'un. Sur le mur vide, elle ne voit rien. <!-- src: hub log 2026-11-26 -->
- **D'autres garde-fous.** Les réglages posés par Calibrate sont rangés derrière le mode « expert ». Une image du mur vide prise à une autre exposition est refusée, avec la raison. Les positions envoyées sont notées, pour juger une séance après coup. <!-- src: hub log 2026-11-26 -->
- **Ce qui a été retenu, et pourquoi.** Un filtre supprimait les détections sans rien derrière elles sur l'image du mur vide. Il n'apportait presque rien : 0,0222 fausse détection par image, pour un objectif de 0,02. Thomas l'a mis de côté jusqu'à ce qu'une prise montre de fausses détections sur le mur nu. <!-- src: hub log 2026-11-26 -->
- **Le modèle dépend du lieu.** Le modèle léger gagne sur la façade mais perd sur 10 des 13 scènes d'essai (hangar, murs texturés, extérieur de nuit). Le modèle devient donc un réglage de chaque projet : le léger pour la façade, le gros ailleurs. <!-- src: hub log 2026-11-26 -->
- Thomas a accepté la release 1 le 26 novembre, sans le filtre. <!-- src: hub log 2026-11-26 -->

#### Le 26 novembre : cap sur le premier spectacle <!-- src: hub log 2026-11-26 -->

- **Les dates.** Grande répétition les 7 et 8 décembre, menée par Nico ; Thomas n'est joignable que par téléphone. Premier spectacle le 12 décembre : Nico seul, Thomas hors réseau. C'est une façade semblable à celle du 8 octobre, peut-être une autre, avec une connexion 4G. <!-- src: hub log 2026-11-26 -->
- **Le calendrier visé.** Le montage complet le 1er décembre, installé sur le portable le 2. Après la répétition, seulement des corrections. Thomas publie la version de spectacle les 9 et 10 ; elle est gelée le 11. <!-- src: hub note 2026-11-25-setup-and-detection-roadmap.md -->
- Une séance avec Nico, la caméra et une personne devant un vrai mur était prévue le 4 décembre. Thomas a fait savoir qu'aucune vraie situation de répétition n'est possible avant le 7 : la forme du 4 reste à décider. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@e577566b9 -->
- **La méthode.** Le travail est découpé en petites pièces. Chacune est construite par un agent d'IA dans son coin, puis intégrée par une session qui coordonne. Une pièce n'entre que si son test passe, et rien ne va dans la version de spectacle sans Thomas.

**Déjà dans le montage** (état au 26 novembre, 14 h 45). Pour chaque pièce, ce qui change pour la personne au portable : <!-- src: hub note 2026-11-26-rehearsal-build-status.md@e577566b9 -->

- **Plus de touche accidentelle.** Taper du texte, par exemple « PIR130 » dans la fiche du matériel, ne déclenche plus les raccourcis d'une lettre. Le badge RUN/STANDBY suit aussi un changement fait à distance. Une bande rouge « LECTURE - pas en direct » s'affiche quand l'app lit un enregistrement au lieu de la caméra. <!-- src: commit feat(operator): no shortcut while typing, one RUN/STANDBY, a playback banner [A4] -->
- **Une relance remet le spectacle en place.** Chaque projet relit sa propre image du mur vide ; avant, il pouvait prendre celle du projet précédent. L'image du mur vide en cours est enregistrée chaque minute et relue au démarrage. Relancée pendant le spectacle, l'app repart avec ce qu'elle voyait. <!-- src: hub log 2026-11-18 ; commit feat(restart): a restart restores the show: the project's own plate, the live plate saved every minute [A2] -->
- **Un verdict sur la lumière, avec la chose à faire.** À partir de l'image du mur vide, l'app compare la partie la plus claire de la zone à la plus sombre. Au-delà de 4 fois, la ligne « wall light » passe au rouge et dit quoi faire du projecteur infrarouge. Les 11 images de mur vide du 8 octobre sont toutes rouges ; un mur uniforme de synthèse est vert. <!-- src: commit feat(readiness): a light verdict from the plate, with one instruction [X2] -->
- **Calibrate ne se laisse plus tromper.** Il vérifie d'abord que le mur est vide. Si quelqu'un est dans l'image, il s'arrête, dit où et remet tout comme avant ; un Calibrate annulé aussi. Rejoué sur une prise du 8 octobre avec un danseur, il s'arrête au bout de 11 images et le situe à 26 pixels près. <!-- src: commit feat(calibrate): emptiness first, and a cancelled Calibrate puts things back [X3+A6] ; hub log 2026-11-25 -->
- **La vérification se lance seule et ne ment plus.** La liste de vérification (« readiness ») tourne seule au démarrage et après chaque Calibrate. Une ligne qui n'a pas pu être vérifiée s'affiche « NOT CHECKED » en bleu-gris, jamais en vert. « READY » n'apparaît que si tout est bon. <!-- src: commit feat(readiness): the check runs at start and after Calibrate, three setup rows, never « skip » as green [H3] -->
- **Une seule ligne de santé.** Pendant le spectacle, une bande porte une seule ligne : le pire problème du moment, avec sa consigne. Elle passe au rouge si une forme qui diffère du mur vide reste plus de 3 secondes sans point. Elle passe à l'orange à 2 arrêts de caméra par minute, au rouge à 6. <!-- src: commit feat(health): one health line during RUN, the worst state wins [H2] -->
- **La veille à distance.** Depuis l'ordinateur de Thomas, un outil relève toutes les 30 secondes l'état de l'app, ses alertes, une petite image de la caméra et la part de danseurs détectés. Il est pensé pour une 4G lente. Un mode « enregistrement seul », pour le 12 décembre, ne contacte personne. <!-- src: commit feat(remote): wdwatch, the watch tool for remote support, and a live hit rate [S1a] -->
- **La ligne d'assistance.** Un message s'affiche sur l'écran du portable jusqu'à ce que Nico clique sur « J'ai lu ». Elle ne bloque rien ; éteinte par défaut, seul le projet de répétition l'allumera. <!-- src: commit feat(remote): the support line, a message on screen until the technician acknowledges it [S1b] -->
- Essai sur le portable le 26, avec la caméra : 10 minutes de veille, 21 relevés, aucun raté. Thomas a lu le message de test en cliquant sur « J'ai lu » au bout de 181 secondes. <!-- src: commit fix(wdremote): run and pytest pass through only as the subcommand [S1] ; hub note 2026-11-26-rehearsal-build-status.md@e577566b9 -->
- **L'exposition protège l'ovale clair.** Calibrate règle maintenant la caméra en surveillant les parties les plus claires de la zone, pour que presque rien ne parte au blanc. Sur un mur de synthèse, l'ancienne règle laissait 5,3 % de la zone au blanc ; la nouvelle arrive à 0 % en 2 étapes. <!-- src: commit feat(calibrate): the exposure servo protects the highlights of the zone [X1] -->
- **L'image du mur vide n'avale plus un danseur immobile.** Cette image se met à jour lentement, pour suivre la lumière, et un danseur immobile pouvait s'y fondre. Sur une immobilité réelle de 35 secondes rejouée 7 fois, 84,5 % de son corps était absorbé ; c'est maintenant 0 %. L'alerte « plate stale » (image périmée) reste affichée jusqu'à une nouvelle image. <!-- src: commit feat(foreground): the plate is honest: « plate stale » stays, a still dancer is not absorbed [F2] -->
- Ces deux dernières pièces sont intégrées. À 14 h 45, la dernière vérification de l'image du mur vide tournait encore. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@e577566b9 -->
- **La fiche de Nico.** Une fiche en français, sur deux pages, est commencée : le lancement, le réglage pas à pas, chaque alerte avec la seule chose à faire, les secours. Elle sera complétée le 1er décembre, puis relue par Thomas le 4. <!-- src: commit docs(fiche): the technician's sheet in French, the skeleton [S2] -->
- **Sur le portable.** L'emplacement de test (« DEV ») reçoit les versions du montage ; la version de spectacle n'a pas bougé. La batterie de tests du logiciel y donne 1 238 tests réussis, aucun échec. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@e577566b9 ; commit fix(wdremote): run and pytest pass through only as the subcommand [S1] -->

**Encore en chantier :**

- **Un point maintenu qui suit un vrai corps.** Quand le détecteur perd un danseur, le suivi garde son point un moment. Ce point devra rester posé sur une forme réelle, et lâcher s'il n'y en a plus. <!-- src: hub note 2026-11-25-setup-and-detection-roadmap.md -->
- Sur une prise du 8 octobre rejouée, un point est resté 21 secondes sur le bord du toit, un autre 43 secondes au-dessus du duo. Cette pièce est construite et relue ; ses corrections sont en cours. Dans la nuit, elle attendait une décision de Thomas (plus bas). <!-- src: hub note 2026-11-25-rehearsal-0810-counter-analysis.md ; hub note 2026-11-26-rehearsal-build-status.md@e577566b9 -->
- **L'étape « un danseur, 10 secondes ».** Un danseur reste 10 secondes sur le mur. L'app mesure sa taille et choisit le renforcement de contraste sur lui, et non sur le mur vide. <!-- src: hub note 2026-11-25-setup-and-detection-roadmap.md -->
- Elle attend la forme finale de l'essai de réglages décrit plus bas, pour n'avoir qu'un seul moment de réglage. Dans la nuit, construite et relue, elle attendait elle aussi une décision de Thomas. <!-- src: hub note 2026-11-26-rehearsal-build-brief.md ; hub note 2026-11-26-rehearsal-build-status.md@e577566b9 -->
- **Les arrêts de caméra ne videront plus les maintiens.** Le temps de maintien d'un point comptera en images, et non plus à l'horloge : un arrêt de 1,7 seconde ne l'épuisera plus. Elle viendra juste après le point maintenu, qui touche la même partie du logiciel. <!-- src: hub log 2026-11-25 ; hub note 2026-11-26-rehearsal-build-status.md@e577566b9 -->
- Le 8 octobre, 17 des 97 pertes de danseur tombaient juste après un arrêt. <!-- src: hub note 2026-11-25-rehearsal-0810-counter-analysis.md -->

**L'essai d'autres réglages (« swipe »)**, construit par une autre session en parallèle :

- **Ce que c'est.** Pendant l'échauffement, avec une vraie image du mur vide, l'app garde des images où elle rate un danseur. Le bouton « Try other settings » les repasse avec d'autres réglages de détection, arbitrés par la comparaison au mur vide. L'app affiche le meilleur, le technicien décide ; rien ne change tout seul. Sur le portable, un essai prend de 12,0 à 12,4 secondes. <!-- src: hub log 2026-11-26 -->
- **Pourquoi il n'est pas encore dans le montage.** Une relecture indépendante a conclu : « pas en l'état ». Le bouton « Apply » rechargeait le modèle en plein RUN : caméra coupée, plus aucun point envoyé pendant quelques secondes. Et sur une prise où le réglage en cours est déjà bon, l'essai désigne un réglage moins bon sur 13,9 % des essais. <!-- src: hub log 2026-11-26 -->
- **La suite.** Il a été renvoyé à sa session ; « Apply » est déjà refusé en RUN. Il n'entrera que si ses tests et une nouvelle relecture sont verts d'ici le 3 décembre. Sa version automatique, voulue par Thomas pour la répétition, attend derrière. Il est entré le soir même, comme conseil : voir plus bas. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@e577566b9 ; hub log 2026-11-26 -->
- La session de montage recommande de s'en tenir à des suggestions le 7 décembre ; c'est à Thomas de trancher. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@e577566b9 -->

#### Le soir du 26 et la nuit du 26 au 27 <!-- src: hub note 2026-11-26-rehearsal-build-status.md@05f992706 ; hub note 2026-11-26-rehearsal-build-status.md@f89e7065b ; hub note 2026-11-27-camera-stalls-addendum.md -->

Ce qui a rejoint le montage, puis ce qui attend Thomas. Pour chaque pièce, dans le même ordre : ce qu'on a vu, ce qu'on a fait, ce que ça apporte, ses limites.

**L'essai de réglages, à la main (« Try other settings »).**

- *Vu :* le 8 octobre, le détecteur tournait avec un réglage qui voyait un danseur sur deux, alors qu'un autre en voyait neuf sur dix. <!-- src: hub note 2026-11-26-night-session-wrap.md ; hub note 2026-11-26-rehearsal-build-status.md@05f992706 -->
- *Fait :* le bouton est entré dans le montage le soir du 26, après deux relectures indépendantes et 8 défauts corrigés. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@05f992706 -->
- Il faut une vraie image du mur vide et des danseurs sur le mur. L'app repasse alors les images qu'elle vient de voir avec les autres modèles et tailles d'image, et en conseille un.
- *Apporte :* un essai prend environ 19 secondes sur le portable du spectacle, TouchDesigner ouvert. Un mauvais réglage peut se repérer dès l'échauffement. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@05f992706 -->
- *Limites :* c'est un conseil. Le nouveau réglage ne s'applique qu'en STANDBY, donc à une pause. Pendant l'essai, un tiers à la moitié des images du direct prennent plus de 50 millisecondes. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@05f992706 -->
- Si l'image du mur vide n'est plus valable, le conseil est retiré : refaire Calibrate, puis relancer l'essai. Sa version automatique n'est pas dans le montage ; si elle y entre, son usage reste à décider par Thomas.

**L'image du mur vide sous surveillance.**

- *Vu :* une mauvaise image du mur vide ne se voyait qu'à ses effets, jamais comme cause. Le 26, Thomas a fixé la règle : l'app ne doit pas faire confiance à ce qu'apporte un spectacle chargé. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@05f992706 -->
- *Fait :* au chargement et en RUN, l'app compare cette image à ce que voit la caméra, sans avoir besoin d'un danseur. Elle dit si les deux concordent, si la caméra a bougé, ou si c'est un autre mur.
- Quand le mur est vide et que l'image ne va pas, elle demande un Calibrate. Une ligne, au chargement, dit d'où viennent l'image du mur vide, le dernier Calibrate et le modèle.
- *Apporte :* la ligne de santé et la vérification nomment l'image comme cause. Le test indépendant écrit par l'autre session : 55 cas justes sur 55 ; une vérification prend 12,7 millisecondes. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@05f992706 -->
- *Limites :* l'app alerte et donne une seule consigne ; elle ne reprend jamais l'image elle-même. Une reprise automatique pourrait avaler un danseur immobile : elle ne sera pas construite sans l'accord de Thomas.

**Les arrêts de la caméra : la découverte de la nuit.**

- *Vu :* à chaque arrêt, le transfert d'une image par le câble USB casse, et le pilote de la caméra met 1,65 à 1,72 seconde à le réparer. Sur la façade, le 8 octobre, l'app en comptait 3 à 4 par minute. <!-- src: hub note 2026-11-27-camera-stalls-addendum.md -->
- *D'abord une fausse piste.* L'après-midi du 26, une enquête sur la caméra seule a conclu que le lieu fixait le niveau des arrêts : sur un bureau, presque rien, 0,11 par minute. <!-- src: hub note 2026-11-27-camera-stalls-addendum.md ; hub note 2026-11-26-rehearsal-build-status.md@05f992706 -->
- Le soir même, les journaux de l'app disaient l'inverse : sur un bureau, l'app se figeait plusieurs fois par minute. La conclusion de l'après-midi se trompait donc sur l'essentiel : ce n'est pas d'abord le câble ni le lieu.
- *Mesuré côte à côte, la nuit,* sur le bureau, aux réglages exacts de l'app, 6 minutes chaque fois. La caméra seule : 0 arrêt. L'app en RUN : 39. La même app en STANDBY, qui lit la caméra sans traiter les images : 0. <!-- src: hub note 2026-11-27-camera-stalls-addendum.md -->
- Ni l'aperçu de l'image, ni le détecteur, ni la carte graphique ne sont en cause. L'étage qui repère ce qui bouge y est mêlé : réduit au quart de l'image, on passe de 35 arrêts à 1. <!-- src: hub note 2026-11-27-camera-stalls-addendum.md -->
- *Ce qui les supprime :* ralentir le lien de la caméra de 162 à 124 Mo/s. En alternant, 0 arrêt contre 46 ; puis 0 en 15 minutes de plus, avec ses 20 images par seconde. <!-- src: hub note 2026-11-27-camera-stalls-addendum.md -->
- *Fait aussi :* l'image déchirée de chaque arrêt n'est plus transmise comme une image entière. Quand les arrêts deviennent fréquents, la consigne est maintenant : à la prochaine pause, rebrancher à fond le câble USB aux deux bouts, puis essayer l'autre câble. <!-- src: commit fix(camera): a torn picture is no longer published; the link's throughput limit as a project key; the stall instruction [U2] -->
- *Apporte :* on sait enfin d'où viennent les arrêts : de l'app, et non de la caméra ou du câble seuls. Un réglage les supprime sur le bureau ; les essais de câble ne sont plus qu'une confirmation.
- *Limites :* tout est mesuré sur un bureau, pas sur la façade, et sans TouchDesigner à côté. On ne sait pas pourquoi l'étage du mouvement provoque ces arrêts. Le réduire n'est pas conseillé : son effet sur le suivi n'est pas mesuré.
- Le lien à 124 Mo/s est un réglage du projet, éteint par défaut. Le mettre pour la répétition est une décision de Thomas, pas encore prise. <!-- src: hub note 2026-11-27-camera-stalls-addendum.md ; hub note 2026-11-26-rehearsal-build-status.md@05f992706 -->

**Une ligne de santé qui dit sa consigne en entier.**

- *Vu :* le soir du 26, sur l'écran du portable, la ligne de santé était coupée au bord de la fenêtre, consigne comprise. Sur une image noire, la ligne de lumière disait de reculer le projecteur, alors qu'il n'y avait aucune lumière. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@05f992706 -->
- *Fait :* la consigne n'est plus jamais coupée : c'est le détail du constat qui s'abrège. Sur une image noire, l'app dit qu'aucune lumière infrarouge n'arrive à la caméra : allumer le projecteur, vérifier son alimentation, ôter le bouchon de l'objectif.
- *Apporte :* la consigne est entière dans 230 cas dessinés sur 230, contre 155 sur 210 avant. On l'a vue ainsi sur l'écran du portable, le 26 au soir. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@05f992706 -->
- *Limites :* un projecteur éteint en STANDBY fait passer la ligne au rouge au bout de 10 secondes. Et la ligne « mur vide » de la vérification ajoute encore sa propre phrase. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@05f992706 -->

**Le renforcement léger (« enhance lite ») aveuglait le détecteur sur le portable.**

- *Vu :* sur le portable, avec ce réglage, le détecteur ne voyait plus personne dans un enregistrement rejoué. La caméra en direct n'a jamais été touchée.
- *Fait :* une correction d'une ligne, là où l'image est passée au détecteur. Prouvée sur le portable lui-même : 2,0 détections par image au lieu de 0,0, sur les mêmes 60 images. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@05f992706 -->
- *Apporte :* les prises rejouées avec ce réglage redonnent des détections.
- *Limites :* la machine d'analyse ne montrait pas le défaut ; un test passé là seulement ne prouvait rien pour le portable. Le test de la correction lit désormais la mémoire comme le portable.

**Les maintiens pendant un arrêt de caméra : construit, relu, et laissé de côté.**

- *Vu :* la pièce prévue devait empêcher un arrêt de la caméra d'user le temps où l'on garde le point d'un danseur perdu. La relecture indépendante a montré que le montage le fait déjà. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@f89e7065b -->
- Le montage porte un pont pour les arrêts, que la version du 8 octobre n'avait pas : pendant un arrêt, il met en pause le temps de maintien. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@f89e7065b -->
- *Fait :* on a rejoué les prises du 8 octobre avec leurs vrais arrêts. Avec ce pont seul, les points perdus sur la première image après un arrêt passent de 42 à 1, et de 89 à 47 en tout. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@f89e7065b -->
- Sur la prise 5 du mur, la danseuse a son point sur 98 % des images, au lieu de 87 %. La pièce prévue, ajoutée par-dessus, ne change rien en direct : même sortie sur les quatre prises. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@f89e7065b -->
- Elle n'entre donc pas dans le montage, sur la recommandation de la session de montage ; elle est gardée de côté.
- *Apporte :* pas de code en plus qui ne changerait rien : les points tiennent déjà pendant un arrêt de caméra.
- *Limites :* quand les images reviennent, un point gardé suit parfois le mauvais groupe. Sur trois prises, la part de points sur personne monte un peu, de moins d'un point. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@f89e7065b -->
- C'est à regarder plus tard ; cela comptera moins si le lien à 124 Mo/s supprime les arrêts. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@f89e7065b -->

**Changer de projet ou de caméra repart à neuf.**

- *Vu :* essayé sur le vrai chemin de l'app, le montage échouait à chaque fois. Le danseur suivi et son point maintenu passaient d'un projet à l'autre, et d'une caméra à l'autre. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@f89e7065b -->
- L'ancien point était encore envoyé jusqu'à 48 images de plus. Un changement court était pris pour un arrêt de caméra. Après un plus long, le premier danseur du nouveau projet partait de l'ancien point. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@f89e7065b -->
- *Fait :* un changement de projet, un changement de caméra et « New project » repartent maintenant à neuf, comme le bouton « Reset ». Un changement d'éclairage dans un même projet garde ses danseurs. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@f89e7065b -->
- Un changement raté garde l'ancien projet et n'est plus pris pour un arrêt. Un modèle qui échoue à se charger ne garde plus de mémoire de la carte graphique.
- *Apporte :* le nouveau projet ne reçoit plus rien de l'ancien, et les visuels plus aucun point venu de l'ancien projet.
- *Limites :* recharger un modèle en RUN garde les points figés, avec leurs numéros, pendant ses 3 secondes, comme un arrêt de caméra. C'est laissé tel quel : à Thomas de le changer s'il le veut. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@f89e7065b -->

**La ligne « Wall light » de la carte de Calibrate.**

- *Vu :* en prenant les images du guide, on a vu que la carte « Scene Calibration » perdait sa ligne « Wall light », la lumière du mur. C'était le cas à chaque vrai Calibrate. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@f89e7065b -->
- Et juste après un Calibrate, la vérification disait au technicien de refaire Calibrate, sur sa ligne « plate exposure » : l'exposition de l'image du mur vide.
- *Fait :* une seule cause aux deux : l'image du mur vide reste en attente, non écrite, jusqu'à « Save ». La carte finit maintenant toujours par sa ligne « Wall light » : le verdict, « en cours de mesure », ou « pas de verdict » avec quoi faire.
- L'image du mur vide garde aussi, dès sa prise, l'exposition et le gain de la caméra. La vérification ne redemande donc plus de Calibrate juste après un Calibrate.
- *Apporte :* le technicien lit toujours la lumière du mur sur la carte, et ne reçoit plus deux consignes contraires.
- *Limites :* deux cas rares à l'écran. Une carte fermée pendant les deux secondes de la prise revient une fois ; l'assistant guidé montrerait cette mise à jour dans l'ancienne fenêtre.

**La pause de la boucle d'images : corrigée.**

- *Vu :* toutes les quelques minutes, la boucle d'images s'arrêtait 100 à 170 millisecondes pour un grand nettoyage de la mémoire. L'autre session l'a trouvé ; sur le portable, des images prenaient 193 et 216 millisecondes. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@f89e7065b -->
- *Fait :* en RUN, ce grand nettoyage ne part plus de lui-même. L'app le fait au démarrage, en passant en STANDBY, après les chargements et à la fin de Calibrate. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@f89e7065b -->
- *Apporte :* vérifié dans la vraie app, sur le portable du spectacle, avec la caméra : aucun grand nettoyage en RUN en 11 minutes. Les petits durent 0,6 milliseconde d'habitude, 4,2 au plus. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@f89e7065b -->
- Sur 17 minutes, la soupape de sécurité s'est déclenchée une fois, à 14 min 19 s. Elle a fait son nettoyage sur le mur vide, en 174 millisecondes, comme prévu. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@f89e7065b -->
- *Limites :* ce nettoyage n'a rien libéré. Quelque chose dans l'app s'accumule lentement : quelques dizaines de mégaoctets sur un spectacle de trois heures. À trouver, mais pas urgent. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@f89e7065b -->

**Petit, mais réel.**

- *Le fichier « dernier projet ».* L'autre session a vu que la batterie de tests, lancée sur le portable, réécrivait le fichier qui dit à l'app quel projet proposer au démarrage. Le 26 au soir, il a désigné quelques minutes un projet d'essai. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@05f992706 -->
- Il a été remis aussitôt. Un garde-fou fait maintenant échouer tout test qui toucherait le vrai fichier. Prouvé sur le portable dans la nuit : toute la batterie y passe, le fichier intact.
- *Le guide a ses vraies images.* Le guide de Nico montre maintenant 13 vraies images de l'app, prises la nuit sur la machine d'analyse ; il fait 41 pages. Seule manque la photo du dispositif sur place. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@f89e7065b -->
- Ces images ont forcé deux corrections : la vérification compte quatorze lignes, et les messages « Go-Live check » s'affichent en haut à gauche de la fenêtre.

**En attente d'une décision de Thomas** (état au 27 novembre, 3 h 49) : <!-- src: hub note 2026-11-26-rehearsal-build-status.md@f89e7065b -->

- **Le point maintenu sur un vrai corps.** Il réduit presque de moitié les points envoyés sur personne : 3,14 % au lieu de 5,82 sur la machine d'analyse, 3,5 % au lieu de 6,2 sur le portable. L'objectif était 2 %. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@05f992706 -->
- La session de montage recommande de l'accepter avec un objectif reformulé. En attendant, il reste hors du montage.
- **L'étape « un danseur ».** Elle échoue à la lettre sur sa fenêtre de taille : 4 cas sur 6. Mais cette fenêtre reprend la taille des danseurs sur l'image d'origine ; lue ainsi, 5 sur 6, le sixième à 1 pixel près. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@05f992706 -->
- Recommandé : l'accepter, lue en pixels de l'image d'origine.
- **Le lien de la caméra à 124 Mo/s pour le projet de répétition.** Recommandé : une ligne dans la configuration du projet, qui s'enlève de la même façon. Mesuré sur un bureau seulement. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@05f992706 ; hub note 2026-11-27-camera-stalls-addendum.md -->

### Ce que ça apporte

- Sur la façade, le détecteur trouve un danseur neuf fois sur dix au lieu d'une fois sur deux. Le point envoyé aux visuels suit mieux et décroche moins.
- Une personne sur le mur ne fausse plus Calibrate : il s'arrête et dit où. L'app dit aussi ce qui ne va pas avec la lumière, et quoi déplacer.
- Une relance remet le spectacle comme il était. Rien de « non vérifié » ne s'affiche en vert, et une seule ligne résume l'état pendant le spectacle.
- On peut taper du texte sans risque, et on voit toujours si l'image est en direct.
- Pendant la répétition, Thomas peut suivre le portable à distance et écrire à Nico sur l'écran.
- On sait d'où viennent les arrêts de la caméra, et un réglage les supprime sur un bureau. L'app vérifie l'image du mur vide qu'elle charge, et le dit.
- Pendant l'échauffement, un bouton conseille un meilleur réglage du détecteur ; rien ne change sans le technicien. La consigne de la ligne de santé n'est plus jamais coupée.
- Changer de projet ou de caméra repart à neuf, et la boucle d'images ne marque plus de pause pendant le spectacle. La carte de Calibrate dit toujours la lumière du mur.

### Limites et ce qui reste

- **Une seule soirée de preuves.** Tous les chiffres viennent d'une façade, un soir, avec deux danseurs en costumes clairs. Le lieu du spectacle peut être une autre façade.
- **Pas de vrai mur avant le 7 décembre.** Le nouveau réglage d'exposition sur un vrai ovale, l'étape « un danseur » sur un vrai danseur et le contrôle du mur vide sur place ne seront essayés qu'à la répétition. Nico les découvrira là. Chaque étape automatique garde un chemin manuel, et la version du 8 octobre reste le secours. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@e577566b9 -->
- La veille à distance n'a pas encore été essayée sur une vraie 4G lente. <!-- src: commit fix(wdremote): run and pytest pass through only as the subcommand [S1] -->
- **Un choix ouvert sur l'exposition.** Protéger l'ovale assombrit le reste du mur : avec la lumière du 8 octobre, la luminosité médiane du mur passerait de 65 à 36–41. L'autre option garde l'exposition d'alors et demande de reculer le projecteur. Une mesure est en cours. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@e577566b9 -->
- **La ligne de santé rougit trop souvent.** Sur trois prises, 58 des 88 secondes de rouge tombent alors que chaque danseur a son point : une ombre, des corps au bord du toit. Elle sera mesurée à nouveau avec les pièces suivantes. <!-- src: commit feat(health): one health line during RUN, the worst state wins [H2] -->
- **Les arrêts de caméra.** Ils viennent de l'app elle-même ; le lien ralenti à 124 Mo/s les supprime sur un bureau. Reste à le vérifier sur la façade et à côté de TouchDesigner, si Thomas retient ce réglage. <!-- src: hub note 2026-11-27-camera-stalls-addendum.md ; hub note 2026-11-26-rehearsal-build-status.md@05f992706 -->
- **Le capot.** Fermer le capot avec l'app ouverte met l'ordinateur en veille, et l'app s'arrête avec lui. À éviter pendant un spectacle. <!-- src: hub log 2026-11-25 -->
- **Un message attendu.** Le 26 novembre à 13 h 02, la version de spectacle a été fermée de force, avec l'accord de Thomas, pour libérer la caméra. À sa prochaine ouverture, elle affichera une fois « Previous session crashed » : c'est normal. <!-- src: hub note 2026-11-26-rehearsal-build-status.md@e577566b9 -->
- **Reste à faire avant la répétition.** Trancher le point maintenu et l'étape « un danseur ». Compléter la fiche de Nico, installer le montage sur le portable, choisir le modèle de chaque projet. Et Thomas doit vérifier à l'œil que la comparaison au mur vide voit bien les danseurs que le détecteur rate. <!-- src: hub note 2026-11-26-night-session-wrap.md ; hub project.md ; hub note 2026-11-26-rehearsal-build-status.md@05f992706 -->
- **Repoussé après le 12 décembre.** L'assistant de réglage pas à pas, le filtre des fausses détections et la refonte du suivi du Plan Cordée. <!-- src: hub note 2026-11-25-setup-and-detection-roadmap.md -->

## Séance du 18 novembre 2026 — l'audit et le « Plan Cordée » <!-- src: hub log 2026-11-18 -->

En bref : une matinée pour faire le point sur tout le code, d'où est sorti un plan de travail découpé en tâches.

### Où on en était

- Le projet était en pause depuis le 11 novembre : la visite prévue pour le câble de la caméra n'avait pas eu lieu. <!-- src: hub log 2026-11-11 -->
- Entre mi-octobre et cette date, le hub ne note que du planning, sans séance de travail. Le portable tournait la version du 8 octobre, et aucune ligne de code n'avait changé depuis. <!-- src: hub log 2026-11-18 -->
- Thomas voulait faire le point, et traiter les vidéos d'une répétition récente. <!-- src: hub log 2026-11-16 -->

### Ce qu'on a observé

- On a audité tout le code : 119 constats, dont 61 revérifiés et 53 confirmés. <!-- src: hub log 2026-11-18 -->
- Verdict : la chorégraphie prévue tombe là où la logique livrée échoue par construction. Dans un enlacement de 5 secondes où le détecteur ne voit qu'une silhouette, le point de la partenaire se perd pendant 3,8 à 4,9 secondes. <!-- src: hub log 2026-11-18 -->
- Avec le maximum de 2 danseurs réglé par défaut, un troisième n'est jamais envoyé, et rien ne prévient. <!-- src: hub log 2026-11-18 -->
- Aucun test automatique ne couvrait ce qui part réellement en spectacle.
- Les vidéos de la répétition étaient restées sur le portable, injoignable ce jour-là.

### Ce qu'on a fait

- Un plan de travail, le « Plan Cordée » : 40 étapes en trois vagues, chacune avec son test. Thomas l'a adopté le jour même ; la première vague compte 24 tâches. <!-- src: hub log 2026-11-18 -->
- Les décisions de Thomas : le spectacle est mené par un technicien formé, au portable. Il déclare le nombre de danseurs à l'écran et relance le réglage de la lumière à la main. TouchDesigner reçoit un point ou rien. Les libellés de l'app passeront en français.
- Les conditions du spectacle notées : jusqu'à 4 à 6 danseurs, des contacts et des croisements, des entrées par le toit ou le sol. Aussi des immobilités de 10 secondes ou plus, et le passage du crépuscule à la nuit. <!-- src: hub log 2026-11-18 -->

### Ce que ça apporte

- Un plan découpé en tâches, chacune avec son test, au lieu d'une liste d'idées ; et les besoins réels du spectacle, notés de la bouche de Thomas.

### Limites et ce qui reste

- Aucune ligne de code n'a changé ce jour-là. Le plan supposait le détecteur tel qu'il était ; la relecture du 25 novembre a changé l'ordre des priorités. <!-- src: hub log 2026-11-25 ; hub log 2026-11-18 -->
- Le plan représentait environ 300 heures de sessions, sans budget ni date de spectacle connus à ce moment-là. <!-- src: hub log 2026-11-18 -->

## Séance du 8 octobre 2026 — la version du 8 octobre et la répétition sur façade <!-- src: hub log 2026-10-08 -->

En bref : une version stable publiée sur le portable l'après-midi, et le soir la répétition sur façade, enregistrée mais pas analysée avant fin novembre. <!-- src: hub log 2026-10-08 ; hub log 2026-11-25 -->

### Où on en était

- La veille, la démonstration avait été reportée à cause de la pluie. Les prises de nuit du 7 octobre étaient quatre prises en solo, à environ 28 mètres. <!-- src: hub log 2026-10-07 ; hub log 2026-10-08 -->

### Ce qu'on a observé

- Calibrate rendait une image noire : son réglage d'exposition lisait une vieille image de la webcam au lieu de la caméra.
- L'objectif est un 6 mm, et non un 8 mm comme noté. Le filtre infrarouge, placé derrière l'objectif, empêche une mise au point nette au loin. <!-- src: hub log 2026-10-08 -->
- Sous la seule lumière des projecteurs infrarouges, le corps est trop sombre pour le détecteur.
- La caméra se fige par moments, environ 1,67 seconde à chaque fois, avec un trou de 23,6 secondes dans un essai. <!-- src: hub log 2026-10-08 -->
- Sur les prises du 7 octobre, la version suit la personne au fond du hangar de 96 à 100 % du temps. <!-- src: hub log 2026-10-08 -->

### Ce qu'on a fait

- Une nouvelle version a été publiée vers 14 h 45 et installée dans l'emplacement de test du portable. <!-- src: hub log 2026-10-08 -->
- Les réglages de détection validés deviennent ceux d'un nouveau projet. Calibrate mesure enfin la bonne caméra. Un nouveau projet reprend les réglages de caméra du précédent.
- Décision : arrêter l'horloge des maintiens pendant les arrêts de caméra. Cette correction n'a jamais rejoint la version de spectacle.
- Le soir, la répétition sur façade a été enregistrée : 13 prises, faites avec l'emplacement de test du portable. <!-- src: hub log 2026-11-25 -->

### Ce que ça apporte

- Une version stable sur le portable, qui sert encore de secours aujourd'hui. Et les premières vraies prises sur façade avec deux danseurs, base de tout le travail de novembre.

### Limites et ce qui reste

- Le hub n'a aucun compte rendu de la répétition elle-même : ni l'avis de la compagnie, ni les notes de la personne au portable. Ce qu'on en sait vient de l'analyse des vidéos, le 25 novembre. <!-- src: hub log 2026-11-25 -->
- Du 8 octobre au 18 novembre : non documenté, sauf par les journaux du portable. Il a surtout rejoué en boucle des extraits de la répétition, sans rien enregistrer. <!-- src: hub log 2026-11-25 -->

## Séances des 6 et 7 octobre 2026 — le portable rejoint et copié <!-- src: hub log 2026-10-06 ; hub log 2026-10-07 -->

En bref : le portable devient joignable à distance, son contenu est copié, un premier audit est fait.

### Où on en était

- L'app était livrée depuis le 25 juin 2026 : détection, interface, calibration, accélération graphique, sortie vers les visuels, et un test d'endurance de 4 heures. <!-- src: hub log 2026-10-05 -->
- Elle était en essai chez l'opérateur depuis juillet, avec peu de retours écrits. Avant le 5 octobre 2026, le hub ne garde aucune trace des séances : non documenté. <!-- src: hub log 2026-10-05 -->

### Ce qu'on a observé

- Le code du portable était identique à celui publié : l'audit a donc lu exactement ce qui tournait.
- L'ancien lanceur de l'app pouvait effacer du travail local lors d'une mise à jour.
- L'audit : les trous du suivi venaient surtout de la couche de suivi. Un seul danseur pouvait recevoir jusqu'à 25 numéros différents au cours d'une prise. <!-- src: hub log 2026-10-06 -->

### Ce qu'on a fait

- Le portable a rejoint un réseau privé sécurisé ; on y accède à distance. Un nouveau lanceur et un emplacement de test (« DEV ») ont été installés à côté de la version de spectacle.
- Réglages, journaux et 10 des 12 enregistrements de référence ont été copiés pour analyse. <!-- src: hub log 2026-10-06 -->
- Une commande à distance de l'app : son état, l'image de la caméra, des commandes, l'enregistrement.
- Des numéros de danseur stables. Sur une prise en solo de 4,3 minutes, on passe de 15 numéros à 1. <!-- src: hub log 2026-10-06 -->
- Le 7 octobre, la démonstration prévue pour la compagnie a été reportée à cause de la pluie. <!-- src: hub log 2026-10-07 -->

### Ce que ça apporte

- On peut travailler sur le portable sans se déplacer et rejouer ses vidéos sur la machine d'analyse. Les visuels reçoivent un numéro stable par danseur.

### Limites et ce qui reste

- Pas encore de mesure à deux danseurs dans le dispositif de la démonstration, toujours sans date. <!-- src: hub log 2026-10-08 -->

## Petit glossaire

- **Calibrate** — le bouton de réglage automatique : il apprend le mur vide, règle la caméra et prend l'image du mur vide.
- **Plate** — l'image de référence du mur vide. L'app repère les danseurs à ce qui en diffère. « plate stale » : cette image ne correspond plus à la scène.
- **Readiness** (« Check readiness ») — la liste de vérification avant de jouer. En tête, READY (prêt) ou NOT ALL CHECKED (tout n'a pas pu être vérifié) ; chaque ligne est verte (OK), bleu-gris (NOT CHECKED), orange (WARN) ou rouge (FAIL).
- **RUN, STANDBY** — en RUN, l'app détecte et envoie les positions aux visuels. En STANDBY, elle montre l'image sans rien détecter ni envoyer.
- **ROI** (« Region of Interest ») — la zone surveillée, tracée sur l'image. Ce qui est hors de la zone est ignoré.
- **DEV** — l'emplacement de test du portable, où l'on essaie les nouvelles versions à côté de celle du spectacle.
- **Release** — une version du logiciel. La « release 1 » est la première étape de la feuille de route de novembre ; le portable du spectacle tourne encore la version du 8 octobre. <!-- src: hub log 2026-11-26 ; hub log 2026-11-25 -->
- **Swipe** (« Try other settings ») — l'essai d'autres réglages de détection sur quelques images.
- **Ligne de santé** — la bande sous l'image, pendant le spectacle : le pire souci du moment et sa consigne.
- **Enhance lite** — une version légère du renforcement de contraste appliqué à l'image avant le détecteur.
- **Point, maintien** — le point est la position envoyée aux visuels pour un danseur. Il est « maintenu » quand l'app le garde sans voir le danseur.
- **Détecteur, modèle « x » ou « l »** — le réseau de neurones qui repère une silhouette humaine. « x » est le plus gros, « l » un cran plus léger.
- **TouchDesigner** — le logiciel qui fabrique les visuels du spectacle à partir des positions envoyées.
