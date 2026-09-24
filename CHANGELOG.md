# Journal des versions

## 1.1.17 — 2026-09-24

- Mixeur : barre de chargement audio dédiée (%, voies), visible dès le deep-link `?t=` / seek ; préchauffage buffer au positionnement.

## 1.1.16 — 2026-09-23

- METAREA FR : lexique synoptique complété (moving, weakening, New High, Monsoon trough, vestige Fay, UTC collé, coords décimales).
- Trafic : deep-link `?t=` positionne correctement le mixeur (plus de clamp à 1 s avant métadonnées audio).

## 1.1.15 — 2026-09-23

- Buddy : max 2 QRG par Kiwi ; 4/6 MHz sur NVIS (proche), 8/12 MHz sur saut ; détection « same IP » au screencast.
- Trafic : bouton copier le lien (`/trafic/<id>?zoom=1&ch=&t=`) ; clic droit sur waterfall / barre TU pour se placer.
- METAREA FR : lexique enrichi ; positions `52N35W` lues « 52 Nord, 35 Ouest » (TTS inclus).

## 1.1.14 — 2026-09-23

- URL profonde : `/?trafic=<id>&zoom=1` ou `/trafic/<id>?zoom=1` (piste via `&ch=`) ; retour après login si besoin.
- Buddy call : écoute **8294** et **12353 kHz** en plus de 4483 / 6516 (répartition sur les places libres des Kiwi).

## 1.1.13 — 2026-09-22

- Interface **FR/EN** complète (boutons, À propos, Setup, mixage, calques, METAREA).
- Bulletin METAREA : lecture et disclaimer en anglais officiel WWMIWS sous EN (pas une retraduction).
- MP3 METAREA : voix neurale edge-tts (Denise/Henri FR, Sonia/Ryan EN) ; plus d’espeak.
- KiwiSDR bulletin : faisceau France → flotte → Atlantique Sud (`min_along` 0,75) + omni ACK/buddy vers l’avant.
- SSO Google + connexion e-mail (lien / OTP) ; liste blanche ANFR ; Loki avec indicatif / e-mail / surnom.

## 1.1.12 — 2026-09-21

- UI et enregistreur sur deux pods : Chromium ne sature plus le site pendant un bulletin / buddy.
- `/health` allégé pour kubelet ; l’état d’enregistrement passe par `/api/recording`.
- Preview sans enregistreur ; leftover preview mis à 0 au déploiement prod.

## 1.1.11 — 2026-09-21

- Onglet Trafic : heure d’antenne **12:00 TU** / **18:00 TU** (la minute d’avance sert à se caler, expliquée dans Setup).
- Menu général fermé à l’arrivée ; première ouverture manuelle sur **À propos**.
- Purge manuelle des archives : `python -m app.store list|delete|purge` (hors UI).

## 1.1.10 — 2026-09-20

- À propos : ordre des émetteurs bulletin selon la flotte — Guy F4DAI (cette semaine Philippe F4HWM), puis Cap Town, enfin Michel FO5QB.

## 1.1.9 — 2026-09-20

- Cette semaine : bulletin France **Philippe F4HWM / F6KUF** depuis Talmont-Saint-Hilaire (lun. et jeu. 18:00 TU).
- **Michel FO5QB** : 14.135 MHz USB **tous les jours** 18:00 TU ; Kiwi France seulement lun. et jeu.
- Intro globe : pan 2,5 s avec bandeau lent, zoom 2 s vers la flotte, puis 2 s encore et arrêt pour lire le bandeau.

## 1.1.8 — 2026-09-20

- Globe : plus d’écran noir à l’arrivée (variables `globe` / `map` initialisées avant l’onglet À propos).
- Page d’attente « Site momentanément indisponible » (globe 3D flouté) : auto si le globe plante, 503 serveur, Setup, ou `/?wait=1`.

## 1.1.7 — 2026-09-20

