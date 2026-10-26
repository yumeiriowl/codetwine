"""COBOL: the right margin, OF / IN qualification, copybooks named by COPY whatever their
extension, BMS symbolic maps, extensions in any case, and the level, group and name line
of the definitions."""

import os

import pytest

from codetwine.cobol_file_index import file_index_cache_dict, reference_target_cache_dict
from codetwine.config.settings import EXT_TO_LANGUAGE_DICT, has_language, language_ext
from codetwine.extractors.bms_source import read_bms_source
from codetwine.extractors.cobol_source import read_cobol_source
from codetwine.extractors.dependency_graph import build_project_dependencies
from codetwine.file_analyzer import get_file_dependencies
from codetwine.parsers.cobol_format import code_text_list
from codetwine.parsers.ts_parser import parse_cache_dict
from codetwine.utils.file_utils import copy_path_to_rel


def _name_line_list(usage_list: list[dict]) -> list[dict]:
    """Return the usages as {"lines", "name"} dicts, without the fields of the target definition."""
    return [{"lines": usage["lines"], "name": usage["name"]} for usage in usage_list]


@pytest.fixture(autouse=True)
def _clear_cache():
    """Start each test without parse results, file indexes and references of another project."""
    parse_cache_dict.clear()
    file_index_cache_dict.clear()
    reference_target_cache_dict.clear()


def _fixed(line_list: list[str], tag: str) -> str:
    """Write lines in fixed format with a sequence number and a tag in columns 73 to 80."""
    return "".join(
        f"{number * 100:06d} {code}".ljust(72) + tag + "\n"
        for number, code in enumerate(line_list, 1)
    )


def _program(name: str, data_list: list[str], procedure_list: list[str]) -> str:
    """Write a program in fixed format from column 8, without sequence numbers."""
    line_list = [
        "IDENTIFICATION DIVISION.", f"PROGRAM-ID. {name}.", "DATA DIVISION.",
        "WORKING-STORAGE SECTION.", *data_list, "PROCEDURE DIVISION.", *procedure_list,
    ]
    return "".join(f"       {line}\n" for line in line_list)


def _make_project(tmp_path, file_dict: dict[str, str]) -> str:
    """Write the files of a project and return its directory."""
    project = tmp_path / "proj"
    for file_rel, text in file_dict.items():
        path = project / file_rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return str(project)


def _analyse(project_dir: str) -> dict[str, dict]:
    """Build the graph of a project and return get_file_dependencies of every file with a language."""
    project_name = os.path.basename(project_dir)
    graph = {}
    for dep in build_project_dependencies(project_dir):
        rel = copy_path_to_rel(dep["file"][len(project_name) + 1:])
        graph[rel] = [copy_path_to_rel(p[len(project_name) + 1:]) for p in dep["callers"]]
    project_file_set = {f for f in graph if has_language(os.path.join(project_dir, f))}
    caller_map = {f: sorted(graph[f]) for f in project_file_set}
    return {
        file_rel: get_file_dependencies(
            os.path.join(project_dir, file_rel), project_dir,
            project_file_set, caller_map,
        )
        for file_rel in sorted(project_file_set)
    }


def _callee(dependencies: dict) -> set[tuple[str, str, int]]:
    """Return (name, file name of the definition, line) of each callee usage."""
    return {
        (usage["name"], os.path.basename(usage["from"]), line)
        for usage in dependencies["callee_usages"] for line in usage["lines"]
    }


def _caller(dependencies: dict) -> set[tuple[str, str, int]]:
    """Return (name, file name of the user, line) of each caller usage."""
    return {
        (usage["name"], os.path.basename(usage["file"]), line)
        for usage in dependencies["caller_usages"] for line in usage["lines"]
    }


def _source(text: str):
    """Read COBOL text into a CobolSource."""
    return read_cobol_source(text, EXT_TO_LANGUAGE_DICT["cbl"])


# == Right margin =============================================================

MARGIN_LINE_LIST = [
    "IDENTIFICATION DIVISION.", "PROGRAM-ID. MARGIN.", "DATA DIVISION.",
    "WORKING-STORAGE SECTION.", "01  WS-A PIC 9.", "PROCEDURE DIVISION.", "MAIN-PARA.",
    "    MOVE 1 TO WS-A", "    STOP RUN.", "SUB-PARA.", "    DISPLAY WS-A.",
]


def test_a_line_past_column_80_keeps_the_right_margin_of_a_tagged_file():
    text = _fixed(MARGIN_LINE_LIST, "MARGIN  ")
    line_list = text.splitlines()
    line_list[8] += "X"

    definition_list = [(d.name, d.start_line) for d in _source("\n".join(line_list)).definition_list]

    assert definition_list == [
        ("MARGIN", 1), ("WS-A", 5), ("MAIN-PARA", 7), ("SUB-PARA", 10),
    ]
    assert code_text_list(line_list)[8].strip() == "STOP RUN."


def test_code_past_column_72_is_read_where_no_line_has_an_identification_field():
    line_list = [
        "       01  CUSTOMER-RECORD.",
        "           05  CUST-ID                                 PIC 9(09).",
        "           05  CUST-FIRST-NAME                               PIC X(25).",
        "           05  CUST-LAST-NAME                                PIC X(25).",
    ]

    assert [d.name for d in _source("\n".join(line_list)).definition_list] == [
        "CUSTOMER-RECORD", "CUST-ID", "CUST-FIRST-NAME", "CUST-LAST-NAME",
    ]
    assert code_text_list(line_list)[2].endswith("PIC X(25).")


