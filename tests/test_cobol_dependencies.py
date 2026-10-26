"""COBOL files depend on each other through COPY, CALL and EXEC statements.

A COPY statement leads to the copybook of that name, a CALL statement to the file that
defines the program. build_project_dependencies() adds an edge for each of them, and
get_file_dependencies() lists the definitions of a file and each usage with the
definition it names. Fixed-format and free-format source, names outside ASCII and
statements the grammar does not read are covered.
"""

import json
import os

import pytest

from codetwine.cobol_file_index import file_index_cache_dict, replace_name
from codetwine.config.settings import EXT_TO_LANGUAGE_DICT
from codetwine.extractors.cobol_source import read_cobol_source
from codetwine.extractors.definition_source import extract_callee_source
from codetwine.extractors.dependency_graph import (
    build_project_dependencies,
)
from codetwine.extractors.imports import cobol_module, cobol_module_part
from codetwine.file_analyzer import get_file_dependencies
from codetwine.parsers.cobol_format import split_cobol_source
from codetwine.parsers.ts_parser import parse_cache_dict
from codetwine.config.settings import has_language
from codetwine.utils.file_utils import copy_path_to_rel

MAIN_SOURCE = """\
000100 ID DIVISION.
000200 PROGRAM-ID. 顧客管理.
000300 ENVIRONMENT DIVISION.
000400 INPUT-OUTPUT SECTION.
000500 FILE-CONTROL.
000600     SELECT CUST-FILE ASSIGN TO "CUST.DAT".
000700 DATA DIVISION.
000800 FILE SECTION.
000900 FD  CUST-FILE.
001000     COPY CUSTREC OF COPYLIB.
001100 WORKING-STORAGE SECTION.
001200 01  WS-PGM             PIC X(8) VALUE 'TAXCALC'.
001300 01  顧客レコード.
001400     05  顧客番号       PIC 9(6).
001500     05  WS_NAME        PIC X(20) VALUE "山田太郎山田太郎".       00150000
001600         88  名前なし   VALUE SPACES.
001700 01  C-MAX CONSTANT AS 10.
001800 78  C-MIN VALUE 1.
001900     EXEC SQL INCLUDE SQLCA END-EXEC.
002000     COPY DATEFMT REPLACING ==:PFX:== BY ==WS==.
002100 LINKAGE SECTION.
002200 PROCEDURE DIVISION.
002300 初期処理 SECTION.
002400 MAIN-PARA.
002500     MOVE 'RPTPRINT' TO WS-PGM. MOVE WS-YEAR TO 顧客番号.
002600     IF 顧客番号 NOT= 0 AND 顧客番号 <> 9
002700        PERFORM 終了処理 THRU 終了処理-EXIT
002800     END-IF.
002900     CALL WS-PGM USING 顧客レコード.
003000     CALL "SUBPROG" USING BY CONTENT WS_NAME.
003100     EXEC CICS XCTL PROGRAM(WS-PGM) COMMAREA(顧客レコード) END-EXEC.
003200     EXEC SQL SELECT A INTO :LIB-CUST-ID FROM T END-EXEC.
003300     ENTRY 'ALTENTRY' USING 顧客レコード.
003400     GO TO 終了処理.
003500 終了処理.
003600     DISPLAY 'ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZABCDE
003700-    'FGHIJ' 顧客番号.
003800 終了処理-EXIT.
003900     EXIT.
004000 END PROGRAM 顧客管理.
"""

