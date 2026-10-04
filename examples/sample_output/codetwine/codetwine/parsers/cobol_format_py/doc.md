# Design Document: codetwine/parsers/cobol_format.py

# Design Specification

**Overview**

Parse COBOL source files into statements (units) that a grammar can read, handling fixed-format and free-format syntax, comments, continuations, and non-ASCII characters.

Callers use this file to:
- Call `split_cobol_source()` to convert COBOL source text into a `CobolText` object containing units (data items, file descriptions, SELECT entries, procedure division sentences), program metadata, COPY statements, CALL statements, and word references for semantic analysis.
- Call `item_unit()` to construct a minimal data item unit for testing whether the grammar recognizes a given word as an item name.
- Call `literal_value()` to extract the content of a literal token (removing quotes and prefix) or return the text unchanged if it is not a literal.
- Call `qualifier_tuple()` to find the qualifying names that follow OF/IN keywords after a given word in a statement.
- Call `code_text_list()` to extract the code portion of each source line (columns 8–72 of fixed-format lines, full free-format lines) with comments and continuations handled.

The file depends on `line_list_of()` from `codetwine/utils/file_utils.py` to normalize line breaks. It is used by `codetwine/cobol_file_index.py` to identify whether a file contains COBOL code (checking for ITEM_UNIT, FILE_UNIT, or PROCEDURE_UNIT), and by `codetwine/extractors/cobol_source.py` to parse units with tree-sitter and extract definitions and references.

The file handles column-width differences for East Asian characters by treating ambiguous-width characters as full-width (two columns) when the file contains any wide character, normalizing columns consistently across fixed-format line layout rules. Tab expansion occurs at parse entry points; the file works only with expanded lines thereafter. A literal or word that spans lines is merged into a single token. Comments are dropped during tokenization; EXEC blocks and COPY statements are recorded separately and not included in units passed to the grammar.

**Definitions**

## `split_cobol_source`

Main entry point: tokenizes source lines, splits tokens into statements, replaces non-ASCII names with stand-in names, and returns a `CobolText` object containing units, programs, COPY statements, CALL statements, ENTRY statements, and word references. Processes fixed-format and free-format source, applying format directives to switch modes, and decides whether the file has a right margin (code area ending at column 72) by checking whether most lines with text past column 72 contain code or identification fields.

## `code_text_list`

Extract the code portion from each source line, expanding tabs and dropping comments and compiler option lines. Returns a list of strings (one per input line) where each string is the code area of a fixed-format line (columns 8–72, or to line end if no right margin is detected) or the entire free-format line; lines without code return empty strings. Used by callers to analyze line-by-line code content without parsing structure.

## `literal_value`

Return the content of a literal token (text between opening and closing quote, after any prefix letter) or the input text unchanged if it is not a literal. Used to extract program names, copybook names, and literal operands from tokens.

## `qualifier_tuple`

Return a tuple of names that qualify a word via OF or IN keywords (for example, "CODE-X OF REC-A IN FILE-B" returns `("REC-A", "FILE-B")`). Called during statement analysis to capture the qualified context of a name reference.

## `item_unit`

Construct and return a minimal `CobolUnit` of kind ITEM_UNIT representing a single-character data item with a given name. Used by semantic analysis to test whether the grammar recognizes a word as an item name without parsing a full file.

## `CobolUnit`

Data class representing one statement (unit) that the grammar reads: a data item, file description, SELECT entry, or procedure division sentence. Fields include the fixed-format text to parse, origin line numbers for each text line (0 for synthetic header lines), a list of (word, line) tuples for words in the statement, token positions, and a boolean flag indicating whether a token is written immediately after the first word without whitespace (used to handle malformed names like `(PFX)-NAME`).

## `CobolText`

Container for all units and non-unit statements extracted from one COBOL file. Fields include unit_list (data items, file descriptions, SELECT entries, sentences), name_dict (stand-in name mappings for non-ASCII names), program_list (program names and their lines), program_end_list (END PROGRAM statements), entry_list (ENTRY statements), copy_list (COPY statements and EXEC SQL INCLUDE), call_list (CALL statements and EXEC CICS PROGRAM references), word_list (words in non-unit statements with their qualifying names), code_line_list (source lines containing code), and other_statement_position_list (token positions of non-unit statements like division headers).

## `CobolCopy`

Data class recording a COPY statement or EXEC SQL INCLUDE: name (copybook name), line (source line of statement start), end_line (last source line), position (token index), library (optional library name from OF/IN clause), and replacing_list (operands of the REPLACING phrase as position/old/new tuples).

## `_Token`

