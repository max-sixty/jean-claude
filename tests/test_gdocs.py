"""Tests for gdocs module."""

from jean_claude.gdocs import _get_end_index


class TestGetEndIndex:
    """Tests for _get_end_index function."""

    def test_empty_document(self):
        """Empty document returns index 1."""
        doc = {"body": {"content": []}}
        assert _get_end_index(doc) == 1

    def test_single_element(self):
        """Single element returns its endIndex - 1."""
        doc = {
            "body": {
                "content": [
                    {"startIndex": 1, "endIndex": 50, "paragraph": {"elements": []}}
                ]
            }
        }
        assert _get_end_index(doc) == 49

    def test_multiple_elements(self):
        """Multiple elements returns last element's endIndex - 1."""
        doc = {
            "body": {
                "content": [
                    {"startIndex": 1, "endIndex": 50, "paragraph": {"elements": []}},
                    {"startIndex": 50, "endIndex": 100, "paragraph": {"elements": []}},
                ]
            }
        }
        assert _get_end_index(doc) == 99

    def test_missing_body(self):
        """Document without body returns 1."""
        doc = {}
        assert _get_end_index(doc) == 1

    def test_element_without_endindex(self):
        """Element without endIndex is skipped, uses previous element."""
        doc = {
            "body": {
                "content": [
                    {"startIndex": 1, "endIndex": 50, "paragraph": {"elements": []}},
                    {"sectionBreak": {"sectionStyle": {}}},  # No endIndex
                ]
            }
        }
        assert _get_end_index(doc) == 49

    def test_all_elements_missing_endindex(self):
        """All elements missing endIndex returns 1."""
        doc = {
            "body": {
                "content": [
                    {"sectionBreak": {"sectionStyle": {}}},
                ]
            }
        }
        assert _get_end_index(doc) == 1