def test_most_lines_decide_the_right_margin():
    tagged = "       MOVE A TO B.".ljust(72) + "00010000"
    long_code = "       MOVE CUSTOMER-FIRST-NAME TO OUTPUT-RECORD-CUSTOMER-FIRST-NAME-FIELD."
    floating_comment = "       MOVE A TO B.   *> a remark that runs on past column 72 and 80"

    assert code_text_list([tagged, tagged, long_code])[2].endswith("NAME-FIE")
    assert code_text_list([tagged, long_code, long_code])[0].endswith("00010000")
    assert code_text_list([floating_comment, long_code])[1].endswith("FIELD.")


def test_a_literal_continued_on_the_next_line_is_not_counted():
    line_list = [
        "       01  A PIC X.".ljust(72) + "00010000",
        "           DISPLAY 'ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZABCDE",
        "      -    'FGHIJ'.",
    ]

    assert code_text_list(line_list)[0].rstrip() == "01  A PIC X."


# == OF / IN qualification ====================================================

def test_qualified_names_go_to_the_copybook_of_their_group(tmp_path):
    project_dir = _make_project(tmp_path, {
        "copy/RECA.cpy": "       01  REC-A.\n           05  CODE-X  PIC X.\n",
        "copy/RECB.cpy": "       01  REC-B.\n           05  CODE-X  PIC X.\n",
        "src/QUAL.cbl": _program("QUAL", ["COPY RECA.", "COPY RECB."], [
            "    MOVE 'A' TO CODE-X OF REC-A",
            "    MOVE 'B' TO CODE-X IN REC-B",
            "    MOVE CODE-X OF REC-A TO CODE-X OF REC-B",
            "    DISPLAY CODE-X",
            "    STOP RUN.",
        ]),
    })

    result = _analyse(project_dir)

    assert {usage for usage in _callee(result["src/QUAL.cbl"]) if usage[0] == "CODE-X"} == {
        ("CODE-X", "RECA.cpy", 8), ("CODE-X", "RECB.cpy", 9),
        ("CODE-X", "RECA.cpy", 10), ("CODE-X", "RECB.cpy", 10),
        # Unqualified: the first COPY statement that brings the name in
        ("CODE-X", "RECA.cpy", 11),
    }
    assert ("CODE-X", "QUAL.cbl", 9) in _caller(result["copy/RECB.cpy"])
    assert ("CODE-X", "QUAL.cbl", 8) not in _caller(result["copy/RECB.cpy"])
    assert ("CODE-X", "QUAL.cbl", 11) not in _caller(result["copy/RECB.cpy"])


def test_a_group_around_a_copy_statement_qualifies_its_names(tmp_path):
    project_dir = _make_project(tmp_path, {
        "copy/FLDA.cpy": "           05  CODE-X  PIC X.\n",
        "copy/FLDB.cpy": "           05  CODE-X  PIC X.\n",
        "copy/FLDC.cpy": "           05  :TAG:-CODE  PIC X.\n",
        "src/MAIN.cbl": _program("MAIN", [
            "01  REC-A.", "    COPY FLDA.", "01  REC-B.", "    COPY FLDB.",
            "01  REC-C.", "    COPY FLDC REPLACING ==:TAG:== BY ==C==.",
            "01  REC-D.", "    05  CODE-X PIC X.",
        ], [
            "    MOVE 'B' TO CODE-X OF REC-B",
            "    MOVE 'D' TO CODE-X OF REC-D",
            "    MOVE 'C' TO C-CODE OF REC-C",
            "    EXEC SQL SELECT A INTO :REC-B.CODE-X FROM T END-EXEC",
            "    STOP RUN.",
        ]),
    })

    result = _analyse(project_dir)["src/MAIN.cbl"]

    assert {usage for usage in _callee(result) if usage[0] in ("CODE-X", "C-CODE")} == {
        ("CODE-X", "FLDB.cpy", 14), ("C-CODE", "FLDC.cpy", 16), ("CODE-X", "FLDB.cpy", 17),
    }
    assert {"lines": [15], "name": "CODE-X"} in _name_line_list(result["same_file_usages"])
    target = next(u for u in result["callee_usages"] if u["name"] == "C-CODE")
    assert target["target_context"].strip() == "05  :TAG:-CODE  PIC X."


def test_a_paragraph_qualified_by_its_section():
    source = _source(_program("SECT", [], [
        "S1 SECTION.", "P1.", "    DISPLAY 1.", "S2 SECTION.", "P1.", "    PERFORM P1 OF S1.",
    ]))

    reference = next(r for r in source.reference_list if r.name == "P1")
    assert (reference.line, reference.qualifier_tuple) == (11, ("S1",))


# == Copybooks named by COPY, whatever their extension ========================

