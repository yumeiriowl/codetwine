import os
import re
import json
import codecs
import hashlib
import contextlib
from collections.abc import Hashable, Iterator
from typing import TypeVar
from charset_normalizer import from_bytes
from codetwine.config.settings import SOURCE_ENCODING

# Size of one read (bytes) when is_text_file looks for a character that is not whitespace
_TEXT_PROBE_SIZE = 8192

# Size of one read (bytes) when compute_file_hash hashes a file
_HASH_CHUNK_SIZE = 8192

# A "\r" that is not the start of "\r\n"
_LONE_CR_RE = re.compile(r"\r(?!\n)")

# (BOM, codec that decodes the file and drops the BOM, whether a file that codec does
# not decode goes through the other steps of read_source() without the BOM), checked in
# this order. The first BOM a file starts with decides its codec. A file whose codec
# does not decode it and does not go through the other steps is decoded by that codec
# with each invalid byte sequence replaced by U+FFFD.
_BOM_CODEC_TUPLE = (
    (codecs.BOM_UTF32_LE, "utf-32", False),
    (codecs.BOM_UTF32_BE, "utf-32", False),
    (codecs.BOM_UTF8, "utf-8-sig", True),
    (codecs.BOM_UTF16_LE, "utf-16", False),
    (codecs.BOM_UTF16_BE, "utf-16", False),
)

# Names of the files written in the output directory of a file (resolve_file_output_dir)
DEPENDENCY_FILE_NAME = "file_dependencies.json"
DOC_JSON_NAME = "doc.json"
DOC_MD_NAME = "doc.md"

DictKey = TypeVar("DictKey", bound=Hashable)
ListValue = TypeVar("ListValue")


def _bom_entry(head: bytes) -> tuple[bytes, str, bool] | None:
    """Return the _BOM_CODEC_TUPLE entry of the BOM head starts with, or None when it has none."""
    for bom_entry in _BOM_CODEC_TUPLE:
        if head.startswith(bom_entry[0]):
            return bom_entry
    return None


def _plain_codec_tuple() -> tuple[str, ...]:
    """Return the codecs tried, in order, on a file without a BOM: UTF-8, then SOURCE_ENCODING."""
    return ("utf-8", *SOURCE_ENCODING)


def is_text_file(file_path: str) -> bool:
    """Return whether a file is non-empty text.

    The file is read _TEXT_PROBE_SIZE bytes at a time until a read holds something
    other than whitespace. A file that starts with a UTF-8, UTF-16 or UTF-32 BOM is text
    when its text, decoded with that codec, is not all whitespace. Any other file is
    text when it is not all whitespace and none of those reads holds a NUL byte. A file
    that cannot be read, and a path that is not a regular file (a named pipe, a socket, a
    device, a directory), count as not text; such a path is not opened.

    Args:
        file_path: Absolute path of the file to probe.

    Returns:
        True for a non-empty text file, False for an empty, binary or unreadable one and
        for a path that is not a regular file.
    """
    if not os.path.isfile(file_path):
        return False
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(_TEXT_PROBE_SIZE)
            bom_entry = _bom_entry(chunk)
            decoder = (
                codecs.getincrementaldecoder(bom_entry[1])(errors="ignore")
                if bom_entry is not None else None
            )
            while chunk:
                if decoder is not None:
                    if decoder.decode(chunk).strip():
                        return True
                elif b"\0" in chunk:
                    return False
                elif chunk.strip():
                    return True
                chunk = f.read(_TEXT_PROBE_SIZE)
    except OSError:
        return False
    return False


def check_source_encoding() -> None:
    """Check that every name in SOURCE_ENCODING is a text encoding Python knows.

    What is checked is the value this module holds when it is called.

    Raises:
        ValueError: When a name is not a known codec, or names one that does not decode
            bytes to text (hex, base64, rot13, ...).
    """
    for encoding in SOURCE_ENCODING:
        try:
            "".encode(encoding)
        except LookupError:
            raise ValueError(
                f"SOURCE_ENCODING names '{encoding}', which is not a text encoding Python "
                f"knows. Use Python codec names such as cp932, euc_jp or cp1252, "
                f"in the .env file or your shell."
            ) from None