APP_FILE_DICT = {
    "src/MAIN.CBL": MAIN_SOURCE,
    "src/taxcalc.cbl": """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. TAXCALC.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-RATE            PIC V99 VALUE .08.
       LINKAGE SECTION.
       01  LK-AMOUNT          PIC 9(7)V99.
       PROCEDURE DIVISION USING LK-AMOUNT.
       CALC-TAX.
           COMPUTE LK-AMOUNT = LK-AMOUNT * (1 + WS-RATE).
           GOBACK.
""",
    "src/report.cob": """\
>>SOURCE FORMAT IS FREE
identification division.
program-id. rptprint.
data division.
working-storage section.
copy "DATEFMT.cpy".
01 ws-long-line pic x(200) value "this literal is long enough to go past the right margin of a fixed-format line".
procedure division.
main-para.
    display ws-long-line *> inline comment
    call "taxcalc" using ws-long-line
    perform 終了処理
    goback.
終了処理.
    exit.
""",
    "copy/CUSTREC.cpy": """\
      * customer record
       01  CUST-RECORD.
           05  CUST-ID        PIC 9(6).
           05  CUST-NAME      PIC X(30).
""",
    "COPYLIB/CUSTREC.cpy": """\
       01  LIB-CUST-RECORD.
           05  LIB-CUST-ID    PIC 9(6).
""",
    "copy/DATEFMT.CPY": """\
       01  :PFX:-DATE.
           05  :PFX:-YEAR     PIC 9(4).
           05  :PFX:-MONTH    PIC 9(2).
""",
    "copy/ERRPARA.cpy": """\
       ERR-PARA.
           DISPLAY "ERROR".
           STOP RUN.
""",
}


@pytest.fixture(autouse=True)
def _clear_cache():
    """Start each test without parse results and file indexes of another project."""
    parse_cache_dict.clear()
    file_index_cache_dict.clear()


def _make_project(tmp_path, file_dict: dict[str, str] = APP_FILE_DICT) -> str:
    """Write the files of a COBOL project and return its directory."""
    project = tmp_path / "bank"
    for file_rel, text in file_dict.items():
        path = project / file_rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return str(project)


def _graph(project_dir: str) -> dict[str, dict]:
    """Build the dependency graph keyed by the original relative path."""
    project_name = os.path.basename(project_dir)
    graph = {}
    for dep in build_project_dependencies(project_dir):
        rel = copy_path_to_rel(dep["file"][len(project_name) + 1:])
        graph[rel] = {
            key: {copy_path_to_rel(p[len(project_name) + 1:]) for p in dep[key]}
            for key in ("callers", "callees")
        }
    return graph


def _file_dependencies(project_dir: str, file_rel: str) -> dict:
    """Run get_file_dependencies for one file of the project."""
    graph = _graph(project_dir)
    project_file_set = {f for f in graph if has_language(f)}
    caller_map = {f: sorted(graph[f]["callers"]) for f in project_file_set}
    return get_file_dependencies(
        os.path.join(project_dir, file_rel), project_dir,
        project_file_set, caller_map,
    )


def _source(text: str):
    """Read COBOL text into a CobolSource."""
    return read_cobol_source(text, EXT_TO_LANGUAGE_DICT["cbl"])


def test_import_edges(tmp_path):
    graph = _graph(_make_project(tmp_path))

    # COPY ... OF library, COPY ... REPLACING, a CALL of a data item given two
    # literals, and EXEC CICS XCTL PROGRAM(data item)
    assert graph["src/MAIN.CBL"]["callees"] == {
        "COPYLIB/CUSTREC.cpy", "copy/DATEFMT.CPY", "src/report.cob", "src/taxcalc.cbl",
    }
    # A free-format file; the copybook name and the program name in another case
    assert graph["src/report.cob"]["callees"] == {"copy/DATEFMT.CPY", "src/taxcalc.cbl"}
    assert graph["src/taxcalc.cbl"]["callers"] == {"src/MAIN.CBL", "src/report.cob"}
    assert graph["copy/CUSTREC.cpy"]["callers"] == set()


def test_definitions_of_a_program(tmp_path):
    dependencies = _file_dependencies(_make_project(tmp_path), "src/MAIN.CBL")
    definition_list = [
        (d["name"], d["type"], d["start_line"], d["end_line"])
        for d in dependencies["definitions"]
    ]

    assert definition_list == [
        ("顧客管理", "program_definition", 1, 40),
        ("CUST-FILE", "file_description_entry", 9, 10),
        ("WS-PGM", "data_description", 12, 12),
        ("顧客レコード", "data_description", 13, 16),
        ("顧客番号", "data_description", 14, 14),
        ("WS_NAME", "data_description", 15, 16),
        ("名前なし", "data_description", 16, 16),
        ("C-MAX", "data_description", 17, 17),
        ("C-MIN", "data_description", 18, 18),
        ("初期処理", "section_header", 23, 39),
        ("MAIN-PARA", "paragraph_header", 24, 34),
        ("ALTENTRY", "entry_statement", 33, 33),
        ("終了処理", "paragraph_header", 35, 37),
        ("終了処理-EXIT", "paragraph_header", 38, 39),
    ]
    context = dependencies["definitions"][3]["context"]
    assert context.splitlines()[0] == "001300 01  顧客レコード."
    assert len(context.splitlines()) == 4


