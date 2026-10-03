import re

# First line of an R code chunk: ```{r}, ```{r name, fig.cap="a {b}"}, ~~~{r}, > ```{r}
_CHUNK_START_RE = re.compile(
    rb"^(?P<prefix>[ \t>]*)(?P<fence>`{3,}|~{3,})[ \t]*\{[ \t]*[rR](?:[ \t,].*)?\}[ \t]*\r?$"
)

# A line of fence characters alone: the last line of a chunk
_CHUNK_END_RE = re.compile(rb"^[ \t>]*(?P<fence>`{3,}|~{3,})[ \t]*\r?$")

# The blockquote marks and blanks before the code of a line
_QUOTE_PREFIX_RE = re.compile(rb"^[ \t>]*")


def has_r_chunk(content: bytes) -> bool:
    """Return whether the text of an R Markdown or Quarto file has an R code chunk.

    Args:
        content: The file's text as UTF-8 bytes.

    Returns:
        True when a line of the text starts an R chunk (```{r ...}).
    """
    return any(_CHUNK_START_RE.match(line) for line in content.split(b"\n"))


def r_chunk_code(content: bytes) -> bytes:
    """Return the text of an R Markdown or Quarto file with only its R code chunks kept.

    A chunk starts at a fence line of three or more backticks or tildes followed by
    {r ...} and ends at the next line of the same fence character, at least as many of
    them, alone. Every byte outside the chunks, and the fence lines themselves, becomes
    a space; line breaks stay. In a chunk whose fence line starts with blockquote marks
    (> ```{r}), the marks before the code of each line become spaces as well. The
    result has the length and the lines of content, so a position in it is the same
    position in the file.

    Examples:
        b"# Title\\n```{r}\\nx <- 1\\n```\\n"   -> b"       \\n      \\nx <- 1\\n   \\n"
        b"> ```{r}\\n> x <- 1\\n> ```\\n"         -> b"        \\n  x <- 1\\n     \\n"

    Args:
        content: The file's text as UTF-8 bytes.

    Returns:
        The bytes with everything but the code of the R chunks blanked. A chunk without
        an end line runs to the end of the file. Chunks of other languages ({python})
        are blanked.
    """
    code_line_list: list[bytes] = []
    fence: bytes | None = None
    is_quote = False
    for line in content.split(b"\n"):
        if fence is None:
            start_match = _CHUNK_START_RE.match(line)
            if start_match:
                fence = start_match.group("fence")
                is_quote = b">" in start_match.group("prefix")
            code_line_list.append(b" " * len(line))
            continue

        end_match = _CHUNK_END_RE.match(line)
        end_fence = end_match.group("fence") if end_match else b""
        if end_fence[:1] == fence[:1] and len(end_fence) >= len(fence):
            fence = None
            code_line_list.append(b" " * len(line))
            continue
        if is_quote:
            prefix_length = _QUOTE_PREFIX_RE.match(line).end()
            line = b" " * prefix_length + line[prefix_length:]
        code_line_list.append(line)
    return b"\n".join(code_line_list)
