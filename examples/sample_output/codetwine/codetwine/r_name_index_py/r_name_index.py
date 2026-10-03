import os
import re
import logging
import posixpath
from dataclasses import dataclass, field
from codetwine.parsers.ts_parser import parse_file
from codetwine.extractors.r_source import (
    BOX_IMPORT,
    LIBRARY_IMPORT,
    MEMBER_REFERENCE,
    NAMESPACE_REFERENCE,
    SOURCE_IMPORT,
    RDefinition,
    RImport,
    RReference,
    RSource,
    read_r_source,
)
from codetwine.utils.project_cache import project_cache_value
from codetwine.config.settings import R_EXT_SET, R_MARKDOWN_EXT_SET, language_ext

logger = logging.getLogger(__name__)

# File at the root of a package, and the field of it that holds the package name
_PACKAGE_FILE = "DESCRIPTION"
_PACKAGE_NAME_FIELD = "Package:"

# Directory of a package, and of a Shiny app, whose scripts are loaded together
_CODE_DIR = "R"

# File names (lower case) that make a directory a Shiny app
_APP_FILE_SET = {"app.r", "server.r"}

# File names (lower case) of a Shiny app directory that see global.R and the scripts of R/
_APP_SCRIPT_SET = {"app.r", "ui.r", "server.r", "global.r"}

# File name (lower case) of a Shiny app whose names the other files of the app see
_APP_GLOBAL_FILE = "global.r"

# Directory name of testthat tests, and the file name prefixes (lower case) of the
# scripts in it whose names the other files of the directory see
_TEST_DIR = "testthat"
_TEST_HELPER_PREFIX_TUPLE = ("helper", "setup")

# Extensions a box module path is tried with, and the file name of a module that is a directory
_MODULE_EXT_TUPLE = (".R", ".r")
_MODULE_INIT_FILE = "__init__"

# A path that starts at a root: /x, ~/x, C:/x
_ROOT_PATH_RE = re.compile(r"^(/|~|[A-Za-z]:)")

# A definition and the relative path of the file it is written in
_Entry = tuple[str, RDefinition]

# Name -> the definitions of that name
_NameTable = dict[str, list[_Entry]]

# Cache of name indexes: project_dir -> (project file set the index was built from, index)
r_name_index_cache: dict[str, tuple[set[str], "RNameIndex"]] = {}

# Cache of the resolved references of R files: absolute path -> (project file set they
# were resolved with, targets)
r_target_cache: dict[str, tuple[set[str], list["RReferenceTarget"]]] = {}


@dataclass
class RReferenceTarget:
    """The definition one reference of an R file resolves to."""

    name: str               # Name of the usage: the name of the definition
    line: int               # Line of the reference (1-based)
    file_rel: str           # Relative path of the file with the definition
    definition_name: str    # Name the definition is looked up by in file_rel
    start_line: int         # First line of the definition in file_rel (1-based)
    end_line: int           # Last line of the definition in file_rel


@dataclass
class _BoxModule:
    """What one argument of box::use leads to: a script of the project or one of its packages."""

    file_rel: str | None = None       # Relative path of the module script
    package_name: str = ""            # Name of the package


@dataclass
class _BoxScope:
    """What the box::use calls of one file bind."""

    # Name bound in the file -> the definitions attached under it one by one
    attach_table: _NameTable = field(default_factory=dict)
    # The names of the modules attached whole ([...])
    attach_all_table: _NameTable = field(default_factory=dict)
    # Name a module is bound to -> the module
    module_dict: dict[str, _BoxModule] = field(default_factory=dict)


