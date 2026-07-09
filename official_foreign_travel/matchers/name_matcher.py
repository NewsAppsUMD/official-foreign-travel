"""Advanced fuzzy name matching for legislators."""

import pickle
from functools import cache
from itertools import permutations
from pathlib import Path
from typing import Optional

import yaml

from ..models.match import NameMatch, NameMatchResult
from ..utils.config import Config, get_config
from ..utils.logging import get_logger
from ..utils.text import lower_name, normalize_name

logger = get_logger(__name__)


@cache
def _word_score(s1: str, s2: str) -> float:
    """
    Score two words based on longest common substring.

    First letter must match, then scores based on longest common
    substring of remaining characters. Pure function of its two
    arguments, and the same word pairs recur constantly across the
    ~440 candidates scored per query -- cached unbounded (the
    vocabulary is name words, small and finite).
    """
    if not s1 or not s2 or s1[0] != s2[0]:
        return 0.0

    len_s1 = len(s1)
    len_s2 = len(s2)

    # Dynamic programming for longest common substring
    scores = [[0] * len_s2 for _ in range(len_s1)]

    for j in range(1, len_s2):
        for i in range(1, len_s1):
            if s1[i] == s2[j]:
                scores[i][j] = scores[i - 1][j - 1] + 1
            scores[i][j] = max(scores[i - 1][j], scores[i][j - 1], scores[i][j])

    return (1.0 + scores[-1][-1]) / max(len_s1, len_s2)


@cache
def _words_list_score(s1: str, s2: str) -> float:
    """
    Score two word lists using dynamic programming over word alignments.

    Pure function of its two arguments; cached for the same reason as
    _word_score.
    """
    if not s1.strip() or not s2.strip():
        return 0.0

    words1 = s1.strip().split(" ")
    words2 = s2.strip().split(" ")
    len1 = len(words1)
    len2 = len(words2)

    # DP: best alignment of words
    scores = [[0.0] * (len2 + 1) for _ in range(len1 + 1)]

    for i in range(1, len1 + 1):
        for j in range(1, len2 + 1):
            scores[i][j] = max(
                scores[i - 1][j],
                scores[i][j - 1],
                scores[i - 1][j - 1] + _word_score(words1[i - 1], words2[j - 1]),
            )

    return scores[-1][-1] / max(len2, len1)


