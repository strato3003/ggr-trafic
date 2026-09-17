# Journal des versions

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
