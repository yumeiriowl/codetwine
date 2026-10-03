import os
from dataclasses import dataclass
from dotenv import load_dotenv
from tree_sitter import Language
import tree_sitter_c as tsc
import tree_sitter_c_sharp as tscsharp
import tree_sitter_cpp as tscpp
import tree_sitter_java as tsjava
import tree_sitter_javascript as tsjavascript
import tree_sitter_kotlin as tskotlin
import tree_sitter_language_pack as tspack
import tree_sitter_python as tspython
import tree_sitter_rust as tsrust
import tree_sitter_sql as tssql
import tree_sitter_typescript as tstypescript


load_dotenv()

_REQUIRED = object()


def get_config_value(
    key: str, default: object = _REQUIRED, var_type: type = str,
) -> str | int | float | bool | None:
    """Retrieve an environment variable and return it converted to the specified type.

    Args:
        key: Environment variable name.
        default: Default value when the variable is not set.
                 If omitted, raises ValueError when the variable is missing.
        var_type: Target type for conversion (str / int / float / bool).

    Returns:
        The converted configuration value.

    Raises:
        ValueError: If a required environment variable is not set.
    """
    value = os.getenv(key)

    # When the environment variable is not set
    if value is None:
        if default is _REQUIRED:
            raise ValueError(
                f"Environment variable '{key}' is not set. "
                f"Please set it in the .env file or your shell."
            )
        if default is None:
            return None
        value = str(default)

    # Type conversion
    if var_type == bool:
        return value.lower() in ("true", "1", "yes", "on")
    if var_type == int:
        return int(value)
    if var_type == float:
        return float(value)
    return value


# == LLM settings =============================================
LLM_API_KEY = get_config_value("LLM_API_KEY", default="")
LLM_MODEL = get_config_value("LLM_MODEL", default="")
LLM_API_BASE = get_config_value("LLM_API_BASE", default="")
OUTPUT_LANGUAGE = get_config_value("OUTPUT_LANGUAGE", default="English")
DOC_MAX_TOKENS = get_config_value("DOC_MAX_TOKENS", default=16384, var_type=int)

# == Path settings =============================================
REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_PROJECT_DIR = get_config_value("DEFAULT_PROJECT_DIR", default=REPO_ROOT)
DEFAULT_OUTPUT_DIR = get_config_value(
    "DEFAULT_OUTPUT_DIR",
    default=os.path.join(REPO_ROOT, "output"),
)
DOC_TEMPLATE_PATH = get_config_value(
    "DOC_TEMPLATE_PATH",
    default=os.path.join(REPO_ROOT, "doc_template.json"),
)

# == Performance settings ===================================
MAX_WORKERS = get_config_value("MAX_WORKERS", default=4, var_type=int)
# Retries of an LLM call after a rate limit error (0: call once, no retry)
MAX_RETRIES = get_config_value("MAX_RETRIES", default=3, var_type=int)
RETRY_WAIT = get_config_value("RETRY_WAIT", default=2, var_type=int)

# Maximum number of files whose parse results are kept in memory at once.
# The least recently used entry is discarded once the count exceeds this value.
# 0 keeps every parse result until the run ends.
PARSE_CACHE_MAX_FILES = get_config_value(
    "PARSE_CACHE_MAX_FILES", default=200, var_type=int
)

# == Output settings ========================================
# The accepted values of KNOWLEDGE_FORMAT. process_all_files() checks the setting
# against this before it analyses anything; what it checks is the value the pipeline
# module holds at that point, not what the environment held at import time
KNOWLEDGE_FORMAT_TUPLE = ("json", "sqlite", "both")

# Which form the whole-project analysis result is written in.
#   json   ... project_knowledge.json only
#   sqlite ... project_knowledge.sqlite only
#   both   ... both files
# The per-file JSON files under the output directory are written in every case.
KNOWLEDGE_FORMAT = get_config_value(
    "KNOWLEDGE_FORMAT", default="json", var_type=str
).strip().lower()

# == Analysis settings =============================================
ENABLE_LLM_DOC = get_config_value("ENABLE_LLM_DOC", default=True, var_type=bool)
SUMMARY_MAX_CHARS: int = get_config_value("SUMMARY_MAX_CHARS", default=600, var_type=int)

# Enable LLM summarization of large code as a fallback when the design-document
# prompt exceeds the model context window (True/False). When False, the context
# is only reduced by dropping caller/callee context (no extra LLM calls).
ENABLE_CODE_SUMMARY = get_config_value("ENABLE_CODE_SUMMARY", default=True, var_type=bool)

# Definitions / dependency symbols longer than this many lines are candidates
# for LLM summarization during context-overflow fallback.
CODE_SUMMARY_TRIGGER_LINES = get_config_value(
    "CODE_SUMMARY_TRIGGER_LINES", default=40, var_type=int
)

# Target character limit for a single code behavior summary.
CODE_SUMMARY_MAX_CHARS = get_config_value(
    "CODE_SUMMARY_MAX_CHARS", default=400, var_type=int
)

# Encodings tried, in order, on a source file that has no BOM and is not valid UTF-8
# (comma-separated Python codec names, e.g. "euc_jp,cp932"). Empty: none. A file that
# none of them decodes is decoded with the encoding charset-normalizer detects.
# process_all_files() checks each name before it analyses anything.
_SOURCE_ENCODING_ENV = get_config_value("SOURCE_ENCODING", default="", var_type=str)
SOURCE_ENCODING: list[str] = [
    e.strip() for e in _SOURCE_ENCODING_ENV.split(",") if e.strip()
]

_EXCLUDE_PATTERNS_ENV = get_config_value("EXCLUDE_PATTERNS", default="", var_type=str)
EXCLUDE_PATTERNS: list[str] = (
    [p.strip() for p in _EXCLUDE_PATTERNS_ENV.split(",") if p.strip()]
    if _EXCLUDE_PATTERNS_ENV
    else [
        "__pycache__",
        ".git",
        ".github",
        ".venv",
        "node_modules",
    ]
)

# Per-language definition node settings
#
# Mapping of "AST node type -> child node type that holds the name"
#
# Standard pattern:
#   The value specifies the child node type to look for among the node's direct children.
#
# Special pattern (sentinel value):
#   When the name node is nested two or more levels deep, or the node defines several
#   names, a "__sentinel__" value is used. definition_name_list in definitions.py checks
#   the sentinel value and dispatches to a dedicated extraction function.
PYTHON_DEFINITION_DICT = {
    "function_definition": "identifier",
    "class_definition": "identifier",
    "decorated_definition": "identifier",
    "expression_statement": "__assignment__",
    "type_alias_statement": "__type_alias__",
}