def _decode_source(file_content: bytes) -> tuple[str, str, bool]:
    """Decode the bytes of a text file as read_source() describes.

    Args:
        file_content: The whole file's bytes.

    Returns:
        A (text, encoding, need_detect) tuple. encoding is the codec name that decoded the
        bytes, or "" when they were decoded as UTF-8 with replacement; need_detect is
        True when neither a BOM, UTF-8 nor SOURCE_ENCODING decoded them without error
        (steps 4 and 5, and a UTF-16 or UTF-32 file decoded with replacement).
    """
    # BOM: its codec, else the codec with replacement or the next steps without the BOM
    bom_entry = _bom_entry(file_content)
    if bom_entry is not None:
        bom, bom_codec, try_other_codec = bom_entry
        try:
            return file_content.decode(bom_codec), bom_codec, False
        except UnicodeDecodeError:
            if not try_other_codec:
                return file_content.decode(bom_codec, errors="replace"), bom_codec, True
            file_content = file_content[len(bom):]

    # UTF-8 and the configured encodings: the first that decodes without error
    for codec in _plain_codec_tuple():
        try:
            return file_content.decode(codec), codec, False
        except UnicodeDecodeError:
            continue

    # Detected encoding, then UTF-8 with replacement
    best_match = from_bytes(file_content).best()
    if best_match is not None:
        return str(best_match), best_match.encoding, True
    return file_content.decode("utf-8", errors="replace"), "", True


def head_codec(head: bytes) -> str | None:
    """Return the codec the first bytes of a file decode with, in the order read_source tries.

    The codec of the BOM the bytes start with, else the first of UTF-8 and
    SOURCE_ENCODING that decodes them; a character cut off at their end is allowed.

    Args:
        head: The first bytes of the file.

    Returns:
        The codec. None when none of them decodes the bytes.
    """
    bom_entry = _bom_entry(head)
    if bom_entry is not None:
        return bom_entry[1]
    for codec in _plain_codec_tuple():
        try:
            codecs.getincrementaldecoder(codec)().decode(head, final=False)
            return codec
        except UnicodeDecodeError:
            continue
    return None


def read_source(file_path: str) -> tuple[str, str]:
    """Read a text file and return its contents decoded, with the encoding used.

    The encoding is chosen in this order:
    1. The codec named by a BOM at the start of the file (the BOM is dropped)
    2. UTF-8
    3. The encodings of SOURCE_ENCODING, in order
    4. The encoding charset-normalizer detects
    5. UTF-8 with each invalid byte replaced by U+FFFD
    Steps 1 to 3 take the first codec that decodes the whole file without error. A file
    that starts with a UTF-8 BOM and is not UTF-8 goes through steps 2 to 5 without the
    BOM. A file that starts with a UTF-16 or UTF-32 BOM and that codec does not decode is
    decoded by that codec with each invalid byte sequence replaced by U+FFFD.
    Line breaks are kept as they are; line numbers match the file.

    Args:
        file_path: Absolute path of the file to read.

    Returns:
        A (text, encoding) tuple. encoding is the codec name that decoded the file,
        or "" when step 5 decoded it.

    Raises:
        OSError: When the file cannot be read.
    """
    text, encoding, _ = read_source_info(file_path)
    return text, encoding


def detected_encoding(file_path: str) -> str | None:
    """Return the encoding read_source() decodes a file with when it has to detect it.

    Args:
        file_path: Absolute path of the file to read.

    Returns:
        The encoding charset-normalizer detects (read_source() step 4), "" when the file
        is decoded as UTF-8 with replacement (step 5), the codec of the BOM when a file
        that starts with a UTF-16 or UTF-32 BOM is decoded by that codec with
        replacement, or None when a BOM, UTF-8 or SOURCE_ENCODING decodes it.

    Raises:
        OSError: When the file cannot be read.
    """
    return read_source_info(file_path)[2]


def read_source_info(file_path: str) -> tuple[str, str, str | None]:
    """Read a text file as read_source() does and return what both read_source() and
    detected_encoding() return for it, from one read.

    Args:
        file_path: Absolute path of the file to read.

    Returns:
        A (text, encoding, detected encoding) tuple: the first two as read_source()
        returns them, the third as detected_encoding() returns it.

    Raises:
        OSError: When the file cannot be read.
    """
    with open(file_path, "rb") as f:
        text, encoding, need_detect = _decode_source(f.read())
    return text, encoding, encoding if need_detect else None