def test_usages_of_a_program(tmp_path):
    dependencies = _file_dependencies(_make_project(tmp_path), "src/MAIN.CBL")
    callee_dict = {u["name"]: u for u in dependencies["callee_usages"]}
    same_file_dict = {u["name"]: u["lines"] for u in dependencies["same_file_usages"]}

    # A name of the copybook as REPLACING writes it, with the definition in the copybook
    assert callee_dict["WS-YEAR"]["from"] == "copy/DATEFMT.CPY"
    assert callee_dict["WS-YEAR"]["lines"] == [25]
    assert ":PFX:-YEAR" in callee_dict["WS-YEAR"]["target_context"]
    # A name used inside an EXEC SQL block
    assert callee_dict["LIB-CUST-ID"]["from"] == "COPYLIB/CUSTREC.cpy"
    assert callee_dict["LIB-CUST-ID"]["lines"] == [32]
    # The literals of the VALUE clause and of MOVE, on the lines that call the data item
    assert callee_dict["TAXCALC"]["from"] == "src/taxcalc.cbl"
    assert callee_dict["TAXCALC"]["lines"] == [29, 31]
    assert callee_dict["rptprint"]["from"] == "src/report.cob"
    assert "PROGRAM-ID. TAXCALC." in callee_dict["TAXCALC"]["target_context"]
    assert "SUBPROG" not in callee_dict

    assert same_file_dict["顧客番号"] == [25, 26, 37]
    assert same_file_dict["終了処理"] == [27, 34]
    assert same_file_dict["終了処理-EXIT"] == [27]
    assert same_file_dict["顧客レコード"] == [29, 31, 33]
    assert same_file_dict["WS_NAME"] == [30]
    assert same_file_dict["CUST-FILE"] == [6]


def test_caller_usages(tmp_path):
    project_dir = _make_project(tmp_path)

    program = _file_dependencies(project_dir, "src/taxcalc.cbl")
    assert {(u["name"], u["file"], tuple(u["lines"])) for u in program["caller_usages"]} == {
        ("TAXCALC", "src/MAIN.CBL", (29, 31)),
        ("TAXCALC", "src/report.cob", (11,)),
    }
    copybook = _file_dependencies(project_dir, "copy/DATEFMT.CPY")
    assert {(u["name"], u["file"], tuple(u["lines"])) for u in copybook["caller_usages"]} == {
        ("WS-YEAR", "src/MAIN.CBL", (25,)),
    }


def test_free_format_and_copybook_definitions(tmp_path):
    project_dir = _make_project(tmp_path)

    report = _file_dependencies(project_dir, "src/report.cob")
    assert [(d["name"], d["type"], d["start_line"], d["end_line"]) for d in report["definitions"]] == [
        ("rptprint", "program_definition", 2, 15),
        ("ws-long-line", "data_description", 7, 7),
        ("main-para", "paragraph_header", 9, 13),
        ("終了処理", "paragraph_header", 14, 15),
    ]
    data_copybook = _file_dependencies(project_dir, "copy/CUSTREC.cpy")
    assert [(d["name"], d["start_line"], d["end_line"]) for d in data_copybook["definitions"]] == [
        ("CUST-RECORD", 2, 4), ("CUST-ID", 3, 3), ("CUST-NAME", 4, 4),
    ]
    procedure_copybook = _file_dependencies(project_dir, "copy/ERRPARA.cpy")
    assert [(d["name"], d["type"]) for d in procedure_copybook["definitions"]] == [
        ("ERR-PARA", "paragraph_header"),
    ]


def test_callee_source_ignores_case(tmp_path):
    project_dir = _make_project(tmp_path)

    source = extract_callee_source("src/taxcalc.cbl", "lk-amount", project_dir)
    assert source == "       01  LK-AMOUNT          PIC 9(7)V99."
    assert extract_callee_source("src/taxcalc.cbl", "NO-SUCH-NAME", project_dir) is None