@dataclass
class RNameIndex:
    """The definitions, imports and packages of the R files of a project."""

    # Relative path -> what the file defines, reads and refers to
    source_dict: dict[str, RSource] = field(default_factory=dict)
    # Relative path -> the top-level names the file defines
    table_dict: dict[str, _NameTable] = field(default_factory=dict)
    # Relative paths of the R scripts (not R Markdown or Quarto files)
    script_file_set: set[str] = field(default_factory=set)
    # Relative path of an R script in lower case -> the scripts with that path in any case
    lower_file_dict: dict[str, list[str]] = field(default_factory=dict)
    # Directory -> the R scripts directly in it, in path order
    dir_file_dict: dict[str, list[str]] = field(default_factory=dict)
    # Relative path -> directory of the nearest package above the file (None for no package)
    package_dir_dict: dict[str, str | None] = field(default_factory=dict)
    # Package name -> directories of the packages of that name, in path order
    package_name_dict: dict[str, list[str]] = field(default_factory=dict)
    # Directory of a package -> the top-level names of the scripts in its R directory
    package_table_dict: dict[str, _NameTable] = field(default_factory=dict)
    # Directory of a package -> the scripts in its R directory
    package_file_dict: dict[str, set[str]] = field(default_factory=dict)
    # Name -> the top-level definitions of that name in the R files, in path order
    entry_dict: _NameTable = field(default_factory=dict)
    # Relative path -> the scripts the file reads with source(), in line order
    source_file_dict: dict[str, list[str]] = field(default_factory=dict)
    # Relative path of a script -> the files that read it with source(), in path order
    reader_file_dict: dict[str, list[str]] = field(default_factory=dict)
    # Relative path -> what the box::use calls of the file bind (kept by _box_scope)
    box_scope_dict: dict[str, _BoxScope] = field(default_factory=dict)


def _file_table(file_rel: str, r_source: RSource) -> _NameTable:
    """Return the top-level names an R file defines.

    Members of a class and calls written for a name defined elsewhere (setMethod) are
    left out.
    """
    table: _NameTable = {}
    for definition in r_source.definition_list:
        if definition.is_member or definition.is_attach:
            continue
        table.setdefault(definition.name, []).append((file_rel, definition))
    return table


def _merge_table(table_list: list[_NameTable]) -> _NameTable:
    """Return one name table with the definitions of every given table, in order."""
    merge_table: _NameTable = {}
    for table in table_list:
        for name, entry_list in table.items():
            merge_table.setdefault(name, []).extend(entry_list)
    return merge_table


def _ancestor_dir_list(file_rel: str) -> list[str]:
    """Return the directory of a file and each directory above it, nearest first.

    Examples:
        "app/logic/calc.R" -> ["app/logic", "app", ""]
        "main.R"           -> [""]
    """
    directory = posixpath.dirname(file_rel)
    dir_list = [directory]
    while directory:
        directory = posixpath.dirname(directory)
        dir_list.append(directory)
    return dir_list


def _package_name(project_dir: str, directory: str) -> str | None:
    """Return the package name the DESCRIPTION file of a directory gives.

    Args:
        project_dir: Absolute path to the project root.
        directory: Relative path of the directory ("" for the project root).

    Returns:
        The value of the "Package:" field. None when the directory has no DESCRIPTION
        file, or the file has no such field.
    """
    description_path = os.path.join(project_dir, directory, _PACKAGE_FILE)
    try:
        with open(description_path, encoding="utf-8-sig", errors="replace") as description_file:
            for line in description_file:
                if line.startswith(_PACKAGE_NAME_FIELD):
                    return line[len(_PACKAGE_NAME_FIELD):].strip() or None
    except OSError:
        return None
    return None


def _add_package(name_index: RNameIndex, project_dir: str, file_rel_list: list[str]) -> None:
    """Index the packages of the project: the directory of each file's package, the
    package names, and the names of the scripts in each package's R directory."""
    name_by_dir_dict: dict[str, str | None] = {}
    for file_rel in file_rel_list:
        package_dir: str | None = None
        for directory in _ancestor_dir_list(file_rel):
            if directory not in name_by_dir_dict:
                name_by_dir_dict[directory] = _package_name(project_dir, directory)
            if name_by_dir_dict[directory] is not None:
                package_dir = directory
                break
        name_index.package_dir_dict[file_rel] = package_dir

    for directory, package_name in sorted(name_by_dir_dict.items()):
        if package_name is None:
            continue
        name_index.package_name_dict.setdefault(package_name, []).append(directory)
        code_file_list = name_index.dir_file_dict.get(posixpath.join(directory, _CODE_DIR), [])
        name_index.package_file_dict[directory] = set(code_file_list)
        name_index.package_table_dict[directory] = _merge_table(
            [name_index.table_dict[file_rel] for file_rel in code_file_list]
        )