class NameMatcher:
    """
    Advanced name matching using fuzzy string matching and temporal indexing.

    This class loads legislator data and creates a time-indexed database
    for efficient name matching. It uses a sophisticated scoring algorithm
    that considers multiple name components and their variations.
    """

    def __init__(self, config: Optional[Config] = None):
        """
        Initialize name matcher.

        Args:
            config: Optional configuration object
        """
        self.config = config or get_config()
        self.members_index: dict[tuple[int, int], dict[str, tuple]] = {}
        self.members_dict: dict[str, dict] = {}
        self.charset: set[str] = set()
        self._initialized = False

    def initialize(self, use_cache: bool = True, cache_path: Optional[Path] = None) -> None:
        """
        Load and index legislator data.

        Args:
            use_cache: Whether to use pickle cache
            cache_path: Path to cache file (default: names_index.pickle)
        """
        if self._initialized:
            logger.debug("Already initialized")
            return

        if cache_path is None:
            cache_path = Path("names_index.pickle")

        # Try to load from cache
        if use_cache and cache_path.exists():
            logger.info(f"Loading name index from cache: {cache_path}")
            try:
                with open(cache_path, "rb") as f:
                    data = pickle.load(f)
                    self.charset, members_list, self.members_dict, self.members_index = data
                self._initialized = True
                logger.info(
                    f"Loaded {len(self.members_dict)} members, "
                    f"{len(self.members_index)} time periods from cache"
                )
                return
            except Exception as e:
                logger.warning(f"Failed to load cache: {e}")

        # Load from YAML files
        logger.info("Loading legislator data from YAML files...")
        members_list = self._load_yaml_data()

        # Build index
        self.charset = self._get_charset(members_list)
        self.members_dict = self._generate_bioguide_dict(members_list)
        self.members_index = {}
        self._append_data(members_list)

        self._initialized = True

        # Save to cache
        if use_cache:
            try:
                with open(cache_path, "wb") as f:
                    pickle.dump(
                        (self.charset, members_list, self.members_dict, self.members_index),
                        f,
                    )
                logger.info(f"Saved name index to cache: {cache_path}")
            except Exception as e:
                logger.warning(f"Failed to save cache: {e}")

        logger.info(
            f"Initialized with {len(self.members_dict)} members, "
            f"{len(self.members_index)} time periods"
        )

    def _load_yaml_data(self) -> list[dict]:
        """Load legislator data from YAML files."""
        members_list = []

        # Load current legislators
        if self.config.legislators_current_yaml.exists():
            try:
                with open(self.config.legislators_current_yaml, encoding="utf-8") as f:
                    current = yaml.safe_load(f.read())
                    members_list.extend(current)
                logger.info(f"Loaded {len(current)} current legislators")
            except Exception as e:
                logger.error(f"Error loading current legislators: {e}", exc_info=True)

        # Load historical legislators
        if self.config.legislators_historical_yaml.exists():
            try:
                with open(self.config.legislators_historical_yaml, encoding="utf-8") as f:
                    historical = yaml.safe_load(f.read())
                    members_list.extend(historical)
                logger.info(f"Loaded {len(historical)} historical legislators")
            except Exception as e:
                logger.error(f"Error loading historical legislators: {e}", exc_info=True)

        if not members_list:
            logger.warning("No legislator data loaded!")

        return members_list

    def _get_charset(self, members_list: list[dict]) -> set[str]:
        """Extract character set from all member names."""
        charset = set()
        for member in members_list:
            name_dict = member.get("name", {})
            for field in ["first", "middle", "last", "suffix", "nickname"]:
                if field in name_dict:
                    charset.update(set(lower_name(name_dict[field])))
        return charset

    def _generate_bioguide_dict(self, members_list: list[dict]) -> dict[str, dict]:
        """Create bioguide ID to member dict mapping."""
        return {member["id"]["bioguide"]: member for member in members_list}

    def _get_names(self, name_dict: dict) -> tuple[str, str, str, str, str]:
        """Extract name components from name dict."""
        return (
            name_dict.get("first", ""),
            name_dict.get("middle", ""),
            name_dict.get("last", ""),
            name_dict.get("suffix", ""),
            name_dict.get("nickname", ""),
        )

    def _month_iterator(
        self, start_year: int, start_month: int, end_year: int, end_month: int
    ) -> tuple[int, int]:
        """Generate (year, month) tuples for a date range."""
        year, month = start_year, start_month
        while (year < end_year) or ((year == end_year) and (month <= end_month)):
            yield (year, month)
            month += 1
            if month > 12:
                month = 1
                year += 1

    def _append_data(self, members_list: list[dict]) -> None:
        """Build time-indexed member database."""
        for member in members_list:
            firstname, middlename, lastname, suffix, nickname = self._get_names(member["name"])

            # Normalize all name components
            first_lower = lower_name(firstname)
            mid_lower = lower_name(middlename)
            last_lower = lower_name(lastname)
            suf_lower = lower_name(suffix)
            nick_lower = lower_name(nickname)

            member_tuple = (
                firstname,
                middlename,
                lastname,
                suffix,
                nickname,
                first_lower,
                mid_lower,
                last_lower,
                suf_lower,
                nick_lower,
            )

            member_bioguide = member["id"]["bioguide"]

            # Index by each month they served
            for term in member.get("terms", []):
                term_start = term["start"].split("-")
                term_end = term["end"].split("-")
                start_year, start_month = int(term_start[0]), int(term_start[1])
                end_year, end_month = int(term_end[0]), int(term_end[1])

                for year, month in self._month_iterator(
                    start_year, start_month, end_year, end_month
                ):
                    if (year, month) not in self.members_index:
                        self.members_index[(year, month)] = {}
                    self.members_index[(year, month)][member_bioguide] = member_tuple

    def _word_score(self, s1: str, s2: str) -> float:
        """Score two words -- see the module-level cached implementation."""
        return _word_score(s1, s2)

    def _words_list_score(self, s1: str, s2: str) -> float:
        """Score two word lists -- see the module-level cached implementation."""
        return _words_list_score(s1, s2)

    def _name_match(
        self,
        names: tuple[str, str, str, str, str],
        target: list[str],
        weights: tuple[float, float, float, float, float] = (0.8, 0.4, 4.0, 0.2, 1.0),
    ) -> float:
        """
        Score a member's name against target words.

        Uses dynamic programming to find the best permutation and alignment
        of name components against target words.

        Args:
            names: Tuple of (first, middle, last, suffix, nickname)
            target: List of target words
            weights: Weights for each name component (last name weighted highest)

        Returns:
            Match score
        """
        len_target = len(target)

        # Precompute all target slices (contiguous word sequences)
        target_slices = [[""] * (len_target + 1) for _ in range(len_target)]
        for start in range(len_target):
            for end in range(start + 1, len_target + 1):
                if end == start + 1:
                    target_slices[start][end] = target[start]
                else:
                    target_slices[start][end] = (
                        target_slices[start][end - 1] + " " + target[end - 1]
                    )

        # Score every (component, slice) pair exactly once. These scores
        # depend only on the component text and the slice -- not on the
        # permutation -- but the loop below used to recompute them inside
        # every one of the 120 permutations, which made a single query take
        # ~0.6s (a full-corpus run: an hour+).
        pair_scores = [
            [[0.0] * (len_target + 1) for _ in range(len_target)] for _ in range(len(names))
        ]
        for component in range(len(names)):
            name_text = names[component]
            weight = weights[component]
            for start in range(len_target):
                for end in range(start + 1, len_target + 1):
                    pair_scores[component][start][end] = weight * _words_list_score(
                        name_text, target_slices[start][end]
                    )

        # A component that can't score against any slice (usually an empty
        # middle/suffix/nickname) is a DP identity row -- its placement in
        # the permutation never changes the result -- so only permute the
        # components that can actually contribute. Most names have 2-3, so
        # this is 2-6 permutations instead of 120, with identical output.
        active = [
            component
            for component in range(len(names))
            if any(score for row in pair_scores[component] for score in row)
        ]
        if not active:
            return 0.0

        best_score = 0.0
        for perm in permutations(active):
            # DP: best alignment of this permutation
            scores = [[0.0] * (len_target + 1) for _ in range(len(perm) + 1)]

            for i in range(1, len(perm) + 1):
                component_scores = pair_scores[perm[i - 1]]
                for j in range(1, len_target + 1):
                    options = [scores[i][j - 1], scores[i - 1][j]]

                    # Try matching this name component to various target slices
                    for start in range(j):
                        options.append(scores[i - 1][start] + component_scores[start][j])

                    scores[i][j] = max(options)

            best_score = max(best_score, scores[-1][-1])

        return best_score

    def search_by_name(self, name: str, arrival_date: str, departure_date: str) -> NameMatchResult:
        """
        Search for legislators by name during a date range.

        Args:
            name: Name to search for
            arrival_date: Arrival date (M/D/YYYY format)
            departure_date: Departure date (M/D/YYYY format)

        Returns:
            NameMatchResult with ranked matches
        """
        if not self._initialized:
            self.initialize()

        # Normalize the query name
        normalized_name = normalize_name(name, self.charset)
        name_words = normalized_name.split()

        # Parse dates
        try:
            arr_month, _, arr_year = [int(x) for x in arrival_date.split("/")]
            dep_month, _, dep_year = [int(x) for x in departure_date.split("/")]
        except ValueError as e:
            logger.error(f"Invalid date format: {e}")
            return NameMatchResult(
                query_name=name,
                arrival_date=arrival_date,
                departure_date=departure_date,
                matches=[],
            )

        # Search all members active during the date range
        candidates: dict[str, float] = {}

        for year, month in self._month_iterator(arr_year, arr_month, dep_year, dep_month):
            if (year, month) not in self.members_index:
                continue

            for bioguide, member_tuple in self.members_index[(year, month)].items():
                if bioguide in candidates:
                    continue  # Already scored this person

                # Filter name words by matching first initials
                initials = set()
                for name_part in member_tuple[-5:]:  # Lowercase versions
                    if name_part:
                        initials.update(word[0] for word in name_part.split() if word)

                filtered_words = [word for word in name_words if word and word[0] in initials]

                # Score this candidate
                score = self._name_match(member_tuple[-5:], filtered_words)
                candidates[bioguide] = score

        # Create ranked list
        matches = []
        for bioguide, score in candidates.items():
            if bioguide in self.members_dict:
                member = self.members_dict[bioguide]
                name_dict = member.get("name", {})
                match = NameMatch(
                    bioguide_id=bioguide,
                    score=score,
                    first_name=name_dict.get("first", ""),
                    last_name=name_dict.get("last", ""),
                )
                matches.append(match)

        # Sort by score (descending)
        matches.sort(key=lambda x: x.score, reverse=True)

        # Take top N matches
        top_matches = matches[: self.config.match_return_count]

        # Create result
        result = NameMatchResult(
            query_name=name,
            arrival_date=arrival_date,
            departure_date=departure_date,
            matches=top_matches,
        )

        # Validate match quality
        result.validate_match(self.config.min_match_score, self.config.ambiguity_threshold)

        return result

    def get_name_by_bioguide(self, bioguide_id: str) -> Optional[dict]:
        """
        Get full name dict by bioguide ID.

        Args:
            bioguide_id: Bioguide ID

        Returns:
            Name dict or None
        """
        if not self._initialized:
            self.initialize()

        member = self.members_dict.get(bioguide_id)
        return member.get("name") if member else None