def test_statement_the_grammar_does_not_read():
    source = _source("""\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. T1.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-OUT             PIC X(99).
       01  WS-VALUE           USAGE FLOAT-LONG SYNC.
       01  WS-REC.
           05  WS-A           PIC X.
       PROCEDURE DIVISION.
       MAIN-PARA.
           JSON GENERATE WS-OUT FROM WS-REC
              ON EXCEPTION CALL 'ERRH' USING WS-A
           END-JSON.
           CALL 'NEXT1' USING WS-A.
       LAST-PARA.
           GOBACK.
""")

    assert [d.name for d in source.definition_list] == [
        "T1", "WS-OUT", "WS-VALUE", "WS-REC", "WS-A", "MAIN-PARA", "LAST-PARA",
    ]
    assert [(i.kind, i.name, i.line) for i in source.import_list] == [
        ("CALL", "ERRH", 12), ("CALL", "NEXT1", 14),
    ]
    assert source.usage_line_list({"WS-A", "WS-REC"}) == [
        ("WS-REC", 11), ("WS-A", 12), ("WS-A", 14),
    ]


def test_nested_programs_and_sections():
    source = _source("""\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. "OUTER".
       PROCEDURE DIVISION.
       A SECTION.
       A1.
           CALL "INNER".
       B SECTION.
       B1.
           STOP RUN.
       IDENTIFICATION DIVISION.
       PROGRAM-ID. INNER IS COMMON.
       PROCEDURE DIVISION.
       P1.
           DISPLAY "X".
       END PROGRAM INNER.
       END PROGRAM "OUTER".
""")

    assert [(d.name, d.type, d.start_line, d.end_line) for d in source.definition_list] == [
        ("OUTER", "program_definition", 1, 16),
        ("A", "section_header", 4, 6),
        ("A1", "paragraph_header", 5, 6),
        ("B", "section_header", 7, 9),
        ("B1", "paragraph_header", 8, 9),
        ("INNER", "program_definition", 10, 15),
        ("P1", "paragraph_header", 13, 14),
    ]


def test_fixed_format_columns():
    # Six full-width characters take twelve columns
    wide_name = "顧客" * 3
    wide_line = f'000700     DISPLAY "{wide_name}".'.ljust(72 - len(wide_name)) + "T1000700\n"
    source = _source(
        "000100 IDENTIFICATION DIVISION.                                         T1000100\n"
        "000200 PROGRAM-ID. T1.                                                  T1000200\n"
        "000300* CALL \"COMMENT\"\n"
        "000400 PROCEDURE DIVISION.\n"
        "000500 MAIN-PARA.\n"
        "000600D    CALL \"DEBUG\".\n"
        + wide_line +
        "000800\tCALL \"TABCALL\".\n"
        "000900     CALL \"SUB1\".                                                 T1000900\n"
    )

    assert [(i.name, i.line) for i in source.import_list] == [("TABCALL", 8), ("SUB1", 9)]
    assert source.usage_line_list({"T1000700", "T1000900"}) == []


def test_unit_text_fits_the_code_area():
    cobol_text = split_cobol_source(MAIN_SOURCE + APP_FILE_DICT["src/report.cob"])

    assert cobol_text.unit_list
    for unit in cobol_text.unit_list:
        line_list = unit.text.splitlines()
        assert len(line_list) == len(unit.origin_line_list)
        assert all(len(line) <= 72 for line in line_list)
        assert unit.text.isascii() or '"' in unit.text or "'" in unit.text


def test_a_literal_on_a_line_longer_than_the_code_area_is_read_whole():
    name = "ws-program-name-for-the-dynamic-call"
    source = _source(f"""\
>>SOURCE FORMAT FREE
identification division.
program-id. t1.
data division.
working-storage section.
01 {name}  pic x(8) value "SUBPGM01".
01 ws-b                                  pic x(8).
procedure division.
    call {name}
    move "SUBPGM02" to ws-b-of-a-long-name-that-runs-past-the-code-area-x ws-b
    call ws-b.
""")

    assert [(i.name, i.line) for i in source.import_list] == [("SUBPGM01", 9), ("SUBPGM02", 11)]


