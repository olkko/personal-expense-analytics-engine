"""Text normalization used for matching merchants and descriptions."""

import re
import unicodedata

_NOISE = re.compile(r"[\d#*_/\\.,;:()\-]+")


def normalize_text(text: str) -> str:
    """Lowercase text, remove digits and punctuation, and collapse whitespace.

    Args:
        text (str): Text to normalize.

    Returns:
        str: Normalized text suitable for matching.
    """
    text = unicodedata.normalize("NFKC", text).casefold()
    text = _NOISE.sub(" ", text)
    return " ".join(text.split())


def merchant_key(text: str) -> str:
    """Short grouping key for a merchant: the first word of the normalized text.

    For payment-processor descriptors like "LIQPAY*Albion" the part after the last
    "*" is the real merchant, so it is used instead.

    Args:
        text (str): Merchant name or transaction description.

    Returns:
        str: First word of the normalized merchant name, or an empty string.
    """
    if "*" in text:
        tail = text.rsplit("*", 1)[1]
        if normalize_text(tail):
            text = tail
    words = normalize_text(text).split()
    return words[0] if words else ""
