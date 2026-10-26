"""JCL: jobs, procedures and INCLUDE members, the programs a step runs, the procedures it
calls, steps and symbols, and JCL files without a JCL extension."""

import os

import pytest

from codetwine.cobol_file_index import file_index_cache_dict, reference_target_cache_dict
from codetwine.config.settings import has_language, language_ext
from codetwine.extractors.dependency_graph import build_project_dependencies
from codetwine.extractors.jcl_source import read_jcl_source
from codetwine.file_analyzer import get_file_dependencies
from codetwine.parsers.ts_parser import parse_cache_dict
from codetwine.utils.file_utils import copy_path_to_rel


@pytest.fixture(autouse=True)
def _clear_cache():
    """Start each test without parse results, file indexes and references of another project."""
    parse_cache_dict.clear()
    file_index_cache_dict.clear()
    reference_target_cache_dict.clear()


def _program(name: str) -> str:
    """Write a COBOL program that does nothing."""
    line_list = ["IDENTIFICATION DIVISION.", f"PROGRAM-ID. {name}.", "PROCEDURE DIVISION.", "    GOBACK."]
    return "".join(f"       {line}\n" for line in line_list)


def _make_project(tmp_path, file_dict: dict[str, str]) -> str:
    """Write the files of a project and return its directory."""
    project = tmp_path / "proj"
    for file_rel, text in file_dict.items():
        path = project / file_rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return str(project)


def _graph(project_dir: str) -> dict[str, set[str]]:
    """Build the graph of a project and return {file: callee files}."""
    project_name = os.path.basename(project_dir)
    return {
        copy_path_to_rel(dep["file"][len(project_name) + 1:]): {
            copy_path_to_rel(path[len(project_name) + 1:]) for path in dep["callees"]
        }
        for dep in build_project_dependencies(project_dir)
    }


def _analyse(project_dir: str) -> dict[str, dict]:
    """Build the graph of a project and return get_file_dependencies of every file with a language."""
    project_name = os.path.basename(project_dir)
    caller_dict = {}
    for dep in build_project_dependencies(project_dir):
        rel = copy_path_to_rel(dep["file"][len(project_name) + 1:])
        caller_dict[rel] = [copy_path_to_rel(p[len(project_name) + 1:]) for p in dep["callers"]]
    project_file_set = {f for f in caller_dict if has_language(os.path.join(project_dir, f))}
    caller_map = {f: sorted(caller_dict[f]) for f in project_file_set}
    return {
        file_rel: get_file_dependencies(
            os.path.join(project_dir, file_rel), project_dir, project_file_set, caller_map,
        )
        for file_rel in sorted(project_file_set)
    }


def _usage(usage_list: list[dict], key: str) -> set[tuple]:
    """Return (name, target name, first line of the target, lines) of each usage, with the file under key."""
    return {
        (usage["name"], usage.get("target_name"), usage.get("target_start_line"),
         usage.get(key, ""), tuple(usage["lines"]))
        for usage in usage_list
    }


# == Reader ===================================================================

JOB_SOURCE = """\
//NIGHTLY  JOB (ACCT),'NIGHT RUN',CLASS=A,
//             MSGCLASS=X
//* A comment statement
// SET HLQ=PROD.DATA,PROG=CBTRN02C
//MYPROC   PROC OUT=SYSOUT,PGMX=CBTRN03C
//PSTEP1   EXEC PGM=&PGMX
//OUT      DD SYSOUT=&OUT
// PEND
//STEP01   EXEC PGM=&PROG
//INFILE   DD DSN=&HLQ..TRANS,
//            DISP=SHR
//SYSIN    DD *
//NOT A STATEMENT IN THE DATA OF STAR
/*
//STEP02   EXEC MYPROC,OUT=A,
//            COND=(4,LT,STEP01)
//PSTEP1.OUT DD SYSOUT=B
//STEP03   EXEC PGM=IKJEFT01,PARM='A B'
//SYSTSIN  DD DATA,DLM=@@
//NOT A STATEMENT EITHER
 DSN SYSTEM(DB2P)
 RUN PROGRAM(CBLDB21) -
     PLAN(PLAN1)
@@
//IMS      EXEC PGM=DFSRRC00,PARM='DLI,PAUDBUNL,PSB1'
//COPY1    DD DSN=*.STEP01.INFILE,DISP=SHR
// IF (STEP02.PSTEP1.RC > 4 |
//     STEP03.RC = 0) THEN
//STEP04   EXEC CATPROC
// ENDIF
// INCLUDE MEMBER=DBSTEPS
"""


def _definition_dict(text: str) -> dict[tuple[str, str], tuple[int, int]]:
    """Return {(type, name): (start line, end line)} of the definitions of a JCL source."""
    return {
        (definition.type, definition.name): (definition.start_line, definition.end_line)
        for definition in read_jcl_source(text).definition_list
    }


def test_definitions_of_a_job():
    definition_dict = _definition_dict(JOB_SOURCE)

    assert definition_dict[("job_statement", "NIGHTLY")] == (1, 31)
    assert definition_dict[("symbolic_parameter", "HLQ")] == (4, 4)
    assert definition_dict[("proc_statement", "MYPROC")] == (5, 8)
    assert definition_dict[("symbolic_parameter", "PGMX")] == (5, 5)
    assert definition_dict[("exec_statement", "PSTEP1")] == (6, 7)
    assert definition_dict[("exec_statement", "STEP01")] == (9, 13)
    assert definition_dict[("exec_statement", "STEP02")] == (15, 17)
    assert definition_dict[("exec_statement", "STEP03")] == (18, 23)
    assert definition_dict[("exec_statement", "IMS")] == (25, 26)
    assert definition_dict[("exec_statement", "STEP04")] == (29, 29)


def test_files_a_job_names():
    import_set = {(i.kind, i.name, i.line) for i in read_jcl_source(JOB_SOURCE).import_list}

    assert import_set == {
        ("CALL", "CBTRN03C", 6),     # PGM=&PGMX: the default of MYPROC (STEP02 gives no PGMX)
        ("CALL", "CBTRN02C", 9),     # PGM=&PROG: the SET statement
        ("CALL", "IKJEFT01", 18),
        ("CALL", "CBLDB21", 22),     # RUN PROGRAM of the SYSTSIN data
        ("CALL", "DFSRRC00", 25),
        ("CALL", "PAUDBUNL", 25),    # PARM of the IMS batch step
        ("PROC", "CATPROC", 29),     # MYPROC is in-stream: not a file
        ("INCLUDE", "DBSTEPS", 31),
    }


def test_references_of_a_job():
    reference_set = {
        (r.kind, r.name, r.line, r.exec_line, r.definition_line)
        for r in read_jcl_source(JOB_SOURCE).reference_list
    }

    assert ("symbol", "PGMX", 6, 0, 5) in reference_set
    assert ("symbol", "OUT", 7, 0, 5) in reference_set
    assert ("symbol", "PROG", 9, 0, 4) in reference_set
    assert ("symbol", "HLQ", 10, 0, 4) in reference_set
    assert ("proc", "MYPROC", 15, 15, 5) in reference_set
    assert ("parameter", "OUT", 15, 15, 0) in reference_set
    assert ("step", "STEP01", 16, 0, 9) in reference_set
    assert ("step", "PSTEP1", 17, 15, 0) in reference_set
    assert ("step", "STEP01", 26, 0, 9) in reference_set
    assert ("step", "STEP02", 27, 0, 15) in reference_set
    assert ("step", "PSTEP1", 27, 15, 0) in reference_set
    assert ("step", "STEP03", 28, 0, 18) in reference_set
    assert ("proc", "CATPROC", 29, 29, 0) in reference_set
    assert not any(name == "NOT" for _, name, *_ in reference_set)