def test_the_paragraph_of_an_exec_sql_whenever_statement_is_a_usage():
    source = _source("""\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. W1.
       PROCEDURE DIVISION.
       MAIN-PARA.
           EXEC SQL WHENEVER SQLERROR GO TO ERR-PARA END-EXEC
           EXEC SQL
               WHENEVER NOT FOUND
               GOTO END-PARA
           END-EXEC
           EXEC SQL WHENEVER SQLWARNING CONTINUE END-EXEC
           GOBACK.
       ERR-PARA.
           GOBACK.
       END-PARA.
           GOBACK.
""")

    assert source.usage_line_list({"ERR-PARA", "END-PARA", "SQLERROR", "FOUND", "SQLWARNING"}) == [
        ("ERR-PARA", 5), ("END-PARA", 8),
    ]


def test_the_last_header_of_the_declaratives_ends_before_end_declaratives():
    source = _source("""\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. D1.
       PROCEDURE DIVISION.
       DECLARATIVES.
       ERR-SEC SECTION.
           USE AFTER STANDARD ERROR PROCEDURE ON F1.
       ERR-PARA.
           DISPLAY 'ERR'.
       END DECLARATIVES.
       MAIN-SEC SECTION.
       MAIN-PARA.
           GOBACK.
""")

    assert [(d.name, d.start_line, d.end_line) for d in source.definition_list] == [
        ("D1", 1, 12), ("ERR-SEC", 5, 8), ("ERR-PARA", 7, 8), ("MAIN-SEC", 10, 12),
        ("MAIN-PARA", 11, 12),
    ]


def test_picture_string_after_pic_is():
    source = _source("""\
       01  WS-G PIC IS ９(5).
       01  WS-H PICTURE IS X(3).
""")

    assert [d.name for d in source.definition_list] == ["WS-G", "WS-H"]
    assert source.reference_list == []


def test_replace_name():
    assert replace_name(":PFX:-CUST-ID", [("", ":PFX:", "WS")]) == "WS-CUST-ID"
    assert replace_name("old-name", [("", "OLD-NAME", "NEW-NAME")]) == "NEW-NAME"
    assert replace_name("OLD-NAME-2", [("", "OLD-NAME", "NEW-NAME")]) == "OLD-NAME-2"
    assert replace_name("IN-RECORD", [("LEADING", "IN", "OUT")]) == "OUT-RECORD"
    assert replace_name("RECORD-X", [("TRAILING", "X", "Y")]) == "RECORD-Y"
    assert replace_name("RECORD-X", [("LEADING", "X", "Y")]) == "RECORD-X"


def test_copybook_choice(tmp_path):
    project_dir = _make_project(tmp_path, {
        "app/MAIN.cbl": (
            "       IDENTIFICATION DIVISION.\n"
            "       PROGRAM-ID. MAIN.\n"
            "       DATA DIVISION.\n"
            "       WORKING-STORAGE SECTION.\n"
            "       COPY SHARE.\n"
            "       COPY LOCAL.\n"
            "       PROCEDURE DIVISION.\n"
            "           CALL 'SHARE'.\n"
            "           CALL 'ALIAS1'.\n"
        ),
        "app/LOCAL.cpy": "       01  LOCAL-A PIC X.\n",
        "other/LOCAL.cpy": "       01  LOCAL-B PIC X.\n",
        "copy/SHARE.cpy": "       01  SHARE-A PIC X.\n",
        "pgm/SHARE.cbl": (
            "       IDENTIFICATION DIVISION.\n"
            "       PROGRAM-ID. SHARE.\n"
            "       PROCEDURE DIVISION.\n"
            "           GOBACK.\n"
        ),
        "pgm/OTHERNAME.cbl": (
            "       IDENTIFICATION DIVISION.\n"
            "       PROGRAM-ID. ALIAS1.\n"
            "       PROCEDURE DIVISION.\n"
            "           GOBACK.\n"
        ),
    })

    assert _graph(project_dir)["app/MAIN.cbl"]["callees"] == {
        "app/LOCAL.cpy", "copy/SHARE.cpy", "pgm/SHARE.cbl", "pgm/OTHERNAME.cbl",
    }


