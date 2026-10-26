"""Line numbers count lines at "\\n", "\\r\\n" and a lone "\\r" alone.

The rows of the syntax tree, the source text of each definition, the COBOL and BMS
readers and the usage context all split a file the same way (line_list_of), so a form
feed or U+2028 stays inside its line and a file whose lines end in a lone "\\r" is not
read as one line.
"""

import os

from codetwine.file_analyzer import get_file_dependencies
from codetwine.utils.file_utils import line_list_of, lone_cr_to_lf, read_source_text


def _definition_list(tmp_path, name: str, data: bytes) -> list[tuple]:
    path = tmp_path / name
    path.write_bytes(data)
    dependencies = get_file_dependencies(str(path), str(tmp_path), {name}, {})
    return [(d["name"], d["start_line"], d["end_line"], d["context"])
            for d in dependencies["definitions"]]


def test_a_text_is_split_at_lf_crlf_and_a_lone_cr_alone():
    assert line_list_of("a\r\nb\rc\n") == ["a", "b", "c"]
    assert line_list_of("a\n\f\nb") == ["a", "\f", "b"]
    assert line_list_of("a b\x85c\vd\n") == ["a b\x85c\vd"]
    assert line_list_of("a\n\n") == ["a", ""]
    assert line_list_of("") == []


def test_a_lone_cr_becomes_lf_and_every_character_keeps_its_place():
    assert lone_cr_to_lf("a\rb\r\nc\r") == "a\nb\r\nc\n"


def test_read_source_text_has_the_lines_of_line_list_of(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes(b"a\rb\r\nc\n\fd\n")
    assert read_source_text(str(path)).split("\n")[:-1] == line_list_of("a\rb\r\nc\n\fd\n")


def test_a_file_whose_lines_end_in_a_lone_cr_has_one_line_per_cr(tmp_path):
    definition_list = _definition_list(
        tmp_path, "cr.py", b"def a():\r    return 1\r\r\rdef b():\r    return 2\r")
    assert definition_list == [
        ("a", 1, 2, "def a():\n    return 1"),
        ("b", 5, 6, "def b():\n    return 2"),
    ]


def test_a_form_feed_stays_inside_its_line_in_the_source_of_a_definition(tmp_path):
    definition_list = _definition_list(
        tmp_path, "ff.c",
        b"int a(void) {\n  return 1;\n}\n\f\nint b(void) {\n  return 2;\n}\n")
    assert definition_list[1] == ("b", 5, 7, "int b(void) {\n  return 2;\n}")


def test_a_form_feed_line_in_cobol_is_one_line(tmp_path):
    source = ("       IDENTIFICATION DIVISION.\n"
              "       PROGRAM-ID. HELLO.\n"
              "       DATA DIVISION.\n"
              "\f\n"
              "       WORKING-STORAGE SECTION.\n"
              "       01  WS-A PIC X.\n"
              "       PROCEDURE DIVISION.\n"
              "       MAIN-PARA.\n"
              "           STOP RUN.\n")
    definition_dict = {d[0]: d for d in _definition_list(tmp_path, "HELLO.cbl",
                                                         source.encode("utf-8"))}
    assert definition_dict["WS-A"][1:3] == (6, 6)
    assert definition_dict["MAIN-PARA"][1] == 8


def test_cobol_whose_lines_end_in_a_lone_cr_counts_each_line(tmp_path):
    source = ("       IDENTIFICATION DIVISION.\r"
              "       PROGRAM-ID. HELLO.\r"
              "       DATA DIVISION.\r"
              "       WORKING-STORAGE SECTION.\r"
              "       01  WS-A PIC X.\r"
              "       PROCEDURE DIVISION.\r"
              "       MAIN-PARA.\r"
              "           STOP RUN.\r")
    definition_dict = {d[0]: d for d in _definition_list(tmp_path, "HELLO.cbl",
                                                         source.encode("utf-8"))}
    assert definition_dict["WS-A"][1:3] == (5, 5)
    assert definition_dict["MAIN-PARA"][1] == 7
