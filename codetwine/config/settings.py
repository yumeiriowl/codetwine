import os
from dataclasses import dataclass
from dotenv import load_dotenv
from tree_sitter import Language
import tree_sitter_c as tsc
import tree_sitter_cpp as tscpp
import tree_sitter_java as tsjava
import tree_sitter_javascript as tsjavascript
import tree_sitter_kotlin as tskotlin
import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript


load_dotenv()

_REQUIRED = object()


def get_config_value(key: str, default=_REQUIRED, var_type: type = str):
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
DOC_MAX_TOKENS = get_config_value("DOC_MAX_TOKENS", default=8192, var_type=int)

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
MAX_RETRIES = get_config_value("MAX_RETRIES", default=3, var_type=int)
RETRY_WAIT = get_config_value("RETRY_WAIT", default=2, var_type=int)

# == Analysis settings =============================================
ENABLE_LLM_DOC = get_config_value("ENABLE_LLM_DOC", default=True, var_type=bool)
SUMMARY_MAX_CHARS: int = get_config_value("SUMMARY_MAX_CHARS", default=600, var_type=int)
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
#   When the name node is nested two or more levels deep, a "__sentinel__" value is used.
#   _extract_name in definitions.py checks the sentinel value and dispatches
#   to a dedicated extraction function.
PYTHON_DEFINITION_DICT = {
    "function_definition": "identifier",
    "class_definition": "identifier",
    "decorated_definition": "identifier",
    "expression_statement": "__assignment__",
}

JAVA_DEFINITION_DICT = {
    "class_declaration": "identifier",
    "method_declaration": "identifier",
    "interface_declaration": "identifier",
    "constructor_declaration": "identifier",
    "enum_declaration": "identifier",
}

CPP_DEFINITION_DICT = {
    "class_specifier": "type_identifier",
    "struct_specifier": "type_identifier",
    "function_declarator": "identifier",
    "function_definition": "__function_declarator__",
    "namespace_definition": "namespace_identifier",
    "declaration": "__init_declarator__",
    "alias_declaration": "type_identifier",
    "enum_specifier": "type_identifier",
    "preproc_def": "identifier",
}

C_DEFINITION_DICT = {
    "function_declarator": "identifier",
    "function_definition": "__function_declarator__",
    "struct_specifier": "type_identifier",
    "declaration": "__init_declarator__",
    "preproc_def": "identifier",
    "type_definition": "type_identifier",
    "enum_specifier": "type_identifier",
}

KOTLIN_DEFINITION_DICT = {
    "class_declaration": "identifier",
    "function_declaration": "identifier",
    "object_declaration": "identifier",
}

JS_DEFINITION_DICT = {
    "function_declaration": "identifier",
    "method_definition": "identifier",
    "class_declaration": "identifier",
    "lexical_declaration": "__variable_declarator__",
    "variable_declaration": "__variable_declarator__",
}

