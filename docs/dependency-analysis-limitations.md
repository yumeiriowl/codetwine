# Dependency Analysis Limitations

Dependencies are extracted by static analysis of the source text. This page lists, per language, the cases where they are missing, incomplete or linked to more files than the code uses.

## Common to All Languages

- **Dynamic imports**: module names built at run time are not followed: Python `importlib.import_module(name)` and `__import__(name)`, JavaScript/TypeScript `import(variable)` and ``require(`./${name}`)``, Java `Class.forName("com.example.Foo")`.
- **`self` / `this`**: `self.name`, `cls.name` and `this.name` are not linked to a member inherited from a class of another file (except in C++, from a class the file sees through `#include`). Outside a class (`this` in a function assigned to a prototype or in an object literal), they are linked to the definition `name` of the same file, whatever class it belongs to.
- **Detected encodings**: a file that neither UTF-8 nor `SOURCE_ENCODING` decodes is decoded with a detected encoding, which can be wrong: comments and strings may be garbled and definitions lost. Set `SOURCE_ENCODING` for such files. A file with a UTF-16 or UTF-32 BOM that its codec does not decode is read with invalid bytes replaced. These files are listed in `detected_encoding_dict` and carry `detected_encoding`.
- **File paths**: a file whose path is not valid UTF-8 is not analyzed; a warning names it.

## Python

- **Import roots**: modules are found from the file's directory, the project root, `src/` directories and by the end of the project paths (`from app.models import User` can lead to `backend/app/models.py`). Roots added through `PYTHONPATH` or `sys.path` are not read.
- **Modules of the same name**: a module that both a project file and an installed package provide (`import utils` with a `utils.py` in the project) is linked to the project file.
- **`__all__`**: an `__all__` built at run time is not read; `from m import *` then brings in every name of `m` that does not start with `_`.
- **Method calls on values**: `e = Engine(); e.run()` is listed as `Engine.run` only when the variable is assigned an instance of the class, or annotated with it, in the same function. The nearest assignment above the line counts, whatever branch it is in. Function results, attributes (`self.engine.run()`) and variables assigned at the top level of the file are not followed.

## JavaScript / TypeScript