def test_a_named_file_without_a_cobol_extension_is_a_copybook(tmp_path):
    project_dir = _make_project(tmp_path, {
        "dcl/DCLX.dcl": "       01  DCL-X.\n           05  DCL-A  PIC X.\n",
        "copy/INCX.inc": "       01  INC-X.\n           05  INC-A  PIC X.\n       COPY NOEXT.\n",
        "copy/NOEXT": "       01  NOEXT-A  PIC X.\n",
        "copy/UNUSED.inc": "       01  UNUSED-A  PIC X.\n",
        "jcl/SHARE.jcl": "//SHARE JOB\n",
        "copy/SHARE.cpy": "       01  SHARE-A  PIC X.\n",
        "src/OTHER.cbl": _program("OTHER", [
            "    EXEC SQL INCLUDE DCLX END-EXEC.", "COPY 'INCX.inc'.", "COPY SHARE.",
        ], [
            "    MOVE 'A' TO DCL-A INC-A NOEXT-A SHARE-A",
            "    STOP RUN.",
        ]),
    })

    result = _analyse(project_dir)

    assert {language_ext(os.path.join(project_dir, f)) for f in
            ("dcl/DCLX.dcl", "copy/INCX.inc", "copy/NOEXT")} == {"cpy"}
    assert not has_language(os.path.join(project_dir, "copy/UNUSED.inc"))
    assert language_ext(os.path.join(project_dir, "jcl/SHARE.jcl")) == "jcl"
    assert _callee(result["src/OTHER.cbl"]) == {
        ("DCL-A", "DCLX.dcl", 9), ("INC-A", "INCX.inc", 9), ("SHARE-A", "SHARE.cpy", 9),
        ("NOEXT-A", "NOEXT", 9),
    }
    assert [d["name"] for d in result["copy/NOEXT"]["definitions"]] == ["NOEXT-A"]
    assert _caller(result["copy/INCX.inc"]) == {("INC-A", "OTHER.cbl", 9)}
    assert {f: result[f]["language"] for f in
            ("dcl/DCLX.dcl", "copy/NOEXT", "copy/SHARE.cpy", "src/OTHER.cbl")} == {
        "dcl/DCLX.dcl": "cpy", "copy/NOEXT": "cpy", "copy/SHARE.cpy": "cpy",
        "src/OTHER.cbl": "cbl"}


def test_a_named_file_keeps_its_language_only_while_it_is_named(tmp_path):
    file_dict = {
        "copy/INCX.inc": "       01  INC-A  PIC X.\n",
        "src/OTHER.cbl": _program("OTHER", ["COPY 'INCX.inc'."], ["    STOP RUN."]),
    }
    project_dir = _make_project(tmp_path, file_dict)
    build_project_dependencies(project_dir)
    assert has_language(os.path.join(project_dir, "copy/INCX.inc"))

    (tmp_path / "proj" / "src" / "OTHER.cbl").write_text(
        _program("OTHER", [], ["    STOP RUN."]), encoding="utf-8",
    )
    parse_cache_dict.clear()
    build_project_dependencies(project_dir)
    assert not has_language(os.path.join(project_dir, "copy/INCX.inc"))


# == BMS ======================================================================

BMS_SOURCE = """\
* A mapset with extended attributes
MAPSET   DFHMSD TYPE=&SYSPARM,MODE=INOUT,LANG=COBOL,TIOAPFX=YES,       X
               DSATTS=(COLOR,HILIGHT),MAPATTS=(COLOR,HILIGHT)
MAP1     DFHMDI SIZE=(24,80)
CUSTNO   DFHMDF POS=(2,1),LENGTH=8,ATTRB=(UNPROT,IC)
LIST     DFHMDF POS=(3,1),LENGTH=10,OCCURS=5
DAY      DFHMDF POS=(4,1),LENGTH=2,GRPNAME=DATE
MON      DFHMDF POS=(4,3),LENGTH=2,GRPNAME=DATE
         DFHMDF POS=(5,1),LENGTH=5,INITIAL='TITLE'
         DFHMSD TYPE=FINAL
         END
"""


def test_bms_source_gives_the_names_of_its_symbolic_map():
    source = read_bms_source(BMS_SOURCE)
    item_dict = {d.name: (d.level, d.is_group, d.start_line, d.end_line) for d in source.definition_list}

    assert source.copy_name_list == ["MAPSET"]
    assert item_dict["MAP1I"] == (1, True, 4, 9)
    assert item_dict["MAP1O"] == (1, True, 4, 9)
    for suffix, level in (("L", 2), ("F", 2), ("A", 3), ("I", 2), ("C", 2), ("H", 2), ("O", 2)):
        assert item_dict["CUSTNO" + suffix] == (level, False, 5, 5)
    assert "CUSTNOP" not in item_dict
    assert item_dict["LISTI"] == (3, False, 6, 6)
    assert item_dict["DATEI"] == (2, True, 7, 8)
    assert item_dict["DAYI"] == (3, False, 7, 7)
    assert item_dict["MONO"] == (3, False, 8, 8)
    assert "DAYL" not in item_dict


def test_copy_of_a_mapset_goes_to_its_bms_source(tmp_path):
    screen = _program("SCREEN", ["COPY MAPSET."], ["    MOVE SPACES TO CUSTNOI", "    STOP RUN."])
    project_dir = _make_project(tmp_path, {"bms/MAPSET.bms": BMS_SOURCE, "src/SCREEN.cbl": screen})

    result = _analyse(project_dir)

    assert _callee(result["src/SCREEN.cbl"]) == {("CUSTNOI", "MAPSET.bms", 7)}
    assert _caller(result["bms/MAPSET.bms"]) == {("CUSTNOI", "SCREEN.cbl", 7)}
    assert {d["name"]: d["name_line"] for d in result["bms/MAPSET.bms"]["definitions"]}["CUSTNOI"] == 5