def _source_file(name_index: RNameIndex, file_rel: str, path: str) -> str | None:
    """Return the script a source() path leads to.

    The path is tried from the directory of the file, then from each directory above
    it up to the project root. When no script has the path as written, the one script
    whose path differs from it only in upper and lower case is taken, in the same order.

    Examples (file "app/server.R"):
        "helper.R"     -> "app/helper.R", else "helper.R"
        "../R/util.R"  -> "R/util.R"
        "helper.r"     -> "app/helper.R" (no "app/helper.r", no "helper.r")

    Args:
        name_index: The name index of the project.
        file_rel: Relative path of the file the path is written in.
        path: The path as written.

    Returns:
        The relative path of the script, or None when the path starts at a root or
        leads to no R script of the project.
    """
    path = path.replace("\\", "/")
    if _ROOT_PATH_RE.match(path):
        return None
    candidate_list = [
        posixpath.normpath(posixpath.join(directory, path))
        for directory in _ancestor_dir_list(file_rel)
    ]
    for candidate in candidate_list:
        if candidate in name_index.script_file_set:
            return candidate
    for candidate in candidate_list:
        same_name_list = name_index.lower_file_dict.get(candidate.lower(), [])
        if len(same_name_list) == 1:
            return same_name_list[0]
    return None


def _box_module_file(name_index: RNameIndex, file_rel: str, path: str) -> str | None:
    """Return the script a box module path leads to.

    A path that starts with "./" or "../" is taken from the directory of the file; any
    other path from the directory of the file, then from each directory above it. The
    path leads to <path>.R, <path>.r, <path>/__init__.R or <path>/__init__.r.

    Examples (file "app/main.R"):
        "./logic/calc"   -> "app/logic/calc.R"
        "app/view/table" -> "app/view/table.R" (from the project root)

    Args:
        name_index: The name index of the project.
        file_rel: Relative path of the file box::use is written in.
        path: The module path, its parts joined with "/".

    Returns:
        The relative path of the script, or None when no R script of the project
        matches.
    """
    is_relative = path.startswith(("./", "../"))
    dir_list = _ancestor_dir_list(file_rel)
    for directory in dir_list[:1] if is_relative else dir_list:
        base_path = posixpath.normpath(posixpath.join(directory, path))
        for candidate_base in (base_path, posixpath.join(base_path, _MODULE_INIT_FILE)):
            for module_ext in _MODULE_EXT_TUPLE:
                if candidate_base + module_ext in name_index.script_file_set:
                    return candidate_base + module_ext
    return None


def _import_file(name_index: RNameIndex, file_rel: str, r_import: RImport) -> str | None:
    """Return the script a source() call or a box module path of a file leads to.

    Returns:
        The relative path, or None for a package (library(pkg), box::use(pkg)) and
        for a path that leads to no script.
    """
    if r_import.kind == SOURCE_IMPORT:
        return _source_file(name_index, file_rel, r_import.path)
    if r_import.kind == BOX_IMPORT:
        module = _box_module(name_index, file_rel, r_import)
        return module.file_rel if module else None
    return None


def _add_source_edge(name_index: RNameIndex) -> None:
    """Index, for each file, the scripts its source() calls lead to and the files that read it."""
    for file_rel, r_source in name_index.source_dict.items():
        source_file_list: list[str] = []
        for r_import in r_source.import_list:
            if r_import.kind != SOURCE_IMPORT:
                continue
            source_rel = _source_file(name_index, file_rel, r_import.path)
            if source_rel and source_rel != file_rel and source_rel not in source_file_list:
                source_file_list.append(source_rel)
        name_index.source_file_dict[file_rel] = source_file_list
        for source_rel in source_file_list:
            name_index.reader_file_dict.setdefault(source_rel, []).append(file_rel)


def _reach_list(edge_dict: dict[str, list[str]], start_list: list[str]) -> list[str]:
    """Return the start files and every file reached from them through the edges.

    Examples (edges a -> b, b -> c, c -> a):
        ["a"]       -> ["a", "b", "c"]
        ["b", "x"]  -> ["b", "x", "c", "a"]

    Args:
        edge_dict: {file: files one edge away}.
        start_list: The files to start from.

    Returns:
        The files, each once, the start files first.
    """
    reach_list = list(dict.fromkeys(start_list))
    visit_set = set(reach_list)
    position = 0
    while position < len(reach_list):
        for next_rel in edge_dict.get(reach_list[position], []):
            if next_rel not in visit_set:
                visit_set.add(next_rel)
                reach_list.append(next_rel)
        position += 1
    return reach_list


