# Journal des versions

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
