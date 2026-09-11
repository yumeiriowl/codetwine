import os
import hashlib

# How many leading bytes is_text_file reads to decide whether a file is text
TEXT_PROBE_BYTES = 8192


def is_text_file(file_path: str) -> bool:
    """Return whether a file is non-empty text, judged from its first TEXT_PROBE_BYTES bytes.

    A file is text when the probe holds no NUL byte and is not all whitespace. A file
    that cannot be read counts as not text.

    Args:
        file_path: Absolute path of the file to probe.

    Returns:
        True for a non-empty text file, False for an empty, binary or unreadable one.
    """
    try:
        with open(file_path, "rb") as f:
            head = f.read(TEXT_PROBE_BYTES)
    except OSError:
        return False
    return b"\0" not in head and bool(head.strip())


def _to_dir_name(filename: str) -> str:
    """Generate an output directory name from a filename.

    Returns the name with the "." in the extension replaced by "_".
    Files without extensions (e.g. Makefile) are returned as-is.

    Examples:
        "settings.py"  -> "settings_py"
        "helper.h"     -> "helper_h"
        "Makefile"     -> "Makefile"

    Args:
        filename: The source filename (e.g. "settings.py").

    Returns:
        str: The directory name with the extension's "." replaced by "_".
    """
    stem, ext = os.path.splitext(filename)
    if ext:
        return f"{stem}_{ext[1:]}"
    return stem


def rel_to_copy_path(rel_path: str) -> str:
    """Convert a project-relative path to a copy-destination directory structure path.

    Matches the path structure used by process_single_file when copying source code.
    The destination follows the format {parent_dir}/{stem}_{ext}/{filename}.
    By appending the extension as a suffix, output destinations for files with the
    same name but different extensions (e.g. utils.c and utils.h) do not collide.

    Examples:
        "config.py"                    -> "config_py/config.py"
        "repo_graphrag/prompts.py"     -> "repo_graphrag/prompts_py/prompts.py"
        "repo_graphrag/llm/client.py"  -> "repo_graphrag/llm/client_py/client.py"
        "Makefile"                     -> "Makefile/Makefile"

    Args:
        rel_path: Relative path from the project root.

    Returns:
        A path matching the copy-destination directory structure.
    """
    # Separate the filename and parent directory
    parent_dir = os.path.dirname(rel_path)
    filename = os.path.basename(rel_path)
    dir_name = _to_dir_name(filename)
    # Include the parent directory in the path if present
    if parent_dir:
        return f"{parent_dir}/{dir_name}/{filename}"
    # For top-level files
    return f"{dir_name}/{filename}"


def copy_path_to_rel(copy_path: str) -> str:
    """Restore a copy-destination directory structure path to a project-relative path.

    The inverse of rel_to_copy_path. In copy-destination paths, a {stem}_{ext}
    directory is inserted; this removes it to recover the original relative path.

    Examples:
        "config_py/config.py"                    -> "config.py"
        "repo_graphrag/prompts_py/prompts.py"     -> "repo_graphrag/prompts.py"
        "repo_graphrag/llm/client_py/client.py"   -> "repo_graphrag/llm/client.py"
        "Makefile/Makefile"                       -> "Makefile"

    Args:
        copy_path: A copy-destination directory structure path.

    Returns:
        The relative path from the project root.
    """
    # Split the path by separator
    part_list = copy_path.replace("\\", "/").split("/")
    if len(part_list) >= 2:
        filename = part_list[-1]
        # If the second-to-last directory name matches _to_dir_name(filename), it was inserted
        if part_list[-2] == _to_dir_name(filename):
            return "/".join(part_list[:-2] + [filename])
    return copy_path


def output_path_to_rel(output_path: str) -> str:
    """Restore a "project_name/copy_destination_path" format path to a source-relative path.

    The inverse of to_output_path() in output.py.
    Removes the project name prefix and converts the copy-destination path
    back to the original relative path.

    Examples:
        "js_project/src/emitter_js/emitter.js"  -> "src/emitter.js"
        "my_project/config_py/config.py"         -> "config.py"

    Args:
        output_path: A path in "project_name/copy_destination_path" format.

    Returns:
        The relative path from the project root.
    """
    part_list = output_path.split("/", 1)
    if len(part_list) == 2:
        return copy_path_to_rel(part_list[1])
    return output_path


def resolve_file_output_dir(base_output_dir: str, file_rel: str) -> str:
    """Resolve the absolute output directory path from a file's relative path.

    The output destination follows the structure {base_output_dir}/{parent_dir}/{stem}_{ext}/.
    Shares the same path structure as rel_to_copy_path; by appending the extension
    as a suffix, output destinations for files with the same name but different
    extensions (e.g. utils.c and utils.h) do not collide.

    Args:
        base_output_dir: Base output directory.
        file_rel: File's relative path (e.g. "src/foo.py").

    Returns:
        The absolute path of the output directory.
    """
    # Convert the path structure with rel_to_copy_path and use its parent directory as the output destination
    copy_path = rel_to_copy_path(file_rel)
    return os.path.join(base_output_dir, os.path.dirname(copy_path))


def compute_file_hash(file_path: str) -> str:
    """Return the SHA256 hash of a file as a hex string.

    Args:
        file_path: Absolute path of the file to hash.

    Returns:
        SHA256 hash as a hex string.
    """
    # Initialize a SHA256 hash object
    sha256 = hashlib.sha256()
    # Read and hash in 8KB chunks
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()