def lone_cr_to_lf(text: str) -> str:
    """Return a text with every lone "\\r" turned into "\\n".

    The text keeps its length and the position of every character, and "\\r\\n" stays.
    Each "\\n" of the result ends one line as line_list_of() splits the text.

    Args:
        text: The text to change.

    Returns:
        The changed text.
    """
    return _LONE_CR_RE.sub("\n", text)


def line_list_of(text: str) -> list[str]:
    """Split a text into its lines at "\\n", "\\r\\n" and a lone "\\r" alone.

    Every line number of the analysis counts lines this way. A form feed, a vertical tab,
    U+0085, U+2028 and the other characters str.splitlines() also breaks at stay inside
    their line.

    Examples:
        "a\\r\\nb\\rc\\n" -> ["a", "b", "c"]
        "a\\n\\f\\nb"      -> ["a", "\\f", "b"]
        ""              -> []

    Args:
        text: The text to split.

    Returns:
        The lines without their line breaks. A text that ends in a line break has no
        empty line after it.
    """
    line_list = lone_cr_to_lf(text).replace("\r\n", "\n").split("\n")
    if line_list[-1] == "":
        line_list.pop()
    return line_list


def read_source_text(file_path: str) -> str:
    """Read a text file decoded by read_source(), with every line break turned into "\\n".

    "\\r\\n" and a lone "\\r" become "\\n", as in a file opened in text mode, so its lines are
    those of line_list_of().

    Args:
        file_path: Absolute path of the file to read.

    Returns:
        The file's text.

    Raises:
        OSError: When the file cannot be read.
    """
    text = read_source(file_path)[0]
    return lone_cr_to_lf(text).replace("\r\n", "\n")


def add_file_name(
    name_dict: dict[str, list[str]], file_rel: str, name_list: list[str] | None = None,
) -> None:
    """Index a file by its upper-case file name, with and without its extension, and other names.

    Examples:
        "copy/CustRec.cpy" -> keys "CUSTREC.CPY" and "CUSTREC"

    Args:
        name_dict: Upper-case name -> relative paths of the files; the file is appended
            to the list of each of its names. A file is added to a name_dict once.
        file_rel: Relative path of the file.
        name_list: Upper-case names the file is indexed by as well.
    """
    file_name = os.path.basename(file_rel).upper()
    for name in dict.fromkeys((file_name, os.path.splitext(file_name)[0], *(name_list or ()))):
        name_dict.setdefault(name, []).append(file_rel)


def add_unique(value_dict: dict[DictKey, list[ListValue]], key: DictKey, value: ListValue) -> None:
    """Append a value to the list a dict keeps under a key unless the list holds it already.

    Args:
        value_dict: Key -> values; a key without a list gets an empty one first.
        key: The key of the list.
        value: The value to append.
    """
    value_list = value_dict.setdefault(key, [])
    if value not in value_list:
        value_list.append(value)


def lower_ext(file_path: str) -> str:
    """Return the extension of a file in lower case, without ".".

    Examples:
        "jcl/A.JCL"  -> "jcl"
        "Makefile"   -> ""

    Args:
        file_path: Path of the file, relative or absolute.

    Returns:
        The extension, "" for a file without one.
    """
    return os.path.splitext(file_path)[1].lstrip(".").lower()


def directory_distance(file_rel: str, current_file_rel: str) -> tuple[int, int]:
    """Return how far the directory of a file is from that of the current file, as a sort key.

    A key sorts first when the directory shares more leading directories with that of
    the current file, then when it has fewer directories after those; a file in the
    directory of the current file has the first key of all.

    Examples:
        ("app/csd/B.csd", "app/cbl/P.cbl")  -> (-1, 1)
        ("app/cbl/S.cbl", "app/cbl/P.cbl")  -> (-2, 0)
        ("csd/a/M.bms", "csd/APP.csd")      -> (-1, 1)

    Args:
        file_rel: Relative path of the file, with "/" between directories.
        current_file_rel: Relative path of the current file, with "/" between directories.

    Returns:
        (minus the number of leading directories the two share, number of the other
        directories of file_rel).
    """
    part_tuple = tuple(part for part in os.path.dirname(file_rel).split("/") if part)
    current_part_tuple = tuple(
        part for part in os.path.dirname(current_file_rel).split("/") if part
    )
    depth = 0
    while (
        depth < min(len(part_tuple), len(current_part_tuple))
        and part_tuple[depth] == current_part_tuple[depth]
    ):
        depth += 1
    return -depth, len(part_tuple) - depth


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


