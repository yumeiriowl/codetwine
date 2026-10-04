import os
import logging
from dataclasses import dataclass, field, replace
from codetwine.parsers.ts_parser import parse_file
from codetwine.extractors.csharp_source import (
    ALIAS_USING,
    ATTRIBUTE_REFERENCE,
    MEMBER_REFERENCE,
    NAME_REFERENCE,
    NAMESPACE_USING,
    STATIC_USING,
    THIS_REFERENCE,
    CsharpDeclaration,
    CsharpMember,
    CsharpReference,
    CsharpType,
    CsharpUsing,
    csharp_reference_list,
    join_name,
    read_csharp_declaration,
)
from codetwine.utils.project_cache import project_cache_value
from codetwine.config.settings import CSHARP_EXT_SET, language_ext

logger = logging.getLogger(__name__)

# Parameter types every argument fits
_ANY_TYPE_SET = {"object", "dynamic"}

# Type name of a number -> the wider number types a value of it is passed as
_WIDER_TYPE_DICT = {
    "char": {"int", "uint", "long", "ulong", "float", "double", "decimal"},
    "int": {"long", "float", "double", "decimal"},
    "uint": {"long", "ulong", "float", "double", "decimal"},
    "long": {"float", "double", "decimal"},
    "ulong": {"float", "double", "decimal"},
    "float": {"double"},
}

# Extension of a C# project file (lower case, with ".")
_PROJECT_FILE_EXT = ".csproj"

# Suffix the name of an attribute is also looked up with ([Route] -> RouteAttribute)
_ATTRIBUTE_SUFFIX = "Attribute"

# Cache of namespace indexes: project_dir -> (project file set the index was built from, index)
namespace_index_cache: dict[str, tuple[set[str], "CsharpNamespaceIndex"]] = {}

# Cache of the resolved references of C# files: absolute path -> (project file set they
# were resolved with, targets)
csharp_target_cache: dict[str, tuple[set[str], list["CsharpReferenceTarget"]]] = {}


@dataclass
class CsharpReferenceTarget:
    """The definition one reference of a C# file resolves to."""

    name: str               # Name of the usage as the referring file writes it
    line: int               # Line of the reference (1-based)
    file_rel: str           # Relative path of the file with the definition
    definition_name: str    # Name the definition is looked up by in file_rel (Type.Member)
    definition_line: int    # First line of the declaration in file_rel the reference names


@dataclass
class _TypeEntry:
    """One declaration of a type and the file it is written in."""

    file_rel: str
    csharp_type: CsharpType


@dataclass
class CsharpNamespaceIndex:
    """The namespaces, types and extension methods of the C# files of a project."""

    # Relative path -> declarations of the file
    declaration_dict: dict[str, CsharpDeclaration] = field(default_factory=dict)
    # (namespace, type path) -> declarations of the type, in path order
    type_dict: dict[tuple[str, str], list[_TypeEntry]] = field(default_factory=dict)
    # Every namespace of the project and each of its leading parts (A.B.C -> A, A.B, A.B.C)
    namespace_set: set[str] = field(default_factory=set)
    # Extension method name -> declarations of the types that define it, in path order
    extension_dict: dict[str, list[_TypeEntry]] = field(default_factory=dict)
    # Relative path -> directory of the nearest .csproj file above the file ("" for none)
    project_dir_dict: dict[str, str] = field(default_factory=dict)
    # Directory of a .csproj file -> global using directives of the files under it
    global_using_dict: dict[str, list[CsharpUsing]] = field(default_factory=dict)


@dataclass
class _LookupStep:
    """One namespace a name is looked up in, with the using directives written for it."""

    namespace: str
    using_list: list[CsharpUsing] = field(default_factory=list)


@dataclass
class _Match:
    """The type, or member of a type, the leading parts of a reference name."""

    entry_list: list[_TypeEntry]    # Declarations of the type
    name: str                       # The parts of the reference that name it, joined with "."
    definition_name: str            # Type path, with the member name when one is named
    part_count: int                 # Number of leading parts of the reference the name takes
    member_name: str = ""           # Name of the member; "" when the name is the type
    is_type_first: bool = False     # Whether the first part of the reference names the type


