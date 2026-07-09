"""Tests for building members.csv/committees.csv from congress-legislators YAML data."""

from official_foreign_travel.scrapers.reference_data import (
    build_committees_index,
    build_members_index,
    committee_name_variants,
    full_names_for,
)


class TestFullNamesFor:
    def test_uses_official_full_when_present(self):
        names = full_names_for(
            {"first": "Charles", "last": "Rangel", "official_full": "Charles B. Rangel"}
        )
        assert "Charles B. Rangel" in names

    def test_always_includes_plain_first_last_even_with_official_full(self):
        names = full_names_for(
            {"first": "Charles", "last": "Rangel", "official_full": "Charles B. Rangel"}
        )
        assert "Charles Rangel" in names

    def test_falls_back_to_first_last_when_no_official_full(self):
        names = full_names_for({"first": "Bill", "last": "Sali"})
        assert "Bill Sali" in names

    def test_includes_middle_name_variant(self):
        names = full_names_for({"first": "Sander", "middle": "M.", "last": "Levin"})
        assert "Sander Levin" in names
        assert "Sander M. Levin" in names

    def test_includes_nickname_variant(self):
        names = full_names_for({"first": "William", "nickname": "Bill", "last": "Clinton"})
        assert "William Clinton" in names
        assert "Bill Clinton" in names

    def test_includes_suffix_variant(self):
        names = full_names_for({"first": "John", "last": "Doe", "suffix": "Jr."})
        assert "John Doe" in names
        assert "John Doe Jr." in names

    def test_returns_empty_when_no_first_or_last(self):
        assert full_names_for({}) == set()
        assert full_names_for({"first": "Only"}) == set()

    def test_folds_accents(self):
        names = full_names_for({"first": "Nydia", "middle": "M.", "last": "Velázquez"})
        assert "Nydia Velazquez" in names
        assert "Nydia M. Velazquez" in names

    def test_hyphenates_multi_word_last_name(self):
        names = full_names_for({"first": "Sheila", "last": "Jackson Lee"})
        assert "Sheila Jackson Lee" in names
        assert "Sheila Jackson-Lee" in names

    def test_unhyphenates_hyphenated_last_name(self):
        names = full_names_for({"first": "Jane", "last": "Smith-Jones"})
        assert "Jane Smith Jones" in names

    def test_reduces_middle_word_to_initial(self):
        names = full_names_for({"first": "David", "middle": "Lee", "last": "Hobson"})
        assert "David L. Hobson" in names

    def test_first_and_middle_as_initials(self):
        names = full_names_for({"first": "Eddie", "middle": "Bernice", "last": "Johnson"})
        assert "E. B. Johnson" in names
        assert "E.B. Johnson" in names

    def test_collapses_consecutive_initials(self):
        names = full_names_for({"first": "C.", "middle": "W. Bill", "last": "Young"})
        assert "C.W. Bill Young" in names

    def test_goby_name_from_middle_when_first_is_an_initial(self):
        names = full_names_for({"first": "K.", "middle": "Michael", "last": "Conaway"})
        assert "Michael Conaway" in names
        assert "Mike Conaway" in names  # diminutive of the go-by

        names = full_names_for({"first": "C.", "middle": "A. Dutch", "last": "Ruppersberger"})
        assert "Dutch Ruppersberger" in names

    def test_diminutives_of_formal_first_name(self):
        names = full_names_for({"first": "Gerald", "middle": "E.", "last": "Connolly"})
        assert "Gerry Connolly" in names
        assert "Jerry Connolly" in names

    def test_formal_name_from_informal_first_name(self):
        # The YAML sometimes stores the informal name ("Tom Lantos") while
        # reports print the formal one ("Thomas Lantos").
        names = full_names_for({"first": "Tom", "last": "Lantos"})
        assert "Thomas Lantos" in names

    def test_comma_and_bare_suffix_forms(self):
        names = full_names_for(
            {"first": "George", "middle": "E.", "last": "Brown", "suffix": "Jr."}
        )
        assert "George E. Brown, Jr" in names
        assert "George E. Brown Jr." in names
        assert "George Brown Jr" in names