def test_symbolic_program_of_an_in_stream_procedure_called_twice():
    text = (
        "//J JOB\n"
        "//P PROC PROG=DEFPGM\n"
        "//S EXEC PGM=&PROG\n"
        "// PEND\n"
        "//A EXEC P,PROG=PGMA\n"
        "//B EXEC P,PROG=PGMB\n"
        "//C EXEC P\n"
    )
    import_set = {(i.name, i.line) for i in read_jcl_source(text).import_list}

    assert import_set == {("PGMA", 3), ("PGMB", 3), ("DEFPGM", 3)}


def test_quoted_string_continued_in_column_16():
    text = (
        "//J JOB\n"
        "//S EXEC PGM=MAINPGM,PARM='A VERY LONG PARAMETER THAT RUNS TO COLUMN SEVENTY-ON\n"
        "//             E',REGION=0M\n"
        "//D DD DSN=&HLQ..X\n"
    )
    source = read_jcl_source(text)

    assert [(i.name, i.line) for i in source.import_list] == [("MAINPGM", 2)]
    assert {d.name: d.end_line for d in source.definition_list}["S"] == 4


# == Project ==================================================================

PROC_SOURCE = """\
//* The member name, not the name of the PROC statement, names the procedure
//RPROC    PROC CNTLLIB=X.CNTL
//PRC001   EXEC PGM=IDCAMS
//FILEIN   DD DSN=&CNTLLIB
"""

JOB_WITH_PROC_SOURCE = """\
//TRANBKP  JOB 'BACKUP'
//STEP05   EXEC PROC=REPROC,
//            CNTLLIB=MY.CNTL
//PRC001.FILEIN DD DSN=MY.TRANS
//STEP10   EXEC PGM=CBTRN03C
"""


def test_job_links_to_its_procedure_and_program(tmp_path):
    project_dir = _make_project(tmp_path, {
        "jcl/TRANBKP.jcl": JOB_WITH_PROC_SOURCE,
        "proc/REPROC.prc": PROC_SOURCE,
        "cbl/CBTRN03C.cbl": _program("CBTRN03C"),
    })

    result = _analyse(project_dir)

    assert _usage(result["jcl/TRANBKP.jcl"]["callee_usages"], "from") == {
        ("RPROC", "RPROC", 2, "proc/REPROC.prc", (2,)),
        ("CNTLLIB", "CNTLLIB", 2, "proc/REPROC.prc", (3,)),
        ("PRC001", "PRC001", 3, "proc/REPROC.prc", (4,)),
        ("CBTRN03C", "CBTRN03C", 1, "cbl/CBTRN03C.cbl", (5,)),
    }
    assert _usage(result["cbl/CBTRN03C.cbl"]["caller_usages"], "file") == {
        ("CBTRN03C", None, None, "jcl/TRANBKP.jcl", (5,)),
    }
    assert _usage(result["proc/REPROC.prc"]["same_file_usages"], "") == {
        ("CNTLLIB", "CNTLLIB", 2, "", (4,)),
    }
    assert result["jcl/TRANBKP.jcl"]["language"] == "jcl"


def test_include_member_and_extensions_in_any_case(tmp_path):
    project_dir = _make_project(tmp_path, {
        "jcl/DAILY.JCL": "//DAILY JOB\n// INCLUDE MEMBER=DBSTEPS\n",
        "jcl/DBSTEPS.Jcl": "//STEPDB EXEC PGM=DBLOAD\n",
        "proc/LOAD.Proc": "//LOAD PROC\n//S1 EXEC PGM=DBLOAD\n",
        "cbl/DBLOAD.cbl": _program("DBLOAD"),
    })

    graph = _graph(project_dir)

    assert graph["jcl/DAILY.JCL"] == {"jcl/DBSTEPS.Jcl"}
    assert graph["jcl/DBSTEPS.Jcl"] == {"cbl/DBLOAD.cbl"}
    assert graph["proc/LOAD.Proc"] == {"cbl/DBLOAD.cbl"}
    assert [language_ext(p) for p in ("A.JCL", "A.Prc", "A.PROC", "A.jcl")] == ["jcl", "prc", "proc", "jcl"]


def test_jcl_files_without_a_jcl_extension(tmp_path):
    project_dir = _make_project(tmp_path, {
        "jcl/NIGHTLY": "//* nightly run\n//NIGHTLY JOB\n//S1 EXEC BATCHP\n",
        "notes/BATCHP.txt": "BATCHP runs the batch.\n",
        "proc/BATCHP": "//BATCHP PROC\n//S1 EXEC PGM=BATCH1\n",
        "cbl/BATCH1.cbl": _program("BATCH1"),
        "docs/readme.txt": "// not a job\n",
    })

    graph = _graph(project_dir)

    assert graph["jcl/NIGHTLY"] == {"proc/BATCHP"}
    assert graph["proc/BATCHP"] == {"cbl/BATCH1.cbl"}
    assert language_ext(os.path.join(project_dir, "jcl/NIGHTLY")) == "jcl"
    assert not has_language(os.path.join(project_dir, "notes/BATCHP.txt"))
    assert not has_language(os.path.join(project_dir, "docs/readme.txt"))


def test_copy_does_not_go_to_a_jcl_file(tmp_path):
    copy_program = _program("MAINPGM").replace(
        "       PROCEDURE DIVISION.\n",
        "       DATA DIVISION.\n       WORKING-STORAGE SECTION.\n       COPY CUSTREC.\n"
        "       PROCEDURE DIVISION.\n",
    )
    project_dir = _make_project(tmp_path, {
        "cbl/MAINPGM.cbl": copy_program,
        "jcl/CUSTREC.jcl": "//CUSTREC JOB\n//S1 EXEC PGM=MAINPGM\n",
    })

    graph = _graph(project_dir)

    assert graph["cbl/MAINPGM.cbl"] == set()
    assert graph["jcl/CUSTREC.jcl"] == {"cbl/MAINPGM.cbl"}


# == Procedure lookup, scopes and file detection ===========================

def test_exec_of_a_procedure_does_not_go_to_a_job(tmp_path):
    project_dir = _make_project(tmp_path, {
        "JCL/PAYROLL": "//PAYROLL JOB 1\n//S1 EXEC PAYROLL\n",
        "JCL/RUNPAY": "//RUNPAY JOB 1\n//S1 EXEC PAYROLL\n",
        "PROCLIB/PAYROLL": "//PAYROLL PROC\n//P1 EXEC PGM=PAYPGM\n",
    })

    graph = _graph(project_dir)

    assert graph["JCL/RUNPAY"] == {"PROCLIB/PAYROLL"}
    assert graph["JCL/PAYROLL"] == {"PROCLIB/PAYROLL"}


def test_symbol_in_a_procedure_name():
    text = "// SET PN=MYPROC\n//S1 EXEC &PN\n//S2 EXEC PROC=&PN.\n"
    import_set = {(i.kind, i.name, i.line) for i in read_jcl_source(text).import_list}

    assert import_set == {("PROC", "MYPROC", 2), ("PROC", "MYPROC", 3)}


def test_in_stream_procedures_and_set_values_count_within_their_job():
    text = (
        "//JOBA JOB\n"
        "// SET P=PROGA\n"
        "//P PROC\n"
        "//S1 EXEC PGM=A\n"
        "// PEND\n"
        "//X1 EXEC P\n"
        "//JOBB JOB\n"
        "//X2 EXEC P\n"
        "//X3 EXEC PGM=&P\n"
    )
    source = read_jcl_source(text)

    assert {(i.kind, i.name, i.line) for i in source.import_list} == {
        ("CALL", "A", 4), ("PROC", "P", 8),
    }
    assert ("proc", "P", 6, 6, 3) in {
        (r.kind, r.name, r.line, r.exec_line, r.definition_line) for r in source.reference_list
    }


