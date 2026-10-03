# Design Document: codetwine/parsers/r_markdown.py

# Design Specification

**Overview**

Parse R Markdown and Quarto files to extract and isolate R code chunks while preserving file structure and line alignment.

- Call `has_r_chunk` to check whether a file contains any R code chunks, returning a boolean used to filter files in dependency graph analysis.
- Call `r_chunk_code` to extract R code chunks from a file with all non-chunk content blanked to spaces, producing output suitable for syntax tree parsing while maintaining byte-for-byte position correspondence with the original file.

This file is a parser used by the dependency graph extractor and tree parser. The dependency graph extractor uses `has_r_chunk` to identify which R Markdown files contain executable code. The tree parser uses `r_chunk_code` to extract only R chunks from R Markdown documents before parsing them, optionally filtering chunks based on a custom validation function.

The design preserves byte positions and line structure: every non-chunk byte becomes a space, fence lines are blanked, and blockquote prefixes within chunks are blanked, ensuring that character offsets in the returned content match offsets in the original file. Chunks without end lines extend to the end of file. Chunks in non-R languages are treated as non-chunk content and blanked.

**Definitions**

## `_CHUNK_START_RE`

Regular expression matching the opening fence line of an R code chunk, which must contain three or more backticks or tildes followed by `{r ...}`, optionally preceded by blockquote marks and spaces. Used internally to detect where chunks begin during parsing.

## `_CHUNK_END_RE`

Regular expression matching a closing fence line containing three or more backticks or tildes alone on the line, optionally preceded by blockquote marks and spaces. Used internally to detect where chunks end during parsing.

## `_QUOTE_PREFIX_RE`

Regular expression matching the blockquote marks and leading whitespace at the start of a line. Used internally to identify and blank the blockquote prefix within quoted code chunks, preserving the code content that follows.

## `has_r_chunk`

Checks whether a file contains at least one R code chunk by testing each line against the chunk start pattern. Returns true if any R chunk opening is found, enabling quick filtering of files before full parsing.

## `r_chunk_code`

Extracts R code chunks from a file by blanking all non-chunk content and fence lines to spaces while preserving line breaks and file length, maintaining byte-for-byte alignment with the original file. Accepts an optional filter function to selectively blank chunks that fail validation, useful for syntax-aware chunk filtering in the parsing pipeline.

# Summary

# Summary: codetwine/parsers/r_markdown.py

This parser extracts R code chunks from R Markdown and Quarto files while preserving file structure and byte-position alignment. It has two main public functions: `has_r_chunk` performs quick boolean filtering to identify files containing executable R code, and `r_chunk_code` extracts chunks by blanking non-chunk content to spaces, maintaining line breaks and character offsets for downstream syntax parsing. The module handles blockquote-prefixed chunks, validates chunks against optional custom filters, and treats non-R language blocks as non-executable content. Internal regex patterns detect chunk fence lines and blockquote markers.
