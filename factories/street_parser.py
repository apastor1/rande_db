# street_parser.py
from __future__ import annotations

# Always load .env (your preference)
from dotenv import load_dotenv
load_dotenv()

import re
from typing import Dict, Optional, Iterable


class StreetParser:
    """
    Parse and standardize U.S. street lines into components:
      - street_number
      - predir (N, S, E, W, NE, NW, SE, SW)
      - name (core street name)
      - suffix (USPS type like ST, AVE, RD, HWY, ...)
      - postdir

    Methods:
      - parse(text) -> dict
      - standardize(components, case="usps") -> str
          Joins in order: street_number, predir, name, suffix, postdir
          Skips missing parts (no extra spaces).

    Notes:
      * Aim is pragmatic, dependency-free. For production-grade parsing at scale,
        consider libpostal/usaddress.
    """

    # USPS-style directionals → canonical
    DEFAULT_DIRECTION_MAP = {
        "N": "N", "NORTH": "N",
        "S": "S", "SOUTH": "S",
        "E": "E", "EAST": "E",
        "W": "W", "WEST": "W",
        "NE": "NE", "NORTHEAST": "NE",
        "NW": "NW", "NORTHWEST": "NW",
        "SE": "SE", "SOUTHEAST": "SE",
        "SW": "SW", "SOUTHWEST": "SW",
    }

    # USPS suffix normalization (solid subset; extend as needed)
    DEFAULT_SUFFIX_MAP = {
        "ST": "ST", "STREET": "ST",
        "AVE": "AVE", "AV": "AVE", "AVENUE": "AVE",
        "BLVD": "BLVD", "BOULEVARD": "BLVD",
        "RD": "RD", "ROAD": "RD",
        "HWY": "HWY", "HIGHWAY": "HWY",
        "PKWY": "PKWY", "PARKWAY": "PKWY",
        "DR": "DR", "DRIVE": "DR",
        "LN": "LN", "LANE": "LN",
        "TER": "TER", "TERRACE": "TER",
        "CIR": "CIR", "CIRCLE": "CIR",
        "CT": "CT", "COURT": "CT",
        "PL": "PL", "PLACE": "PL",
        "WAY": "WAY",
        "SQ": "SQ", "SQUARE": "SQ",
        "TRL": "TRL", "TRAIL": "TRL",
        "EXPY": "EXPY", "EXPRESSWAY": "EXPY",
        "FWY": "FWY", "FREEWAY": "FWY",
        "TPKE": "TPKE", "TURNPIKE": "TPKE",
        "PIKE": "PIKE",
        "ALY": "ALY", "ALLEY": "ALY",
        "RUN": "RUN",
        "BND": "BND", "BEND": "BND",
        "PT": "PT", "POINT": "PT",
        "CV": "CV", "COVE": "CV",
        "HOLW": "HOLW", "HOLLOW": "HOLW",
        "PLZ": "PLZ", "PLAZA": "PLZ",
        "VW": "VW", "VIEW": "VW",
        "VIS": "VIS", "VISTA": "VIS",
    }

    # Unit tokens (everything from the first one onward is ignored)
    DEFAULT_UNIT_TOKENS = {
        "#", "APT", "APARTMENT", "STE", "SUITE", "UNIT",
        "FL", "FLOOR", "BLDG", "BUILDING", "ROOM", "RM"
    }

    HOUSE_NUM_RE = re.compile(r"^\d+[A-Z]?(-\d+[A-Z]?)?$")  # 123, 123B, 12-34, 12-34B

    def __init__(
        self,
        direction_map: Optional[Dict[str, str]] = None,
        suffix_map: Optional[Dict[str, str]] = None,
        unit_tokens: Optional[Iterable[str]] = None,
    ) -> None:
        self.direction_map = {k.upper(): v for k, v in (direction_map or self.DEFAULT_DIRECTION_MAP).items()}
        self.suffix_map = {k.upper(): v for k, v in (suffix_map or self.DEFAULT_SUFFIX_MAP).items()}
        self.unit_tokens = {t.upper() for t in (unit_tokens or self.DEFAULT_UNIT_TOKENS)}

    # ----------------- public API -----------------

    def parse(self, s: Optional[str]) -> Dict[str, Optional[str]]:
        """
        Parse a street line into components.
        Returns a dict:
          {
            "street_number": str|None,
            "predir": str|None,
            "name": str|None,
            "suffix": str|None,   # USPS-normalized abbreviation
            "postdir": str|None,
            "direction": str|None # convenience: predir if present else postdir
          }
        """
        if not s:
            return self._empty_result()

        s = self._clean(s)
        if not s:
            return self._empty_result()

        tokens = s.split()

        # strip unit tail
        cut = None
        for i, tok in enumerate(tokens):
            if self._canon(tok) in self.unit_tokens or tok == "#":
                cut = i
                break
        if cut is not None:
            tokens = tokens[:cut]
        if not tokens:
            return self._empty_result()

        # 1) Leading house number
        street_number = None
        if self.HOUSE_NUM_RE.match(self._canon(tokens[0])):
            street_number = tokens.pop(0)

        # 2) Pre-direction (optional)
        predir = None
        if tokens and (d := self._norm_direction(tokens[0])):
            predir = d
            tokens.pop(0)

        # 3/4) Suffix & Post-direction near the end
        suffix = None
        postdir = None

        if tokens and (suf := self._norm_suffix(tokens[-1])):
            suffix = suf
            tokens.pop()

        if tokens and (d := self._norm_direction(tokens[-1])):
            postdir = d
            tokens.pop()

        if suffix is None and tokens and (suf := self._norm_suffix(tokens[-1])):
            suffix = suf
            tokens.pop()

        # Remaining tokens form the street name
        name = self._collapse_ws(" ".join(tokens)) if tokens else None
        direction = predir or postdir

        return {
            "street_number": street_number,
            "predir": predir,
            "name": name,
            "suffix": suffix,
            "postdir": postdir,
            "direction": direction,
        }

    def standardize(self, parts: Dict[str, Optional[str]], *, case: str = "usps",include_street_number=True) -> str:
        """
        Join components in USPS order: number, predir, name, suffix, postdir.
        Skips missing parts to avoid extra spaces.

        case:
          - "usps": number untouched, predir/suffix/postdir UPPER, name Title Case
          - "upper": all UPPER
          - "lower": all lower
          - "title": number unchanged, others Title Case
        """
        num = (parts.get("street_number") or "").strip()
        predir = (parts.get("predir") or "").strip()
        name = (parts.get("name") or "").strip()
        suffix = (parts.get("suffix") or "").strip()
        postdir = (parts.get("postdir") or "").strip()

        if case == "usps":
            predir = predir.upper()
            suffix = suffix.upper()
            postdir = postdir.upper()
            name = self._titlecase(name)
        elif case == "upper":
            num = num.upper()
            predir = predir.upper()
            name = name.upper()
            suffix = suffix.upper()
            postdir = postdir.upper()
        elif case == "lower":
            num = num.lower()
            predir = predir.lower()
            name = name.lower()
            suffix = suffix.lower()
            postdir = postdir.lower()
        elif case == "title":
            # USPS usually prefers uppercase for suffix/direction,
            # but title-case is sometimes desired for display.
            name = self._titlecase(name)
            predir = self._titlecase(predir)
            suffix = self._titlecase(suffix)
            postdir = self._titlecase(postdir)

        join_list = [num, predir, name, suffix, postdir] if include_street_number else [predir, name, suffix, postdir]
        parts_list = [p for p in join_list if p]
        return self._collapse_ws(" ".join(parts_list))

    # ----------------- helpers -----------------

    @staticmethod
    def _empty_result() -> Dict[str, Optional[str]]:
        return {
            "street_number": None,
            "predir": None,
            "name": None,
            "suffix": None,
            "postdir": None,
            "direction": None,
        }

    @staticmethod
    def _collapse_ws(s: str) -> str:
        return re.sub(r"\s+", " ", s.strip())

    @staticmethod
    def _titlecase(s: str) -> str:
        if not s:
            return s
        # Simple Title Case with preservation for all-caps acronyms/numbers
        def tc_word(w: str) -> str:
            if w.isupper() and len(w) <= 4:  # keep short acronyms like "FM", "N", "US"
                return w
            return w[:1].upper() + w[1:].lower() if w else w
        return " ".join(tc_word(w) for w in s.split())

    @staticmethod
    def _clean(s: str) -> str:
        # keep hyphens for Queens-style numbers, drop punctuation that often appears
        s = re.sub(r"[.,;:]", " ", s)
        return StreetParser._collapse_ws(s)

    @staticmethod
    def _canon(tok: str) -> str:
        return re.sub(r"[^\w\-]", "", tok).upper()

    def _norm_direction(self, tok: str) -> Optional[str]:
        return self.direction_map.get(self._canon(tok))

    def _norm_suffix(self, tok: str) -> Optional[str]:
        return self.suffix_map.get(self._canon(tok))


# ---- quick demo ----
if __name__ == "__main__":
    sp = StreetParser()
    samples = [
        "100 N Main Street",
        "200 Main Ave NE",
        "12-34 34th Avenue",
        "500 W 1st St Apt 4",
        "123B South Broadway Blvd",
        "742 Evergreen Ter",
        "3505 S. Las Vegas Blvd # 12",
        "55 West End Ave",
        "FM 620",
    ]
    for s in samples:
        parts = sp.parse(s)
        print(s, "->", parts, "||", sp.standardize(parts, case="lower"))