def test_comment_after_endif_is_no_operand():
    text = "//J JOB\n// IF (RC = 0) THEN\n//A EXEC PGM=PROGA\n// ENDIF  IT'S DONE\n//         EXEC PGM=PROGB\n"
    import_set = {(i.name, i.line) for i in read_jcl_source(text).import_list}

    assert import_set == {("PROGA", 3), ("PROGB", 5)}


def test_slash_comments_of_other_files_are_no_jcl(tmp_path):
    project_dir = _make_project(tmp_path, {
        "jcl/DAILY.jcl": "//DAILY JOB\n// INCLUDE MEMBER=SETUP\n",
        "src/SETUP": "// Set up the build\npackage main\n",
        "src/notes": "//todo job list\n",
        "plsql/add_cust.prc": "CREATE OR REPLACE PROCEDURE add_cust AS\nBEGIN NULL; END;\n",
        "proc/LOWER.prc": "//lower proc\n//s1 exec pgm=lowpgm\n",
        "cntl/TEMPLATE.jcl": "//<APPLID> JOB ,'APPL',\n//S1 EXEC PGM=DFHSIP\n",
    })

    graph = _graph(project_dir)

    assert graph["jcl/DAILY.jcl"] == set()
    for file_rel in ("src/SETUP", "src/notes", "plsql/add_cust.prc"):
        assert not has_language(os.path.join(project_dir, file_rel))
    assert language_ext(os.path.join(project_dir, "proc/LOWER.prc")) == "prc"
    assert language_ext(os.path.join(project_dir, "cntl/TEMPLATE.jcl")) == "jcl"


def test_values_of_a_call_through_a_symbol():
    text = (
        "//J JOB\n"
        "// SET PN=INP\n"
        "//INP PROC\n"
        "//S EXEC PGM=&PG\n"
        "// PEND\n"
        "//A EXEC &PN,PG=PGMA\n"
        "//B EXEC INP,PG=PGMB\n"
    )
    import_set = {(i.name, i.line) for i in read_jcl_source(text).import_list}

    assert import_set == {("PGMA", 4), ("PGMB", 4)}


def test_tab_and_utf16_files_are_read_as_jcl(tmp_path):
    project_dir = _make_project(tmp_path, {
        "jcl/NIGHTLY.jcl": "//NIGHTLY\tJOB 1\n//S1 EXEC PGM=PAYPGM\n",
        "jcl/NOEXT": "//NOEXT\tJOB 1\n//S1 EXEC PGM=PAYPGM\n",
        "cbl/PAYPGM.cbl": _program("PAYPGM"),
    })
    (tmp_path / "proj" / "jcl" / "WIDE").write_text(
        "//WIDE JOB 1\n//S1 EXEC PGM=PAYPGM\n", encoding="utf-16",
    )

    graph = _graph(project_dir)

    for file_rel in ("jcl/NIGHTLY.jcl", "jcl/NOEXT", "jcl/WIDE"):
        assert graph[file_rel] == {"cbl/PAYPGM.cbl"}


def test_step_of_each_procedure_an_exec_calls(tmp_path):
    project_dir = _make_project(tmp_path, {
        "jcl/RUN.jcl": (
            "//RUN JOB\n"
            "//OUTER PROC PN=PA\n"
            "//S EXEC &PN,X=1\n"
            "//PS.D DD DUMMY\n"
            "// PEND\n"
            "//A EXEC OUTER\n"
            "//B EXEC OUTER,PN=PB\n"
        ),
        "proc/PA.prc": "//PA PROC X=0\n//PS EXEC PGM=IEFBR14\n",
        "proc/PB.prc": "//PB PROC X=0\n//PS EXEC PGM=IEFBR14\n",
    })

    callee_set = {
        (usage["name"], usage["from"]) for usage in _analyse(project_dir)["jcl/RUN.jcl"]["callee_usages"]
    }

    assert {("PS", "proc/PA.prc"), ("PS", "proc/PB.prc"), ("X", "proc/PA.prc"), ("X", "proc/PB.prc")} <= callee_set


def test_call_through_a_symbol_from_another_in_stream_procedure():
    text = (
        "//J1       JOB 1\n"
        "//PA       PROC X=DEFX\n"
        "//SA       EXEC &X\n"
        "//         PEND\n"
        "//PB       PROC Y=PA\n"
        "//SB       EXEC &Y,X=CATPROC\n"
        "//         PEND\n"
        "//STEP1    EXEC PB\n"
    )
    import_set = {(i.kind, i.name, i.line) for i in read_jcl_source(text).import_list}

    assert ("PROC", "CATPROC", 3) in import_set
    assert ("PROC", "DEFX", 3) not in import_set


def test_long_chain_of_set_symbols():
    count = 2000
    text = f"//J JOB\n// SET A{count}=LASTPGM\n"
    text += "".join(f"// SET A{i}=&A{i + 1}\n" for i in reversed(range(count)))
    text += "//S EXEC PGM=&A0\n"

    import_list = read_jcl_source(text).import_list

    assert [(i.name, i.line) for i in import_list] == [("LASTPGM", count + 3)]


def test_ebcdic_file_with_source_encoding(tmp_path, monkeypatch):
    import codetwine.utils.file_utils as file_utils

    monkeypatch.setattr(file_utils, "SOURCE_ENCODING", ["cp037"])
    project_dir = _make_project(tmp_path, {"cbl/PAYPGM.cbl": _program("PAYPGM")})
    (tmp_path / "proj" / "jcl").mkdir()
    (tmp_path / "proj" / "jcl" / "NIGHT.jcl").write_bytes(
        "//NIGHT    JOB 1\n//S1 EXEC PGM=PAYPGM\n".encode("cp037"),
    )

    assert _graph(project_dir)["jcl/NIGHT.jcl"] == {"cbl/PAYPGM.cbl"}


def test_set_value_is_replaced_where_the_set_statement_is():
    def program_set(text: str) -> set[tuple[str, int]]:
        return {(i.name, i.line) for i in read_jcl_source(text).import_list}

    assert program_set("// SET A=X\n// SET B=&A\n// SET A=Y\n//S1 EXEC PGM=&B\n") == {("X", 4)}
    assert program_set("// SET A=PGMX\n// SET A=&A\n//S EXEC PGM=&A\n") == {("PGMX", 3)}
    assert program_set(
        "//P PROC A=PGMA\n// SET X=&A\n//S EXEC PGM=&X\n// PEND\n//C EXEC P,A=PGMB\n",
    ) == {("PGMB", 3)}
    reference_set = {
        (r.name, r.line, r.definition_line)
        for r in read_jcl_source("// SET A=PGMX\n// SET A=&A\n").reference_list
    }
    assert reference_set == {("A", 2, 1)}


def test_many_calls_of_nested_defaults():
    depth, call_count = 8, 200
    parameter = ",\n//         ".join([*(f"A{i}=&A{i + 1}" for i in range(depth)), f"A{depth}=PGMX"])
    text = f"//J JOB\n//P PROC {parameter}\n//S EXEC PGM=&A0\n// PEND\n"
    text += "".join(f"//C{i} EXEC P\n" for i in range(call_count))

    assert [(i.name, i.line) for i in read_jcl_source(text).import_list] == [("PGMX", depth + 3)]


def _program_set(text: str) -> set[tuple[str, int]]:
    """Return (name, line) of the programs a JCL source runs."""
    return {(i.name, i.line) for i in read_jcl_source(text).import_list if i.kind == "CALL"}


def test_set_statement_counts_in_its_procedure_or_job():
    assert _program_set(
        "//P PROC\n// SET X=PGMA\n//S1 EXEC PGM=&X\n// PEND\n// SET X=PGMB\n//C1 EXEC P\n",
    ) == {("PGMA", 3)}
    assert _program_set("//P PROC\n//S1 EXEC PGM=&X\n// SET X=PGMA\n// PEND\n//C1 EXEC P\n") == set()
    assert _program_set("//Q PROC\n// SET X=QQQ\n// PEND\n//S1 EXEC PGM=&X\n") == set()
    assert _program_set(
        "// SET X=PGMJ\n//P PROC\n//S1 EXEC PGM=&X\n// PEND\n//C1 EXEC P\n",
    ) == {("PGMJ", 3)}