JAVA_DEFINITION_DICT = {
    "class_declaration": "identifier",
    "method_declaration": "identifier",
    "interface_declaration": "identifier",
    "constructor_declaration": "identifier",
    "compact_constructor_declaration": "identifier",
    "enum_declaration": "identifier",
    "enum_constant": "identifier",
    "record_declaration": "identifier",
    "annotation_type_declaration": "identifier",
    "annotation_type_element_declaration": "identifier",
    "field_declaration": "__variable_declarator__",
}

CPP_DEFINITION_DICT = {
    "class_specifier": "__body_name__",
    "struct_specifier": "__body_name__",
    "union_specifier": "__body_name__",
    "enum_specifier": "__body_name__",
    "enumerator": "identifier",
    "function_declarator": "__declarator_name__",
    "function_definition": "__function_declarator__",
    "namespace_definition": "namespace_identifier",
    "declaration": "__init_declarator__",
    "field_declaration": "__field_declarator__",
    "type_definition": "__type_declarator__",
    "alias_declaration": "type_identifier",
    "concept_definition": "identifier",
    "preproc_def": "identifier",
    "preproc_function_def": "identifier",
}

C_DEFINITION_DICT = {
    "function_declarator": "__declarator_name__",
    "function_definition": "__function_declarator__",
    "struct_specifier": "__body_name__",
    "union_specifier": "__body_name__",
    "enum_specifier": "__body_name__",
    "enumerator": "identifier",
    "declaration": "__init_declarator__",
    "field_declaration": "__field_declarator__",
    "type_definition": "__type_declarator__",
    "preproc_def": "identifier",
    "preproc_function_def": "identifier",
}

KOTLIN_DEFINITION_DICT = {
    "class_declaration": "identifier",
    "function_declaration": "identifier",
    "object_declaration": "identifier",
    "companion_object": "identifier",
    "enum_entry": "identifier",
    "type_alias": "identifier",
    "property_declaration": "__kotlin_property__",
}

JS_DEFINITION_DICT = {
    "function_declaration": "identifier",
    "generator_function_declaration": "identifier",
    "method_definition": "property_identifier",
    "class_declaration": "identifier",
    "field_definition": "property_identifier",
    "lexical_declaration": "__variable_declarator__",
    "variable_declaration": "__variable_declarator__",
    "expression_statement": "__member_assignment__",
    "export_statement": "__default_export__",
    "pair": "__export_member__",
}

TS_DEFINITION_DICT = {
    "function_declaration": "identifier",
    "generator_function_declaration": "identifier",
    "function_signature": "identifier",
    "method_definition": "property_identifier",
    "method_signature": "property_identifier",
    "abstract_method_signature": "property_identifier",
    "property_signature": "property_identifier",
    "class_declaration": "type_identifier",
    "abstract_class_declaration": "type_identifier",
    "interface_declaration": "type_identifier",
    "public_field_definition": "property_identifier",
    "lexical_declaration": "__variable_declarator__",
    "variable_declaration": "__variable_declarator__",
    "type_alias_declaration": "type_identifier",
    "enum_declaration": "identifier",
    "enum_assignment": "property_identifier",
    "property_identifier": "__enum_member__",
    "internal_module": "identifier",
    "expression_statement": "__member_assignment__",
    "export_statement": "__default_export__",
    "pair": "__export_member__",
}

RUST_DEFINITION_DICT = {
    "function_item": "identifier",
    "function_signature_item": "identifier",
    "struct_item": "type_identifier",
    "enum_item": "type_identifier",
    "union_item": "type_identifier",
    "trait_item": "type_identifier",
    "impl_item": "__impl_type__",
    "type_item": "type_identifier",
    "const_item": "identifier",
    "static_item": "identifier",
    "mod_item": "__inline_module__",
    "macro_definition": "identifier",
}

CSHARP_DEFINITION_DICT = {
    "class_declaration": "__name_field__",
    "struct_declaration": "__name_field__",
    "interface_declaration": "__name_field__",
    "enum_declaration": "__name_field__",
    "record_declaration": "__name_field__",
    "delegate_declaration": "__name_field__",
    "method_declaration": "__name_field__",
    "constructor_declaration": "__name_field__",
    "property_declaration": "__name_field__",
    "event_declaration": "__name_field__",
    "enum_member_declaration": "__name_field__",
    "field_declaration": "__variable_declaration__",
    "event_field_declaration": "__variable_declaration__",
    "indexer_declaration": "__unnamed_member__",
    "operator_declaration": "__unnamed_member__",
    "conversion_operator_declaration": "__unnamed_member__",
    "destructor_declaration": "__unnamed_member__",
}

# Definition types of COBOL -> the node of a unit that holds the name.
# The definitions are read by cobol_source.read_cobol_source(); the values are not read
COBOL_DEFINITION_DICT = {
    "program_definition": "program_name",
    "entry_statement": "string",
    "section_header": "WORD",
    "paragraph_header": "WORD",
    "data_description": "entry_name",
    "file_description_entry": "WORD",
    "occurs_indexed": "WORD",
}

# Definition types of a BMS source (the symbolic map of each map) -> the node that holds
# the name. The definitions are read by bms_source.read_bms_source(); the values are not read
BMS_DEFINITION_DICT = {
    "data_description": "entry_name",
}

# Definition types of R -> the node that holds the name.
# The definitions are read by r_source.r_definition_list(); the values are not read
R_DEFINITION_DICT = {
    "function_definition": "identifier",
    "binary_operator": "identifier",
    "call": "string",
    "argument": "identifier",
}

SQL_DEFINITION_DICT = {
    "create_table": "__object_reference__",
    "create_view": "__object_reference__",
    "create_materialized_view": "__object_reference__",
    "create_function": "__object_reference__",
    "create_procedure": "__object_reference__",
    "create_type": "__object_reference__",
    "create_sequence": "__object_reference__",
    "create_trigger": "__object_reference__",
    "create_index": "identifier",
    "create_schema": "identifier",
}


# Per-language import extraction queries (for tree-sitter Query)
#
# tree-sitter queries describe AST patterns using S-expressions.
# Capture names: @module  -> the import source module/path
#                @name    -> individual imported name (the Y in "from X import Y")
#                @import_node -> the entire import statement (used for line number retrieval)
#
# Each language's query list contains multiple patterns matching the language grammar.
# Multiple patterns can appear in a single query string (separated by newlines).

