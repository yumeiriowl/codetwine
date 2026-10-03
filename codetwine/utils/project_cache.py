from typing import TypeVar

CacheValue = TypeVar("CacheValue")


def project_cache_value(
    cache_dict: dict[str, tuple[set[str], CacheValue]],
    cache_key: str,
    project_file_set: set[str],
) -> CacheValue | None:
    """Return the value a cache keeps under a key for a project file set.

    A value kept with an equal set that is another object is kept with
    project_file_set from then on.

    Args:
        cache_dict: A {key: (project file set the value was built for, value)} cache.
        cache_key: The key of the value.
        project_file_set: Relative paths of the project files that have a language.

    Returns:
        The value, None when the cache has none under the key or it was built for
        another project file set.
    """
    cache_entry = cache_dict.get(cache_key)
    if cache_entry is None:
        return None
    cache_file_set, value = cache_entry
    if cache_file_set is project_file_set:
        return value
    if cache_file_set == project_file_set:
        cache_dict[cache_key] = (project_file_set, value)
        return value
    return None