def test_continued_set_statement_reads_values_before_it():
    assert _program_set("// SET A=OLD\n// SET A=NEW,\n//  B=&A\n//S EXEC PGM=&B\n") == {("OLD", 4)}


def test_symbol_without_an_earlier_value_does_not_refer_to_itself():
    for text in ("//J JOB\n// SET A=&A\n", "//P PROC A=&A\n//S EXEC PGM=X\n"):
        assert [r for r in read_jcl_source(text).reference_list if r.kind == "symbol"] == []


def test_quoted_ims_parm_in_parentheses():
    assert _program_set("//S EXEC PGM=DFSRRC00,PARM='(DLI,PGMA,PSB)'\n") == {
        ("DFSRRC00", 1), ("PGMA", 1),
    }


def test_statement_with_a_name_only_does_not_end_the_job():
    text = "//J JOB\n//S1 EXEC PGM=A\n//NAMEONLY\n//S2 EXEC PGM=B\n"
    definition_dict = {d.name: (d.start_line, d.end_line) for d in read_jcl_source(text).definition_list}

    assert definition_dict["J"] == (1, 4)


def test_default_that_names_another_parameter():
    text = "//P PROC HLQ=X,\n//   CPY=&HLQ..CPY,A=&A\n//S EXEC PGM=Y\n"
    reference_set = {
        (r.name, r.line, r.definition_line) for r in read_jcl_source(text).reference_list
        if r.kind == "symbol"
    }

    assert reference_set == {("HLQ", 2, 1)}


def _symbol_reference_set(text: str) -> set[tuple[str, int, int]]:
    """Return (name, line, line of the definition) of the symbol references of a JCL source."""
    return {
        (r.name, r.line, r.definition_line)
        for r in read_jcl_source(text).reference_list if r.kind == "symbol"
    }


def test_symbol_references_follow_the_values():
    later_set = "//J JOB\n//S1 EXEC PGM=&P\n// SET P=PROGA\n//S2 EXEC PGM=&P\n"
    assert _symbol_reference_set(later_set) == {("P", 4, 3)}
    assert _program_set(later_set) == {("PROGA", 4)}
    assert _symbol_reference_set("//J JOB\n// SET A=&A\n// SET A=X\n") == set()

    caller_set = (
        "//J JOB\n// SET X=PGMA\n//P PROC\n//PS EXEC PGM=&X\n// PEND\n// SET X=PGMB\n//C EXEC P\n"
    )
    assert _symbol_reference_set(caller_set) == {("X", 4, 6)}
    assert _program_set(caller_set) == {("PGMB", 4)}


def test_symbol_of_a_procedure_called_from_another_procedure():
    text = (
        "// SET X=AAA\n//P1 PROC\n//PS EXEC PGM=&X\n// PEND\n"
        "//P2 PROC\n//QS EXEC P1\n// PEND\n//C1 EXEC P2\n"
    )
    assert _program_set(text) == {("AAA", 3)}
    assert _symbol_reference_set(text) == {("X", 3, 1)}


def test_default_that_is_the_symbol_itself():
    text = "//P PROC A=&A\n//PS EXEC PGM=&A\n// PEND\n// SET A=JA\n//C EXEC P\n"
    assert _program_set(text) == {("JA", 2)}
    assert _symbol_reference_set(text) == {("A", 1, 4), ("A", 2, 1), ("A", 2, 4)}


def test_long_statement():
    text = "//S EXEC PGM=X,PARM='A\n" + "//          DD\n" * 40000

    assert _program_set(text) == {("X", 1)}


def test_include_member_does_not_go_to_a_job(tmp_path):
    project_dir = _make_project(tmp_path, {
        "jcl/X.jcl": "//X JOB\n// INCLUDE MEMBER=COMMON\n",
        "jcl/COMMON.jcl": "//COMMON JOB\n//S EXEC PGM=A\n",
        "lib/COMMON": "//SYSIN DD DUMMY\n",
    })

    assert _graph(project_dir)["jcl/X.jcl"] == {"lib/COMMON"}


def test_same_line_parameter_reference_is_a_usage(tmp_path):
    project_dir = _make_project(tmp_path, {"proc/P.prc": "//P PROC HLQ=X,CPY=&HLQ..CPY\n//S EXEC PGM=Y\n"})

    usage_list = _analyse(project_dir)["proc/P.prc"]["same_file_usages"]

    assert [(u["name"], u["lines"], u["target_start_line"]) for u in usage_list] == [("HLQ", [1], 1)]


def test_override_written_as_another_form_of_the_symbol():
    text = "//J JOB\n// SET X=PGMA\n//P PROC\n//S EXEC PGM=&X\n// PEND\n//C EXEC P,X=&X.\n"
    assert _program_set(text) == {("PGMA", 4)}
    assert ("X", 4, 2) in _symbol_reference_set(text)


def test_nameless_job_is_no_member(tmp_path):
    project_dir = _make_project(tmp_path, {
        "jcl/MAIN.jcl": "//MAIN JOB\n//S1 EXEC MYPROC\n",
        "jcl/MYPROC": "//         JOB (1),X\n//S EXEC PGM=A\n",
    })

    assert _graph(project_dir)["jcl/MAIN.jcl"] == set()
    assert not has_language(os.path.join(project_dir, "jcl/MYPROC"))


def test_references_in_call_order():
    text = (
        "//J JOB\n//P PROC\n//S EXEC PGM=&X\n// PEND\n"
        "// SET X=PGMA\n//C1 EXEC P\n// SET X=PGMB\n//C2 EXEC P\n"
    )
    reference_list = [
        r.definition_line for r in read_jcl_source(text).reference_list if r.kind == "symbol"
    ]
    assert reference_list == [5, 7]


def test_many_calls_and_set_statements_stay_fast():
    use_count = 2000
    text = "//J JOB\n//P PROC\n" + "".join(f"//S{i} EXEC PGM=&X\n" for i in range(use_count))
    text += "// PEND\n// SET X=PGMA\n" + "".join(f"//C{i} EXEC P\n" for i in range(use_count))
    text += "".join(f"// SET A=V{i}\n//T{i} EXEC PGM=&A,COND=(4,LT,T{i})\n" for i in range(use_count))

    import_set = {(i.name, i.line) for i in read_jcl_source(text).import_list}

    assert ("PGMA", 3) in import_set and ("V0", 2 * use_count + 6) in import_set


def test_symbol_refers_to_its_parameter_and_values_in_call_order():
    assert _symbol_reference_set("//P PROC A=&A\n//S EXEC PGM=&A\n") == {("A", 2, 1)}
    text = "//J JOB\n// SET A=Y\n//P PROC A=&A\n//S EXEC PGM=&A\n// PEND\n//C1 EXEC P\n//C2 EXEC P,A=Z\n"
    reference_list = [
        r.definition_line for r in read_jcl_source(text).reference_list
        if r.kind == "symbol" and r.line == 4
    ]
    assert reference_list == [3, 2]
    assert _program_set(text) == {("Y", 4), ("Z", 4)}


def test_cached_lookup_after_a_set_statement():
    count = 2000
    text = "//J JOB\n//P PROC\n// SET A=&B\n" + "".join(f"//S{i} EXEC PGM=&A\n" for i in range(count))
    text += "// PEND\n" + "".join(f"//C{i} EXEC P,B=SAME\n" for i in range(count))

    assert len(read_jcl_source(text).import_list) == count