# Python import queries
# - import X / import X as Y: captures @module only
# - from X import Y, Z: captures both @module and @name
# - from X import *: captures @module only; the statement holds a wildcard_import
_PYTHON_IMPORT_QUERY = """
(import_statement
  name: (dotted_name) @module) @import_node

(import_statement
  name: (aliased_import
    name: (dotted_name) @module)) @import_node

(import_from_statement
  module_name: (_) @module
  name: (_) @name) @import_node

(import_from_statement
  module_name: (_) @module
  (wildcard_import)) @import_node
"""

# JavaScript / TypeScript import queries
# - import X from 'module': @module and @default_name
# - import { X, Y } from 'module': @module and @name
# - import * as X from 'module': @module and @namespace_name
# - import 'module': @module only
# - export { X } from 'module': @module and @name (re-export)
# - export * from 'module': @module only (re-export); the statement holds a "*"
# - export * as X from 'module': @module and @namespace_name (re-export)
# - require('module') / import('module'): @module only (CommonJS, dynamic import)
# - const X = require('module'): @module and @namespace_name (CommonJS)
# - const { X, Y: Z, V = 1, W: U = 2 } = require('module'): @module and @name (CommonJS
#   destructuring, with or without a default value)
# - const X = require('module').Y: @module, @name and @member_name (a member of the module)
# - const X = await import('module') / const { X, Y: Z } = await import('module'):
#   @module and @namespace_name / @name (dynamic import)
# - require('module').X: @module and @member_use (a member used where the module is required)
# - import('module').then((X) => ...): @module and @callback (the function given the module)
_JS_IMPORT_QUERY = """
(import_statement
  source: (string) @module) @import_node

(import_statement
  (import_clause
    (identifier) @default_name)
  source: (string) @module) @import_node

(import_statement
  (import_clause
    (namespace_import
      (identifier) @namespace_name))
  source: (string) @module) @import_node

(import_statement
  (import_clause
    (named_imports
      (import_specifier
        name: (identifier) @name)))
  source: (string) @module) @import_node

(export_statement
  source: (string) @module) @import_node

(export_statement
  (namespace_export
    (identifier) @namespace_name)
  source: (string) @module) @import_node

(export_statement
  (export_clause
    (export_specifier
      name: (identifier) @name))
  source: (string) @module) @import_node

(call_expression
  function: (identifier) @_require_func
  arguments: (arguments (string) @module)) @import_node

(call_expression
  function: (import)
  arguments: (arguments (string) @module)) @import_node

(member_expression
  object: (call_expression
    function: (identifier) @_require_func
    arguments: (arguments (string) @module))
  property: (property_identifier) @member_use) @import_node

(call_expression
  function: (member_expression
    object: (call_expression
      function: (import)
      arguments: (arguments (string) @module))
    property: (property_identifier) @_then_func)
  arguments: (arguments . [(arrow_function) (function_expression)] @callback))

(variable_declarator
  name: (identifier) @namespace_name
  value: (call_expression
    function: (identifier) @_require_func
    arguments: (arguments (string) @module))) @import_node

(variable_declarator
  name: (object_pattern
    (shorthand_property_identifier_pattern) @name)
  value: (call_expression
    function: (identifier) @_require_func
    arguments: (arguments (string) @module))) @import_node

(variable_declarator
  name: (object_pattern
    (pair_pattern
      value: (identifier) @name))
  value: (call_expression
    function: (identifier) @_require_func
    arguments: (arguments (string) @module))) @import_node

(variable_declarator
  name: (object_pattern
    (object_assignment_pattern
      left: (shorthand_property_identifier_pattern) @name))
  value: (call_expression
    function: (identifier) @_require_func
    arguments: (arguments (string) @module))) @import_node

(variable_declarator
  name: (object_pattern
    (pair_pattern
      value: (assignment_pattern
        left: (identifier) @name)))
  value: (call_expression
    function: (identifier) @_require_func
    arguments: (arguments (string) @module))) @import_node

(variable_declarator
  name: (identifier) @name
  value: (member_expression
    object: (call_expression
      function: (identifier) @_require_func
      arguments: (arguments (string) @module))
    property: (property_identifier) @member_name)) @import_node

(variable_declarator
  name: (identifier) @namespace_name
  value: (await_expression
    (call_expression
      function: (import)
      arguments: (arguments (string) @module)))) @import_node

(variable_declarator
  name: (object_pattern
    (shorthand_property_identifier_pattern) @name)
  value: (await_expression
    (call_expression
      function: (import)
      arguments: (arguments (string) @module)))) @import_node

(variable_declarator
  name: (object_pattern
    (pair_pattern
      value: (identifier) @name))
  value: (await_expression
    (call_expression
      function: (import)
      arguments: (arguments (string) @module)))) @import_node

(variable_declarator
  name: (object_pattern
    (object_assignment_pattern
      left: (shorthand_property_identifier_pattern) @name))
  value: (await_expression
    (call_expression
      function: (import)
      arguments: (arguments (string) @module)))) @import_node

(variable_declarator
  name: (object_pattern
    (pair_pattern
      value: (assignment_pattern
        left: (identifier) @name)))
  value: (await_expression
    (call_expression
      function: (import)
      arguments: (arguments (string) @module)))) @import_node
"""

# Java import queries
# - import com.example.Foo: @module only
_JAVA_IMPORT_QUERY = """
(import_declaration
  (scoped_identifier) @module) @import_node
"""

# C/C++ #include queries
# - #include <stdio.h> / #include "helper.h": @module only
_C_IMPORT_QUERY = """
(preproc_include
  path: (_) @module) @import_node
"""

# Kotlin import queries
# - import com.example.Foo: @module only
_KOTLIN_IMPORT_QUERY = """
(import
  (qualified_identifier) @module) @import_node
"""

# Rust use / mod / extern crate / path queries
# - use a::b::{C, D as E}: expanded into one import per name
# - mod name; (without a body): the child module file
# - extern crate name;
# - a::b::c written in code without a use declaration, also in the arguments of a macro
# @path_item is handled by rust_import_list in rust_path.py
_RUST_IMPORT_QUERY = """
(use_declaration) @path_item

(mod_item
  name: (identifier)
  !body) @path_item

(extern_crate_declaration) @path_item

(scoped_identifier) @path_item

(scoped_type_identifier) @path_item

(token_tree) @path_item
"""


