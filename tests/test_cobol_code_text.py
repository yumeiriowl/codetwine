"""code_text_list(): the code of each line of a COBOL source file."""

from codetwine.parsers.cobol_format import code_text_list


def test_fixed_format_lines_give_columns_8_to_72():
    line = "000100 PROGRAM-ID. MAIN.".ljust(72) + "MAIN0001"
    assert [text.strip() for text in code_text_list([line, "000200* a comment"])] == \
        ["PROGRAM-ID. MAIN.", ""]


def test_free_format_lines_are_read_whole():
    assert code_text_list(["MOVE A TO B.", "MAIN-PARA."]) == ["MOVE A TO B.", "MAIN-PARA."]


def test_an_indented_star_line_of_a_free_format_file_carries_on_an_expression():
    line_list = [
        "PROCEDURE DIVISION.",
        "* a comment in column 1",
        "    *> a comment that starts with the comment mark",
        "    * a comment after a sentence that ended",
        "    COMPUTE X = A",
        "        * B",
        "* a comment in column 1 after an operand",
        "    DISPLAY X.",
    ]
    assert code_text_list(line_list) == [
        "PROCEDURE DIVISION.", "", "", "",
        "    COMPUTE X = A", "        * B", "", "    DISPLAY X.",
    ]


def test_a_free_format_directive_switches_the_format():
    line_list = ["      >>SOURCE FORMAT IS FREE", "MOVE A TO B."]
    assert code_text_list(line_list) == ["", "MOVE A TO B."]


def test_tabs_are_expanded_and_a_full_width_character_takes_two_columns():
    assert code_text_list(["\t01 X PIC 9."]) == [" 01 X PIC 9."]
    # "       01 顧客番号 PIC X." takes 25 columns, so ABCD starts in column 73
    line = "       01 顧客番号 PIC X." + " " * 47 + "ABCD"
    assert code_text_list([line])[0].rstrip() == "01 顧客番号 PIC X."


def _tagged(code: str, tag: str) -> str:
    """Write code from column 8 and a tag in columns 73 to 80, each character outside ASCII as two columns."""
    return "       " + code + " " * (65 - len(code.encode("cp932"))) + tag


def test_an_ambiguous_width_character_takes_two_columns_in_a_file_with_a_full_width_one():
    line_list = [
        _tagged('01  WS-MSG1 PIC X(20) VALUE "日本語".', "00000500"),
        _tagged('01  WS-MSG2 PIC X(20) VALUE "※①→■○×".', "00000600"),
        _tagged("01  WS-NEXT PIC X.", "00000700"),
    ]

    assert [text.rstrip() for text in code_text_list(line_list)] == [
        '01  WS-MSG1 PIC X(20) VALUE "日本語".', '01  WS-MSG2 PIC X(20) VALUE "※①→■○×".',
        "01  WS-NEXT PIC X.",
    ]


def test_an_ambiguous_width_character_takes_one_column_in_a_file_without_a_full_width_one():
    line = "       DISPLAY 'été à Köln'.".ljust(72) + "00000100"

    assert code_text_list([line])[0].rstrip() == "DISPLAY 'été à Köln'."


def test_a_sequence_area_that_starts_with_a_directive_character_is_not_a_directive():
    line_list = [
        "@BA01  PROGRAM-ID. SEQ.",
        "$A002  DATA DIVISION.",
        "       01  WS-A PIC 9 VALUE 1",
        "                      @USAGE@.",
    ]

    assert [text.strip() for text in code_text_list(line_list)] == [
        "PROGRAM-ID. SEQ.", "DATA DIVISION.", "01  WS-A PIC 9 VALUE 1", "@USAGE@.",
    ]


def test_directive_lines_hold_no_code():
    line_list = [
        "$SET SOURCEFORMAT\"FIXED\"",
        "000100 @OPTIONS MAIN",
        "      $IF DEBUG DEFINED",
        "       >>DEFINE DEBUG AS 1",
        "       01  WS-A PIC 9.",
    ]

    assert [text.strip() for text in code_text_list(line_list)] == ["", "", "", "", "01  WS-A PIC 9."]