def test_cond_on_a_continued_exec_line_names_an_earlier_step():
    text = "//J JOB\n//S1 EXEC PGM=A\n//S1 EXEC PGM=B,\n//  COND=(4,LT,S1)\n"
    step_list = [(r.line, r.definition_line) for r in read_jcl_source(text).reference_list if r.kind == "step"]

    assert step_list == [(4, 2)]


def test_systsin_override_of_a_procedure_step():
    text = (
        "//P PROC\n//TSO EXEC PGM=IKJEFT01\n// PEND\n//S EXEC P\n"
        "//TSO.SYSTSIN DD *\n RUN PROGRAM(MYPGM) PLAN(X)\n"
    )
    assert _program_set(text) == {("IKJEFT01", 2), ("MYPGM", 6)}


def test_many_members_without_an_extension(tmp_path):
    count = 1500
    file_dict = {"jcl/MAIN.jcl": "//MAIN JOB\n" + "".join(f"// INCLUDE MEMBER=M{i}\n" for i in range(count))}
    file_dict.update({f"lib/M{i}": f"//S{i} EXEC PGM=X\n" for i in range(count)})
    project_dir = _make_project(tmp_path, file_dict)

    assert len(_graph(project_dir)["jcl/MAIN.jcl"]) == count


def test_nameless_dd_concatenated_to_systsin():
    text = (
        "//S1 EXEC PGM=IKJEFT01\n//SYSTSIN DD DSN=A.B(X),DISP=SHR\n//        DD *\n"
        "  RUN PROGRAM(PROGZ) PLAN(P)\n"
    )
    assert _program_set(text) == {("IKJEFT01", 1), ("PROGZ", 4)}


def test_lines_of_names_on_continuation_lines():
    text = "//J JOB\n//S1 EXEC PGM=A\n//S2 EXEC PGM=B\n//S3 EXEC PGM=C,COND=((4,LT,S1),\n//  (8,LT,S2))\n"
    step_set = {(r.name, r.line) for r in read_jcl_source(text).reference_list if r.kind == "step"}
    assert step_set == {("S1", 4), ("S2", 5)}
    assert _program_set("//S EXEC PGM=DFSRRC00,PARM=(DLI,\n//  PGMX,PSB1)\n") == {
        ("DFSRRC00", 1), ("PGMX", 2),
    }


def test_many_jobs_with_the_same_in_stream_procedure():
    job_count = 4000
    text = "".join(
        f"//J{i} JOB 1\n//P PROC X=A{i}\n//S EXEC PGM=&X\n// PEND\n//C EXEC P\n"
        for i in range(job_count)
    )

    import_list = read_jcl_source(text).import_list

    assert len(import_list) == job_count and import_list[-1].name == f"A{job_count - 1}"


def test_one_index_per_procedure_file(tmp_path, monkeypatch):
    import codetwine.jcl_reference as jcl_reference

    project_dir = _make_project(tmp_path, {
        "jcl/RUN.jcl": "//RUN JOB\n" + "".join(f"//C{i} EXEC BIG\n" for i in range(50)),
        "proc/BIG.prc": "//BIG PROC\n//S1 EXEC PGM=X\n",
    })
    build_count = []
    original_init = jcl_reference._DefinitionIndex.__init__

    def counting_init(self, jcl_source):
        build_count.append(1)
        original_init(self, jcl_source)

    monkeypatch.setattr(jcl_reference._DefinitionIndex, "__init__", counting_init)
    _graph(project_dir)

    assert len(build_count) <= 3


def test_many_same_named_files_without_a_language(tmp_path):
    file_dict = {"jcl/RUN.jcl": "//RUN JOB\n" + "// INCLUDE MEMBER=CONFIG\n" * 50}
    file_dict.update({f"svc{i}/config.yaml": "name: x\n" for i in range(800)})
    file_dict["lib/CONFIG"] = "//SYSIN DD DUMMY\n"
    project_dir = _make_project(tmp_path, file_dict)

    assert _graph(project_dir)["jcl/RUN.jcl"] == {"lib/CONFIG"}


def test_empty_delimiter_ends_data_at_the_standard_one():
    text = "//S1 EXEC PGM=IKJEFT01\n//SYSTSIN DD DATA,DLM=''\n RUN PROGRAM(PG1)\n/*\n//S2 EXEC PGM=B\n"
    assert _program_set(text) == {("IKJEFT01", 1), ("PG1", 3), ("B", 5)}


def test_empty_delimiter_of_star_data_ends_at_the_next_statement():
    text = "//J JOB\n//S1 EXEC PGM=A\n//SYSIN DD *,DLM=''\ndata\n//S2 EXEC PGM=B\n"
    assert _program_set(text) == {("A", 2), ("B", 5)}


def test_negated_if_tests_and_job_group_files(tmp_path):
    text = "//J JOB\n//S1 EXEC PGM=A\n//S2 EXEC PGM=B\n// IF S1.¬ABEND AND S2.^RUN THEN\n//S3 EXEC PGM=C\n// ENDIF\n"
    step_set = {(r.name, r.line) for r in read_jcl_source(text).reference_list if r.kind == "step"}
    assert step_set == {("S1", 4), ("S2", 4)}

    project_dir = _make_project(tmp_path, {
        "jcl/GROUP1.jcl": "//GROUP1 JOBGROUP OWNER=X\n//JOBA JOB 1\n//S EXEC PGM=A\n",
        "jcl/ENDIF1.jcl": "// ENDIF\n//S EXEC PGM=A\n",
    })
    _graph(project_dir)
    for file_rel in ("jcl/GROUP1.jcl", "jcl/ENDIF1.jcl"):
        assert language_ext(os.path.join(project_dir, file_rel)) == "jcl"


def test_job_group_file_is_a_job(tmp_path):
    group_text = "//TESTGRP JOBGROUP OWNER=X\n//ENDGRP ENDGROUP\n//JOBA JOB 1\n//S1 EXEC PGM=PA\n"
    project_dir = _make_project(tmp_path, {
        "jcl/NIGHTGRP": group_text,
        "jcl/GROUP1.jcl": group_text,
        "jcl/CALLER.jcl": "//CALLER JOB\n//S1 EXEC GROUP1\n//S2 EXEC NIGHTGRP\n",
        "jcl/NOTIFY1.jcl": "//N1 NOTIFY EMAIL='OPS@EXAMPLE.COM'\n//J JOB\n",
        "src/PA.cbl": _program("PA"),
    })

    graph = _graph(project_dir)

    assert graph["jcl/NIGHTGRP"] == {"src/PA.cbl"}
    assert graph["jcl/CALLER.jcl"] == set()
    assert language_ext(os.path.join(project_dir, "jcl/NOTIFY1.jcl")) == "jcl"


def test_long_continued_tso_command():
    count = 80000
    text = "//S1 EXEC PGM=IKJEFT01\n//SYSTSIN DD *\n RUN PROGRAM(PG1) +\n" + " X +\n" * count + " END\n"

    assert _program_set(text) == {("IKJEFT01", 1), ("PG1", 3)}


def test_tso_continuation_characters():
    def tso_program_list(*code_tuple: str) -> set[tuple[str, int]]:
        data = "".join(f"{code}\n" for code in code_tuple)
        return _program_set(f"//S1 EXEC PGM=IKJEFT01\n//SYSTSIN DD *\n{data}") - {("IKJEFT01", 1)}

    assert tso_program_list(" RUN PROGRAM(PG-", "MA)") == {("PGMA", 3)}
    assert tso_program_list(" RUN PROG-", "RAM(PGMA)") == {("PGMA", 3)}
    assert tso_program_list(" RUN PROGRAM(+", "     PGMA)") == {("PGMA", 3)}
    assert tso_program_list(" RUN PROGRAM(PGMA) -", "     PLAN(X)") == {("PGMA", 3)}