# Per-language usage tracking settings (for extract_usages)
#
# call_types:     AST node types representing function calls
# attribute_types: AST node types representing attribute access
# skip_parent_types: Do not treat an identifier as a usage when its parent is one of these types
#                    (definition names, import names, parameter names, etc. that are part of syntax)
# skip_parent_types_for_type_ref:
#     Skip only when the parent of a type_identifier / namespace_identifier is one of these types.
#     Almost all occurrences of type references indicate dependencies.
#     Only import statements and scope resolution are skipped.
# identifier_parent_types: When set, treat an identifier as a usage only when its parent is one of these types
# identifier_types: AST node types read like an identifier (JS/TS: the name in { name })
# path_types:     AST node types of a path written with "::" (Rust). The outermost path is a usage
#                 when the whole path or its first segment is a tracked name; a path whose parent
#                 is in skip_parent_types_for_type_ref is skipped
# macro_argument_types: AST node types of the arguments of a macro (Rust), whose paths are
#                 read from loose tokens
# self_names / self_types: identifier texts / AST node types of the object a member is written on
#                 (self.name, this.name); the member after it is a usage of the file's own name
# typed_alias_parent_types: AST node types of a variable declaration with a type; a variable
#                 declared with a tracked type is tracked under the type name
# typed_alias_name_field_dict: typed_alias_parent_types node type -> field holding its
#                 variable ("" for its first named child); its type is in the field "type"
# typed_alias_value_dict: AST node type that gives a variable a value -> (field of the
#                 variable, field of the value; "" for its first named child)
# typed_alias_new_dict: AST node type of a value that makes an object -> field naming its
#                 type; a variable given such a value of a tracked type inside a function
#                 is tracked under the type name from that line on
# typed_alias_class_only: True when typed_alias_new_dict also matches a plain call, so the
#                 type has to be a definition in CLASS_DEFINITION_TYPE_SET (Python)
#
# Names bound inside a function are not usages of a name written outside it; a name an
# import statement binds there (const m = require("./m")) stays a name of the import:
# scope_types:    AST node types that open a scope (functions, lambdas)
# scope_body_dict: scope node type -> field of the scope node whose nodes the names of the
#                 scope are bound for; a scope type without an entry binds them for every
#                 node of the scope (Python: a default value and an annotation of a
#                 function are read outside it)
# scope_binding_dict: scope node type -> field of the scope node holding a pattern it binds
# local_binding_dict: node type -> field holding the pattern the node binds in the scope around
#                 it; "" when the named children of the node are the patterns
# pattern_types:  AST node types of a pattern whose named children are patterns
# pattern_field_dict: pattern node type -> the one field of it that is a pattern ("" for
#                 its first named child)
# pattern_reference_types: definition types a name written in a pattern refers to instead
#                 of binding it (Rust: a constant, a static, a struct without fields)
# pattern_variant_types: definition types whose members such a name refers to as well
#                 (Rust: the variants of an enum)
# opaque_types:   AST node types inside a scope whose inner nodes bind nothing in it
# unbind_types:   AST node types naming names that are not local (Python global / nonlocal)
# call_ignores_local: True when the name of a called function is never a local variable (Java)
_PYTHON_USAGE_NODE_TYPE_DICT = {
    "call_types": {"call"},
    "attribute_types": {"attribute"},
    "skip_parent_types": {
        "attribute", "call",
        "import_statement", "import_from_statement",
        "dotted_name", "aliased_import",
        "function_definition", "class_definition",
        "parameter", "parameters", "lambda_parameters", "typed_parameter",
        "list_splat_pattern", "dictionary_splat_pattern",
    },
    "skip_name_field_types": {
        "default_parameter", "typed_default_parameter", "keyword_argument",
    },
    "skip_parent_types_for_type_ref": set(),
    "self_names": {"self", "cls"},
    "typed_alias_parent_types": {"typed_parameter", "typed_default_parameter", "assignment"},
    "typed_alias_name_field_dict": {
        "typed_parameter": "", "typed_default_parameter": "name", "assignment": "left",
    },
    "typed_alias_value_dict": {"assignment": ("left", "right"), "as_pattern": ("alias", "")},
    "typed_alias_new_dict": {"call": "function"},
    "typed_alias_class_only": True,
    "scope_types": {
        "function_definition", "lambda",
        "list_comprehension", "set_comprehension", "dictionary_comprehension",
        "generator_expression",
    },
    "scope_body_dict": {"function_definition": "body", "lambda": "body"},
    "local_binding_dict": {
        "parameters": "",
        "lambda_parameters": "",
        "assignment": "left",
        "augmented_assignment": "left",
        "named_expression": "name",
        "for_statement": "left",
        "for_in_clause": "left",
        "as_pattern": "alias",
        "function_definition": "name",
        "class_definition": "name",
    },
    "pattern_types": {
        "pattern_list", "tuple_pattern", "list_pattern", "list_splat_pattern",
        "dictionary_splat_pattern", "typed_parameter", "as_pattern_target",
    },
    "pattern_field_dict": {
        "default_parameter": "name",
        "typed_default_parameter": "name",
    },
    "opaque_types": {"class_definition"},
    "unbind_types": {"global_statement", "nonlocal_statement"},
}

_JAVA_USAGE_NODE_TYPE_DICT = {
    "call_types": {"method_invocation"},
    "attribute_types": {"field_access"},
    "skip_parent_types": {
        "method_invocation", "field_access",
        "import_declaration", "scoped_identifier",
        "class_declaration", "method_declaration",
        "interface_declaration", "constructor_declaration",
        "formal_parameter", "spread_parameter",
    },
    "skip_parent_types_for_type_ref": {
        "scoped_identifier", "import_declaration",
    },
    "typed_alias_parent_types": {
        "field_declaration", "local_variable_declaration", "formal_parameter",
    },
    "self_types": {"this"},
    "call_ignores_local": True,
    "scope_types": {
        "method_declaration", "constructor_declaration", "compact_constructor_declaration",
        "lambda_expression",
    },
    "scope_binding_dict": {"lambda_expression": "parameters"},
    "local_binding_dict": {
        "formal_parameter": "name",
        "spread_parameter": "",
        "local_variable_declaration": "declarator",
        "enhanced_for_statement": "name",
        "catch_formal_parameter": "name",
        "resource": "name",
    },
    "pattern_types": {"inferred_parameters"},
    "pattern_field_dict": {"variable_declarator": "name"},
}

