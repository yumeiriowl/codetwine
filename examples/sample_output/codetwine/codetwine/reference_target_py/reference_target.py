import os
from collections.abc import Callable
from codetwine.cobol_file_index import CobolReferenceTarget, cobol_reference_target_list
from codetwine.csharp_namespace_index import CsharpReferenceTarget, csharp_reference_target_list
from codetwine.import_reference import ImportReferenceTarget, import_reference_target_list
from codetwine.r_name_index import RReferenceTarget, r_reference_target_list
from codetwine.config.settings import EXT_TO_REFERENCE_KIND_DICT, language_ext

# A reference of a file resolved to its definition
ReferenceTarget = (
    CobolReferenceTarget | CsharpReferenceTarget | RReferenceTarget | ImportReferenceTarget
)

# Reference kind of a language (EXT_TO_REFERENCE_KIND_DICT) -> function that resolves the
# references of a file: (file_rel, project_file_set, project_dir) -> targets
_TARGET_LIST_FUNCTION_DICT: dict[str, Callable[[str, set[str], str], list]] = {
    "cobol":  cobol_reference_target_list,
    "csharp": csharp_reference_target_list,
    "r":      r_reference_target_list,
    "import": import_reference_target_list,
}


def reference_kind(file_abs: str) -> str | None:
    """Return how the references of a file are resolved.

    Args:
        file_abs: Absolute path of the file.

    Returns:
        "cobol", "csharp", "r" or "import" (EXT_TO_REFERENCE_KIND_DICT), None for a
        file without a language.
    """
    return EXT_TO_REFERENCE_KIND_DICT.get(language_ext(file_abs))


def reference_target_list(
    file_rel: str, project_file_set: set[str], project_dir: str,
) -> list[ReferenceTarget]:
    """Resolve each reference of a file to the definition it names.

    The references are resolved the way the language of the file names things: a COBOL
    file or a BMS source with OF / IN qualification (cobol_reference_target_list), a C#
    file through its namespaces and using directives (csharp_reference_target_list), an
    R file through the names it sees (r_reference_target_list), a file of any other
    language through its import statements (import_reference_target_list).

    Args:
        file_rel: Relative path of the file.
        project_file_set: Relative paths of the project files that have a language.
        project_dir: Absolute path to the project root.

    Returns:
        The resolved references; each has name, line and file_rel. A target whose
        file_rel is the file itself is a reference to a definition of the file. Empty
        for a file without a language.
    """
    file_kind = reference_kind(os.path.join(project_dir, file_rel))
    if file_kind is None:
        return []
    return _TARGET_LIST_FUNCTION_DICT[file_kind](file_rel, project_file_set, project_dir)