def _project_dir_dict(project_dir: str, file_rel_list: list[str]) -> dict[str, str]:
    """Map each file to the directory of the nearest .csproj file above it.

    Examples:
        "src/App/Services/Order.cs" with src/App/App.csproj -> "src/App"
        "tools/Gen.cs" with no .csproj above it              -> ""

    Args:
        project_dir: Absolute path to the project root.
        file_rel_list: Relative paths of the C# files.

    Returns:
        A {relative path: relative directory} dict; "" is the project root, and the
        directory of a file with no .csproj file above it.
    """
    has_project_dict: dict[str, bool] = {}

    def has_project(directory: str) -> bool:
        """Return whether a directory holds a .csproj file."""
        if directory not in has_project_dict:
            try:
                name_list = os.listdir(os.path.join(project_dir, directory))
            except OSError:
                name_list = []
            has_project_dict[directory] = any(
                name.lower().endswith(_PROJECT_FILE_EXT) for name in name_list
            )
        return has_project_dict[directory]

    project_dir_dict: dict[str, str] = {}
    for file_rel in file_rel_list:
        directory = os.path.dirname(file_rel)
        while directory and not has_project(directory):
            directory = os.path.dirname(directory)
        project_dir_dict[file_rel] = directory
    return project_dir_dict


def _add_declaration(
    namespace_index: CsharpNamespaceIndex, file_rel: str, declaration: CsharpDeclaration,
) -> None:
    """Index the namespaces, types, extension methods and global using directives of a file."""
    namespace_index.declaration_dict[file_rel] = declaration
    for scope in declaration.scope_list:
        part_list = scope.namespace.split(".") if scope.namespace else []
        for part_count in range(1, len(part_list) + 1):
            namespace_index.namespace_set.add(".".join(part_list[:part_count]))
        project_dir = namespace_index.project_dir_dict.get(file_rel, "")
        namespace_index.global_using_dict.setdefault(project_dir, []).extend(
            using for using in scope.using_list if using.is_global
        )
    for csharp_type in declaration.type_list:
        entry = _TypeEntry(file_rel, csharp_type)
        namespace_index.type_dict.setdefault(
            (csharp_type.namespace, csharp_type.path), [],
        ).append(entry)
        for name in csharp_type.extension_dict:
            namespace_index.extension_dict.setdefault(name, []).append(entry)


def _build_namespace_index(project_dir: str, project_file_set: set[str]) -> CsharpNamespaceIndex:
    """Parse the C# files of a project and build their index.

    Args:
        project_dir: Absolute path to the project root.
        project_file_set: Relative paths of the project files that have a language.

    Returns:
        The CsharpNamespaceIndex of the C# files in project_file_set. A file that
        cannot be read or parsed is not in it; the exception is logged.
    """
    file_rel_list = sorted(
        file_rel for file_rel in project_file_set
        if language_ext(os.path.join(project_dir, file_rel)) in CSHARP_EXT_SET
    )
    namespace_index = CsharpNamespaceIndex(
        project_dir_dict=_project_dir_dict(project_dir, file_rel_list),
    )
    for file_rel in file_rel_list:
        try:
            root_node = parse_file(os.path.join(project_dir, file_rel))[0]
            declaration = read_csharp_declaration(root_node)
        except Exception as e:
            logger.warning(f"{file_rel} is not read as C#: {type(e).__name__}: {e}")
            continue
        _add_declaration(namespace_index, file_rel, declaration)
    return namespace_index


def _get_namespace_index(project_dir: str, project_file_set: set[str]) -> CsharpNamespaceIndex:
    """Return the namespace index of the project, building it on the first call.

    The index is kept in namespace_index_cache until the project file set changes or
    the cache is cleared.

    Args:
        project_dir: Absolute path to the project root.
        project_file_set: Relative paths of the project files that have a language.

    Returns:
        The CsharpNamespaceIndex of the C# files in project_file_set.
    """
    namespace_index = project_cache_value(namespace_index_cache, project_dir, project_file_set)
    if namespace_index is not None:
        return namespace_index

    namespace_index = _build_namespace_index(project_dir, project_file_set)
    namespace_index_cache[project_dir] = (project_file_set, namespace_index)
    return namespace_index


def _find_type(
    namespace_index: CsharpNamespaceIndex,
    namespace: str,
    part_tuple: tuple[str, ...],
    is_namespace_part: bool = True,
) -> tuple[str, str, int, int] | None:
    """Follow the parts of a name from a namespace to a type.

    The leading parts are taken as namespaces inside the namespace until a part names a
    type of the namespace reached; the parts after it are taken as types inside that
    type as long as they name one.

    Examples (from "App", with the type App.Models.Customer and Address inside it):
        (Models, Customer, Address, City) -> ("App.Models", "Customer.Address", 1, 3)
        (Models, Customer)                -> ("App.Models", "Customer", 1, 2)
        (Models,)                         -> None

    Args:
        namespace_index: The namespace index of the project.
        namespace: Full name of the namespace to start from; "" for the global one.
        part_tuple: Parts of the name.
        is_namespace_part: False takes the first part as a type only.

    Returns:
        (namespace of the type, type path, index of the first part of the type, index
        after its last part). None when the parts lead to no type.
    """
    for position, name in enumerate(part_tuple):
        if (namespace, name) in namespace_index.type_dict:
            path = name
            end = position + 1
            while end < len(part_tuple) and (
                (namespace, f"{path}.{part_tuple[end]}") in namespace_index.type_dict
            ):
                path = f"{path}.{part_tuple[end]}"
                end += 1
            return namespace, path, position, end
        namespace = join_name(namespace, name)
        if not is_namespace_part or namespace not in namespace_index.namespace_set:
            return None
    return None