def _package_dir_list(name_index: RNameIndex, package_name: str, file_rel: str) -> list[str]:
    """Return the directories of the packages of the project a file names with a package name.

    Args:
        name_index: The name index of the project.
        package_name: The name as written (library(pkg), pkg::name, box::use(pkg)).
        file_rel: Relative path of the file the name is written in.

    Returns:
        The directory of the package the file is in when it has that name, else the
        directories of every package of that name, in path order.
    """
    package_dir_list = name_index.package_name_dict.get(package_name, [])
    own_package_dir = name_index.package_dir_dict.get(file_rel)
    return [own_package_dir] if own_package_dir in package_dir_list else package_dir_list


def _package_table(name_index: RNameIndex, package_name: str, file_rel: str) -> _NameTable:
    """Return the names of the packages a file names with a package name (_package_dir_list)."""
    package_dir_list = _package_dir_list(name_index, package_name, file_rel)
    if len(package_dir_list) == 1:
        return name_index.package_table_dict[package_dir_list[0]]
    return _merge_table(
        [name_index.package_table_dict[package_dir] for package_dir in package_dir_list]
    )


def _box_module(name_index: RNameIndex, file_rel: str, r_import: RImport) -> _BoxModule | None:
    """Return the module one argument of box::use leads to.

    A path with "/" is a module script (_box_module_file); a path of one part is a
    package of the project with that name.

    Returns:
        The module, or None when the path leads to nothing in the project.
    """
    if "/" in r_import.path:
        module_file = _box_module_file(name_index, file_rel, r_import.path)
        return _BoxModule(file_rel=module_file) if module_file else None
    if r_import.path in name_index.package_name_dict:
        return _BoxModule(package_name=r_import.path)
    return None


def _module_table(
    name_index: RNameIndex,
    module: _BoxModule,
    file_rel: str,
    visit_set: frozenset[str] = frozenset(),
    hit_set: set[str] | None = None,
) -> _NameTable:
    """Return the names a box module gives the file that binds it.

    A module script gives its own top-level names, then the names its own box::use
    calls attach; a module in visit_set gives its own names only. A package gives the
    names of the packages of that name (_package_table).

    Args:
        name_index: The name index of the project.
        module: The module.
        file_rel: Relative path of the file that binds the module.
        visit_set: The files whose box::use calls are read at this moment.
        hit_set: Given the modules of visit_set the read comes back to; modified in
            place.
    """
    if module.file_rel is None:
        return _package_table(name_index, module.package_name, file_rel)
    own_table = name_index.table_dict[module.file_rel]
    if module.file_rel in visit_set:
        if hit_set is not None:
            hit_set.add(module.file_rel)
        return own_table
    box_scope = _box_scope(name_index, module.file_rel, visit_set, hit_set)
    return _merge_table([own_table, box_scope.attach_table, box_scope.attach_all_table])


def _box_scope(
    name_index: RNameIndex,
    file_rel: str,
    visit_set: frozenset[str] = frozenset(),
    hit_set: set[str] | None = None,
) -> _BoxScope:
    """Return what the box::use calls of a file bind.

    The scope is kept in name_index.box_scope_dict when the read of it came back to no
    file of visit_set. Modules that attach the names of one another in a circle give
    each file the names of all of them.

    Args:
        name_index: The name index of the project.
        file_rel: Relative path of the file.
        visit_set: The files whose box::use calls are read at this moment, when the
            file is read as a module of one of them.
        hit_set: Given the modules the read of the file comes back to; modified in
            place.

    Returns:
        The _BoxScope of the file.
    """
    box_scope = name_index.box_scope_dict.get(file_rel)
    if box_scope is not None:
        return box_scope
    box_scope = _BoxScope()
    module_visit_set = visit_set | {file_rel}
    module_hit_set: set[str] = set()

    attach_all_table_list: list[_NameTable] = []
    for r_import in name_index.source_dict[file_rel].import_list:
        if r_import.kind != BOX_IMPORT:
            continue
        module = _box_module(name_index, file_rel, r_import)
        if module is None:
            continue
        if r_import.alias:
            box_scope.module_dict[r_import.alias] = module
        if not r_import.name_dict and not r_import.is_attach_all:
            continue
        module_table = _module_table(
            name_index, module, file_rel, module_visit_set, module_hit_set,
        )
        for name, original_name in r_import.name_dict.items():
            box_scope.attach_table.setdefault(name, []).extend(module_table.get(original_name, []))
        if r_import.is_attach_all:
            attach_all_table_list.append(module_table)
    box_scope.attach_all_table.update(_merge_table(attach_all_table_list))

    if not module_hit_set & visit_set:
        name_index.box_scope_dict[file_rel] = box_scope
    if hit_set is not None:
        hit_set.update(module_hit_set)
    return box_scope


