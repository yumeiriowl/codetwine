# Design Document: codetwine/parsers/cobol_format.py

# Design Specification

**Overview**

Parse COBOL source files in fixed and free formats into grammatically-readable units by handling format detection, tokenization, continuation lines, literals, and non-ASCII characters.

This file is used by:
- `split_cobol_source()` to convert raw COBOL source text into a `CobolText` object containing units (data items, file descriptions, SELECT entries, procedure sentences), programs, COPY statements, CALL/ENTRY statements, EXEC blocks, and word references with qualifier information.
- `item_unit()` to generate a synthetic data item unit for grammar validation of whether a word is recognized as an item name.
- `CobolText` data structure consumers in `codetwine/extractors/cobol_source.py` to read unit definitions and references via the `text`, `origin_line_list`, `word_list`, and `kind` fields of `CobolUnit` objects.
- `codetwine/cobol_file_index.py` to identify COBOL programs and copybooks by checking for units of kind `ITEM_UNIT`, `FILE_UNIT`, or `PROCEDURE_UNIT`.

The file depends on `line_list_of()` from `codetwine/utils/file_utils.py` to normalize line endings across different line break conventions (`\n`, `\r\n`, `\r`), ensuring consistent line numbering throughout the parsing pipeline. Files consuming this module use it to extract COBOL language elements (definitions, references, structure) for indexing and source analysis.

Design decisions: Column positions in fixed-format COBOL are counted with full-width characters (East Asian width classes W/F) as two columns, and ambiguous-width characters (A class) are treated as full-width when the file contains any wide character, requiring a stand-in character (`\u3013`) for accurate column slicing. The format (free/fixed/variable) is inferred from line structure and format directives; when ambiguous, free format is preferred. Stand-in names replace words containing non-ASCII characters outside the letters, digits, and hyphen set so the grammar can parse them; the mapping is recorded for later reconstruction. Continuation lines are tracked separately from line content to handle literals and words split across lines as single tokens.

**Definitions**

## `_INDICATOR_COLUMN`

The fixed-format column position (0-based, value 6) that holds the indicator character marking comment lines, continuation lines, debug lines, or directives; used to check line format validity and extract the code area starting at column 7.

## `_CODE_START_COLUMN`

The fixed-format column (0-based, value 7) where the code area of a fixed-format line begins; columns 1–6 are the sequence/indicator area, columns 7–72 are the code area.

## `_CODE_END_COLUMN`

The fixed-format column (0-based, value 72) after the last column of the code area; columns 73–80 are the identification area in traditional fixed format when the right margin is present.

## `_LINE_END_COLUMN`

The fixed-format column (0-based, value 80) marking the last column of a fixed-format line.

## `_TAB_SIZE`

The column width (value 8) to which tab characters are expanded during preprocessing.

## `_WIDE_CLASS_SET`

The set of East Asian width classes ("W", "F") that indicate a character occupies two columns; used to calculate line width for column-based slicing.

## `_AMBIGUOUS_CLASS`

The East Asian width class "A" (ambiguous) that may occupy one or two columns depending on context; treated as two columns when the file contains any full-width character.

## `_WIDE_STAND_IN`

A full-width stand-in character (U+3013) written in place of ambiguous-width characters during column counting to ensure accurate column-based text extraction.

## `_INDICATOR_CHAR_SET`

The set of valid indicator column characters (" ", "*", "/", "-", "d", "D", "$") defining what makes a line a valid fixed-format line.

## `_COMMENT_INDICATOR_CHAR_SET`

The indicator characters ("*", "/") that mark a line as a comment, so no code is extracted from it.

## `_NO_CODE_INDICATOR_CHAR_SET`

The indicator characters ("*", "/", "d", "D", "$") that indicate a line holds no executable code (comment, debug, or directive).

## `_LEFT_MARGIN`

The blank prefix string (seven spaces) written at the start of each code line in output units to position code in the fixed-format code area.

## `_CODE_WIDTH`

The number of columns (value 65) available in the code area of a fixed-format line (columns 7–72).

