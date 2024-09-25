"""Custom client handling, including CSVStream base class."""

from __future__ import annotations

import csv
import os
import typing as t
from datetime import datetime, timezone

from singer_sdk import typing as th
from singer_sdk.streams import Stream

if t.TYPE_CHECKING:
    from singer_sdk.helpers.types import Context

SDC_SOURCE_FILE_COLUMN = "_sdc_source_file"
SDC_SOURCE_LINENO_COLUMN = "_sdc_source_lineno"
SDC_SOURCE_FILE_MTIME_COLUMN = "_sdc_source_file_mtime"


class CSVStream(Stream):
    """Stream class for CSV streams."""

    file_paths: list[str] = []  # noqa: RUF012
    header: list[str] = []  # noqa: RUF012

    def __init__(self, *args, **kwargs):
        """Init CSVStram."""
        # cache file_config so we dont need to go iterating the config list again later
        self.file_config = kwargs.pop("file_config")
        super().__init__(*args, **kwargs)

    def get_records(self, context: Context | None) -> t.Iterable[dict]:
        """Return a generator of row-type dictionary objects.

        The optional `context` argument is used to identify a specific slice of the
        stream if partitioning is required for the stream. Most implementations do not
        require partitioning and should ignore the `context` argument.
        """
        schema = self.schema  # Get schema from the catalog


        self.logger.info(f"schema---{schema}")

        date_columns = self._get_date_columns(schema)
        int_columns = self._get_columns_by_type(schema, "integer")
        double_columns = self._get_columns_by_type(schema, "double")
        string_columns = self._get_columns_by_type(schema, "string")

        for file_path in self.get_file_paths():
            file_last_modified = datetime.fromtimestamp(
                os.path.getmtime(file_path), timezone.utc
            )

            file_lineno = -1

            for row in self.get_rows(file_path):
                file_lineno += 1

                if not file_lineno:
                    continue

                if self.config.get("add_metadata_columns", False):
                    row = [file_path, file_last_modified, file_lineno, *row]

                # # Apply date transformation for date columns
                row_dict = dict(zip(self.header, row))
                # for date_col in date_columns:
                #     if date_col in row_dict:
                #         row_dict[date_col] = self._transform_date(row_dict[date_col])
                #
                #
                # for int_col in int_columns:
                #     if int_col in row_dict:
                #         row_dict[int_col] = int(row_dict[int_col])
                #
                # for dob_col in double_columns:
                #     if dob_col in row_dict:
                #         row_dict[dob_col] = int(row_dict[dob_col])
                #
                # for str_col in string_columns:
                #     if dob_col in row_dict:
                #         row_dict[str_col] = str(row_dict[str_col])


                self._apply_transformations(row_dict, date_columns, int_columns, double_columns, string_columns)
                yield row_dict

    def _apply_transformations(self, row_dict: dict, date_columns: list[str], int_columns: list[str],
                               double_columns: list[str], string_columns: list[str]) -> None:
        """Apply necessary transformations to the row based on column type."""
        for col in date_columns:
            if col in row_dict:
                row_dict[col] = self._transform_date(row_dict[col])

        for col in int_columns:
            if col in row_dict:
                row_dict[col] = self._safe_cast(row_dict[col], int)

        for col in double_columns:
            if col in row_dict:
                row_dict[col] = self._safe_cast(row_dict[col], float)

        for col in string_columns:
            if col in row_dict:
                row_dict[col] = str(row_dict[col])

    def _safe_cast(self, value: str, target_type: t.Type) -> t.Any:
        """Safely cast a value to the target type."""
        try:
            return target_type(value)
        except ValueError:
            self.logger.warning(f"Failed to cast {value} to {target_type}")
            return value

    def _get_recursive_file_paths(self, file_path: str) -> list:
        file_paths = []

        for dirpath, _, filenames in os.walk(file_path):
            for filename in filenames:
                file_path = os.path.join(dirpath, filename)
                if self.is_valid_filename(file_path):
                    file_paths.append(file_path)

        return file_paths


    def _transform_date(self, date_str: str) -> str:
        """Transform date from MM/DD/YY to YYYY-MM-DD."""
        try:
            # Parse and reformat the date
            return  datetime.strptime(date_str, '%m/%d/%y').strftime('%Y-%m-%d')
        except ValueError:
            self.logger.warning(f"Date format not recognized for value: {date_str}")
            return date_str  # Return the original value if parsing fails

    def _get_date_columns(self, schema: dict) -> list[str]:
        """Extract columns from the schema that are of type 'date'."""
        date_columns = []
        for field, field_schema in schema.get("properties", {}).items():
            if "string" in field_schema.get("type", []) and field_schema.get("format") == "date":
                date_columns.append(field)
        return date_columns


    def _get_columns_by_type(self, schema: dict, data_type: str) -> list[str]:
        """Extract columns from the schema that match the specified data type."""
        return [
            field
            for field, field_schema in schema.get("properties", {}).items()
            if data_type in field_schema.get("type", [])
        ]

    def get_file_paths(self) -> list:
        """Return a list of file paths to read.

        This tap accepts file names and directories so it will detect
        directories and iterate files inside.
        """
        # Cache file paths so we dont have to iterate multiple times
        if self.file_paths:
            return self.file_paths

        file_path = self.file_config["path"]
        if not os.path.exists(file_path):
            raise Exception(f"File path does not exist {file_path}")

        file_paths = []
        if os.path.isdir(file_path):
            clean_file_path = os.path.normpath(file_path) + os.sep
            file_paths = self._get_recursive_file_paths(clean_file_path)
        elif self.is_valid_filename(file_path):
            file_paths.append(file_path)

        if not file_paths:
            raise RuntimeError(
                f"Stream '{self.name}' has no acceptable files. \
                    See warning for more detail."
            )
        self.file_paths = file_paths
        return file_paths

    def is_valid_filename(self, file_path: str) -> bool:
        """Return a boolean of whether the file includes CSV extension."""
        is_valid = True
        if file_path[-4:] != ".csv":
            is_valid = False
            self.logger.warning(f"Skipping non-csv file '{file_path}'")
            self.logger.warning(
                "Please provide a CSV file that ends with '.csv'; e.g. 'users.csv'"
            )
        return is_valid

    def get_rows(self, file_path: str) -> t.Iterable[list]:
        """Return a generator of the rows in a particular CSV file."""
        encoding = self.file_config.get("encoding", None)
        csv.register_dialect(
            "tap_dialect",
            delimiter=self.file_config.get("delimiter", ","),
            doublequote=self.file_config.get("doublequote", True),
            escapechar=self.file_config.get("escapechar", None),
            quotechar=self.file_config.get("quotechar", '"'),
            skipinitialspace=self.file_config.get("skipinitialspace", False),
            strict=self.file_config.get("strict", False),
        )
        with open(file_path, encoding=encoding) as f:
            yield from csv.reader(f, dialect="tap_dialect")

    @property
    def schema(self) -> dict:
        """Return dictionary of record schema.

        Dynamically detect the json schema for the stream.
        This is evaluated prior to any records being retrieved.
        """
        properties: list[th.Property] = []
        self.primary_keys = self.file_config.get("keys", [])


        custom_mappings = self.config.get("custom_mappings", [])

        for file_path in self.get_file_paths():
            for header in self.get_rows(file_path):  # noqa: B007
                break
            break

        # Check each column header and assign date format if it's in the config list
        for column in header:

            mapping = next((item for item in custom_mappings if item['key'] == column), None)
            if mapping is not None:

                if mapping["data_type"] == "date":
                    properties.append(th.Property(column, th.DateType()))

                elif mapping["data_type"] == "integer":
                    properties.append(th.Property(column, th.IntegerType()))

                elif mapping["data_type"] == "double":
                    properties.append(th.Property(column, th.DoubleType()))

                elif mapping["data_type"] == "string":
                    properties.append(th.Property(column, th.StringType()))
            else:
                # Default to string type if no mapping is found for the column
                properties.append(th.Property(column, th.StringType()))

        # If enabled, add file's metadata to output
        if self.config.get("add_metadata_columns", False):
            header = [
                SDC_SOURCE_FILE_COLUMN,
                SDC_SOURCE_FILE_MTIME_COLUMN,
                SDC_SOURCE_LINENO_COLUMN,
                *header,
            ]

            properties.extend(
                (
                    th.Property(SDC_SOURCE_FILE_COLUMN, th.StringType),
                    th.Property(SDC_SOURCE_FILE_MTIME_COLUMN, th.DateTimeType),
                    th.Property(SDC_SOURCE_LINENO_COLUMN, th.IntegerType),
                )
            )
        # Cache header for future use
        self.header = header

        return th.PropertiesList(*properties).to_dict()
