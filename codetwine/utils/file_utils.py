import os
import hashlib


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
    parts = copy_path.replace("\\", "/").split("/")
    if len(parts) >= 2:
        filename = parts[-1]
        # If the second-to-last directory name matches _to_dir_name(filename), it was inserted
        if parts[-2] == _to_dir_name(filename):
            return "/".join(parts[:-2] + [filename])
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
    parts = output_path.split("/", 1)
    if len(parts) == 2:
        return copy_path_to_rel(parts[1])
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
    h = hashlib.sha256()
    # Read and hash in 8KB chunks
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def is_file_unchanged(source_path: str, copied_path: str) -> bool:
    """Compare the SHA256 hashes of the original file and its copy in the output
    directory, returning True if the file has not changed.

    Returns False (treated as changed) if the copy does not exist at the destination.

    Args:
        source_path: Absolute path of the original file in the project.
        copied_path: Absolute path of the copied file in the output directory.

    Returns:
        True if the hashes match.
    """
    # Treat as changed if the copy does not exist at the destination
    if not os.path.exists(copied_path):
        return False
    # Compare the SHA256 hashes of both files
    return compute_file_hash(source_path) == compute_file_hash(copied_path)