class TestBuildMembersIndex:
    def _person(self, bioguide, first, last, end="2018-01-03", **name_extra):
        return {
            "id": {"bioguide": bioguide},
            "name": {"first": first, "last": last, **name_extra},
            "terms": [{"type": "rep", "start": "2016-01-03", "end": end}],
        }

    def test_builds_hon_prefixed_key(self):
        result = build_members_index([[self._person("D000123", "Jane", "Doe")]])
        assert result.rows["HON. JANE DOE"] == "D000123"

    def test_excludes_people_whose_last_term_ended_before_min_term_end(self):
        result = build_members_index(
            [[self._person("D000123", "Jane", "Doe", end="1980-01-01")]],
            min_term_end="1990-01-01",
        )
        assert result.rows == {}
        assert result.people_considered == 0

    def test_excludes_senate_only_terms(self):
        person = {
            "id": {"bioguide": "S000123"},
            "name": {"first": "Jane", "last": "Doe"},
            "terms": [{"type": "sen", "start": "2016-01-03", "end": "2022-01-03"}],
        }
        result = build_members_index([[person]])
        assert result.rows == {}

    def test_conflicting_names_across_different_people_are_dropped_not_guessed(self):
        result = build_members_index(
            [
                [
                    self._person("A000001", "John", "Smith"),
                    self._person("B000002", "John", "Smith"),
                ]
            ]
        )
        assert "HON. JOHN SMITH" not in result.rows
        assert "HON. JOHN SMITH" in result.dropped_ambiguous

    def test_same_person_appearing_twice_is_not_flagged_ambiguous(self):
        # e.g. the same person appears in both legislators-current and
        # legislators-historical docs, or has multiple qualifying terms.
        person = self._person("A000001", "Jane", "Doe")
        result = build_members_index([[person], [person]])
        assert result.rows["HON. JANE DOE"] == "A000001"
        assert result.dropped_ambiguous == []

    def test_person_with_no_usable_name_is_skipped_and_counted(self):
        person = {
            "id": {"bioguide": "A000001"},
            "name": {},
            "terms": [{"type": "rep", "end": "2018-01-01"}],
        }
        result = build_members_index([[person]])
        assert result.rows == {}
        assert result.skipped_no_name == 1


class TestCommitteeNameVariants:
    def test_strips_house_chamber_prefix(self):
        variants = committee_name_variants("House Committee on Agriculture", "House ")
        assert "Committee on Agriculture" in variants

    def test_generates_the_insertion_and_removal(self):
        variants = committee_name_variants("Committee on Agriculture", "")
        assert "Committee on Agriculture" in variants
        assert "Committee on the Agriculture" in variants

        variants = committee_name_variants("Committee on the Budget", "")
        assert "Committee on the Budget" in variants
        assert "Committee on Budget" in variants

    def test_reorders_select_suffix_to_leading_modifier(self):
        variants = committee_name_variants(
            "House Committee on Intelligence (Permanent Select)", "House "
        )
        assert "Permanent Select Committee on Intelligence" in variants

    def test_does_not_add_bare_form_when_suffix_present(self):
        # Regression: without this, "Committee on Ethics (Select)" (a
        # short-lived 95th-Congress committee) would also emit the bare
        # "Committee on Ethics", colliding with the unrelated, long-running
        # standing Ethics committee.
        variants = committee_name_variants("House Committee on Ethics (Select)", "House ")
        assert "Committee on Ethics" not in variants
        assert "Select Committee on Ethics" in variants


class TestBuildCommitteesIndex:
    def _committee(self, code, name, ctype="house", names=None):
        c = {"type": ctype, "thomas_id": code, "name": name}
        if names:
            c["names"] = names
        return c

    def test_builds_upper_case_name_to_code_index(self):
        result = build_committees_index(
            [[self._committee("HSAG", "House Committee on Agriculture")]]
        )
        assert result.rows["COMMITTEE ON AGRICULTURE"] == "HSAG"

    def test_excludes_senate_committees_by_default(self):
        result = build_committees_index(
            [[self._committee("SSAG", "Senate Committee on Agriculture", ctype="senate")]]
        )
        assert result.rows == {}

    def test_includes_joint_committees(self):
        result = build_committees_index(
            [[self._committee("JSTX", "Joint Committee on Taxation", ctype="joint")]]
        )
        assert result.rows["JOINT COMMITTEE ON TAXATION"] == "JSTX"

    def test_expands_historical_names_per_congress(self):
        result = build_committees_index(
            [
                [
                    self._committee(
                        "HSAS",
                        "House Committee on Armed Services",
                        names={104: "National Security", 105: "National Security"},
                    )
                ]
            ]
        )
        assert result.rows["COMMITTEE ON NATIONAL SECURITY"] == "HSAS"
        assert result.rows["COMMITTEE ON ARMED SERVICES"] == "HSAS"

    def test_current_doc_wins_collision_when_listed_first(self):
        current = [self._committee("HSSO", "House Committee on Ethics")]
        historical = [self._committee("HLET", "House Committee on Ethics")]
        result = build_committees_index([current, historical])
        assert result.rows["COMMITTEE ON ETHICS"] == "HSSO"
        assert ("COMMITTEE ON ETHICS", "HSSO", "HLET") in result.collisions