## `_DIRECTIVE_START_RE`

A regex matching the start of a compiler directive line (">>", or "$"/"@" followed by a word) to identify directive statements that do not contain code.

## `_COMMENT_START`

The character "*" marking the start of a free-format comment line when it appears in column 1.

## `_INLINE_COMMENT_START`

The sequence "*>" that starts an inline comment anywhere in a line, ending the line's code.

## `_OPERAND_END_CHAR_SET`

The characters (")", '"', "'") that can end an operand in a continuation check for free-format lines; used to determine if an indented "*" continues an expression or starts a comment.

## `_LEVEL_SEQUENCE_AREA_RE`

A regex matching a sequence area (columns 1–6) holding only a level number (1–2 decimal digits) and whitespace; used to detect data item definitions in the sequence area.

## `_WORD_SEQUENCE_AREA_RE`

A regex matching a sequence area holding a word followed by other text, indicating the sequence area contains code rather than a sequence number.

## `_RECORD_LEVEL_RANGE`

A range of valid level numbers (1–49) for data items in a record structure.

## `_OTHER_LEVEL_SET`

A set of level numbers (66, 77, 78, 88) for RENAMES, standalone items, and condition names not part of a record.

## `_OPTION_LINE_RE`

A regex matching compiler option lines starting with "CBL" or "PROCESS"; these lines contain no grammar code.

## `_FORMAT_DIRECTIVE_RE`

A regex extracting the source format ("FREE", "FIXED", "VARIABLE") from format directive lines (">>SOURCE FORMAT IS", "$SET SOURCEFORMAT").

## `_SPACE_RE`

A regex matching one or more whitespace characters; used to tokenize code by skipping spaces between tokens.

## `_LITERAL_START_RE`

A regex matching the start of a literal: optional prefix (up to 2 letters) followed by a quote character; used to identify and extract string/hex/alphanumeric literals.

## `_PSEUDO_TEXT_RE`

A regex matching pseudo-text delimiters "==" ... "==" used in COPY REPLACING clauses.

## `_NAME_TAG_PATTERN`

A regex pattern for name tags (":word:") embedded in host variable references in EXEC blocks.

## `_WORD_RE`

A regex matching a COBOL word: letters, digits, underscore, hyphen, or non-ASCII characters, with optional name tags; used to extract identifiers and variable names.

## `_PICTURE_RE`

A regex matching a picture string (any non-space sequence); used to extract PIC clause values without further parsing.

## `GRAMMAR_NAME_RE`

A regex matching names the grammar recognizes: ASCII letters, digits, and hyphens; used to distinguish whether a word needs a stand-in name.

## `_PICTURE_WORD_SET`

The keywords ("PIC", "PICTURE") that precede a picture string in a clause.

## `_NO_CODE_WORD_SET`

Keywords ("EJECT", "SKIP1", "SKIP2", "SKIP3") that start statements holding no code; these are dropped during parsing.

## `_QUALIFIER_WORD_SET`

The qualifier keywords ("OF", "IN") used in data item references to build qualifier tuples (e.g., X OF Y IN Z).

## `_IDENTIFICATION_WORD_SET`

The keywords ("IDENTIFICATION", "ID") that start the identification division header.

## `_DATA_SECTION_WORD_SET`

The section keywords ("FILE", "WORKING-STORAGE", "LOCAL-STORAGE", "LINKAGE", "SCREEN", "REPORT") in the data division.

## `_NO_UNIT_SECTION_WORD_SET`

Section keywords ("SCREEN", "REPORT") whose statements are not parsed into units because the grammar does not read them.

## `_FILE_ENTRY_WORD_SET`

The keywords ("FD", "SD") that start file descriptions; these are units.

## `_OPERATOR_CHAR_SET`

Operator characters ("=", "<", ">") that require spaces around them when adjacent to words; used for token spacing.

## `_SEPARATOR_TEXT_SET`

Separator tokens (",", ";") that separate clauses; used in name suffix detection.

## `IDENTIFICATION_DIVISION`

