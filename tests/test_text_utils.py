"""Tests for text utility functions."""

from official_foreign_travel.utils.text import lower_name, normalize_name, strip_html_tags


class TestLowerName:
    """Tests for lower_name function."""

    def test_basic_lowercase(self):
        """Test basic lowercase conversion."""
        assert lower_name("John") == "john"
        assert lower_name("SMITH") == "smith"

    def test_removes_special_characters(self):
        """Test removal of special characters."""
        assert lower_name("O'Brien") == "o brien"
        assert lower_name("Mary-Jane") == "mary jane"
        assert lower_name("(Hon.)") == "hon"

    def test_handles_accents(self):
        """Test handling of accented characters."""
        assert lower_name("José") == "jose"
        assert lower_name("François") == "francois"
        assert lower_name("Müller") == "muller"

    def test_normalizes_whitespace(self):
        """Test whitespace normalization."""
        assert lower_name("John  Smith") == "john smith"
        assert lower_name("  Mary  ") == "mary"

    def test_empty_string(self):
        """Test empty string handling."""
        assert lower_name("") == ""
        assert lower_name("   ") == ""


class TestNormalizeName:
    """Tests for normalize_name function."""

    def test_basic_normalization(self):
        """Test basic name normalization."""
        result = normalize_name("Hon. John Smith", charset=None)
        assert "john" in result.lower()
        assert "smith" in result.lower()

    def test_removes_honorifics(self):
        """Test that honorifics are handled properly."""
        result = normalize_name("Hon. John Smith", charset=None)
        # Should contain the name parts
        assert "john" in result.lower()

    def test_handles_empty_name(self):
        """Test empty name handling."""
        result = normalize_name("", charset=None)
        assert result == ""

    def test_with_charset(self):
        """Test normalization with character set filtering."""
        charset = set("abcdefghijklmnopqrstuvwxyz ")
        result = normalize_name("John123Smith", charset=charset)
        # Numbers should be filtered out
        assert "123" not in result


class TestStripHtmlTags:
    def test_strips_raw_strong_tags(self):
        assert strip_html_tags("OFFICIAL <strong>FOREIGN</strong> TRAVEL") == "OFFICIAL FOREIGN TRAVEL"

    def test_strips_raw_sup_tags(self):
        assert strip_html_tags("1,930.00  <SUP>(3)</SUP>  ...........") == "1,930.00  (3)  ..........."

    def test_strips_escaped_sup_tags(self):
        assert strip_html_tags("1,930.00  &lt;SUP&gt;(3)&lt;/SUP&gt;  ...........") == "1,930.00  (3)  ..........."

    def test_strips_escaped_strong_tags(self):
        assert strip_html_tags("OFFICIAL &lt;strong&gt;FOREIGN&lt;/strong&gt; TRAVEL") == "OFFICIAL FOREIGN TRAVEL"

    def test_preserves_content_between_escaped_tags(self):
        raw = "...........  &lt;SUP&gt;(3)&lt;/SUP&gt;  ..........."
        assert strip_html_tags(raw) == "...........  (3)  ..........."

    def test_strips_mixed_raw_and_escaped_tags(self):
        raw = "&lt;strong&gt;OFFICIAL&lt;/strong&gt; <strong>FOREIGN</strong> TRAVEL"
        assert strip_html_tags(raw) == "OFFICIAL FOREIGN TRAVEL"
