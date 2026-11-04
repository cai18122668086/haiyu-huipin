"""Compare runtime settings without treating environment ordering as a change."""


def environment_values(entries: list[str]) -> dict[str, str]:
    result = {}
    for entry in entries:
        name, separator, value = entry.partition("=")
        if not separator or not name or name in result:
            raise ValueError("Malformed or duplicate runtime environment key")
        result[name] = value
    return result
