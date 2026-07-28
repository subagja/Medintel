import re


NUMBER_PATTERN = r"(\d{1,3}(?:[.,]\d{3})*|\d+)"


def parse_count(value: str) -> int:
    return int(
        value.replace(".", "").replace(",", "")
    )


def extract_first_count(
    text: str,
    patterns: tuple[str, ...],
) -> int | None:
    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if match:
            return parse_count(
                match.group(1)
            )

    return None