def test_include_member_is_a_usage(tmp_path):
    project_dir = _make_project(tmp_path, {
        "jcl/NIGHTLY.jcl": "//NIGHTLY JOB\n//         INCLUDE MEMBER=COMMON\n",
        "proc/COMMON": "//INC1 EXEC PGM=X\n",
    })

    result = _analyse(project_dir)

    assert _usage(result["jcl/NIGHTLY.jcl"]["callee_usages"], "from") == {
        ("COMMON", None, None, "proc/COMMON", (2,)),
    }
    assert _usage(result["proc/COMMON"]["caller_usages"], "file") == {
        ("COMMON", None, None, "jcl/NIGHTLY.jcl", (2,)),
    }


# == CSD ========================================================================

CSD_SOURCE = """\
* CICS resources
 DEFINE PROGRAM(COACTVWC) GROUP(CARDDEMO)
 DESCRIPTION(VIEW ACCT (MAIN))
        CONCURRENCY(QUASIRENT) TRANSID(CAVW)
 DEFINE TRANSACTION(CAVW) GROUP(CARDDEMO)
        PROGRAM(COACTVWC) TWASIZE(0)
Def Mapset(COACTVW) G(CARDDEMO)
 DEFINE FILE(ACCTDAT) GROUP(CARDDEMO)
        DSNAME(AWS.ACCTDATA)
Add Group(CARDDEMO) List(CARDLIST)
"""

BMS_MAPSET_SOURCE = """\
COACTVW  DFHMSD TYPE=MAP,MODE=INOUT,LANG=COBOL
CACTVWA  DFHMDI SIZE=(24,80)
ACCTSID  DFHMDF POS=(2,1),LENGTH=11
         DFHMSD TYPE=FINAL
"""


def test_csd_file(tmp_path):
    from codetwine.extractors.csd_source import read_csd_source

    source = read_csd_source(CSD_SOURCE)
    assert [(d.type, d.name, d.start_line, d.end_line) for d in source.definition_list] == [
        ("define_program", "COACTVWC", 2, 4),
        ("define_transaction", "CAVW", 5, 6),
        ("define_mapset", "COACTVW", 7, 7),
        ("define_file", "ACCTDAT", 8, 9),
    ]
    assert {(i.kind, i.name, i.line) for i in source.import_list} == {
        ("CALL", "COACTVWC", 2), ("CALL", "COACTVWC", 6), ("MAPSET", "COACTVW", 7),
    }

    project_dir = _make_project(tmp_path, {
        "csd/CARDDEMO.CSD": CSD_SOURCE,
        "cbl/COACTVWC.cbl": _program("COACTVWC"),
        "bms/COACTVW.bms": BMS_MAPSET_SOURCE,
        "cpy/COACTVW.cpy": "       01  CACTVWAI.\n           05  ACCTSIDI  PIC X(11).\n",
    })
    result = _analyse(project_dir)

    assert result["csd/CARDDEMO.CSD"]["language"] == "csd"
    assert _usage(result["csd/CARDDEMO.CSD"]["callee_usages"], "from") == {
        ("COACTVWC", "COACTVWC", 1, "cbl/COACTVWC.cbl", (2, 6)),
        ("COACTVW", None, None, "bms/COACTVW.bms", (7,)),
    }
    assert _usage(result["csd/CARDDEMO.CSD"]["same_file_usages"], "") == {
        ("CAVW", "CAVW", 5, "", (4,)),
        ("COACTVWC", "COACTVWC", 2, "", (6,)),
    }
    assert ("COACTVWC", None, None, "csd/CARDDEMO.CSD", (2, 6)) in _usage(
        result["cbl/COACTVWC.cbl"]["caller_usages"], "file",
    )


def test_csd_commands_in_the_sysin_data_of_dfhcsdup(tmp_path):
    project_dir = _make_project(tmp_path, {
        "jcl/CSDDEF.jcl": (
            "//CSDDEF JOB 1\n//S1 EXEC PGM=DFHCSDUP\n//SYSIN DD *\n"
            "Define Transaction(SSC1) Group(GENA)\n       Program(LGTESTC1)\n"
            "/*\n//S2 EXEC PGM=IDCAMS\n//SYSIN DD *\n DEFINE PROGRAM(NOTCSD)\n/*\n"
        ),
        "cbl/LGTESTC1.cbl": _program("LGTESTC1"),
        "cbl/NOTCSD.cbl": _program("NOTCSD"),
    })

    assert _graph(project_dir)["jcl/CSDDEF.jcl"] == {"cbl/LGTESTC1.cbl"}


def _csd_command_list(text: str):
    from codetwine.parsers.csd_command import csd_command_list

    return csd_command_list(list(enumerate(text.splitlines(), 1)))


def test_csd_value_continued_with_an_asterisk():
    from codetwine.parsers.csd_command import csd_definition

    command_list = _csd_command_list(
        " DEFINE PROGRAM(PRG1) GROUP(GRP1) DESCRIPTION(FIRST PART OF *\n"
        "        THE TEXT) TRANSID(TRN1)\n"
        " DEFINE TRANSACTION(TRN1) GROUP(GRP1) PROGRAM(PRG1)\n"
    )
    assert [(c.verb, c.start_line, c.end_line) for c in command_list] == [
        ("DEFINE", 1, 2), ("DEFINE", 3, 3),
    ]
    keyword_dict = {k.name: (k.value, k.line) for k in command_list[0].keyword_list}
    assert keyword_dict["DESCRIPTION"] == ("FIRST PART OF THE TEXT", 1)
    assert keyword_dict["TRANSID"] == ("TRN1", 2)
    definition = csd_definition(command_list[0])
    assert [(n.type_tuple, n.name, n.line) for n in definition.attribute_list] == [
        (("TRANSACTION",), "TRN1", 2),
    ]


def test_csd_quote_and_unclosed_parenthesis_stay_in_their_value():
    command_list = _csd_command_list(
        " DEFINE PROGRAM(PRG1) GROUP(GRP1) DESCRIPTION(CUSTOMER'S DATA)\n"
        " DEFINE PROGRAM(PRG2) GROUP(GRP1) DESCRIPTION('IT''S (A) TEST')\n"
        " DEFINE PROGRAM(PRG3) GROUP(GRP1) DESCRIPTION(OPEN ( TEXT)\n"
        " DEFINE PROGRAM(PRG4) GROUP(GRP1)\n"
    )
    assert [c.keyword_list[0].value for c in command_list] == ["PRG1", "PRG2", "PRG3", "PRG4"]
    assert command_list[1].keyword_list[2].value == "IT'S (A) TEST"
    assert command_list[2].keyword_list[2].value == "OPEN ( TEXT"


def test_csd_names_types_and_userdefine():
    from codetwine.extractors.csd_source import read_csd_source

    source = read_csd_source(
        " USERDEFINE PROGRAM(PRG#1) GROUP(GRP1)\n"
        " DEF TDQ(TDQ1) GROUP(GRP1) TYPE(INDIRECT) INDIRECTNAME(TDQ2)\n"
        " DEF TDQ(TDQ2) GROUP(GRP1)\n"
        " DEF DB2TRAN(DBT1) GROUP(GRP1) ENTRY(DBE1) TRANSID(TRN1)\n"
        " DEF DB2E(DBE1) GROUP(GRP1)\n"
        " DEF CONN(CON1) GROUP(GRP1)\n"
        " DEF TRANS(TRN1) GROUP(GRP1) PROG(PRG#1) REMOTESYSTEM(CON1)\n"
    )
    assert [(d.type, d.name) for d in source.definition_list] == [
        ("define_program", "PRG#1"), ("define_tdqueue", "TDQ1"), ("define_tdqueue", "TDQ2"),
        ("define_db2tran", "DBT1"), ("define_db2entry", "DBE1"), ("define_connection", "CON1"),
        ("define_transaction", "TRN1"),
    ]
    assert {(r.name, r.line, r.definition_line) for r in source.reference_list} >= {
        ("TDQ2", 2, 3), ("DBE1", 4, 5), ("TRN1", 4, 7), ("PRG#1", 7, 1), ("CON1", 7, 6),
    }
    assert {(i.kind, i.name) for i in source.import_list} == {("CALL", "PRG#1")}