- **Path aliases**: non-relative modules are resolved through `paths` and `baseUrl` of the `tsconfig.json` / `jsconfig.json` that covers the file. `extends` that names a package is not followed. In `vite.config.*` / `webpack.config.*`, only simple `resolve.alias` entries are read (a string starting with `.` (or with `/` in `vite.config.*`, a path from the config file's directory), `path.resolve(__dirname, ...)`, `path.join(__dirname, ...)`, `fileURLToPath(new URL("...", import.meta.url))`).
- **Modules of the same name**: without a config file, a non-relative module whose package is not listed in a nearby `package.json` (a Node.js built-in, an unlisted package) is linked to a project file of that path under the project root or `src/` (`import fs from "fs"` with `src/fs.js`).
- **Packages**: `workspaces` and `pnpm-workspace.yaml` are not read. A module is linked to the project's `package.json` that has its `name`, wherever it is (also a test fixture named like an installed package). From a TypeScript file, a package is linked to its declaration file (`types` / `typings`) when it has one.
- **Imports not bound to a name**: a `require()` or `import()` whose result is not bound to a name adds a dependency on the file only, except `require("./m").run()` and the callback of `import("./m").then((m) => ...)`.
- **Method calls on values**: `const s = new Store(); s.get()` is listed as `Store.get` only when the variable is given the object with `new` in the same function, or is annotated with the class in TypeScript. Any other value is not followed.
- **Definitions**: declarations inside a callback (`describe("...", function () { ... })`) are not listed. Entries and methods of an object are listed only in an exported object (`export default { ... }`, `module.exports = { ... }`). An unnamed default export is listed as `default`. A property declared in TypeScript constructor parameters (`constructor(public r: number)`) is not a definition, so `this.r` is not linked.

## Java / Kotlin

- **Packages**: a file reached through a wildcard import or the same package is a dependency only when one of its names is used.
- **Kotlin seen from Java**: Java sees a Kotlin file as its top-level classes and its file class (`StringsKt` for `Strings.kt`, or the name `@file:JvmName` gives). A file class shared by several files (`@JvmMultifileClass`) is linked to one of them only.
- **Method calls on values**: `User u; u.getName()` is listed as `User.getName`. A variable without a written type (`var`, `val u = ...`) and call chains are not followed.
- **Members by name alone**: a member inherited from a class of another file is not linked. A call by name alone is linked to a method of the class before an imported function of that name, unless the method has no parameters and the call passes arguments.
- **Constructor properties and records**: properties of a Kotlin primary constructor (`class Car(val engine: Engine)`) and Java record components are not definitions; `car.engine` is linked to `Car`.
- **Kotlin extension functions**: `value.name()` is linked to an extension `name` the file sees, whatever the type of `value`, unless a project class has a member of that name.
- **Unsupported Kotlin syntax**: a keyword used as a name (`final = true`), `$$"..."` strings and `when` guards are not parsed. Definitions after them, to the end of the file, may be missing.

## C / C++

- **Include paths**: `#include` is looked up from the file's directory, the project root, `src/` directories and by the end of the project paths. Include paths set in CMake or Makefile (`-I`) are not read.
- **Functions of the same name**: a function a header declares is linked to every file that includes the header and defines it (per-platform sources, test doubles). A variable declared `extern` is linked to the header only. A function declared earlier in the same file (`static int fact(int n);`) is linked to that declaration.
- **Namespaces**: `using namespace` is not evaluated, and names are linked without namespaces (`geo::Shape::count()` is `Shape::count`), so equal names in different namespaces are not told apart.
- **Members**: a member of a base class written through a `typedef` or a template parameter is not linked.
- **Preprocessor**: `#if` / `#ifdef` are not evaluated; every branch is analyzed. Definitions produced by macros are not analyzed, and a declaration written with a macro (`TEST(a, b) { ... }`) may be listed under the macro's name.

## SQL

- **Object references**: names are compared as written; a reference that differs in case or quoting from the `CREATE` statement is not found.
- **Unsupported syntax**: PostgreSQL-style `CREATE PROCEDURE`, `CALL`, `GRANT` and psql `\i` are not parsed. The statements after a PL/pgSQL function body that is not parsed can be missing from the definitions.

## Rust

- **Module paths**: a path starting with a crate name is resolved only when that crate's `Cargo.toml` is in the project. A `mod` inside an inline module (`mod a { mod b; }`) and `#[cfg_attr(..., path = "...")]` are not resolved. A name re-exported from outside the project (`pub use std::collections::HashMap;`) is not linked.
- **Usage names**: `Self::new()` in an `impl` block is listed as `Type::new`.
- **Macros**: code generated by macros (`macro_rules!`, procedural macros, `include!`) is not analyzed. In macro arguments, only `::` paths and plain names are linked. A `#[macro_export]` macro is not linked to its defining file.
- **Method calls on values**: `c: &Circle; c.area()` is listed as `Circle.area`. A variable without a written type and fields (`self.shape.area()`) are not followed.
- **Conditional compilation**: `#[cfg(...)]` is not evaluated; every alternative module is a dependency.

## C#

- **Usage names**: usages are listed as written, without namespaces (`App.Models.Customer.Parse()` is `Customer.Parse`). An attribute is listed with its class name (`AuditAttribute`), and a type alias as its type.
- **Overloads**: a method is linked by name, argument count and, where the file shows them, argument types. When no single declaration fits, `target_context` is the first one.
- **Extension methods**: `value.Method()` is linked to every extension method of that name and argument count the line sees, whatever the type of `value`.
- **Method calls on values**: apart from extension methods, `value.Method()` is not linked to the type of `value`; `Type.Method()` is. A member inherited from a base type is not linked by its name alone.
- **Project files**: `ProjectReference`, `<Using Include="..." />` and `ImplicitUsings` in `.csproj` are not read.
- **Conditional compilation**: `#if` is not evaluated; every branch is analyzed. A directive that splits a declaration or statement is not parsed.
- **Unsupported syntax**: `extension` blocks, `allows ref struct` and null-conditional assignment (`a?.b = c`) are not parsed; the code around them is still analyzed.
- **Generated code**: source-generator output and `.razor` / `.cshtml` files are not analyzed.

## COBOL

- **Definitions**: items of the `SCREEN` and `REPORT` sections, `FUNCTION-ID`, `CLASS-ID` and `METHOD-ID` are not definitions, nor is a data item whose name has other text joined to it (`(PFX)-ID`).
- **Names**: a name defined in several copybooks is linked to the first in `COPY` order. A name qualified with `OF` / `IN` that matches no definition is linked as unqualified. In `EXEC` blocks other than `EXEC SQL`, every word is read as a name.
- **COPY**: copybooks are not expanded; each is analyzed as its own file. A name with a directory part is looked up by its file name. Of `REPLACING`, only one-word operands (`==:TAG:== BY ==WS==`, `OLD-NAME BY NEW-NAME`, `LEADING` / `TRAILING`) are applied.
- **CALL**: a `CALL` of a data item leads only to the literals the item is given in the same file (`VALUE`, `MOVE "name" TO item`).
- **CICS**: only `PROGRAM(...)` and `TRANSID(...)` of `EXEC CICS` are linked. `TRANSID(...)` leads to a CSD `DEFINE TRANSACTION`, or to a JCL file whose DFHCSDUP step defines it.
- **Several candidates**: when several files fit a `COPY`, program or transaction, the one nearest to the COBOL file in the directory tree is taken.
- **Source format**: without a `>>SOURCE` / `$SET SOURCEFORMAT` directive, fixed or free format is inferred.
- **Not evaluated**: debug lines (`D` in the indicator column), `>>` directives, `REPLACE` and conditional compilation. In a statement that is not parsed, every word that names a definition counts as a usage.

## JCL

- **Programs**: only `PGM=`, `RUN PROGRAM(...)` in the in-stream `SYSTSIN` of a TSO batch step (`IKJEFT01`, `IKJEFT1A`, `IKJEFT1B`) or of a step that calls a procedure, the program in the `PARM` of an IMS batch step (`DFSRRC00`), and the CSD commands in the in-stream `SYSIN` of a `PGM=DFHCSDUP` step lead to programs. A program passed to a procedure for another purpose (the source member of a compile procedure) is not linked.
- **Symbols**: `&NAME` is replaced only with values written in the same file (`SET`, `PROC` defaults, `EXEC` of an in-stream procedure); values given by another file's `EXEC` are not used.
- **Procedures and members**: `JCLLIB ORDER` is not read; procedures and `INCLUDE` members are found by file name anywhere in the project, nearest first. An `INCLUDE` member is not read into the including file, so its steps are not seen by `COND`, `IF`, referbacks or `DD` overrides.
- **Definitions**: a step or `PROC` statement without a name is not a definition.
- **Not analyzed**: DD names (not linked to COBOL `SELECT ... ASSIGN`), data set names, other in-stream data, JES statements and JES2 job group statements (`GJOB`, `AFTER`, `SCHEDULE`, ...).

## CSD

- **Dependencies**: only `DEFINE PROGRAM` and the `PROGRAM` of `DEFINE TRANSACTION` (to COBOL programs), and `DEFINE MAPSET` (to the BMS source) lead to other files.
- **Commands**: only `DEFINE` and `USERDEFINE` are read (also abbreviated, e.g. `DEF`); `LIST`, `ADD`, `ALTER`, `DELETE` and other commands are not.
- **Resource references**: some attributes that name another resource (`PROGRAM`, `TRANSID`, `REMOTESYSTEM`, `TRPROF`, ...) are linked, and only when the same file defines that resource.
- **Files**: CSD commands are read from `.csd` files and from the in-stream `SYSIN` of a `PGM=DFHCSDUP` step in JCL, not from a step that calls a procedure running DFHCSDUP. Resources in that JCL `SYSIN` are not definitions of the JCL file.

## R

- **Scope**: scripts that share only a directory, with no `source()`, `box::use`, package (`DESCRIPTION`), Shiny app or `testthat` helper tying them, do not see each other. A name defined in several visible files is linked to all of them. Usages are listed by the definition's name (`pkg::f` and `mod$f` are `f`).
- **Local names**: a name a function assigns is local from its first assignment in the text on; a read before it (`cfg <- modifyList(cfg, extra)`) is linked to the definition outside.
- **Definitions**: `assign("name", ...)`, names assigned inside the arguments of a top-level call (`test_that("...", { x <- 1 })`, `local({ })`, `tryCatch({ })`) and S7 `method(generic, class) <- ...` are not definitions.
- **`source()` and `box::use`**: a `source()` path built at run time is not followed. `#' @export` is not read: a `box` module exposes every top-level name.
- **Bare column names**: a column written as a bare name (`count(df, n)`, `DT[, sum(x), by = month]`) is linked when a definition of that name is visible.
- **Classes**: a call of an S3 generic (`print(x)`) is not linked to its methods. `object$method()` and `object@slot` are not linked to the S4, R6 or Reference Class of `object`.
- **Names in strings**: `get("x")`, `exists("x")` and names built at run time are not linked.
- **R Markdown and Quarto**: only `{r}` chunks are analyzed. Chunks whose code does not end inside the chunk, other languages, inline code (`` `r x` ``), `knitr::read_chunk()` and `child` documents are not.
- **Not analyzed**: `import::from()`, `NAMESPACE` files, and the link between `.Call()` / Rcpp and C or C++ code.
