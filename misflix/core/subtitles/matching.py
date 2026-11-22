"""Decidir que candidatos de un repo de subtitulos corresponden a un video local, y en que orden probarlos.

La busqueda de subdivx es por substring: "house s01e01" trae *House M.D.*,
pero tambien *House of Cards*, *Bleak House*, *Desperate Housewives*... y el
campo IMDb casi nunca viene en series (si en peliculas). Por eso el filtro es
por titulo normalizado + codigo de episodio, y el orden por cuanto se parece
la version para la que se hizo el subtitulo (descripcion del uploader) al
archivo local: el mismo grupo de release casi garantiza la misma sincronia
(verificado en vivo: el subtitulo "para la version bluray.x264-filmhd" de
House S01E01 ya venia perfecto para `house.s01e01.1080p.bluray.x264-filmhd`).
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from misflix.core.models import SubtitleCandidate

_EPISODE_RE = re.compile(
    r"\bs(?P<season>\d{1,2})\s*e(?P<first>\d{1,3})(?:\s*(?:-|al|a)\s*(?:s\d{1,2})?\s*e?(?P<last>\d{1,3}))?\b"
    r"|\b(?P<season_x>\d{1,2})x(?P<first_x>\d{1,3})(?:\s*-\s*(?:\d{1,2}x)?(?P<last_x>\d{1,3}))?\b",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
# Lo que puede acompañar al nombre de la serie en el titulo de subdivx sin
# que sea otra serie: "House M.D.", "House MD", "The Office (US)", "Skins (UK)".
_ALLOWED_TITLE_EXTRAS = {"md", "m", "d", "us", "uk", "au", "the", "tv", "series", "serie"}
_FORCED_RE = re.compile(r"\b(forzad[oa]s?|forced)\b", re.IGNORECASE)
# "Tôkyô goddofâzâzu (2003) aka Tokyo Godfathers": el titulo original
# romanizado y despues el conocido (verificado en vivo en subdivx).
_ALTERNATE_TITLES_RE = re.compile(r"\s+(?:aka|a\.k\.a\.?)\s+|\s+/\s+", re.IGNORECASE)
# Duraciones que los uploaders ponen en la descripcion ("1:31:46", "92 min")
# para distinguir cortes (ej. la version Remastered dura un minuto mas).
_HMS_RE = re.compile(r"\b(\d{1,2}):([0-5]\d):([0-5]\d)\b")
_MINUTES_RE = re.compile(r"\b(\d{2,3})\s*min(?:utos|s)?\b", re.IGNORECASE)

_SOURCES = {
    "bluray": ("bluray", "blu ray", "bdrip", "brrip", "bd", "remux", "bdremux"),
    "webdl": ("web dl", "webdl", "web"),
    "webrip": ("webrip", "web rip"),
    "hdtv": ("hdtv", "pdtv", "hdtvrip"),
    "dvd": ("dvdrip", "dvd", "dvdscr"),
}
_RESOLUTIONS = ("2160p", "1080p", "720p", "576p", "480p")
_SERVICES = ("amzn", "nf", "dsnp", "hmax", "max", "atvp", "hulu", "pcok", "pmtp", "crav")
_EDITIONS = ("extended", "directors cut", "unrated", "remastered", "theatrical", "imax")
_NOT_A_GROUP = {"x264", "x265", "h264", "h265", "hevc", "avc", "aac", "dts", "ac3", "dl", "rip"}


@dataclass(frozen=True)
class EpisodeRange:
    """Temporada y rango de episodios que cubre un subtitulo o archivo.

    Attributes:
        season: Numero de temporada.
        first: Primer episodio (inclusive).
        last: Ultimo episodio (inclusive); igual a `first` si es uno solo.
    """

    season: int
    first: int
    last: int

    def covers(self, season: int, episode: int) -> bool:
        """Indica si el rango incluye `season`x`episode`.

        Args:
            season: Temporada buscada.
            episode: Episodio buscado.

        Returns:
            True si esta dentro del rango.
        """
        return self.season == season and self.first <= episode <= self.last


@dataclass(frozen=True)
class ReleaseProfile:
    """Rasgos de version de un video, extraidos de su nombre de archivo/release.

    Attributes:
        group: Grupo de release (ej. "filmhd"), si se pudo deducir.
        source: Origen normalizado ("bluray", "webdl", "webrip", "hdtv", "dvd").
        resolution: Resolucion ("1080p", ...).
        service: Servicio de streaming de origen ("amzn", "nf", ...).
        editions: Ediciones especiales mencionadas ("extended", ...).
    """

    group: str | None
    source: str | None
    resolution: str | None
    service: str | None
    editions: frozenset[str]


def normalize(text: str) -> str:
    """Minusculas, sin acentos ni puntuacion, espacios simples ("Dr. House" -> "dr house").

    Args:
        text: Texto a normalizar.

    Returns:
        El texto normalizado.
    """
    decomposed = unicodedata.normalize("NFKD", text.casefold().replace("&", " and "))
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", without_accents).split())


def parse_episode_range(text: str) -> EpisodeRange | None:
    """Encuentra un codigo de episodio (o rango) en un titulo o nombre de archivo.

    Acepta `S01E01`, `S01E01-11`, `S01E01-E11`, `1x01` y `1x01-1x05`.

    Args:
        text: Titulo o nombre de archivo.

    Returns:
        El rango encontrado, o None si no hay codigo de episodio.
    """
    match = _EPISODE_RE.search(text)
    if match is None:
        return None
    if match["season"] is not None:
        season, first, last = match["season"], match["first"], match["last"]
    else:
        season, first, last = match["season_x"], match["first_x"], match["last_x"]
    first_number = int(first)
    last_number = int(last) if last is not None else first_number
    return EpisodeRange(season=int(season), first=first_number, last=max(first_number, last_number))


def title_matches(candidate_title: str, wanted_titles: Iterable[str]) -> bool:
    """Indica si el titulo de un candidato (sin codigo de episodio ni año) es el de la serie/pelicula.

    Exige que el titulo del candidato *empiece* con uno de los buscados y que
    lo que sobre sea solo un año o algo de `_ALLOWED_TITLE_EXTRAS` — asi
    "House M.D." pasa para "House" pero "House of Cards" no. Los titulos
    alternativos ("X aka Y", "X / Y") se prueban cada uno por separado.

    Args:
        candidate_title: Titulo tal como lo cargo el uploader.
        wanted_titles: Titulos aceptables (original, traducido, nombre de carpeta).

    Returns:
        True si corresponde a alguno de los titulos buscados.
    """
    variants = [normalize(part) for part in _ALTERNATE_TITLES_RE.split(_strip_episode_code(candidate_title))]
    for wanted in {normalize(title) for title in wanted_titles if title.strip()}:
        for base in variants:
            if not wanted or not (base == wanted or base.startswith(wanted + " ")):
                continue
            extras = base[len(wanted) :].split()
            if all(extra in _ALLOWED_TITLE_EXTRAS or _YEAR_RE.fullmatch(extra) for extra in extras):
                return True
    return False


def matches_episode(
    candidate: SubtitleCandidate, wanted_titles: Iterable[str], season: int, episode: int
) -> EpisodeRange | None:
    """Indica si `candidate` es un subtitulo (o pack) que incluye ese episodio.

    Args:
        candidate: Candidato del repo.
        wanted_titles: Titulos aceptables de la serie.
        season: Temporada del video local.
        episode: Episodio del video local.

    Returns:
        El rango que cubre el candidato si incluye el episodio, si no None.
    """
    if _FORCED_RE.search(candidate.title) or _FORCED_RE.search(candidate.description):
        return None
    episodes = parse_episode_range(candidate.title)
    if episodes is None or not episodes.covers(season, episode):
        return None
    return episodes if title_matches(candidate.title, wanted_titles) else None


def matches_movie(
    candidate: SubtitleCandidate, wanted_titles: Iterable[str], year: int | None, imdb_id: str | None
) -> bool:
    """Indica si `candidate` es un subtitulo de esa pelicula.

    Si ambos lados tienen id de IMDb, manda el id (subdivx lo trae en casi
    todas las peliculas). Si no, titulo + año (±1: el año de estreno varia
    segun el pais).

    Args:
        candidate: Candidato del repo.
        wanted_titles: Titulos aceptables de la pelicula.
        year: Año de la pelicula local, si se conoce.
        imdb_id: Id de IMDb local, si se conoce.

    Returns:
        True si corresponde a la pelicula.
    """
    if _FORCED_RE.search(candidate.title) or _FORCED_RE.search(candidate.description):
        return False
    if parse_episode_range(candidate.title) is not None:
        return False
    if imdb_id and candidate.imdb_id:
        return candidate.imdb_id == imdb_id
    if not title_matches(candidate.title, wanted_titles):
        return False
    years = [int(match.group(0)) for match in _YEAR_RE.finditer(candidate.title)]
    return year is None or not years or any(abs(found - year) <= 1 for found in years)


def release_profile(*names: str) -> ReleaseProfile:
    """Extrae los rasgos de version de uno o mas nombres (archivo, carpeta, tag de titulo).

    Args:
        *names: Nombres donde buscar (el primero con un grupo de release gana).

    Returns:
        El perfil de version.
    """
    text = " ".join(normalize(name) for name in names)
    return ReleaseProfile(
        group=next((group for name in names if (group := _release_group(name))), None),
        source=_find_source(text),
        resolution=next((r for r in _RESOLUTIONS if r in text.split()), None),
        service=next((s for s in _SERVICES if f" {s} " in f" {text} "), None),
        editions=frozenset(e for e in _EDITIONS if f" {e} " in f" {text} "),
    )


def rank_candidates(
    candidates: Sequence[SubtitleCandidate], local: ReleaseProfile, duration: float | None = None
) -> list[SubtitleCandidate]:
    """Ordena candidatos del mas al menos prometedor para el video local.

    Args:
        candidates: Candidatos ya filtrados (ver `matches_episode`/`matches_movie`).
        local: Perfil de version del video local.
        duration: Duracion del video local en segundos, si se conoce.

    Returns:
        Los candidatos ordenados por `similarity` (desempatando por descargas).
    """
    return sorted(candidates, key=lambda candidate: similarity(candidate, local, duration), reverse=True)


def similarity(candidate: SubtitleCandidate, local: ReleaseProfile, duration: float | None = None) -> float:
    """Puntaje de que tan probable es que `candidate` venga sincronizado para el video local.

    Pesa lo que en la practica determina la sincronia: el mismo grupo de
    release (mismo encode) es casi una garantia; la misma fuente (BluRay vs
    HDTV con cortes comerciales) importa bastante; resolucion y servicio,
    menos. Las descargas suman poco (logaritmico) para desempatar: un
    subtitulo muy bajado suele estar bien hecho, pero no para *esta* version.

    Si la descripcion dice cuanto dura el video para el que se hizo, eso
    pesa mucho: distingue cortes (version extendida, remasterizada) mejor que
    cualquier nombre de release.

    Args:
        candidate: Candidato a puntuar.
        local: Perfil de version del video local.
        duration: Duracion del video local en segundos, si se conoce.

    Returns:
        Puntaje (mas alto es mejor).
    """
    text = f" {normalize(candidate.title + ' ' + candidate.description)} "
    score = 0.0
    if local.group and f" {normalize(local.group)} " in text:
        score += 6.0
    candidate_source = _find_source(text)
    if local.source and candidate_source == local.source:
        score += 3.0
    elif {local.source, candidate_source} == {"webdl", "webrip"}:
        score += 1.5
    if local.resolution and f" {local.resolution} " in text:
        score += 1.0
    if local.service and f" {local.service} " in text:
        score += 1.5
    score += sum(1.0 for edition in local.editions if f" {edition} " in text)
    score += _duration_score(candidate.description, duration)
    return score + 0.5 * math.log10(candidate.downloads + 1)


def pick_subtitle_files(names: Sequence[str], season: int | None, episode: int | None) -> list[str]:
    """Elige, de los archivos de un zip/rar, los .srt que corresponden al video.

    Para una pelicula (sin `season`/`episode`) devuelve todos los .srt
    (normalmente uno; con varios CDs, cada uno se prueba aparte). Para un
    episodio, los que tienen ese codigo en el nombre — o, si el archivo trae
    un unico .srt sin codigo, ese.

    Args:
        names: Nombres (rutas relativas) de los archivos extraidos.
        season: Temporada buscada, o None para peliculas.
        episode: Episodio buscado, o None para peliculas.

    Returns:
        Los nombres elegidos, en el orden recibido.
    """
    subtitles = [name for name in names if name.lower().endswith(".srt")]
    if season is None or episode is None:
        return subtitles
    matching = [
        name
        for name in subtitles
        if (found := parse_episode_range(name.rsplit("/", 1)[-1])) is not None and found.covers(season, episode)
    ]
    if matching:
        return matching
    unnumbered = [name for name in subtitles if parse_episode_range(name.rsplit("/", 1)[-1]) is None]
    return unnumbered if len(subtitles) == 1 else []


def mentioned_durations(text: str) -> list[float]:
    """Duraciones (en segundos) mencionadas en una descripcion: "1:31:46", "92 min".

    Args:
        text: Descripcion del uploader.

    Returns:
        Las duraciones encontradas.
    """
    found = [int(h) * 3600 + int(m) * 60 + int(s) for h, m, s in _HMS_RE.findall(text)]
    found += [int(minutes) * 60 for minutes in _MINUTES_RE.findall(text)]
    return [float(value) for value in found if 60 <= value <= 6 * 3600]


def _duration_score(description: str, duration: float | None) -> float:
    if not duration:
        return 0.0
    mentioned = mentioned_durations(description)
    if not mentioned:
        return 0.0
    closest = min(abs(value - duration) for value in mentioned)
    # la misma duracion es el mismo corte: pesa tanto como el mismo grupo de release
    if closest <= 20:
        return 6.0
    if closest <= 90:
        return 1.0
    return -3.0 if closest > 180 else 0.0


def _strip_episode_code(title: str) -> str:
    match = _EPISODE_RE.search(title)
    return title[: match.start()] if match else title


def _find_source(normalized_text: str) -> str | None:
    padded = f" {normalized_text} "
    # webrip antes que webdl: "web" solo es un prefijo de ambos
    for source in ("webrip", "webdl", "bluray", "hdtv", "dvd"):
        if any(f" {alias} " in padded for alias in _SOURCES[source]):
            return source
    return None


def _release_group(name: str) -> str | None:
    stem = re.sub(r"\.(mkv|mp4|avi|m4v|srt)$", "", name.strip(), flags=re.IGNORECASE)
    match = re.search(r"-\s*\[?([A-Za-z0-9]+)\]?\s*$", stem)
    if match is None:
        return None
    group = match.group(1).lower()
    return None if group in _NOT_A_GROUP or group.isdigit() or group in _RESOLUTIONS else group
