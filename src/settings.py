"""
Runtime settings of the pipeline, read from environment variables.

Only values that may change between runs are settings. Fixed thresholds are
module constants in the modules that use them.
"""
import os
from dataclasses import dataclass

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


def _env_int(name: str, default: int) -> int:
    """Reads a positive integer from an environment variable.

    Args:
        name: Name of the environment variable.
        default: Value used when the variable is unset or empty.

    Returns:
        int: The parsed value.

    Raises:
        ValueError: If the variable is set but is not an integer of at least 1.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as e:
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}") from e
    if value < 1:
        raise ValueError(f"Environment variable {name} must be at least 1, got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    """Settings that can be changed per run without code changes.

    Attributes:
        gemini_model (str): Gemini model id used for extraction.
        max_images_per_post (int): Maximum number of images sent to Gemini per post.
        max_gemini_calls_per_run (int): Upper bound for Gemini calls in one run,
            including retries.
    """
    gemini_model: str = DEFAULT_GEMINI_MODEL
    max_images_per_post: int = 8
    max_gemini_calls_per_run: int = 50

    @classmethod
    def from_env(cls) -> "Settings":
        """Builds the settings from environment variables, falling back to the defaults.

        Returns:
            Settings: The settings for this run.
        """
        return cls(
            gemini_model=os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL,
            max_images_per_post=_env_int("MAX_IMAGES_PER_POST", cls.max_images_per_post),
            max_gemini_calls_per_run=_env_int("MAX_GEMINI_CALLS_PER_RUN", cls.max_gemini_calls_per_run),
        )