def _build_name_index(project_dir: str, project_file_set: set[str]) -> RNameIndex:
    """Parse the R files of a project and build their index.

    Args:
        project_dir: Absolute path to the project root.
        project_file_set: Relative paths of the project files that have a language.

    Returns:
        The RNameIndex of the R files in project_file_set. A file that cannot be read
        or parsed is not in it; the exception is logged.
    """
    name_index = RNameIndex()
    file_rel_list: list[str] = []
    for file_rel in sorted(project_file_set):
        file_abs = os.path.join(project_dir, file_rel)
        file_ext = language_ext(file_abs)
        if file_ext not in R_EXT_SET:
            continue
        try:
            r_source = read_r_source(parse_file(file_abs)[0])
        except Exception as e:
            logger.warning(f"{file_rel} is not read as R: {type(e).__name__}: {e}")
            continue
        file_rel_list.append(file_rel)
        name_index.source_dict[file_rel] = r_source
        name_index.table_dict[file_rel] = _file_table(file_rel, r_source)
        for name, entry_list in name_index.table_dict[file_rel].items():
            name_index.entry_dict.setdefault(name, []).extend(entry_list)
        if file_ext not in R_MARKDOWN_EXT_SET:
            name_index.script_file_set.add(file_rel)
            name_index.lower_file_dict.setdefault(file_rel.lower(), []).append(file_rel)
            name_index.dir_file_dict.setdefault(posixpath.dirname(file_rel), []).append(file_rel)

    _add_package(name_index, project_dir, file_rel_list)
    _add_source_edge(name_index)
    return name_index


def _get_name_index(project_dir: str, project_file_set: set[str]) -> RNameIndex:
    """Return the name index of the project, building it on the first call.

    The index is kept in r_name_index_cache until the project file set changes or the
    cache is cleared.

    Args:
        project_dir: Absolute path to the project root.
        project_file_set: Relative paths of the project files that have a language.

    Returns:
        The RNameIndex of the R files in project_file_set.
    """
    name_index = project_cache_value(r_name_index_cache, project_dir, project_file_set)
    if name_index is not None:
        return name_index

    name_index = _build_name_index(project_dir, project_file_set)
    r_name_index_cache[project_dir] = (project_file_set, name_index)
    return name_index


