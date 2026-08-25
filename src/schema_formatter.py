from typing import Any


def format_dataset_structure(
    dataset_path: str,
    dataset_structure: list[dict[str, Any]],
    relationships: list[str] | None = None,
) -> str:
    """Convert BigQuery metadata into a compact LLM-friendly document."""

    lines: list[str] = [
        f"DATASET: {dataset_path}",
        "",
    ]

    for table_details in dataset_structure:
        metadata = table_details["metadata"]
        columns = table_details["columns"]

        table_name = metadata["table_name"]

        lines.append(f"TABLE: {table_name}")
        lines.append(f"ROW COUNT: {metadata['row_count']}")
        lines.append(f"TABLE TYPE: {metadata['table_type']}")

        description = metadata.get("description")

        if description:
            lines.append(f"DESCRIPTION: {description}")

        partition_field = metadata.get("partition_field")

        if partition_field:
            partition_type = metadata.get("partition_type")

            lines.append(
                f"PARTITIONED BY: {partition_field} "
                f"({partition_type})"
            )

        clustering_fields = metadata.get("clustering_fields", [])

        if clustering_fields:
            clustering_text = ", ".join(clustering_fields)
            lines.append(f"CLUSTERED BY: {clustering_text}")

        lines.append("COLUMNS:")

        for column in columns:
            column_line = (
                f"- {column['name']} "
                f"{column['type']} "
                f"{column['mode']}"
            )

            column_description = column.get("description")

            if column_description:
                column_line += f" — {column_description}"

            lines.append(column_line)

        lines.append("")

    if relationships:
        lines.append("RELATIONSHIPS:")

        for relationship in relationships:
            lines.append(f"- {relationship}")

    return "\n".join(lines).strip()


# --- condensed version, for comparison only ---


# def _optional_line(label: str, value: Any) -> list[str]:
#     return [f"{label}: {value}"] if value else []


# def format_dataset_structure_condensed(
#     dataset_path: str,
#     dataset_structure: list[dict[str, Any]],
#     relationships: list[str] | None = None,
# ) -> str:
#     """Convert BigQuery metadata into a compact LLM-friendly document."""

#     lines = [f"DATASET: {dataset_path}", ""]

#     for table in dataset_structure:
#         metadata, columns = table["metadata"], table["columns"]

#         lines += [
#             f"TABLE: {metadata['table_name']}",
#             f"ROW COUNT: {metadata['row_count']}",
#             f"TABLE TYPE: {metadata['table_type']}",
#         ]
#         lines += _optional_line("DESCRIPTION", metadata.get("description"))

#         if partition_field := metadata.get("partition_field"):
#             lines.append(
#                 f"PARTITIONED BY: {partition_field} ({metadata.get('partition_type')})"
#             )

#         if clustering_fields := metadata.get("clustering_fields"):
#             lines.append(f"CLUSTERED BY: {', '.join(clustering_fields)}")

#         lines.append("COLUMNS:")
#         for column in columns:
#             line = f"- {column['name']} {column['type']} {column['mode']}"
#             if description := column.get("description"):
#                 line += f" — {description}"
#             lines.append(line)

#         lines.append("")

#     if relationships:
#         lines.append("RELATIONSHIPS:")
#         lines += [f"- {r}" for r in relationships]

#     return "\n".join(lines).strip()