_DATE_PROGRAM_TEXT = (
    "       IDENTIFICATION DIVISION.\n"
    "       PROGRAM-ID. DATEPGM.\n"
    "       DATA DIVISION.\n"
    "       LINKAGE SECTION.\n"
    "       01  LK-PARM PIC X(8).\n"
    "       PROCEDURE DIVISION USING LK-PARM.\n"
    "           GOBACK.\n"
)
_DATE_COPYBOOK_TEXT = "       01  DATE-PARM.\n           05  DP-DATE PIC X(8).\n"


@pytest.mark.parametrize(("program_rel", "copybook_rel_list", "copy_target_rel"), [
    ("app/batch/DATEPGM.cbl", ["copylib/DATEPGM.cpy"], "copylib/DATEPGM.cpy"),
    ("app/online/DATEPGM.cbl", ["copylib/DATEPGM.cpy"], "copylib/DATEPGM.cpy"),
    ("app/batch/DATEPGM.cob", ["lib/copy/x/DATEPGM.CPY"], "lib/copy/x/DATEPGM.CPY"),
    ("lib/DATEPGM.cbl", ["app/cpy/DATEPGM.cpy"], "app/cpy/DATEPGM.cpy"),
    (
        "app/batch/DATEPGM.cbl",
        ["app/online/DATEPGM.cpy", "app/copy/DATEPGM.cpy", "copylib/DATEPGM.cpy"],
        "app/online/DATEPGM.cpy",
    ),
    (
        "lib/DATEPGM.cbl",
        ["copylib/DATEPGM.cpy", "app/copy/DATEPGM.cpy"],
        "app/copy/DATEPGM.cpy",
    ),
])
def test_copy_takes_a_copybook_before_a_program_of_the_name(
    tmp_path, program_rel, copybook_rel_list, copy_target_rel,
):
    """COPY brings in a copybook, the nearest of several; CALL goes to the program."""
    file_dict = {
        "app/online/MAIN.cbl": (
            "       IDENTIFICATION DIVISION.\n"
            "       PROGRAM-ID. MAIN.\n"
            "       DATA DIVISION.\n"
            "       WORKING-STORAGE SECTION.\n"
            "       COPY DATEPGM.\n"
            "       PROCEDURE DIVISION.\n"
            "           MOVE SPACES TO DP-DATE.\n"
            "           CALL 'DATEPGM' USING DATE-PARM.\n"
        ),
        program_rel: _DATE_PROGRAM_TEXT,
    }
    for copybook_rel in copybook_rel_list:
        file_dict[copybook_rel] = _DATE_COPYBOOK_TEXT
    project_dir = _make_project(tmp_path, file_dict)

    assert _graph(project_dir)["app/online/MAIN.cbl"]["callees"] == {
        copy_target_rel, program_rel,
    }


def test_format_directive_inside_a_literal_is_not_read():
    source = _source("""\
>>SOURCE FORMAT IS FREE
identification division.
program-id. t1.
procedure division.
main-para.
    move '       >>SOURCE FORMAT IS FIXED' to ws-row
*>     >>SOURCE FIXED in a comment
    call static "sub1" using ws-row
    goback.
""")

    assert [(i.name, i.line) for i in source.import_list] == [("sub1", 8)]
    assert [d.name for d in source.definition_list] == ["t1", "main-para"]


def test_copybook_that_starts_with_an_exec_block():
    source = _source("""\
           EXEC SQL DECLARE TEXE2 TABLE
           ( FIRST_NAME                  CHAR(10) NOT NULL
           ) END-EXEC.
      * COBOL DECLARATION
       01  TEXE2.
           10 FIRST-NAME           PIC X(10).
""")

    assert [(d.name, d.type, d.start_line, d.end_line) for d in source.definition_list] == [
        ("TEXE2", "data_description", 5, 6),
        ("FIRST-NAME", "data_description", 6, 6),
    ]


