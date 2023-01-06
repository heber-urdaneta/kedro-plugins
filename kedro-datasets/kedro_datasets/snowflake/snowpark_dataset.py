"""``AbstractDataSet`` implementation to access Snowflake using Snowpark dataframes
"""
import logging
from copy import deepcopy
from typing import Any, Dict, Union

import pandas as pd
import snowflake.snowpark as sp

from kedro.io.core import AbstractDataSet, DataSetError

logger = logging.getLogger(__name__)


class SnowParkDataSet(
    AbstractDataSet[pd.DataFrame, pd.DataFrame]
):
    """``SnowParkDataSet`` loads and saves Snowpark dataframes.

    Example adding a catalog entry with
    `YAML API <https://kedro.readthedocs.io/en/stable/data/\
        data_catalog.html#use-the-data-catalog-with-the-yaml-api>`_:

    .. code-block:: yaml

        >>> weather:
        >>>   type: kedro_datasets.snowflake.SnowParkDataSet
        >>>   table_name: "weather_data"
        >>>   database: "meteorology"
        >>>   schema: "observations"
        >>>   credentials: db_credentials
        >>>   save_args:
        >>>     mode: overwrite
        >>>     column_order: name
        >>>     table_type: ''

    One can skip everything but "table_name" if database and
    schema provided via credentials. Therefore catalog entries can be shorter
    if ex. all used Snowflake tables live in same database/schema.
    Values in dataset definition take priority over ones defined in credentials

    Example:
    Credentials file provides all connection attributes, catalog entry
    "weather" reuse credentials parameters, "polygons" catalog entry reuse
    all credentials parameters except providing different schema name

    catalog.yml

    .. code-block:: yaml
        >>> weather:
        >>>   type: kedro_datasets.snowflake.SnowParkDataSet
        >>>   table_name: "weather_data"
        >>>   save_args:
        >>>     mode: overwrite
        >>>     column_order: name
        >>>     table_type: ''

        >>> polygons:
        >>>   type: kedro_datasets.snowflake.SnowParkDataSet
        >>>   table_name: "geopolygons"
        >>>   schema: "geodata"

    credentials.yml

    .. code-block:: yaml
        >>> snowflake_client:
        >>>   account: 'ab12345.eu-central-1'
        >>>   port: 443
        >>>   warehouse: "datascience_wh"
        >>>   database: "detailed_data"
        >>>   schema: "observations"
        >>>   user: "service_account_abc"
        >>>   password: "supersecret"
    """

    # this dataset cannot be used with ``ParallelRunner``,
    # therefore it has the attribute ``_SINGLE_PROCESS = True``
    # for parallelism within a pipeline please consider
    # ``ThreadRunner`` instead
    _SINGLE_PROCESS = True
    DEFAULT_LOAD_ARGS = {}  # type: Dict[str, Any]
    DEFAULT_SAVE_ARGS = {}  # type: Dict[str, Any]

    # TODO: Update docstring
    def __init__(  # pylint: disable=too-many-arguments
        self,
        table_name: str,
        schema: str = None,
        database: str = None,
        load_args: Dict[str, Any] = None,
        save_args: Dict[str, Any] = None,
        credentials: Dict[str, Any] = None,
    ) -> None:
        """Creates a new instance of ``SnowParkDataSet``.

        Args:
            table_name: The table name to load or save data to.
            schema: Name of the schema where ``table_name`` is.
                Optional as can be provided as part of ``credentials``
                dictionary. Argument value takes priority over one provided
                in ``credentials`` if any.
            database: Name of the database where ``schema`` is.
                Optional as can be provided as part of ``credentials``
                dictionary. Argument value takes priority over one provided
                in ``credentials`` if any.
            load_args: Currently not used
            save_args: Provided to underlying snowpark ``save_as_table``
                To find all supported arguments, see here:
                https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/api/snowflake.snowpark.DataFrameWriter.saveAsTable.html
            credentials: A dictionary with a snowpark connection string.
                To find all supported arguments, see here:
                https://docs.snowflake.com/en/user-guide/python-connector-api.html#connect
        """

        if not table_name:
            raise DataSetError("'table_name' argument cannot be empty.")

        if not credentials:
            raise DataSetError("'credentials' argument cannot be empty.")

        if not database:
            if not ("database" in credentials and credentials["database"]):
                raise DataSetError("'database' must be provided by credentials or dataset.")
            else:
                database = credentials["database"]

        if not schema:
            if not ("schema" in credentials and credentials["schema"]):
                raise DataSetError("'schema' must be provided by credentials or dataset.")
            else:
                schema = credentials["schema"]


        # Handle default load and save arguments
        self._load_args = deepcopy(self.DEFAULT_LOAD_ARGS)
        if load_args is not None:
            self._load_args.update(load_args)
        self._save_args = deepcopy(self.DEFAULT_SAVE_ARGS)
        if save_args is not None:
            self._save_args.update(save_args)

        self._table_name = table_name
        self._database = database
        self._schema = schema

        connection_parameters = credentials
        connection_parameters.update(
            {"database": self._database,
            "schema": self._schema
             }
        )

        self._connection_parameters = connection_parameters
        self._session = self._get_session(self._connection_parameters)

    def _describe(self) -> Dict[str, Any]:
        return dict(
            table_name=self._table_name,
            database=self._database,
            schema=self._schema,
        )

    @staticmethod
    def _get_session(connection_parameters) -> sp.Session:
        """Given a connection string, create singleton connection
        to be used across all instances of `SnowParkDataSet` that
        need to connect to the same source.
        connection_parameters is a dictionary of any values
        supported by snowflake python connector: https://docs.snowflake.com/en/user-guide/python-connector-api.html#connect
            example:
            connection_parameters = {
                "account": "",
                "user": "",
                "password": "", (optional)
                "role": "", (optional)
                "warehouse": "", (optional)
                "database": "", (optional)
                "schema": "", (optional)
                "authenticator: "" (optional)
                }
        """
        try:
            logger.debug("Trying to reuse active snowpark session...")
            # if hook is implemented, get active session
            session = sp.context.get_active_session()
        except sp.exceptions.SnowparkSessionException as exc:
            # create session if there is no active one
            logger.debug("No active snowpark session found. Creating")
            session = sp.Session.builder.configs(connection_parameters).create()
        return session

    def _load(self) -> sp.DataFrame:
        table_name = [
            self._database,
            self._schema,
            self._table_name,
        ]

        sp_df = self._session.table(".".join(table_name))
        return sp_df

    def _save(self, data: Union[pd.DataFrame, sp.DataFrame]) -> None:
        if not isinstance(data, sp.DataFrame):
            sp_df = self._session.create_dataframe(data)
        else:
            sp_df = data

        table_name = [
            self._database,
            self._schema,
            self._table_name,
        ]

        sp_df.write.save_as_table(table_name, **self._save_args)

    def _exists(self) -> bool:
        session = self._session
        query = "SELECT COUNT(*) FROM {database}.INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = '{schema}' AND TABLE_NAME = '{table_name}'"
        rows = session.sql(query.format(database = self._database, schema = self._schema, table_name = self._table_name)).collect()
        return rows[0][0] == 1
