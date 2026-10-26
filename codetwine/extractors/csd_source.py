from codetwine.extractors.cobol_source import CobolDefinition
from codetwine.extractors.jcl_source import (
    RESOURCE_REFERENCE,
    JclReference,
    JclSource,
    add_csd_file_name,
)
from codetwine.parsers.csd_command import (
    TRANSACTION_RESOURCE,
    CsdAttribute,
    CsdDefinition,
    csd_command_list,
    csd_definition,
)
from codetwine.utils.file_utils import line_list_of

# Start of the type of the definition of a resource: define_ + the lower-case resource type
_DEFINE_TYPE_PREFIX = "define_"
# Type of the definition of a transaction
TRANSACTION_TYPE = _DEFINE_TYPE_PREFIX + TRANSACTION_RESOURCE.lower()


def _attribute_definition(
    attribute: CsdAttribute,
    group: str,
    resource_dict: dict[tuple[str, str], CobolDefinition],
    group_resource_dict: dict[tuple[str, str, str], CobolDefinition],
) -> CobolDefinition | None:
    """Return the resource of the file an attribute names.

    The resource types of the attribute are tried in order; of a type, the resource of
    the GROUP of the command is taken, else the first one of the file.

    Args:
        attribute: The attribute.
        group: Upper-case GROUP of the command of the attribute.
        resource_dict: (resource type, upper-case name) -> the first resource with them.
        group_resource_dict: (resource type, upper-case name, GROUP) -> the first
            resource of the GROUP with them.

    Returns:
        The definition of the resource. None when the file defines none.
    """
    for resource_type in attribute.type_tuple:
        resource_key = (resource_type, attribute.name.upper())
        definition = group_resource_dict.get((*resource_key, group)) or resource_dict.get(
            resource_key,
        )
        if definition is not None:
            return definition
    return None


def read_csd_source(source: str) -> JclSource:
    """Read the CSD commands of DFHCSDUP input and return what they define and name.

    Definitions: the resources DEFINE and USERDEFINE commands define (define_transaction,
    define_program, define_mapset, ...), each over the lines of its command. Statements
    naming another file: the program of DEFINE PROGRAM and the PROGRAM of DEFINE
    TRANSACTION as CALL_KIND, the mapset of DEFINE MAPSET as MAPSET_KIND
    (CsdDefinition.file_name_list). References: those programs and mapsets, and each
    resource of the file an attribute names (CsdDefinition.attribute_list): the one of
    the same GROUP, else the first one of the file. The resources the commands define
    are its csd_resource_list as well.

    Processing flow:
    1. Read the commands and the resources they define
    2. Add the programs, mapsets and resources of the file each command names

    Args:
        source: The text of the file.

    Returns:
        A JclSource; its definitions and statements are in line order.
    """
    line_list = line_list_of(source)
    jcl_source = JclSource(line_list=line_list)

    # == Step 1: Commands and resources =======================================
    command_definition_list: list[CsdDefinition] = []
    # (upper-case resource type, upper-case name) -> the first resource with them, and
    # (upper-case resource type, upper-case name, GROUP) -> the first one of the GROUP
    resource_dict: dict[tuple[str, str], CobolDefinition] = {}
    group_resource_dict: dict[tuple[str, str, str], CobolDefinition] = {}
    for command in csd_command_list(list(enumerate(line_list, 1))):
        command_definition = csd_definition(command)
        if command_definition is None:
            continue
        command_definition_list.append(command_definition)
        resource = command_definition.resource
        jcl_source.csd_resource_list.append(resource)
        definition = CobolDefinition(
            name=resource.name,
            type=_DEFINE_TYPE_PREFIX + resource.type.lower(),
            start_line=command.start_line,
            end_line=command.end_line,
            name_line=resource.line,
        )
        jcl_source.definition_list.append(definition)
        resource_key = (resource.type, resource.name.upper())
        resource_dict.setdefault(resource_key, definition)
        group_resource_dict.setdefault((*resource_key, command_definition.group), definition)

    # == Step 2: Names of each command ========================================
    for command_definition in command_definition_list:
        for csd_name in command_definition.file_name_list:
            add_csd_file_name(jcl_source, csd_name)
        for attribute in command_definition.attribute_list:
            definition = _attribute_definition(
                attribute, command_definition.group, resource_dict, group_resource_dict,
            )
            if definition is not None:
                jcl_source.reference_list.append(JclReference(
                    definition.name.upper(), attribute.line, RESOURCE_REFERENCE,
                    definition_line=definition.start_line,
                ))
    return jcl_source
