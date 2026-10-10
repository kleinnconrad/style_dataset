"""
Runtime settings of the pipeline, read from environment variables.

Only values that may change between runs are settings. Fixed thresholds are
module constants in the modules that use them.
"""
import os
from dataclasses import dataclass
from pathlib import Path

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


def default_output_dir() -> Path:
    """Returns the folder for records and state.

    In GitHub Actions this is ``data/`` in the repository, which the workflow
    commits. Locally it is a folder in Downloads, so that test runs do not
    change the committed dataset.

    Returns:
        Path: The output folder.
    """
    if os.getenv("GITHUB_ACTIONS") == "true":
        return Path("data")
    return Path.home() / "Downloads" / "style_dataset"


@dataclass(frozen=True)
class Settings:
    """Settings that can be changed per run without code changes.

    Attributes:
        gemini_model (str): Gemini model id used for extraction.
        max_images_per_post (int): Maximum number of images sent to Gemini per post.
        max_gemini_calls_per_run (int): Upper bound for Gemini calls in one run,
            including retries.
        max_posts_per_run (int): Maximum number of posts processed in one run.
        max_posts_per_source (int): Maximum number of posts per source in one run.
        max_probation_posts_per_run (int): Maximum number of posts from sources
            on probation in one run.
        feed_lookback_days (int): Posts published longer ago are ignored, and
            unfinished posts are given up after this many days.
        output_dir (Path): Folder for records (``YYYY/MM/``) and state (``state/``).
    """
    gemini_model: str = DEFAULT_GEMINI_MODEL
    max_images_per_post: int = 8
    max_gemini_calls_per_run: int = 50
    max_posts_per_run: int = 40
    max_posts_per_source: int = 3
    max_probation_posts_per_run: int = 10
    feed_lookback_days: int = 30
    output_dir: Path = Path("data")

    @property
    def state_dir(self) -> Path:
        """Folder of the state files (registry, posts, images, run log)."""
        return self.output_dir / "state"

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
            max_posts_per_run=_env_int("MAX_POSTS_PER_RUN", cls.max_posts_per_run),
            max_posts_per_source=_env_int("MAX_POSTS_PER_SOURCE", cls.max_posts_per_source),
            max_probation_posts_per_run=_env_int("MAX_PROBATION_POSTS_PER_RUN", cls.max_probation_posts_per_run),
            feed_lookback_days=_env_int("FEED_LOOKBACK_DAYS", cls.feed_lookback_days),
            output_dir=default_output_dir(),
        )
