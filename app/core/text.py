"""Text normalization shared by PDF intake and XML prompt preparation."""


def xml_safe_text(value: str) -> str:
    """Replace characters XML 1.0 cannot carry, preserving source offsets."""
    if not isinstance(value, str):
        raise TypeError("Expected text")
    return "".join(char if (char in "\t\n\r" or
                            0x20 <= ord(char) <= 0xD7FF or
                            0xE000 <= ord(char) <= 0xFFFD or
                            0x10000 <= ord(char) <= 0x10FFFF) else " "
                   for char in value)