def _is_constructor_match(match: _Match) -> bool:
    """Return whether a match is the constructors of a type and nothing else.

    Returns:
        True when the member the match names is declared, and every declaration of it
        is a constructor.
    """
    member_list = [
        member
        for entry in match.entry_list
        for member in entry.csharp_type.member_list_dict.get(match.member_name, [])
    ]
    return bool(member_list) and all(member.is_constructor for member in member_list)


def _is_static_name(match: _Match) -> bool:
    """Return whether a match is a name written through a type.

    Returns:
        True when the first part of the reference names the type and the match is the
        type itself, or a member of it that at least one of its declarations declares
        as static, const or an enum member.
    """
    if not match.is_type_first:
        return False
    if not match.member_name:
        return True
    return any(
        member.is_static
        for entry in match.entry_list
        for member in entry.csharp_type.member_list_dict.get(match.member_name, [])
    )


def _is_type_fit(
    member: CsharpMember, argument_type_tuple: tuple[str | None, ...], is_extension: bool,
) -> bool:
    """Return whether the arguments of a call can be the parameters of a method declaration.

    An argument without a known type, a parameter without a type name and a parameter
    of a type in _ANY_TYPE_SET fit anything; an argument past the last parameter
    (params) fits. A number fits a wider number type (_WIDER_TYPE_DICT).

    Args:
        member: The declaration.
        argument_type_tuple: Type name of each argument of the call, None where unknown.
        is_extension: Whether the call is written on a value, which is the argument of
            the first parameter.
    """
    offset = 1 if is_extension else 0
    for index, argument_type in enumerate(argument_type_tuple):
        if argument_type is None or index + offset >= len(member.parameter_type_tuple):
            continue
        parameter_type = member.parameter_type_tuple[index + offset]
        if parameter_type is None or parameter_type in _ANY_TYPE_SET:
            continue
        if parameter_type != argument_type and parameter_type not in _WIDER_TYPE_DICT.get(argument_type, ()):
            return False
    return True


def _exact_type_count(
    member: CsharpMember, argument_type_tuple: tuple[str | None, ...], is_extension: bool,
) -> int:
    """Return how many arguments of a call have exactly the type of their parameter.

    Args:
        member: The declaration.
        argument_type_tuple: Type name of each argument of the call, None where unknown.
        is_extension: Whether the call is written on a value, which is the argument of
            the first parameter.
    """
    offset = 1 if is_extension else 0
    return sum(
        1 for index, argument_type in enumerate(argument_type_tuple)
        if argument_type is not None and index + offset < len(member.parameter_type_tuple)
        and member.parameter_type_tuple[index + offset] == argument_type
    )


def _declaration_line(
    csharp_type: CsharpType,
    member_name: str,
    argument_count: int | None,
    is_extension: bool = False,
    argument_type_tuple: tuple[str | None, ...] = (),
) -> int:
    """Return the first line of the declaration of a type, or of a member of it.

    Examples (Twice(int a, int b) on line 4 and Twice(string s) on line 5 of a type
    that starts on line 2):
        "Twice", 1      -> 5
        "Twice", None   -> 4
        "Twice", 3      -> 4
        "", None        -> 2

    Args:
        csharp_type: The declaration of the type.
        member_name: Name of the member; "" for the type itself.
        argument_count: Number of arguments of the call when the member is called;
            None when it is not.
        is_extension: Whether the call is written on a value, which is the argument of
            the first parameter.
        argument_type_tuple: Type name of each argument of the call, None where the
            file does not tell it.

    Returns:
        The line of the one method declaration of the name that takes that number of
        arguments, or of the one among several of them whose parameter types the
        arguments fit (_is_type_fit), an exact type before a wider one; the line of the
        first declaration of the name
        when the member is not called or the call fits none or several; the line of
        the type when it declares no member of the name.
    """
    member_list = csharp_type.member_list_dict.get(member_name, [])
    if not member_list:
        return csharp_type.start_line
    if argument_count is not None:
        # Number of arguments with the value an extension method is called on
        full_count = argument_count + 1 if is_extension else argument_count
        fit_list = [
            member for member in member_list
            if member.argument_range is not None
            and (member.is_extension or not is_extension)
            and member.argument_range[0] <= full_count
            and (member.argument_range[1] is None or full_count <= member.argument_range[1])
        ]
        if len(fit_list) > 1:
            type_fit_list = [
                member for member in fit_list
                if _is_type_fit(member, argument_type_tuple, is_extension)
            ]
            # Of several that fit, the ones with the most parameters of exactly the
            # type of their argument (Show(int) before Show(double) for Show(1))
            exact_count_list = [
                _exact_type_count(member, argument_type_tuple, is_extension)
                for member in type_fit_list
            ]
            type_fit_list = [
                member for member, exact_count in zip(type_fit_list, exact_count_list)
                if exact_count == max(exact_count_list)
            ]
            fit_list = type_fit_list if len(type_fit_list) == 1 else fit_list
        if len(fit_list) == 1:
            return fit_list[0].start_line
    return member_list[0].start_line