class _ReferenceResolver:
    """Resolves the references of one R file to the definitions they refer to."""

    def __init__(self, name_index: RNameIndex, file_rel: str) -> None:
        """Collect the names the file sees.

        Args:
            name_index: The name index of the project.
            file_rel: Relative path of the file.
        """
        self._name_index = name_index
        self._file_rel = file_rel
        self._r_source = name_index.source_dict[file_rel]
        self._box_scope = _box_scope(name_index, file_rel)
        # Package name -> the names of the packages the file names with it
        self._package_table_dict: dict[str, _NameTable] = {}
        # Name -> definitions of it the file sees, kept once looked up
        self._lookup_dict: dict[str, list[_Entry]] = {}
        self._file_set_list = self._lookup_file_set_list()

    def _with_source(self, file_rel_list: list[str], skip_set: set[str]) -> list[str]:
        """Return the given scripts and the ones they read with source(), followed through.

        Args:
            file_rel_list: The scripts to start from.
            skip_set: Files left out of the return value.

        Returns:
            The scripts, each once, without the ones in skip_set.
        """
        reach_list = _reach_list(self._name_index.source_file_dict, file_rel_list)
        return [file_rel for file_rel in reach_list if file_rel not in skip_set]

    def _reader_file_list(self, skip_set: set[str]) -> list[str]:
        """Return the scripts that read the file with source(), followed up through the
        scripts that read those, and every script they read."""
        reader_list = _reach_list(self._name_index.reader_file_dict, [self._file_rel])
        return self._with_source(reader_list, skip_set)

    def _is_app_dir(self, directory: str) -> bool:
        """Return whether a directory holds app.R or server.R."""
        return any(
            posixpath.basename(script_rel).lower() in _APP_FILE_SET
            for script_rel in self._name_index.dir_file_dict.get(directory, [])
        )

    def _app_file_list(self, file_rel: str) -> list[str]:
        """Return the scripts of the Shiny app of a file whose names it sees.

        For app.R, ui.R, server.R and global.R of an app directory, and for the scripts
        of its R directory: global.R and the scripts of the R directory.
        """
        if file_rel not in self._name_index.script_file_set:
            return []
        directory = posixpath.dirname(file_rel)
        if posixpath.basename(file_rel).lower() in _APP_SCRIPT_SET and self._is_app_dir(directory):
            app_dir = directory
        elif posixpath.basename(directory) == _CODE_DIR and self._is_app_dir(posixpath.dirname(directory)):
            app_dir = posixpath.dirname(directory)
        else:
            return []
        dir_file_dict = self._name_index.dir_file_dict
        file_list = [
            script_rel for script_rel in dir_file_dict.get(app_dir, [])
            if posixpath.basename(script_rel).lower() == _APP_GLOBAL_FILE
        ]
        file_list.extend(dir_file_dict.get(posixpath.join(app_dir, _CODE_DIR), []))
        return file_list

    def _test_helper_file_list(self, file_rel: str) -> list[str]:
        """Return the helper and setup scripts of the testthat directory a file is in."""
        directory = posixpath.dirname(file_rel)
        if posixpath.basename(directory) != _TEST_DIR:
            return []
        return [
            script_rel for script_rel in self._name_index.dir_file_dict.get(directory, [])
            if posixpath.basename(script_rel).lower().startswith(_TEST_HELPER_PREFIX_TUPLE)
        ]

    def _library_file_set_list(self, file_rel_list: list[str]) -> list[set[str]]:
        """Return the scripts of the project packages the given files attach with
        library() / require(): one set for each package name, the package attached
        last first, without the package the file itself is in."""
        name_index = self._name_index
        own_package_dir = name_index.package_dir_dict.get(self._file_rel)
        file_set_dict: dict[str, set[str]] = {}
        for file_rel in file_rel_list:
            for r_import in name_index.source_dict[file_rel].import_list:
                if r_import.kind != LIBRARY_IMPORT or r_import.path in file_set_dict:
                    continue
                file_set_dict[r_import.path] = set().union(*(
                    name_index.package_file_dict[package_dir]
                    for package_dir in _package_dir_list(name_index, r_import.path, self._file_rel)
                    if package_dir != own_package_dir
                ))
        return list(file_set_dict.values())[::-1]

    def _lookup_file_set_list(self) -> list[set[str]]:
        """Return the sets of scripts a name of the file is looked up in, in order.

        After the file itself and the names box::use attaches (_lookup):
        1. The package the file is in, when the file is a script of its R directory
        2. The scripts the file reads with source(), followed through
        3. The files that read the file with source(), followed up, and the scripts
           those read
        4. global.R and the R directory of the Shiny app of the file, and of the Shiny
           app of each file that reads it (followed up), and the scripts those read
        5. The helper and setup scripts of the testthat directory of the file, and of
           the testthat directory of each file that reads it (followed up), and the
           scripts those read
        6. The package the file is in, when the file is not a script of its R directory
        7. The packages of the project that the file, or a script of 2 to 5, attaches
           with library() / require()

        Returns:
            The sets that hold a script, in that order.
        """
        name_index = self._name_index
        skip_set = {self._file_rel}
        source_list = self._with_source([self._file_rel], skip_set)
        reader_list = self._reader_file_list(skip_set | set(source_list))
        chain_list = _reach_list(name_index.reader_file_dict, [self._file_rel])
        app_list = self._with_source(
            [app_rel for chain_rel in chain_list for app_rel in self._app_file_list(chain_rel)],
            skip_set,
        )
        helper_list = self._with_source(
            [
                helper_rel for chain_rel in chain_list
                for helper_rel in self._test_helper_file_list(chain_rel)
            ],
            skip_set,
        )
        package_dir = name_index.package_dir_dict.get(self._file_rel)
        package_file_set = (
            name_index.package_file_dict[package_dir] if package_dir is not None else set()
        )
        is_package_code = self._file_rel in package_file_set

        file_set_list: list[set[str]] = [package_file_set] if is_package_code else []
        file_set_list.extend(
            set(file_rel_list)
            for file_rel_list in (source_list, reader_list, app_list, helper_list)
        )
        if not is_package_code:
            file_set_list.append(package_file_set)
        file_set_list.extend(self._library_file_set_list(
            [self._file_rel, *source_list, *reader_list, *app_list, *helper_list]
        ))
        return [file_set for file_set in file_set_list if file_set]

    def _lookup(self, name: str) -> list[_Entry]:
        """Return the definitions of a name the file sees.

        The definitions of the file itself, else the ones box::use attaches under the
        name (one by one, then whole modules), else the ones in the first set of
        _lookup_file_set_list with a script that defines the name. A name is looked up
        once per file.
        """
        entry_list = self._lookup_dict.get(name)
        if entry_list is None:
            entry_list = self._find_entry_list(name)
            self._lookup_dict[name] = entry_list
        return entry_list

    def _find_entry_list(self, name: str) -> list[_Entry]:
        """Look up the definitions of a name the file sees (see _lookup)."""
        own_table_list = [
            self._name_index.table_dict[self._file_rel],
            self._box_scope.attach_table,
            self._box_scope.attach_all_table,
        ]
        for table in own_table_list:
            entry_list = table.get(name)
            if entry_list:
                return entry_list
        all_entry_list = self._name_index.entry_dict.get(name)
        if not all_entry_list:
            return []
        for file_set in self._file_set_list:
            entry_list = [entry for entry in all_entry_list if entry[0] in file_set]
            if entry_list:
                return entry_list
        return []

    def _package_table(self, package_name: str) -> _NameTable:
        """Return the names of the packages of the project the file names with a package
        name, read once for each name."""
        if package_name not in self._package_table_dict:
            self._package_table_dict[package_name] = _package_table(
                self._name_index, package_name, self._file_rel,
            )
        return self._package_table_dict[package_name]

    def _member_entry_list(self, module: _BoxModule, name_list: list[str]) -> list[_Entry]:
        """Return the definitions a chain of names after a box module refers to.

        A name that the module itself binds to another module with box::use leads into
        that module; the first name that does not is looked up among the names the
        module gives.

        Examples (logic bound to app/logic/__init__.R, which has box::use(app/logic/data)):
            logic$data$load   -> load of app/logic/data.R
            logic$helper      -> helper of app/logic/__init__.R

        Args:
            module: The module the owner of the reference is bound to.
            name_list: The names after the owner, in order.
        """
        for position, name in enumerate(name_list):
            inner_module = None
            if module.file_rel is not None and position < len(name_list) - 1:
                inner_module = _box_scope(self._name_index, module.file_rel).module_dict.get(name)
            if inner_module is None:
                return _module_table(self._name_index, module, self._file_rel).get(name, [])
            module = inner_module
        return []

    def _entry_list(self, reference: RReference) -> list[_Entry]:
        """Return the definitions a reference refers to.

        pkg::name is looked up in the package of the project named pkg. owner$name is
        looked up in the box module bound to owner (_member_entry_list); with no such
        module it is a reference to owner. Any other name is looked up through the
        tables of the file.
        """
        if reference.kind == NAMESPACE_REFERENCE:
            return self._package_table(reference.owner).get(reference.name, [])
        if reference.kind == MEMBER_REFERENCE:
            module = self._box_scope.module_dict.get(reference.owner)
            if module is not None:
                return self._member_entry_list(module, [reference.name, *reference.member_tuple])
            return self._lookup(reference.owner)
        return self._lookup(reference.name)

    def _method_target_list(self) -> list[RReferenceTarget]:
        """Return the generic each S3 method of the file is written for.

        A top-level function named generic.class leads to the function named generic
        that the file sees and that calls UseMethod, on the first line of the method.
        Of the names a method name can be split into, the longest generic is taken.

        Examples:
            print.person          -> print
            as.data.frame.tbl_df  -> as.data.frame, else as.data, else as
        """
        target_list: list[RReferenceTarget] = []
        for definition in self._r_source.definition_list:
            if not definition.is_function or definition.is_member or definition.is_attach:
                continue
            part_list = definition.name.split(".")
            for part_count in range(len(part_list) - 1, 0, -1):
                generic_name = ".".join(part_list[:part_count])
                generic_entry_list = [
                    entry for entry in self._lookup(generic_name) if entry[1].is_generic
                ] if generic_name else []
                if not generic_entry_list:
                    continue
                target_list.extend(
                    RReferenceTarget(
                        generic_name, definition.start_line, entry_rel, generic_name,
                        generic.start_line, generic.end_line,
                    )
                    for entry_rel, generic in generic_entry_list
                )
                break
        return target_list

    def target_list(self) -> list[RReferenceTarget]:
        """Return the definitions the references and the S3 methods of the file lead to."""
        target_list = [
            RReferenceTarget(
                definition.name, reference.line, entry_rel, definition.name,
                definition.start_line, definition.end_line,
            )
            for reference in self._r_source.reference_list
            for entry_rel, definition in self._entry_list(reference)
        ]
        target_list.extend(self._method_target_list())
        return target_list


