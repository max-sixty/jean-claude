"""Tests for gdocs module."""

from jean_claude.gdocs import _extract_text, _get_end_index


class TestExtractText:
    """Tests for _extract_text function."""

    def test_empty_document(self):
        """Empty document returns empty string."""
        doc = {"body": {"content": []}}
        assert _extract_text(doc) == ""

    def test_single_paragraph(self):
        """Single paragraph text is extracted."""
        doc = {
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [{"textRun": {"content": "Hello, world!\n"}}]
                        }
                    }
                ]
            }
        }
        assert _extract_text(doc) == "Hello, world!\n"

    def test_multiple_paragraphs(self):
        """Multiple paragraphs are concatenated."""
        doc = {
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [{"textRun": {"content": "First paragraph.\n"}}]
                        }
                    },
                    {
                        "paragraph": {
                            "elements": [
                                {"textRun": {"content": "Second paragraph.\n"}}
                            ]
                        }
                    },
                ]
            }
        }
        assert _extract_text(doc) == "First paragraph.\nSecond paragraph.\n"

    def test_multiple_text_runs_in_paragraph(self):
        """Multiple text runs in same paragraph are concatenated."""
        doc = {
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {"textRun": {"content": "Hello, "}},
                                {"textRun": {"content": "world!"}},
                            ]
                        }
                    }
                ]
            }
        }
        assert _extract_text(doc) == "Hello, world!"

    def test_ignores_non_paragraph_elements(self):
        """Non-paragraph elements (like sectionBreak) are ignored."""
        doc = {
            "body": {
                "content": [
                    {"sectionBreak": {"sectionStyle": {}}},
                    {
                        "paragraph": {
                            "elements": [{"textRun": {"content": "Content\n"}}]
                        }
                    },
                ]
            }
        }
        assert _extract_text(doc) == "Content\n"

    def test_ignores_non_textrun_elements(self):
        """Non-textRun elements in paragraphs are ignored."""
        doc = {
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {"inlineObjectElement": {"inlineObjectId": "obj123"}},
                                {"textRun": {"content": "Text after image\n"}},
                            ]
                        }
                    }
                ]
            }
        }
        assert _extract_text(doc) == "Text after image\n"

    def test_missing_body(self):
        """Document without body returns empty string."""
        doc = {}
        assert _extract_text(doc) == ""

    def test_missing_content(self):
        """Document with empty body returns empty string."""
        doc = {"body": {}}
        assert _extract_text(doc) == ""


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