class _ReferenceResolver:
    """Resolves the references of one C# file through the namespace index of its project."""

    def __init__(
        self, namespace_index: CsharpNamespaceIndex, file_rel: str, declaration: CsharpDeclaration,
    ) -> None:
        """Keep the index and the declarations of the file.

        Args:
            namespace_index: The namespace index of the project.
            file_rel: Relative path of the referring file.
            declaration: The declarations of the referring file.
        """
        self.namespace_index = namespace_index
        self.file_rel = file_rel
        self.declaration = declaration
        self.project_dir = namespace_index.project_dir_dict.get(file_rel, "")
        # Index of the innermost scope -> lookup steps from that scope
        self._step_list_dict: dict[int, list[_LookupStep]] = {}
        # (reference without its line, innermost scope, types around it) -> match
        self._match_dict: dict[tuple, _Match | None] = {}

    def _scope_index(self, line: int) -> int:
        """Return the index in scope_list of the innermost scope that holds a line."""
        scope_list = self.declaration.scope_list
        index_list = [
            index for index, scope in enumerate(scope_list)
            if scope.start_line <= line <= scope.end_line
        ]
        return max(index_list, key=lambda index: scope_list[index].depth, default=0)

    def _step_list(self, scope_index: int) -> list[_LookupStep]:
        """Return the namespaces a name written in a scope is looked up in, innermost first.

        Examples (namespace A.B.C { using X; } in a file with using Y;):
            A.B.C with using X -> A.B -> A -> "" with using Y and the global using
            directives of the project

        Args:
            scope_index: Index in scope_list of the innermost scope around the name.

        Returns:
            The lookup steps. The last one is the global namespace.
        """
        if scope_index in self._step_list_dict:
            return self._step_list_dict[scope_index]

        innermost = self.declaration.scope_list[scope_index]
        scope_list = sorted(
            (
                scope for scope in self.declaration.scope_list
                if scope.start_line <= innermost.start_line
                and innermost.end_line <= scope.end_line
                and scope.depth <= innermost.depth
            ),
            key=lambda scope: scope.depth,
            reverse=True,
        )
        step_list: list[_LookupStep] = []
        for position, scope in enumerate(scope_list):
            is_file_scope = position + 1 == len(scope_list)
            outer_namespace = "" if is_file_scope else scope_list[position + 1].namespace
            namespace = scope.namespace
            using_list = [self._absolute_using(using, namespace) for using in scope.using_list]
            if is_file_scope:
                using_list += self.namespace_index.global_using_dict.get(self.project_dir, [])
            while True:
                step_list.append(_LookupStep(namespace, using_list))
                using_list = []
                if not namespace:
                    break
                namespace = namespace.rpartition(".")[0]
                if namespace == outer_namespace and not is_file_scope:
                    break
        self._step_list_dict[scope_index] = step_list
        return step_list

    def _absolute_using(self, using: CsharpUsing, namespace: str) -> CsharpUsing:
        """Return a using directive with the full name of what it names.

        The name is looked up from the namespace the directive is written in, then
        from each namespace around that one, then from the global namespace.

        Examples (written in namespace App.Services):
            using Models;         -> using App.Models;        (App.Models is a namespace)
            using System.Text;    -> using System.Text;

        Args:
            using: The using directive.
            namespace: Full name of the namespace the directive is written in.

        Returns:
            The directive with the first name that is a namespace of the project or
            leads to a type of it; the directive itself when no name is.
        """
        while namespace:
            part_tuple = tuple(namespace.split(".")) + using.part_tuple
            if (
                ".".join(part_tuple) in self.namespace_index.namespace_set
                or _find_type(self.namespace_index, "", part_tuple) is not None
            ):
                return replace(using, part_tuple=part_tuple)
            namespace = namespace.rpartition(".")[0]
        return using

    def _outer_type_list(self, line: int) -> list[CsharpType]:
        """Return the types of the file whose declaration holds a line, innermost first."""
        return sorted(
            (
                csharp_type for csharp_type in self.declaration.type_list
                if csharp_type.start_line <= line <= csharp_type.end_line
            ),
            key=lambda csharp_type: (csharp_type.start_line, -csharp_type.end_line),
            reverse=True,
        )

    def _select_entry_list(
        self, entry_list: list[_TypeEntry], is_own_first: bool,
    ) -> list[_TypeEntry]:
        """Choose among the declarations a name leads to.

        Args:
            entry_list: The declarations.
            is_own_first: Whether the declarations of the referring file come before
                any other.

        Returns:
            The declarations in the referring file when is_own_first and there are
            any; else the ones under the .csproj directory of the referring file;
            else all of them.
        """
        if is_own_first:
            own_entry_list = [entry for entry in entry_list if entry.file_rel == self.file_rel]
            if own_entry_list:
                return own_entry_list
        project_entry_list = [
            entry for entry in entry_list
            if self.namespace_index.project_dir_dict.get(entry.file_rel, "") == self.project_dir
        ]
        return project_entry_list or entry_list

    def _match_name(
        self,
        reference: CsharpReference,
        namespace: str,
        prefix_tuple: tuple[str, ...] = (),
        prefix_arity: int = 0,
        skip_count: int = 0,
        is_member_need: bool = False,
        is_namespace_part: bool = True,
        is_any_arity: bool = False,
    ) -> _Match | None:
        """Look up the name of a reference from a namespace.

        The name looked up is prefix_tuple followed by the parts of the reference
        without the first skip_count ones. A type is found with _find_type; the part
        after the type is taken as a member of it. The match is named with the parts
        the reference writes, except that a name written with an alias of a type is
        named with the type.

        Examples (reference -> prefix_tuple, skip_count -> name, definition_name):
            Models.Customer.Parse()                   -> Customer.Parse, Customer.Parse
            Json.Write() with using Json = App.Text.Serializer;
                -> (App, Text, Serializer), 1         -> Serializer.Write, Serializer.Write
            Sq() with using static App.MathEx;
                -> (App, MathEx), 0                   -> Sq, MathEx.Sq

        Args:
            reference: The reference.
            namespace: Full name of the namespace to start from.
            prefix_tuple: Parts the reference does not write (the name a using
                directive gives, the type the reference is written in).
            prefix_arity: Number of type parameters of the type prefix_tuple names.
            skip_count: Number of leading parts of the reference prefix_tuple stands for.
            is_member_need: Whether a name that ends with the type prefix_tuple names
                must go on with a member that type defines.
            is_namespace_part: False takes the first part as a type only.
            is_any_arity: True takes the declarations of the type with another number
                of type parameters when none has the number the name is written with.

        Returns:
            The match. None when the name leads to no type, when the type ends before
            the parts of the reference, when the reference needs a type
            (TYPE_REFERENCE, ATTRIBUTE_REFERENCE) and the name ends with a member, or
            when no declaration of the type has the number of type parameters the name
            is written with and is_any_arity is False.
        """
        prefix_count = len(prefix_tuple)
        part_tuple = prefix_tuple + reference.part_tuple[skip_count:]
        arity_tuple = (
            (0,) * max(prefix_count - 1, 0) + (prefix_arity,) * min(prefix_count, 1)
            + reference.arity_tuple[skip_count:]
        )
        type_match = _find_type(self.namespace_index, namespace, part_tuple, is_namespace_part)
        if type_match is None:
            return None
        type_namespace, path, type_start, type_end = type_match

        is_member = type_end < len(part_tuple)
        name_end = type_end + 1 if is_member else type_end
        write_start = max(type_start - prefix_count + skip_count, 0)
        write_end = name_end - prefix_count + skip_count
        type_write_end = type_end - prefix_count + skip_count
        if write_end <= write_start or write_end < 1:
            return None
        is_type_need = reference.kind not in (NAME_REFERENCE, THIS_REFERENCE)
        if is_type_need and is_member:
            return None

        entry_list = self.namespace_index.type_dict[(type_namespace, path)]
        arity_entry_list = [
            entry for entry in entry_list
            if entry.csharp_type.arity == arity_tuple[type_end - 1]
        ]
        if not arity_entry_list and not is_any_arity:
            return None
        entry_list = arity_entry_list or entry_list
        if is_member:
            member_entry_list = [
                entry for entry in entry_list
                if part_tuple[type_end] in entry.csharp_type.member_name_set
            ]
            if is_member_need and type_end <= prefix_count and not member_entry_list:
                return None
            entry_list = member_entry_list or entry_list

        name_part_tuple = reference.part_tuple[write_start:write_end]
        if write_start < skip_count:
            name_part_tuple = part_tuple[type_start:name_end]
        return _Match(
            entry_list=self._select_entry_list(entry_list, is_own_first=True),
            name=".".join(name_part_tuple),
            definition_name=join_name(path, part_tuple[type_end] if is_member else ""),
            part_count=write_end,
            member_name=part_tuple[type_end] if is_member else "",
            is_type_first=write_start == 0 and type_write_end >= 1,
        )

    def _match_outer_type(
        self, reference: CsharpReference, line: int, is_any_arity: bool,
    ) -> _Match | None:
        """Look up the first part of a reference among the members of the types around it.

        The members are the ones the declarations of the type define, in the file
        itself and in the other files that declare the same type (partial).

        Args:
            reference: The reference.
            line: Line of the reference.
            is_any_arity: Passed to _match_name.

        Returns:
            The match of the innermost type that defines the first part. None when
            none of the types around the line defines it.
        """
        for csharp_type in self._outer_type_list(line):
            prefix_tuple = tuple(
                join_name(csharp_type.namespace, csharp_type.path).split(".")
            )
            match = self._match_name(
                reference, "", prefix_tuple, csharp_type.arity, is_member_need=True,
                is_any_arity=is_any_arity,
            )
            if match is not None and match.part_count >= 1:
                return match
        return None

    def _match_step(
        self, reference: CsharpReference, step: _LookupStep, is_any_arity: bool,
    ) -> _Match | None:
        """Look up a reference in one namespace and the using directives written for it.

        The first of these that leads to a type is taken: the namespace itself, an
        alias named like the first part, the namespaces of the using directives (the
        first part is a type of the namespace), the types of the using static
        directives (the first part is a member of the type). The type an alias or a
        using static directive names has the number of type arguments the directive
        writes.

        Args:
            reference: The reference.
            step: The namespace and its using directives.
            is_any_arity: Passed to _match_name.

        Returns:
            The match; the declarations of every namespace of the using directives
            that has the type are in it. None when nothing leads to a type.
        """
        match = self._match_name(reference, step.namespace, is_any_arity=is_any_arity)
        if match is not None:
            return match

        for using in step.using_list:
            if using.kind == ALIAS_USING and using.alias == reference.part_tuple[0]:
                match = self._match_name(
                    reference, "", using.part_tuple, using.arity, skip_count=1,
                    is_any_arity=is_any_arity,
                )
                if match is not None:
                    return match

        match_list = [
            self._match_name(
                reference, ".".join(using.part_tuple), is_namespace_part=False,
                is_any_arity=is_any_arity,
            )
            for using in step.using_list if using.kind == NAMESPACE_USING
        ]
        match_list = [match for match in match_list if match is not None]
        if match_list:
            match = match_list[0]
            entry_dict = {
                id(entry): entry for other in match_list for entry in other.entry_list
            }
            match.entry_list = self._select_entry_list(
                list(entry_dict.values()), is_own_first=True,
            )
            return match

        for using in step.using_list:
            if using.kind == STATIC_USING:
                match = self._match_name(
                    reference, "", using.part_tuple, using.arity, is_member_need=True,
                    is_any_arity=is_any_arity,
                )
                if match is not None:
                    return match
        return None

    def _match_reference(self, reference: CsharpReference, line: int) -> _Match | None:
        """Return the type or member the leading parts of a reference name.

        A chain that starts with "global::" is looked up from the global namespace.
        Any other chain is looked up among the members of the types around it, then in
        the namespaces around it from the innermost (_step_list, _match_step). A chain
        after "this." is looked up among the members only. The name of an attribute is
        looked up as written, then with the suffix "Attribute"; the match of the second
        lookup names the attribute with the suffix ([Audit] -> AuditAttribute).
        A type is looked up with the number of type arguments it is written with
        (Result<int> is not Result); when that leads to nothing, the whole lookup is
        done again with the types of any number of type parameters.

        Args:
            reference: A reference whose kind is not MEMBER_REFERENCE.
            line: Line of the reference.

        Returns:
            The match, or None when the chain leads to no type of the project.
        """
        for is_any_arity in (False, True):
            match = self._match_written(reference, line, is_any_arity)
            if match is None and reference.kind == ATTRIBUTE_REFERENCE:
                part_tuple = (
                    reference.part_tuple[:-1] + (reference.part_tuple[-1] + _ATTRIBUTE_SUFFIX,)
                )
                match = self._match_written(
                    replace(reference, part_tuple=part_tuple), line, is_any_arity,
                )
            if match is not None:
                return match
        return None

    def _match_written(
        self, reference: CsharpReference, line: int, is_any_arity: bool,
    ) -> _Match | None:
        """Look up a chain with the names it writes: from the global namespace when it
        starts with "global::" (_match_name), else from where it is written (_match_chain)."""
        if reference.is_absolute:
            return self._match_name(reference, "", is_any_arity=is_any_arity)
        return self._match_chain(reference, line, is_any_arity)

    def _match_namespace(
        self, reference: CsharpReference, line: int, is_any_arity: bool,
    ) -> _Match | None:
        """Return the match of the first namespace around a line that has one (_match_step)."""
        for step in self._step_list(self._scope_index(line)):
            match = self._match_step(reference, step, is_any_arity)
            if match is not None:
                return match
        return None

    def _match_chain(
        self, reference: CsharpReference, line: int, is_any_arity: bool,
    ) -> _Match | None:
        """Look up a chain that does not start with "global::" with the names it writes.

        Examples (in a type with the property Status of the enum Status):
            Status              -> the property
            Status.Closed       -> the member Closed of the enum
            Status.ToString()   -> the property

        Args:
            reference: The reference.
            line: Line of the reference.
            is_any_arity: Passed to _match_name.

        Returns:
            The match among the members of the types around the line
            (_match_outer_type); without one, and for a chain that is not after
            "this.", the match of the first namespace around the line that has one
            (_match_namespace). When a member takes only the first part of a chain of
            several parts, the match of the namespaces comes before it when it is a
            name written through a type (_is_static_name) or the member is the
            constructors of the type (_is_constructor_match). None when the chain
            leads to no type of the project.
        """
        member_match = self._match_outer_type(reference, line, is_any_arity)
        if reference.kind == THIS_REFERENCE:
            return member_match
        if member_match is None:
            return self._match_namespace(reference, line, is_any_arity)
        if member_match.part_count == 1 and len(reference.part_tuple) > 1:
            type_match = self._match_namespace(reference, line, is_any_arity)
            if type_match is not None and (
                _is_static_name(type_match) or _is_constructor_match(member_match)
            ):
                return type_match
        return member_match

    def _extension_entry_list(
        self, name: str, line: int, argument_count: int,
    ) -> list[_TypeEntry]:
        """Return the declarations of the types whose extension method a call names.

        The types are looked up in the namespaces around the line from the innermost:
        the types of the namespace itself, of the namespaces of its using directives,
        and the types its using static directives name. The first namespace with such
        a type is taken.

        Args:
            name: Name of the method.
            line: Line of the call.
            argument_count: Number of arguments of the call.

        Returns:
            The declarations, chosen with _select_entry_list. Empty when no type that
            the line sees defines an extension method of that name that takes that
            number of arguments.
        """
        candidate_list = [
            entry for entry in self.namespace_index.extension_dict.get(name, [])
            if any(
                least <= argument_count and (most is None or argument_count <= most)
                for least, most in entry.csharp_type.extension_dict[name]
            )
        ]
        if not candidate_list:
            return []
        for step in self._step_list(self._scope_index(line)):
            namespace_set = {step.namespace}
            type_name_set: set[str] = set()
            for using in step.using_list:
                if using.kind == NAMESPACE_USING:
                    namespace_set.add(".".join(using.part_tuple))
                elif using.kind == STATIC_USING:
                    type_name_set.add(".".join(using.part_tuple))
            entry_list = [
                entry for entry in candidate_list
                if entry.csharp_type.namespace in namespace_set
                or join_name(entry.csharp_type.namespace, entry.csharp_type.path) in type_name_set
            ]
            if entry_list:
                return self._select_entry_list(entry_list, is_own_first=False)
        return []

    def resolve(self, reference: CsharpReference) -> list[CsharpReferenceTarget]:
        """Resolve one reference to the definitions it refers to.

        The leading parts are resolved to a type or a member of a type
        (_match_reference). The last part of a call is resolved to an extension method
        (_extension_entry_list) when the leading parts do not take it and the call is
        written on a value: a chain of two or more parts, a chain after "this.", or a
        member of another expression.

        Args:
            reference: A reference of the file.

        Returns:
            One target per file a definition is in, with the line of the declaration
            in that file (_declaration_line). Empty when the reference leads to no
            definition of the project.
        """
        line = reference.line
        match = None
        if reference.kind != MEMBER_REFERENCE:
            match_key = (
                reference.kind, reference.part_tuple, reference.arity_tuple,
                reference.is_absolute, self._scope_index(line),
                tuple(csharp_type.start_line for csharp_type in self._outer_type_list(line)),
            )
            if match_key not in self._match_dict:
                self._match_dict[match_key] = self._match_reference(reference, line)
            match = self._match_dict[match_key]

        target_list: list[CsharpReferenceTarget] = []
        part_count = 0
        if match is not None:
            part_count = match.part_count
            is_member_call = reference.is_call and part_count == len(reference.part_tuple)
            argument_count = reference.argument_count if is_member_call else None
            target_list.extend(
                CsharpReferenceTarget(
                    match.name, line, entry.file_rel, match.definition_name,
                    _declaration_line(
                        entry.csharp_type, match.member_name, argument_count,
                        argument_type_tuple=reference.argument_type_tuple,
                    ),
                )
                for entry in match.entry_list
            )

        is_value_call = reference.is_call and (
            len(reference.part_tuple) >= 2
            or reference.kind in (THIS_REFERENCE, MEMBER_REFERENCE)
        )
        if is_value_call and part_count < len(reference.part_tuple):
            name = reference.part_tuple[-1]
            target_list.extend(
                CsharpReferenceTarget(
                    name, line, entry.file_rel, join_name(entry.csharp_type.path, name),
                    _declaration_line(
                        entry.csharp_type, name, reference.argument_count, is_extension=True,
                        argument_type_tuple=reference.argument_type_tuple,
                    ),
                )
                for entry in self._extension_entry_list(name, line, reference.argument_count)
            )
        return target_list