@pytest.mark.parametrize("first_line", [
    "       EJECT",
    "       SKIP2",
    "       TITLE 'A RECORD'",
    "       REPLACE ==:P:== BY ==AB==.",
])
def test_copybook_that_starts_with_a_statement_of_no_code(first_line):
    source = _source(first_line + "\n       01  REC-A.\n           05 ID-A PIC X(5).\n")

    assert [(d.name, d.type, d.start_line, d.end_line) for d in source.definition_list] == [
        ("REC-A", "data_description", 2, 3),
        ("ID-A", "data_description", 3, 3),
    ]


def test_replace_between_a_group_and_its_copy_statement():
    source = _source("""\
       01  REC-A.
       REPLACE ==:P:== BY ==AB==.
           COPY RECB.
       REPLACE OFF.
       01  REC-C PIC X.
""")

    assert [(d.name, d.end_line, d.is_group) for d in source.definition_list] == [
        ("REC-A", 3, True), ("REC-C", 5, False),
    ]


@pytest.mark.parametrize("header", [
    "INPUT-OUTPUT SECTION.", "FILE-CONTROL.", "CONFIGURATION SECTION.", "SPECIAL-NAMES.",
])
def test_copybook_that_starts_with_a_header_of_the_environment_division(header):
    source = _source(f"       {header}\n           SELECT IN-A ASSIGN TO DD-A.\n")

    assert source.definition_list == []
    assert source.usage_line_list({"IN-A", "DD-A"}) == [("DD-A", 2), ("IN-A", 2)]


def test_compiler_option_line_in_the_code_area():
    source = _source("""\
       PROCESS SQL
       IDENTIFICATION DIVISION.
       PROGRAM-ID. T1.
       PROCEDURE DIVISION.
       PROCESS-PARA.
           GOBACK.
""")

    assert [(d.name, d.start_line) for d in source.definition_list] == [
        ("T1", 2), ("PROCESS-PARA", 5),
    ]
    assert source.usage_line_list({"SQL", "PROCESS"}) == []


def test_full_width_character_in_the_sequence_area():
    # A full-width space takes two columns: the line is a fixed-format line
    source = _source(
        "       IDENTIFICATION DIVISION.\n"
        "       PROGRAM-ID. T1.\n"
        "       DATA DIVISION.\n"
        "       WORKING-STORAGE SECTION.\n"
        # 71 characters take 72 columns: the identification area starts after them
        + "\u3000     01  F00 PIC X.".ljust(71) + "ZZZ00500\n"
    )

    assert [(d.name, d.start_line) for d in source.definition_list] == [("T1", 1), ("F00", 5)]
    assert source.usage_line_list({"ZZZ00500"}) == []


def test_directive_in_the_indicator_column_keeps_the_fixed_format():
    source = _source(
        "       IDENTIFICATION DIVISION.\n"
        "      >> IF DEBUG-MODE DEFINED\n"
        "       PROGRAM-ID. T1.                                                  ZZZ00300\n"
        "      >> END-IF\n"
        "       PROCEDURE DIVISION.\n"
        "           CALL 'SUB1'.                                                 ZZZ00600\n"
    )

    assert [(i.name, i.line) for i in source.import_list] == [("SUB1", 6)]
    assert source.usage_line_list({"ZZZ00300", "ZZZ00600", "IF", "DEBUG-MODE"}) == []


def test_renames_item_belongs_to_the_record_only():
    source = _source("""\
       01  REC.
           05  A-PART         PIC X.
           05  B-PART         PIC X.
       66  AB-PART RENAMES A-PART THRU B-PART.
       01  NEXT-REC           PIC X.
""")

    assert [(d.name, d.start_line, d.end_line) for d in source.definition_list] == [
        ("REC", 1, 4), ("A-PART", 2, 2), ("B-PART", 3, 3), ("AB-PART", 4, 4), ("NEXT-REC", 5, 5),
    ]


def test_level_number_of_digits_that_are_not_decimal():
    source = _source("""\
       01  B PIC X.
       ² A PIC X.
       01  C PIC X.
""")

    assert [d.name for d in source.definition_list] == ["B", "C"]