Constant string "IDENTIFICATION" naming the first division of a COBOL program.

## `ENVIRONMENT_DIVISION`

Constant string "ENVIRONMENT" naming the division containing SELECT entries and I/O configuration.

## `DATA_DIVISION`

Constant string "DATA" naming the division containing data item definitions and file descriptions.

## `PROCEDURE_DIVISION`

Constant string "PROCEDURE" naming the division containing executable statements and paragraphs.

## `_DIRECTIVE_MODE`

Constant string "directive" indicating a line is a format directive with no code.

## `_FREE_MODE`

Constant string "free" indicating a line is in free-format source.

## `_FIXED_MODE`

Constant string "fixed" indicating a line is in fixed-format source with right margin at column 72.

## `_VARIABLE_MODE`

Constant string "variable" indicating a line is in fixed-format source without right margin (SOURCEFORMAT VARIABLE).

## `_NO_TEXT_BEYOND`

Constant string "" indicating nothing but blanks or a floating comment exists past the code area of a fixed-format line.

## `_TAG_BEYOND`

Constant string "tag" indicating an identification field (sequence/tag) exists past the code area and the line has the right margin.

## `_CODE_BEYOND`

Constant string "code" indicating executable code runs past the code area, so the line has no right margin.

## `ITEM_UNIT`

Constant string "item" identifying a unit that is a data item definition.

## `FILE_UNIT`

Constant string "file" identifying a unit that is a file description (FD/SD).

## `SELECT_UNIT`

Constant string "select" identifying a unit that is a SELECT entry.

## `PROCEDURE_UNIT`

Constant string "procedure" identifying a unit that is a sentence of the procedure division.

## `_START_LINE_DICT`

A mapping from unit kind to the list of header lines written before each unit's statement (identification division, data division, relevant section) to make the unit parseable by the grammar.

## `_FILLER_ITEM_LINE`

The constant line "01 FILLER PIC X." appended after file descriptions to make them valid units.

## `_CONTINUE_LINE`

The constant line "CONTINUE." appended after procedure sentences that are single names.

## `_STAND_IN_PREFIX`

The initial prefix "QX" for stand-in names replacing words with non-ASCII characters; extended with "Q" characters to avoid collision with source words.

## `_STAND_IN_PREFIX_CHAR`

The character "Q" appended to the stand-in prefix to avoid collisions.

## `_Token`

A dataclass representing one token (word, literal, picture, pseudo-text, period, or other character) with its kind, text, source line, column position, gap/glue flags, and position index; the basic unit produced by tokenization.

## `_Token.kind`

The token type: "word", "literal", "picture", "pseudo", "period", or "other".

## `_Token.text`

The token's source text, possibly with transformations (upper-case literal prefix, non-ASCII picture characters as "9", "<>" as "NOT =").

## `_Token.line`

The source line number (1-based) where the token starts.

## `_Token.column`

The column (0-based) in the code text where the token starts; initialized to 0 and set later.

## `_Token.has_gap`

Whether whitespace or a line start precedes the token.

## `_Token.is_glue`

Whether the token continues a previous line (continuation line with no literal break).

## `_Token.position`

The index of the token among all tokens in the file.

## `CobolUnit`

A dataclass representing one statement parsed into a unit (data item, file description, SELECT entry, or procedure sentence) with fixed-format text, origin line list, word list, token positions, and a flag for name suffixes; the output unit passed to the grammar.

## `CobolUnit.kind`

The unit type (ITEM_UNIT, FILE_UNIT, SELECT_UNIT, or PROCEDURE_UNIT).

## `CobolUnit.text`

The fixed-format text of the unit with header lines followed by statement lines; ready to parse with the COBOL grammar.

## `CobolUnit.origin_line_list`

A list mapping each line of the unit text to its source line number (1-based), or 0 for synthetic header lines.

## `CobolUnit.word_list`

A list of (word text, source line) tuples for each word in the statement, used to match tree-sitter nodes to source lines.

## `CobolUnit.start_position`