def test_a_generated_map_comes_before_the_bms_source(tmp_path):
    screen = _program("SCREEN", ["COPY MAPSET."], ["    MOVE SPACES TO CUSTNOI", "    STOP RUN."])
    project_dir = _make_project(tmp_path, {
        "src/MAPSET.bms": BMS_SOURCE,
        "cpy-bms/MAPSET.CPY": "       01  MAP1I.\n           02  CUSTNOI  PIC X(8).\n",
        "src/SCREEN.cbl": screen,
    })

    assert _callee(_analyse(project_dir)["src/SCREEN.cbl"]) == {("CUSTNOI", "MAPSET.CPY", 7)}


# == Extensions in any case ===================================================

def test_cobol_and_bms_extensions_in_any_case(tmp_path):
    assert [language_ext(path) for path in ("A.Cbl", "A.cOB", "A.Cpy", "A.CBL", "A.Bms", "A.Py")] == [
        "cbl", "cob", "cpy", "cbl", "bms", "",
    ]
    project_dir = _make_project(tmp_path, {
        "src/MIXED.Cbl": _program("MIXED", ["COPY RECM."], ["MIXED-PARA.", "    DISPLAY REC-M."]),
        "copy/RECM.Cpy": "       01  REC-M  PIC X.\n",
    })

    result = _analyse(project_dir)

    assert [d["name"] for d in result["src/MIXED.Cbl"]["definitions"]] == ["MIXED", "MIXED-PARA"]
    assert _callee(result["src/MIXED.Cbl"]) == {("REC-M", "RECM.Cpy", 8)}


# == Level, group item and name line ==========================================

def test_level_group_and_name_line_of_the_definitions(tmp_path):
    project_dir = _make_project(tmp_path, {
        "copy/FLDS.cpy": "           05  F1  PIC X.\n",
        "src/MAIN.cbl": (
            "       IDENTIFICATION DIVISION.\n"
            "       PROGRAM-ID.\n"
            "           MAIN.\n"
            "       DATA DIVISION.\n"
            "       WORKING-STORAGE SECTION.\n"
            "       01  REC.\n"
            "           05  CODE-A PIC X.\n"
            "               88  CODE-OK VALUE 'Y'.\n"
            "           05\n"
            "               CODE-B PIC X.\n"
            "       01  FLAG PIC X.\n"
            "           88  FLAG-ON VALUE 'Y'.\n"
            "       01  WITH-COPY.\n"
            "           COPY FLDS.\n"
            "       01  NUM BINARY-LONG.\n"
            "       66  ALIAS RENAMES FLAG.\n"
            "       PROCEDURE DIVISION.\n"
            "       MAIN-PARA.\n"
            "           STOP RUN.\n"
        ),
    })

    definition_dict = {
        d["name"]: (d["name_line"], d.get("level"), d.get("is_group"))
        for d in _analyse(project_dir)["src/MAIN.cbl"]["definitions"]
    }

    assert definition_dict == {
        "MAIN": (3, None, None),
        "REC": (6, 1, True),
        "CODE-A": (7, 5, False),
        "CODE-OK": (8, 88, False),
        "CODE-B": (10, 5, False),
        "FLAG": (11, 1, False),
        "FLAG-ON": (12, 88, False),
        "WITH-COPY": (13, 1, True),
        "NUM": (15, 1, False),
        "ALIAS": (16, 66, False),
        "MAIN-PARA": (18, None, None),
    }


# == Cases found in review ====================================================

def test_the_outer_group_holds_a_copy_statement_of_an_inner_group(tmp_path):
    project_dir = _make_project(tmp_path, {
        "CUSTA.cpy": "              10 CUST-ID PIC X.\n",
        "CUSTB.cpy": "              10 CUST-ID PIC X.\n",
        "MAIN.cbl": _program("MAIN", [
            "01 WS-AREA.", "   05 WS-OLD.", "      COPY CUSTA.", "   05 WS-NEW.", "      COPY CUSTB.",
        ], [
            "    MOVE 'X' TO CUST-ID OF WS-NEW OF WS-AREA.",
            "    MOVE 'X' TO CUST-ID IN WS-NEW.",
            "    STOP RUN.",
        ]),
    })

    result = _analyse(project_dir)["MAIN.cbl"]

    assert _callee(result) == {("CUST-ID", "CUSTB.cpy", 11), ("CUST-ID", "CUSTB.cpy", 12)}
    line_dict = {d["name"]: (d["start_line"], d["end_line"]) for d in result["definitions"]}
    assert line_dict["WS-AREA"] == (5, 9)
    assert line_dict["WS-OLD"] == (6, 7)


def test_a_copy_statement_on_the_line_of_its_group(tmp_path):
    project_dir = _make_project(tmp_path, {
        "CUSTA.cpy": "           05 CUST-ID PIC X.\n",
        "CUSTB.cpy": "           05 CUST-ID PIC X.\n",
        "MAIN.cbl": _program("MAIN", ["01 OLD-REC. COPY CUSTA.", "01 NEW-REC. COPY CUSTB."], [
            "    MOVE CUST-ID OF NEW-REC TO CUST-ID OF OLD-REC.",
            "    STOP RUN.",
        ]),
    })

    result = _analyse(project_dir)["MAIN.cbl"]

    assert _callee(result) == {("CUST-ID", "CUSTB.cpy", 8), ("CUST-ID", "CUSTA.cpy", 8)}
    assert {d["name"]: d["is_group"] for d in result["definitions"] if "level" in d} == {
        "OLD-REC": True, "NEW-REC": True,
    }