_JS_USAGE_NODE_TYPE_DICT = {
    "call_types": {"call_expression"},
    "attribute_types": {"member_expression"},
    "skip_parent_types": {
        "call_expression", "member_expression",
        "import_statement", "import_clause", "import_specifier", "namespace_import",
        "namespace_export",
        "function_declaration", "class_declaration", "method_definition",
        "formal_parameters", "pair_pattern",
    },
    "skip_name_field_types": {"variable_declarator"},
    "skip_parent_types_for_type_ref": {
        "import_statement", "import_specifier", "namespace_import",
    },
    "identifier_types": {"shorthand_property_identifier"},
    "self_types": {"this"},
    "typed_alias_parent_types": {"variable_declarator", "required_parameter", "optional_parameter"},
    "typed_alias_name_field_dict": {
        "variable_declarator": "name", "required_parameter": "pattern",
        "optional_parameter": "pattern",
    },
    "typed_alias_value_dict": {"variable_declarator": ("name", "value")},
    "typed_alias_new_dict": {"new_expression": "constructor"},
    "scope_types": {
        "function_declaration", "generator_function_declaration", "function_expression",
        "generator_function", "arrow_function", "method_definition",
        "function_signature", "method_signature", "abstract_method_signature",
        "call_signature", "construct_signature", "function_type", "constructor_type",
    },
    "scope_binding_dict": {"arrow_function": "parameter"},
    "local_binding_dict": {
        "formal_parameters": "",
        "variable_declarator": "name",
        "function_declaration": "name",
        "generator_function_declaration": "name",
        "class_declaration": "name",
        "catch_clause": "parameter",
        "for_in_statement": "left",
    },
    "pattern_types": {"object_pattern", "array_pattern", "rest_pattern"},
    "pattern_field_dict": {
        "assignment_pattern": "left",
        "object_assignment_pattern": "left",
        "pair_pattern": "value",
        "required_parameter": "pattern",
        "optional_parameter": "pattern",
    },
}

_C_USAGE_NODE_TYPE_DICT = {
    "call_types": {"call_expression"},
    "attribute_types": {"field_expression"},
    "skip_parent_types": {
        "call_expression", "field_expression",
        "preproc_include",
        "function_declarator", "function_definition",
        "struct_specifier", "parameter_declaration",
        "qualified_identifier",
    },
    "skip_parent_types_for_type_ref": {
        "preproc_include", "qualified_identifier",
    },
    "typed_alias_parent_types": {
        "declaration", "parameter_declaration",
    },
    "self_types": {"this"},
    "scope_types": {"function_definition", "lambda_expression"},
    "local_binding_dict": {
        "parameter_declaration": "declarator",
        "optional_parameter_declaration": "declarator",
        "declaration": "declarator",
        "for_range_loop": "declarator",
        "condition_declaration": "declarator",
    },
    "pattern_types": {
        "structured_binding_declarator", "reference_declarator", "parenthesized_declarator",
    },
    "pattern_field_dict": {
        "init_declarator": "declarator",
        "pointer_declarator": "declarator",
        "array_declarator": "declarator",
        "function_declarator": "declarator",
    },
}

_KOTLIN_USAGE_NODE_TYPE_DICT = {
    "call_types": {"call_expression"},
    "attribute_types": {"navigation_expression"},
    "skip_parent_types": {
        "call_expression", "navigation_expression",
        "import", "qualified_identifier",
        "class_declaration", "function_declaration",
        "object_declaration",
        "parameter", "package_header",
    },
    "skip_parent_types_for_type_ref": {
        "import", "qualified_identifier", "package_header",
    },
    "typed_alias_parent_types": {
        "property_declaration", "parameter",
    },
    "self_types": {"this_expression"},
    "scope_types": {
        "function_declaration", "lambda_literal", "anonymous_function", "secondary_constructor",
    },
    "local_binding_dict": {
        "parameter": "",
        "variable_declaration": "",
        "catch_block": "",
    },
}

_RUST_USAGE_NODE_TYPE_DICT = {
    "call_types": {"call_expression"},
    "attribute_types": {"field_expression"},
    "path_types": {"scoped_identifier", "scoped_type_identifier"},
    "macro_argument_types": {"token_tree"},
    "skip_parent_types": {
        "call_expression", "field_expression",
        "scoped_identifier", "scoped_type_identifier",
        "use_declaration", "use_list", "scoped_use_list", "use_as_clause", "use_wildcard",
        "extern_crate_declaration", "mod_item",
        "function_item", "function_signature_item", "macro_definition",
        "parameter",
    },
    "skip_name_field_types": {"const_item", "static_item", "enum_variant"},
    "skip_parent_types_for_type_ref": {
        "scoped_type_identifier",
        "use_declaration", "use_list", "scoped_use_list", "use_as_clause", "use_wildcard",
        "visibility_modifier",
    },
    "self_types": {"self"},
    "scope_types": {
        "function_item", "closure_expression", "match_arm", "if_expression", "while_expression",
    },
    "scope_binding_dict": {"match_arm": "pattern"},
    "local_binding_dict": {
        "parameter": "pattern",
        "let_declaration": "pattern",
        "let_condition": "pattern",
        "for_expression": "pattern",
        "closure_parameters": "",
    },
    "pattern_types": {
        "tuple_pattern", "slice_pattern", "reference_pattern", "mut_pattern", "ref_pattern",
        "tuple_struct_pattern", "struct_pattern", "field_pattern", "or_pattern",
        "captured_pattern",
    },
    "pattern_field_dict": {"parameter": "pattern", "match_pattern": ""},
    "pattern_reference_types": {"const_item", "static_item", "struct_item"},
    "pattern_variant_types": {"enum_item"},
}

_SQL_USAGE_NODE_TYPE_DICT = {
    "call_types": set(),
    "attribute_types": set(),
    "skip_parent_types": set(),
    "identifier_parent_types": {"object_reference"},
}