The token index of the first token of the statement among all file tokens.

## `CobolUnit.end_position`

The token index of the last token of the statement.

## `CobolUnit.has_name_suffix`

Whether a token immediately follows the first word after the unit's first token without a gap, indicating a name constructed with operators.

## `CobolCopy`

A dataclass representing a COPY statement or EXEC SQL INCLUDE with the copybook name, source lines, token position, optional library name, and replacing operands.

## `CobolCopy.name`

The copybook name to be copied.

## `CobolCopy.line`

The source line (1-based) of the COPY/INCLUDE statement.

## `CobolCopy.end_line`

The last source line of the COPY/INCLUDE statement (may span multiple lines).

## `CobolCopy.position`

The token index of the COPY/EXEC keyword.

## `CobolCopy.library`

The library name if the COPY uses "OF library" syntax.

## `CobolCopy.replacing_list`

The REPLACING operands as (position, old text, new text) tuples, where position is "" (any), "LEADING", or "TRAILING".

## `CobolText`

A dataclass collecting all parsed units and metadata from a COBOL file: units, program/entry/call information, COPY statements, non-unit words, and code line list; the primary output of split_cobol_source().

## `CobolText.unit_list`

Data items, file descriptions, SELECT entries, and procedure sentences in source order.

## `CobolText.name_dict`

Mapping from stand-in names (upper-case) to their source text for words with non-ASCII characters.

## `CobolText.program_list`

Tuples of (program name, program start line, PROGRAM-ID line) for each PROGRAM-ID.

## `CobolText.program_end_list`

Tuples of (program name, END PROGRAM line) for each END PROGRAM statement.

## `CobolText.entry_list`

Tuples of (entry name, ENTRY line) for each ENTRY statement.

## `CobolText.copy_list`

COPY statements and EXEC SQL INCLUDE blocks with names and replacement info.

## `CobolText.call_list`

Tuples of (program name, is literal, line) for each CALL statement and EXEC CICS PROGRAM() reference.

## `CobolText.word_list`

Tuples of (word, line, qualifier tuple) for words in non-unit statements, with qualifiers from OF/IN/colon syntax.

## `CobolText.code_line_list`

Source lines containing tokens, in ascending order.

## `CobolText.other_statement_position_list`

Token indices of the first token of each non-unit statement (division/section headers, PROGRAM-ID, etc.).

## `_char_width`

Return the column width (1 or 2) of a character based on its East Asian width class; full-width characters (W/F) count as 2 columns.

## `_column_line_list`

Convert lines to a version where ambiguous-width characters are replaced with a stand-in full-width character if the file contains any full-width characters, ensuring accurate column counting; otherwise return lines as-is.

## `_line_width`

Return the total column width of a line by summing character widths; ASCII lines use character count, non-ASCII lines sum individual character widths.

## `_column_slice`

Extract the substring of a line occupying a given column range (start to end), accounting for multi-column characters; used to extract fixed-format fields like indicator, code area, and identification area.

## `_indicator`

Extract the indicator column character from a fixed-format line; return "" if no character starts in the indicator column due to line length or wide character positioning.

## `_is_directive_line`

Determine whether a line is a compiler directive by checking if a directive pattern appears at the line start or from the indicator column onward.

## `_format_directive`

Extract the source format name ("FREE", "FIXED", "VARIABLE") from a format directive line, or return None if the line is not a format directive.

## `_is_fixed_line`

Determine whether a line can be a fixed-format line by checking if it is blank, short, an option line, a directive, or has a valid indicator character; used to infer source format before the first directive.

## `_fixed_code_text`

Extract the code text (columns 8–end) from a fixed-format line with code, excluding lines that are comments, directives, or compiler options; used to extract the executable content.

## `_has_code_in_sequence_area`

Determine whether columns 1–6 of a fixed-format line contain code (not a sequence number) by checking for a word followed by text or a level number before other code; used to infer free-format source.

## `_is_free_start`

Determine whether lines before the first format directive are free-format source by checking if any line is not a valid fixed-format line or contains code in the sequence area.

