# Changelog

## 0.5.0 - 2026-10-27

### Added
- `parsers/ts_node.py`: the text of a tree-sitter node (`node_text()`), the nodes of a tree in source order (`iter_node()`) and whether a node is written inside a node of the given types (`has_ancestor()`)
- `file_utils.path_to_rel()` (the path of a file relative to the project root with `/` separators), `file_utils.load_doc()` and `file_utils.load_file_dependencies()` (the `doc.json` and `file_dependencies.json` of a file's output directory; `doc_creator.load_doc()` stays), `file_utils.read_source_info()` (what `read_source()` and `detected_encoding()` return, from one read), `file_utils.replace_on_success()` (a temporary path moved into place once written), `file_utils.lower_ext()`, `file_utils.add_unique()` and `file_utils.directory_distance()`
- `doc_creator.check_doc_template()` and `doc_creator.check_worker_count()` check the template `DOC_TEMPLATE_PATH` names and the number of design documents generated in parallel; `doc_creator.sync_md_edit()` takes the edits of the `doc.md` of the given files into their `doc.json`
- `dependency_graph.clear_analysis_cache()` forgets the syntax trees, indexes and resolved references of an analysis; `pipeline` calls it when a run ends
- `project_cache.identity_cache_tuple()`, `ts_parser.file_detect_encoding()`, `definitions.is_within()`, `definitions.export_value_node()`, `definitions.is_module_string()`, `import_binding.member_function_class_name()`, `package_path.directory_entry_path_list()`, `package_path.is_dependency_module()`, `parse_error.blank_text()`, `csd_command.non_blank_index()`, `rust_path.inline_module_name()` and `rust_path.node_line_tuple()`
- Constants shared by several modules: `settings.TAB_SIZE`, `settings.C_HEADER_EXT_TUPLE`, `settings.C_IMPL_EXT_TUPLE`, `file_utils.DEPENDENCY_FILE_NAME`, `file_utils.DOC_JSON_NAME`, `file_utils.DOC_MD_NAME`, `cobol_format.RECORD_LEVEL_RANGE`, `jcl_source.COMMENT_PREFIX`, `jcl_source.JES_PREFIX` and `rust_path.MODULE_PATH_ROOT_SET`

### Changed
- `EXCLUDE_PATTERNS` adds its patterns to `__pycache__`, `.git`, `.github`, `.venv` and `node_modules` instead of replacing them; a value of only blanks and commas adds none
- `RETRY_WAIT` takes seconds with a fraction (`0.5`)
- A True / False setting (`ENABLE_LLM_DOC`, `ENABLE_CODE_SUMMARY`) reads `true` / `false`, `yes` / `no`, `on` / `off` and `1` / `0` in any case and with blanks around them, and any other value stops the import with a `ValueError` naming the setting; so does a number setting whose value is no number
- The whole-project files (`project_knowledge.json`, `project_knowledge.sqlite`, `project_dependency_summary.json`, `dependency_graph.md`) leave out a file whose dependency extraction failed, also as a caller or callee of the other files; `iter_dependencies()` yields the same files as `project_dependencies`
- The whole-project files take the design document and summary of a file only from a `doc.json` generated from the file's current content (`source_hash`); a `doc.json` of an older content stays in the output directory and comes back once the content is the same again
- `dependency_graph.md` names its nodes `n0`, `n1`, ... in the order of their paths, and writes a `"` of a label as `#quot;`
- A run removes `project_knowledge.json` or `project_knowledge.sqlite` when `KNOWLEDGE_FORMAT` does not select that form
- `knowledge_db.find_definitions(partial=True)` ignores the case of any letter (`str.casefold()`), not only of ASCII letters
- `process_all_files()` and `build_project_dependencies()` leave out an output directory inside the project directory, also when `file_list` names files in it (`build_project_dependencies(exclude_dir=...)`) and when one of the two paths goes through a symbolic link; an output directory that holds the project directory above its parent (such as `~` or `/`), where the results are written outside the project directory, leaves out nothing
- `process_all_files(file_list=...)` names in a warning the paths it leaves out because they do not exist or lead outside the project directory
- `main.py` logs a wrong directory or setting (a `ValueError` of `process_all_files()` or `LLMClient()`) as an error and exits with status 1, instead of printing a traceback
- A design document's summary is generated only when the template has a `summary_prompt`; without one the document's `summary` is `""` and no warning is logged
- A C/C++ header's design document is regenerated when its implementation file (the `.cpp`, `.c`, `.cc` or `.cxx` file of the same name in the same directory) changed, and the implementation file given with a header is taken from the files of the project only
- The line that ends design document generation reports the documents generated, reused and left incomplete or failed (`Generated: 1, Reused: 3, Incomplete or failed: 0 / Total: 4`)
- A file left without a design document is reported once, as a warning, instead of once more on the standard output
- A warning names a file whose `doc.md` was edited by hand when its design document is regenerated and the edits are replaced; a `doc.md` that is newer than its `doc.json` with the same texts gives no warning
- `codetwine.llm.client` no longer imports `openai`; `LLMClient` takes an exception of a type `litellm.LITELLM_EXCEPTION_TYPES` lists for a failed call
- JavaScript / TypeScript: a method of an object (`run() {}`, `get size() {}`) is a definition only in an object the module exports (`export default { ... }`, `module.exports = { ... }`), like an entry `run: function () {}`; the methods of an object passed to a call (`defineComponent({ data() {} })`, `app.use({ handler() {} })`) are no definitions. The exported object may be written in parentheses, with `as` or `satisfies` (`export default { ... } satisfies Config`), in a chain (`exports = module.exports = { ... }`), as either value of a conditional expression or of `||` / `??` (`export default cond ? { ... } : { ... }`, `module.exports = window.api || { ... }`) and, in TypeScript, as `export = { ... }` <!-- F-1 -->
- C#: `operator checked +` is named `operator +`, and the type in the name of a conversion operator has its blanks made one space (`operator Dictionary<int, string>`), as in C++
- The definitions that start on the same line are listed in the order they are written, each before the definitions written inside it
- `caller_usages` has one entry per dependent file and name: a name whose lines lead to two definitions of the file (an R name assigned twice, two C# overloads) gives one entry with all its lines
- C / C++: `#include "x.h"` is looked up from the directory of the file before the project root, and `#include <x.h>` is no longer looked up in the directory of the file; Python: a file in a directory without `__init__.py` looks up `import utils` from its directory before the project root
- Python: a module whose first part is a name of the standard library of Python 3.11 to 3.14 (`import logging`, `from email.utils import ...`), whichever version runs codetwine, is linked to a file under a `src/` directory or to a file whose path ends with the module path only in a directory above the file that has no `__init__.py`: `from profile.models import Profile` in `src/accounts/views.py` is linked to `src/profile/models.py`, `import logging` in `app/main.py` no longer to `tools/lint/src/logging.py`. JavaScript / TypeScript: without a config file, a package that a `package.json` around the file lists in `dependencies`, `devDependencies`, `peerDependencies` or `optionalDependencies` is not linked to a project file under the project root or a `src/` directory
- Java / Kotlin: a Java file sees a Kotlin file of the project as its top-level classes and the class of the file (`StringsKt` for `Strings.kt`, `String_utilsKt` for `string-utils.kt`, or the name `@file:JvmName` gives), whose members are the top-level functions and properties of the file: `StringsKt.shout()`, `import static a.b.StringsKt.shout` and `NameUtils.clean()` are linked, and a name a Java file writes by itself is no longer linked to a top-level function or property of a Kotlin file of its package
- Java / Kotlin: a name written with its package (`com.acme.model.Shape.MAX`, `acme.model.helper()`) is linked to the definition; a variable, parameter or property named like the first part of the package comes before it (`app.timeout` with a property `app: Server` is `Server.timeout`)
- COBOL, JCL and CSD: of several files that fit a `COPY` statement, a program (`CALL`, `EXEC CICS ... PROGRAM(...)`, `PGM=`, `DEFINE PROGRAM`), a procedure of an `EXEC` statement or an `INCLUDE` member, the one nearest to the file that names it is taken, as for a transaction and a mapset (the most leading directories in common, then the fewest other directories), instead of the first in path order outside the directory of that file; so is a file without a language read as JCL for an `EXEC` / `INCLUDE` statement. The conditions that came before the directory of that file still come before the nearest: for a `COPY` statement a file whose path ends with the name written, a file in the library of `OF` / `IN`, a file with a COBOL extension and a BMS source, and for a procedure a `.prc` / `.proc` file
- COBOL: a `COPY` statement brings in a copybook of the name before a COBOL file that defines a program of that name, also when the program is in the directory of the file that names it
- `examples/rlm_qa`: `graph_search()` keys a node as `file:name:start_line` (`file:__module__` for the code of a file outside every definition) and gives each node its `start_line`, so definitions of one name in one file are separate nodes; a dependent's usage is an edge from the innermost definition each of its lines is written in (a method rather than the class around it), not from the first definition only
- `examples/rlm_qa`: the agent's instructions and the `get_file_detail()` docstring list `same_file_usages`, `target_name` and `target_start_line`, and say that `doc` and `usage_context` can be absent and `target_context` null; the table of design document sections is left out when no file has one
- `examples/rlm_qa`: `create_qa_agent()` takes the `PythonInterpreter`; `main()` creates it, and shuts it down and closes the knowledge store when it ends
- Names used only inside their module are private (`_` prefix), and the module caches are named `*_cache_dict` (`ts_parser.parse_cache` is `ts_parser.parse_cache_dict`); `output.build_summary_map()` is `output.build_summary_dict()`, `output.build_symbol_level_deps()` is `output.build_symbol_level_dep_dict()`, `import_to_path.get_import_params()` is `import_to_path.get_import_param_tuple()` and `import_to_path.top_level_definition_names()` is `import_to_path.top_level_definition_name_list()`
- README: the limitations name the definitions after a Kotlin syntax that is not parsed, which may be missing to the end of the file (a definition holding it may extend to the end of the file); in a Python class body, a name written after the declaration of a class variable of that name, which is linked to what the file imports or defines outside the class; a C / C++ function declared before its definition in the same file, which is linked to that declaration, also from inside the function; a C / C++ variable in upper case followed by a macro (`int COUNT GUARDED_BY(mu);`), which is listed under the name of the macro; and a line `# <title of the next section>` added to a section of an edited `doc.md`, or `# Summary` added to its last section, which is taken for that heading; and a Kotlin top-level declaration written after annotations that the grammar reads as an expression, which starts at its first modifier or keyword <!-- F-2 -->
- README: a file read as a COBOL copybook or as JCL by its content has an extension the table does not list, or none; the name of the `JOB` / `JOBGROUP` statement of such a JCL file has 1 to 8 characters, and that of a JCL statement 1 to 8 characters, two such names joined by `.` (`//STEP1.SYSIN DD`) or none; a CSD file defines resources of every type
- README: `caller_usages` has one entry per dependent file and name, `callee_usages` and `same_file_usages` one per name and definition, and `caller_usages[].usage_context` is absent when the dependent file cannot be read
- README: C#: a call after a name the project does not define (`Math.Max(a, b)`) is linked to an extension method of that name, also when only the method follows a name of a namespace (`Database.Seed()` on a property of a base type, with a namespace `App.Database` in the project); R: how a name a function assigns is read before and after its assignment
- README and `.env.example`: the lower limits of `MAX_RETRIES` and `PARSE_CACHE_MAX_FILES` (0 or more), and the `max_workers` argument of `process_all_files()`; the `.env.example` example of `EXCLUDE_PATTERNS` leaves out an output directory inside the project, which is never analyzed
- README: `doc.md` is to be saved as UTF-8, and a C/C++ header whose content did not change keeps its design document when its implementation file is removed

### Removed
- `csharp_source.argument_type()`, `usages.typed_alias_type()`, `definition_source.extract_definition_source()`

### Fixed
- A file that starts with a UTF-8 BOM and is not UTF-8 is decoded without the BOM by `SOURCE_ENCODING`, the detected encoding or UTF-8 with replacement; a file that starts with a UTF-16 or UTF-32 BOM and that codec does not decode (an odd byte at the end, a lone surrogate) is decoded by that codec with invalid bytes replaced, and is listed in `detected_encoding_dict` and `detected_encoding` as `utf-16` or `utf-32`
- `process_all_files()` stops with a `ValueError` before it analyses anything when, while `ENABLE_LLM_DOC` is True, `llm_client` is `None` (instead of failing the design document of every file with an `AttributeError`), `max_workers` (`MAX_WORKERS`) is less than 1, or the template `DOC_TEMPLATE_PATH` names cannot be read as JSON or has no list of sections with an `id` of their own, a `title` and a `prompt`; and when `PARSE_CACHE_MAX_FILES` is negative or `SOURCE_ENCODING` names a codec that does not decode bytes to text (`hex`, `base64`, `rot13`, ...). `generate_all_docs()` stops on such a `max_workers` or template too
- `process_all_files()` stops with a `ValueError` before it writes anything when the project directory does not exist or is not a directory, is the file system root or has a name that is not valid UTF-8, or when the output directory is the project directory itself or its parent, also through a symbolic link; an output directory that is the project directory no longer leaves out a directory of the project that has the project's name (`requests/requests/`)
- A project directory given with a trailing separator or as `.` keeps its name: the results are written to `<output_dir>/<name>/` and the paths start with `<name>/`
- A log message of only whitespace writes no line to the console or the log file; one with an exception writes its traceback
- `is_text_file()` reads past leading whitespace longer than 8 KB instead of taking the file for empty
- A named pipe, socket or device in the project, also one named in `file_list` or an R package's `DESCRIPTION`, is skipped instead of making the run wait on it without end
- `knowledge_db.open_knowledge()` opens a database whose path holds `#`, `?` or `%`, read-only, instead of failing on a missing table and creating an empty file at the part of the path before the `#` or `?`
- A run with `ENABLE_LLM_DOC=False` takes the edits of `doc.md` into `doc.json` and the whole-project files for the files whose content has not changed since their design document was generated, as the README says
- A file whose path is not valid UTF-8 is left out with a warning instead of stopping the whole-project files with a `UnicodeEncodeError`
- A `doc.json` that cannot be read is left out of the whole-project files with a warning instead of stopping them with a `JSONDecodeError`
- `project_dependency_summary.json` and `dependency_graph.md` are written to `<output path>.tmp` and moved into place once complete, like `project_knowledge.json` and `project_knowledge.sqlite`; a stopped write leaves the previous file and no temporary file
- `project_knowledge.sqlite` keeps an empty `file_dependencies` or `doc` as `{}` instead of `NULL`
- The files of a dependency cycle documented in the same step no longer get each other's summaries, whatever `MAX_WORKERS` is
- A heading of only the section's title is taken off the start of a generated section, and a heading of only `Summary` off the start of a generated summary, with the blank lines after it; a heading that holds more than the title (`## Error Handling Policy`) and the indentation of the next line are kept
- Taking the edits of `doc.md` finds the headings of the sections and of the summary in the order of `doc.json`, past the lines of the same form the texts of `doc.json` hold, so a `# Summary` line or a `# <title>` line of another section in a section's text or in the summary, a summary that starts with its own `# Summary` line, or two sections of the same title no longer cut or mix the sections and the summary
- The last section of a `doc.md` without a summary is taken from `doc.md` up to its end, also past a `# Summary` line in it; a section next to a heading removed from `doc.md` keeps its `doc.json` text, a warning names it and `doc.md` is left as it is
- A `doc.md` that cannot be read as UTF-8 text leaves its `doc.json` as it is and a warning names the file; the design document is reused instead of failing the file on every run
- A `doc.json` that holds no JSON object, is not UTF-8 or has no sections of the form a run writes is regenerated, instead of stopping the run or failing the file on every run; an error during the generation of a document names the file, and a reply without choices counts as a failed call
- C / C++: a variable or member that is a function pointer (`int (*open)(const char *);`, `static void (*callbacks[4])(void);`) is a definition, and a member call through it (`o->open()`) is linked to the member instead of its struct; a function that returns a function pointer (`void (*handler_of(int sig))(int) { ... }`) is a `function_definition`
- C / C++: a function declared or defined with its name in parentheses (`int (name)(int x);`, `int (name)(int x) { ... }`), also after an upper-case macro (`LUA_API int (lua_absindex) (lua_State *L, int idx);`), is a function declaration or a `function_definition` instead of a variable or a function named after its type, and a call of it is linked to both, as for `int name(int x);`
- C / C++: a struct, union, enum or class written as the type of a declaration that declares no variable (`struct T { int y; } make_t(void);`, `struct Handle {};` in a class) covers only its own text: the prototype written with it is a top-level definition instead of a member of the struct, and the text linked to the struct (`target_context`) ends at its `}`
- JavaScript / TypeScript: `export = { ... }` is listed as the definition `default`, and a default export of a name in parentheses (`export default (helper);`, `module.exports = (helper);`) is linked to that name
- C++: a member of a nested class whose body is written outside its class (`class Slice::Iter { int next(); };`) is looked up in that body (`Slice::Iter.next`)
- C++: a static member defined outside its class (`int Shape::count = 0;`, `template <typename T> int Box<T>::total = 0;`) is a definition named with its scopes (`Shape::count`, `Box::total`)
- C++: a conversion operator declared in its class (`explicit operator bool() const;`) is a definition
- TypeScript: a namespace of a dotted name (`namespace Outer.Inner { ... }`, listed as `Outer.Inner`) and a `module Legacy { ... }` declaration are definitions, and a name through such a namespace (`Admin.Models.User`) is linked to the member written in it, also when another namespace of the file has a member of that name
- Kotlin: a top-level declaration written after annotations that the grammar reads as an expression without an error (`@Retention(RUNTIME) @Target(FUNCTION) public annotation class ToJson`, `@Suppress("unused") enum class Level { LOW }`, `@Suppress("unused") class Box { fun size() = 1 }`) is a definition, and the names that link to it are linked <!-- F-2 -->
- TypeScript: an enum member named by a string (`"up-left" = 1`, `'down'`) is a definition, named without its quotes
- JavaScript: the declarations inside a function that wraps a file and is called with `.call(this)` or `.apply(this, arguments)` are definitions, as in `(function () { ... })()`
- Classes and structs written on one line (`struct A { int x; }; struct B { int y; };`) keep their members and are all top-level definitions, and a member written with its class (`B.x`) is looked up inside that class also on the same line, also for C# (`B.X` of `class A { int X; } class B { int X; }`)
- C++: a class written with a macro between `class` and its name is a definition, and the members written in it are its members, whatever follows the name: base classes written in any way (`: public Base<Widget>`, `: virtual public Base`, `: public std::exception`, several bases on one line or on several lines), `final`, another macro or an empty body (`class EXPORT Tag {};`), also inside another class (`class EXPORT Inner { ... };`)
- C++: a forward declaration written with a macro (`class EXPORT Cache;`) is no definition, and the references to the class lead to the class instead of the forward declaration
- C / C++: an upper-case name declared after a macro (`EXPORT int RESET(void);`, `int STDCALL START(int a);`, `EXPORT extern const int LEVEL_MAX;`) is a definition
- Kotlin: the annotations of a getter or setter (`val size` / `@JvmName("size")` / `get() = ...`) no longer leave the definitions after them in the file missing, also where the file has another form read in another way (a call of `get` at the start of a line)
- Kotlin: a constructor written on a line after its class name (`class Reader` / `@Inject` / `internal constructor(...)`) is read in a file with `\r\n` line breaks too
- Kotlin: a secondary constructor written on the line after a nested class without a body (`private class Inner` / `constructor(y: Int) : this(y) {}`) stays a constructor of the outer class instead of extending the nested class, also where another part of the file is read in another way; a constructor written on a line after its class name is joined to it only where the grammar does not read the file as written
- A name written apart from a longer name of the same line is a usage of its own (`this.config = config.copy();`, the argument of `this.config.merge(config)`, the type and the call of `Shape s = b.build();`), also in the arguments of a Rust macro (`Config` of `println!("{:?} {}", Config { n: 1 }, Config::size())`) and in a Python annotation written as a string (`core` of `"core.Engine | core"`); only the parts of one written name (`a` and `a.b` of `a.b.c`) are left out
- Python: a name a class body or the top level of a file assigns (`validate: bool = True`, `a, b = 1, 2`) is no usage of the name, also when an import binds it; an assignment in a function to a name declared `global` stays a usage. Kotlin: the name a `val` or `var` declares is no usage
- `same_file_usages` leaves out a reference only inside the lines of the definition it leads to: a call of another overload (C#, Kotlin), a call of a function of the file from a method of the same name (Python, JavaScript) and the struct written in one of its `impl` blocks, the line `impl Config {` or `impl Display for Config {` included (Rust, as an `impl` block of another file is in `callee_usages`), are listed
- The name a definition of the file is written with is no usage, neither of another definition of that name nor of a name the file imports: a type, function, macro or `typedef` defined in each `#if` branch or `#[cfg(...)]` alternative (`#[cfg(unix)] pub struct Handle` and `#[cfg(windows)] pub struct Handle`, also next to `#[cfg(unix)] use sys::Handle;`), in two modules, namespaces or classes (`namespace A { export class User {} }` and `namespace B { export class User {} }`), and a TypeScript declaration that merges with another (`interface Options` written twice, `class Box` with `interface Box`). The name of a second definition written with its scopes outside its class (`W::W(double b) {}` after `W::W(int a) {}`) is no usage of the first; the type an `impl` block is written for stays a usage of the type. JavaScript / TypeScript: the name of an overload signature, of a generator function and of a named function expression is no usage
- `same_file_usages` tells apart two definitions of one name that start on the same line: a reference to one written inside the other (`return A.run();` inside the `run` of `class B` that starts on the line of `class A { static run() {} }`) is listed
- `caller_usages[].usage_context` holds the code around two near usage lines once instead of twice
- JavaScript / TypeScript: `"."` and `".."` (`import { a } from ".."`, `require("..")`) are resolved like `"./"` and `"../"`
- TypeScript: `import x = require("./m")` is an import that binds `x` to the module
- JavaScript / TypeScript: a `require()` or `import()` of a template string without `${}` (``require(`./m`)``) is an import
- TypeScript: a module written without an extension is resolved to a declaration file (`./types` to `types.d.ts`, `./api` to `api/index.d.ts`; `.d.mts` and `.d.cts` for `.mjs` and `.cjs`)
- JavaScript / TypeScript: a package of the project (`require("mylib")`) is linked to the file its `exports` or `main` names, and a relative module that names a directory with a `package.json` (`require("../")`, `require("../pkg")`) to the file its `types`, `typings`, `source`, `module` or `main` names before the `index` file of the directory; from a JavaScript file a declaration file (`.d.ts`) is taken only when no other file fits, and from a TypeScript file it comes first
- A relative module (`from .b import x`, `require("./util")`, `#include "../x.h"`) is looked up from the directory of the file only: no longer under a `src/` directory (`./util` of `app/main.js` to `src/app/util.js`)
- `vite.config.*`: an alias written as a string starting with `/` (`"@": "/src"`) is read as a path from the directory of the config file
- A member of a class written after `self` / `this` is linked to the member also when an import of the file binds a name like it (`from .rules import validate` with a method `validate`, `from os import path` with a field `path`); in Java, Kotlin and C++ so is a member written by its name alone inside its class, a call to a method of the class (not one declared without parameters for a call with arguments; a string written in an annotation of the method is not read as its parameters) and any other name to a member that is no method. A Java `import static a.Util.*` no longer takes the members of a class nested in `Util`
- C++: `this->x` inside a member function defined outside its class is linked to the member `x` of the class; Rust: `self.x()` inside an `impl` block of a type of another file is linked to the method of the type, also when a third file defines it
- C / C++: a function a header declares is no longer linked to a member function of the same name written inside a class of a file that includes the header
- C++: a function a header declares in a namespace is linked to its definition written with the namespace (`int geo::area() { ... }`), and a member defined with its classes (`int Table::Cmp::make() { ... }`) leads to that definition (`target_name`, `target_start_line`) instead of none
- C++ / Rust: a method called on a variable whose type is written with its class or module (`const Table::Cmp& c; c.compare()`, `c: &shape::Circle; c.area()`) is linked to the method
- A comment at the end of the first line of a class (`class Cache:  # ...`, `class Cache extends List { // ...`) no longer makes the names on the next lines classes the class is made from
- A class written before or after another one on the same line is no longer taken as a class inside it or around it for `self.x` / `this.x`, and a member reached through the classes around a usage leads to that member also when another member of its name starts on the same line
- Java / Kotlin / C++: inside a class, a nested class is linked before a class of its name the file imports, sees in its package or includes, also in a Kotlin call that makes an object (`Entry("a", 1)`) and for a member of a variable of its type (`root.v`, `other.size()`). C++: a member written by its name alone in a nested class is linked to the member of that class instead of the member of an included class of the same name, and so is a member written by its name alone in a function defined outside its class of the same file (`int Box::fit(int v) const { return clamp(v); }`), before a function of that name a header declares. Rust: `self.name` in an `impl` block of a type of the file is linked to a method an `impl` block of another file defines for the type
- Rust: a file several crate roots declare as a module (`mod common;` in `tests/a.rs` and `tests/b.rs`, or in two files under `src/bin/`) is linked from each of them, instead of from the first only
- Rust: a use declaration inside an inline module (`pub mod prelude { pub use crate::shape::Circle; }`) is followed: `use crate::prelude::Circle`, `crate::prelude::Circle::new()` and a path through a glob import of the module lead to the definition instead of the inline module; a name it re-exports from outside the project leads nowhere, and `use crate::prelude::Kind::*` takes the variants of an enum of the inline module. A path of such a declaration that starts with a name of the inline module (`pub use imp::g;` for a `mod imp` inside it) is followed from the inline module
- C#: a call written after a namespace, an alias (`System.IO.File.Exists(path)`, `IO.File.Exists(path)` with `using IO = System.IO;`) or `global::` is no longer linked to an extension method of the same name
- C#: the type of an argument written as a name, by which an overload is chosen, is taken from the declaration the name refers to: the variable of the block the call is in, not one of the same name in another block; a field or property of the type the call is in, not one of another type of the namespace or of a nested type; a parameter of the primary constructor of that type (`record Rec(Box B)`, `class Prim(Box pc)`); a typed `foreach` or `catch` variable. A name a base type declares has no type
- C#: a call of a method declared in several files of a partial type leads to the file of the overload it takes (`Run(1)` to `Run(int n)` of the other file) instead of the declaration in the calling file or every file
- C#: an overload is chosen with a type of `System` written by its name as its keyword (`String` as `string`, `Int32` as `int`, `Object` as `object`), a `byte`, `sbyte`, `short` or `ushort` argument is passed as a wider number type, and a hexadecimal or binary literal with a suffix (`0x10L`, `0b1u`) has the type of its suffix
- C#: a `params` parameter is a local name: it is not linked to a field or property of the same name, and its type is not taken from such a member
- C#: a parameter whose type is a type parameter of its class (`void Put(T v)` in `class Box<T>`) fits any argument when an overload is chosen
- R: `library(pkg, character.only = FALSE)` and `require(pkg, character.only = F)` attach the package `pkg`; with `character.only = TRUE` (or `T`) only a string names the package, and a name given there is a reference to the variable that holds it. `x::library(pkg)` attaches no package, and its arguments are references
- R: a name a function, or a top-level statement outside its functions, reads before it assigns the name (`cfg <- modifyList(cfg, extra)`, `load_data <- load_data(path)`) is linked to the definition outside, and so is a name read outside the functions of a file before the file assigns it (`data <- subset(data, x > 0)` after `source("load.R")`). The code given to `on.exit()`, `withr::defer()` and `withr::defer_parent()` and the default values of the parameters see every name the function assigns
- R: a name assigned inside the arguments of a top-level call (`test_that("...", { x <- lrn("a") })`, `on.exit({ })`, `tryCatch({ })`) is no longer a definition of the file, so the other references of the file to the name lead to the definitions of the testthat helper, the package or the sourced script instead of that line
- COBOL: a copybook that starts with `EJECT`, `SKIP1` to `SKIP3`, `TITLE` or a `REPLACE` statement is read by the statement after it (its data items are definitions), and a `REPLACE` statement between a data item and a `COPY` statement no longer keeps the item from taking the copybook as its items, as `EJECT` does not
- COBOL: a copybook that starts with a header of the environment division (`INPUT-OUTPUT SECTION.`, `FILE-CONTROL.`, `SPECIAL-NAMES.`, ...) is read as the environment division instead of making its headers sections and paragraphs
- COBOL: a fixed-format file whose sequence numbers are aligned to the right without leading zeros (`    10 IDENTIFICATION DIVISION.`) is read as fixed format: whether columns 1 to 6 hold code or a sequence number is decided by the lines before the first format directive, not by the first line that can hold code there
- COBOL: a literal on a long line (free format, or fixed format without the right margin) is read whole, and a `CALL` or `TRANSID` of a data item given it leads to the program it names instead of a name cut short
- COBOL: the picture string after `PIC IS` / `PICTURE IS` is read as one, so an item whose picture string has a character outside ASCII (`PIC IS ９(5)`) is parsed
- COBOL: the paragraph or section `EXEC SQL WHENEVER ... GO TO` / `GOTO` names is a usage of it
- COBOL: the last section and paragraph of the declaratives end before `END DECLARATIVES`
- COBOL: `COPY X OF "lib/copy"` (a library written as a directory path, with `/` or `\`) leads to the copybook in a directory whose path ends with it
- COBOL: a file without a language that one `COPY` statement names with its whole file name (`COPY "CUSTREC.txt"`), but whose text is not read as COBOL, is no longer the copybook of another `COPY` statement that names it without its extension (`COPY CUSTREC`); that statement leads to the copybook it fits, whichever file is read first
- BMS: the continuation line of a statement whose operands end with a blank not after a comma is a remark, so its words no longer join the value of the last operand (`GRPNAME=DATE` followed by a remark, `OCCURS=2` followed by a remark)
- A module looked up under the source roots (`src/`, `src/main/java/`, ...) is found through an index of the paths under them, in a time that does not grow with the number of source roots
- Analyzing the references of a project takes time and memory in proportion to the number of files where every file sees every name of the others (SQL, the files of one Java / Kotlin package, C / C++ files that include one header that includes all others)
- `add_file_name()` takes time in proportion to the number of files of the same name
- Rust: resolving the paths of a crate whose modules all glob-import a module that glob-imports them (`use crate::prelude::*;`) takes time in proportion to the number of modules instead of its square or more
- C#: reading the argument types of the calls of a file takes time in proportion to its size instead of its square (a type with many methods, a method with many statements)
- C#: reading the declarations of a file with many types or methods in one namespace or type takes time in proportion to their number
- R: resolving the `mod$f` references of a file to a box module, or to several project packages of one name bound with `box::use(pkg)`, takes time in proportion to the number of references instead of references times names of the module
- R: reading the definitions and references of a file reads each top-level statement once
- Kotlin: reading the constructors written on a line after their class names no longer counts the lines from the start of the file for each of them
- Kotlin: reading a file that has a part the grammar does not read no longer takes a time that doubles with each lower-case letter at the start of the line in front of a `constructor` line (`protected open lateinit var subscriptions: Binding`)
- The encoding of a file is detected once per run instead of twice
- A file whose syntax tree or definitions cannot be read is reported once instead of once for every name another file uses from it
- `examples/rlm_qa`: `graph_search()` follows a usage to the definition its `target_name` and `target_start_line` name instead of the name the file writes (`a.helper()`, `Type.method()`), follows the usages within a file (`same_file_usages`), and lists an edge once
- `examples/rlm_qa`: the store of a `project_knowledge.json` finds definitions in the order of file path and start line, and compares a partial name after `str.casefold()`, as the store of a `project_knowledge.sqlite` does
- `examples/rlm_qa`: `read_source_file()` reads only paths inside the output directory of the knowledge file
- `examples/rlm_qa`: `search_text()` with a `limit` of 0 or less returns no hits
- `examples/rlm_qa`: the interactive loop ends at the end of input (Ctrl-D)
- README and `.env.example`: `SUMMARY_MAX_CHARS` and `CODE_SUMMARY_MAX_CHARS` are the character counts the LLM is asked for; a longer summary is kept as it is
- README: the `file` of `doc.json` is the path relative to the project root, not the path of the copy in the output directory
- README: the whole-project files list the files analyzed except those in `dependency_fail_list`, which the `callee_usages[].from` and `caller_usages[].file` of the other files can still name
- README: `#include` is also looked up under the `src/` directories of the project
- README: the RLM QA agent calls the models of the `LLM_MODEL` and `SUB_LLM_MODEL` constants of `rlm_qa_agent.py`, not the `LLM_MODEL` of `.env`

## 0.4.5 - 2026-10-26

### Added
- `uv.toml`: when uv resolves the versions `uv.lock` records, it picks no version published less than a day ago, and uv older than 0.11.8 refuses to run in the project

### Changed
- `uv.lock` records the latest versions of the dependencies published a day ago or earlier, with litellm 1.105.4 and tree-sitter 0.25.2

### Fixed
- tree-sitter 0.26.0 is not installed (`tree-sitter>=0.25.2,!=0.26.0`): with it, the analysis of a project could end with a segmentation fault

## 0.4.4 - 2026-10-24

### Added
- Design documents: `DOC_EFFORT` (`off`, `low`, `medium`, `high`; default `low`) sets how much the model thinks: `off` sends `thinking` disabled (the `low` form to a model litellm's `AnthropicModelInfo._is_always_on_thinking_model` says always thinks, and after a refusal of it), the others adaptive thinking with `output_config.effort`; `LLMClient(effort=...)` takes it per client and stops with a `ValueError` on any other value. A model that refuses these parameters (a `BadRequestError` naming `thinking`, `effort` or `output_config`, `UnsupportedParamsError` included) is called again with the next form or without them, and the client sends the refused form no more

### Changed
- litellm 1.101.0 or later is required
- Design documents: every call asks for adaptive thinking at effort `low` unless `DOC_EFFORT` says otherwise, and the thinking counts toward `DOC_MAX_TOKENS`

## 0.4.3 - 2026-10-21

### Added
- CSD: CICS resource definitions (DFHCSDUP input, `csd` in any case, whose first line that is no blank or comment is a DFHCSDUP command; another `.csd` file is analyzed without a language unless it is read as a COBOL copybook or as JCL, `jcl_member.no_csd_file_list()`) are analyzed without a grammar (`csd_source.read_csd_source()`, `parsers/csd_command.py`): each resource a `DEFINE` or `USERDEFINE` command defines is a definition (`define_` + the resource type: `define_transaction`, `define_program`, `define_mapset`, ...; with `name_line`); commands and resource types may be shortened (`DEF`, `TRANS`, `PROG`, `TD`) and written in any case, and a value may go on with the next line after `*`
- CSD: `DEFINE PROGRAM` and the `PROGRAM` of `DEFINE TRANSACTION` depend on the COBOL program, `DEFINE MAPSET` on the BMS source that defines the mapset (no `target_context`), and an attribute that names a resource of the same file (`PROGRAM(P)`, `TRANSID(T)`, `REMOTESYSTEM(C)`, ...) is a usage of it, of the same `GROUP` first
- JCL: the CSD commands of the in-stream `SYSIN` data of a `PGM=DFHCSDUP` step give the same dependencies on programs and mapsets
- COBOL: `EXEC CICS ... TRANSID(...)` (a literal, or the literals a data item is given in the same file) depends on the CSD file whose `DEFINE TRANSACTION` defines the transaction (`target_context` the command), or on the JCL file whose `PGM=DFHCSDUP` step defines it (no `target_context`); of several such files, the one whose directory shares the most leading directories with that of the COBOL file, then the one with the fewest other directories (`cobol_file_index._transid_path()`); a mapset of a CSD command goes to the nearest BMS source in the same way

### Changed
- Design documents: the files of a dependency cycle start at the level after the files outside the cycle they depend on, those that depend on the fewest other files of the cycle first, and the files that depend on them come after them; a change regenerates the documents of the files that depend on the changed file through a cycle as well

### Fixed
- JCL: whether a file is JCL is decided from columns 1 to 71 of its first statement, the columns its statements are read from; a line with text only in columns 72 to 80 is skipped

## 0.4.2 - 2026-10-21

### Added
- JCL: jobs, procedures and `INCLUDE` members (`jcl`, `prc`, `proc` in any case) are analyzed without a grammar (`jcl_source.read_jcl_source()`): jobs, procedures, named steps and the symbols `PROC` and `SET` statements give a value are definitions (`job_statement`, `proc_statement`, `exec_statement`, `symbolic_parameter`, with `name_line`)
- JCL: a step depends on the program it runs (`PGM=`, `RUN PROGRAM(...)` in the in-stream `SYSTSIN` data of a TSO batch step or of a step that calls a procedure, the program in the `PARM` of an IMS batch step), an `EXEC` statement on the procedure it calls (an in-stream procedure of its job, else the file named like it that is no job, a `prc` / `proc` file first; the PROC statement that starts the file whatever its name) and an `INCLUDE` statement on its member; a symbol in those names stands for the values the file gives it (`SET` statements of the same procedure or job before it, in-stream procedures of the same job, `PROC` defaults, the `EXEC` statements that call an in-stream procedure)
- JCL: the `INCLUDE` members (with no `target_context`), the steps named in `COND` parameters, `IF` statements and referbacks (`*.STEP.DD`), the steps and symbolic parameters of a called procedure named on its `EXEC` and `DD` statements (`PARM.STEP=`, `//STEP.DD DD`, `MEM=value`) and the symbols of the operands (`&HLQ`) are usages, resolved by `jcl_reference.jcl_reference_target_list()`
- JCL: a file without a language whose first statement is a `JOB` or `JOBGROUP` statement in upper case, or that an `EXEC` or `INCLUDE` statement of a JCL file names and whose first statement is a JCL statement in upper case, is analyzed as JCL (`language` `jcl`, `jcl_member.register_jcl_target()`); a `jcl` / `prc` / `proc` file whose first statement is no JCL statement is analyzed without a language (`jcl_member.no_jcl_file_list()`)
- The operands of BMS macros and JCL statements are split by `parsers/operand.py`, and the file names of COBOL and JCL files are indexed by `file_utils.add_file_name()`

## 0.4.1 - 2026-10-04

### Added
- Kotlin: a file the grammar does not read as written is parsed again with a constructor written on the lines after its class name (`class Reader` / `internal constructor(`) joined to the class, and with a body written on one line (`class A { val x = 1 }`) closed by `;`; a parse is kept only when it leaves less of the file unread (`parsers/kotlin_form.py`, `parsers/parse_error.py`)
- Kotlin: a class declaration the grammar reads as part of an ERROR node is a definition, and a class whose body it reads as an ERROR node covers that node, so the members are no top-level names of the file
- Kotlin: `value.name()` and `value.name` are linked to an extension function or property `name` the file sees, unless a class of the project has a member of that name (`extension_call` of the usage settings, `DefinitionInfo.has_receiver`, `ImportBinder.receiver_name_set()`, `ImportBinder.package_member_name_set()`)
- Kotlin: `"$name"` in a string is a usage of `name`, a type written with the type it is a member of (`Shape.Empty`) is listed and linked as that member, and a name written through the companion object of a class (`import a.Shape.Companion.unit`) is linked to the member
- C/C++: a function written with a macro that takes its name (`TEST_IMPL(ping) { ... }`) is the definition `ping`, and a C++ conversion operator the definition `operator bool`
- C/C++: an upper-case macro name written as a line of its own (`NODE_FIELDS` in a struct body, `BEGIN_DECLS`) or next to the type of a declaration (`EXPORT int f(void);`, `int STDCALL f(int a);`) is read as spaces where the grammar misreads it and that leaves less of the file unread; the name stays a usage of the macro on its line (`parsers/c_macro.py`)
- C++: the member and the base class a constructor initializes (`Circle::Circle() : Shape(1), r_(0) {}`) are usages (`identifier_parent_dict` of the usage settings)
- Java: the constants of an interface are definitions (`constant_declaration`)
- Java: a type written with the type it is a member of (`Circle.Builder`), an annotation written that way (`@Shape.Marker`) and a method reference (`Shape::area`) are listed and linked as that member; a type written with its package (`com.acme.model.Circle`) is listed as `Circle` (`attribute_skip_parent_types`, `package_path_types` of the usage settings)
- TypeScript: a type written with a namespace import (`T.Options`) is listed and linked as that member
- JS/TS: class members written with a private name (`#count`, `#check()`) are definitions
- JS/TS: a module passed on as a member (`exports.util = require("./util")`, `module.exports = { util: require("./util") }`) is followed to that file (`imports.member_module_export_dict()`)
- JS/TS: a name bound to the default export of a CommonJS module (`import util from "./util"`) is followed through the names the module exports (`ImportBinder.module_member_binding()`)
- Python: the class a `case` pattern names (`case Point(x=0):`, `case core.Point():`) and a dotted constant in a pattern (`case Color.RED:`) are usages (`name_path_dict` of the usage settings)
- Python: the names of an annotation written as a string (`e: "Engine"`, `-> "list[core.Config]"`) are usages; the arguments of `Literal[...]` are not (`annotation_string_dict`, `annotation_literal_names` of the usage settings)
- Python: a `type` alias with type parameters (`type Pair[T] = tuple[T, T]`) is a definition
- Rust: a parameter or a `let` variable written with its type is named after the type (`c: &Circle; c.area()` is `Circle.area`), also for a type the file defines itself (`typed_alias_parent_types` of the Rust usage settings)
- A member written after `self` / `this`, and in Java, Kotlin and C/C++ a name written by itself inside a class, is linked to the member of the class it is written in, then of a class of the file named in the head of that class (`class Child(Base)`, `class B extends A`, `impl Runner for Config`), then of a class of the file made from it; `target_start_line` is the line of that member (`UsageInfo.is_member`, `ImportReferenceTarget.definition_line`, `definition_source.member_path_definition()`; `member_by_name` and `class_head_argument_skip` of the usage settings)
- A variable declared in a block counts for that block: outside it, its name is a usage again (JS/TS `let` / `const` / `catch` / loop variables, Java, Kotlin, C/C++, Rust; `block_scope_types`, `function_binding_types` and `function_binding_token_types` of the usage settings). JS/TS `var` and function declarations count for the whole function
- A field or a variable of the file declared with a type of the project is listed under its own name as well as under the type (`engine.run()` is `Engine.run` and `engine.run`; `TypedAlias.is_file_level`)
- A variable of a function declared with a type is named after the type also when an import brings in a name spelled like the variable
- Java: the variable of a `for (Shape s : shapes)` loop, of a `try (...)` resource, of `x instanceof Shape s` and of `case Shape s` is named after its type (`typed_alias_type_field_dict` of the usage settings)
- Kotlin: a property declared in a primary constructor with its type (`class Car(val engine: Engine)`) is named after the type (`engine.run()` is `Engine.run`)
- Kotlin: a file the grammar does not read as written is also parsed with the white space in front of a call of `get` that starts a row read as `;` (`val service by inject<Service>()` followed by `get("/path") { ... }`), and a function declaration with a body that the grammar reads as part of an ERROR node is a definition whose body is not searched
- C/C++: an upper-case macro written after a declarator (`void Lock() ACQUIRE() { ... }`, `int size_ GUARDED_BY(mu_);`, `void Stop() NOEXCEPT_MACRO;`, `Status Read(int* out)` followed by `REQUIRES(mu_);`) is read as spaces where the grammar misreads it; the macro and the names written in its arguments stay usages on their lines
- C/C++: a backslash in front of a line break written as `\r\n` continues the line (a string or a macro written over several lines)
- C/C++: a variable made with arguments inside a function (`Engine e(1);`) and the variable of a range `for` (`for (Shape& s : shapes)`) are named after their type; a variable declared with a `typedef` name of the file (`typedef struct node { ... } node_t;`) is named after it
- JS/TS: a class written as the value of a declaration or an assignment (`const Shape = class { ... }`, `module.exports = class { ... }`, `exports.Shape = class { ... }`) is a definition of type `class`, and its members are definitions
- Rust: an associated type declared in a trait (`type Output;`) is a definition (`associated_type`)
- Design documents: a prompt that exceeds the context window is reduced through more stages, in this order: caller source code, dependency summaries, summaries of large dependency symbols, dependency source code, the usage lists, summaries of the large definitions of the implementation file of a header, that implementation file, summaries of the large definitions of the file, summaries of runs of its lines (`doc_creator._PromptReducer`). A stage that changes nothing is passed over without a call
- Design documents: `CODE_SUMMARY_PIECE_LINES` (default `200`), the lines summarized together when a file still exceeds the context window after its large definitions are summarized; a run starts where a definition starts, also in a source whose large definitions are summarized already, and the runs are summarized again while the source gets smaller. A value less than 1 stops the run before anything is analysed, unless `ENABLE_CODE_SUMMARY` is `False` (`doc_creator.check_code_summary_setting()`)
- Design documents: the number of code blocks a reduction stage is about to summarize is printed (`Summarizing definitions (127): shell.c`)
- Design documents: a code block whose summary prompt exceeds the context window is summarized in halves (`doc_creator._generate_by_half()`), and so is the summary of a document: each section is summarized by itself first

### Changed
- JS/TS: a name bound by `require()` or `await import()` (`const m = require("./m")`, `const { a, b: c } = require("./m")`, `const run = require("./m").run`) is no longer listed in `definitions`; a file that passes such a name on (`module.exports = { m }`) leads to the file the import brings it from
- JS/TS: a name the file defines only as a member of another object (`req.accepts = function () {}`, `exports.run = ...`) is a usage after `this`, not when written by itself (`member_definition_types` of the usage settings)
- C/C++: `typedef struct node { ... } node;` is listed once, as the struct; a typedef name that differs from the name of its struct, union or enum is listed as before
- JS/TS: a namespace import written by itself or with a member the file does not export (`import * as ns from "./m"; ns`) no longer leads to the default export of the file; only the value of a CommonJS module (`module.exports = ...`) is followed that way (`ImportBinder.module_value_binding()`, in place of `default_binding()`)
- Python, JS/TS, Rust: a name the file defines only as a member of a class is a usage after `self` / `this` (in Python also where it is written in the body of the class that defines it), not when written by itself: `open("x")` beside a method `open` is no usage of the method. In the arguments of a Rust macro, a method called on a value (`assert!(v.len() > 0)`) is a usage only after `self`
- A name written by itself that several definitions of the file carry leads to a top-level one first, then to one written inside a module or namespace, and to a member of a class last
- JS/TS: an export whose value is a name (`exports.run = run`, as before `module.exports = run`) is no definition; the named definition stands for the export
- C/C++: a macro call written as a statement (`MODULE_INIT(setup);`, `DISALLOW_COPY(Shape);`), which the grammar reads as a declaration without a type, is a usage of the macro and no definition (`definitions.is_macro_call_declarator()`, `macro_declarator_types` of the usage settings)
- C/C++: the declarations inside a statement the grammar reads outside a function (`if`, `for`, `while`, `switch`, `do`, `try`) and inside a block that is the body of no function are not definitions, unless the block follows the head of a namespace, a type or a linkage specification
- Kotlin: an `enum_entry` is a definition only in the body of a class declared with `enum`
- A name written with `::` is not named from a part that is a variable of a function (`log::Writer` beside a variable `log`)
- Design documents: a definition that holds other definitions (a class) is no longer replaced by one summary; its large methods are, and the lines of the class around them are kept (`doc_creator._large_leaf_definition_list()` in place of `_select_outermost_large_definitions()`). A definition that shares a line with another one (several on one line, one starting on the last line of the one before it) is left to the summaries of runs of lines, and definitions of the same lines are summarized once under all their names. A summary replaces a code block only when it is shorter
- Design documents: the sections of a file after the first start from the stage the section before them fit at (`doc_creator._generate_section()` in place of `_generate_section_with_fallback()`)
- Design documents: `CODE_SUMMARY_TRIGGER_LINES` counts a line longer than 80 characters as one line per 80 characters
- `LLMClient.generate()` returns `None` for a reply without text
- `ts_parser.class_macro_cache` is `ts_parser.blank_macro_cache`: it holds every macro name read as spaces
- `extract_usages()` reads each node through a handler looked up by its node type

### Fixed
- The declarations inside the body of a function that gives no name were listed as definitions of the file (C/C++: a function written with a macro, a conversion operator; JS/TS: a method named by a string or a computed key; Kotlin: a secondary constructor); a definition whose name the grammar reads as missing is no longer listed with an empty name
- C++: inside a member function defined outside its class and inside a class with a base class, the name of the class or of a base class led to its constructor, so `int Shape::count() { ... }` was linked to the constructor declaration in place of the declaration of `count`
- C/C++: the line of a macro name read as spaces was counted from the start of the file once per name
- A member written after `self` / `this` led to the first definition of its name in the file, also when the class it is written in defines the member, and to a definition that is no member (a function of the file named like an attribute)
- A variable declared in a block hid the usages of its name in the whole function (`for (const path of list) { ... }` followed by `path.join(...)`)
- A member written after `self` / `this` was named after the type of a parameter or local variable of the same name
- Java: `Shape::area` listed `area` by itself as well, which led to a method `area` of the file
- Kotlin: the name of a parameter of a primary constructor (`class P(val b: Int)`) was a usage of a definition `b` the file sees
- C++: the name of a parameter declared as a reference (`void f(const Key& key);`) was a usage of a definition `key` the file sees
- Rust: `type Output;` in a trait was a usage of an associated type of the same name
- Design documents: an LLM call that failed for another reason than the context window (a rate limit past its retries, a connection error) was handled like an overflow, so the section was written from a reduced prompt; such a call now ends the section, and a failed summary call no longer replaces the code by a note
- Design documents: a prompt stayed too long, and the file got no document, when the implementation file of a header was large, when the file had many small definitions or long lines and no large definition, or when it used many small dependency symbols
- Design documents: a summary prompt that exceeded the context window left the summary empty, and the document was generated again by every run
- Design documents: a request the provider refused for its size (HTTP 413, `request_too_large`, a `Request too large` rate limit error) ended the section, the rate limit error after `MAX_RETRIES` waits; it is now handled like a prompt that exceeds the context window (`LLMClient` raises `ContextWindowExceededError`)
- A reply whose text is missing raised an exception that ended the document of the file

## 0.4.0 - 2026-10-04

### Added
- C/C++: a class, struct or union declared with a macro between the keyword and its name (`class EXPORT Shape : public Base { ... }`) is read as that type: the macro name is read as spaces before parsing, and stays a usage of the macro on its line (`ts_parser.class_macro_cache`)
- C++: inside a member function defined outside its class and inside a class, a member of the class or of a base class written by its name alone is linked to that member, and a member written with a class that only a base class declares is linked to the base class (`ImportBinder.member_scope_list()`, `ImportBinder.inherit_binding()`)
- Python: `from m import *` takes the names the `__all__` of `m` lists (`__all__ = [...]`, `+=`, `.extend([...])`, `.append("...")` with string constants), else the names of `m` that do not start with `_` (`imports.python_all_name_list()`, `ImportBinder.star_name_set()`)
- Python, JS/TS: a variable given an object of a class inside a function is named after the class from that line on (`e = Engine(); e.run()` and `const e = new Engine(); e.run()` are `Engine.run`) until it is given anything else (another value, a destructuring, the variable of a loop or of a `catch`), as is a variable or parameter annotated with a class in Python and TypeScript, which keeps that class (`usages.extract_value_aliases()`; `typed_alias_name_field_dict`, `typed_alias_value_dict`, `typed_alias_new_dict` and `typed_alias_class_only` of the usage settings)
- A variable declared with a type the file defines itself is named after that type, as one declared with an imported type is (Java, Kotlin, C/C++, Python, TypeScript)
- JS/TS: a module that starts with `#` is resolved through `imports` of the nearest `package.json`, and a module that names a package of the project through `exports`, `types`, `module` and `main` of its `package.json`, with a build output path looked up under `src/` (`package_path.py`)
- JS/TS: of the `tsconfig.json` / `jsconfig.json` files, the one that covers the file through `files`, `include` and `exclude` is taken, also among the files the nearest one names in `references`
- JS/TS: `resolve.alias` of the nearest `vite.config.*` / `webpack.config.*` is read for aliases written as a string, `path.resolve(__dirname, ...)`, `path.join(__dirname, ...)` or `fileURLToPath(new URL("...", import.meta.url))` (`alias_path.py`)
- JS/TS: `require("./m").run()` is linked to `run` of the file, and the first parameter of the callback of `import("./m").then((m) => ...)` is bound to the file for the lines of the callback (`ImportInfo.is_use`)
- JS/TS, Python definitions: a destructuring target with a default value or a rest (`const { a: b = 1, c = 2, ...rest } = obj`, `first, *rest = values`)
- Rust: a name a pattern of `match` / `if let` / `while let` / `let` binds is no usage of a name of the file, and a constant, static, struct or enum variant written in a pattern is (`pattern_reference_types`, `pattern_variant_types` of the usage settings)
- Rust: `Self::NAME` inside a `trait` is read as `Trait::NAME`
- Rust: inside an inline module without `use super::*`, a top-level name of the file other than a `macro_rules!` macro and a name a `use` declaration of the file binds are not linked (`ImportBinder.module_scope_list()`, `module_open_types` of the usage settings)
- C#: among the declarations of a method that take the number of arguments of a call, the one whose parameter types fit the arguments is taken when the file tells their types, a parameter of exactly the type of its argument before a wider one (`CsharpMember.parameter_type_tuple`, `CsharpReference.argument_type_tuple`)
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
- A project file named `file_dependencies.json`, `doc.json` or `doc.md` is not copied to its output directory, where the copy replaced the dependency information of the file
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
