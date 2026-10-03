# Changelog

## Unreleased

### Added
- C/C++: a class, struct or union declared with a macro between the keyword and its name (`class EXPORT Shape : public Base { ... }`) is read as that type: the macro name is read as spaces before parsing, and stays a usage of the macro on its line (`ts_parser.class_macro_cache`)
- C++: inside a member function defined outside its class and inside a class, a member of the class or of a base class written by its name alone is linked to that member, and a member written with a class that only a base class declares is linked to the base class (`ImportBinder.member_scope_list()`, `ImportBinder.inherit_binding()`)
- Python: `from m import *` takes the names the `__all__` of `m` lists (`__all__ = [...]`, `+=`, `.extend([...])`, `.append("...")` with string constants), else the names of `m` that do not start with `_` (`imports.python_all_name_list()`, `ImportBinder.star_name_set()`)
- Python, JS/TS: a variable given an object of a class inside a function is named after the class from that line on (`e = Engine(); e.run()` and `const e = new Engine(); e.run()` are `Engine.run`), as is a variable or parameter annotated with a class in Python and TypeScript (`usages.extract_value_aliases()`; `typed_alias_name_field_dict`, `typed_alias_value_dict`, `typed_alias_new_dict` and `typed_alias_class_only` of the usage settings)
- A variable declared with a type the file defines itself is named after that type, as one declared with an imported type is (Java, Kotlin, C/C++, Python, TypeScript)
- JS/TS: a module that starts with `#` is resolved through `imports` of the nearest `package.json`, and a module that names a package of the project through `exports`, `types`, `module` and `main` of its `package.json`, with a build output path looked up under `src/` (`package_path.py`)
- JS/TS: of the `tsconfig.json` / `jsconfig.json` files, the one that covers the file through `files`, `include` and `exclude` is taken, also among the files the nearest one names in `references`
- JS/TS: `resolve.alias` of the nearest `vite.config.*` / `webpack.config.*` is read for aliases written as a string, `path.resolve(__dirname, ...)`, `path.join(__dirname, ...)` or `fileURLToPath(new URL("...", import.meta.url))` (`alias_path.py`)
- JS/TS: `require("./m").run()` is linked to `run` of the file, and the first parameter of the callback of `import("./m").then((m) => ...)` is bound to the file for the lines of the callback (`ImportInfo.is_use`)
- JS/TS, Python definitions: a destructuring target with a default value or a rest (`const { a: b = 1, c = 2, ...rest } = obj`, `first, *rest = values`)
- Rust: a name a pattern of `match` / `if let` / `while let` / `let` binds is no usage of a name of the file, and a constant, static, struct or enum variant written in a pattern is (`pattern_reference_types`, `pattern_variant_types` of the usage settings)
- Rust: `Self::NAME` inside a `trait` is read as `Trait::NAME`
- Rust: inside an inline module without `use super::*`, a top-level name of the file and a name a `use` declaration of the file binds are not linked (`ImportBinder.module_scope_list()`)
- C#: among the declarations of a method that take the number of arguments of a call, the one whose parameter types fit the arguments is taken when the file tells their types (`CsharpMember.parameter_type_tuple`, `CsharpReference.argument_type_tuple`)
- `definition_source.file_definition_list()` and `definition_source.file_content()`: the definitions and the text of a file without its syntax tree
- `file_dependencies.json`: `callee_usages[]` and `same_file_usages[]` carry `target_name` and `target_start_line`, the `definitions[].name` and `definitions[].start_line` of the definition the usage leads to (`null` when it leads to none) (`definition_source.source_definition()`)
- Rust: a `use` declaration inside a block or an inline module is bound for the lines of that block or module only, and a path starting with a name it binds is resolved through it (`ImportInfo.scope_line_tuple`, `ImportBinder.scope_binding_list()`)
- Rust: paths and `use` names that lead to a definition of the file itself (`self::`, `crate::`, `super::`) are listed in `same_file_usages`; `Self::member` inside an `impl` block is read as `Type::member`
- Rust: `Type::member` is also linked to every other file whose `impl` block of the type defines the member
- Rust: a path with a leading `::` is resolved from the crate it names (from the crate root in an edition 2015 package)
- Rust: enum variants written by their bare name after `use a::Enum::*;` are linked to the enum
- C/C++: a function a header only declares is linked as well to each file that includes the header and defines it (`ImportBinder.implement_file_list()`); the usage, the `caller_usages` of that file and the dependency edge are added
- JS/TS definitions: a function assigned to a member at the top level of a file (`app.init = function () {}`), the function entries of an exported object (`module.exports = { run() {} }`, `export default { run: () => 1 }`), and the members of a TypeScript enum; an entry written `key: name` in `module.exports = { ... }` leads to `name`
- `definition_source.file_definition()` and `definition_source.find_definition()`: the definition of a file a name names
- Python, JS/TS: an import statement or `require()` written inside a function binds its names for the lines of that function, and such a name is no local variable of the function (`extract_imports()`: `scope_types` argument; `extract_usages()`: `import_line_dict` argument)
- JS/TS: a name an export statement only passes on (`export { a } from "./m"`, `export { p as q }`) is a name for the files that import the file and no name of the file itself; in the file only the export statement is a usage of the name it passes on. A name passed on with a source comes before a definition of that name in the file (`ImportInfo.is_export`)
- Python: a default value, an annotation and the return type of a function are read outside the function, where a parameter of the same name does not hide an imported or module-level name (`scope_body_dict` of the usage settings)
- JS/TS: a module that is not relative is resolved through `paths` and `baseUrl` of the `tsconfig.json` / `jsconfig.json` that counts for the file (the nearest one from its directory up; `extends` is followed inside the project). With such a config file a module it does not map is no longer looked up from the project root or a source root (`path_config.path_config()`, `path_config_name_list` of the resolve settings)
- JS/TS imports: `const x = require("./m").y`, `const m = await import("./m")` and `const { a, b: c } = await import("./m")` bind their names
- `utils.project_cache.project_cache_value()`: the value a cache keeps for a project file set
- Names bound by import statements are followed to the file that defines them (`import_binding.ImportBinder`). Python: names an `__init__.py` imports, `from m import *`, a module of a package (`from pkg import module`), `import a.b` beside `import a.c`. JS/TS: default and namespace imports, `export ... from`, `export *`, `require()` bound to a name or destructured with a rename, `import()` of a string, `module.exports = name` and `module.exports = require(...)`. Java / Kotlin: nested class imports, static imports, wildcard imports, imports under another name, and top-level functions, properties and type aliases of a package. C/C++: the headers an included header includes
- `reference_target.reference_target_list()`: the definition each reference of a file resolves to, for every language; `import_reference.import_reference_target_list()` resolves the references of the languages with import statements
- A name bound inside a function (parameter, local variable, loop variable, inner function) is not a usage of an imported or file-level name of that name (Python, JS/TS, Java, Kotlin, C/C++, Rust; `scope_types`, `local_binding_dict` and the related keys of the usage settings)
- A member written after `self` / `cls` / `this` is a usage of the definition of that name in the file (`self_names` / `self_types` of the usage settings)
- Definitions. Python: every target of a chained or destructuring assignment, `type` aliases, the members of a decorated class. Java: records, annotation types and their elements, enum constants, compact constructors, every variable of a field declaration. Kotlin: type aliases, enum entries, named companion objects, the variables of a destructuring declaration. JS/TS: generator functions, `exports.name = ...` / `module.exports.name = ...`, an unnamed default export (`default`), every variable of a declaration and of a destructuring pattern; TS abstract classes and their method signatures, function signatures, interface members, namespaces. C/C++: `typedef` names, unions, enumerators, function-like macros, variables declared without an initializer or as pointers or arrays, every variable of a declaration, operators and destructors, concepts. C#: indexers (`this[]`), operators (`operator +`), conversion operators (`operator int`), destructors (`~Name`), every variable of a field declaration
- Module resolution: a module written with two or more parts (Python, C/C++ includes of any length) is matched against the end of the project file paths, the nearest file first (`min_path_end_part`); source roots (`src/main/java/` etc.) are detected under any directory; a JS/TS import written with `.js` / `.jsx` / `.mjs` / `.cjs` resolves to the TypeScript file of that name (`source_ext_dict`); a relative JS/TS path that leads to the project root resolves to its index file; the package of a Java / Kotlin file is read from its `package` statement
- Rust: paths written in the arguments of a macro (`assert_eq!(crate::util::add(1, 2), 3)`) are resolved and are usages; a glob import brings in the names the module re-exports with `use`; the value of an enum variant (`A = BASE`) is a usage
- `LangConfig.reference_kind` and `settings.EXT_TO_REFERENCE_KIND_DICT`: how the references of a file are resolved (`import` / `cobol` / `csharp` / `r`)
- `extractors.definition_source`: `extract_callee_source()` (moved from `dependency_graph`) and `clear_definition_source_cache()`
- R (`r`, `rmd`, `qmd`, in any case; grammar from `tree-sitter-language-pack`): top-level assignments, the names given to `setClass`, `setRefClass`, `setClassUnion`, `setGeneric`, `setMethod`, `setReplaceMethod` and `setValidity`, and the members of `R6Class` and `setRefClass` are extracted as definitions. A name no function around it binds is resolved to the file that defines it through the names `box::use` attaches, the scripts read with `source()` / `sys.source()` and the scripts that read the file, `global.R` and the `R/` directory of a Shiny app, the helper scripts of a `testthat` directory, the `R/` directory of the package the file is in (the nearest `DESCRIPTION` file with a `Package:` field) and the project packages attached with `library()` / `require()`; `pkg::name`, `mod$name` of a box module, the class and generic names given as strings to `new`, `setMethod`, `setValidity` and `contains`, `do.call("f")`, `match.fun("f")`, operators written between `%`, `f(x) <- value` (to `f<-`) and `name <<- value` inside a function are resolved as well, and an S3 method `generic.class` leads to its generic. A script read with `source()` or `box::use` is a dependency whether or not a name of it is used. Of an R Markdown or Quarto file the `{r}` code chunks are analyzed, and such a file without one is a file without a language (`codetwine/r_name_index.py`, `codetwine/extractors/r_source.py`, `codetwine/parsers/r_markdown.py`)
- `r_name_index.r_reference_target_list()`: the definition each reference of an R file resolves to, and `r_name_index.r_import_file_list()`: the scripts an R file reads with `source()` and `box::use`
- `settings.R_EXT_SET`: the extensions of the R files, and `settings.R_MARKDOWN_EXT_SET`: the ones whose code is in the R chunks of a document
- `parsers.r_markdown.r_chunk_code()`: the text of an R Markdown or Quarto file with everything but its R code chunks blanked, positions kept, and `parsers.r_markdown.has_r_chunk()`
- `settings.set_no_language_file()`: the files of a project analyzed without a language whatever their extension
- `parse_file()`: for an R Markdown or Quarto file the root node is the tree of its R code chunks, and the byte content the whole file
- `extract_callee_source()`: for an R file the definition named by the whole name is returned; a name with `.` is not split
- Other extensions of supported languages: `cc`, `cxx`, `hpp`, `hh`, `hxx` (C++), `mjs`, `cjs` (JavaScript) and `mts`, `cts` (TypeScript); an include or import written with one of them resolves to the file
- `file_dependencies.json`: `detected_encoding`, the encoding a file was decoded with when no BOM, UTF-8 or `SOURCE_ENCODING` decoded it (`""` when it was read as UTF-8 with invalid bytes replaced, `null` otherwise)
- `process_all_files()`: `detected_encoding_dict`, the files whose encoding was detected, with that encoding
- `file_utils.detected_encoding()`: the encoding `read_source()` detects for a file, or `None` when a BOM, UTF-8 or `SOURCE_ENCODING` decodes it
- `file_utils.line_list_of()`: the lines of a text split at `\n`, `\r\n` and a lone `\r` alone, and `file_utils.lone_cr_to_lf()`
- C# (`cs`, `tree-sitter-c-sharp`): classes, structs, interfaces, enums, records, delegates, methods, constructors, properties, events, fields and enum members are extracted as definitions. A name is resolved to the file of the type or member it names through the namespaces around it, the `using` directives (`using`, `using static`, alias, `global using` under the nearest `.csproj` directory), names written with their namespaces, the members of a `partial` type written in other files, extension methods by name and number of arguments, and attributes to their class, written with or without the suffix `Attribute`. A usage is named as written without its namespaces, except that an attribute written without the suffix is named with it (`[Audit]` is `AuditAttribute`) and a name written with an alias of a type is named with the type (`Fmt.Format` with `using Fmt = App.Text.Formatter;` is `Formatter.Format`) (`codetwine/csharp_namespace_index.py`, `codetwine/extractors/csharp_source.py`)
- `csharp_namespace_index.csharp_reference_target_list()`: the definition each reference of a C# file resolves to
- `settings.CSHARP_EXT_SET`: the extensions of the C# files
- Definition name settings `__name_field__` (the `name` field of a declaration) and `__variable_declaration__` (the variables of a field declaration)
- `tree-sitter-c-sharp` dependency
- `file_dependencies.json`: `language`, the extension whose language settings the file is analyzed with (`cpy` for a copybook of any extension that a `COPY` statement names, `""` for a file without a language)
- `parsers.cobol_format.code_text_list()`: the code of each line of a COBOL source file, as the file is analyzed (columns 8 to 72 in fixed format, the whole line in free format, nothing for a comment or directive line)
- COBOL fixed format: the code area ends at column 72 unless more lines have code past column 72 than an identification field in columns 73 to 80; one line past column 80 no longer makes the whole file read without the right margin
- COBOL references: a name qualified with `OF` / `IN`, and a host variable written `:STRUCT.NAME` in `EXEC SQL`, is linked to the definition under the named groups in the file or in a copybook, a group around a `COPY` statement counting for the copybook's definitions; an unqualified name to the file's own definition, else to the first copybook that defines it; the program name of a `CALL` to the program only (`cobol_file_index.cobol_reference_target_list()`, `cobol_source.CobolReference`)
- COBOL copybooks named by `COPY` / `EXEC SQL INCLUDE` whatever their extension: a file without a language that such a statement names (DCLGEN `.dcl`, `.inc`, no extension) is analyzed as a copybook (`cobol_file_index.register_copy_target()`, `settings.set_copy_target_ext()`)
- BMS (`bms`): the names of the symbolic map of each map (`<map>I` / `<map>O`, and `L`, `F`, `A`, `I`, `O` and the extended attribute letters of each field, with `OCCURS` and `GRPNAME`) are extracted as definitions, and `COPY` of a mapset leads to its BMS source unless a COBOL file of that name is in the project (`codetwine/extractors/bms_source.py`, `settings.BMS_EXT_SET`)
- `file_dependencies.json`: a COBOL or BMS definition also has `name_line`, the line its name is written on, and a data item `level` and `is_group`
- `settings.language_ext()`: the extension whose language settings a file is analyzed with; COBOL and BMS extensions are matched in any case (`LangConfig.ignore_ext_case`)
- COBOL (`cbl`, `cob`, `cpy`, in any case; grammar from `tree-sitter-language-pack`): programs, `ENTRY` names, sections, paragraphs, data items and file descriptions are extracted as definitions. `COPY` (with `OF` library and `REPLACING`), `EXEC SQL INCLUDE`, `CALL` of a literal or of a data item given a literal, and `EXEC CICS ... PROGRAM(...)` are resolved to files by file name and program name. Fixed-format and free-format source, names outside ASCII and names with `_` are read; each data item and each sentence is parsed by itself (`codetwine/parsers/cobol_format.py`, `codetwine/extractors/cobol_source.py`, `codetwine/cobol_file_index.py`)
- `parse_file()`: returns a `CobolSource` in place of the root node for a COBOL file; `extract_definitions()`, `extract_imports()`, `extract_usages()` and `extract_callee_source()` take it
- `settings.COBOL_EXT_SET`: the extensions of the COBOL files (lower case)
- `definitions.BARE_NAME_DEFINITION_TYPE_SET`: definition types `select_top_level_definitions()` keeps wherever they are nested
- `tree-sitter-language-pack` dependency (0.13.0, the release with the grammars inside the wheel)
- Rust (`rs`, `tree-sitter-rust`): functions, structs, enums, unions, traits, impl blocks (named after their type), type aliases, constants, statics, inline modules and `macro_rules!` are extracted as definitions. `use` declarations, `mod` declarations (including `#[path]`), `extern crate` and paths written without `use` are resolved to files through the tree of `mod` declarations, through re-exports, and through the crates whose `Cargo.toml` is in the project (`codetwine/rust_module_tree.py`, `codetwine/extractors/rust_path.py`)
- `resolve_module_to_project_path()`: `project_dir` argument, required for a language whose resolve config has `module_tree` (Rust)
- `definitions.definition_name_list()` and `definitions.select_top_level_definitions()` (moved from `import_to_path._select_top_level_definitions()`)
- `usages.symbol_part_list()` / `usages.usage_root_name()`: split a usage name at `.` and `::`
- SQL (`sql`, `tree-sitter-sql`): tables, views, materialized views, functions, procedures, types, sequences, triggers, indexes and schemas are extracted as definitions, and a reference to an object created in another `.sql` file of the project is a dependency
- `LangConfig.implicit_visibility` (`package` / `project`): the files whose definitions can be referenced without an import statement (Java / Kotlin: same package, SQL: whole project), replacing `same_package_visible`
- Every non-empty text file is analysed: a file whose extension has no tree-sitter language is listed with empty `definitions`, `callee_usages` and `caller_usages`, a `null` summary and no design document, and copied to the output directory (`settings.has_language()`, `file_utils.is_text_file()`)
- `KNOWLEDGE_FORMAT` setting (`json` / `sqlite` / `both`) selecting the form of the whole-project result
- `codetwine/knowledge_db.py`: SQLite output (`project_knowledge.sqlite`) built from the per-file JSON files, with a read API (`open_knowledge`, `iter_files`, `get_file`, `callers_of`, `callees_of`, `find_definitions`)
- `PARSE_CACHE_MAX_FILES` setting capping how many files' parse results are kept in memory
- `knowledge_db.iter_dependencies()`: read each file's summary and dependency lists back from the database
- `examples/rlm_qa`: accepts a `project_knowledge.sqlite` as well as a `project_knowledge.json` (`TARGET_JSON_PATH` renamed to `TARGET_KNOWLEDGE_PATH`)
- `examples/rlm_qa/knowledge_store.py`: per-file read access to either knowledge file form
- `examples/rlm_qa`: `get_file_detail()` and `search_text()` tools
- `knowledge_db.find_definitions()`: `partial` argument for a case-insensitive contains match
- `doc_template.json`: heading format instruction for the `definitions` section (`` ## `<name>` ``)
- `doc.json`: `source_hash`, the SHA256 of the source file the document was generated from
- `doc_creator.load_doc()`: read one file's `doc.json`
- `LLMClient`: a warning when the LLM output was cut at `DOC_MAX_TOKENS`
- `examples/doc_template_search.json`: search-oriented design document template for any language (one section: overview and one prose entry per definition). The sample output is generated with it
- `process_all_files()` / `build_project_dependencies()`: `file_list` argument (paths relative to the project root). When given, only these files are analyzed instead of walking the project directory; `EXCLUDE_PATTERNS` and the text file check apply to them as well
- `process_all_files()`: returns `file_count`, `dependency_fail_list`, `doc_count` and `doc_fail_list` instead of `None`
- `generate_all_docs()`: returns the files left without a complete design document
- `file_dependencies.json`: `same_file_usages`, the lines where a file uses the names it defines itself (`usage_analysis.build_same_file_usages()`). `callee_usages` and the file-level dependency graph still hold other files only
- `SOURCE_ENCODING` setting: encodings tried, in order, on a source file that has no BOM and is not valid UTF-8. `process_all_files()` checks each name before it analyses anything (`file_utils.check_source_encoding()`)
- `file_utils.read_source()`: decode a source file with the codec its BOM names, UTF-8, `SOURCE_ENCODING`, the encoding charset-normalizer detects, or UTF-8 with invalid bytes replaced, and return the encoding used. `file_utils.read_source_text()` returns the same text with line breaks turned into `\n`
- `charset-normalizer` dependency

### Changed
- `examples/sample_output` regenerated from the current source with the template `examples/doc_template_search.json`
- `callee_usages`, `same_file_usages` and `caller_usages` hold one entry per name and definition it leads to: a name that leads to two definitions of one file (two overloads, two data items of one name qualified differently) gives two entries
- A local variable or parameter comes before a variable of the same name declared with a type outside its function (a field, a prototype parameter)
- The name a pattern binds (`inner` in `{ hp: inner = hp }`, a parameter name of a C prototype) is no usage of an imported or file-level name of that name
- COBOL free format: an indented line that starts with `*` is read as code when the line before it ends with an operand (`COMPUTE X = A` / `* B`); a `*` in column 1 and `*>` stay comments
- A file is parsed at most twice in one analysis whatever `PARSE_CACHE_MAX_FILES` is: its import statements, its definitions and what its bindings need are read from one parse and kept without the syntax tree, the dependency graph reads the imports and the references of a file one after the other, and the per-file step reads the definitions it kept
- R: a script read with `source()` sees `global.R`, the `R/` directory and the testthat helper scripts of the files that read it, and the names of an R Markdown or Quarto document that reads it
- R: every target of `a = b <- value` is a definition
- R: a name assigned inside a top-level statement, outside the functions and `local()` calls written in it (`tryCatch({ cfg <- load() })`), is a definition when a later top-level statement refers to the name without assigning it itself
- R: the names `box::use` attaches are read once per module when modules use one another in layers
- COBOL: a data name or procedure name is not linked to the program or `ENTRY` of the same name
- COBOL: a file named by a `COPY` statement without its extension is a copybook when it holds only statements of the procedure division
- COBOL: a file description (`FD` / `SD`) spans its record descriptions and `COPY` statements (its `end_line` and source text), and `name OF file-name` is linked to the record of that file
- COBOL: a `CALL` usage has the name of the program definition; a `CALL` of a program file by its file name is linked to its first program
- COBOL: `COPY name` takes a file with the extension `cbl`, `cob` or `cpy` before a file without an extension
- COBOL: index names (`INDEXED BY`) are definitions of type `occurs_indexed`; the names of a copybook included by a copybook are linked, with a dependency edge to it; an item named like `(PFX)-ID` is not a definition
- COBOL: a file whose lines hold code in columns 1 to 6, or whose first line with code has `-` in column 7, is read as free format
- COBOL: an ambiguous-width character counts as two columns in a file that holds a full-width character
- COBOL: a line that starts with `@` or `$` is a directive line only when a word of letters follows
- C#: a name that is both a member of the enclosing type and a type (`Status Status { get; set; }`, a constructor) is the type when a static member, a constant, an enum member or a nested type follows it (`Status.Closed`)
- C#: a type is looked up with the number of type arguments it is written with (`Result<int>` is not `Result`); a type of another number is taken only when no step of the lookup finds one
- C#: an alias or `using static` of a generic type keeps its number of type arguments; the types written inside a `using` alias are usages on the line of the directive
- C#: `target_context` is the declaration the reference resolves to: the overload that takes the number of arguments of the call, the type with the number of type arguments written (`CsharpReferenceTarget.definition_line`, `definition_source.extract_definition_source()`)
- `same_file_usages`: a reference is left out only inside the lines of the definition it names. `Type.Member` written inside `Type` is kept, and a COBOL reference is compared with the definition it resolved to instead of every definition of that name
- A variable declared with a type of the project is named after the type inside the function it is declared in only (`extract_typed_aliases()` returns `TypedAlias` entries with the lines they count for); a field counts for the whole file
- `extract_usages()`: `keep_local_names` is replaced by `alias_list`
- `caller_usages` of a file are the `callee_usages` of the files that depend on it that lead to the file, for every language: the names, lines and files of the two lists match
- `usage_analysis`: `build_callee_usages()`, `build_same_file_usages()` and `build_caller_usages()` take the resolved references of a file (`reference_target_list()`) for every language, replacing `build_usage_info_list()` and the `build_cobol_*` / `build_csharp_*` / `build_r_*` functions
- `get_file_dependencies()` and `build_caller_usages()` no longer take `source_root_set`
- `extract_callee_source()`: the name is looked up among the definitions of the file (`extract_definitions()`), each part of a name inside the definitions the part before it names (`Delete.Handler.Run`). A name that is no definition of the file gives `None` instead of a definition that mentions the name. The text of a C/C++ function prototype is its whole declaration
- Usage names: an attribute access is named by the names it writes (`helper.process`; a chain after a call starts a new name), C/C++ `a->b` is `a.b`, a C++ name written with `::` is named from its first part that is a definition (`Shape::count`), and a Java method call is named with its method (`User.of`)
- C++: the name of a namespace is not a name of the file (`geo::Circle` is `Circle`); the definitions inside a namespace and the enumerators of an enum count as top-level names; a member defined outside its class is named with its class (`Shape::count`)
- C/C++: a struct, union, enum or class written without a body (`struct node *p`) is not a definition; a name a file defines itself is not linked to a header that declares it
- Definition line ranges: a `#define` ends on its own line, a function returning a pointer is one definition over its body, and a function prototype covers its whole declaration
- `build_project_dependencies()`: `callers` and `callees` are listed in path order; a file is never its own caller or callee; the files of a wildcard import are callees when one of their names is used
- Python: an import that is not relative is not looked up among the files of the directory when that directory is a package (`from types import ...` in a package with `types.py`); `from x.y import a` no longer registers `x` as a name
- JS/TS: an import that names no names (`import "./m"`), a default import and a namespace import no longer register every definition of the module as a name of the file; the import line itself is not a usage
- Java / Kotlin: a name of the same package no longer replaces a name the file imports; a name another file of the package imports is not visible through a wildcard import
- SQL and same-package names: of several files that define a name, the first in path order is linked
- COBOL: of an `EXEC SQL` block only the host variables are read as names of the program
- R Markdown / Quarto: a chunk whose code does not end inside the chunk (an open `{`, an operator at its end) is left out instead of taking the chunks after it into its statement (`r_chunk_code()`: `is_chunk_kept` argument)
- R: of the project packages attached with `library()`, the one attached last is looked up first; `setRefClass(fields = c(...))` lists its members; a name is looked up once per file
- C#: `[global::Namespace.Name]` is linked to `NameAttribute`; `Type.Member` gets the source of the member under the whole path written
- C#: a parameter, local variable, lambda parameter, range variable or local function is not linked to a member or type of that name; a method called on such a value is matched against the extension methods only
- `build_project_dependencies()`: a file whose analysis raises an exception is logged and left without dependencies in the graph instead of stopping the run; `process_all_files()` reports it in `dependency_fail_list`
- `LangConfig` fields renamed after their type: `usage_node_types` -> `usage_node_type_dict`, `import_resolve` -> `import_resolve_dict`
- `extract_callee_source()`: for a name with two or more parts, the last part is first looked up inside the container definitions named by the part before it (`Settings::new`, `Config.load`), and a definition whose own name matches is preferred over a definition that only contains the name
- `same_file_usages`: a name bound by an import statement that the file defines only inside another definition (a method named like an imported module, e.g. Rust `use std::fmt;` and `fn fmt`) is not tracked, even when the import leads outside the project
- Wildcard imports (`from X import *`, `import pkg.*`, Rust `use X::*`) no longer register the names the importing file defines itself
- Public settings renamed after their type: `TREE_SITTER_LANGUAGES` -> `EXT_TO_LANGUAGE_DICT`, `DEFINITION_DICTS` -> `EXT_TO_DEFINITION_DICT`, `IMPORT_QUERIES` -> `EXT_TO_IMPORT_QUERY_DICT`, `USAGE_NODE_TYPES` -> `EXT_TO_USAGE_NODE_TYPE_DICT`, `IMPORT_RESOLVE_CONFIG` -> `EXT_TO_IMPORT_RESOLVE_DICT`, `KNOWLEDGE_FORMATS` -> `KNOWLEDGE_FORMAT_TUPLE`, `SOURCE_ROOT_PATTERNS` -> `SOURCE_ROOT_PATTERN_LIST`
- `build_project_dependencies()`: an implicit dependency (Java / Kotlin same package, SQL) is added when a top-level definition name of the other file is used in the syntax tree, instead of when the file name appears in the source text
- `build_project_dependencies()`: collects every text file instead of only the supported extensions, and skips empty and binary files there; import resolution, dependency edges, change detection and design documents cover the files with a language only
- `examples/rlm_qa`: the agent now receives only the file graph and per-file summaries; definitions, source code and design documents are fetched per file through the tools instead of being sent into the sandbox
- `parse_file()`: the parse cache is now a bounded LRU, so the syntax trees of a whole project are no longer held at once
- `save_consolidated_json()` / `save_dependency_summary()`: entries are written one at a time instead of being assembled in a list first
- `generate_all_docs()`: only each design document's summary is carried forward between levels, not its full section text
- `get_file_dependencies()`: takes the project file set and caller map from the caller instead of rebuilding them per file
- `examples/doc_template_python.json`: the five sections are merged into one `design` section, so a design document takes one LLM call plus the summary
- `process_all_files()`: change detection runs only when design documents are generated
- `DOC_MAX_TOKENS` default raised from `8192` to `16384`
- `litellm` dependency no longer capped at `1.82.6` (`litellm>=1.64.0`)
- `MAX_RETRIES`: the number of retries after the first LLM call instead of the number of calls. `0` makes one call without retrying, and a negative value stops `LLMClient()` with a `ValueError`
- `parse_file()`: the file is decoded by `read_source()` and parsed as UTF-8; the returned byte content is that UTF-8 text instead of the file's bytes. A UTF-8 BOM is no longer part of the content
- `is_text_file()`: a file that starts with a UTF-16 or UTF-32 BOM is text

### Removed
- `import_to_path.build_symbol_to_file_map()`, `import_to_path.import_name_list()`, `settings.implicit_scope_key()`, `cobol_file_index.cobol_import_name_dict()`, `definitions.definition_name()` (see `definitions.definition_name_list()`)
- `is_file_unchanged()`

### Fixed
- C#: `Type.Member` written inside a type with a constructor led to the constructor when `Member` is not static
- C#, COBOL, R: two references of one line that write the same name and lead to two definitions were kept as one
- Rust: a name a module only re-exports from outside the project (`pub use std::collections::HashMap;`) gives no usage
- JS/TS, Kotlin, Rust: the declarations inside a callback, a class static block, a lambda, an `init` block or a closure were listed as definitions of the file. The ones inside a function called where it is written (`(function () { ... })()`) are still listed
- C++ usage names no longer carry template arguments (`obj.get<int>` is `obj.get`)
- Python: `from m import *` was not read as an import; the members of a decorated class were not listed
- JS/TS: a destructuring declaration was listed as one definition named after the pattern (`{ a, b }`)
- C: a header (`.h`) lost its `typedef` names; a function returning a pointer was listed by its declarator line only and its local variables were listed as definitions
- Kotlin: an import under another name (`import a.b.C as D`) was not read; a variable declared with an imported type was not linked to the type
- Usage names no longer carry call arguments or line breaks (`a.b(x).c`, a method chain over several lines)
- Rust: an `impl` block for a type without a name (`impl dyn Trait`, `impl Trait for (u8, u8)`) is listed as a definition and keeps its members
- `build_project_dependencies()`: a second call for a project in one process reads the project again. The Rust module tree, the C# namespace index and the COBOL file index of the first call were kept while the set of analyzed files stayed the same, so a `Cargo.toml` or `.csproj` file added, removed or changed in between did not count
- A file analyzed without a language keeps no design document: the `doc.json` and `doc.md` a previous run wrote while the file had a language (a copybook no `COPY` statement names any more) are removed, and its summary is `null`
- Line numbers count lines at `\n`, `\r\n` and a lone `\r` alone. A file whose lines end in a lone `\r` was read as one line by tree-sitter, so every definition started on line 1, and a form feed, U+2028 or another character `str.splitlines()` breaks at shifted the source text of the definitions after it (and the lines of a COBOL or BMS source) by a line
- File collection leaves out symbolic links and paths under a linked directory, whether the project is walked or `file_list` is given; a file outside the project is no longer analyzed through a link, and a file inside it is not listed twice
- `extract_callee_source()`: returns the definition node the name belongs to instead of the parent of the name node. A C/C++ function definition now comes with its body, and a SQL object with its whole `CREATE` statement
- Change detection compares the source with the `source_hash` recorded in `doc.json` instead of with the source copy in the output directory. The copy is refreshed by every run, so a change made between runs with `ENABLE_LLM_DOC=False` was never regenerated. A design document without `source_hash` is regenerated once
- `KNOWLEDGE_FORMAT`: an unusable value no longer stops `import codetwine`. It is checked at the start of `process_all_files()` instead, before anything is analysed, so a caller that replaces the setting in the pipeline's namespace is not stopped by what the environment holds
- `generate_candidate_path_list()`: resolve an import whose specifier already carries a known extension (JS/TS `import "./helpers.js"`). Such a path is now tried as it is instead of only as a directory index
- `extract_definitions()`: extract methods, constructors and fields declared inside a class, struct, interface, enum or object
- `extract_definitions()`: extract Kotlin `val` / `var` / `const val`
- `process_all_files()`: when the dependency extraction of a file fails, the `file_dependencies.json` and the source copy of a previous run are removed. The consolidated result no longer carries the previous analysis of that file
- `save_consolidated_sqlite()` / `save_consolidated_json()`: the result is written to `<output path>.tmp` and moved into place once complete. A run stopped part way no longer leaves a database with tables but no `meta` rows, or a truncated JSON, in place of the previous result
- `build_caller_usages()`: resolve the caller's imports with the source roots (`src/main/java/` etc.). A Java / Kotlin file imported from another package now has the importing file in `caller_usages` and `callers`
- A source file that is not UTF-8 (Shift_JIS, EUC-JP, Latin-1 etc.) is analysed instead of failing with `UnicodeDecodeError`, and so are the files that import it. Its design document is generated from the decoded text, `usage_context` is filled for it as a caller, and its copy in the output directory keeps the original bytes. A Shift_JIS file is no longer parsed from its raw bytes, where a second byte equal to `\` broke the syntax tree and dropped definitions
- A UTF-16 or UTF-32 file with a BOM is analysed instead of being skipped as binary
- `examples/rlm_qa`: `read_source_file()` reads a source copy that is not UTF-8
- Updated sample output

## 0.3.0 - 2026-07-25

### Added
- LLM code summarization as a context-overflow fallback in design-document generation: large dependency symbols (`callee_usages[].target_context`) and large source definitions are replaced by concise behavior summaries, cached per symbol via SHA256 (`_summarize_code`, `_summarize_callee_usages`, `_splice_large_definitions`)
- `ENABLE_CODE_SUMMARY`, `CODE_SUMMARY_TRIGGER_LINES`, `CODE_SUMMARY_MAX_CHARS` settings

### Changed
- `_generate_section_with_fallback()`: restructured the context-overflow fallback into cumulative reduction stages (drop caller bodies → drop callee context → summarize callee symbols → summarize source definitions)
- `CALLEE_USAGES_SCHEMA_NOTE`: corrected wording to describe the dependency symbol (not file), noting large ones may be summarized

### Removed
- The 100-character compaction stage of the callee-summary fallback (low yield; superseded by symbol-level code summarization). `_build_callee_context_summary()` no longer takes a `compact` argument

## 0.2.1 - 2026-04-15

### Added
- `detect_source_roots()`: Detect source root prefixes (e.g. `src/main/java/`) present in the project
- `resolve_module_to_project_path()`: Fallback resolution with source root prefixes for Maven/Gradle/Scala standard layouts
- `SOURCE_ROOT_PATTERNS`: Configuration for known source root directory patterns
- Updated sample output

## 0.2.0 - 2026-04-11

### Changed
- `_save_doc()`: Changed Markdown section headings from `##` to `#`
- `_parse_md_sections()`: Updated section delimiter from `## {title}` to `# {title}`
- `_build_section_prompt()`: Use `output_path_to_rel()` for relative file path in prompt
- Updated sample output

## 0.1.9 - 2026-03-31

### Fixed
- `_save_doc()`: Strip duplicate section title headers that the LLM may include in its response
- Updated sample output

## 0.1.8 - 2026-03-30

### Changed
- README: Rewrote High-Level Processing Flow for clarity (step 1: file collection, step 3: extraction details, step 4: topological sort and summary propagation, step 5: output description)
- README: Moved Output Files section to directly follow Processing Flow

## 0.1.7 - 2026-03-27

### Changed
- README: Added emoji icons to section headings
- RLM QA agent: Added `LLM_API_BASE` configuration for custom API endpoints (e.g. Ollama, Azure)
- RLM QA agent: Replaced `dspy.configure(lm=lm)` with `rlm.set_lm(lm)` for module-level LM setting

### Removed
- RLM QA agent: Removed `max_iterations` parameter from RLM

## 0.1.6 - 2026-03-26

### Changed
- `rlm_qa_agent.py`: Renamed private functions to public (`_build_doc_schema` → `build_doc_schema`, `_load_project` → `load_project`, `_create_interpreter` → `create_interpreter`)

## 0.1.5 - 2026-03-25

### Changed
- `pyproject.toml`: Pinned all dependencies to exact versions (`>=` → `==`)
- `graph_search()`: Renamed return key `results` → `nodes`
- `graph_search()`: Renamed internal variables for clarity (`starts` → `candidates`, `file_deps` → `deps`, `caller` → `usage`)
- Updated sample output

## 0.1.4 - 2026-03-24

### Changed
- `doc_template.json`: Removed character limit (400-600 chars) from `summary_prompt`
- RLM QA agent: Strengthened Investigation rules to require verifying answers against actual source code before responding
- RLM QA agent: Increased `max_iterations` from 10 to 12

### Added
- RLM QA agent: Added `SUB_LLM_MODEL` to separate sub-LLM for `llm_query` / `llm_query_batched` within RLM sandbox

## 0.1.3 - 2026-03-19

### Fixed
- README: `--output-dir` default description did not match actual behavior when only `--project-dir` is specified
- README: `examples/rlm_qa/qa_tools.py` was missing from Project Structure

### Changed
- README: Clarified `file` / `callers` / `callees` field descriptions in JSON Schema tables to indicate they are paths within the output directory
- README: Added `OUTPUT_LANGUAGE` to Quick Start `.env` example
- README: Added Note in RLM QA section explaining that `file` field paths differ from original source tree paths
- RLM QA agent: Removed usage guidance from `context` field description in JSON Schema, keeping only data structure info

### Added
- RLM QA agent: Added Investigation rules with concrete methods for code investigation (`definitions[].context` / `read_source_file()`)

## 0.1.2 - 2026-03-19

### Fixed
- Python same-directory imports (e.g. `import module_name`) not detected as dependencies

### Changed
- Renamed `config/logging.py` to `config/logger.py` to avoid standard library name collision

### Added
- Python-optimized design document template (`examples/doc_template_python.json`)

## 0.1.1 - 2026-03-18

### Fixed
- Incomplete `doc.json` (missing sections or empty summary) being reused instead of regenerated
- `InternalServerError` and `ServiceUnavailableError` not being caught in LLM API error handling

## 0.1.0 - 2026-03-17

### Added
- Dependency analysis via tree-sitter (supports 7 languages: Python / Java / JavaScript / TypeScript / C / C++ / Kotlin)
- Automated design document generation via LLM (supports multiple providers through litellm)
- Symbol-level (functions, classes) dependency extraction
- Dependency-order-aware document generation via topological sort
- Incremental processing (regenerates only changed files and their affected scope)
- Dependency graph output in Mermaid format
- Customizable design document template (`doc_template.json`)
- Manual editing of `doc.md` with automatic reflection to `doc.json`
- Dependency-only output with `ENABLE_LLM_DOC=False`
- RLM QA agent sample (`examples/rlm_qa/`)