def test_an_item_with_a_value_or_without_a_name_does_not_take_the_next_copy_statement():
    source = _source(_program("M", [
        "01 WIDTHS.", "   02 FILLER VALUE '50'.", "COPY ROWS.",
        "01 MENU1.", "   03 PIC X(80) VALUE 'F1'.", "COPY MORE.",
        "01 CLEAR-REC.", "COPY FIELDS.",
    ], ["    STOP RUN."]))

    assert {d.name: (d.end_line, d.is_group) for d in source.definition_list if d.level} == {
        "WIDTHS": (6, True), "MENU1": (9, True), "CLEAR-REC": (12, True),
    }


def test_a_text_file_of_another_kind_is_not_a_copybook_by_its_stem(tmp_path):
    project_dir = _make_project(tmp_path, {
        "DFHAID.md": "# dfhaid notes\nPF keys.\n",
        "cfg/SETTINGS.yaml": "database:\n  host: localhost\n",
        "doc/NOTES.md": "# notes\n",
        "MAIN.cbl": _program("MAIN", ["COPY DFHAID.", "COPY SETTINGS.", "COPY 'NOTES.md'."], [
            "    STOP RUN.",
        ]),
    })

    _analyse(project_dir)

    assert not has_language(os.path.join(project_dir, "DFHAID.md"))
    assert not has_language(os.path.join(project_dir, "cfg/SETTINGS.yaml"))
    assert language_ext(os.path.join(project_dir, "doc/NOTES.md")) == "cpy"


def test_a_bms_source_comes_before_a_text_file_of_the_same_name(tmp_path):
    screen = _program("SCREEN", ["COPY MAPSET."], ["    MOVE SPACES TO CUSTNOI", "    STOP RUN."])
    project_dir = _make_project(tmp_path, {
        "bms/MAPSET.bms": BMS_SOURCE,
        "src/MAPSET.txt": "       01  MAPSET-NOTE  PIC X.\n",
        "src/SCREEN.cbl": screen,
    })

    assert _callee(_analyse(project_dir)["src/SCREEN.cbl"]) == {("CUSTNOI", "MAPSET.bms", 7)}


def test_code_that_ends_at_column_72_before_an_identification_field():
    code = "000400     MOVE WS-CUSTOMER-IDENTIFIER-NUMBER TO WS-OUT-CUSTOMER-NUM"
    code = code + "X" * (71 - len(code)) + "."
    line_list = ["000100 PROCEDURE DIVISION.", code + "CHG00001", "000500     MOVE A TO B."]

    assert len(code) == 72
    assert code_text_list(line_list)[1].endswith("XX.")


def test_a_literal_continued_from_a_line_past_column_80_is_read_whole():
    line_list = [
        "       PROCEDURE DIVISION.",
        "           DISPLAY 'A VERY LONG MESSAGE WRITTEN FOR A WIDE EDITOR WITHOUT ANY MARGIN AT ALL",
        "      -    ' AND IT GOES ON'.",
    ]

    assert code_text_list(line_list)[1].endswith("AT ALL")


def test_many_qualified_names_of_the_same_field(tmp_path):
    count = 200
    data_list = [line for i in range(count) for line in (f"01 GRP-{i}.", "   05 FLD PIC X.")]
    procedure_list = [f"    MOVE FLD OF GRP-{i} TO FLD OF GRP-{count - 1 - i}" for i in range(count)]
    project_dir = _make_project(tmp_path, {
        "BIG.cbl": _program("BIG", data_list, [*procedure_list, "    STOP RUN."]),
    })

    result = _analyse(project_dir)["BIG.cbl"]

    # Each FLD is a definition of its own: the two lines that name it are one entry
    first_line = 4 + 2 * count + 2
    field_usage_list = [usage for usage in result["same_file_usages"] if usage["name"] == "FLD"]
    assert len(field_usage_list) == count
    assert len({usage["target_start_line"] for usage in field_usage_list}) == count
    assert [first_line, first_line + count - 1] in [usage["lines"] for usage in field_usage_list]


# == Names a program shares with its data and procedures ======================

def test_a_data_name_is_not_linked_to_the_program_of_the_same_name(tmp_path):
    line_list = [
        "IDENTIFICATION DIVISION.", "PROGRAM-ID. PAYROLL.", "ENVIRONMENT DIVISION.",
        "INPUT-OUTPUT SECTION.", "FILE-CONTROL.", "    SELECT PAYROLL ASSIGN TO 'PAY.DAT'.",
        "DATA DIVISION.", "FILE SECTION.", "FD  PAYROLL.", "01  PAY-REC PIC X(80).",
        "PROCEDURE DIVISION.", "MAIN-PARA.", "    OPEN INPUT PAYROLL.", "    READ PAYROLL.",
        "    CLOSE PAYROLL.", "    STOP RUN.",
    ]
    project_dir = _make_project(tmp_path, {
        "PAYROLL.cbl": "".join(f"       {line}\n" for line in line_list),
    })

    result = _analyse(project_dir)["PAYROLL.cbl"]

    assert {"lines": [6, 13, 14, 15], "name": "PAYROLL"} in _name_line_list(result["same_file_usages"])