def test_csd_attribute_prefers_the_resource_of_its_group():
    from codetwine.extractors.csd_source import read_csd_source

    source = read_csd_source(
        " DEFINE PROGRAM(PRG1) GROUP(GRPA)\n"
        " DEFINE PROGRAM(PRG1) GROUP(GRPB)\n"
        " DEFINE TRANSACTION(TRN1) GROUP(GRPB) PROGRAM(PRG1)\n"
        " DEFINE TRANSACTION(TRN2) GROUP(GRPC) PROGRAM(PRG1)\n"
    )
    resource_set = {
        (r.name, r.line, r.definition_line) for r in source.reference_list if r.kind == "resource"
    }
    assert resource_set == {("PRG1", 3, 2), ("PRG1", 4, 1)}


def test_csd_mapset_goes_to_the_bms_source_that_defines_it(tmp_path):
    project_dir = _make_project(tmp_path, {
        "csd/APP.csd": " DEFINE MAPSET(COACTVW) GROUP(GRP1)\n",
        "bms/ZMAPS.bms": BMS_MAPSET_SOURCE,
        "bms/COACTVW.bms": (
            "OTHER    DFHMSD TYPE=MAP,MODE=INOUT,LANG=COBOL\n         DFHMSD TYPE=FINAL\n"
        ),
    })

    assert _graph(project_dir)["csd/APP.csd"] == {"bms/ZMAPS.bms"}


def test_csd_short_abbreviations_and_blank_before_the_value():
    from codetwine.extractors.csd_source import read_csd_source

    source = read_csd_source(
        " DEFINE TD (Q2) GROUP(G)\n"
        " DEFINE TD(Q1) GROUP(G) TYPE(INTRA) INDIRECTNAME (Q2)\n"
        " DEFINE TY(INTRA) GROUP(G)\n"
        " DEFINE FOO(BAR) GROUP(G)\n"
        " DEFINE IPCONN(SYS2) GROUP(G)\n"
        " DEFINE TRANSACTION(T1) GROUP(G) PROGRAM (P1) DUMP(YES)\n"
        "        REMOTESYSTEM(SYS2)\n"
    )
    assert [(d.type, d.name) for d in source.definition_list] == [
        ("define_tdqueue", "Q2"), ("define_tdqueue", "Q1"), ("define_typeterm", "INTRA"),
        ("define_ipconn", "SYS2"), ("define_transaction", "T1"),
    ]
    assert {(r.name, r.line, r.definition_line) for r in source.reference_list} == {
        ("Q2", 2, 1), ("P1", 6, 0), ("SYS2", 7, 5),
    }
    assert {(i.kind, i.name) for i in source.import_list} == {("CALL", "P1")}


def test_csd_unclosed_quote_ends_at_the_first_parenthesis():
    command_list = _csd_command_list(" DEFINE PROGRAM(P1) DESCRIPTION('IT S) TRANSID(T1)\n")
    assert [(k.name, k.value) for k in command_list[0].keyword_list] == [
        ("PROGRAM", "P1"), ("DESCRIPTION", "IT S"), ("TRANSID", "T1"),
    ]


def test_csd_unbalanced_parentheses_stay_fast():
    import time

    from codetwine.extractors.csd_source import read_csd_source

    text = " DEFINE PROGRAM(P1) GROUP(G)\n" + "  DESCRIPTION(A (B) X(Y\n" * 20000
    start = time.monotonic()
    read_csd_source(text)
    assert time.monotonic() - start < 5


def test_csd_group_before_the_resource_and_other_attribute_names():
    from codetwine.extractors.csd_source import read_csd_source

    source = read_csd_source(
        " DEFINE GROUP(G) PROGRAM(P1)\n"
        " DEFINE PROFILE(PRF1) GROUP(G)\n"
        " DEFINE GROUP(G) TRANSACTION(T1) PROGRAM(P1) TRPROF(PRF1)\n"
        " DEFINE TERMINAL(TRM1) GROUP(G) PRINTER(TRM2)\n"
        " DEFINE TERMINAL(TRM2) GROUP(G)\n"
    )
    assert [(d.type, d.name) for d in source.definition_list] == [
        ("define_program", "P1"), ("define_profile", "PRF1"), ("define_transaction", "T1"),
        ("define_terminal", "TRM1"), ("define_terminal", "TRM2"),
    ]
    assert {(r.name, r.line, r.definition_line) for r in source.reference_list} == {
        ("P1", 1, 0), ("P1", 3, 0), ("P1", 3, 1), ("PRF1", 3, 2), ("TRM2", 4, 5),
    }


def test_csd_attribute_named_like_a_command_and_quote_after_a_continuation():
    from codetwine.extractors.csd_source import read_csd_source

    source = read_csd_source(
        " DEFINE CONNECTION(C1) GROUP(G)\n"
        " DEFINE FILE(F1) GROUP(G)\n"
        "        ADD (YES) BROWSE(YES)\n"
        "        DELETE (YES) REMOTESYSTEM(C1)\n"
    )
    assert [(d.name, d.start_line, d.end_line) for d in source.definition_list] == [
        ("C1", 1, 1), ("F1", 2, 4),
    ]
    assert {(r.name, r.line, r.definition_line) for r in source.reference_list} == {("C1", 4, 1)}

    command_list = _csd_command_list(
        " DEFINE PROGRAM(P1) GROUP(G) DESCRIPTION(*\n'a) b *\nc')\n DEFINE PROGRAM(P2)\n"
    )
    assert [k.value for k in command_list[0].keyword_list] == ["P1", "G", "a) b c"]
    assert [c.start_line for c in command_list] == [1, 4]


def test_csd_continuation_follows_the_values_as_they_are_read():
    for first_line in (
        " DEFINE PROGRAM(P1) GROUP(G) DESCRIPTION('A' (B) *",
        " DEFINE PROGRAM(P1) GROUP(G) DESCRIPTION(A (')') *",
        " DEFINE PROGRAM(P1) GROUP(G) ( *",
    ):
        command_list = _csd_command_list(first_line + "\n DEFINE PROGRAM(P2) GROUP(G)\n")
        assert [c.start_line for c in command_list] == [1, 2], first_line
    command_list = _csd_command_list(" DEFINE PROGRAM(P1) DESCRIPTION('A' *\n  B)\n")
    assert [(c.start_line, c.end_line) for c in command_list] == [(1, 2)]


def test_csd_partitions_attribute_names_no_partitionset():
    from codetwine.extractors.csd_source import read_csd_source

    source = read_csd_source(
        " DEFINE PARTITIONSET(YES) GROUP(G)\n DEFINE TYPETERM(TT1) GROUP(G) PARTITIONS(YES)\n"
    )
    assert source.reference_list == []


def test_csd_jvm_attribute_and_name_line_of_a_value_on_the_next_line():
    from codetwine.extractors.csd_source import read_csd_source

    source = read_csd_source(
        " DEFINE TRANSACTION\n   (TRN1) GROUP(G)\n  PROGRAM\n (PRG1)\n"
        " DEFINE PROGRAM(PRG1) GROUP(G) JVM(YES)\n DEFINE JVMSERVER(YES) GROUP(G)\n"
    )
    assert [(d.name, d.start_line, d.name_line) for d in source.definition_list] == [
        ("TRN1", 1, 2), ("PRG1", 5, 5), ("YES", 6, 6),
    ]
    assert {(r.name, r.line, r.definition_line) for r in source.reference_list} == {
        ("PRG1", 4, 0), ("PRG1", 4, 5), ("PRG1", 5, 0),
    }


