"""Bulletins haute mer WWMIWS : METAREA détectée par la flotte, sous-zones si grille connue."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any

import httpx

from recorder.geo import fmt_latlon, point_in_feature

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
SUBZONES_PATH = ROOT / "static" / "geo" / "metarea2-subzones.json"
METAREAS_PATH = ROOT / "static" / "geo" / "metareas.json"
WWMIWS = "https://wwmiws.wmo.int/index.php/metareas"
UA = "GGR-Trafic/1.1.18 (F6KUF; https://ggr-trafic.k3s.lpb.ovh)"
CACHE_TTL_S = 20 * 60
# Grilles intérieures officielles (rectangles + océan). Les autres METAREA
# basculent seules via le polygone OHI + le bulletinset WWMIWS du même n°.
GRIDS = {"II"}
# Horaires SafetyNET connus (Météo-France METAREA II).
SCHEDULE = {"II": ("10:15", "22:15")}
ISSUERS = {"II": "Météo-France Toulouse"}
# Marge (degrés) : un bateau au large mais collé à une boîte côtière
# déclenche le bulletin côtier voisin (ex. Lanzarote → AGADIR).
COASTAL_PAD_DEG = 1.0
DISCLAIMER = (
    "Ce texte est un condensé automatique, calculé d’après les bulletins WWMIWS "
    "et la position de la flotte. Il appartient à l’OM d’en vérifier le contenu "
    "sur le bulletin officiel avant toute lecture à l’antenne."
)
DISCLAIMER_EN = (
    "This text is an automatic digest, computed from WWMIWS bulletins "
    "and the fleet position. The operator must check it against the official "
    "bulletin before any on-air reading."
)
# Bulletins côtiers / NAVTEX de la page WWMIWS. COASTAL_FOR = filtre METAREA II
# (on ne charge Corsen que si la flotte y est). Pour VII, VIII-S, X, XIV, XV :
# tout extra non « HIGH SEAS » est chargé, puis filtré et dédupliqué (zone + texte).
COASTAL_FOR: dict[str, list[str]] = {
    "FQNT72": ["CANARIAS", "AGADIR", "TARFAYA", "CASABLANCA", "MADEIRA", "CAP BLANC"],
    "FQMQ72": ["CADIZ", "GIBRALTAR STRAIT", "SAO VICENTE"],
    "WWMD60": ["MADEIRA"],
    "WWAZ60": ["ACORES"],
    "FQNT71": ["FINISTERRE", "PORTO", "CANTABRICO"],
    "WWPO60": ["PORTO", "SAO VICENTE"],
    "FQNT53": ["PAZENN", "IROISE", "YEU", "ROCHEBONNE", "CANTABRICO"],
}
# Plus spécifique d’abord : Las Palmas avant Tarifa (CASABLANCA atlantique ≠ détroit).
COASTAL_RANK = {
    "WWMD60": 0,
    "WWAZ60": 0,
    "FQNT72": 1,
    "FQMQ72": 2,
    "FQNT71": 2,
    "WWPO60": 2,
    "FQNT53": 2,
}


def wwmiws_urls(n: int) -> dict[str, str]:
    return {
        "metarea": f"{WWMIWS}/affiche/{n}",
        "html": f"{WWMIWS}/bulletinset/{n}/html",
        "json": f"{WWMIWS}/bulletinset_download/{n}/json",
    }

# Grille opérationnelle du bulletin haute mer METAREA II (produit FQNT52 LFPW,
# Météo-France Toulouse). WWMIWS ne publie pas de GeoJSON intérieur : rectangles
# (sud, nord, ouest, est) d’après la carte des zones de prévision haute mer
# (bandes 5°, limite nord 48°27'N = 48,45°, méridiens 35/25/20/15/13/10/8°W),
# puis intersection océan ∩ polygone OHI METAREA II (script clip_metarea_subzones).
BOXES: list[dict[str, Any]] = [
    {"name": "FARADAY", "s": 45.0, "n": 48.45, "w": -35.0, "e": -15.0, "kind": "offshore", "fill": "#2a6f97"},
    {"name": "ROMEO", "s": 45.0, "n": 48.45, "w": -15.0, "e": -8.0, "kind": "offshore", "fill": "#3d8a6e"},
    {"name": "ALTAIR", "s": 40.0, "n": 45.0, "w": -35.0, "e": -20.0, "kind": "offshore", "fill": "#2a6f97"},
    {"name": "CHARCOT", "s": 40.0, "n": 45.0, "w": -20.0, "e": -10.0, "kind": "offshore", "fill": "#3d8a6e"},
    {"name": "ACORES", "s": 35.0, "n": 40.0, "w": -35.0, "e": -25.0, "kind": "offshore", "fill": "#2a6f97"},
    {"name": "JOSEPHINE", "s": 35.0, "n": 40.0, "w": -25.0, "e": -15.0, "kind": "offshore", "fill": "#3d8a6e"},
    {"name": "IRVING", "s": 35.0, "n": 40.0, "w": -15.0, "e": -10.0, "kind": "offshore", "fill": "#b5812e"},
    {"name": "METEOR", "s": 30.0, "n": 35.0, "w": -35.0, "e": -25.0, "kind": "offshore", "fill": "#2a6f97"},
    {"name": "MADEIRA", "s": 30.0, "n": 35.0, "w": -25.0, "e": -15.0, "kind": "offshore", "fill": "#3d8a6e"},
    {"name": "CANARIAS", "s": 25.0, "n": 30.0, "w": -35.0, "e": -13.0, "kind": "offshore", "fill": "#c45c26"},
    {"name": "CAPE VERDE", "s": 12.0, "n": 25.0, "w": -35.0, "e": -20.0, "kind": "offshore", "fill": "#2a6f97"},
    {"name": "PAZENN", "s": 47.3, "n": 48.45, "w": -8.0, "e": -5.3, "kind": "coastal", "fill": "#6b5a2a"},
    {"name": "IROISE", "s": 47.3, "n": 48.45, "w": -5.3, "e": -4.2, "kind": "coastal", "fill": "#6b5a2a"},
    {"name": "YEU", "s": 46.0, "n": 47.3, "w": -8.0, "e": -1.7, "kind": "coastal", "fill": "#6b5a2a"},
    {"name": "ROCHEBONNE", "s": 45.0, "n": 46.0, "w": -8.0, "e": -1.3, "kind": "coastal", "fill": "#6b5a2a"},
    {"name": "CANTABRICO", "s": 43.5, "n": 45.0, "w": -10.0, "e": -1.2, "kind": "coastal", "fill": "#6b5a2a"},
    {"name": "FINISTERRE", "s": 41.5, "n": 43.5, "w": -10.0, "e": -8.0, "kind": "coastal", "fill": "#6b5a2a"},
    {"name": "PORTO", "s": 40.0, "n": 41.5, "w": -10.0, "e": -8.0, "kind": "coastal", "fill": "#6b5a2a"},
    {"name": "SAO VICENTE", "s": 36.0, "n": 40.0, "w": -10.0, "e": -7.2, "kind": "coastal", "fill": "#6b5a2a"},
    {"name": "CADIZ", "s": 35.8, "n": 37.2, "w": -8.0, "e": -6.0, "kind": "coastal", "fill": "#8a3a3a"},
    {"name": "GIBRALTAR STRAIT", "s": 35.7, "n": 36.3, "w": -6.0, "e": -5.1, "kind": "coastal", "fill": "#8a3a3a"},
    {"name": "CASABLANCA", "s": 30.0, "n": 36.0, "w": -15.0, "e": -6.0, "kind": "coastal", "fill": "#6b5a2a"},
    {"name": "AGADIR", "s": 25.0, "n": 30.0, "w": -13.0, "e": -9.0, "kind": "coastal", "fill": "#c45c26"},
    {"name": "TARFAYA", "s": 20.0, "n": 25.0, "w": -20.0, "e": -12.0, "kind": "coastal", "fill": "#c45c26"},
    {"name": "CAP BLANC", "s": 16.0, "n": 20.0, "w": -20.0, "e": -16.0, "kind": "coastal", "fill": "#6b5a2a"},
    {"name": "CAP TIMIRIS", "s": 12.0, "n": 16.0, "w": -20.0, "e": -16.0, "kind": "coastal", "fill": "#6b5a2a"},
    {"name": "SIERRA LEONE", "s": 4.0, "n": 12.0, "w": -20.0, "e": -10.0, "kind": "coastal", "fill": "#6b5a2a"},
    {"name": "GULF OF GUINEA", "s": 0.0, "n": 5.0, "w": -8.0, "e": 10.0, "kind": "coastal", "fill": "#6b5a2a"},
    {"name": "POINTE NOIRE", "s": -6.0, "n": 0.0, "w": 5.0, "e": 12.4, "kind": "coastal", "fill": "#6b5a2a"},
]
BOX_BY_NAME = {b["name"]: b for b in BOXES}

_JOURS = {
    "monday": "lundi",
    "tuesday": "mardi",
    "wednesday": "mercredi",
    "thursday": "jeudi",
    "friday": "vendredi",
    "saturday": "samedi",
    "sunday": "dimanche",
}
_MOIS = {
    "january": "janvier",
    "february": "février",
    "march": "mars",
    "april": "avril",
    "may": "mai",
    "june": "juin",
    "july": "juillet",
    "august": "août",
    "september": "septembre",
    "october": "octobre",
    "november": "novembre",
    "december": "décembre",
}

# Phrases longues d’abord — vocabulaire bulletin marine MF / UK, pour lecture HF.
# Pas de mots isolés (to/of/in/at) : ils cassent les positions 40N23W.
_FR_RAW: list[tuple[str, str]] = [
    (r"\bmoving east slowly\b", "se déplaçant lentement vers l'est"),
    (r"\bmoving west slowly\b", "se déplaçant lentement vers l'ouest"),
    (r"\bmoving slowly\b", "se déplaçant lentement"),
    (r"\bRidge extending northeastwards\b", "dorsale s'étendant vers le nord-est"),
    (r"\bRidge extending\b", "dorsale s'étendant"),
    (r"\bextending northeastwards\b", "s'étendant vers le nord-est"),
    (r"\bextending southeastwards\b", "s'étendant vers le sud-est"),
    (r"\bextending northwestwards\b", "s'étendant vers le nord-ouest"),
    (r"\bextending southwestwards\b", "s'étendant vers le sud-ouest"),
    (r"\bnortheastwards\b", "vers le nord-est"),
    (r"\bsoutheastwards\b", "vers le sud-est"),
    (r"\bnorthwestwards\b", "vers le nord-ouest"),
    (r"\bsouthwestwards\b", "vers le sud-ouest"),
    (r"\beastwards\b", "vers l'est"),
    (r"\bwestwards\b", "vers l'ouest"),
    (r"\bnorthwards\b", "vers le nord"),
    (r"\bsouthwards\b", "vers le sud"),
    (r"\bA cold front\b", "Un front froid"),
    (r"\bcold front\b", "front froid"),
    (r"\bwarm front\b", "front chaud"),
    (r"\boccluded front\b", "front occlus"),
    (r"\bis moving east\b", "se déplace vers l'est"),
    (r"\bis moving west\b", "se déplace vers l'ouest"),
    (r"\bis moving\b", "se déplace"),
    (r"\bPressure falling\b", "Pression en baisse"),
    (r"\bPressure rising\b", "Pression en hausse"),
    (r"\bpressure falling\b", "pression en baisse"),
    (r"\bpressure rising\b", "pression en hausse"),
    (r"\bVisibility:\s*Moderate or poor\b", "Visibilité : moyenne ou médiocre"),
    (r"\bVisibility:\s*Poor or very poor\b", "Visibilité : médiocre ou mauvaise"),
    (r"\bVisibility:\s*Good becoming moderate\b", "Visibilité : bonne devenant moyenne"),
    (r"\bVisibility:\s*Good\b", "Visibilité : bonne"),
    (r"\bVisibility:\s*Moderate\b", "Visibilité : moyenne"),
    (r"\bVisibility:\s*Poor\b", "Visibilité : médiocre"),
    (r"\bVisibility:\s*", "Visibilité : "),
    (r"\bOutlook:\s*Similar\b", "Tendance : similaire"),
    (r"\bOutlook:\s*", "Tendance : "),
    (r"\bWind:\s*", "Vent : "),
    (r"\bSea:\s*", "Mer : "),
    (r"\bSwell:\s*", "Houle : "),
    (r"\bModerate or poor\b", "moyenne ou médiocre"),
    (r"\bGood becoming moderate\b", "bonne devenant moyenne"),
    (r"\bin rain or showers\b", "sous la pluie ou les averses"),
    (r"\bin rain\b", "sous la pluie"),
    (r"\bin showers\b", "sous les averses"),
    (r"\bOccasional rain\b", "Pluie occasionnelle"),
    (r"\boccasional rain\b", "pluie occasionnelle"),
    (r"\bFog patches\b", "Bancs de brouillard"),
    (r"\bfog patches\b", "bancs de brouillard"),
    (r"\boccasionally\b", "parfois"),
    (r"\boccasional\b", "occasionnel"),
    (r"\bSimilar\b", "Similaire"),
    (r"\bsimilar\b", "similaire"),
    (r"\bslowly\b", "lentement"),
    (r"\bShowers\b", "Averses"),
    (r"\bshowers\b", "averses"),
    (r"\brain\b", "pluie"),
    (r"\bfog\b", "brouillard"),
    (r"\bpoor\b", "médiocre"),
    (r"\bwith associated ridge towards\b", "avec dorsale associée vers"),
    (r"\bassociated ridge towards\b", "dorsale associée vers"),
    (r"\bassociated ridge\b", "dorsale associée"),
    (r"\bRidge\b", "Dorsale"),
    (r"\bridge\b", "dorsale"),
    (r"\bassociated Dorsale\b", "dorsale associée"),
    (r"\bbecoming west\b", "devenant d'ouest"),
    (r"\bbecoming east\b", "devenant d'est"),
    (r"\bbecoming north\b", "devenant de nord"),
    (r"\bbecoming south\b", "devenant de sud"),
    (r"\bwest (\d)\b", r"ouest \1"),
    (r"\beast (\d)\b", r"est \1"),
    (r"\bnorth (\d)\b", r"nord \1"),
    (r"\bsouth (\d)\b", r"sud \1"),
    (r"\bAT LEAST\b", "au moins"),
    (r"\bwith associated ridge in Bay of Biscay\b", "avec dorsale associée dans le golfe de Gascogne"),
    (r"\bin Bay of Biscay\b", "dans le golfe de Gascogne"),
    (r"\bBay of Biscay\b", "golfe de Gascogne"),
    (r"\bgradually filling\b", "se comblant progressivement"),
    (r"\bslowly filling\b", "se comblant lentement"),
    (r"\bgradually deepening\b", "se creusant progressivement"),
    (r"\bslowly deepening\b", "se creusant lentement"),
    (r"\bStationary\b", "Stationnaire"),
    (r"\bstationary\b", "stationnaire"),
    (r"\bFilling\b", "Se comblant"),
    (r"\bfilling\b", "se comblant"),
    (r"\bDeepening\b", "Se creusant"),
    (r"\bdeepening\b", "se creusant"),
    (r"\bTropical depression\b", "dépression tropicale"),
    (r"\bTropical storm\b", "tempête tropicale"),
    (r"\bis now a post-tropical remnant Low pressure of\b", "est maintenant le vestige d'une dépression post-tropicale de"),
    (r"\bis now a post-tropical remnant Low\b", "est maintenant le vestige d'une dépression post-tropicale"),
    (r"\bpost-tropical remnant Low pressure of\b", "vestige d'une dépression post-tropicale de"),
    (r"\bpost-tropical remnant Low\b", "vestige d'une dépression post-tropicale"),
    (r"\bpost-tropical remnant\b", "vestige d'une dépression post-tropicale"),
    (r"\bPost-tropical\b", "post-tropicale"),
    (r"\bHurricane\b", "ouragan"),
    (r"\bTropical wave near\b", "onde tropicale près de"),
    (r"\bTropical wave along\b", "onde tropicale le long de"),
    (r"\bTropical wave\b", "onde tropicale"),
    (r"\bwith associated trough over\b", "avec talweg associé sur"),
    (r"\bwith associated trough\b", "avec talweg associé"),
    (r"\bassociated trough over\b", "talweg associé sur"),
    (r"\bassociated trough\b", "talweg associé"),
    (r"\bAssociated trough\b", "Talweg associé"),
    (r"\bwith associated ridge in\b", "avec dorsale associée dans"),
    (r"\bwith associated ridge over\b", "avec dorsale associée sur"),
    (r"\bnorthwestern areas\b", "les régions du nord-ouest"),
    (r"\bnortheastern areas\b", "les régions du nord-est"),
    (r"\bsouthwestern areas\b", "les régions du sud-ouest"),
    (r"\bsoutheastern areas\b", "les régions du sud-est"),
    (r"\bnorthern areas\b", "les régions du nord"),
    (r"\bsouthern areas\b", "les régions du sud"),
    (r"\bwestern areas\b", "les régions de l'ouest"),
    (r"\beastern areas\b", "les régions de l'est"),
    (r"\bmoving westward at around\b", "se déplaçant vers l'ouest à environ"),
    (r"\bmoving eastward at around\b", "se déplaçant vers l'est à environ"),
    (r"\bmoving westward\b", "se déplaçant vers l'ouest"),
    (r"\bmoving eastward\b", "se déplaçant vers l'est"),
    (r"\bmoving northward\b", "se déplaçant vers le nord"),
    (r"\bmoving southward\b", "se déplaçant vers le sud"),
    (r"\bat around\b", "à environ"),
    (r"\bcontinuing to\b", "se poursuivant jusqu'à"),
    (r"\bcontinuing\b", "se poursuivant"),
    (r"\bbecoming High\b", "devenant anticyclonique"),
    (r"\band expected\b", "et prévu"),
    (r"\band then\b", "puis"),
    (r"\bGibraltar Strait\b", "détroit de Gibraltar"),
    (r"\bEast of Cadiz\b", "est de Cadix"),
    (r"\bin and leeward strait\b", "dans le détroit et sous le vent"),
    (r"\bleeward strait\b", "sous le vent du détroit"),
    (r"\bwith little change\b", "peu d'évolution"),
    (r"\bLittle change\b", "peu d'évolution"),
    (r"\bPersistence of associated ridge over France\b", "Persistance de la dorsale associée sur la France"),
    (r"\bAssociated disturbance waving from\b", "Perturbation associée ondulant des"),
    (r"\bAssociated trough reaching\b", "Talweg associé atteignant"),
    (r"\bAssociated ridge\b", "dorsale associée"),
    (r"\bComplex low\b", "Dépression complexe"),
    (r"\bNew low expected\b", "Nouvelle dépression prévue"),
    (r"\bNew High\b", "Nouvel anticyclone"),
    (r"\bNew Low\b", "Nouvelle dépression"),
    (r"\bThundery low\b", "Dépression orageuse"),
    (r"\bMonsoon trough near\b", "Talweg de mousson près de"),
    (r"\bMonsoon trough from\b", "Talweg de mousson de"),
    (r"\bMonsoon trough\b", "Talweg de mousson"),
    (r"\bis now a\b", "est maintenant un"),
    (r"\bremnant Low\b", "dépression résiduelle"),
    (r"\bremnant\b", "vestige"),
    (r"\bpressure of\b", "pression de"),
    (r"\bcentered near\b", "centrée près de"),
    (r"\bwinds of\b", "vents de"),
    (r"\bThe ITCZ extends from\b", "La ZCIT s'étend de"),
    (r"\band continues to\b", "et se poursuit vers"),
    (r"\bjust southwest\b", "juste au sud-ouest de"),
    (r"\bmoving northeast\b", "se déplaçant vers le nord-est"),
    (r"\bmoving towards\b", "se dirigeant vers"),
    (r"\bmoving west near\b", "se déplaçant vers l'ouest près de"),
    (r"\bmoving west\b", "se déplaçant vers l'ouest"),
    (r"\bmoving\b", "se déplaçant"),
    (r"\bweakening\b", "s'affaiblissant"),
    (r"\bstrengthening\b", "se renforçant"),
    (r"\bdeepening on place\b", "se creusant sur place"),
    (r"\bassociated\b", "associé"),
    (r"\btowards\b", "vers"),
    (r"\bover north of France\b", "sur le nord de la France"),
    (r"\bnorth of France\b", "nord de la France"),
    (r"\bover Morocco\b", "sur le Maroc"),
    (r"\bover France\b", "sur la France"),
    (r"\bthe British isles\b", "îles Britanniques"),
    (r"\bNorwegian Sea\b", "mer de Norvège"),
    (r"\bwest of\b", "ouest de"),
    (r"\beast of\b", "est de"),
    (r"\bsouth of\b", "sud de"),
    (r"\bnorth of\b", "nord de"),
    (r"\bcoastal fog patches\b", "bancs de brouillard côtier"),
    (r"\bsand haze\b", "brume de sable"),
    (r"\bthundersqualls\b", "grains orageux"),
    (r"\brain or thundery showers\b", "pluie ou averses orageuses"),
    (r"\bthundery showers\b", "averses orageuses"),
    (r"\bscattered rain or showers\b", "pluie ou averses éparses"),
    (r"\bsome showers\b", "quelques averses"),
    (r"\bnear islands\b", "près des îles"),
    (r"\bN OR NE\b", "nord ou nord-est"),
    (r"\bN OR NW\b", "nord ou nord-ouest"),
    (r"\bE OR NE\b", "est ou nord-est"),
    (r"\bEXCEPT\b", "sauf"),
    (r"\bNEARS ISLANDS\b", "près des îles"),
    (r"\bLIGHT RAINS\b", "pluies faibles"),
    (r"\bIN NE\b", "au nord-est"),
    (r"\bIN SE\b", "au sud-est"),
    (r"\bIN E\b", "à l'est"),
    (r"\bIN W\b", "à l'ouest"),
    (r"\bbetween islands\b", "entre les îles"),
    (r"\bGALE OR NEAR GALE WARNINGS:\s*NONE\.?", "Avis de coup de vent : néant"),
    (r"\bGALE WARNINGS:\s*NONE\.?", "Avis de coup de vent : néant"),
    (r"\bFM THE EARLY MORNING\b", "dès le petit matin"),
    (r"\bFM THE MORNING\b", "dans la matinée"),
    (r"\bFM MIDNIGHT\b", "à partir de minuit"),
    (r"\bAT END\b", "en fin de période"),
    (r"\bSMOOTH OR SLGT\b", "mer calme ou peu agitée"),
    (r"\bSLGT OR MOD\b", "mer peu agitée ou agitée"),
    (r"\bMOD\b", "mer agitée"),
    (r"\bBECMG\b", "devenant"),
    (r"\bDECR\b", "faiblissant"),
    (r"\bINCR\b", "se renforçant"),
    (r"\bTEMPO\b", "temporairement"),
    (r"\bNOSIG\b", "sans changement notable"),
    (r"\bSHWRS\b", "averses"),
    (r"\bTHUNDERSTORMS\b", "orages"),
    (r"\bSMOOTH\b", "mer calme"),
    (r"\bSLGT\b", "mer peu agitée"),
    (r"\bVRB\b", "variable"),
    (r"\bLOC\b", "localement"),
    (r"\bnear the cape\b", "près du cap"),
    (r"\bnear coast\b", "près de la côte"),
    (r"\blong NW swell\b", "longue houle de nord-ouest"),
    (r"\bNW swell\b", "houle de nord-ouest"),
    (r"\bModerate or poor vis\b", "visibilité moyenne ou médiocre"),
    (r"\bPoor or very poor vis\b", "visibilité médiocre ou mauvaise"),
    (r"\bVery poor vis\b", "visibilité mauvaise"),
    (r"\bPoor vis\b", "visibilité médiocre"),
    (r"\bModerate vis\b", "visibilité moyenne"),
    (r"\bSlight or moderate\b", "mer peu agitée ou agitée"),
    (r"\bModerate or rough\b", "mer agitée ou forte"),
    (r"\bvery rough\b", "mer très forte"),
    (r"\bSevere gusts\b", "rafales fortes"),
    (r"\bnear gale or gale\b", "fort coup de vent ou coup de vent"),
    (r"\bnear gale\b", "fort coup de vent"),
    (r"\bwith coastal breeze\b", "avec brise de mer"),
    (r"\btomorrow afternoon\b", "demain après-midi"),
    (r"\btomorrow morning\b", "demain matin"),
    (r"\btomorrow evening\b", "demain soir"),
    (r"\bfrom west soon\b", "par l'ouest bientôt"),
    (r"\bfrom east soon\b", "par l'est bientôt"),
    (r"\bfrom northwest to southeast\b", "du nord-ouest au sud-est"),
    (r"\bfrom northwest\b", "par le nord-ouest"),
    (r"\bfrom southwest\b", "par le sud-ouest"),
    (r"\bfrom east\b", "par l'est"),
    (r"\bfrom west\b", "par l'ouest"),
    (r"\bat first\b", "d'abord"),
    (r"\bat end\b", "en fin de période"),
    (r"\bat times\b", "par moments"),
    (r"\bIn far southeast\b", "À l'extrême sud-est"),
    (r"\bin far southeast\b", "à l'extrême sud-est"),
    (r"\bIn far northwest\b", "À l'extrême nord-ouest"),
    (r"\bin far northwest\b", "à l'extrême nord-ouest"),
    (r"\bfar northwest\b", "extrême nord-ouest"),
    (r"\bfar northeast\b", "extrême nord-est"),
    (r"\bfar southwest\b", "extrême sud-ouest"),
    (r"\bfar southeast\b", "extrême sud-est"),
    (r"\bfar north\b", "extrême nord"),
    (r"\bfar south\b", "extrême sud"),
    (r"\bfar west\b", "extrême ouest"),
    (r"\bfar east\b", "extrême est"),
    (r"\bextreme northwest\b", "extrême nord-ouest"),
    (r"\bextreme southwest\b", "extrême sud-ouest"),
    (r"\bextreme west\b", "extrême ouest"),
    (r"\bextreme east\b", "extrême est"),
    (r"\bin northwest\b", "au nord-ouest"),
    (r"\bin northeast\b", "au nord-est"),
    (r"\bin southwest\b", "au sud-ouest"),
    (r"\bin southeast\b", "au sud-est"),
    (r"\bin west\b", "à l'ouest"),
    (r"\bin east\b", "à l'est"),
    (r"\bin north\b", "au nord"),
    (r"\bin south\b", "au sud"),
    (r"\bNorth or Northeast\b", "nord ou nord-est"),
    (r"\bNorth or Northwest\b", "nord ou nord-ouest"),
    (r"\bSouth or Southwest\b", "sud ou sud-ouest"),
    (r"\bSouth or Southeast\b", "sud ou sud-est"),
    (r"\bEast or Northeast\b", "est ou nord-est"),
    (r"\bEast or Southeast\b", "est ou sud-est"),
    (r"\bWest or Southwest\b", "ouest ou sud-ouest"),
    (r"\bWest or Northwest\b", "ouest ou nord-ouest"),
    (r"\bMainly Easterly\b", "surtout d'est"),
    (r"\bMainly West\b", "surtout d'ouest"),
    (r"\bNortheasterly\b", "vent de nord-est"),
    (r"\bNorthwesterly\b", "vent de nord-ouest"),
    (r"\bSoutheasterly\b", "vent de sud-est"),
    (r"\bSouthwesterly\b", "vent de sud-ouest"),
    (r"\bNortherly\b", "vent de nord"),
    (r"\bSoutherly\b", "vent de sud"),
    (r"\bEasterly\b", "vent d'est"),
    (r"\bWesterly\b", "vent d'ouest"),
    (r"\bNortheast\b", "nord-est"),
    (r"\bNorthwest\b", "nord-ouest"),
    (r"\bSoutheast\b", "sud-est"),
    (r"\bSouthwest\b", "sud-ouest"),
    (r"\bClockwise\b", "rotation horaire"),
    (r"\bCyclonic\b", "cyclonique"),
    (r"\bVariable\b", "variable"),
    (r"\bElsewhere\b", "Ailleurs"),
    (r"\belsewhere\b", "ailleurs"),
    (r"\bsoon\b", "bientôt"),
    (r"\blater\b", "plus tard"),
    (r"\bbecoming\b", "devenant"),
    (r"\bdecreasing\b", "faiblissant"),
    (r"\bincreasing\b", "se renforçant"),
    (r"\bveering\b", "virant à"),
    (r"\bbacking\b", "reculant à"),
    (r"\btemporarily\b", "temporairement"),
    (r"\bovernight\b", "dans la nuit"),
    (r"\beverywhere\b", "partout"),
    (r"\blocally\b", "localement"),
    (r"\bmainly in\b", "surtout au"),
    (r"\bdue to some\b", "par"),
    (r"\bdue to\b", "par"),
    (r"\bPersistence of\b", "Persistance de"),
    (r"\bThreat of\b", "Menace de"),
    (r"\bexpected\b", "prévu"),
    (r"\bIceland\b", "l'Islande"),
    (r"\bWARNING NR\b", "Avis n°"),
    (r"\bover\b", "sur"),
    (r"\bEast\b", "Est"),
    (r"\bwest (?=[A-Z]{3})", "ouest de "),
    (r"\bto (?=[A-Z]{3})", "vers "),
    (r"\bby (?=\d{2}/)", "vers "),
    (r"\bto (?=\d)", "à "),
    (r"\bMorocco\b", "Maroc"),
    (r"\bFrance\b", "France"),
    (r"\bCadiz\b", "Cadix"),
    (r"\bgale\b", "coup de vent"),
    (r"\bLeast\b", "au moins"),
    (r"\bleast\b", "au moins"),
    (r"\bFROM\b", "de"),
    (r"\bHigh\b", "Anticyclone"),
    (r"\bLow\b", "Dépression"),
    (r"\bSlight\b", "Mer peu agitée"),
    (r"\bModerate\b", "Mer agitée"),
    (r"\bRough\b", "Mer forte"),
    (r"\bGusts\b", "Rafales"),
    (r"\bgusts\b", "rafales"),
    (r"\bthen\b", "puis"),
    (r"\bbut\b", "mais"),
    (r"\bwith\b", "avec"),
    (r"\band\b", "et"),
    (r"\bUTC\b", "TU"),
    (r"\bkt\b", "nd"),
]
_FR_PHRASES: list[tuple[re.Pattern[str], str]] = [(re.compile(p, re.I), r) for p, r in _FR_RAW]

_ZONE_LINE = re.compile(r"^([A-Z][A-Z0-9 ,.'/()-]+)\.\s*$")
_ZONE_COLON = re.compile(r"^([A-Z][A-Z0-9 ,.'/()-]+):\s*(.*)$")
_GALE_LINE = re.compile(
    r"^(?:GALE(?:\s+OR\s+NEAR\s+GALE)?\s+WARNINGS?|1\s*:\s*NO WARNING)\s*:?\s*(.*)$",
    re.I,
)
_BULL_POPUP = re.compile(
    r"ouvre_popup\('(https://wwmiws\.wmo\.int/index\.php/metareas/display/bulletin/"
    r"([A-Z0-9]+)_([A-Z0-9]+)/\d+)'\)\"[^>]*>\s*([^<]+)",
    re.I,
)
_GALE_NIL = re.compile(r"^(NONE|NIL|NO WARNING)\.?\s*$", re.I)
_NOT_ZONE = re.compile(
    r"\b(ISSUED|VALID|GALE|SYNOPSIS|WARNING|FORECAST|BEAUFORT|NAVTEX|SECURITE|"
    r"HOURS FCST|STATE MET|GENERAL|PLEASE|WIND SPEED|SEA STATE|COASTAL WATERS)\b",
    re.I,
)
_SEA_WORDS = {
    "RAINS", "RAIN", "GUSTS", "MODERATE", "SLIGHT", "SMOOTH", "ROUGH", "SWELL",
    "SHOWERS", "FOG", "HAZE", "NONE", "NIL", "BT", "POOR", "GOOD", "FAIR",
}
_OFFICIAL_AT = re.compile(
    r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+"
    r"(\d{1,2})\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"(\d{4})\s+at\s+(\d{3,4})\s*UTC",
    re.I,
)
_GTS_HEAD = re.compile(r"^([A-Z]{4}\d{2})\s+([A-Z]{4})\s+(\d{6})\b")

_cache_lock = threading.Lock()
_cache_sets: dict[int, dict[str, Any]] = {}
_cache_pages: dict[str, dict[str, Any]] = {}
_subzones_fc: dict[str, Any] | None = None
_iho_fc: dict[str, Any] | None = None


def load_subzones() -> dict[str, Any]:
    """GeoJSON des sous-zones METAREA II (océan), chargé une fois."""
    global _subzones_fc
    if _subzones_fc is None:
        _subzones_fc = json.loads(SUBZONES_PATH.read_text(encoding="utf-8"))
    return _subzones_fc


def load_iho() -> dict[str, Any]:
    """Limites METAREA OHI (océan), chargées une fois."""
    global _iho_fc
    if _iho_fc is None:
        _iho_fc = json.loads(METAREAS_PATH.read_text(encoding="utf-8"))
    return _iho_fc


def metarea_at(lat: float, lon: float) -> dict[str, Any] | None:
    """METAREA OHI contenant le point, ou None (terre / hors polygone)."""
    for feat in load_iho().get("features") or []:
        if not point_in_feature(lat, lon, feat):
            continue
        p = feat.get("properties") or {}
        try:
            n = int(p.get("n"))
        except (TypeError, ValueError):
            continue
        return {
            "n": n,
            "name": str(p.get("name") or ""),
            "roman": str(p.get("roman") or p.get("name") or ""),
            "coordinator": str(p.get("coordinator") or ""),
        }
    return None


def area_from_n(n: int) -> dict[str, Any]:
    for feat in load_iho().get("features") or []:
        p = feat.get("properties") or {}
        try:
            if int(p.get("n")) != n:
                continue
        except (TypeError, ValueError):
            continue
        return {
            "n": n,
            "name": str(p.get("name") or ""),
            "roman": str(p.get("roman") or p.get("name") or ""),
            "coordinator": str(p.get("coordinator") or ""),
        }
    return {"n": n, "name": str(n), "roman": str(n), "coordinator": ""}


def content_lines(content: Any) -> list[str]:
    """WWMIWS JSON : content est un dict {'1': '...', '2': '...'}."""
    if isinstance(content, dict):
        keys = sorted(content, key=lambda k: int(k) if str(k).isdigit() else 0)
        return [str(content[k]).rstrip() for k in keys]
    if isinstance(content, list):
        return [str(x).rstrip() for x in content]
    if isinstance(content, str):
        return content.splitlines()
    return []


def join_lines(lines: list[str]) -> str:
    out: list[str] = []
    buf = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            if buf:
                out.append(buf)
                buf = ""
            continue
        if buf and not buf.endswith(".") and not _ZONE_LINE.match(line):
            buf = buf + " " + line
        else:
            if buf:
                out.append(buf)
            buf = line
    if buf:
        out.append(buf)
    return "\n".join(out)


def parse_official_at(text: str) -> datetime | None:
    m = _OFFICIAL_AT.search(text)
    if not m:
        return None
    hhmm = m.group(5).zfill(4)
    try:
        return datetime(
            int(m.group(4)),
            list(_MOIS).index(m.group(3).lower()) + 1,
            int(m.group(2)),
            int(hhmm[:2]),
            int(hhmm[2:]),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None


def official_label(dt: datetime | None, fallback: str = "", lang: str = "fr") -> str:
    if dt is None:
        return fallback
    if _ui_lang(lang) == "en":
        days = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
        months = (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        )
        return f"{days[dt.weekday()]} {dt.day} {months[dt.month - 1]} {dt.year} at {dt:%H:%M} UTC"
    jour = _JOURS[dt.strftime("%A").lower()]
    mois = _MOIS[dt.strftime("%B").lower()]
    return f"{jour} {dt.day} {mois} {dt.year} à {dt:%H:%M} TU"


def _fr_clock(raw: str) -> str:
    hhmm = str(raw or "").zfill(4)
    return f"{hhmm[:2]}:{hhmm[2:]}"


def _fr_datetimes(text: str) -> str:
    """Dates anglaises du bulletin (Tuesday 22 September 2026 at 0750 UTC)."""

    def _full(m: re.Match[str]) -> str:
        jour = _JOURS[m.group(1).lower()]
        mois = _MOIS[m.group(3).lower()]
        return f"{jour} {int(m.group(2))} {mois} {m.group(4)} à {_fr_clock(m.group(5))} TU"

    s = _OFFICIAL_AT.sub(_full, text)
    for en, fr in _JOURS.items():
        s = re.sub(rf"\b{en}\b", fr, s, flags=re.I)
    for en, fr in _MOIS.items():
        s = re.sub(rf"\b{en}\b", fr, s, flags=re.I)
    return s


def expand_positions(text: str, *, lang: str = "fr") -> str:
    """52N35W → « 52 Nord, 35 Ouest » (FR) / « 52 North, 35 West » (EN) pour lecture HF."""
    s = str(text or "")
    if not s:
        return ""
    en = str(lang or "").startswith("en")
    ns = {"N": "North", "S": "South"} if en else {"N": "Nord", "S": "Sud"}
    ew = {"E": "East", "W": "West"} if en else {"E": "Est", "W": "Ouest"}
    conj = " to " if en else " à "

    def _num(raw: str) -> str:
        t = str(raw)
        if "." in t:
            return t
        return str(int(t))

    def _full(m: re.Match[str]) -> str:
        return (
            f"{_num(m.group(1))} {ns[m.group(2).upper()]}, "
            f"{_num(m.group(3))} {ew[m.group(4).upper()]}"
        )

    def _lon_range(m: re.Match[str]) -> str:
        return f"{_num(m.group(1))}{conj}{_num(m.group(2))} {ew[m.group(3).upper()]}"

    def _lat(m: re.Match[str]) -> str:
        return f"{_num(m.group(1))} {ns[m.group(2).upper()]}"

    def _lon(m: re.Match[str]) -> str:
        return f"{_num(m.group(1))} {ew[m.group(2).upper()]}"

    # Positions complètes d’abord (sinon 52N de 52N35W partirait seul).
    # Décimales : 30.5N37.0W (le \b lon seul matcherait sinon le « 0W » de « .0W »).
    s = re.sub(
        r"\b(\d{1,2}(?:\.\d+)?)([NnSs])\s*(\d{1,3}(?:\.\d+)?)([EeWw])\b",
        _full,
        s,
    )
    s = re.sub(r"\b(\d{1,2}(?:\.\d+)?)\s*-\s*(\d{1,3}(?:\.\d+)?)([EeWw])\b", _lon_range, s)
    s = re.sub(r"\b(\d{1,2}(?:\.\d+)?)([NnSs])\b(?!\d)", _lat, s)
    s = re.sub(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)([EeWw])\b", _lon, s)
    return s


def fr_marine(text: str) -> str:
    """Anglais EGC → français bulletin, lisible à l’antenne."""
    s = " ".join(str(text or "").split())
    if not s:
        return ""
    # 24/12UTC / 25/ 00UTC → espace avant UTC pour le lexique.
    s = re.sub(r"(\d{1,2}/)\s*(\d{2})\s*UTC\b", r"\1\2 UTC", s, flags=re.I)
    s = _fr_datetimes(s)
    for pat, repl in _FR_PHRASES:
        s = pat.sub(repl, s)
    s = re.sub(r"\s+or\s+", " ou ", s, flags=re.I)
    s = re.sub(r"(?<![/\d])(\d{3,4})\s*TU\b", lambda m: _fr_clock(m.group(1)) + " TU", s)
    s = expand_positions(s, lang="fr")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+([,.;:])", r"\1", s)
    bits = re.split(r"(?<=[.!?])\s+", s)
    s = " ".join((p[0].upper() + p[1:] if p and p[0].islower() else p) for p in bits if p)
    return s


def split_zone_names(header: str) -> list[str]:
    parts = [p.strip() for p in header.replace(".", "").split(",")]
    return [p for p in parts if p]


def parse_forecast(lines: list[str]) -> dict[str, Any]:
    """Découpe FQNT52 : avis, synoptique, zones, tendance."""
    body = [ln.rstrip() for ln in lines]
    gts = body[0] if body else ""
    text = "\n".join(body)
    official = parse_official_at(text)
    parts: dict[str, list[str]] = {"head": [], "warn": [], "syn": [], "areas": [], "out": []}
    bucket = "head"
    for ln in body:
        low = ln.strip().lower()
        if low.startswith("part 1"):
            bucket = "warn"
            parts[bucket].append(ln)
            continue
        if low.startswith("part 2"):
            bucket = "syn"
            parts[bucket].append(ln)
            continue
        if low.startswith("part 3"):
            bucket = "areas"
            continue
        if low.startswith("part 4"):
            bucket = "out"
            parts[bucket].append(ln)
            continue
        parts[bucket].append(ln)

    zones: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for ln in parts["areas"]:
        m = _ZONE_LINE.match(ln.strip())
        if m:
            if cur:
                zones.append(cur)
            names = split_zone_names(m.group(1))
            cur = {"names": names, "header": m.group(1).strip(), "en": []}
            continue
        if cur is not None:
            cur["en"].append(ln)
    if cur:
        zones.append(cur)
    for z in zones:
        z["en_text"] = join_lines(z.pop("en"))
        z["fr"] = fr_marine(z["en_text"])
    syn_lines = parts["syn"][1:] if parts["syn"] else []
    out_lines = parts["out"][1:] if parts["out"] else parts["out"]
    warn_lines = parts["warn"]
    full_lines = [ln.strip() for ln in body[1:] if ln.strip() and ln.strip().upper() not in {"BT", "A", "*="}]
    return {
        "gts": gts,
        "official_at": official,
        "warning_ref": join_lines(warn_lines),
        "synopsis_en": join_lines(syn_lines),
        "synopsis_fr": fr_marine(join_lines(syn_lines)),
        "outlook_en": join_lines(out_lines),
        "outlook_fr": fr_marine(join_lines(out_lines)),
        "full_en": join_lines(full_lines),
        "full_fr": fr_marine(join_lines(full_lines)),
        "zones": zones,
    }


_SKIP_LINE = re.compile(r"^(BT|\*=|A|SECURITE)\s*$", re.I)


def _useful_lines(lines: list[str]) -> list[str]:
    out = []
    for ln in lines:
        s = ln.strip()
        if not s or _SKIP_LINE.match(s):
            continue
        if _GTS_HEAD.match(s):
            continue
        if re.match(r"^SECURITE ON METAREA", s, re.I):
            continue
        out.append(s)
    return out


def parse_warning(lines: list[str]) -> dict[str, Any]:
    gts = lines[0] if lines else ""
    body = _useful_lines(lines[1:] if lines else [])
    kept: list[str] = []
    skip_syn = False
    for s in body:
        if re.match(r"^GENERAL SYNOPSIS", s, re.I):
            skip_syn = True
            continue
        if skip_syn:
            if re.search(r"\b(GIBRALTAR|CADIZ)\b", s, re.I) or (
                _ZONE_LINE.match(s) and _looks_like_zones(_ZONE_LINE.match(s).group(1))
            ):
                skip_syn = False
            else:
                continue
        kept.append(s)
    text = join_lines(kept)
    official = parse_official_at("\n".join(lines))
    return {
        "gts": gts,
        "official_at": official,
        "en": text,
        "fr": fr_marine(text),
    }


def zone_feature_map(fc: dict[str, Any] | None = None) -> dict[str, dict]:
    fc = fc or load_subzones()
    out: dict[str, dict] = {}
    for feat in fc.get("features") or []:
        name = str((feat.get("properties") or {}).get("name") or "").upper()
        if name:
            out[name] = feat
    return out


def zone_at(lat: float, lon: float, fc: dict[str, Any] | None = None) -> str | None:
    """Sous-zone FQNT52 : polygone océan, sinon rectangle officiel (îles, chenal)."""
    feats = zone_feature_map(fc)
    for box in BOXES:
        feat = feats.get(box["name"])
        if feat and point_in_feature(lat, lon, feat):
            return box["name"]
    for box in BOXES:
        if box["s"] <= lat <= box["n"] and box["w"] <= lon <= box["e"]:
            return box["name"]
    return None


def _in_box(lat: float, lon: float, box: dict[str, Any], pad: float = 0.0) -> bool:
    return box["s"] - pad <= lat <= box["n"] + pad and box["w"] - pad <= lon <= box["e"] + pad


def _boxes_touch(a: dict[str, Any], b: dict[str, Any], gap: float = 0.15) -> bool:
    return not (a["e"] < b["w"] - gap or b["e"] < a["w"] - gap or a["n"] < b["s"] - gap or b["n"] < a["s"] - gap)


def neighbor_names(name: str) -> list[str]:
    src = BOX_BY_NAME.get(name)
    if not src:
        return []
    return [b["name"] for b in BOXES if b["name"] != name and _boxes_touch(src, b)]


def near_coastal_names(lat: float, lon: float, occupied: str | None) -> list[str]:
    out = []
    for box in BOXES:
        if box["kind"] != "coastal" or box["name"] == occupied:
            continue
        if _in_box(lat, lon, box, COASTAL_PAD_DEG):
            out.append(box["name"])
    return out


def zone_sets(located: list[dict[str, Any]]) -> tuple[list[str], list[str], list[str]]:
    """Zones occupées, côtières proches, cibles d’avis (occupées + proches + 1 voisin)."""
    fleet: list[str] = []
    seen_f: set[str] = set()
    for b in located:
        z = b.get("zone")
        if z and z not in seen_f:
            seen_f.add(z)
            fleet.append(z)
    extra: list[str] = []
    seen_e: set[str] = set()
    for b in located:
        z = b.get("zone")
        for n in near_coastal_names(float(b["lat"]), float(b["lon"]), z):
            if n not in seen_f and n not in seen_e:
                seen_e.add(n)
                extra.append(n)
    warn: list[str] = list(fleet)
    seen_w = set(fleet)
    for z in fleet + extra:
        if z not in seen_w:
            seen_w.add(z)
            warn.append(z)
        for n in neighbor_names(z):
            if n not in seen_w:
                seen_w.add(n)
                warn.append(n)
    return fleet, extra, warn


def warning_applies(blob: str, names: list[str]) -> bool:
    if _zone_hits_text(names, blob):
        return True
    up = (blob or "").upper()
    if re.search(r"\bGIBRALTAR\b", up) and "GIBRALTAR STRAIT" in names:
        return True
    if re.search(r"EAST OF CADIZ", up) and ("CADIZ" in names or "GIBRALTAR STRAIT" in names):
        return True
    if re.search(r"LEEWARD STRAIT", up) and "GIBRALTAR STRAIT" in names:
        return True
    return False


EXTRA_ZONE_NAMES = {"ALBORAN", "PALOS", "ALGERIA"}


def _looks_like_zones(header: str, *, strict: bool = True) -> bool:
    names = split_zone_names(header)
    if not names:
        return False
    if strict:
        return all(n.upper() in BOX_BY_NAME or n.upper() in EXTRA_ZONE_NAMES for n in names)
    if _NOT_ZONE.search(header):
        return False
    if not re.fullmatch(r"[A-Z0-9 ,.'/()-]+", header.strip()):
        return False
    if all(n.upper() in _SEA_WORDS for n in names):
        return False
    letters = re.sub(r"[^A-Z]", "", header)
    return len(letters) >= 4


def extra_catalog(html: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for url, gts, _cccc, title in _BULL_POPUP.findall(html or ""):
        title = unescape(title).replace("\xa0", " ").strip()
        if "HIGH SEAS" in title.upper():
            continue
        if gts in seen:
            continue
        seen.add(gts)
        out.append({"title": title, "gts": gts, "url": url})
    return out


def parse_display_html(html: str) -> list[str]:
    lines = []
    for raw in re.findall(r"<p>(.*?)</p>", html or "", flags=re.I | re.S):
        text = unescape(re.sub(r"<[^>]+>", "", raw))
        text = " ".join(text.split())
        if text:
            lines.append(text)
    return lines


def parse_coastal(lines: list[str], *, strict: bool = True) -> dict[str, Any]:
    """Découpe un bulletin côtier / NAVTEX (Las Palmas, Tarifa, Corsen, FQZA30…)."""
    gts = lines[0] if lines else ""
    gale_en = ""
    zones: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    syn: list[str] = []
    in_syn = False
    for ln in lines[1:]:
        gm = _GALE_LINE.match(ln.strip())
        if gm:
            gale_en = (gm.group(1) or ln).strip()
            if re.search(r"NO WARNING", ln, re.I):
                gale_en = "NONE"
            continue
        low = ln.strip().lower()
        if "general synopsis" in low or low.startswith("2 : general"):
            in_syn = True
            continue
        if re.match(r"^(24 HOURS FCST|3\s*:\s*FCST)\b", ln.strip(), re.I):
            in_syn = False
            continue
        colon = _ZONE_COLON.match(ln.strip())
        if colon and _looks_like_zones(colon.group(1), strict=strict):
            if cur:
                zones.append(cur)
            names = split_zone_names(colon.group(1))
            cur = {"names": names, "header": colon.group(1).strip(), "en": []}
            rest = (colon.group(2) or "").strip()
            if rest:
                cur["en"].append(rest)
            in_syn = False
            continue
        dotted = _ZONE_LINE.match(ln.strip())
        if dotted and _looks_like_zones(dotted.group(1), strict=strict):
            if cur:
                zones.append(cur)
            names = split_zone_names(dotted.group(1))
            cur = {"names": names, "header": dotted.group(1).strip(), "en": []}
            in_syn = False
            continue
        if cur is not None:
            cur["en"].append(ln)
        elif in_syn:
            syn.append(ln)
    if cur:
        zones.append(cur)
    for z in zones:
        z["en_text"] = re.sub(r"=\s*$", "", join_lines(z.pop("en"))).strip()
        z["fr"] = fr_marine(z["en_text"])
    gale_nil = (not gale_en) or bool(_GALE_NIL.match(gale_en))
    return {
        "gts": gts,
        "gale_en": gale_en,
        "gale_fr": "" if gale_nil else fr_marine(gale_en),
        "gale_nil": gale_nil,
        "synopsis_fr": fr_marine(join_lines(syn)),
        "zones": zones,
        "official_at": parse_official_at("\n".join(lines)),
    }


def locate_boats(boats: list[dict[str, Any]], fc: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    located = []
    for b in boats or []:
        try:
            lat = float(b["lat"])
            lon = float(b["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        area = metarea_at(lat, lon)
        located.append(
            {
                "name": b.get("name") or "—",
                "sail": b.get("sail") or "",
                "lat": lat,
                "lon": lon,
                "fmt": fmt_latlon(lat, lon),
                "zone": zone_at(lat, lon, fc),
                "metarea": (area or {}).get("roman") or None,
                "metarea_n": (area or {}).get("n"),
                "metarea_name": (area or {}).get("name"),
                "coordinator": (area or {}).get("coordinator") or "",
            }
        )
    return located


def _zone_hits_text(names: list[str], blob: str) -> bool:
    up = blob.upper()
    return any(re.search(r"\b" + re.escape(n.upper()) + r"\b", up) for n in names if n)


def _pick_zone_blocks(blocks: list[dict[str, Any]], wanted: set[str], *, has_grid: bool) -> list[dict[str, Any]]:
    picked = []
    for block in blocks or []:
        names = [n.upper() for n in block.get("names") or []]
        if has_grid:
            if any(n in wanted for n in names):
                picked.append({**block, "for_fleet": True})
        else:
            picked.append({**block, "for_fleet": True})
    return picked


def _ui_lang(lang: str | None) -> str:
    return "en" if str(lang or "").lower().startswith("en") else "fr"


def disclaimer_for(lang: str | None) -> str:
    return DISCLAIMER_EN if _ui_lang(lang) == "en" else DISCLAIMER


def _block_line(block: dict[str, Any], lang: str = "fr") -> str:
    titre = block.get("header") or ", ".join(block.get("names") or [])
    ligne = f"{titre}."
    if _ui_lang(lang) == "en":
        txt = block.get("en_text") or block.get("fr") or ""
    else:
        txt = block.get("fr") or ""
    if txt:
        ligne += " " + txt
    return ligne


def _norm_blob(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip(" =.")


def build_digest(
    forecast: dict[str, Any],
    warnings: list[dict[str, Any]] | None,
    located: list[dict[str, Any]],
    *,
    roman: str = "II",
    coordinator: str = "Météo-France Toulouse",
    has_grid: bool = True,
    coastal: list[dict[str, Any]] | None = None,
    lang: str = "fr",
) -> dict[str, Any]:
    en = _ui_lang(lang) == "en"
    here = [b for b in located if b.get("metarea") == roman]
    if not here:
        here = located
    fleet_zones, extra_zones, warn_names = zone_sets(here)
    unknown = [b for b in here if not b.get("zone")]
    wanted = set(z.upper() for z in fleet_zones + extra_zones)

    picked = _pick_zone_blocks(forecast.get("zones") or [], wanted, has_grid=has_grid)

    applied: list[dict[str, Any]] = []
    skipped_warn = False
    for warn in warnings or []:
        blob = (warn.get("en") or "") + " " + (forecast.get("warning_ref") or "")
        if (not has_grid) or warning_applies(blob, warn_names):
            applied.append(warn)
        elif warn.get("fr") or warn.get("en"):
            skipped_warn = True
    out_for_fleet = (not has_grid) or _zone_hits_text(fleet_zones + extra_zones, forecast.get("outlook_en") or "")

    lecture: list[str] = []
    off = official_label(forecast.get("official_at"), lang=lang)
    who = coordinator or ("maritime weather service" if en else "service météo maritime")
    if en:
        lecture.append(f"High seas forecast METAREA {roman}, {who}" + (f", {off}." if off else "."))
    else:
        lecture.append(f"Bulletin haute mer METAREA {roman}, {who}" + (f", {off}." if off else "."))
    if not has_grid:
        for warn in applied:
            body = (warn.get("en") if en else warn.get("fr")) or warn.get("en") or warn.get("fr") or ""
            if body:
                lecture.append(("Warning. " if en else "Avis. ") + body)
        full = (
            (forecast.get("full_en") or forecast.get("synopsis_en") or "")
            if en
            else (forecast.get("full_fr") or forecast.get("synopsis_fr") or "")
        )
        if full:
            lecture.append(full)
        else:
            lecture.append(
                "Official bulletin retrieved, text not usable."
                if en
                else "Bulletin officiel récupéré, texte non exploitable."
            )
    else:
        for warn in applied:
            body = (warn.get("en") if en else warn.get("fr")) or warn.get("en") or warn.get("fr") or ""
            if body:
                lecture.append(("Warning. " if en else "Avis. ") + body)
        if skipped_warn:
            lecture.append(
                "A high-seas warning is in force outside the fleet sub-zones. "
                "Check it on the official warnings bulletin."
                if en
                else "Un avis haute mer est en vigueur hors des sous-zones de la flotte. "
                "Le vérifier sur le bulletin officiel des avis."
            )
        syn = (forecast.get("synopsis_en") if en else forecast.get("synopsis_fr")) or ""
        if syn:
            lecture.append(("General situation. " if en else "Situation générale. ") + syn)
        if not picked:
            lecture.append(
                "No zone forecast for the current position."
                if en
                else "Pas de prévision de zone pour la position actuelle."
            )
        else:
            for block in picked:
                lecture.append(_block_line(block, lang))
        outlook = (forecast.get("outlook_en") if en else forecast.get("outlook_fr")) or ""
        if out_for_fleet and outlook:
            lecture.append(("Outlook. " if en else "Tendance. ") + outlook)

    coastal_used: list[dict[str, Any]] = []
    seen_coastal: set[str] = set()
    seen_txt: set[str] = set()
    for block in picked:
        blob = _norm_blob(block.get("en_text") or block.get("fr") or "")
        if blob:
            seen_txt.add(blob)
    if not has_grid:
        blob = _norm_blob(forecast.get("full_en") or forecast.get("full_fr") or "")
        if blob:
            seen_txt.add(blob)
    ordered = sorted(coastal or [], key=coastal_sort_key)
    filter_zones = bool(wanted)
    for item in ordered:
        czones = []
        for block in _pick_zone_blocks(item.get("zones") or [], wanted, has_grid=filter_zones):
            names = [n.upper() for n in block.get("names") or [] if n]
            if any(n in seen_coastal for n in names):
                continue
            blob = _norm_blob(block.get("en_text") or block.get("fr") or "")
            if blob and blob in seen_txt:
                continue
            czones.append(block)
            seen_coastal.update(names)
            if blob:
                seen_txt.add(blob)
        has_gale = bool(item.get("gale_fr"))
        if not czones and not has_gale:
            continue
        coastal_used.append(item)
        titre = item.get("title") or item.get("gts") or ("coastal" if en else "côtier")
        gts = item.get("gts") or ""
        if en:
            lecture.append(f"Coastal forecast {titre}" + (f" ({gts})" if gts else "") + ".")
        else:
            lecture.append(f"Bulletin côtier {titre}" + (f" ({gts})" if gts else "") + ".")
        if has_gale:
            gale_txt = (item.get("gale_en") if en else item.get("gale_fr")) or item.get("gale_fr") or ""
            if gale_txt:
                lecture.append(("Gale warning. " if en else "Avis de coup de vent. ") + gale_txt)
        for block in czones:
            lecture.append(_block_line(block, lang))

    # EN : positions pour la voix (FR déjà développé dans fr_marine).
    if en:
        lecture = [expand_positions(p, lang="en") for p in lecture]

    return {
        "fleet_zones": fleet_zones,
        "extra_zones": extra_zones,
        "unknown": unknown,
        "blocks": picked,
        "warning_for_fleet": bool(applied),
        "warning_outside": skipped_warn,
        "outlook_for_fleet": out_for_fleet,
        "lecture": " ".join(lecture),
        "paragraphs": lecture,
        "coastal": coastal_used,
    }


def fleet_geojson(fleet_zones: list[str], fc: dict[str, Any] | None = None) -> dict[str, Any]:
    feats = zone_feature_map(fc)
    out = []
    for name in fleet_zones:
        feat = feats.get(name)
        if not feat:
            continue
        props = dict(feat.get("properties") or {})
        props["layer"] = "subzone"
        props["active"] = True
        out.append({"type": "Feature", "properties": props, "geometry": feat.get("geometry")})
    return {
        "type": "FeatureCollection",
        "name": "METAREA II sous-zones flotte",
        "features": out,
    }


def _bulletin_by_label(raw: dict[str, Any], pred) -> dict[str, Any] | None:
    for item in raw.get("bulletin") or []:
        label = str(item.get("label") or "").upper()
        if pred(label):
            return item
    return None


def _bulletins_by_label(raw: dict[str, Any], pred) -> list[dict[str, Any]]:
    out = []
    for item in raw.get("bulletin") or []:
        label = str(item.get("label") or "").upper()
        if pred(label):
            out.append(item)
    return out


def _permalinks(html: str, n: int) -> dict[str, str]:
    links = wwmiws_urls(n)
    m = re.search(r"display/bulletin/(FQ[A-Z0-9]+_[A-Z0-9]+/\d+)", html)
    if m:
        links["forecast"] = f"{WWMIWS}/display/bulletin/" + m.group(1)
    m = re.search(r"display/bulletin/(WO[A-Z0-9]+_[A-Z0-9]+/\d+)", html)
    if m:
        links["warning"] = f"{WWMIWS}/display/bulletin/" + m.group(1)
    return links


def assemble(
    raw: dict[str, Any],
    boats: list[dict[str, Any]],
    links: dict[str, str] | None = None,
    area: dict[str, Any] | None = None,
    coastal: list[dict[str, Any]] | None = None,
    lang: str = "fr",
) -> dict[str, Any]:
    fc = load_subzones()
    located = locate_boats(boats, fc)
    if area is None:
        ns = [b.get("metarea_n") for b in located if b.get("metarea_n")]
        area = area_from_n(int(ns[0])) if ns else {"n": 2, "name": "II", "roman": "II", "coordinator": "France"}
    roman = str(area.get("roman") or area.get("name") or "II")
    name = str(area.get("name") or roman)
    coordinator = ISSUERS.get(name) or ISSUERS.get(roman) or str(area.get("coordinator") or "")
    n = int(area.get("n") or 2)
    has_grid = name in GRIDS or roman in GRIDS
    here = [b for b in located if b.get("metarea_n") == n]
    fcst_item = _bulletin_by_label(raw, lambda l: "HIGH SEAS FORECAST" in l) or _bulletin_by_label(
        raw, lambda l: "FORECAST" in l or l.startswith("FQ")
    )
    warn_items = _bulletins_by_label(raw, lambda l: "WARNING" in l)
    forecast = parse_forecast(content_lines((fcst_item or {}).get("content")))
    warnings = [parse_warning(content_lines(it.get("content"))) for it in warn_items]
    digest = build_digest(
        forecast,
        warnings,
        here or located,
        roman=roman,
        coordinator=coordinator,
        has_grid=has_grid,
        coastal=coastal,
        lang=lang,
    )
    warning = warnings[0] if warnings else {}
    official = forecast.get("official_at") or (warning or {}).get("official_at")
    wwmiws_date = str(raw.get("date") or "")
    fetched = _parse_wwmiws_date(wwmiws_date)
    gts = forecast.get("gts") or ""
    product = gts.split()[0] + (" " + gts.split()[1] if len(gts.split()) > 1 else "") if gts else ""
    overlay_zones = list(digest["fleet_zones"]) + list(digest.get("extra_zones") or [])
    out_links = dict(links or wwmiws_urls(n))
    for item in digest.get("coastal") or []:
        if item.get("url") and item.get("gts"):
            out_links[str(item["gts"]).lower()] = item["url"]
    return {
        "ok": True,
        "metarea": roman,
        "metarea_n": n,
        "coordinator": coordinator,
        "product": product,
        "has_subzones": has_grid,
        "disclaimer": disclaimer_for(lang),
        "official_at": official.isoformat() if isinstance(official, datetime) else None,
        "official_label": official_label(official if isinstance(official, datetime) else None, lang=lang),
        "schedule_utc": list(SCHEDULE.get(name) or SCHEDULE.get(roman) or ()),
        "gts": gts,
        "wwmiws_date": wwmiws_date,
        "retrieved_at": fetched.isoformat() if fetched else None,
        "retrieved_label": official_label(fetched, lang=lang) if fetched else wwmiws_date,
        "links": out_links,
        "boats": located,
        "fleet_zones": digest["fleet_zones"],
        "extra_zones": digest.get("extra_zones") or [],
        "geojson": fleet_geojson(overlay_zones, fc) if has_grid else {"type": "FeatureCollection", "features": []},
        "warning": {
            "gts": warning.get("gts") or "",
            "fr": warning.get("fr") or "",
            "for_fleet": digest["warning_for_fleet"],
            "outside": digest.get("warning_outside") or False,
        },
        "synopsis_fr": forecast.get("synopsis_fr") or "",
        "outlook_fr": forecast.get("outlook_fr") or "",
        "outlook_for_fleet": digest["outlook_for_fleet"],
        "blocks": [
            {
                "names": b.get("names"),
                "header": b.get("header"),
                "fr": b.get("fr"),
            }
            for b in digest["blocks"]
        ],
        "coastal": [
            {"title": c.get("title"), "gts": c.get("gts"), "url": c.get("url")}
            for c in digest.get("coastal") or []
        ],
        "paragraphs": digest["paragraphs"],
        "lecture": digest["lecture"],
    }


def _parse_wwmiws_date(value: str) -> datetime | None:
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


async def fetch_raw(n: int = 2, client: httpx.AsyncClient | None = None) -> tuple[dict[str, Any], dict[str, str], list[dict[str, str]]]:
    urls = wwmiws_urls(n)
    headers = {"User-Agent": UA, "Accept": "application/json, text/html"}
    own = client is None
    client = client or httpx.AsyncClient(timeout=25.0, headers=headers, follow_redirects=True)
    catalog: list[dict[str, str]] = []
    try:
        js = await client.get(urls["json"])
        js.raise_for_status()
        raw = js.json()
        links = dict(urls)
        try:
            page = await client.get(urls["metarea"])
            if page.is_success:
                links = _permalinks(page.text, n)
                catalog = extra_catalog(page.text)
        except httpx.HTTPError:
            log.warning("WWMIWS page METAREA %s indisponible", n)
        return raw, links, catalog
    finally:
        if own:
            await client.aclose()


async def fetch_display_lines(url: str, force: bool = False) -> list[str]:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache_pages.get(url)
        if not force and hit and now - hit["at"] < CACHE_TTL_S:
            return list(hit.get("lines") or [])
    headers = {"User-Agent": UA, "Accept": "text/html"}
    async with httpx.AsyncClient(timeout=25.0, headers=headers, follow_redirects=True) as client:
        page = await client.get(url)
        page.raise_for_status()
        lines = parse_display_html(page.text)
    with _cache_lock:
        _cache_pages[url] = {"at": time.monotonic(), "lines": lines}
    return lines


def coastal_sort_key(item: dict[str, Any]) -> tuple[int, str]:
    gts = str(item.get("gts") or "")
    title = str(item.get("title") or "").upper()
    rank = COASTAL_RANK.get(gts, 8 if title.startswith("COASTAL") else 9)
    return (rank, gts)


def needed_coastal(catalog: list[dict[str, str]], zone_names: list[str]) -> list[dict[str, str]]:
    """II : seulement les NAVTEX dont la zone de service recouvre la flotte.
    Ailleurs : tout extra WWMIWS (côtier), le dédup fera le ménage."""
    wanted = {z.upper() for z in zone_names if z}
    out = []
    for item in catalog or []:
        gts = item.get("gts") or ""
        cover = {z.upper() for z in COASTAL_FOR.get(gts, [])}
        if cover:
            if wanted and not (cover & wanted):
                continue
            out.append(item)
            continue
        out.append(item)
    out.sort(key=coastal_sort_key)
    return out


async def load_coastal(
    catalog: list[dict[str, str]],
    zone_names: list[str],
    keep_names: list[str],
    force: bool = False,
) -> list[dict[str, Any]]:
    extras: list[dict[str, Any]] = []
    keep = {z.upper() for z in keep_names if z}
    for item in needed_coastal(catalog, zone_names):
        try:
            lines = await fetch_display_lines(item["url"], force=force)
        except Exception:
            log.exception("WWMIWS côtier %s", item.get("gts"))
            continue
        parsed = parse_coastal(lines, strict=(item.get("gts") or "") in COASTAL_FOR)
        parsed["title"] = item.get("title") or item.get("gts")
        parsed["gts"] = item.get("gts") or parsed.get("gts")
        parsed["url"] = item.get("url")
        if keep:
            parsed["zones"] = [
                z for z in parsed.get("zones") or [] if any(n.upper() in keep for n in (z.get("names") or []))
            ]
        if not parsed.get("zones") and not parsed.get("gale_fr"):
            continue
        extras.append(parsed)
    return extras


async def _cached_set(n: int, force: bool = False) -> dict[str, Any]:
    now = time.monotonic()
    hit: dict[str, Any] | None = None
    with _cache_lock:
        hit = _cache_sets.get(n)
        if not force and hit and now - hit["at"] < CACHE_TTL_S:
            return {**hit, "stale": True}
    try:
        raw, links, catalog = await fetch_raw(n)
    except Exception:
        log.exception("WWMIWS METAREA %s", n)
        if hit:
            return {**hit, "stale": True}
        return {"raw": None, "links": wwmiws_urls(n), "catalog": [], "stale": False}
    pack = {"at": time.monotonic(), "raw": raw, "links": links, "catalog": catalog}
    with _cache_lock:
        _cache_sets[n] = pack
    return {**pack, "stale": False}


def _merge_parts(parts: list[dict[str, Any]], located: list[dict[str, Any]], lang: str = "fr") -> dict[str, Any]:
    en = _ui_lang(lang) == "en"
    if not parts:
        urls = wwmiws_urls(2)
        miss = "WWMIWS bulletin unavailable." if en else "Bulletin WWMIWS indisponible."
        empty = "High seas bulletin unavailable for now." if en else "Bulletin haute mer indisponible pour le moment."
        return {
            "ok": False,
            "error": miss,
            "metarea": "—",
            "metareas": [],
            "links": urls,
            "geojson": {"type": "FeatureCollection", "features": []},
            "disclaimer": disclaimer_for(lang),
            "paragraphs": [empty],
            "lecture": empty,
            "fleet_zones": [],
            "boats": located,
        }
    if len(parts) == 1:
        body = dict(parts[0])
        body["boats"] = located
        body["metareas"] = [body.get("metarea")]
        return body
    paragraphs: list[str] = []
    feats: list[dict] = []
    zones: list[str] = []
    romans: list[str] = []
    for p in parts:
        romans.append(str(p.get("metarea") or ""))
        paragraphs.extend(p.get("paragraphs") or [])
        feats.extend((p.get("geojson") or {}).get("features") or [])
        zones.extend(p.get("fleet_zones") or [])
    first = dict(parts[0])
    first.update(
        {
            "ok": all(p.get("ok") for p in parts),
            "metarea": ", ".join(r for r in romans if r),
            "metareas": romans,
            "areas": parts,
            "boats": located,
            "fleet_zones": zones,
            "paragraphs": paragraphs,
            "lecture": " ".join(p.get("lecture") or "" for p in parts),
            "geojson": {"type": "FeatureCollection", "features": feats},
        }
    )
    return first


async def snapshot(boats: list[dict[str, Any]], force: bool = False, lang: str = "fr") -> dict[str, Any]:
    """Bulletins WWMIWS des METAREA occupées par la flotte, cache 20 min."""
    located = locate_boats(boats)
    nums = sorted({int(b["metarea_n"]) for b in located if b.get("metarea_n")})
    if not nums:
        empty = (
            "No boat in a known maritime METAREA."
            if _ui_lang(lang) == "en"
            else "Aucun bateau en METAREA maritime connue."
        )
        return {
            "ok": True,
            "metarea": "—",
            "metareas": [],
            "links": wwmiws_urls(2),
            "geojson": {"type": "FeatureCollection", "features": []},
            "disclaimer": disclaimer_for(lang),
            "paragraphs": [empty],
            "lecture": empty,
            "fleet_zones": [],
            "boats": located,
        }
    parts: list[dict[str, Any]] = []
    for n in nums:
        pack = await _cached_set(n, force=force)
        raw = pack.get("raw")
        links = pack.get("links")
        catalog = pack.get("catalog") or []
        stale = pack.get("stale")
        if not raw:
            continue
        by_n = [b for b in located if b.get("metarea_n") == n]
        romans = []
        for b in by_n:
            r = b.get("metarea")
            if r and r not in romans:
                romans.append(r)
        info = area_from_n(n)
        area = {
            "n": n,
            "name": (by_n[0].get("metarea_name") if by_n else None) or info["name"],
            "roman": ", ".join(romans) if romans else info["roman"],
            "coordinator": (by_n[0].get("coordinator") if by_n else None) or info["coordinator"],
        }
        fleet_z, extra_z, _warn_z = zone_sets(by_n or located)
        coastal = await load_coastal(catalog, fleet_z + extra_z, fleet_z + extra_z, force=force)
        body = assemble(raw, boats, links, area, coastal=coastal, lang=lang)
        if stale:
            body["stale"] = True
        parts.append(body)
    return _merge_parts(parts, located, lang=lang)