TS_DEFINITION_DICT = {
    "function_declaration": "identifier",
    "method_definition": "identifier",
    "class_declaration": "type_identifier",
    "interface_declaration": "type_identifier",
    "lexical_declaration": "__variable_declarator__",
    "variable_declaration": "__variable_declarator__",
    "type_alias_declaration": "type_identifier",
    "enum_declaration": "identifier",
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
_PYTHON_IMPORT_QUERY = """
(import_statement
  name: (dotted_name) @module) @import_node

(import_statement
  name: (aliased_import
    name: (dotted_name) @module)) @import_node

(import_from_statement
  module_name: (_) @module
  name: (_) @name) @import_node
"""

# JavaScript / TypeScript import queries
# - import X from 'module': @module only
# - import { X, Y } from 'module': @module and @name
# - import * as X from 'module': @module only
# - export { X } from 'module': @module and @name (re-export)
# - export * from 'module': @module only (re-export)
# - require('module'): @module only (CommonJS)
# - const { X } = require('module'): @module and @name (CommonJS destructuring)
_JS_IMPORT_QUERY = """
(import_statement
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
  (export_clause
    (export_specifier
      name: (identifier) @name))
  source: (string) @module) @import_node

(call_expression
  function: (identifier) @_require_func
  arguments: (arguments (string) @module)) @import_node

(variable_declarator
  name: (object_pattern
    (shorthand_property_identifier_pattern) @name)
  value: (call_expression
    function: (identifier) @_require_func
    arguments: (arguments (string) @module))) @import_node
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
_PYTHON_USAGE_NODE_TYPES = {
    "call_types": {"call"},
    "attribute_types": {"attribute"},
    "skip_parent_types": {
        "attribute", "call",
        "import_statement", "import_from_statement",
        "dotted_name", "aliased_import",
        "function_definition", "class_definition",
        "parameter", "parameters", "typed_parameter",
        "list_splat_pattern", "dictionary_splat_pattern",
    },
    "skip_name_field_types": {
        "default_parameter", "typed_default_parameter", "keyword_argument",
    },
    "skip_parent_types_for_type_ref": set(),
}

_JAVA_USAGE_NODE_TYPES = {
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
}

_JS_USAGE_NODE_TYPES = {
    "call_types": {"call_expression"},
    "attribute_types": {"member_expression"},
    "skip_parent_types": {
        "call_expression", "member_expression",
        "import_statement", "import_specifier", "namespace_import",
        "function_declaration", "class_declaration", "method_definition",
        "formal_parameters",
    },
    "skip_parent_types_for_type_ref": {
        "import_statement", "import_specifier", "namespace_import",
    },
}

_C_USAGE_NODE_TYPES = {
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
}

_KOTLIN_USAGE_NODE_TYPES = {
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
}


# Language registry
#
# LangConfig bundles all settings needed for a single language (extension),
# and _LANG_REGISTRY manages them centrally.
# To add a new language, simply add one entry to _LANG_REGISTRY.
#
# Public mapping dictionaries (TREE_SITTER_LANGUAGES, DEFINITION_DICTS, IMPORT_QUERIES,
# USAGE_NODE_TYPES, IMPORT_RESOLVE_CONFIG) are auto-generated from the registry.
_JS_TS_EXT_LIST = [".ts", ".tsx", ".js", ".jsx"]
_C_CPP_EXT_LIST = [".h", ".c", ".cpp"]


@dataclass(frozen=True)
class LangConfig:
    """Data class bundling all settings associated with a single language (extension).

    language:         tree-sitter Language object
    definition_dict:  Mapping of AST node type -> name node type (for definition extraction)
    import_query:     tree-sitter import extraction query string (S-expression)
    usage_node_types: AST node type settings for usage tracking
    import_resolve:   Module resolution settings. A dict with the following keys:
                        separator      - Module name delimiter ("." or "/")
                        try_init       - Whether to look for __init__.py as a package (Python)
                        index_ext_list - List of extensions to try as index files (JS/TS)
                        alt_ext_list   - List of alternative extensions
                        try_bare_path  - Whether to try paths without extensions (C/C++)
                        try_current_dir - Whether to also try relative paths from the current directory (C/C++)
    """
    language: Language
    definition_dict: dict[str, str]
    import_query: str | None = None
    usage_node_types: dict | None = None
    import_resolve: dict | None = None
    same_package_visible: bool = False


_LANG_REGISTRY: dict[str, LangConfig] = {
    "py": LangConfig(
        language=Language(tspython.language()),
        definition_dict=PYTHON_DEFINITION_DICT,
        import_query=_PYTHON_IMPORT_QUERY,
        usage_node_types=_PYTHON_USAGE_NODE_TYPES,
        import_resolve={"separator": ".", "try_init": True},
    ),
    "java": LangConfig(
        language=Language(tsjava.language()),
        definition_dict=JAVA_DEFINITION_DICT,
        import_query=_JAVA_IMPORT_QUERY,
        usage_node_types=_JAVA_USAGE_NODE_TYPES,
        import_resolve={"separator": "."},
        same_package_visible=True,
    ),
    "cpp": LangConfig(
        language=Language(tscpp.language()),
        definition_dict=CPP_DEFINITION_DICT,
        import_query=_C_IMPORT_QUERY,
        usage_node_types=_C_USAGE_NODE_TYPES,
        import_resolve={
            "separator": "/",
            "alt_ext_list": _C_CPP_EXT_LIST,
            "try_bare_path": True,
            "try_current_dir": True,
        },
    ),
    "c": LangConfig(
        language=Language(tsc.language()),
        definition_dict=C_DEFINITION_DICT,
        import_query=_C_IMPORT_QUERY,
        usage_node_types=_C_USAGE_NODE_TYPES,
        import_resolve={
            "separator": "/",
            "alt_ext_list": _C_CPP_EXT_LIST,
            "try_bare_path": True,
            "try_current_dir": True,
        },
    ),
    "kt": LangConfig(
        language=Language(tskotlin.language()),
        definition_dict=KOTLIN_DEFINITION_DICT,
        import_query=_KOTLIN_IMPORT_QUERY,
        usage_node_types=_KOTLIN_USAGE_NODE_TYPES,
        import_resolve={"separator": "."},
        same_package_visible=True,
    ),
    "js": LangConfig(
        language=Language(tsjavascript.language()),
        definition_dict=JS_DEFINITION_DICT,
        import_query=_JS_IMPORT_QUERY,
        usage_node_types=_JS_USAGE_NODE_TYPES,
        import_resolve={
            "separator": "/",
            "index_ext_list": _JS_TS_EXT_LIST,
            "alt_ext_list": _JS_TS_EXT_LIST,
        },
    ),
    "ts": LangConfig(
        language=Language(tstypescript.language_typescript()),
        definition_dict=TS_DEFINITION_DICT,
        import_query=_JS_IMPORT_QUERY,
        usage_node_types=_JS_USAGE_NODE_TYPES,
        import_resolve={
            "separator": "/",
            "index_ext_list": _JS_TS_EXT_LIST,
            "alt_ext_list": _JS_TS_EXT_LIST,
        },
    ),
    "tsx": LangConfig(
        language=Language(tstypescript.language_tsx()),
        definition_dict=TS_DEFINITION_DICT,
        import_query=_JS_IMPORT_QUERY,
        usage_node_types=_JS_USAGE_NODE_TYPES,
        import_resolve={
            "separator": "/",
            "index_ext_list": _JS_TS_EXT_LIST,
            "alt_ext_list": _JS_TS_EXT_LIST,
        },
    ),
}


# Extension aliases and auto-generation of public mapping dictionaries
#
# _EXT_ALIASES defines a mapping of extensions that share the same language settings.
# When generating public dictionaries from _LANG_REGISTRY, _expand_ext_aliases()
# automatically adds alias extensions (h, kts, jsx).
_EXT_ALIASES: dict[str, str] = {
    "h":   "cpp",
    "kts": "kt",
    "jsx": "js",
}


def _expand_ext_aliases(base_dict: dict) -> dict:
    """Return a new dictionary with alias extension entries added based on _EXT_ALIASES.

    For example, if _EXT_ALIASES = {"h": "cpp"} and base_dict contains "cpp",
    the "h" key is also set to the same value.

    Args:
        base_dict: A settings dictionary keyed by canonical extensions.

    Returns:
        A new dictionary with alias extensions added.
    """
    expanded = dict(base_dict)
    for alias, canonical in _EXT_ALIASES.items():
        if alias not in expanded and canonical in expanded:
            expanded[alias] = expanded[canonical]
    return expanded


# Extension -> tree-sitter Language object
TREE_SITTER_LANGUAGES: dict[str, Language] = _expand_ext_aliases(
    {ext: cfg.language for ext, cfg in _LANG_REGISTRY.items()}
)

# Extension -> definition node mapping dictionary
DEFINITION_DICTS: dict[str, dict[str, str]] = _expand_ext_aliases(
    {ext: cfg.definition_dict for ext, cfg in _LANG_REGISTRY.items()}
)

# Extension -> import extraction query
IMPORT_QUERIES: dict[str, str | None] = _expand_ext_aliases(
    {ext: cfg.import_query for ext, cfg in _LANG_REGISTRY.items()}
)

# Extension -> AST node type settings for usage tracking
USAGE_NODE_TYPES: dict[str, dict | None] = _expand_ext_aliases(
    {ext: cfg.usage_node_types for ext, cfg in _LANG_REGISTRY.items()}
)

# Extension -> import path resolution settings
IMPORT_RESOLVE_CONFIG: dict[str, dict] = _expand_ext_aliases(
    {ext: cfg.import_resolve for ext, cfg in _LANG_REGISTRY.items()
     if cfg.import_resolve is not None}
)

# Extension -> whether implicit same-package references are enabled (Java / Kotlin)
SAME_PACKAGE_VISIBLE: dict[str, bool] = _expand_ext_aliases(
    {ext: cfg.same_package_visible for ext, cfg in _LANG_REGISTRY.items()
     if cfg.same_package_visible}
)
