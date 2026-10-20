import os
import re
import logging
from codetwine.parsers.ts_parser import parse_file
from codetwine.extractors.jcl_source import INCLUDE_KIND, PROC_KIND, JclSource
from codetwine.config.settings import (
    JCL_EXT_SET,
    add_copy_target_ext,
    language_ext,
    set_copy_target_ext,
)
from codetwine.utils.file_utils import add_file_name, head_codec

logger = logging.getLogger(__name__)

# Extension a JCL file without a JCL extension is analyzed with (lower case, without ".")
_JCL_EXT = "jcl"
# Extensions of the files a procedure is looked up in first (lower case)
_PROC_EXT_SET = JCL_EXT_SET - {_JCL_EXT}
# Statements that name a JCL member by its name
_MEMBER_KIND_SET = {PROC_KIND, INCLUDE_KIND}

# Number of leading bytes of a file its codec is chosen from
_HEAD_SIZE = 8192
# Codec of a file no other codec decodes: every byte is a character
_BYTE_CODEC = "latin-1"
# Number of characters read from the start of a line; the rest of a longer line is skipped
_LINE_HEAD_SIZE = 256
# Characters that end a line
_LINE_END_TUPLE = ("\n", "\r")
# Width of a tab
_TAB_SIZE = 8
# Start of a JCL comment statement and of a JES statement
_SKIP_LINE_PREFIX_TUPLE = ("//*", "/*")
# Operations of the JCL statements other than comment statements, JES2 job group
# statements among them
_OPERATION_PATTERN = (
    r"(JOB|EXEC|DD|PROC|PEND|SET|INCLUDE|JCLLIB|IF|ELSE|ENDIF|OUTPUT|CNTL|ENDCNTL|EXPORT"
    r"|XMIT|COMMAND|JOBGROUP|ENDGROUP|GJOB|SJOB|JOBSET|ENDSET|SCHEDULE|CONCURRENT"
    r"|BEFORE|AFTER|NOTIFY)"
)
# A JOB or JOBGROUP statement and a JCL statement in upper case with a name of 1 to 8
# characters (STEP.DD for an override) or, for a JCL statement, none, as a file without
# a JCL extension is read
_STRICT_JOB_LINE_RE = re.compile(r"//[A-Z@#$][A-Z0-9@#$]{0,7} +JOB(GROUP)?( |$)")
_STRICT_JCL_LINE_RE = re.compile(
    r"//([A-Z@#$][A-Z0-9@#$]{0,7}(\.[A-Z@#$][A-Z0-9@#$]{0,7})?)? +"
    rf"{_OPERATION_PATTERN}( |$)",
)
# A JOB or JOBGROUP statement and a JCL statement with any name field in any case
_JOB_LINE_RE = re.compile(r"//\S* +JOB(GROUP)?( |$)", re.IGNORECASE)
_JCL_LINE_RE = re.compile(rf"//\S* +{_OPERATION_PATTERN}( |$)", re.IGNORECASE)


def _first_statement_line(file_abs: str) -> str:
    """Return the first line of a file that is no blank, JCL comment or JES statement.

    The file is decoded with the codec its first bytes decode with (head_codec), else
    byte by byte. Only the first _LINE_HEAD_SIZE characters of a line are read, with
    their tabs expanded.

    Returns:
        The line without trailing blanks; "" without one, and when the file cannot be
        read, which is logged.
    """
    try:
        with open(file_abs, "rb") as file:
            codec = head_codec(file.read(_HEAD_SIZE)) or _BYTE_CODEC
        with open(file_abs, encoding=codec, errors="replace", newline="") as file:
            while line_head := file.readline(_LINE_HEAD_SIZE):
                line = line_head.expandtabs(_TAB_SIZE).rstrip()
                if line and not line.startswith(_SKIP_LINE_PREFIX_TUPLE):
                    return line
                rest = line_head
                while rest and not rest.endswith(_LINE_END_TUPLE):
                    rest = file.readline(_LINE_HEAD_SIZE)
    except OSError as e:
        logger.warning(f"{file_abs} is not read as JCL: {type(e).__name__}: {e}")
    return ""