Internal data class representing one token: kind (word, literal, picture, pseudo, period, other), text, source line (1-based), column in code area, flags for whitespace before the token (has_gap) and glue-continuation (is_glue), and position (index in the file's token list). Words on the same line that are glued by continuation are merged into a single word token.

## `_char_width`

Return the column width of a character: 2 for East Asian wide or fullwidth characters (east_asian_width in {W, F}), 1 otherwise. Used to count columns in fixed-format lines with non-ASCII text.

## `_column_line_list`

Normalize column width calculations by replacing ambiguous-width characters (east_asian_width A) with full-width stand-ins when the file contains any wide character. Returns the lines unchanged if the file is ASCII or has no wide characters (avoiding false positives on files with only ambiguous characters).

## `_line_width`

Return the total column width of a line, where each character contributes 1 or 2 columns based on `_char_width()`. Used to check whether lines fit within fixed-format column boundaries.

## `_column_slice`

Extract characters from a line that occupy columns in a given range [start, end), accounting for wide characters. Returns a substring that may contain fewer characters than the column range if wide characters are involved. Used to extract the indicator column, code area, and other fixed-format regions.

## `_indicator`

Return the character in the indicator column (column 6) of a fixed-format line, or "" if no character starts there. The indicator determines whether a line is a comment (*), continuation (-), or debug (D/d) line.

## `_is_directive_line`

Return whether a line contains a compiler directive (>>SOURCE FORMAT, >>IF, $SET, @OPTIONS, etc.) at the start of the line or the indicator column. Directives are parsed but not included in units.

## `_format_directive`

Extract the source format (FREE, FIXED, or VARIABLE) named by a directive line, or return None if the line is not a format directive. Used to track which format applies to following source lines.

## `_is_fixed_line`

Return whether a line can be a fixed-format source line: blank, short, a compiler option line, a directive, or holding a valid indicator character. Used during initial format detection.

## `_fixed_code_text`

Extract the code portion (columns 8 onward) from a fixed-format line, stripping surrounding whitespace, or return "" for lines without code (comments, directives, compiler options, debug lines).

## `_has_code_in_sequence_area`

Return whether columns 1–6 of a fixed-format line hold code rather than a sequence number. Detects free-format source written as fixed-format (names and operands in columns 1–6, or level numbers with code after column 7).

## `_is_free_start`

Return whether the source before the first format directive is free-format: true if any line cannot be fixed-format, has code in columns 1–6, or is the first code line with "-" in the indicator column.

## `_line_mode_list`

Return the mode (DIRECTIVE_MODE, FREE_MODE, FIXED_MODE, or VARIABLE_MODE) for each source line, tracking format directives to switch between free and fixed format.

## `_code_area_end_state`

Analyze the code area (columns 8–72) of a fixed-format line to determine what is left open at the end: the quote character of an unclosed literal, "*>" if a floating comment started, or "" if nothing is open. Used to decide whether the next line continues a literal.

## `_beyond_code_area`

Return what content (NO_TEXT_BEYOND, TAG_BEYOND, or CODE_BEYOND) lies in columns 73–80 of a fixed-format line. Distinguishes identification fields (tags, digits) from code that runs past column 72, accounting for open literals and floating comments.

## `_has_right_margin`

Return whether the code area of fixed-format lines ends at column 72 (right margin present) or extends to the line end. Counts lines with identification fields (TAG_BEYOND) vs. code past column 72 (CODE_BEYOND); a right margin is present if tag count ≥ code count.

## `_is_free_comment_line`

Return whether a free-format line starting with "*" is a comment line (true) or a continuation of an expression (false). "*" alone in column 1 or "*>" anywhere always starts a comment; an indented "*" continues an expression only if the previous line ends with an operand and the sentence is incomplete.

## `_code_text_list`

Return a list of (code text, is continuation) tuples for each source line, handling comment detection, fixed vs. free format, right margin detection, and column width adjustments for non-ASCII characters. Used internally by `code_text_list()` and token splitting.

## `_literal_end`

Find the position after the closing quote of a literal that starts at a given position, returning None if the literal is not closed by line end. A quote written twice counts as a literal character, not a closing quote.

## `_is_picture_position`

Return whether the next token after the last tokens is a picture string: true if the previous token is PIC or PICTURE, or if the previous two are PIC/PICTURE and IS.

## `_picture_token`

Parse a picture string starting at a position, replacing non-ASCII characters with "9" and returning the token and position after it. A trailing period, comma, or semicolon is left for the next token.

## `_line_token_list`

Tokenize the code of one source line, handling open literals from the previous line and continuation lines. Returns the tokens and any literal left without a closing quote. Implements glue-continuation for lines starting with "-" in the indicator column.

## `_next_token`

Parse the single token starting at a position: picture string (if position follows PIC/PICTURE), literal, "<>" (written as "NOT ="), pseudo-text (==...==), word, period, or other character. Returns the token, position after it, and the token itself if it is a literal without a closing quote.

## `_source_token_list`

Split all source lines into tokens in source order, merging literals and words that span lines and assigning each token a position index. Used as the first step of `split_cobol_source()`.

## `_word`

Return the upper-case text of the word at a token list index, or "" if the token is not a word. Helper for checking word values in statement analysis.

## `_statement_end`

Return the index of the period that ends a statement, or the token count if no period is found. Used to delimit statements.

## `_exec_end`

Return the index of END-EXEC that closes an EXEC block starting at a given index, or None if END-EXEC is not found.

## `_replacing_list`

Parse the operands of a REPLACING phrase (==old== BY ==new==, or LEADING/TRAILING variants) and return a list of (position, old text, new text) tuples. An operand is included only if its text is a single word (no spaces); pseudo-text delimiters are stripped.

## `_host_structure_name`

Return the name of a host structure that qualifies a word in an EXEC block (written as :STRUCT.NAME), or "" if the word is not written that way. Used when parsing EXEC SQL blocks.

## `_is_level_number`

Return whether a word consists only of decimal digits (is a level number).

## `_is_division_header`

Return whether tokens at an index are the first two words of a division header (IDENTIFICATION/ID DIVISION, ENVIRONMENT DIVISION, DATA DIVISION, PROCEDURE DIVISION).

## `_first_statement_start`

Return the token index of the first statement not in an EXEC block or COPY statement, skipping those structures at the file start. Used to determine which division the file begins with.

## `_start_division`

Return the division that the first real statement of a file belongs to (IDENTIFICATION, ENVIRONMENT, DATA, or PROCEDURE), used to initialize statement reading.

## `_call_name_token`

Return the token that names the program in a CALL or ENTRY statement, handling call conventions (STATIC "name") and returning None if no valid name token follows.

## `_put_stand_in_name`

Replace each word with non-ASCII characters in unit tokens with a unique stand-in name (QX1, QX2, ...), recording the mapping in cobol_text.name_dict. Stand-in prefixes are incremented to avoid collisions with existing words in the file.

## `_short_literal`

Cut a literal to a given width while keeping its quotes and prefix, avoiding cutting a doubled quote in half. Used when a line does not fit the code area.

## `_token_gap`

Return the whitespace (" " or "") that should be written between two consecutive tokens: a space if has_gap is true, or if an operator character requires spacing before or after.

## `_code_line_text_list`

Write the tokens of one source line as one or more fixed-format code area lines (up to 65 characters each), preserving indentation if the line fits, otherwise cutting literals and wrapping to subsequent lines.

## `_short_text`

Cut a line to the code area width (65 characters), preserving literal syntax if the line ends with a quote.

## `_is_name_only`

Return whether a statement is a single name (a paragraph name, label, or section header: name PERIOD or name SECTION [digits] PERIOD).

## `_has_name_suffix`

Return whether a token is written immediately after the first word following the first token of a statement without a gap (used to detect malformed names like CUST-(SFX) where a separator or period would normally follow the word).

## `_unit`

Write a statement as a `CobolUnit`, arranging tokens by source line, writing each line in fixed-format text (possibly across multiple lines if code does not fit the 65-character area), adding header lines and synthetic lines (FILLER after files, CONTINUE after single-name sentences), and collecting word/line metadata.

## `_Split`

Internal class that reads statements from the token list and sorts them into units (ITEM_UNIT, FILE_UNIT, SELECT_UNIT, PROCEDURE_UNIT) or non-unit statements. Handles division and section tracking, records PROGRAM-ID, END PROGRAM, ENTRY, COPY, CALL, and EXEC statements in `cobol_text`, and handles statement boundaries (periods, division headers).

# Summary

# Summary: codetwine/parsers/cobol_format.py

**Single Responsibility:** Parse COBOL source files (fixed-format, free-format, with tabs, comments, continuations, and non-ASCII characters) into statements that a grammar can read, while extracting metadata about programs, copybooks, calls, and word references.

**Main Public Functions:**
- `split_cobol_source()` — tokenize source and return units, programs, COPY/CALL statements, and word references
- `code_text_list()` — extract code portions from source lines
- `literal_value()` — extract content from literal tokens
- `qualifier_tuple()` — find qualifying names after OF/IN keywords
- `item_unit()` — construct a minimal test data item unit

**Key Concepts:** Fixed-format (columns 1–72) and free-format source; format directives; continuation lines; comments; East Asian wide characters; stand-in names for non-ASCII identifiers; tokens (words, literals, pictures, pseudo-text, periods); statements; units (ITEM, FILE, SELECT, PROCEDURE); EXEC blocks; COPY and CALL tracking.
