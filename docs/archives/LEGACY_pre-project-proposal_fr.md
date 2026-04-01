# Système de Suivi de Pose Multi-Danseurs

## Plan d'Implémentation Technique pour Performance Murale Verticale 5 Danseurs

**Contexte du Projet :**

- 5 danseurs suspendus sur cordes sur mur vertical de bâtiment (50m × 25m)
- Performance nocturne avec éclairage artificiel contrôlé
- Données de pose en temps réel transmises via OSC vers système de projection vidéo générative
- Modèles pré-entraînés uniquement (pas d'entraînement ML personnalisé)

---

## Clarification des Budgets & Chronologie

**Budgets en EUR :**

- Incluent **matériel (hardware) uniquement**
- Tous les coûts logiciel = **0 EUR** (open-source)
- Estimations **non contractuelles** basées sur achat/location 2025 France/EU
- Travail estimé en jours. Le taux journalier reste à négocier.
- Les temps de travail estimés n'incluent pas les déplacements, défraiement, résidences, etc.

---

## Vue d'Ensemble du Système

Ce document présente une **approche d'implémentation linéaire et incrémentale** où chaque couche s'ajoute progressivement à l'infrastructure précédente. Le système utilise l'estimation de pose basée sur la vision avec des modèles pré-entraînés pour suivre plusieurs danseurs simultanément et produire des données squelettales en temps réel.

**Contraintes Clés :**

- Pas d'entraînement de modèle personnalisé
- Investissement matériel incrémental et modularisé
- Sortie OSC uniquement (logiciel de projection/vidéo non inclus)
- Fonctionnement nocturne avec éclairage artificiel de spectacle

---

## COUCHE 1 : Prototypage POC (Validation)

### Objectif

Valider que la détection de pose basée sur la vision fonctionne dans votre environnement d'éclairage nocturne spécifique avant de s'engager dans l'infrastructure complète multi-danseurs.

### Matériel Requis (Location 5 jours)


| Composant                    | Spécification                              | Coût (EUR) |
| ------------------------------ | --------------------------------------------- | ------------- |
| Ordinateur de développement | Tour i7 + GPU RTX 3090 (location)           | 200         |
| Caméra RGB standard 4K      | Sony ZV-1F ou équivalent, 60fps (location) | 300         |
| **Total Couche 1**           |                                             | **500 EUR** |

### Pile Logicielle (Gratuit)

- **Détection de Pose :** MediaPipe Pose (33 points clés) ou YOLOv8-Nano
- **Capture Caméra :** OpenCV (Python)
- **Sortie OSC :** librairie python-osc
- **Développement :** Python 3.9+, toolkit CUDA

### Chronologie du Développement


| Tâche                                     | Jours       | Notes                                             |
| -------------------------------------------- | ------------- | --------------------------------------------------- |
| Configuration environnement + dépendances | 0,5         | CUDA, librairies Python, pilotes caméra          |
| Calibrage caméra & positionnement         | 1,0         | Correction distorsion objectif, test FOV          |
| Intégration MediaPipe                     | 1,5         | Pipeline détection pose basique                  |
| Implémentation sortie OSC                 | 0,5         | Sérialisation données squelette, config réseau |
| Test in situ                               | 1,0         | Éclairage nocturne, évaluation des occultations |
| Gestion projet & approvisionnement         | 1,5         | Coordination, commandes matériel                 |
| **Total Couche 1**                         | **6 jours** | (~1,2 semaine)                                    |

*Note: cela n'inclus pas l'acquisition et le traitement dans TouchDesigner*

### Performances Attendues

- **Latence :** 50-100ms (capture caméra → détection pose → sortie OSC)
- **Précision :** ±100-200mm à 25m de distance (caméra RGB standard limitée)
- **Fréquence d'images :** 30-60fps (danseur unique)
- **Portée :** Danseur unique

### Critères de Validation

- Points clés squelette détectés de manière fiable dans l'éclairage nocturne
- Occultations (cordes / profils) correctement prises en charge
- Données OSC en flux continu sans images perdues
- Latence acceptable pour synchronisation projection (<100ms)

### Point de Décision

✅ **Si succès :** Procéder à Couche 2A ou 2B selon évaluation des occultations
❌ **Si mauvaise visibilité :** Privilégier Couche 2B (RGB pro)

---

## COUCHE 2A : Multi-Personne RGB Standard (5 Danseurs)

### Objectif

Suivi multi-danseurs prêt pour la production avec caméra RGB standard validée en Couche 1. Chemin économique si captation standard suffisante.

### Matériel Requis (Achat définitif)


| Composant                         | Spécification                                      | Coût (EUR)         |
| ----------------------------------- | ----------------------------------------------------- | --------------------- |
| Ordinateur workstation complet    | RTX 3090, CPU haute performance, 32 Go RAM          | 3 500-5 000         |
| Caméra RGB standard 4K           | Sony ZV-1F ou équivalent, 60fps (achat définitif) | 500-700             |
| Lentille optique                  | C-mount 25-35mm pour distance 25m                   | 300-500             |
| Infrastructure réseau            | Routeur / câbles / boîtier weatherproof           | 400-500             |
| **Incrémental Couche 2A**        |                                                     | **4 700-6 700 EUR** |
| **Total Cumulé (avec Couche 1)** |                                                     | **5 200-7 200 EUR** |

### Pile Logicielle (Gratuit)

- **Détection Personne :** YOLOv8 (standard, pas nano) - traite multi-personne nativement
- **Estimation Pose :** MediaPipe Pose (mode multi-personne)
- **Suivi :** ByteTrack ou SORT (maintient IDs personne entre images)
- **Sortie OSC :** librairie python-osc

### Chronologie du Développement


| Tâche                                     | Jours          | Notes                                |
| -------------------------------------------- | ---------------- | -------------------------------------- |
| Configuration YOLOv8 + détection personne | 1,5            | Boîtes englobantes multi-personne   |
| Estimation pose batch                      | 2,5            | Traiter 5 danseurs simultanément    |
| Implémentation suivi ID personne          | 2,0            | Maintenir IDs constants entre images |
| Logique gestion occultation                | 1,5            | Seuils confiance, interpolation      |
| Sérialisation OSC (5 danseurs)            | 1,0            | Structure données multi-personne    |
| Intégration caméra + infrastructure      | 1,0            | Optimisation Sony standard           |
| Test & optimisation                        | 2,5            | Tuning latence, gestion cas limites  |
| Gestion projet & approvisionnement         | 1,5            | Coordination, commandes matériel    |
| **Total Couche 2A**                        | **13,5 jours** | (~2,7 semaines)                      |

### Performances Attendues

- **Latence :** 60-100ms (5 danseurs traités en parallèle)
- **Précision :** ±20-50mm par point clé à 25m de distance
- **Fréquence d'images :** 30-60fps (5 danseurs)
- **Information 3D :** Aucune (squelette 2D uniquement)
- **Gestion Occultation :** Modérée (interpole points clés manquants depuis articulations visibles)

### Avantages

- **Coût matériel bas** (4,7-6,7k EUR incrémental)
- **Réutilise caméra validée en POC**
- **Déploiement simple**
- **Calibrage minimal** (caméra unique)

### Limitations

- **Point de vue unique** : occultation d'un angle affecte tous les danseurs
- **Réactivité limitée** en cas de mouvements rapides ou luminosités extrêmes
- **Pas de redondance** en cas de défaillance caméra

### Convient Pour

- Budget serré (<7,2k EUR)
- Occultation attendue <20% du temps
- Captation standard validée en POC

---

## COUCHE 2B : Multi-Personne RGB Pro (5 Danseurs)

### Objectif

Suivi multi-danseurs avec caméra basse luminosité professionnelle. Alternative à 2A si captation standard insuffisante.^

**Note :** Couche 2B est une **alternative indépendante à 2A** (choix exclusif, pas accumulation)

### Matériel Requis (Achat définitif)


| Composant                         | Spécification                             | Coût (EUR)          |
| ----------------------------------- | -------------------------------------------- | ---------------------- |
| Ordinateur workstation complet    | RTX 3090, CPU haute performance, 32 Go RAM | 3 500-5 000          |
| Caméra RGB pro basse lumière    | Axis P1468-LE, 4K 60fps, 0-lux capable     | 2 500-3 500          |
| Lentille téléobjectif           | C-mount 25-35mm pour distance 25m          | 300-500              |
| Infrastructure réseau            | Routeur / câbles / boîtier weatherproof  | 400-500              |
| **Incrémental Couche 2B**        |                                            | **6 700-9 500 EUR**  |
| **Total Cumulé (avec Couche 1)** |                                            | **7 200-10 000 EUR** |

### Pile Logicielle (Gratuit)

- **Détection Personne :** YOLOv8 (standard, pas nano)
- **Estimation Pose :** MediaPipe Pose (mode multi-personne)
- **Suivi :** ByteTrack ou SORT
- **Sortie OSC :** librairie python-osc

### Chronologie du Développement


| Tâche                                     | Jours          | Notes                                               |
| -------------------------------------------- | ---------------- | ----------------------------------------------------- |
| Configuration YOLOv8 + détection personne | 1,5            | Boîtes englobantes multi-personne                  |
| Estimation pose batch                      | 2,5            | Traiter 5 danseurs simultanément                   |
| Implémentation suivi ID personne          | 2,0            | Maintenir IDs constants entre images                |
| Logique gestion occultation                | 1,5            | Seuils confiance, interpolation                     |
| Sérialisation OSC (5 danseurs)            | 1,0            | Structure données multi-personne                   |
| Intégration caméra pro Axis              | 2              | Paramètres basse lumière, calibrage professionnel |
| Test & optimisation                        | 2,5            | Tuning latence, gestion cas limites                 |
| Gestion projet & approvisionnement         | 1,5            | Coordination, commandes matériel                   |
| **Total Couche 2B**                        | **14,5 jours** | (~2,9 semaines)                                     |

### Performances Attendues

- **Latence :** 60-100ms (5 danseurs traités en parallèle)
- **Précision :** ±15-40mm par point clé à 25m distance (meilleure que 2A)
- **Fréquence d'images :** 30-60fps (5 danseurs)
- **Information 3D :** Aucune (squelette 2D uniquement)
- **Gestion Occultation :** Modérée (interpole points clés manquants)
- **Robustesse basse lumière :** Excellente (capteur pro 0-lux)

### Avantages vs 2A

- **Meilleure performance basse lumière** (capteur pro 0-lux)
- **Meilleure précision** (±15-40mm vs ±20-50mm)
- **Fiabilité accrue** pour captation nocturne

### Limitations

- **Coût matériel plus élevé** (6,7-9,5k EUR vs 4,7-6,7k EUR)
- **Point de vue unique** : occultation d'un angle affecte tous les danseurs
- **Réactivité limitée** en cas de mouvements rapides
- **Pas de redondance**

### Convient Pour

- Budget modéré (7,2-10k EUR)
- POC a validé caméra standard insuffisante
- Occultation attendue 20-40% du temps
- Priorité à robustesse basse luminosité

---

## COUCHE 3 : Augmentation Caméra Événement

### Objectif

Améliorer robustesse temporelle et gestion occultation en ajoutant caméra événement à Couche 2A ou 2B. Fournit prédiction motrice sans coût multi-caméra complet.

### Matériel Requis (Achat additif)


| Composant                            | Spécification                          | Coût (EUR)           |
| -------------------------------------- | ----------------------------------------- | ----------------------- |
| Caméra événement                  | Prophesee EVK4 HD USB (1280×720)       | 3 000-3 500           |
| Matériel synchronisation            | Câbles déclencheur matériel, montage | 200-300               |
| Infrastructure réseau additionnelle | Câbles, boîtier supplémentaire       | 100-200               |
| **Incrémental Couche 3**            |                                         | **3 300-4 000 EUR**   |
| **Total Cumulé (de 2A)**            |                                         | **8 500-11 200 EUR**  |
| **Total Cumulé (de 2B)**            |                                         | **10 500-14 000 EUR** |

### Pile Logicielle (Gratuit)

- **Détection Personne :** YOLOv8 (flux RGB)
- **Estimation Pose :** MediaPipe Pose (flux RGB)
- **Traitement Événement :** SDK Prophesee (Metavision)
- **Fusion Capteurs :** Python personnalisé (fusion pondérée confiance)
- **Suivi :** ByteTrack + prédiction motrice basée-événement
- **Sortie OSC :** librairie python-osc

### Chronologie du Développement


| Tâche                                     | Jours          | Notes                                         |
| -------------------------------------------- | ---------------- | ----------------------------------------------- |
| Configuration SDK caméra événement      | 1,5            | Environnement Prophesee Metavision            |
| Calibrage événement-vers-RGB             | 2,0            | Alignement spatial + temporel                 |
| Extraction vecteurs motions                | 2,5            | Flux événement → flux optique              |
| Logique fusion capteurs                    | 3,0            | Fusion points clés pondérée confiance      |
| Suivi multi-personne avec motions          | 2,5            | Motions événement comme prédicteur vitesse |
| Implémentation récupération occultation | 2,0            | Prédiction joints basée-événement         |
| Test & tuning robustesse                   | 3,5            | Scénarios complexes cordes                   |
| Gestion projet & approvisionnement         | 1,5            | Coordination, commandes matériel             |
| **Total Couche 3**                         | **18,5 jours** | (~3,7 semaines)                               |

### Approche Technique

**Rôle Caméra Événement :**

- Opère à **résolution temporelle microsecondes** (vs. 16ms pour 60fps RGB)
- Capture uniquement **changements intensité pixels** (motions), pas trames complètes
- Fournit **vecteurs motions** qui augmentent détection pose RGB

**Stratégie Fusion :**

```
Par image :
1. RGB : YOLOv8 détecte 5 boîtes englobantes personne
2. RGB : MediaPipe extrait 17 points clés par personne
3. Événement : Extraire vecteurs motions dans chaque boîte englobante
4. Fusion :
   - Si confiance RGB > 0,7 : Utiliser points clés RGB directement
   - Si 0,4 < confiance RGB < 0,7 : Mélanger RGB + motions événement
   - Si confiance RGB < 0,4 : Utiliser motions événement pour prédire articulations occultées
5. Suivi : Filtre Kalman par danseur (motions événement = mise à jour vitesse)
```

### Performances Attendues

- **Latence :** 60-100ms (identique ; RTX 3090 traite les deux flux en parallèle)
- **Précision (articulations visibles) :** ±20-50mm (identique à 2A/2B)
- **Précision (articulations occultées) :** ±30-80mm (vs. interpolation simple en 2A/2B)
- **Fréquence d'images :** 30-60fps (5 danseurs)
- **Gestion Occultation :** **Significativement améliorée** (~40% réduction perte ID suivi)

### Détails Techniques Caméra Événement

- **Résolution :** 1280×720 (vs. 4K RGB)
  - **Pas une limitation :** Détection points clés opère sur ~50-100 pixels caractéristiques ; 720p suffisant
- **Plage Dynamique :** 120+ dB (vs. 60 dB RGB)
  - Gère transitions d'éclairage extrêmes sans ajustement exposition
- **Débit Données :** Épars (pixels en mouvement seulement), typiquement 10-50 Mbps pour 5 danseurs

### Avantages vs 2A/2B seul

- **Meilleure récupération occultation** (motions événement prédisent articulations cachées)
- **Confusion ID suivi réduite** (motions temporels aident distinguer danseurs)
- **Robustesse temporelle** (mises à jour sub-milliseconde lissent lacunes trames RGB)
- **Incrémental uniquement** (~3,3-4k EUR pour augmentation)

### Limitations

- **Développement plus complexe** (+17 jours vs. 2A/2B seul)
- **Double caméra** : nécessite calibration simple
- **Toujours 2D uniquement** (pas d'information de profondeur)

### Convient Pour

- Occultation 20-40% attendue
- Continuité temporelle critique pour lissage projection
- Budget permet +3,3-4k EUR
- 2A ou 2B base validé

---

## COUCHE 4 : Dual Setup (Multi-Vue 3D)

### Objectif

Déployer configuration complète multi-vue : 2× ordinateurs, 2× caméras RGB, 2× caméras événement. Réalise suivi position 3D vrai via triangulation multi-vue avec résilience occultation maximale.

### Matériel Requis (Achat additif - 2e machine identique Couche 3)


| Composant                           | Spécification                               | Coût (EUR)           |
| ------------------------------------- | ---------------------------------------------- | ----------------------- |
| 2e ordinateur workstation complet   | RTX 3090, CPU haute perf, 32 Go RAM          | 3 500-5 000           |
| 2e caméra RGB (identique 2A ou 2B) | Sony ZV-1F (si 2A) ou Axis P1468 (si 2B)     | 500-3 500             |
| 2e caméra événement              | Prophesee EVK4 HD USB                        | 3 000-3 500           |
| Système synchronisation            | Genlock matériel, distribution déclencheur | 500-800               |
| Portique montage précision         | Support multi-caméra ajustable              | 800-1 500             |
| Infrastructure réseau améliorée  | Commutateur 10GbE + fibre optique            | 1 000-1 500           |
| **Incrémental Couche 4**           |                                              | **9 300-15 800 EUR**  |
| **Total Cumulé (3+4 de 2A)**       |                                              | **17 800-27 000 EUR** |
| **Total Cumulé (3+4 de 2B)**       |                                              | **19 800-29 800 EUR** |

### Pile Logicielle (Gratuit)

- **Pose Multi-Vue :** OpenMMLab MMSkeleton
- **Calibrage Stéréo :** Calibrage caméra stéréo OpenCV
- **Triangulation :** Python personnalisé (géométrie multi-vue)
- **Fusion Événement :** Extension de Couche 3
- **Suivi :** Filtre Kalman 3D par danseur

### Chronologie du Développement


| Tâche                                      | Jours          | Notes                                      |
| --------------------------------------------- | ---------------- | -------------------------------------------- |
| Conception portique multi-caméra & montage | 3,0            | Positionnement physique, chevauchement FOV |
| Calibrage intrinsèque (par caméra)        | 1,5            | Correction distorsion lentille             |
| Calibrage extrinsèque (pose relative)      | 2,5            | Géométrie caméra-à-caméra             |
| Pipeline triangulation stéréo             | 4,0            | Points clés 2D → position 3D             |
| Intégration dual caméra événement       | 3,5            | Flux événements synchronisés            |
| Fusion pose multi-vue                       | 5,0            | Reconstruction 3D pondérée confiance     |
| Traitement GPU parallèle                   | 2,5            | Traitement dual-flux batch                 |
| Gestion occultation (multi-vue)             | 3,0            | Visibilité dépendante vue                |
| Test & maintenance calibrage                | 5,0            | Compensation dérive extérieure           |
| Gestion projet & approvisionnement          | 1,5            | Coordination, commandes matériel          |
| **Total Couche 4**                          | **31,5 jours** | (~6,3 semaines)                            |

### Performances Attendues

- **Latence :** 60-100ms (traitement parallèle maintient performance)
- **Précision 3D :** ±5-15mm par point clé (triangulation depuis 2+ vues)
- **Fréquence d'images :** 30-60fps (5 danseurs)
- **Gestion Occultation :** **Excellente** (multi-vue signifie ≥1 caméra voit la plupart des corps)

### Avantages

- **Position 3D vrai** (information profondeur pour warping projection)
- **Résilience occultation maximale** (points de vue multiples)
- **Précision spatiale haute** (±5-15mm vs. ±20-50mm)
- **Robuste aux défaillances** (dégradation gracieuse du système)

### Limitations

- **Complexité haute** (calibrage multi-caméra non-trivial)
- **Maintenance calibrage** (vibrations extérieures/météo cause dérive)
- **Temps développement long** (6 semaines additionnel)
- **Coût élevé** (17,8-29,8k EUR total)

### Convient Pour

- Occultation très lourde : >40%
- Position 3D vrai requise pour effets
- Précision critique : <20mm erreur nécessaire
- Budget >17,8k EUR et chronologie permet 8-10 semaines total

---

## Comparaison Synthétique

### Aperçu Coûts Matériel


| Couche | Configuration               | Incrémental   | Cumulé      | Jours Dev |
| -------- | ----------------------------- | ---------------- | -------------- | ----------- |
| **1**  | POC RGB standard (location) | 500 EUR        | 500 EUR      | 6         |
| **2A** | RGB standard multi-danseur  | +4,7-6,7k EUR  | 5,2-7,2k EUR | 13,5      |
| **2B** | RGB pro multi-danseur       | +6,7-9,5k EUR  | 7,2-10k EUR  | 14,5      |
| **3**  | +Caméra événement        | +3,3-4k EUR    |              | +18,5     |
| **4**  | Dual (2× Couche 3A)        | +9,3-15,8k EUR |              | +31,5     |

### Comparaison Performance


| Métrique               | Couche 1    | Couche 2A | Couche 2B | Couche 3A/3B          | Couche 4A/4B |
| ------------------------- | ------------- | ----------- | ----------- | ----------------------- | -------------- |
| **Danseurs**            | 1           | 5         | 5         | 5                     | 5            |
| **Latence**             | 50-100ms    | 60-100ms  | 60-100ms  | 60-100ms              | 60-100ms     |
| **Précision**          | ±100-200mm | ±20-50mm | ±15-40mm | ±20-50mm / ±30-80mm | ±5-15mm     |
| **Position 3D**         | Non         | Non       | Non       | Non                   | Oui          |
| **Gestion Occultation** | Faible      | Modérée | Modérée | Bonne                 | Excellente   |
| **Stabilité ID**       | N/A         | Modérée | Modérée | Bonne                 | Excellente   |
| **Redondance**          | Non         | Non       | Non       | Non                   | Oui          |

---

## Exigences Techniques

### Infrastructure Réseau

- **Bande passante :** 1,5-3 Mbps par flux caméra + 1,5 Mbps sortie OSC
- **Latence :** <5ms transmission réseau (Ethernet filaire requis)
- **Protocole :** OSC sur UDP (port standard 8000 ou personnalisé)
- **Fiabilité :** Commutateurs PoE+ industriels pour déploiement extérieur

### Exigences Puissance

- **RTX 3090 :** 500W TDP par machine
- **Caméras RGB :** 15-30W chacune (PoE ou alimentation locale)
- **Caméras événement :** 5W chacune (USB-powered)
- **Système Couche 2A/2B :** ~550W total
- **Système Couche 3 :** ~600W total
- **Système Couche 4 :** ~1200W total (2 machines)

### Considérations Environnementales

- **Weatherproofing :** Boîtiers homologués IP65+ pour caméras
- **Plage Température :** -10°C à +40°C fonctionnement (caméras standard extérieur)
- **Routage Câble :** Câbles Ethernet résistants UV, homologués extérieur
- **Montage :** Supports vibration-résistants (vent, mouvement bâtiment)

### Environnement Logiciel

- **OS :** Ubuntu 20.04+ ou Windows 10+ (CUDA-compatible)
- **Python :** 3.9+ avec support CUDA
- **Pilote GPU :** NVIDIA 535+ (CUDA 12.0+)
- **Librairies Clés :**
  - OpenCV 4.8+
  - MediaPipe 0.10+
  - YOLOv8 (Ultralytics)
  - python-osc
  - SDK Metavision Prophesee (Couche 3/4)

---

## Feuille de Route Développement

### Phase 1 : Validation (Couche 1)

**Durée :** 6 jours
**Investissement :** 500 EUR (location)
**Objectif :** Confirmer faisabilité technique en conditions réelles

**Livrables :**

- Détection pose danseur unique fonctionnelle
- Flux OSC en streaming
- Mesures latence
- Rapport d'évaluation de l'occultation

**Décision Go/Non-Go :** Choisir entre 2A (RGB standard) ou 2B (RGB pro)

### Phase 2 : Production Multi-Danseurs (Couche 2A ou 2B)

**Durée :**

- 2A: 13,5 jours
- 2B: 14,5 jours

**Investissement :**

- 2A: +4,7-6,7k EUR
- 2B: +6,7-9,5k EUR

**Objectif :** Déployer suivi 5-danseurs prêt production avec sortie OSC

**Livrables :**

- Détection & suivi multi-personne
- IDs danseur persistants
- Flux OSC 30-60fps
- Documentation déploiement

**Point de Décision :** Augmenter avec Couche 3 si occultation problématique

### Phase 3 (Optionnel) : Augmentation Événement (Couche 3)

**Durée :** 18,5 jours
**Investissement :** +3,3-4k EUR
**Objectif :** Améliorer robustesse occultation via caméra événement

**Livrables :**

- Fusion RGB + événement
- Prédiction motrice des occultées
- Stabilité ID améliorée

### Phase 4 (Optionnel) : Dual 3D (Couche 4)

**Durée :** 31,5 jours
**Investissement :** +9,3-15,8k EUR
**Objectif :** Position 3D vrai via triangulation multi-vue

**Livrables :**

- Calibrage multi-caméra
- Reconstruction 3D
- Résilience occultation maximale

---

## Résumé Budget

### Investissement Minimal (Couche 1 + 2A)

- **Matériel :** 5,2-7,2k EUR
- **Logiciel :** 0 EUR
- **Développement :** 19,5 jours
- **Total :** 5,2-7,2k EUR + travail

### Investissement Recommandé (Couche 1 + 2B)

- **Matériel :** 7,2-10k EUR
- **Logiciel :** 0 EUR
- **Développement :** 20,5 jours
- **Total :** 7,2-10k EUR + travail

### Investissement Intermédiaire (Couche 1 + 2A/2B + 3)

- **Matériel :** 8,5-14k EUR
- **Logiciel :** 0 EUR
- **Développement :** 38-39 jours
- **Total :** 8,5-14k EUR + travail

### Investissement Complet (Couche 1 + 2A/2B + 3 + 4)

- **Matériel :** 17,8-29,8k EUR
- **Logiciel :** 0 EUR
- **Développement :** 69,5-70,5 jours
- **Total :** 17,8-29,8k EUR + travail

---

## Appendice : Technologies Clés

### MediaPipe Pose

- **Développeur :** Google | **Licence :** Apache 2.0
- **Points Clés :** 33 corps + visage
- **Performance :** 30-60fps sur RTX 3090 (5 danseurs)

### YOLOv8

- **Développeur :** Ultralytics | **Licence :** AGPL-3.0
- **Fonction :** Détection personne multi-scale
- **Performance :** 60fps+ sur RTX 3090 (5 danseurs)

### Prophesee EVK4

- **Capteur :** Sony IMX636ES (1280×720)
- **Plage Dynamique :** 120+ dB
- **SDK :** Metavision (gratuit, enregistrement requis)

### Open Sound Control (OSC)

- **Protocole :** UDP message passing
- **Latence :** <1ms sur LAN
- **Librairies :** python-osc, liblo, oscpack

---

## Version Document

- **Version :** 3.0 (Restructuré - Plan linéaire incrémental)
- **Date :** 7 Novembre 2025
- **Statut :** Prêt pour review et approvisionnement

---

*Fin du Plan d'Implémentation Technique*