- Mixeur : le chargement audio ne reste plus figé à 0/4 (play muet pour lancer le fetch, puis seek).
- Calques : bandeau GGR trafic HF on/off ; libellé « SDR choisis selon centre flotte » pour le prochain record schedulé.
- Setup : défauts d’affichage globe (bandeau, SDR, skippers, bateaux, METAREA, sous-zones).

## 1.1.6 — 2026-09-20

- Boucle A–B : traits or collés aux bornes (plus le centre des poignées) ; le son ne reprend qu’après le seek, sans dépasser A ni B.

## 1.1.5 — 2026-09-20

- Boucle A–B : la lecture reste entre les bornes (plus de départ avant A ni de dépassement après B).
- Zoom d’une voie : mute retiré ; piste amplitude façon Audacity sous le spectrogramme, avec légende des couleurs USB.

## 1.1.4 — 2026-09-20

- Annuaire KiwiSDR local, rafraîchi toutes les heures : le calque SDR potentiels ne dépend plus d’un fetch à la demande.
- SDR potentiels plus petits (4 px) avec le même clignotement 0,5 s que les SDR d’un mux.
- Zoom d’une voie : icônes type YouTube, barre de lecture en bas, boucle avec bornes A–B façon Audacity.
- Libellés des liaisons collés à la tangente écran du trait (le zoom / POV ne les décale plus).
- Menus repliés : « Calques » et « Menu général » en vertical derrière la flèche, avec une légère surbrillance.

## 1.1.3 — 2026-09-20

- Mux déplié : SDR du record (pas les Kiwi live), pointillés clignotants 0,5 s, libellés nom + distance.
- Zoom d’une voie : panneau presque pleine largeur, bornes A–B sur le waterfall, lecture en boucle.
- Calques carte à gauche (en face du rail) : SDR actifs / potentiels, skippers, bateaux + traces, METAREA, sous-zones.

## 1.1.2 — 2026-09-20

- Setup : journal visites par IP (pages consultées, replays lancés, horodatage TU), 14 jours.
- Lieu = nœud du préfixe FAI (ville, code postal, région, opérateur, reverse DNS), pas le GPS du foyer.

## 1.1.1 — 2026-09-19

- Globe : calque **METAREA / Sous-zone** ; l’onglet METAREA suit le bulletin WWMIWS de la zone occupée par toute la flotte (II aujourd’hui, puis VII, VIII-S, X, XIV, XV).
- Lecture OM : condensé automatique (avis voisins, côtiers seulement si la zone de service recouvre la flotte, sans doublon de zone ni de texte) ; l’OM doit le recouper avec l’officiel.

## 1.1.0 — 2026-09-18

- Globe 2D/3D : calque METAREA (limites OHI, teintes OMM), découpé sur l’océan pour suivre les côtes ; le bandeau GGR est masqué tant que le calque est allumé.

## 1.0.9 — 2026-09-18

- Mixer : PNG waterfall via `fetch` (cache + préchargement liste Trafic), sans WAV ni FFT ; tuiles OSM sur un pool séparé pour ne plus bloquer `/media`.

## 1.0.8 — 2026-09-18

- Mixer : afficher d’abord les PNG waterfall (déjà calculés), sans lancer le téléchargement des WAV — les waterfalls restaient vides ~80 s le temps des 14 Mo × N voies.

## 1.0.7 — 2026-09-17

- Mixer : ne plus afficher « Aucune piste audio décodable » quand le waterfall PNG est déjà là et que les WAV se chargent encore.

## 1.0.6 — 2026-09-17

- Waterfall USB calculé et stocké (`waterfall-{id}.png`) dès la fin de chaque WAV (en parallèle des autres voies) ; le mux charge ce PNG au lieu de refaire la FFT à chaque ouverture.

## 1.0.5 — 2026-09-17

- Accordéon Trafic : afficher tout de suite les lignes mux (volume / mute / QRG / waterfall) au lieu d’attendre `/api/trafic/…` — l’accordéon restait vide (lecture + « — TU » seulement).