def test_a_file_with_statements_only_is_a_copybook_by_its_stem(tmp_path):
    project_dir = _make_project(tmp_path, {
        "copy/ERRPARA.inc": "       ERR-PARA.\n           DISPLAY \"ERR\".\n",
        "MAIN.cbl": _program("MAIN", [], [
            "MAIN-PARA.", "    PERFORM ERR-PARA.", "    STOP RUN.", "COPY ERRPARA.",
        ]),
    })

    result = _analyse(project_dir)

    assert language_ext(os.path.join(project_dir, "copy/ERRPARA.inc")) == "cpy"
    assert _callee(result["MAIN.cbl"]) == {("ERR-PARA", "ERRPARA.inc", 7)}
    assert _caller(result["copy/ERRPARA.inc"]) == {("ERR-PARA", "MAIN.cbl", 7)}


# == Descriptions of files ====================================================

def test_a_name_qualified_by_a_file_goes_to_the_record_of_that_file(tmp_path):
    line_list = [
        "IDENTIFICATION DIVISION.", "PROGRAM-ID. MAIN.", "DATA DIVISION.", "FILE SECTION.",
        "FD  A-FILE.", "    COPY RECA.", "FD  B-FILE.", "    COPY RECB.", "SD  C-FILE.",
        "01  C-REC.", "    05  REC-ID PIC 9(4).", "WORKING-STORAGE SECTION.",
        "01  WS-ID PIC 9(4).", "PROCEDURE DIVISION.", "    MOVE REC-ID OF B-FILE TO WS-ID.",
        "    MOVE REC-ID OF C-FILE TO WS-ID.", "    MOVE REC-ID OF A-FILE TO WS-ID.",
    ]
    project_dir = _make_project(tmp_path, {
        "RECA.cpy": "       01  REC-A.\n           05  REC-ID PIC 9(4).\n",
        "RECB.cpy": "       01  REC-B.\n           05  REC-ID PIC 9(4).\n",
        "MAIN.cbl": "".join(f"       {line}\n" for line in line_list),
    })

    result = _analyse(project_dir)["MAIN.cbl"]

    assert _callee(result) == {("REC-ID", "RECB.cpy", 15), ("REC-ID", "RECA.cpy", 17)}
    assert {"lines": [16], "name": "REC-ID"} in _name_line_list(result["same_file_usages"])
    assert {
        d["name"]: (d["start_line"], d["end_line"]) for d in result["definitions"]
        if d["type"] == "file_description_entry"
    } == {"A-FILE": (5, 6), "B-FILE": (7, 8), "C-FILE": (9, 11)}


# == CALL =====================================================================

def test_a_call_is_named_after_the_program_it_leads_to(tmp_path):
    program = "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. {}.\n" \
              "       PROCEDURE DIVISION.\n           GOBACK.\n"
    project_dir = _make_project(tmp_path, {
        "BYNAME.cbl": program.format("OTHERNM"),
        "SUB1.cbl": program.format("Sub1"),
        "MAIN.cbl": _program("MAIN", [], [
            "    CALL \"BYNAME\".", "    CALL \"SUB1\".", "    CALL \"sub1\".", "    CALL 'Sub1'.",
            "    STOP RUN.",
        ]),
    })

    result = _analyse(project_dir)

    assert _callee(result["MAIN.cbl"]) == {
        ("OTHERNM", "BYNAME.cbl", 6), ("Sub1", "SUB1.cbl", 7), ("Sub1", "SUB1.cbl", 8),
        ("Sub1", "SUB1.cbl", 9),
    }
    assert all("PROGRAM-ID" in usage["target_context"] for usage in result["MAIN.cbl"]["callee_usages"])
    assert _caller(result["BYNAME.cbl"]) == {("OTHERNM", "MAIN.cbl", 6)}
    assert [usage["lines"] for usage in result["SUB1.cbl"]["caller_usages"]] == [[7, 8, 9]]


# == Choice among the files a COPY statement can name ==========================

def test_a_cobol_extension_comes_before_a_file_without_an_extension(tmp_path):
    project_dir = _make_project(tmp_path, {
        "copy/CUSTREC": "       01  CUST-ID PIC 9.\n",
        "copy/CUSTREC.cpy": "       01  CUST-ID PIC 9.\n",
        "lib/DATEREC": "       01  DATE-ID PIC 9.\n",
        "lib/DATEREC.cpy": "       01  DATE-ID PIC 9.\n",
        "MAIN.cbl": _program("MAIN", ["COPY CUSTREC.", "COPY 'lib/DATEREC'."], [
            "    MOVE 1 TO CUST-ID DATE-ID.",
        ]),
    })

    result = _analyse(project_dir)

    assert _callee(result["MAIN.cbl"]) == {
        ("CUST-ID", "CUSTREC.cpy", 8), ("DATE-ID", "DATEREC.cpy", 8),
    }
    assert not has_language(os.path.join(project_dir, "copy/CUSTREC"))


# == Index names, copybooks of copybooks, names with a part in parentheses =====

def test_an_index_name_is_a_definition(tmp_path):
    project_dir = _make_project(tmp_path, {
        "MAIN.cbl": _program("MAIN", [
            "01  WS-REC.", "    05  WS-TAB PIC X OCCURS 10 TIMES", "        INDEXED BY WS-IX WS-IY.",
        ], ["    SET WS-IX TO 1.", "    SET WS-IY UP BY 1.", "    MOVE 'A' TO WS-TAB (WS-IX)."]),
    })

    result = _analyse(project_dir)["MAIN.cbl"]

    assert [
        (d["name"], d["start_line"], d["end_line"], d["name_line"]) for d in result["definitions"]
        if d["type"] == "occurs_indexed"
    ] == [("WS-IX", 6, 7, 7), ("WS-IY", 6, 7, 7)]
    assert all("level" not in d for d in result["definitions"] if d["type"] == "occurs_indexed")
    assert {"lines": [9, 11], "name": "WS-IX"} in _name_line_list(result["same_file_usages"])
    assert {"lines": [10], "name": "WS-IY"} in _name_line_list(result["same_file_usages"])