def r_reference_target_list(
    file_rel: str,
    project_file_set: set[str],
    project_dir: str,
) -> list[RReferenceTarget]:
    """Resolve each reference of an R file to the definition it refers to.

    A name no function around it binds is looked up, in this order, among the top-level
    names of: the file itself; the box modules whose names box::use attaches; the
    scripts the file reads with source() / sys.source(), followed through; the scripts
    that read the file with source(), followed up, and the scripts those read; global.R
    and the R directory of the Shiny app the file belongs to; the helper and setup
    scripts of the testthat directory the file is in; the scripts in the R directory of
    the package the file is in (the nearest directory above it whose DESCRIPTION file
    has a "Package:" field); the packages of the project that the file, or one of the
    scripts above, attaches with library() / require(). The scripts global.R, the R
    directory of an app and the helper scripts read with source() count with them.
    pkg::name and pkg:::name are looked up in the package of the project named pkg, and
    owner$name in the box module bound to owner, through the modules that module binds
    (owner$inner$name) and the names it attaches. A top-level function named
    generic.class leads to the function generic that calls UseMethod.
    A name defined in several files of the table it is found in leads to all of them.

    Processing flow:
    1. Return the targets of r_target_cache when they were resolved with the same
       project file set
    2. Resolve the references of the file through the name index of the project

    Args:
        file_rel: Relative path of the file.
        project_file_set: Relative paths of the project files that have a language.
        project_dir: Absolute path to the project root.

    Returns:
        One target per reference and file a definition is in, in line order, without
        duplicates; the usage is named after the definition. A target whose file_rel
        is the file itself is a reference to a definition of the file. The targets are
        kept in r_target_cache until the project file set changes or the cache is
        cleared. Empty for a file that could not be read.
    """
    # == Step 1: Cache ========================================================
    cache_key = os.path.abspath(os.path.join(project_dir, file_rel))
    target_list = project_cache_value(r_target_cache, cache_key, project_file_set)
    if target_list is not None:
        return target_list

    # == Step 2: Targets ======================================================
    name_index = _get_name_index(project_dir, project_file_set)
    if file_rel not in name_index.source_dict:
        return []
    target_dict: dict[tuple[str, int, str, int], RReferenceTarget] = {}
    for target in _ReferenceResolver(name_index, file_rel).target_list():
        target_dict.setdefault(
            (target.name, target.line, target.file_rel, target.start_line), target,
        )

    target_list = sorted(
        target_dict.values(), key=lambda target: (target.line, target.name, target.file_rel),
    )
    r_target_cache[cache_key] = (project_file_set, target_list)
    return target_list


def r_import_file_list(
    file_rel: str,
    project_file_set: set[str],
    project_dir: str,
) -> list[str]:
    """Return the scripts an R file reads with source() / sys.source() and box::use.

    Args:
        file_rel: Relative path of the file.
        project_file_set: Relative paths of the project files that have a language.
        project_dir: Absolute path to the project root.

    Returns:
        The relative paths of the scripts the calls of the file lead to, in line order,
        without duplicates and without the file itself.
    """
    name_index = _get_name_index(project_dir, project_file_set)
    r_source = name_index.source_dict.get(file_rel)
    if r_source is None:
        return []
    import_file_list: list[str] = []
    for r_import in r_source.import_list:
        import_rel = _import_file(name_index, file_rel, r_import)
        if import_rel and import_rel != file_rel and import_rel not in import_file_list:
            import_file_list.append(import_rel)
    return import_file_list