def path_to_rel(path: str, project_dir: str) -> str:
    """Return the path of a file relative to the project root, with "/" separators.

    Examples:
        ("/work/proj/src/a.py", "/work/proj") -> "src/a.py"

    Args:
        path: Path of the file, absolute or relative to the current directory.
        project_dir: Root directory of the project, in the same form as path.

    Returns:
        The relative path from the project root.
    """
    return os.path.relpath(path, project_dir).replace("\\", "/")


def rel_to_copy_path(rel_path: str) -> str:
    """Convert a project-relative path to a copy-destination directory structure path.

    The destination follows the format {parent_dir}/{stem}_{ext}/{filename}, the path at
    which the pipeline copies each source file into the output directory. Files with the
    same stem and different extensions (e.g. utils.c and utils.h) get different directories.

    Examples:
        "config.py"                    -> "config_py/config.py"
        "src/prompts.py"               -> "src/prompts_py/prompts.py"
        "src/llm/client.py"            -> "src/llm/client_py/client.py"
        "Makefile"                     -> "Makefile/Makefile"

    Args:
        rel_path: Relative path from the project root.

    Returns:
        A path matching the copy-destination directory structure.
    """
    parent_dir = os.path.dirname(rel_path)
    filename = os.path.basename(rel_path)
    dir_name = _to_dir_name(filename)
    if parent_dir:
        return f"{parent_dir}/{dir_name}/{filename}"
    return f"{dir_name}/{filename}"


def copy_path_to_rel(copy_path: str) -> str:
    """Restore a copy-destination directory structure path to a project-relative path.

    The inverse of rel_to_copy_path. In copy-destination paths, a {stem}_{ext}
    directory is inserted; this removes it to recover the original relative path.

    Examples:
        "config_py/config.py"                    -> "config.py"
        "src/prompts_py/prompts.py"               -> "src/prompts.py"
        "src/llm/client_py/client.py"             -> "src/llm/client.py"
        "Makefile/Makefile"                       -> "Makefile"

    Args:
        copy_path: A copy-destination directory structure path.

    Returns:
        The relative path from the project root.
    """
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


def load_doc(output_dir: str) -> dict | None:
    """Read the design document (doc.json) saved in a file's output directory.

    Args:
        output_dir: Output directory of the file.

    Returns:
        The design document dict, or None if doc.json does not exist, cannot be read, or
        holds no JSON object.
    """
    json_path = os.path.join(output_dir, DOC_JSON_NAME)
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def load_file_dependencies(output_dir: str) -> dict | None:
    """Read the dependency information (file_dependencies.json) saved in a file's output directory.

    Args:
        output_dir: Output directory of the file.

    Returns:
        The parsed JSON, or None if file_dependencies.json does not exist.
    """
    dep_path = os.path.join(output_dir, DEPENDENCY_FILE_NAME)
    if not os.path.exists(dep_path):
        return None
    with open(dep_path, "r", encoding="utf-8") as f:
        return json.load(f)


@contextlib.contextmanager
def replace_on_success(output_path: str) -> Iterator[str]:
    """Yield a temporary path to write a file at, and move that file to output_path once
    the block ends without an exception.

    The temporary path is output_path + ".tmp"; a file left there by a run that stopped
    is removed first. When the block raises, the file at the temporary path is removed
    and the file at output_path is left as it was.

    Args:
        output_path: Path the finished file is moved to.

    Yields:
        The temporary path.
    """
    tmp_path = output_path + ".tmp"
    with contextlib.suppress(FileNotFoundError):
        os.remove(tmp_path)
    try:
        yield tmp_path
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp_path)
        raise
    os.replace(tmp_path, output_path)