# Language registry
#
# LangConfig bundles all settings needed for a single language (extension),
# and _LANG_REGISTRY manages them centrally.
# To add a new language, simply add one entry to _LANG_REGISTRY.
#
# Public mapping dictionaries (EXT_TO_LANGUAGE_DICT, EXT_TO_DEFINITION_DICT, EXT_TO_IMPORT_QUERY_DICT,
# EXT_TO_USAGE_NODE_TYPE_DICT, EXT_TO_IMPORT_RESOLVE_DICT, EXT_TO_IMPLICIT_VISIBILITY_DICT,
# EXT_TO_REFERENCE_KIND_DICT) are auto-generated from the registry.
_JS_TS_EXT_LIST = [".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs"]
_C_CPP_EXT_LIST = [".h", ".c", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"]

# Extension written in a JS/TS import -> extensions of the source files it can stand for
_JS_SOURCE_EXT_DICT = {
    ".js":  [".ts", ".tsx", ".jsx"],
    ".jsx": [".tsx"],
    ".mjs": [".mts"],
    ".cjs": [".cts"],
}

_JS_TS_RESOLVE_DICT = {
    "separator": "/",
    "bind": "export",
    "index_ext_list": _JS_TS_EXT_LIST,
    "alt_ext_list": _JS_TS_EXT_LIST,
    "source_ext_dict": _JS_SOURCE_EXT_DICT,
    "path_config_name_list": ["tsconfig.json", "jsconfig.json"],
    "package_file_name": "package.json",
    "alias_config_name_list": [
        "vite.config.ts", "vite.config.mts", "vite.config.js", "vite.config.mjs",
        "vite.config.cjs", "webpack.config.js", "webpack.config.cjs", "webpack.config.mjs",
        "webpack.config.ts",
    ],
}

_C_CPP_RESOLVE_DICT = {
    "separator": "/",
    "bind": "include",
    "alt_ext_list": _C_CPP_EXT_LIST,
    "try_bare_path": True,
    "try_current_dir": True,
    "min_path_end_part": 1,
}


@dataclass(frozen=True)
class LangConfig:
    """Data class bundling all settings associated with a single language (extension).

    language:         tree-sitter Language object
    definition_dict:  Mapping of AST node type -> name node type (for definition extraction)
    import_query:     tree-sitter import extraction query string (S-expression)
    usage_node_type_dict: AST node type settings for usage tracking
    import_resolve_dict:  Module resolution settings. A dict with the following keys:
                        separator      - Module name delimiter ("." or "/")
                        bind           - What an import statement binds in the file:
                                         "module"  - a module, or names of one (Python)
                                         "export"  - names a file exports (JS/TS)
                                         "package" - names of a package (Java / Kotlin)
                                         "include" - every name of the file (C/C++)
                                         "path"    - the item a path names (Rust)
                        try_init       - Whether to look for __init__.py as a package (Python)
                        index_ext_list - List of extensions to try as index files (JS/TS)
                        alt_ext_list   - List of alternative extensions
                        source_ext_dict - Extension written in an import -> extensions of the
                                         files it can stand for (JS/TS: ".js" -> ".ts", ".tsx")
                        try_bare_path  - Whether to try paths without extensions (C/C++)
                        try_current_dir - Whether to also try relative paths from the current directory (C/C++)
                        package_file   - File name that makes a directory a package; a file in
                                         such a directory does not get try_current_dir (Python)
                        path_config_name_list - File names of the config files whose "baseUrl"
                                         and "paths" a module that is not relative is
                                         resolved through (JS/TS)
                        package_file_name - File name of the package file whose "imports"
                                         and "name" a module that is not relative is
                                         resolved through (JS/TS)
                        alias_config_name_list - File names of the bundler config files whose
                                         aliases such a module is resolved through (JS/TS)
                        min_path_end_part - Number of parts a module needs to be matched against
                                         the end of the project file paths; absent: never
                        module_tree    - Whether to resolve paths through the tree of mod declarations (Rust)
                        name_index     - Whether to resolve names through the file names and
                                         program names of the project (COBOL)
    implicit_visibility: Which files' definitions can be referenced without an import statement:
                        "package" - files that declare the same package; for a file without
                                    a package statement, the files of its directory that
                                    have none either (Java / Kotlin)
                        "project" - every file of the same extension (SQL)
                        None      - none
    ignore_ext_case:  Whether the extension is matched without regard to upper and lower
                      case (COBOL, BMS, R)
    reference_kind:   How the references of a file are resolved to definitions:
                        "import" - through its import statements and implicit_visibility
                        "cobol"  - COBOL names with OF / IN qualification (COBOL, BMS)
                        "csharp" - through namespaces and using directives (C#)
                        "r"      - through the names an R file sees (R)
    """
    language: Language
    definition_dict: dict[str, str]
    import_query: str | None = None
    usage_node_type_dict: dict | None = None
    import_resolve_dict: dict | None = None
    implicit_visibility: str | None = None
    ignore_ext_case: bool = False
    reference_kind: str = "import"


_COBOL_LANG_CONFIG = LangConfig(
    language=tspack.get_language("cobol"),
    definition_dict=COBOL_DEFINITION_DICT,
    import_resolve_dict={"separator": " ", "name_index": True},
    ignore_ext_case=True,
    reference_kind="cobol",
)

# A BMS source is read without a grammar; the COBOL grammar only fills the field
_BMS_LANG_CONFIG = LangConfig(
    language=tspack.get_language("cobol"),
    definition_dict=BMS_DEFINITION_DICT,
    ignore_ext_case=True,
    reference_kind="cobol",
)

# R scripts, and R Markdown / Quarto files read through their R code chunks
_R_LANG_CONFIG = LangConfig(
    language=tspack.get_language("r"),
    definition_dict=R_DEFINITION_DICT,
    ignore_ext_case=True,
    reference_kind="r",
)

_LANG_REGISTRY: dict[str, LangConfig] = {
    "py": LangConfig(
        language=Language(tspython.language()),
        definition_dict=PYTHON_DEFINITION_DICT,
        import_query=_PYTHON_IMPORT_QUERY,
        usage_node_type_dict=_PYTHON_USAGE_NODE_TYPE_DICT,
        import_resolve_dict={
            "separator": ".",
            "bind": "module",
            "try_init": True,
            "try_current_dir": True,
            "package_file": "__init__.py",
            "min_path_end_part": 2,
        },
    ),
    "java": LangConfig(
        language=Language(tsjava.language()),
        definition_dict=JAVA_DEFINITION_DICT,
        import_query=_JAVA_IMPORT_QUERY,
        usage_node_type_dict=_JAVA_USAGE_NODE_TYPE_DICT,
        import_resolve_dict={"separator": ".", "bind": "package"},
        implicit_visibility="package",
    ),
    "cpp": LangConfig(
        language=Language(tscpp.language()),
        definition_dict=CPP_DEFINITION_DICT,
        import_query=_C_IMPORT_QUERY,
        usage_node_type_dict=_C_USAGE_NODE_TYPE_DICT,
        import_resolve_dict=_C_CPP_RESOLVE_DICT,
    ),
    "c": LangConfig(
        language=Language(tsc.language()),
        definition_dict=C_DEFINITION_DICT,
        import_query=_C_IMPORT_QUERY,
        usage_node_type_dict=_C_USAGE_NODE_TYPE_DICT,
        import_resolve_dict=_C_CPP_RESOLVE_DICT,
    ),
    "kt": LangConfig(
        language=Language(tskotlin.language()),
        definition_dict=KOTLIN_DEFINITION_DICT,
        import_query=_KOTLIN_IMPORT_QUERY,
        usage_node_type_dict=_KOTLIN_USAGE_NODE_TYPE_DICT,
        import_resolve_dict={"separator": ".", "bind": "package"},
        implicit_visibility="package",
    ),
    "js": LangConfig(
        language=Language(tsjavascript.language()),
        definition_dict=JS_DEFINITION_DICT,
        import_query=_JS_IMPORT_QUERY,
        usage_node_type_dict=_JS_USAGE_NODE_TYPE_DICT,
        import_resolve_dict=_JS_TS_RESOLVE_DICT,
    ),
    "ts": LangConfig(
        language=Language(tstypescript.language_typescript()),
        definition_dict=TS_DEFINITION_DICT,
        import_query=_JS_IMPORT_QUERY,
        usage_node_type_dict=_JS_USAGE_NODE_TYPE_DICT,
        import_resolve_dict=_JS_TS_RESOLVE_DICT,
    ),
    "tsx": LangConfig(
        language=Language(tstypescript.language_tsx()),
        definition_dict=TS_DEFINITION_DICT,
        import_query=_JS_IMPORT_QUERY,
        usage_node_type_dict=_JS_USAGE_NODE_TYPE_DICT,
        import_resolve_dict=_JS_TS_RESOLVE_DICT,
    ),
    "rs": LangConfig(
        language=Language(tsrust.language()),
        definition_dict=RUST_DEFINITION_DICT,
        import_query=_RUST_IMPORT_QUERY,
        usage_node_type_dict=_RUST_USAGE_NODE_TYPE_DICT,
        import_resolve_dict={"separator": "::", "bind": "path", "module_tree": True},
    ),
    "cs": LangConfig(
        language=Language(tscsharp.language()),
        definition_dict=CSHARP_DEFINITION_DICT,
        reference_kind="csharp",
    ),
    "sql": LangConfig(
        language=Language(tssql.language()),
        definition_dict=SQL_DEFINITION_DICT,
        usage_node_type_dict=_SQL_USAGE_NODE_TYPE_DICT,
        implicit_visibility="project",
    ),
    "cbl": _COBOL_LANG_CONFIG,
    "cob": _COBOL_LANG_CONFIG,
    "cpy": _COBOL_LANG_CONFIG,
    "bms": _BMS_LANG_CONFIG,
    "r": _R_LANG_CONFIG,
    "rmd": _R_LANG_CONFIG,
    "qmd": _R_LANG_CONFIG,
}


# Extension aliases and auto-generation of public mapping dictionaries
#
# _EXT_ALIAS_DICT defines a mapping of extensions that share the same language settings.
# When generating public dictionaries from _LANG_REGISTRY, _expand_ext_aliases()
# automatically adds alias extensions (h, cc, kts, jsx, mts, ...).
_EXT_ALIAS_DICT: dict[str, str] = {
    "h":   "cpp",
    "cc":  "cpp",
    "cxx": "cpp",
    "hpp": "cpp",
    "hh":  "cpp",
    "hxx": "cpp",
    "kts": "kt",
    "jsx": "js",
    "mjs": "js",
    "cjs": "js",
    "mts": "ts",
    "cts": "ts",
}


def _expand_ext_aliases(base_dict: dict) -> dict:
    """Return a new dictionary with alias extension entries added based on _EXT_ALIAS_DICT.

    For example, if _EXT_ALIAS_DICT = {"h": "cpp"} and base_dict contains "cpp",
    the "h" key is also set to the same value.

    Args:
        base_dict: A settings dictionary keyed by canonical extensions.

    Returns:
        A new dictionary with alias extensions added.
    """
    expanded_dict = dict(base_dict)
    for alias, canonical in _EXT_ALIAS_DICT.items():
        if alias not in expanded_dict and canonical in expanded_dict:
            expanded_dict[alias] = expanded_dict[canonical]
    return expanded_dict


# Extension -> tree-sitter Language object
EXT_TO_LANGUAGE_DICT: dict[str, Language] = _expand_ext_aliases(
    {ext: lang_config.language for ext, lang_config in _LANG_REGISTRY.items()}
)

# Extension -> definition node mapping dictionary
EXT_TO_DEFINITION_DICT: dict[str, dict[str, str]] = _expand_ext_aliases(
    {ext: lang_config.definition_dict for ext, lang_config in _LANG_REGISTRY.items()}
)


# Extensions of the COBOL files (lower case)
COBOL_EXT_SET: set[str] = {
    ext for ext, definition_dict in EXT_TO_DEFINITION_DICT.items()
    if definition_dict is COBOL_DEFINITION_DICT
}

# Extensions of the C and C++ files
C_FAMILY_EXT_SET: set[str] = {
    ext for ext, lang_config in _LANG_REGISTRY.items()
    if (lang_config.import_resolve_dict or {}).get("bind") == "include"
} | {
    alias for alias, canonical in _EXT_ALIAS_DICT.items()
    if (_LANG_REGISTRY[canonical].import_resolve_dict or {}).get("bind") == "include"
}

# Extensions of the C# files
CSHARP_EXT_SET: set[str] = {
    ext for ext, definition_dict in EXT_TO_DEFINITION_DICT.items()
    if definition_dict is CSHARP_DEFINITION_DICT
}

# Extensions of the R files: scripts, R Markdown and Quarto (lower case)
R_EXT_SET: set[str] = {
    ext for ext, definition_dict in EXT_TO_DEFINITION_DICT.items()
    if definition_dict is R_DEFINITION_DICT
}

# Extensions of the R files whose code is in the R chunks of a document (lower case)
R_MARKDOWN_EXT_SET: set[str] = {"rmd", "qmd"}

# Extensions of the BMS sources (lower case)
BMS_EXT_SET: set[str] = {
    ext for ext, definition_dict in EXT_TO_DEFINITION_DICT.items()
    if definition_dict is BMS_DEFINITION_DICT
}

# Extensions matched without regard to upper and lower case (lower case)
_IGNORE_CASE_EXT_SET: set[str] = {
    ext for ext, lang_config in _LANG_REGISTRY.items() if lang_config.ignore_ext_case
}

# Absolute path of a file whose language comes from the files that name it, not from its
# extension -> the extension whose language settings it is analyzed with.
# Filled by set_copy_target_ext() (COBOL: a copybook named by a COPY statement)
_copy_target_ext_dict: dict[str, str] = {}


def set_copy_target_ext(project_dir: str, file_ext_dict: dict[str, str]) -> None:
    """Record the files of a project whose language comes from the files that name them.

    The files recorded before for the same project are forgotten.

    Args:
        project_dir: Root directory of the project.
        file_ext_dict: {path relative to project_dir: extension whose language settings
            the file is analyzed with}.
    """
    project_prefix = os.path.join(os.path.abspath(project_dir), "")
    for path in [path for path in _copy_target_ext_dict if path.startswith(project_prefix)]:
        del _copy_target_ext_dict[path]
    for file_rel, ext in file_ext_dict.items():
        _copy_target_ext_dict[os.path.abspath(os.path.join(project_dir, file_rel))] = ext


# Absolute path of a file whose extension has a language but which is analyzed without
# one. Filled by set_no_language_file() (R Markdown / Quarto: a file without an R code chunk)
_no_language_path_set: set[str] = set()


def set_no_language_file(project_dir: str, file_rel_list: list[str]) -> None:
    """Record the files of a project that are analyzed without a language whatever their extension.

    The files recorded before for the same project are forgotten.

    Args:
        project_dir: Root directory of the project.
        file_rel_list: Paths relative to project_dir.
    """
    project_prefix = os.path.join(os.path.abspath(project_dir), "")
    for path in [path for path in _no_language_path_set if path.startswith(project_prefix)]:
        _no_language_path_set.discard(path)
    for file_rel in file_rel_list:
        _no_language_path_set.add(os.path.abspath(os.path.join(project_dir, file_rel)))


def language_ext(path: str) -> str:
    """Return the extension whose language settings a file is analyzed with.

    A file recorded by set_copy_target_ext() gets the extension recorded for it, and a
    file recorded by set_no_language_file() none. Every other file gets its own
    extension when it is a key of EXT_TO_DEFINITION_DICT; the extensions of the
    languages with ignore_ext_case are matched in any case.

    Examples:
        "src/app.py"          -> "py"
        "src/MAIN.Cbl"        -> "cbl"
        "R/utils.R"           -> "r"
        "config.yaml"         -> ""
        "dcl/DCLCUST.dcl"     -> "cpy" (when a COPY statement names it)
        "docs/notes.qmd"      -> "" (when it has no R code chunk)

    Args:
        path: A file path. A relative path is taken from the current directory when
            it is looked up among the recorded files.

    Returns:
        The extension (without the dot, a key of EXT_TO_DEFINITION_DICT), or "" for a
        file without a language.
    """
    path_abs = os.path.abspath(path)
    copy_target_ext = _copy_target_ext_dict.get(path_abs)
    if copy_target_ext:
        return copy_target_ext
    if path_abs in _no_language_path_set:
        return ""
    ext = os.path.splitext(path)[1].lstrip(".")
    if ext in EXT_TO_DEFINITION_DICT:
        return ext
    if ext.lower() in _IGNORE_CASE_EXT_SET:
        return ext.lower()
    return ""


def has_language(path: str) -> bool:
    """Return whether a file is analyzed with a language of the registry.

    Only such files get definitions, dependencies and design documents; every other
    text file is carried through the analysis with those left empty.

    Examples:
        "src/app.py"    -> True
        "config.yaml"   -> False
        "Makefile"      -> False

    Args:
        path: A file path. A file recorded by set_copy_target_ext() is known by its
            absolute path.

    Returns:
        True when language_ext() gives an extension.
    """
    return language_ext(path) != ""


# Extension -> import extraction query
EXT_TO_IMPORT_QUERY_DICT: dict[str, str | None] = _expand_ext_aliases(
    {ext: lang_config.import_query for ext, lang_config in _LANG_REGISTRY.items()}
)

# Extension -> AST node type settings for usage tracking
EXT_TO_USAGE_NODE_TYPE_DICT: dict[str, dict | None] = _expand_ext_aliases(
    {ext: lang_config.usage_node_type_dict for ext, lang_config in _LANG_REGISTRY.items()}
)

# Pattern node types of every language together (pattern_types, pattern_field_dict):
# what the names a declaration defines are read with
PATTERN_TYPE_SET: set[str] = set().union(*(
    lang_config.usage_node_type_dict.get("pattern_types", set())
    for lang_config in _LANG_REGISTRY.values() if lang_config.usage_node_type_dict
))
PATTERN_FIELD_DICT: dict[str, str] = {
    node_type: field_name
    for lang_config in _LANG_REGISTRY.values() if lang_config.usage_node_type_dict
    for node_type, field_name in lang_config.usage_node_type_dict.get("pattern_field_dict", {}).items()
}

# Extension -> import path resolution settings
EXT_TO_IMPORT_RESOLVE_DICT: dict[str, dict] = _expand_ext_aliases(
    {ext: lang_config.import_resolve_dict for ext, lang_config in _LANG_REGISTRY.items()
     if lang_config.import_resolve_dict is not None}
)

# Extension -> how the references of a file are resolved ("import" / "cobol" / "csharp" / "r")
EXT_TO_REFERENCE_KIND_DICT: dict[str, str] = _expand_ext_aliases(
    {ext: lang_config.reference_kind for ext, lang_config in _LANG_REGISTRY.items()}
)

# Extension -> scope of the files whose definitions can be referenced without an import ("package" / "project")
EXT_TO_IMPLICIT_VISIBILITY_DICT: dict[str, str] = _expand_ext_aliases(
    {ext: lang_config.implicit_visibility for ext, lang_config in _LANG_REGISTRY.items()
     if lang_config.implicit_visibility}
)


# Source root prefixes for Maven/Gradle standard layouts and Python src-layout.
# Used to resolve import statements when source files are nested under these directories.
SOURCE_ROOT_PATTERN_LIST: list[str] = [
    "src/main/java/",
    "src/test/java/",
    "src/main/kotlin/",
    "src/test/kotlin/",
    "src/main/scala/",
    "src/test/scala/",
    "src/",
]
