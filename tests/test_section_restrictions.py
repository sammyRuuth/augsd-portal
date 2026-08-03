"""Tests for section restrictions module"""


from app.core.section_restrictions import (
    SECTION_SUFFIX_RESTRICTIONS,
    add_restriction,
    filter_sections_for_branch,
    get_all_restrictions,
    get_allowed_branches_for_suffix,
    get_restricted_suffix,
    get_restriction_info,
    is_section_allowed_for_branch,
    remove_restriction,
)


class TestGetRestrictedSuffix:
    """Tests for get_restricted_suffix function"""

    def test_section_ending_with_c(self):
        """Section ending with C should match C suffix"""
        assert get_restricted_suffix("L1C") == "C"
        assert get_restricted_suffix("P2C") == "C"
        assert get_restricted_suffix("T3C") == "C"

    def test_section_ending_with_e(self):
        """Section ending with E should match E suffix"""
        assert get_restricted_suffix("L1E") == "E"
        assert get_restricted_suffix("P2E") == "E"
        assert get_restricted_suffix("T3E") == "E"

    def test_section_without_restriction(self):
        """Sections without restricted suffixes should return None"""
        assert get_restricted_suffix("L1") is None
        assert get_restricted_suffix("P2") is None
        assert get_restricted_suffix("T3A") is None
        assert get_restricted_suffix("L1B") is None

    def test_empty_or_none(self):
        """Empty or None section names should return None"""
        assert get_restricted_suffix("") is None
        assert get_restricted_suffix(None) is None


class TestGetAllowedBranchesForSuffix:
    """Tests for get_allowed_branches_for_suffix function"""

    def test_c_suffix_branches(self):
        """C suffix should allow A7"""
        branches = get_allowed_branches_for_suffix("C")
        assert "A7" in branches
        assert len(branches) == 1

    def test_e_suffix_branches(self):
        """E suffix should allow A3, A8, AA"""
        branches = get_allowed_branches_for_suffix("E")
        assert "A3" in branches
        assert "A8" in branches
        assert "AA" in branches
        assert len(branches) == 3

    def test_unknown_suffix(self):
        """Unknown suffix should return empty list"""
        assert get_allowed_branches_for_suffix("X") == []
        assert get_allowed_branches_for_suffix("Z") == []


class TestIsSectionAllowedForBranch:
    """Tests for is_section_allowed_for_branch function"""

    # C suffix tests (A7 only)
    def test_c_section_allowed_for_a7(self):
        """C sections should be allowed for A7"""
        assert is_section_allowed_for_branch("L1C", ["A7"]) is True
        assert is_section_allowed_for_branch("P2C", ["A7"]) is True

    def test_c_section_not_allowed_for_other_branches(self):
        """C sections should NOT be allowed for non-A7 branches"""
        assert is_section_allowed_for_branch("L1C", ["A3"]) is False
        assert is_section_allowed_for_branch("L1C", ["A8"]) is False
        assert is_section_allowed_for_branch("L1C", ["AA"]) is False
        assert is_section_allowed_for_branch("L1C", ["B2"]) is False

    # E suffix tests (A3, A8, AA)
    def test_e_section_allowed_for_a3_a8_aa(self):
        """E sections should be allowed for A3, A8, AA"""
        assert is_section_allowed_for_branch("L1E", ["A3"]) is True
        assert is_section_allowed_for_branch("L1E", ["A8"]) is True
        assert is_section_allowed_for_branch("L1E", ["AA"]) is True

    def test_e_section_not_allowed_for_other_branches(self):
        """E sections should NOT be allowed for non-A3/A8/AA branches"""
        assert is_section_allowed_for_branch("L1E", ["A7"]) is False
        assert is_section_allowed_for_branch("L1E", ["B2"]) is False
        assert is_section_allowed_for_branch("L1E", ["A1"]) is False

    # Dual degree tests
    def test_dual_degree_any_branch_allowed(self):
        """Dual degree: allowed if ANY branch matches"""
        # B2A7 student can access C sections (because of A7)
        assert is_section_allowed_for_branch("L1C", ["B2", "A7"]) is True
        # B2A3 student can access E sections (because of A3)
        assert is_section_allowed_for_branch("L1E", ["B2", "A3"]) is True

    def test_dual_degree_neither_branch_allowed(self):
        """Dual degree: denied if NEITHER branch matches"""
        # B1B2 student cannot access C sections (neither is A7)
        assert is_section_allowed_for_branch("L1C", ["B1", "B2"]) is False
        # B1A7 student cannot access E sections (neither is A3/A8/AA)
        assert is_section_allowed_for_branch("L1E", ["B1", "A7"]) is False

    # Unrestricted sections
    def test_unrestricted_sections_allowed_for_all(self):
        """Sections without restrictions should be allowed for all branches"""
        assert is_section_allowed_for_branch("L1", ["A7"]) is True
        assert is_section_allowed_for_branch("L1", ["A3"]) is True
        assert is_section_allowed_for_branch("P2", ["B2"]) is True
        assert is_section_allowed_for_branch("T3A", ["AA"]) is True

    # Edge cases
    def test_empty_branches_allow_all(self):
        """Empty branches list should allow all sections"""
        assert is_section_allowed_for_branch("L1C", []) is True
        assert is_section_allowed_for_branch("L1E", []) is True

    def test_none_branches_allow_all(self):
        """None branches should allow all sections"""
        assert is_section_allowed_for_branch("L1C", None) is True

    def test_empty_section_name_allow_all(self):
        """Empty section name should allow (no restriction can match)"""
        assert is_section_allowed_for_branch("", ["A7"]) is True

    # Component filtering
    def test_component_filtering(self):
        """Restrictions should only apply to LAB, LEC, TUT components"""
        # LAB, LEC, TUT should be restricted
        assert is_section_allowed_for_branch("L1C", ["A3"], component="LAB") is False
        assert is_section_allowed_for_branch("L1C", ["A3"], component="LEC") is False
        assert is_section_allowed_for_branch("L1C", ["A3"], component="TUT") is False

        # Other components (if any) should not be restricted
        # Note: Current config restricts LAB, LEC, TUT only
        assert is_section_allowed_for_branch("L1C", ["A3"], component="OTHER") is True