def test_copybook_name_with_a_directory(tmp_path):
    project_dir = _make_project(tmp_path, {
        "app/MAIN.cbl": (
            "       IDENTIFICATION DIVISION.\n"
            "       PROGRAM-ID. MAIN.\n"
            "       DATA DIVISION.\n"
            "       WORKING-STORAGE SECTION.\n"
            "       COPY 'lib/CONST.cpy'.\n"
        ),
        "app/lib/CONST.cpy": "       01  CONST-A PIC X.\n",
        "app/CONST.cpy": "       01  CONST-B PIC X.\n",
    })

    assert _graph(project_dir)["app/MAIN.cbl"]["callees"] == {"app/lib/CONST.cpy"}


def test_module_string_of_a_program_name_with_of():
    assert cobol_module_part(cobol_module("CALL", "LIST OF ITEMS")) == ("CALL", "LIST OF ITEMS", "")
    assert cobol_module_part(cobol_module("COPY", "CUSTREC", "LIB")) == ("COPY", "CUSTREC", "LIB")


def test_copy_before_the_identification_division():
    source = _source("""\
      >>SOURCE FORMAT IS FIXED
       copy "const-set-1.cpy".
       IDENTIFICATION DIVISION.
       PROGRAM-ID. T1.
       AUTHOR. COPY OF THE TEAM.
       PROCEDURE DIVISION.
           GOBACK.
""")

    assert [(i.kind, i.name, i.line) for i in source.import_list] == [("COPY", "const-set-1.cpy", 2)]
    assert [(d.name, d.start_line) for d in source.definition_list] == [("T1", 3)]


_FAIL_PROJECT_DICT = {
    "src/MAIN.cbl": (
        "       IDENTIFICATION DIVISION.\n"
        "       PROGRAM-ID. MAIN.\n"
        "       DATA DIVISION.\n"
        "       WORKING-STORAGE SECTION.\n"
        "       COPY GOOD.\n"
        "       COPY BAD.\n"
        "       PROCEDURE DIVISION.\n"
        "           MOVE GOOD-A TO GOOD-A.\n"
        "           CALL 'SUB1'.\n"
    ),
    "copy/GOOD.cpy": "       01  GOOD-A PIC X.\n",
    "copy/BAD.cpy": "      * BROKEN-MARK\n       01  BAD-A PIC X.\n",
    "src/SUB1.cbl": (
        "       IDENTIFICATION DIVISION.\n"
        "       PROGRAM-ID. SUB1.\n"
        "       PROCEDURE DIVISION.\n"
        "           GOBACK.\n"
    ),
}


def _fail_on_mark(monkeypatch) -> None:
    """Make the parse of a COBOL file with BROKEN-MARK in it raise an exception."""
    from codetwine.parsers import ts_parser
    real_read_cobol_source = ts_parser.read_cobol_source

    def read_or_fail(source, language):
        if "BROKEN-MARK" in source:
            raise ValueError("unexpected token")
        return real_read_cobol_source(source, language)

    monkeypatch.setattr(ts_parser, "read_cobol_source", read_or_fail)


def test_a_file_that_fails_to_parse_is_left_out_of_the_graph(tmp_path, monkeypatch):
    _fail_on_mark(monkeypatch)
    graph = _graph(_make_project(tmp_path, _FAIL_PROJECT_DICT))

    assert graph["src/MAIN.cbl"]["callees"] == {"copy/GOOD.cpy", "src/SUB1.cbl"}
    assert graph["copy/BAD.cpy"] == {"callers": set(), "callees": set()}


async def test_a_file_that_fails_to_parse_is_reported_and_the_run_goes_on(tmp_path, monkeypatch):
    from codetwine import pipeline
    _fail_on_mark(monkeypatch)
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", False)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "json")
    project_dir = _make_project(tmp_path, _FAIL_PROJECT_DICT)

    result = await pipeline.process_all_files(project_dir, str(tmp_path / "out"), None, max_workers=1)

    assert result["file_count"] == 4
    assert result["dependency_fail_list"] == ["copy/BAD.cpy"]
    knowledge = json.loads((tmp_path / "out" / "bank" / "project_knowledge.json").read_text())
    main = next(f for f in knowledge["files"] if f["file"].endswith("/MAIN.cbl"))
    assert {
        u["name"]: copy_path_to_rel(u["from"].split("/", 1)[1])
        for u in main["file_dependencies"]["callee_usages"]
    } == {"GOOD-A": "copy/GOOD.cpy", "SUB1": "src/SUB1.cbl"}