## 1.0.4 — 2026-09-17

- Mixer : volume, mute, QRG/SDR, puis waterfall (plus l’inverse).
- Waterfall USB dessiné au fil de l’extraction, avec **Extraction bande son en cours… xx %** — il restait noir tant que le WAV entier n’était pas FFT.

## 1.0.3 — 2026-09-17

- Badge : **Prochain enregistrement** `hh:mm:ss` (buddy 11:59 TU ou bulletin 17:59 TU) ; pendant un record, décompte restant.
- Onglet Trafic : une seule liste (météo, buddy, test), sans sous-onglets ni bandeau.
- Mixer en accordéon sous la ligne (un seul ouvert) : pistes horizontales (waterfall, mute, volume), 6 voies max.

## 1.0.2 — 2026-09-17

- Mixer : lecture / pause en symboles (triangle / barres), plus de texte.
- Glisser le curseur waterfall déplace aussi la timeline **Heure TU**.
- Listes météo et buddy : nombre de KiwiSDR distincts ayant de l’audio, pas le nombre de QRG.

## 1.0.1 — 2026-09-17

- UI : badge **enregistrement en cours** avec décompte restant `hh:mm:ss` (globe même panneau replié, barre QRG). `/health` expose la fin prévue.
- `update.sh` refuse un Recreate s’il y a un `.recording.lock` : le deploy visites du 17/09 à 12:03 TU a tué le buddy call 4483 / 6516 kHz, l’audio n’était encore qu’en RAM.

## 1.0.0 — 2026-09-17

- Première version de production : bulletin 14.135 MHz USB près de l’émetteur (F6KUF, puis Tahiti après Bonne-Espérance) et près de la flotte ; ACK 16.551 / 12.418 MHz ; buddy call 4483 / 6516 kHz.
- Globe 3D OSM au démarrage (sans panneau), carte 2D OSM commutée, marqueurs bateaux et Kiwi ; À propos après l’intro.
- Déploiement k3s (`ggr-trafic`), métriques Prometheus, replay des archives.

## 0.3.9 — 2026-09-17

- Carte 2D : initialiser Leaflet (`L.map`) — le commutateur 2D n’affichait qu’un fond vide.

## 0.3.8 — 2026-09-17

- Globe : carte OpenStreetMap 2D avec commutateur 2D / 3D (même flotte, Kiwi, QTH ; le choix est mémorisé).

## 0.3.7 — 2026-09-14

- Globe : bandeau équatorial « GGR 2026 - trafic HF » (or #DEB200, Montserrat 800) pendant l’intro ; 55 % d’opacité ensuite, quasi transparent si la flotte passe sous l’équateur.

## 0.3.6 — 2026-09-14

- Globe : légendes distance / azimut sur les pointillés à la même taille que les noms de skippers.

## 0.3.5 — 2026-09-14

- Globe : jusqu’à 5 QTH d’émission du bulletin, pointillé vers le centroïde flotte avec distance et azimut d’antenne (vrai nord).
- Lecteurs audio / vidéo et mixeur : horloge et curseur en heure TU ; waterfall mixeur avec t=0 en bas ; fader volume plus fort vers le haut.
- WAV servis en `audio/wav` avec cache navigateur 24 h.

## 0.3.4 — 2026-09-14

- Globe flotte : cercle autour du centroïde, liaisons SDR en pointillés 2 px, distances km sur la ligne (milliers collés) et libellés `sdr, <ville>`.
- Table de mixage : waterfall USB vertical (couleurs Kiwi), une colonne par voie avec fader / mute, et démarrage audio après reprise de l’AudioContext.

## 0.3.3 — 2026-09-13

- Spectrogramme de la table de mixage : contraste calé sur les percentiles 25–99 (au lieu d’un tapis de bruit) pour lire la phonie USB.

## 0.3.2 — 2026-09-13

- Table de mixage : spectrogrammes USB empilés, volume / muet par Kiwi, timeline commune.
