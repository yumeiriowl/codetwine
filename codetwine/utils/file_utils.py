import os
import codecs
import hashlib
from charset_normalizer import from_bytes
from codetwine.config.settings import SOURCE_ENCODING

# How many leading bytes is_text_file reads to decide whether a file is text
_TEXT_PROBE_SIZE = 8192

# Size of one read (bytes) when compute_file_hash hashes a file
_HASH_CHUNK_SIZE = 8192

# (BOM, codec that decodes the file and drops the BOM), checked in this order.
# The first BOM a file starts with decides its codec.
_BOM_CODEC_TUPLE = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def _bom_codec(head: bytes) -> str | None:
    """Return the codec named by the BOM at the start of head, or None when it has none."""
    for bom, codec in _BOM_CODEC_TUPLE:
        if head.startswith(bom):
            return codec
    return None


def is_text_file(file_path: str) -> bool:
    """Return whether a file is non-empty text, judged from its first _TEXT_PROBE_SIZE bytes.

    A file that starts with a UTF-8, UTF-16 or UTF-32 BOM is text when the probe,
    decoded with that codec, is not all whitespace. Any other file is text when the
    probe holds no NUL byte and is not all whitespace. A file that cannot be read
    counts as not text.

    Args:
        file_path: Absolute path of the file to probe.

    Returns:
        True for a non-empty text file, False for an empty, binary or unreadable one.
    """
    try:
        with open(file_path, "rb") as f:
            head = f.read(_TEXT_PROBE_SIZE)
    except OSError:
        return False
    bom_codec = _bom_codec(head)
    if bom_codec is not None:
        return bool(head.decode(bom_codec, errors="ignore").strip())
    return b"\0" not in head and bool(head.strip())


def check_source_encoding() -> None:
    """Check that every name in SOURCE_ENCODING is a codec Python knows.

    What is checked is the value this module holds when it is called.

    Raises:
        ValueError: When a name is not a known codec.
    """
    for encoding in SOURCE_ENCODING:
        try:
            codecs.lookup(encoding)
        except LookupError:
            raise ValueError(
                f"SOURCE_ENCODING names an unknown encoding '{encoding}'. "
                f"Use Python codec names such as cp932, euc_jp or cp1252, "
                f"in the .env file or your shell."
            ) from None


def read_source(file_path: str) -> tuple[str, str]:
    """Read a text file and return its contents decoded, with the encoding used.

    The encoding is chosen in this order:
    1. The codec named by a BOM at the start of the file (the BOM is dropped)
    2. UTF-8
    3. The encodings of SOURCE_ENCODING, in order
    4. The encoding charset-normalizer detects
    5. UTF-8 with each invalid byte replaced by U+FFFD
    Steps 1 to 3 take the first codec that decodes the whole file without error.
    Line breaks are kept as they are; line numbers match the file.

    Args:
        file_path: Absolute path of the file to read.

    Returns:
        A (text, encoding) tuple. encoding is the codec name that decoded the file,
        or "" when step 5 decoded it.

    Raises:
        OSError: When the file cannot be read.
    """
    with open(file_path, "rb") as f:
        file_content = f.read()

    # BOM, UTF-8 and the configured encodings: the first that decodes without error
    bom_codec = _bom_codec(file_content)
    codec_list = ([bom_codec] if bom_codec else []) + ["utf-8", *SOURCE_ENCODING]
    for codec in codec_list:
        try:
            return file_content.decode(codec), codec
        except UnicodeDecodeError:
            continue

    # Detected encoding, then UTF-8 with replacement
    best_match = from_bytes(file_content).best()
    if best_match is not None:
        return str(best_match), best_match.encoding
    return file_content.decode("utf-8", errors="replace"), ""


def read_source_text(file_path: str) -> str:
    """Read a text file decoded by read_source(), with every line break turned into "\\n".

    "\\r\\n" and a lone "\\r" become "\\n", as in a file opened in text mode.

    Args:
        file_path: Absolute path of the file to read.

    Returns:
        The file's text.

    Raises:
        OSError: When the file cannot be read.
    """
    text = read_source(file_path)[0]
    return text.replace("\r\n", "\n").replace("\r", "\n")


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

    The destination follows the format {parent_dir}/{stem}_{ext}/{filename}, the path at
    which the pipeline copies each source file into the output directory. Files with the
    same stem and different extensions (e.g. utils.c and utils.h) get different directories.

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

    The output destination follows the structure {base_output_dir}/{parent_dir}/{stem}_{ext}/,
    the directory of rel_to_copy_path's result.

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
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK_SIZE), b""):
            sha256.update(chunk)
    return sha256.hexdigest()