class TestGetRestrictionInfo:
    """Tests for get_restriction_info function"""

    def test_restricted_section_info(self):
        """Should return info for restricted sections"""
        info = get_restriction_info("L1C")
        assert info is not None
        assert info["suffix"] == "C"
        assert "A7" in info["allowed_branches"]

    def test_unrestricted_section_info(self):
        """Should return None for unrestricted sections"""
        assert get_restriction_info("L1") is None
        assert get_restriction_info("P2") is None


class TestFilterSectionsForBranch:
    """Tests for filter_sections_for_branch function"""

    def test_filter_with_mock_sections(self):
        """Should filter sections based on branch"""

        class MockSection:
            def __init__(self, section, component="LAB"):
                self.section = section
                self.component = component

        sections = [
            MockSection("L1"),  # Unrestricted
            MockSection("L1C"),  # A7 only
            MockSection("L1E"),  # A3, A8, AA only
            MockSection("L2"),  # Unrestricted
        ]

        # A7 student should see L1, L1C, L2 (not L1E)
        filtered = filter_sections_for_branch(sections, ["A7"])
        filtered_names = [s.section for s in filtered]
        assert "L1" in filtered_names
        assert "L1C" in filtered_names
        assert "L2" in filtered_names
        assert "L1E" not in filtered_names

        # A3 student should see L1, L1E, L2 (not L1C)
        filtered = filter_sections_for_branch(sections, ["A3"])
        filtered_names = [s.section for s in filtered]
        assert "L1" in filtered_names
        assert "L1E" in filtered_names
        assert "L2" in filtered_names
        assert "L1C" not in filtered_names

    def test_filter_empty_branches_returns_all(self):
        """Empty branches should return all sections"""

        class MockSection:
            def __init__(self, section, component="LAB"):
                self.section = section
                self.component = component

        sections = [MockSection("L1"), MockSection("L1C"), MockSection("L1E")]
        filtered = filter_sections_for_branch(sections, [])
        assert len(filtered) == 3


class TestDynamicRestrictions:
    """Tests for runtime restriction management"""

    def test_add_and_remove_restriction(self):
        """Should be able to add and remove restrictions at runtime"""
        # Add a new restriction
        add_restriction("M", ["B1", "B2"], "Mechanical branch sections")

        # Verify it was added
        assert "M" in get_all_restrictions()
        branches = get_allowed_branches_for_suffix("M")
        assert "B1" in branches
        assert "B2" in branches

        # Test the new restriction works
        assert is_section_allowed_for_branch("L1M", ["B1"]) is True
        assert is_section_allowed_for_branch("L1M", ["A7"]) is False

        # Remove the restriction
        assert remove_restriction("M") is True

        # Verify it was removed
        assert "M" not in get_all_restrictions()
        # After removal, section should be allowed for all
        assert is_section_allowed_for_branch("L1M", ["A7"]) is True

    def test_remove_nonexistent_restriction(self):
        """Removing nonexistent restriction should return False"""
        assert remove_restriction("NONEXISTENT") is False


class TestGetAllRestrictions:
    """Tests for get_all_restrictions function"""

    def test_returns_copy(self):
        """Should return a copy, not the original dict"""
        restrictions = get_all_restrictions()
        # Modifying the returned dict should not affect original
        restrictions["TEST"] = {"allowed_branches": []}
        assert "TEST" not in SECTION_SUFFIX_RESTRICTIONS