## `_line_mode_list`

Return the read mode (directive/free/fixed/variable) for each line, applying format directives and inferring free format for lines before the first directive.

## `_code_area_end_state`

Return the state of the code area at its end (column 72): an open quote character, "*>" for a floating comment start, or "" for closed state; used to determine if code/literals extend past column 72.

## `_beyond_code_area`

Determine the content type (none/tag/code) past column 72 of a fixed-format line by checking for open literals, floating comments, sequence numbers, and next-line continuation; used to infer whether the file has a right margin.

## `_has_right_margin`

Determine whether fixed-format lines have a right margin at column 72 by counting lines with identification fields (tag) versus lines with code past column 72; more tags indicate a right margin.

## `_is_free_comment_line`

Determine whether a free-format line starting with "*" is a comment or a continuation of an operand from the previous line; continuation occurs when the last line ends with an operand and does not end its sentence.

## `_code_text_list`

Return the code text and continuation flag for each line by splitting based on format mode, handling comments, literals, and right margin; returns a list of (code text, is continuation) tuples.

## `code_text_list`

Extract code from each line of a COBOL source by expanding tabs, determining format and right margin, and returning one code string per line; used as the public interface for code extraction.

## `_literal_end`

Find the position after the closing quote of a literal, accounting for doubled quotes as quote characters within the literal; return None if the literal is not closed on the line.

## `_is_picture_position`

Determine whether the next token should be read as a picture string by checking if the last token is PIC/PICTURE or IS following PIC/PICTURE.

## `_picture_token`

Parse a picture string (non-space sequence) and return it as a token, converting non-ASCII characters to "9"; a final period is left for the next token.

## `_line_token_list`

Tokenize the code of one line, handling open literals from the previous line, continuation lines, and gaps; return (token list, open literal or None) tuple; open literals have their text extended in place.

## `_next_token`

Read and return the next token starting at a position in one line's code, recognizing pictures, literals (with prefix and quote), "<>" as "NOT =", pseudo-text, words, periods, and other characters; return (token, position after, open literal if unclosed) tuple.

## `_source_token_list`

Tokenize all code of a COBOL file by processing each line, handling continuation lines and merging glued words; return all tokens in source order with position indices.

## `_word`

Return the upper-case text of the word at a token list index, or "" if the token is not a word.

## `_statement_end`

Find the index of the period ending a statement, or return the token count if no period is found.

## `_exec_end`

Find the index of the END-EXEC keyword closing an EXEC block, or return None if not found.

## `_replacing_list`

Parse REPLACING operands (old BY new) from tokens, extracting position modifiers (LEADING/TRAILING) and operand texts; operands must be single words or pseudo-texts without internal spaces.

## `literal_value`

Extract the content of a literal (removing prefix and quotes) or return the text unchanged if it is not a literal.

## `qualifier_tuple`

Extract the names that qualify a word (X OF Y IN Z) by following OF/IN keywords after a word's position; return them as a tuple in order.

## `_host_structure_name`

Extract the structure name from a host variable reference in EXEC blocks written as :STRUCT.NAME, or return "" if the word is not in that format.

## `_is_level_number`

Return whether a word is a decimal level number (1–2 digits).

## `_is_division_header`

Determine whether tokens at an index are the start of a division header (word and DIVISION keyword).

## `_first_statement_start`

Find the index of the first statement after skipping EXEC blocks, COPY statements, and periods at the file start.

## `_start_division`

Determine the division (IDENTIFICATION, ENVIRONMENT, DATA, or PROCEDURE) containing the first statement, inferring from the first statement's keyword.

## `_call_name_token`

Extract the program name token from a CALL or ENTRY statement, handling call convention modifiers like STATIC; return the word or literal token, or None if not found.

## `_Split`

A class that processes token streams into units and records what is parsed without the grammar (programs, COPY, CALL, EXEC, ENTRY); maintains division/section context and coordinates unit creation.

## `_Split.__init__`

Initialize the splitter with a token list, output recorder, starting division, and parsing state flags.

