from time import monotonic

from src import config
from src.interfaces import BigQueryReader
from src.schema_formatter import format_dataset_structure


class SchemaProvider:
    """Build an LLM-friendly document from live bigquery metadata"""

    def __init__(
        self,
        *,
        bigquery_service: BigQueryReader,
        relationships: list[str],
        cache_ttl_seconds: float | None = None,
    ) -> None:
        self.bigquery_service = bigquery_service
        self.relationships = relationships
        self.cache_ttl_seconds = (
            config.SCHEMA_CACHE_TTL_SECONDS
            if cache_ttl_seconds is None
            else cache_ttl_seconds
        )

        self._cached_document: str | None = None
        self._cached_at: float | None = None

        # Incremented on every real BigQuery walk. Exposed so tests can assert
        # the cache is actually preventing round-trips.
        self.fetch_count = 0

    def get_schema_document(
        self,
        *,
        force_refresh: bool = False,
    ) -> str:
        """Return the current dataset schema as formatted text.

        Building the document costs roughly four BigQuery API calls per table
        and it is requested again on every repair attempt, so the result is
        cached for ``cache_ttl_seconds``.
        """

        if not force_refresh and self._is_cache_valid():
            # Narrowed by _is_cache_valid.
            return self._cached_document  # type: ignore[return-value]

        dataset_structure = self.bigquery_service.get_dataset_structure()
        self.fetch_count += 1

        document = format_dataset_structure(
            dataset_path=self.bigquery_service.dataset_path,
            dataset_structure=dataset_structure,
            relationships=self.relationships,
        )

        self._cached_document = document
        self._cached_at = monotonic()

        return document

    def _is_cache_valid(self) -> bool:
        """Return whether a cached document exists and has not expired."""

        if self._cached_document is None or self._cached_at is None:
            return False

        if self.cache_ttl_seconds <= 0:
            return False

        return (monotonic() - self._cached_at) < self.cache_ttl_seconds

    def invalidate_cache(self) -> None:
        """Discard the cached schema document."""

        self._cached_document = None
        self._cached_at = None