def test_csd_extension_without_csd_commands_has_no_language(tmp_path):
    project_dir = _make_project(tmp_path, {
        "sound/tone.csd": "<CsoundSynthesizer>\n<CsInstruments>\ninstr 1\nendin\n",
        "csd/APP.csd": "* resources\n\n DEFINE PROGRAM(P1) GROUP(G)\n",
        "cbl/P1.cbl": _program("P1"),
    })
    assert _graph(project_dir)["csd/APP.csd"] == {"cbl/P1.cbl"}
    assert language_ext(os.path.join(project_dir, "sound/tone.csd")) == ""
    assert language_ext(os.path.join(project_dir, "csd/APP.csd")) == "csd"


def test_numbered_blank_line_before_the_first_command_or_statement(tmp_path):
    project_dir = _make_project(tmp_path, {
        "csd/APP.csd": (
            " " * 72 + "00010000\n DEFINE PROGRAM(P1) GROUP(G)" + " " * 44 + "00020000\n"
        ),
        "jcl/NIGHTLY.jcl": " " * 72 + "00010000\n//NIGHTLY JOB 1\n//S1 EXEC PGM=P1\n",
        "cbl/P1.cbl": _program("P1"),
    })
    graph = _graph(project_dir)

    assert graph["csd/APP.csd"] == {"cbl/P1.cbl"}
    assert graph["jcl/NIGHTLY.jcl"] == {"cbl/P1.cbl"}


def test_jcl_gate_reads_the_columns_of_a_statement(tmp_path):
    job_line = "//MYJOB" + " " * 62 + "JOB00000100"
    project_dir = _make_project(tmp_path, {
        "jcl/LATE.jcl": job_line + "\n//S1 EXEC PGM=P1\n",
        "jcl/MARK.jcl": " " * 71 + "X\n//MARK JOB 1\n//S1 EXEC PGM=P1\n",
        "cbl/P1.cbl": _program("P1"),
    })
    _graph(project_dir)

    assert language_ext(os.path.join(project_dir, "jcl/LATE.jcl")) == ""
    assert language_ext(os.path.join(project_dir, "jcl/MARK.jcl")) == "jcl"


def _cics_program(name: str, procedure_text: str, data_text: str = "") -> str:
    """Return a COBOL program whose procedure division holds the text given."""
    return (
        "       IDENTIFICATION DIVISION.\n"
        f"       PROGRAM-ID. {name}.\n"
        "       DATA DIVISION.\n"
        "       WORKING-STORAGE SECTION.\n"
        f"{data_text}"
        "       PROCEDURE DIVISION.\n"
        f"{procedure_text}"
        "           GOBACK.\n"
    )


def test_cics_transid_goes_to_the_define_transaction(tmp_path):
    project_dir = _make_project(tmp_path, {
        "app/cbl/MENU.cbl": _cics_program(
            "MENU",
            "           EXEC CICS RETURN TRANSID('TX01') END-EXEC.\n"
            "           EXEC CICS START TRANSID (WS-NEXT) END-EXEC.\n"
            "           EXEC CICS RETURN TRANSID('NONE') END-EXEC.\n",
            "       01  WS-NEXT PIC X(4) VALUE 'TX02'.\n",
        ),
        "app/csd/APP.csd": (
            " DEFINE TRANSACTION(TX01) GROUP(G) PROGRAM(MENU)\n"
            " DEFINE TRANSACTION(TX02) GROUP(G) PROGRAM(MENU)\n"
        ),
        "other/csd/OTHER.csd": " DEFINE TRANSACTION(TX01) GROUP(H) PROGRAM(MENU)\n",
        "app/jcl/CSDJOB.jcl": (
            "//CSDJOB JOB 1\n//S1 EXEC PGM=DFHCSDUP\n//SYSIN DD *\n"
            " DEFINE TRANSACTION(TX03) GROUP(G) PROGRAM(MENU)\n/*\n"
        ),
        "app/cbl/LAST.cbl": _cics_program(
            "LAST", "           EXEC CICS RETURN TRANSID('TX03') END-EXEC.\n",
        ),
    })
    graph = _graph(project_dir)
    assert graph["app/cbl/MENU.cbl"] == {"app/csd/APP.csd"}
    assert graph["app/cbl/LAST.cbl"] == {"app/jcl/CSDJOB.jcl"}

    result = _analyse(project_dir)
    assert _usage(result["app/cbl/MENU.cbl"]["callee_usages"], "from") == {
        ("TX01", "TX01", 1, "app/csd/APP.csd", (7,)),
        ("TX02", "TX02", 2, "app/csd/APP.csd", (8,)),
    }
    assert _usage(result["app/cbl/LAST.cbl"]["callee_usages"], "from") == {
        ("TX03", None, None, "app/jcl/CSDJOB.jcl", (6,)),
    }
    assert ("TX01", None, None, "app/cbl/MENU.cbl", (7,)) in _usage(
        result["app/csd/APP.csd"]["caller_usages"], "file",
    )


def test_nearest_file_takes_the_directory_of_the_current_file_first():
    from codetwine.cobol_file_index import _nearest_file

    assert _nearest_file(["csd/a/M.bms", "csd/M.bms"], "csd/APP.csd") == "csd/M.bms"
    assert _nearest_file(["other/csd/A.csd", "app/csd/B.csd"], "app/cbl/P.cbl") == "app/csd/B.csd"
    assert _nearest_file(["b/A.csd", "A.csd"], "P.cbl") == "A.csd"
    assert _nearest_file([], "P.cbl") is None


def test_each_name_goes_to_the_nearest_file_of_several(tmp_path):
    # The first sub-application in path order holds a file of every name as well
    file_dict = {}
    for app in ("app1", "app2"):
        file_dict |= {
            f"{app}/cpy/REC.cpy": "       01  REC-A PIC X.\n",
            f"{app}/cbl/MAIN.cbl": _cics_program("MAIN", ""),
            f"{app}/sub/SUB.cbl": _program("SUB"),
            f"{app}/csd/APP.csd": " DEFINE TRANSACTION(TX01) GROUP(G) PROGRAM(MAIN)\n",
            f"{app}/proc/RUNP.prc": "//RUNP PROC\n//S1 EXEC PGM=SUB\n",
            f"{app}/inc/STEPS.jcl": "//S2 EXEC PGM=SUB\n",
            f"{app}/proc/NOEXTP": "//NOEXTP PROC\n//S3 EXEC PGM=SUB\n",
        }
    file_dict["app2/cbl/CALLER.cbl"] = _cics_program(
        "CALLER",
        "           CALL 'SUB'.\n"
        "           EXEC CICS RETURN TRANSID('TX01') END-EXEC.\n",
        "       COPY REC.\n",
    )
    file_dict["app2/jcl/RUN.jcl"] = (
        "//RUN JOB 1\n//S1 EXEC PGM=MAIN\n//S2 EXEC RUNP\n//S3 EXEC NOEXTP\n"
        "// INCLUDE MEMBER=STEPS\n"
    )
    project_dir = _make_project(tmp_path, file_dict)

    graph = _graph(project_dir)

    assert graph["app2/cbl/CALLER.cbl"] == {
        "app2/cpy/REC.cpy", "app2/sub/SUB.cbl", "app2/csd/APP.csd",
    }
    assert graph["app2/csd/APP.csd"] == {"app2/cbl/MAIN.cbl"}
    assert graph["app2/jcl/RUN.jcl"] == {
        "app2/cbl/MAIN.cbl", "app2/proc/RUNP.prc", "app2/proc/NOEXTP", "app2/inc/STEPS.jcl",
    }
    assert language_ext(os.path.join(project_dir, "app2/proc/NOEXTP")) == "jcl"
    assert not has_language(os.path.join(project_dir, "app1/proc/NOEXTP"))