def is_job_file(file_abs: str, is_strict: bool) -> bool:
    """Return whether the first statement of a file is a JOB or JOBGROUP statement.

    Args:
        file_abs: Absolute path of the file.
        is_strict: Whether the statement must be in upper case with a name of 1 to 8
            characters; otherwise any name field in any case.
    """
    line_re = _STRICT_JOB_LINE_RE if is_strict else _JOB_LINE_RE
    return bool(line_re.match(_first_statement_line(file_abs)))


def _is_jcl_file(file_abs: str, is_strict: bool) -> bool:
    """Return whether the first statement of a file is a JCL statement.

    Args:
        file_abs: Absolute path of the file.
        is_strict: Whether the statement must be in upper case with a name of 1 to 8
            characters or none; otherwise any name field in any case.
    """
    line_re = _STRICT_JCL_LINE_RE if is_strict else _JCL_LINE_RE
    return bool(line_re.match(_first_statement_line(file_abs)))


def _member_candidate_list(
    name: str,
    kind: str,
    current_file_rel: str,
    name_dict: dict[str, list[str]],
    job_file_set: set[str],
) -> list[str]:
    """Return the files that can hold an EXEC statement's procedure or an INCLUDE member, best first.

    The files whose name, with or without its extension, is the name are the
    candidates; upper and lower case are not told apart. A job is neither a procedure
    nor a member. The order: for a procedure a file with a procedure extension first,
    then a file in the directory of the current file, then path order.

    Args:
        name: Name of the procedure or member.
        kind: PROC_KIND or INCLUDE_KIND.
        current_file_rel: Relative path of the file the statement is written in.
        name_dict: Upper-case file name, with and without its extension -> files
            (add_file_name).
        job_file_set: Relative paths of the files whose first statement is a JOB or
            JOBGROUP statement.

    Returns:
        Relative paths of the other files with the name that are no jobs.
    """
    current_dir = os.path.dirname(current_file_rel)
    return sorted(
        (
            file_rel for file_rel in name_dict.get(name.upper(), [])
            if file_rel != current_file_rel and file_rel not in job_file_set
        ),
        key=lambda file_rel: (
            kind == PROC_KIND
            and os.path.splitext(file_rel)[1].lstrip(".").lower() not in _PROC_EXT_SET,
            os.path.dirname(file_rel) != current_dir,
            file_rel,
        ),
    )


def member_path(
    name: str,
    kind: str,
    current_file_rel: str,
    name_dict: dict[str, list[str]],
    job_file_set: set[str],
) -> str | None:
    """Return the file that holds the procedure of an EXEC statement or an INCLUDE member.

    It is the first of _member_candidate_list.

    Args:
        name: Name of the procedure or member.
        kind: PROC_KIND or INCLUDE_KIND.
        current_file_rel: Relative path of the file the statement is written in.
        name_dict: Upper-case file name, with and without its extension -> files
            (add_file_name).
        job_file_set: Relative paths of the files whose first statement is a JOB or
            JOBGROUP statement.

    Returns:
        Relative path of the file. None when no other file than the jobs has the name.
    """
    candidate_list = _member_candidate_list(name, kind, current_file_rel, name_dict, job_file_set)
    return candidate_list[0] if candidate_list else None


def parse_jcl_file(file_rel: str, project_dir: str) -> JclSource | None:
    """Parse a JCL file of the project and return its JclSource.

    Args:
        file_rel: Relative path of the file.
        project_dir: Absolute path to the project root.

    Returns:
        The JclSource. None when the file is no JCL file, and when reading or parsing it
        raises an exception, which is logged.
    """
    try:
        jcl_source = parse_file(os.path.join(project_dir, file_rel))[0]
    except Exception as e:
        logger.warning(f"{file_rel} is not read as JCL: {type(e).__name__}: {e}")
        return None
    return jcl_source if isinstance(jcl_source, JclSource) else None


def _member_name_list(file_rel: str, project_dir: str) -> list[tuple[str, str]]:
    """Return (kind, name) of each procedure and INCLUDE member a JCL file names, in line order."""
    jcl_source = parse_jcl_file(file_rel, project_dir)
    if jcl_source is None:
        return []
    return [
        (jcl_import.kind, jcl_import.name)
        for jcl_import in jcl_source.import_list if jcl_import.kind in _MEMBER_KIND_SET
    ]


