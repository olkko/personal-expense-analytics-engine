"""Text normalization used for matching merchants and descriptions."""

import re
import unicodedata

_NOISE = re.compile(r"[\d#*_/\\.,;:()\-]+")


def normalize_text(text: str) -> str:
    """Lowercase, drop digits and punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = _NOISE.sub(" ", text)
    return " ".join(text.split())


def merchant_key(text: str) -> str:
    """Short grouping key for a merchant: the first word of the normalized text.

    For payment-processor descriptors like "LIQPAY*Albion" the part after the last
    "*" is the real merchant, so it is used instead.
    """
    if "*" in text:
        tail = text.rsplit("*", 1)[1]
        if normalize_text(tail):
            text = tail
    words = normalize_text(text).split()
    return words[0] if words else ""