## `_Split.run`

Process all tokens into statements, categorize them as units or non-unit records, and return the (unit kind, tokens) list.

## `_Split._record_word_list`

Record the words of a non-unit statement with their line numbers and qualifiers into the output cobol_text.

## `_Split._statement`

Collect tokens for one statement, ending at a period or division header; skip COPY and EXEC in code areas, and drop no-code keywords; handle IDENTIFICATION division separately.

## `_Split._read_statement`

Route a statement to the appropriate reader based on its first word and current division; return the next token index.

## `_Split._read_program`

Record a PROGRAM-ID and its name; if the name is on the next line, fetch it; return next token index.

## `_Split._read_environment`

Process environment division statements: SELECT entries become units, others are recorded as non-unit words.

## `_Split._read_data`

Process data division statements: section headers set the current section, data items and file descriptions become units in most sections, SCREEN/REPORT sections are recorded as non-unit words.

## `_Split._read_procedure`

Process procedure division sentences: record CALL and ENTRY names, convert ENTRY to CALL in the unit, skip DECLARATIVES, and make sentences units.

## `_Split._read_exec`

Process EXEC blocks: record SQL INCLUDE as COPY, record CICS PROGRAM() as CALL, record host variables as non-unit words, replace the block with CONTINUE in procedure division units.

## `_Split._read_copy`

Record a COPY statement with its name, library, and REPLACING operands; return the index after the statement.

## `_put_stand_in_name`

Replace each word containing non-ASCII characters (outside ASCII letters, digits, hyphen) with a generated stand-in name, recording the mapping in cobol_text.name_dict; extend the stand-in prefix to avoid collisions.

## `_short_literal`

Truncate a literal to a given width while preserving its quotes and not splitting doubled quotes; used to fit literals into the code area.

## `_token_gap`

Return the space between two consecutive tokens (" " or ""), accounting for gaps, operator characters, and word boundaries.

## `_code_line_text_list`

Convert tokens of one source line into one or more fixed-format code lines (max 65 characters) by truncating literals and wrapping long lines; preserve the first token's indentation if it fits.

## `_short_text`

Truncate code text to the code width, cutting literals carefully to avoid incomplete quotes.

## `_is_name_only`

Determine whether a sentence is a single name, or a name followed by SECTION; used to detect trivial sentences.

## `_has_name_suffix`

Determine whether a token (other than period or separator) immediately follows the first word after the first token without a gap; indicates a name built with operators or pseudo-text.

## `_unit`

Create a CobolUnit from a statement by grouping tokens by source line, writing header lines, formatting code into fixed-format lines, and adding any required suffix lines (FILLER for files, CONTINUE for name-only procedure sentences).

## `item_unit`

Create a synthetic data item unit for a single character with the given name to test whether the grammar recognizes it as an item name; used for disambiguation.

## `split_cobol_source`

Parse a COBOL source file into units, programs, copybooks, calls, and metadata by tokenizing, splitting into statements, applying stand-in names, and formatting as fixed-format text; the main public entry point.

# Summary

# Summary: codetwine/parsers/cobol_format.py

**Single Responsibility:** Parse COBOL source files in fixed and free formats into grammatically-readable units by detecting format, tokenizing code, handling continuation lines and literals, and normalizing non-ASCII characters.

**Main Public Definitions:**
- `split_cobol_source()` – primary entry point converting raw COBOL text into CobolText object
- `code_text_list()` – extracts code from each source line
- `item_unit()` – creates synthetic data item for grammar validation
- `CobolText` – output dataclass collecting units, programs, copies, calls, metadata
- `CobolUnit` – represents one parsed statement (data item, file, SELECT, procedure)

**Key Terms:** Fixed/free format detection, indicator/code columns, tab expansion, wide-character column counting with stand-in substitution, literal and picture parsing, continuation line handling, statement tokenization, COPY/CALL/EXEC/ENTRY extraction, word qualification (OF/IN), non-ASCII name mapping, fixed-format text generation with headers and wrapping.