def test_the_names_of_a_copybook_of_a_copybook_are_linked(tmp_path):
    project_dir = _make_project(tmp_path, {
        "NESTED.cpy": "           05  NEST-A PIC X.\n           COPY DEEP.\n",
        "DEEP.cpy": "           05  DEEP-A PIC X.\n           COPY NESTED.\n",
        "MAIN.cbl": _program("MAIN", ["01  WS-GRP.", "    COPY NESTED.", "01  WS-B PIC X."], [
            "    MOVE DEEP-A TO NEST-A.", "    MOVE DEEP-A OF WS-GRP TO WS-B.",
        ]),
    })

    result = _analyse(project_dir)

    assert {usage for usage in _callee(result["MAIN.cbl"])} == {
        ("DEEP-A", "DEEP.cpy", 9), ("NEST-A", "NESTED.cpy", 9), ("DEEP-A", "DEEP.cpy", 10),
    }
    assert _caller(result["DEEP.cpy"]) == {("DEEP-A", "MAIN.cbl", 9), ("DEEP-A", "MAIN.cbl", 10)}


def test_an_item_whose_name_has_a_part_in_parentheses_has_no_name():
    source = _source(_program("TAGS", [
        "01  WS-REC.", "    05  (PFX)-ID PIC X.", "    05  WS-(SFX) PIC X.", "    05  WS-C PIC X.",
    ], ["    STOP RUN."]))

    assert [d.name for d in source.definition_list] == ["TAGS", "WS-REC", "WS-C"]


# == Free format whose lines fit the fixed-format columns ======================

def test_a_free_format_copybook_whose_lines_fit_the_fixed_columns(tmp_path):
    main_line_list = [
        "identification division.", "program-id. main.", "data division.",
        "working-storage section.", "COPY CUSTREC.", "COPY DATEREC.", "COPY ITEMREC.",
        "01 ws-total pic 9(4).", "procedure division.", "main-para.",
        "    move ws-id to ws-total", "    move ws-year to ws-total",
        "    move ws-item to ws-total", "    goback.",
    ]
    project_dir = _make_project(tmp_path, {
        "MAIN.cbl": "\n".join(main_line_list) + "\n",
        # Column 7 holds "-", then a blank
        "CUSTREC.cpy": "01  WS-CUSTOMER.\n    05  WS-ID PIC 9(4).\n",
        # Column 7 holds "D", then a blank
        "DATEREC.cpy": "01 WS-DATE.\n    05 WS-YEAR PIC 9(4).\n",
        # Column 7 holds a blank on every line
        "ITEMREC.cpy": "    05 WS-ITEM PIC 9(4).\n    05 WS-ITEM-B PIC 9(4).\n",
    })

    result = _analyse(project_dir)

    assert [d["name"] for d in result["CUSTREC.cpy"]["definitions"]] == ["WS-CUSTOMER", "WS-ID"]
    assert [d["name"] for d in result["DATEREC.cpy"]["definitions"]] == ["WS-DATE", "WS-YEAR"]
    assert [d["name"] for d in result["ITEMREC.cpy"]["definitions"]] == ["WS-ITEM", "WS-ITEM-B"]
    assert _callee(result["MAIN.cbl"]) == {
        ("WS-ID", "CUSTREC.cpy", 11), ("WS-YEAR", "DATEREC.cpy", 12),
        ("WS-ITEM", "ITEMREC.cpy", 13),
    }


def test_a_sequence_number_or_a_tag_keeps_the_fixed_format():
    line_list = [
        "CBL LIB,APOST",
        "000100 01  WS-A PIC X.",
        " 00200     05  WS-B PIC X.",
        "CHG001 01  WS-C PIC X.",
        "*  ab  note written from column 1",
        "   100 01  WS-D PIC X.",
        "    12*    a comment line",
        "000300-    'CONTINUED'.",
    ]

    assert [text.strip() for text in code_text_list(line_list)] == [
        "", "01  WS-A PIC X.", "05  WS-B PIC X.", "01  WS-C PIC X.",
        "note written from column 1", "01  WS-D PIC X.", "", "'CONTINUED'.",
    ]


@pytest.mark.parametrize("step", [1, 10, 100])
def test_line_numbers_aligned_to_the_right_keep_the_fixed_format(step):
    line_list = [
        f"{(index + 1) * step:6d} {line}"
        for index, line in enumerate(MARGIN_LINE_LIST)
    ]

    source = _source("\n".join(line_list) + "\n")

    assert [(d.name, d.start_line) for d in source.definition_list] == [
        ("MARGIN", 1), ("WS-A", 5), ("MAIN-PARA", 7), ("SUB-PARA", 10),
    ]


