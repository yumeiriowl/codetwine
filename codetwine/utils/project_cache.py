from typing import TypeVar

CacheValue = TypeVar("CacheValue")
CacheTuple = TypeVar("CacheTuple", bound=tuple)


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
    cache_tuple = cache_dict.get(cache_key)
    if cache_tuple is None:
        return None
    cache_file_set, value = cache_tuple
    if cache_file_set is project_file_set:
        return value
    if cache_file_set == project_file_set:
        cache_dict[cache_key] = (project_file_set, value)
        return value
    return None


def identity_cache_tuple(cache_dict: dict[int, CacheTuple], key_object: object) -> CacheTuple | None:
    """Return what a cache keeps for an object under the id of the object.

    Args:
        cache_dict: A {id of an object: (that object, values built for it ...)} cache.
        key_object: The object the values were built for.

    Returns:
        The tuple kept under the id, None when the cache has none under it or the tuple
        was kept for another object that had the same id.
    """
    cache_tuple = cache_dict.get(id(key_object))
    if cache_tuple is None or cache_tuple[0] is not key_object:
        return None
    return cache_tuple