def csharp_reference_target_list(
    file_rel: str,
    project_file_set: set[str],
    project_dir: str,
) -> list[CsharpReferenceTarget]:
    """Resolve each reference of a C# file to the definition it refers to.

    A name is looked up as the language does: among the members of the types the name
    is written in (the other files of a partial type included), then in each namespace
    around it from the innermost, with the alias, namespace and static using directives
    written for that namespace; the global using directives of the files under the same
    .csproj directory count for the file. A name written with namespaces
    (App.Models.Customer, Models.Customer) is followed through them. The part after a
    type is taken as a member of it. A type is looked up with the number of type
    arguments it is written with, and with any number when that leads to nothing. A
    method called on a value is resolved by its name and its number of arguments to
    the extension methods of the types the line sees.
    A type declared in several files leads to the file itself when it declares the
    type, else to the files under the .csproj directory of the file, else to all of
    them; with a member named, only the files that define the member count.

    Processing flow:
    1. Return the targets of csharp_target_cache when they were resolved with the same
       project file set
    2. Read the references of the file
    3. Resolve each reference

    Args:
        file_rel: Relative path of the file.
        project_file_set: Relative paths of the project files that have a language.
        project_dir: Absolute path to the project root.

    Returns:
        One target per reference and file a definition is in, in line order, without
        duplicates. A target whose file_rel is the file itself is a reference to a
        definition of the file. definition_line is the first line of the declaration
        of the type or member; of several declarations of a called method, the one
        that takes the number of arguments of the call (_declaration_line). The
        targets are kept in csharp_target_cache until the project file set changes or
        the cache is cleared.
    """
    # == Step 1: Cache ========================================================
    cache_key = os.path.abspath(os.path.join(project_dir, file_rel))
    target_list = project_cache_value(csharp_target_cache, cache_key, project_file_set)
    if target_list is not None:
        return target_list

    # == Step 2: References ===================================================
    namespace_index = _get_namespace_index(project_dir, project_file_set)
    declaration = namespace_index.declaration_dict.get(file_rel)
    if declaration is None:
        return []
    resolver = _ReferenceResolver(namespace_index, file_rel, declaration)

    # == Step 3: Targets ======================================================
    # Two calls of one line that write the same name lead to two declarations when
    # they call two overloads
    target_dict: dict[tuple[str, int, str, int], CsharpReferenceTarget] = {}
    root_node = parse_file(os.path.join(project_dir, file_rel))[0]
    for reference in csharp_reference_list(root_node):
        for target in resolver.resolve(reference):
            target_dict.setdefault(
                (target.name, target.line, target.file_rel, target.definition_line), target,
            )

    target_list = sorted(
        target_dict.values(), key=lambda target: (target.line, target.name, target.file_rel),
    )
    csharp_target_cache[cache_key] = (project_file_set, target_list)
    return target_list