@pytest.mark.parametrize("line_list", [
    ["    10 01  WS-A.", "    20     05 WS-B PIC X."],
    ["    10 MAIN-PARA.", "    20     MOVE A TO B", "    30     MOVE C TO D", "    40     GOBACK.",
     "    50 SUB-PARA.", "    60     EXIT."],
])
def test_line_numbers_aligned_to_the_right_in_a_copybook(line_list):
    assert [text.strip() for text in code_text_list(line_list)] == [
        line[7:].strip() for line in line_list
    ]


@pytest.mark.parametrize("line_list", [
    ["    01 WS-A.", "    05 WS-B.", "    10 WS-C PIC X."],
    ["    01 WS-A.", "    05 WS-B PIC X.", "    05 WS-C PIC X.", "       MOVE A TO B."],
])
def test_level_numbers_in_columns_1_to_6_of_a_free_format_copybook(line_list):
    assert code_text_list(line_list) == line_list


def test_a_continuation_on_the_first_line_with_code_is_free_format():
    line_list = ["      * a comment", "DISPLAY-PARA.", "    DISPLAY 'A'."]

    assert code_text_list(line_list) == ["", "DISPLAY-PARA.", "    DISPLAY 'A'."]


def test_a_library_written_as_a_directory_path(tmp_path):
    project_dir = _make_project(tmp_path, {
        "alt/RECA.cpy": "       01  ALT-A PIC X.\n",
        "lib/dir/RECA.cpy": "       01  LIB-A PIC X.\n",
        "dir/RECB.cpy": "       01  ALT-B PIC X.\n",
        "lib/dir/RECB.cpy": "       01  LIB-B PIC X.\n",
        "src/MAIN.cbl": _program("MAIN", [
            "COPY RECA OF \"lib/dir\".", "COPY RECB IN \"lib\\dir\".",
        ], ["    MOVE 'A' TO LIB-A LIB-B", "    STOP RUN."]),
    })

    result = _analyse(project_dir)

    assert {usage["from"] for usage in result["src/MAIN.cbl"]["callee_usages"]} == {
        "lib/dir/RECA.cpy", "lib/dir/RECB.cpy",
    }


@pytest.mark.parametrize("whole_file_rel, stem_file_rel", [
    ("src/A.cbl", "src/B.cbl"), ("src/B.cbl", "src/A.cbl"),
])
def test_a_file_named_whole_by_one_copy_is_not_the_copybook_of_another(
    tmp_path, whole_file_rel, stem_file_rel,
):
    project_dir = _make_project(tmp_path, {
        "src/CUSTREC.txt": "Notes on the customer record.\n",
        "copy/CUSTREC": "       01  CUST-REC.\n           05  CUST-ID PIC 9.\n",
        whole_file_rel: _program("WHOLE", ["COPY \"CUSTREC.txt\"."], ["    STOP RUN."]),
        stem_file_rel: _program("STEM", ["COPY CUSTREC."], ["    MOVE 1 TO CUST-ID", "    STOP RUN."]),
    })

    result = _analyse(project_dir)

    assert language_ext(os.path.join(project_dir, "src/CUSTREC.txt")) == "cpy"
    assert _callee(result[stem_file_rel]) == {("CUST-ID", "CUSTREC", 7)}
    file_index_cache_dict.clear()
    reference_target_cache_dict.clear()
    assert _callee(_analyse_again(project_dir, stem_file_rel)) == {("CUST-ID", "CUSTREC", 7)}


def _analyse_again(project_dir: str, file_rel: str) -> dict:
    """Return get_file_dependencies of a file with the languages the last analysis recorded."""
    file_set = {
        os.path.relpath(os.path.join(directory, name), project_dir)
        for directory, _, name_list in os.walk(project_dir) for name in name_list
    }
    project_file_set = {f for f in file_set if has_language(os.path.join(project_dir, f))}
    return get_file_dependencies(
        os.path.join(project_dir, file_rel), project_dir, project_file_set,
        {f: [] for f in project_file_set},
    )


def _bms_line(text: str, mark: str = " ") -> str:
    """Return a BMS source line with a mark in column 72."""
    return text.ljust(71) + mark + "\n"


def test_a_remark_continued_after_the_operands_is_no_operand():
    source = read_bms_source(
        _bms_line("SET      DFHMSD TYPE=MAP,MODE=INOUT,LANG=COBOL")
        + _bms_line("MAP1     DFHMDI SIZE=(24,80)")
        + _bms_line("MO       DFHMDF POS=(1,1),LENGTH=2,GRPNAME=DATE    MONTH OF", "X")
        + _bms_line("               THE DATE")
        + _bms_line("DY       DFHMDF POS=(1,4),LENGTH=2,GRPNAME=DATE")
        + _bms_line("ROW      DFHMDF POS=(2,1),LENGTH=2,OCCURS=2  ONE ROW", "X")
        + _bms_line("               PER LINE")
        + _bms_line("COL      DFHMDF POS=(3,1),LENGTH=2,", "X")
        + _bms_line("               OCCURS=3")
        + _bms_line("         DFHMSD TYPE=FINAL")
    )
    item_dict = {d.name: (d.level, d.is_group, d.start_line, d.end_line) for d in source.definition_list}

    assert item_dict["DATEI"] == (2, True, 3, 5)
    assert item_dict["MOI"] == (3, False, 3, 4)
    assert item_dict["DYI"] == (3, False, 5, 5)
    assert not any(name.startswith("DATETHE") for name in item_dict)
    assert item_dict["ROWI"] == (3, False, 6, 7)
    assert item_dict["COLI"] == (3, False, 8, 9)