def register_jcl_target(
    project_dir: str, other_file_set: set[str], language_file_set: set[str],
) -> dict[str, str]:
    """Find the files without a language that are JCL, and record them as JCL.

    A file whose first statement is a JOB or JOBGROUP statement in upper case with a
    name of 1 to 8 characters is a JCL file. So is a file the procedure of an EXEC
    statement or the member of an INCLUDE statement of a JCL file leads to: its
    candidates (_member_candidate_list, with the files without a language as candidates
    too) are taken in order; a candidate with a language, or recorded already, ends the
    search, the first candidate whose first statement is a JCL statement in upper case
    with a name of 1 to 8 characters or none (_is_jcl_file with is_strict) and is no JOB
    or JOBGROUP statement is recorded, and the other candidates are passed over. The
    statements of such a file are followed as well. The files are recorded with
    set_copy_target_ext() and add_copy_target_ext(), replacing the ones recorded before
    for the project.

    Processing flow:
    1. Record the files without a language whose first statement is a JOB or JOBGROUP
       statement
    2. Index the JCL files and the files without a language by their file names
    3. Follow the EXEC and INCLUDE statements of the JCL files, recording each file
       without a language they lead to

    Args:
        project_dir: Absolute path to the project root.
        other_file_set: Relative paths of the project files without a language.
        language_file_set: Relative paths of the project files with a language.

    Returns:
        {relative path: "jcl"} of the files recorded.
    """
    # == Step 1: Jobs =========================================================
    jcl_ext_dict = {
        file_rel: _JCL_EXT for file_rel in sorted(other_file_set)
        if is_job_file(os.path.join(project_dir, file_rel), is_strict=True)
    }
    set_copy_target_ext(project_dir, jcl_ext_dict)

    # == Step 2: Names ========================================================
    language_jcl_file_list = sorted(
        file_rel for file_rel in language_file_set
        if language_ext(os.path.join(project_dir, file_rel)) in JCL_EXT_SET
    )
    job_file_set = set(jcl_ext_dict) | {
        file_rel for file_rel in language_jcl_file_list
        if is_job_file(os.path.join(project_dir, file_rel), is_strict=False)
    }
    name_dict: dict[str, list[str]] = {}
    for file_rel in sorted({*language_jcl_file_list, *other_file_set}):
        add_file_name(name_dict, file_rel)

    # == Step 3: EXEC and INCLUDE statements ==================================
    # Relative path of a file without a language -> whether its first statement is JCL
    is_jcl_text_dict: dict[str, bool] = {}

    def is_jcl_text(file_rel: str) -> bool:
        """Return whether a file without a language starts with an upper-case JCL statement, not a job."""
        if file_rel not in is_jcl_text_dict:
            file_abs = os.path.join(project_dir, file_rel)
            is_jcl_text_dict[file_rel] = (
                _is_jcl_file(file_abs, is_strict=True)
                and not is_job_file(file_abs, is_strict=False)
            )
        return is_jcl_text_dict[file_rel]

    queue = [*language_jcl_file_list, *jcl_ext_dict]
    while queue:
        file_rel = queue.pop(0)
        # (kind, upper-case name) -> the candidates of the file's statements, best first
        candidate_list_dict: dict[tuple[str, str], list[str]] = {}
        for kind, name in _member_name_list(file_rel, project_dir):
            candidate_key = (kind, name.upper())
            if candidate_key not in candidate_list_dict:
                candidate_list_dict[candidate_key] = _member_candidate_list(
                    name, kind, file_rel, name_dict, job_file_set,
                )
            for candidate in candidate_list_dict[candidate_key]:
                if candidate not in other_file_set or candidate in jcl_ext_dict:
                    break
                if is_jcl_text(candidate):
                    jcl_ext_dict[candidate] = _JCL_EXT
                    add_copy_target_ext(project_dir, candidate, _JCL_EXT)
                    queue.append(candidate)
                    break
    return jcl_ext_dict


def no_jcl_file_list(project_dir: str, file_rel_list: list[str]) -> list[str]:
    """Return the files with a JCL extension whose first statement is no JCL statement.

    The statements are matched with any name field in any case.

    Args:
        project_dir: Absolute path to the project root.
        file_rel_list: Relative paths of the project files.

    Returns:
        Relative paths, in the order of file_rel_list.
    """
    return [
        file_rel for file_rel in file_rel_list
        if os.path.splitext(file_rel)[1].lstrip(".").lower() in JCL_EXT_SET
        and not _is_jcl_file(os.path.join(project_dir, file_rel), is_strict=False)
    ]
